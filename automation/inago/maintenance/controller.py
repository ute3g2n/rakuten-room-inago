"""Application-level operations for the maintenance state machine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from automation.inago.maintenance.investigation import (
    InvestigationContractError,
    MaintenanceCheckStageResult,
    build_investigation_handoff_payload,
    render_investigation_markdown,
    run_check_stage,
    save_attempt_artifact,
    validate_investigation_response,
    validate_investigation_review,
)

from automation.inago.maintenance.store import (
    AIParentSessionRecord,
    DailyCheckResult,
    GateResult,
    JobInputManifest,
    JobFinalizeResult,
    JobStartResult,
    HandoffContractError,
    HandoffResult,
    LeaseToken,
    MaintenanceStore,
    MaintenanceStoreError,
    StepAttemptResult,
    StepAttemptRecord,
)


@dataclass(frozen=True)
class MaintenanceDailyCheckOutcome:
    """The check-stage route and its durable daily-check record."""

    stage: MaintenanceCheckStageResult
    record: DailyCheckResult


@dataclass(frozen=True)
class InvestigationAttemptOutcome:
    """Persisted investigation artifacts and the decision after review."""

    attempt_id: str
    result_version: str
    investigation_status: str
    review_status: str
    next_action: str
    output_path: str
    output_sha256: str
    facts_path: str
    facts_sha256: str
    markdown_path: str
    markdown_sha256: str
    review_findings: tuple[str, ...]
    review_reason_code: str | None
    handoff: HandoffResult | None


@dataclass(frozen=True)
class InvestigationAttemptRequest:
    """All caller-owned inputs for one investigation/review attempt."""

    context: Mapping[str, Any]
    response: object
    attempts_root: str | Path
    output_root: str | Path
    result_version: str
    created_at: datetime | str
    completed_at: datetime | str
    review_started_at: datetime | str
    reviewed_at: datetime | str
    accepted_at: datetime | str
    lease: LeaseToken
    author_id: str
    author_execution_id: str
    reviewer_id: str
    reviewer_execution_id: str
    review_runner: Callable[[Mapping[str, object]], object]
    review_round: int
    handoff_root: str | Path
    synthetic_fixture: bool = False


def _investigation_identity(context: Mapping[str, Any]) -> dict[str, object]:
    return {
        key: context[key]
        for key in (
            "incident_id",
            "source_launch_id",
            "source_run_id",
            "target_date",
            "job_id",
        )
    }


def _save_investigation_inputs(
    request: InvestigationAttemptRequest,
    attempt_id: str,
    validated: Mapping[str, Any],
    identity: Mapping[str, object],
) -> tuple[str, str, str, str]:
    markdown = render_investigation_markdown(
        validated,
        request.context,
        result_version=request.result_version,
        created_at=request.created_at,
        synthetic_fixture=request.synthetic_fixture,
    )
    facts_json = (
        json.dumps(
            {
                "schema_version": "maintenance-investigation-facts-v1",
                "identity": identity,
                "attempt_id": attempt_id,
                "result_version": request.result_version,
                "evidence_refs": list(validated["evidence_refs"]),
                "facts": validated["facts"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    facts_path, facts_sha256 = save_attempt_artifact(
        request.output_root,
        request.attempts_root,
        request.context,
        filename="facts.json",
        content=facts_json,
    )
    markdown_path, markdown_sha256 = save_attempt_artifact(
        request.output_root,
        request.attempts_root,
        request.context,
        filename="investigation.md",
        content=markdown,
    )
    return facts_path, facts_sha256, markdown_path, markdown_sha256


def _run_investigation_review(
    request: InvestigationAttemptRequest,
    *,
    attempt_id: str,
    facts_path: str,
    facts_sha256: str,
    markdown_path: str,
    markdown_sha256: str,
) -> tuple[dict[str, Any], str | None]:
    review_input = {
        "attempt_id": attempt_id,
        "result_version": request.result_version,
        "facts_path": facts_path,
        "facts_sha256": facts_sha256,
        "markdown_path": markdown_path,
        "markdown_sha256": markdown_sha256,
        "review_execution_id": request.reviewer_execution_id,
    }
    try:
        response = request.review_runner(review_input)
    except Exception:
        return (
            {
                "handoff_allowed": False,
                "next_action": "blocked",
                "reviewer_id": request.reviewer_id,
                "review_execution_id": request.reviewer_execution_id,
                "reviewed_version": request.result_version,
                "review_round": request.review_round,
                "status": "unavailable",
                "findings": [],
            },
            "review_runner_failed",
        )
    try:
        review = validate_investigation_review(
            response,
            request.context,
            result_version=request.result_version,
            author_id=request.author_id,
            author_execution_id=request.author_execution_id,
            reviewer_id=request.reviewer_id,
            reviewer_execution_id=request.reviewer_execution_id,
            review_round=request.review_round,
            max_review_rounds=3,
        )
    except Exception:
        return (
            {
                "handoff_allowed": False,
                "next_action": "blocked",
                "reviewer_id": request.reviewer_id,
                "review_execution_id": request.reviewer_execution_id,
                "reviewed_version": request.result_version,
                "review_round": request.review_round,
                "status": "unavailable",
                "findings": [],
            },
            "review_response_invalid",
        )
    return review, None


def _investigation_result_document(
    request: InvestigationAttemptRequest,
    *,
    attempt_id: str,
    identity: Mapping[str, object],
    validated: Mapping[str, Any],
    facts_path: str,
    facts_sha256: str,
    markdown_path: str,
    markdown_sha256: str,
    review: Mapping[str, Any],
    review_failure: str | None,
    store_review_status: str,
    next_action: str,
) -> dict[str, object]:
    return {
        "schema_version": "maintenance-investigation-result-v1",
        "identity": dict(identity),
        "attempt_id": attempt_id,
        "result_version": request.result_version,
        "created_at": (
            request.created_at.isoformat()
            if isinstance(request.created_at, datetime)
            else request.created_at
        ),
        "investigation_status": validated["status"],
        "summary": validated["summary"],
        "facts": {"path": facts_path, "sha256": facts_sha256},
        "report": {"path": markdown_path, "sha256": markdown_sha256},
        "review": {
            "author_id": request.author_id,
            "author_execution_id": request.author_execution_id,
            "reviewer_id": review["reviewer_id"],
            "review_execution_id": review["review_execution_id"],
            "reviewed_version": review["reviewed_version"],
            "review_round": review["review_round"],
            "status": review["status"],
            "gate_status": store_review_status,
            "reason_code": review_failure
            or ("investigation_blocked" if validated["status"] != "complete" else None),
            "findings": list(review["findings"]),
        },
        "next_action": next_action,
    }


class MaintenanceController:
    """Expose safe workflow operations while keeping persistence in the store."""

    def __init__(self, store: MaintenanceStore) -> None:
        self._store = store

    def record_daily_check(
        self,
        *,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
        check_status: str,
        observed_at: datetime | str,
        reason_code: str | None,
        incident: dict[str, object] | None,
    ) -> DailyCheckResult:
        return self._store.record_daily_check(
            source_launch_id=source_launch_id,
            source_run_id=source_run_id,
            target_date=target_date,
            check_status=check_status,
            observed_at=observed_at,
            reason_code=reason_code,
            incident=incident,
        )

    def evaluate_release_gate(
        self, *, release_id: str, evaluated_at: datetime | str
    ) -> GateResult:
        return self._store.evaluate_release_gate(
            release_id=release_id,
            evaluated_at=evaluated_at,
        )

    def bind_job_input_manifest(
        self,
        *,
        incident_id: str,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
        job_id: str,
        created_at: datetime | str,
    ) -> JobInputManifest:
        return self._store.bind_job_input_manifest(
            incident_id=incident_id,
            source_launch_id=source_launch_id,
            source_run_id=source_run_id,
            target_date=target_date,
            job_id=job_id,
            created_at=created_at,
        )

    def get_job_input_manifest(
        self,
        *,
        job_id: str,
        expected_identity: dict[str, object] | None = None,
    ) -> JobInputManifest:
        return self._store.get_job_input_manifest(
            job_id=job_id,
            expected_identity=expected_identity,
        )

    def get_step_attempt(self, *, attempt_id: str) -> StepAttemptRecord:
        return self._store.get_step_attempt(attempt_id)

    def begin_step_attempt(
        self,
        *,
        job_id: str,
        step_key: str,
        sequence: int,
        lease: LeaseToken,
        started_at: datetime | str,
    ) -> StepAttemptResult:
        return self._store.begin_step_attempt(
            job_id=job_id,
            step_key=step_key,
            sequence=sequence,
            lease=lease,
            started_at=started_at,
        )

    def run_daily_check_stage(
        self,
        *,
        context: Mapping[str, Any],
        preclassification: str,
        check_runner: Callable[[dict[str, Any]], object],
        observed_at: datetime | str,
        incident: dict[str, object] | None = None,
    ) -> MaintenanceDailyCheckOutcome:
        """Run the pure check-stage router and record its durable result."""

        stage = run_check_stage(context, preclassification, check_runner)
        if stage.status == "checked_ok":
            check_status = (
                "normal_shortfall"
                if preclassification == "candidate_shortfall"
                else "normal"
            )
        elif stage.status == "waiting_external":
            check_status = "incomplete"
        elif stage.status == "investigation_required":
            check_status = "abnormal"
        elif preclassification == "contract_violation":
            check_status = "abnormal"
        elif preclassification == "unknown":
            check_status = "unknown"
        else:
            check_status = "incomplete"

        if check_status == "abnormal" and incident is None:
            raise ValueError("daily_check_incident_required")

        reason_code = stage.reason_code
        record = self.record_daily_check(
            source_launch_id=str(context.get("source_launch_id", "")),
            source_run_id=context.get("source_run_id"),
            target_date=str(context.get("target_date", "")),
            check_status=check_status,
            observed_at=observed_at,
            reason_code=reason_code,
            incident=incident if check_status == "abnormal" else None,
        )
        return MaintenanceDailyCheckOutcome(stage=stage, record=record)

    def complete_investigation_attempt(
        self, request: InvestigationAttemptRequest
    ) -> InvestigationAttemptOutcome:
        """Persist, independently review, and hash-bind one investigation attempt."""
        context = request.context
        validated = validate_investigation_response(request.response, context)
        attempt = self._store.get_step_attempt(str(context["attempt_id"]))
        if (
            attempt.job_id != context["job_id"]
            or attempt.source_run_id != context["source_run_id"]
            or attempt.step_key != "maintenance_investigation"
            or attempt.status != "running"
        ):
            raise ValueError("step_attempt_identity_invalid")
        review_round = self._store.next_step_review_round(attempt.attempt_id)
        if (
            isinstance(request.review_round, bool)
            or not isinstance(request.review_round, int)
            or request.review_round != review_round
        ):
            raise MaintenanceStoreError("review_round_mismatch")
        prior_review_execution_ids = self._store.prior_u12_review_execution_ids(
            job_id=attempt.job_id,
            sequence=attempt.sequence,
            current_attempt_id=attempt.attempt_id,
            attempts_root=request.attempts_root,
        )
        if request.reviewer_execution_id in prior_review_execution_ids:
            raise MaintenanceStoreError("review_execution_id_reused")
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=request.created_at,
            author_id=request.author_id,
            reviewer_id=request.reviewer_id,
        )

        manifest_identity = _investigation_identity(context)
        self.get_job_input_manifest(
            job_id=str(context["job_id"]), expected_identity=manifest_identity
        )
        (
            facts_path,
            facts_sha256,
            markdown_path,
            markdown_sha256,
        ) = _save_investigation_inputs(
            request,
            attempt.attempt_id,
            validated,
            manifest_identity,
        )
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=request.review_started_at,
            author_id=request.author_id,
            reviewer_id=request.reviewer_id,
        )
        review, review_failure = _run_investigation_review(
            request,
            attempt_id=attempt.attempt_id,
            facts_path=facts_path,
            facts_sha256=facts_sha256,
            markdown_path=markdown_path,
            markdown_sha256=markdown_sha256,
        )
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=request.completed_at,
            author_id=request.author_id,
            reviewer_id=request.reviewer_id,
        )
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=request.reviewed_at,
            author_id=request.author_id,
            reviewer_id=request.reviewer_id,
        )
        handoff_allowed = (
            review["handoff_allowed"] is True
            and validated["status"] == "complete"
            and review["status"] == "approved"
        )
        next_action = (
            review["next_action"] if validated["status"] == "complete" else "blocked"
        )
        store_review_status = str(review["status"])
        if validated["status"] != "complete" and store_review_status == "approved":
            store_review_status = "rejected"
        result_document = _investigation_result_document(
            request,
            attempt_id=attempt.attempt_id,
            identity=manifest_identity,
            validated=validated,
            facts_path=facts_path,
            facts_sha256=facts_sha256,
            markdown_path=markdown_path,
            markdown_sha256=markdown_sha256,
            review=review,
            review_failure=review_failure,
            store_review_status=store_review_status,
            next_action=next_action,
        )
        result_json = (
            json.dumps(
                result_document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        output_path, output_sha256 = save_attempt_artifact(
            request.output_root,
            request.attempts_root,
            context,
            filename="result.json",
            content=result_json,
        )
        self._store.complete_step_attempt_with_review(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            result_version=request.result_version,
            completed_at=request.completed_at,
            output_path=output_path,
            output_sha256=output_sha256,
            reviewer_id=str(review["reviewer_id"]),
            reviewed_version=str(review["reviewed_version"]),
            review_status=store_review_status,
            reviewed_at=request.reviewed_at,
            review_round=review_round,
        )

        accepted_handoff = None
        if handoff_allowed:
            payload = build_investigation_handoff_payload(
                context,
                result_version=request.result_version,
                reviewed_version=str(review["reviewed_version"]),
                output_path=output_path,
                output_sha256=output_sha256,
                facts_path=facts_path,
                facts_sha256=facts_sha256,
                report_path=markdown_path,
                report_sha256=markdown_sha256,
                review_status=str(review["status"]),
            )
            accepted_handoff = self._store.accept_handoff(
                payload,
                handoff_root=request.handoff_root,
                lease=request.lease,
                accepted_at=request.accepted_at,
            )

        return InvestigationAttemptOutcome(
            attempt_id=attempt.attempt_id,
            result_version=request.result_version,
            investigation_status=str(validated["status"]),
            review_status=str(review["status"]),
            next_action=next_action,
            output_path=output_path,
            output_sha256=output_sha256,
            facts_path=facts_path,
            facts_sha256=facts_sha256,
            markdown_path=markdown_path,
            markdown_sha256=markdown_sha256,
            review_findings=tuple(str(item) for item in review["findings"]),
            review_reason_code=review_failure,
            handoff=accepted_handoff,
        )

    def resume_investigation_handoff(
        self,
        *,
        attempt_id: str,
        attempts_root: str | Path,
        handoff_root: str | Path,
        lease: LeaseToken,
        accepted_at: datetime | str,
    ) -> HandoffResult:
        """Retry an approved U12 handoff without rerunning investigation or review."""

        attempt = self._store.get_step_attempt(attempt_id)
        if (
            attempt.step_key != "maintenance_investigation"
            or attempt.status not in {"complete", "handoff_accepted"}
            or not attempt.output_path
            or not attempt.output_sha256
        ):
            raise InvestigationContractError("investigation_handoff_resume_invalid")
        output_bytes = self._store.read_u12_investigation_result(
            attempt_id=attempt.attempt_id,
            attempts_root=attempts_root,
        )
        try:
            result = json.loads(output_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvestigationContractError(
                "investigation_handoff_resume_invalid"
            ) from exc
        if not isinstance(result, dict):
            raise InvestigationContractError("investigation_handoff_resume_invalid")
        identity = result.get("identity")
        review = result.get("review")
        facts = result.get("facts")
        report = result.get("report")
        if (
            not isinstance(identity, dict)
            or not isinstance(review, dict)
            or not isinstance(facts, dict)
            or not isinstance(report, dict)
        ):
            raise InvestigationContractError("investigation_handoff_resume_invalid")
        payload = {
            "schema_version": "maintenance-handoff-v1",
            "incident_id": identity.get("incident_id"),
            "job_id": attempt.job_id,
            "attempt_id": attempt.attempt_id,
            "source_run_id": attempt.source_run_id,
            "result_version": result.get("result_version"),
            "reviewed_version": review.get("reviewed_version"),
            "output": {
                "path": attempt.output_path,
                "sha256": attempt.output_sha256,
                "artifacts": {"facts": facts, "report": report},
            },
        }
        try:
            return self._store.accept_handoff(
                payload,
                handoff_root=handoff_root,
                lease=lease,
                accepted_at=accepted_at,
                resume_completed_u12=True,
            )
        except HandoffContractError as exc:
            raise InvestigationContractError(
                "investigation_handoff_resume_invalid"
            ) from exc

    def bind_ai_parent_session(
        self,
        *,
        job_id: str,
        session_key: str,
        parent_key: str,
        role_name: str,
        thread_id: str,
        manifest_sha256: str,
        requested_model: str,
        requested_effort: str,
        requested_service_tier: str,
        requested_fast_mode: bool,
        role_config_sha256: str,
        sandbox: Mapping[str, object],
        created_at: datetime | str,
    ) -> AIParentSessionRecord:
        return self._store.bind_ai_parent_session(
            job_id=job_id,
            session_key=session_key,
            parent_key=parent_key,
            role_name=role_name,
            thread_id=thread_id,
            manifest_sha256=manifest_sha256,
            requested_model=requested_model,
            requested_effort=requested_effort,
            requested_service_tier=requested_service_tier,
            requested_fast_mode=requested_fast_mode,
            role_config_sha256=role_config_sha256,
            sandbox=sandbox,
            created_at=created_at,
        )

    def get_ai_parent_session(
        self, *, job_id: str, session_key: str
    ) -> AIParentSessionRecord | None:
        return self._store.get_ai_parent_session(
            job_id=job_id,
            session_key=session_key,
        )

    def record_ai_session_observation(
        self,
        *,
        job_id: str,
        session_key: str,
        thread_id: str,
        status: str,
        observed_at: datetime | str,
        observed_model: str | None = None,
        observed_effort: str | None = None,
        observed_service_tier: str | None = None,
        reason_code: str | None = None,
    ) -> str:
        return self._store.record_ai_session_observation(
            job_id=job_id,
            session_key=session_key,
            thread_id=thread_id,
            status=status,
            observed_at=observed_at,
            observed_model=observed_model,
            observed_effort=observed_effort,
            observed_service_tier=observed_service_tier,
            reason_code=reason_code,
        )

    def start_fix_job(
        self,
        *,
        incident_id: str,
        release_id: str,
        job_id: str,
        started_at: datetime | str,
        source_run_id: str | None = None,
    ) -> JobStartResult:
        # The store recomputes the gate inside the same write transaction.
        return self._store.start_fix_job(
            incident_id=incident_id,
            release_id=release_id,
            job_id=job_id,
            started_at=started_at,
            source_run_id=source_run_id,
        )

    def finalize_job(
        self,
        *,
        job_id: str,
        final_status: str,
        lease: LeaseToken,
        finalized_at: datetime | str,
        reason_code: str | None = None,
    ) -> JobFinalizeResult:
        return self._store.finalize_job(
            job_id=job_id,
            final_status=final_status,
            lease=lease,
            finalized_at=finalized_at,
            reason_code=reason_code,
        )
