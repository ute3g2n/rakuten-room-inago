"""Application-level operations for the maintenance state machine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any

from automation.inago.maintenance.diagnostic_trace import (
    MaintenanceDiagnosticTrace,
    audit_maintenance_stage,
)
from automation.inago.maintenance.investigation import (
    InvestigationContractError,
    MaintenanceCheckStageResult,
    _assert_no_reparse_components,
    build_investigation_handoff_payload,
    render_investigation_markdown,
    run_check_stage,
    save_attempt_artifact,
    _validate_context,
    validate_investigation_response,
    validate_investigation_review,
)
from automation.inago.maintenance.planning import (
    PlanningContractError,
    build_requirement_step_case_map,
    render_plan_markdown,
    render_planning_review_markdown,
    validate_planning_response,
    validate_planning_review,
)
from automation.inago.maintenance.repair import (
    RepairAttemptOutcome,
    RepairAttemptRequest,
    RepairContractError,
    RepairController,
    RepairHostEvidence,
    RepairHostEvidenceContext,
    RepairReviewContext,
    RepairReviewEvidence,
    RepositoryRepairEvidenceResolver,
    RepairTestLedger,
    _validate_schema as _validate_repair_schema,
    repository_code_sha256,
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
    RepairHostReviewExecutionRecord,
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
class PlanningAttemptOutcome:
    """Persisted U13 planning artifacts and effective review gate."""

    attempt_id: str
    result_version: str
    plan_status: str
    review_status: str
    gate_status: str
    next_action: str
    output_path: str
    output_sha256: str
    artifacts: Mapping[str, tuple[str, str]]
    review_findings: tuple[str, ...]
    review_reason_code: str | None


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
    time_provider: Callable[[], datetime] | None = None


@dataclass(frozen=True)
class PlanningAttemptRequest:
    """All caller-owned inputs for one U13 plan and review attempt."""

    context: Mapping[str, Any]
    response: object
    attempts_root: str | Path
    output_root: str | Path
    result_version: str
    created_at: datetime | str
    completed_at: datetime | str
    review_started_at: datetime | str
    reviewed_at: datetime | str
    lease: LeaseToken
    author_id: str
    author_execution_id: str
    reviewer_id: str
    reviewer_execution_id: str
    review_runner: Callable[[Mapping[str, object]], object]
    review_round: int
    time_provider: Callable[[], datetime] | None = None


_SAFE_DAILY_CODE = re.compile(r"^[a-z][a-z0-9._:-]{0,127}$")
_SAFE_REVIEW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DAILY_COMPLETION_KEYS = {
    "schema_version",
    "source_launch_id",
    "target_date",
    "status",
    "exit_code",
    "source_run_id",
    "overall_status",
    "daily_status",
    "reason_code",
    "failure_phase",
    "failed_checks",
    "primary_error_class",
    "secondary_error_classes",
    "trace_status",
}


def _validate_planning_workspace(
    context: Mapping[str, Any], trusted_workspace_root: str | Path
) -> tuple[Path, Path]:
    """Bind prompt paths to the host checkout and reject path aliases."""

    try:
        trusted_input = Path(trusted_workspace_root).expanduser()
        supplied_input = Path(context["work_root"]).expanduser()
        if not trusted_input.is_absolute() or not supplied_input.is_absolute():
            raise PlanningContractError("planning_workspace_root_invalid")
        _assert_no_reparse_components(trusted_input)
        _assert_no_reparse_components(supplied_input)
        trusted_root = trusted_input.resolve(strict=True)
        supplied_root = supplied_input.resolve(strict=True)
    except PlanningContractError:
        raise
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise PlanningContractError("planning_workspace_root_invalid") from exc
    if (
        trusted_root != supplied_root
        or not trusted_root.is_dir()
        or not supplied_root.is_dir()
    ):
        raise PlanningContractError("planning_workspace_root_invalid")

    raw_document = context.get("plan_document")
    if not isinstance(raw_document, str) or not raw_document.strip():
        raise PlanningContractError("planning_plan_document_invalid")
    normalized = raw_document.replace("\\", "/")
    posix_document = PurePosixPath(normalized)
    windows_document = PureWindowsPath(raw_document)
    if (
        posix_document.is_absolute()
        or windows_document.is_absolute()
        or bool(windows_document.drive)
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise PlanningContractError("planning_plan_document_invalid")
    try:
        document_path = trusted_root.joinpath(*posix_document.parts)
        _assert_no_reparse_components(document_path)
        resolved_document = document_path.resolve(strict=True)
        resolved_document.relative_to(trusted_root)
    except (InvestigationContractError, OSError, RuntimeError, ValueError) as exc:
        raise PlanningContractError("planning_plan_document_invalid") from exc
    if not resolved_document.is_file():
        raise PlanningContractError("planning_plan_document_invalid")
    return trusted_root, resolved_document


def _module_workspace_root() -> Path:
    """Return the checkout containing this host-side maintenance controller."""

    try:
        module_path = Path(__file__).resolve(strict=True)
        _assert_no_reparse_components(module_path)
        root = module_path.parents[3].resolve(strict=True)
        _assert_no_reparse_components(root)
    except (
        InvestigationContractError,
        IndexError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        raise PlanningContractError("planning_workspace_root_invalid") from exc
    if not root.is_dir():
        raise PlanningContractError("planning_workspace_root_invalid")
    return root


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
        "base_commit": request.context["base_commit"],
        "input_refs": list(request.context["input_refs"]),
        "evidence_refs": list(validated["evidence_refs"]),
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

    def __init__(
        self,
        store: MaintenanceStore,
        *,
        identity_resolver: Callable[[str], object] | None = None,
        repair_host_evidence_resolver: (
            Callable[[RepairHostEvidenceContext], RepairHostEvidence] | None
        ) = None,
        repair_review_receipt_resolver: (
            Callable[[RepairReviewContext], RepairReviewEvidence] | None
        ) = None,
        repair_checkout_root: str | Path | None = None,
        diagnostic_trace: MaintenanceDiagnosticTrace | None = None,
    ) -> None:
        self._store = store
        self._diagnostic_trace = diagnostic_trace or MaintenanceDiagnosticTrace(
            store.database_path.parent.parent
        )
        trusted_identity_resolver = (
            identity_resolver or store.resolve_repair_identity_role
        )
        self._repair_controller = RepairController(
            identity_resolver=trusted_identity_resolver
        )
        self._repair_host_evidence_resolver = (
            repair_host_evidence_resolver
            or RepositoryRepairEvidenceResolver(
                store=store,
                checkout_root=repair_checkout_root,
            )
        )
        self._repair_review_receipt_resolver = repair_review_receipt_resolver

    @audit_maintenance_stage("daily_check")
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

    @audit_maintenance_stage("deployment")
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

    @audit_maintenance_stage("investigation")
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

    @audit_maintenance_stage("repair")
    def begin_repair_attempt(
        self,
        *,
        job_id: str,
        attempts_root: str | Path,
        lease: LeaseToken,
        started_at: datetime | str,
    ) -> StepAttemptResult:
        """Start sequence 3 only from the latest approved, ready U13 plan."""

        try:
            plan_attempt = self._store.get_latest_step_attempt(
                job_id=job_id,
                sequence=2,
            )
            review_record = self._store.get_step_attempt_review(
                attempt_id=plan_attempt.attempt_id
            )
            manifest = self._store.get_job_input_manifest(job_id=job_id)
            if (
                plan_attempt.step_key != "maintenance_planning"
                or plan_attempt.status != "complete"
                or plan_attempt.result_version is None
                or plan_attempt.output_path is None
                or plan_attempt.output_sha256 is None
                or review_record is None
                or review_record["status"] != "approved"
                or review_record["reviewed_version"] != plan_attempt.result_version
            ):
                raise RepairContractError("repair_plan_not_approved")
            raw_result = self._store.read_attempt_artifact(
                attempts_root=attempts_root,
                relative_path=plan_attempt.output_path,
                expected_sha256=plan_attempt.output_sha256,
            )
            result = json.loads(raw_result)
            review = result.get("review") if isinstance(result, dict) else None
            if (
                not isinstance(result, dict)
                or result.get("schema_version") != "maintenance-planning-result-v1"
                or result.get("identity") != manifest.identity()
                or result.get("attempt_id") != plan_attempt.attempt_id
                or result.get("result_version") != plan_attempt.result_version
                or result.get("base_commit")
                != self._store.get_job_base_commit(job_id=job_id)
                or result.get("plan_status") != "ready"
                or result.get("next_action") != "handoff"
                or not isinstance(review, dict)
                or review.get("status") != "approved"
                or review.get("gate_status") != "approved"
                or review.get("reviewed_version") != plan_attempt.result_version
                or review.get("reviewer_id") != review_record["reviewer_id"]
            ):
                raise RepairContractError("repair_plan_not_approved")
        except RepairContractError:
            raise
        except (
            MaintenanceStoreError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise RepairContractError("repair_plan_unavailable") from exc

        return self._store.begin_step_attempt(
            job_id=job_id,
            step_key="maintenance_repair",
            sequence=3,
            lease=lease,
            started_at=started_at,
            repair_predecessor_attempt_id=plan_attempt.attempt_id,
            repair_predecessor_result_version=plan_attempt.result_version,
            repair_predecessor_output_sha256=plan_attempt.output_sha256,
        )

    @audit_maintenance_stage("test")
    def prepare_repair_campaign(
        self,
        *,
        attempt_id: str,
        attempts_root: str | Path,
    ) -> RepairHostEvidence:
        """Resolve the pinned approved plan and open its durable test campaign."""

        try:
            attempt = self._store.get_step_attempt(attempt_id)
            if (
                attempt.step_key != "maintenance_repair"
                or attempt.sequence != 3
                or attempt.status != "running"
            ):
                raise RepairContractError("repair_attempt_identity_invalid")
            pin = self._store.get_repair_step_predecessor(attempt_id=attempt.attempt_id)
            plan_attempt = self._store.get_step_attempt(pin["attempt_id"])
            if plan_attempt.output_path is None:
                raise RepairContractError("repair_plan_not_approved")
            plan_bytes = self._store.read_attempt_artifact(
                attempts_root=attempts_root,
                relative_path=plan_attempt.output_path,
                expected_sha256=pin["output_sha256"],
            )
            plan_result = json.loads(plan_bytes)
            host_context = RepairHostEvidenceContext(
                job_id=attempt.job_id,
                attempt_id=attempt.attempt_id,
                base_commit=self._store.get_latest_deployed_commit(),
                approved_plan=plan_result,
                attempts_root=attempts_root,
            )
            host_evidence = self._repair_host_evidence_resolver(host_context)
        except RepairContractError:
            raise
        except Exception as exc:
            raise RepairContractError(
                getattr(exc, "code", "repair_host_evidence_unavailable")
            ) from exc
        if not isinstance(host_evidence, RepairHostEvidence):
            raise RepairContractError("repair_host_evidence_invalid")
        return host_evidence

    @audit_maintenance_stage("test", finish_event="stage_completed", include_test=True)
    def run_repair_case(
        self, ledger: RepairTestLedger, semantic_case_id: str
    ) -> object:
        """Run and return a single persisted campaign node receipt."""

        runner = getattr(self._repair_host_evidence_resolver, "runner", None)
        if runner is None:
            raise RepairContractError("host_pytest_runner_unavailable")
        return runner.run_case(ledger, semantic_case_id)

    @audit_maintenance_stage("test", finish_event="stage_completed", include_test=True)
    def run_repair_full_regression(self, ledger: RepairTestLedger) -> object:
        """Run the campaign's persisted exact node set and return its receipt."""

        runner = getattr(self._repair_host_evidence_resolver, "runner", None)
        if runner is None:
            raise RepairContractError("host_pytest_runner_unavailable")
        return runner.run_full_regression(ledger)

    @audit_maintenance_stage("repair", include_review=True)
    def record_repair_review_execution(
        self,
        *,
        context: RepairReviewContext,
        reviewer_session_key: str,
        reviewer_turn_id: str,
        reviewer_output_sha256: str,
        approved: bool,
        unresolved_critical_findings: tuple[str, ...],
        created_at: datetime | str,
    ) -> RepairHostReviewExecutionRecord:
        """Persist an idempotent host-observed reviewer decision."""

        if not isinstance(context, RepairReviewContext):
            raise RepairContractError("repair_review_context_invalid")
        try:
            return self._store._record_host_repair_review_execution(
                capability=self._store._repair_review_capability,
                job_id=context.job_id,
                attempt_id=context.attempt_id,
                result_version=context.result_version,
                author_id=context.author_id,
                reviewer_id=context.reviewer_id,
                reviewer_session_key=reviewer_session_key,
                reviewer_turn_id=reviewer_turn_id,
                reviewer_output_sha256=reviewer_output_sha256,
                reviewed_output_sha256=context.reviewed_output_sha256,
                code_sha256=context.code_sha256,
                patch_sha256=context.patch_sha256,
                test_ledger_sha256=context.test_ledger_sha256,
                approved=approved,
                unresolved_critical_findings=unresolved_critical_findings,
                created_at=created_at,
            )
        except MaintenanceStoreError as exc:
            raise RepairContractError(exc.code) from exc

    @audit_maintenance_stage("repair", include_review=True)
    def complete_repair_attempt(
        self, request: RepairAttemptRequest
    ) -> RepairAttemptOutcome:
        """Persist one owned patch, passing regression, and independent review."""

        if not isinstance(request, RepairAttemptRequest):
            raise RepairContractError("repair_request_invalid")
        try:
            context = _validate_context(request.context)
        except InvestigationContractError as exc:
            raise RepairContractError(exc.code) from exc

        try:
            attempt = self._store.get_step_attempt(context["attempt_id"])
            manifest = self._store.get_job_input_manifest(job_id=context["job_id"])
            expected_identity = manifest.identity()
            if (
                attempt.job_id != context["job_id"]
                or attempt.source_run_id != context["source_run_id"]
                or attempt.attempt_id != context["attempt_id"]
                or attempt.step_key != "maintenance_repair"
                or attempt.sequence != 3
                or attempt.status != "running"
                or any(
                    context.get(key) != value
                    for key, value in expected_identity.items()
                )
                or context["base_commit"] != self._store.get_latest_deployed_commit()
            ):
                raise RepairContractError("repair_attempt_identity_invalid")

            predecessor_pin = self._store.get_repair_step_predecessor(
                attempt_id=attempt.attempt_id
            )
            plan_attempt = self._store.get_step_attempt(predecessor_pin["attempt_id"])
            plan_review_record = self._store.get_step_attempt_review(
                attempt_id=plan_attempt.attempt_id
            )
            if (
                plan_attempt.step_key != "maintenance_planning"
                or plan_attempt.status != "complete"
                or plan_attempt.result_version != predecessor_pin["result_version"]
                or plan_attempt.output_sha256 != predecessor_pin["output_sha256"]
                or plan_attempt.output_path is None
                or plan_review_record is None
                or plan_review_record["status"] != "approved"
                or plan_review_record["reviewed_version"] != plan_attempt.result_version
            ):
                raise RepairContractError("repair_plan_not_approved")
            plan_bytes = self._store.read_attempt_artifact(
                attempts_root=request.attempts_root,
                relative_path=plan_attempt.output_path,
                expected_sha256=plan_attempt.output_sha256,
            )
            plan_result = json.loads(plan_bytes)
            plan_review = (
                plan_result.get("review") if isinstance(plan_result, dict) else None
            )
            if (
                not isinstance(plan_result, dict)
                or plan_result.get("schema_version") != "maintenance-planning-result-v1"
                or plan_result.get("identity") != expected_identity
                or plan_result.get("attempt_id") != plan_attempt.attempt_id
                or plan_result.get("result_version") != plan_attempt.result_version
                or plan_result.get("base_commit")
                != self._store.get_job_base_commit(job_id=attempt.job_id)
                or plan_result.get("plan_status") != "ready"
                or plan_result.get("next_action") != "handoff"
                or not isinstance(plan_review, dict)
                or plan_review.get("status") != "approved"
                or plan_review.get("gate_status") != "approved"
                or plan_review.get("reviewed_version") != plan_attempt.result_version
                or plan_review.get("reviewer_id") != plan_review_record["reviewer_id"]
            ):
                raise RepairContractError("repair_plan_not_approved")
        except RepairContractError:
            raise
        except (
            MaintenanceStoreError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise RepairContractError("repair_predecessor_unavailable") from exc

        if self._repair_host_evidence_resolver is None:
            raise RepairContractError("repair_host_evidence_unavailable")
        host_context = RepairHostEvidenceContext(
            job_id=attempt.job_id,
            attempt_id=attempt.attempt_id,
            base_commit=context["base_commit"],
            approved_plan=plan_result,
            attempts_root=request.attempts_root,
        )
        try:
            host_evidence = self._repair_host_evidence_resolver(host_context)
        except Exception as exc:
            raise RepairContractError("repair_host_evidence_unavailable") from exc
        if not isinstance(host_evidence, RepairHostEvidence):
            raise RepairContractError("repair_host_evidence_invalid")

        review = request.review
        current_code_hash = host_evidence.current_code_sha256
        if (
            not isinstance(current_code_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", current_code_hash) is None
            or not isinstance(host_evidence.test_ledger, RepairTestLedger)
            or host_evidence.test_ledger.invocation_parent_id is not None
            or host_evidence.approved_cases is None
            or host_evidence.job_id != attempt.job_id
            or host_evidence.attempt_id != attempt.attempt_id
            or host_evidence.base_commit != context["base_commit"]
            or host_evidence.plan_attempt_id != predecessor_pin["attempt_id"]
            or host_evidence.plan_result_version != predecessor_pin["result_version"]
            or host_evidence.plan_output_sha256 != predecessor_pin["output_sha256"]
            or not host_evidence.checkout_root
            or host_evidence.test_ledger.job_id != attempt.job_id
            or host_evidence.test_ledger.attempt_id != attempt.attempt_id
        ):
            raise RepairContractError("repair_evidence_identity_invalid")

        assessment = self._repair_controller.assess_patch(
            changed_paths=host_evidence.changed_paths,
            owned_paths=host_evidence.owned_paths,
            baseline_assertions=host_evidence.baseline_assertions,
            candidate_assertions=host_evidence.candidate_assertions,
        )
        if not assessment.accepted:
            raise RepairContractError("repair_patch_rejected")
        if (
            not host_evidence.test_ledger.frozen_receipts_match_code_hash(
                current_code_hash
            )
            or host_evidence.test_ledger.completion_status(
                current_code_sha256=current_code_hash
            )
            != "ready"
        ):
            raise RepairContractError("repair_regression_not_ready")
        full_regression = host_evidence.test_ledger.full_regression_record
        approved_cases = dict(host_evidence.approved_cases)
        full_cases = {} if full_regression is None else dict(full_regression.cases)
        frozen_receipts = {
            case_id: receipt
            for case_id, receipt in host_evidence.test_ledger.frozen_pass_receipts.items()
            if case_id not in full_cases
        }
        frozen_case_rows = host_evidence.test_ledger.frozen_cases
        proof_union = {
            case_id: str(receipt.get("node", ""))
            for case_id, receipt in frozen_receipts.items()
        }
        proof_union.update(full_cases)
        frozen_receipts_valid = all(
            case_id in frozen_case_rows
            and frozen_receipts[case_id].get("semantic_case_id") == case_id
            and frozen_receipts[case_id].get("node") == frozen_case_rows[case_id].node
            and frozen_receipts[case_id].get("semantic_signature")
            == frozen_case_rows[case_id].semantic_signature
            and frozen_receipts[case_id].get("outcome") == "PASS"
            and frozen_receipts[case_id].get("code_sha256") == current_code_hash
            and bool(frozen_receipts[case_id].get("receipt_id"))
            and re.fullmatch(
                r"[a-f0-9]{64}",
                str(frozen_receipts[case_id].get("receipt_sha256", "")),
            )
            is not None
            for case_id in frozen_receipts
        )
        if (
            full_regression is None
            or full_regression.receipt_id is None
            or full_regression.receipt_sha256 is None
            or re.fullmatch(r"[a-f0-9]{64}", full_regression.receipt_sha256) is None
            or full_regression.cases != host_evidence.test_ledger.full_regression_cases
            or set(full_regression.results) != set(full_cases)
            or any(status != "PASS" for status in full_regression.results.values())
            or not frozen_receipts_valid
            or proof_union != approved_cases
        ):
            raise RepairContractError("repair_approved_case_receipt_mismatch")

        if (
            not self._repair_controller.validate_review(review)
            or review.tested_result_version != request.result_version
            or review.tested_code_sha256 != current_code_hash
            or review.reviewed_code_sha256 != current_code_hash
        ):
            raise RepairContractError("repair_review_rejected")

        try:
            review_round = self._store.next_step_review_round(attempt.attempt_id)
            self._store.validate_step_attempt_lease(
                attempt_id=attempt.attempt_id,
                lease=request.lease,
                at=request.completed_at,
                author_id=review.author_id,
                reviewer_id=review.reviewer_id,
            )
        except MaintenanceStoreError as exc:
            raise RepairContractError(exc.code) from exc

        identity = expected_identity
        patch_document = {
            "schema_version": "maintenance-repair-patch-assessment-v1",
            "attestation_source": "host_collected",
            "identity": identity,
            "attempt_id": attempt.attempt_id,
            "result_version": request.result_version,
            "tested_code_sha256": current_code_hash,
            "assessment": {
                "accepted": assessment.accepted,
                "reason_codes": list(assessment.reason_codes),
                "changed_paths": list(assessment.changed_paths),
                "assertion_snapshot_sha256": assessment.assertion_snapshot_sha256,
            },
            "owned_paths": list(host_evidence.owned_paths),
            "baseline_assertions": {
                path: dict(assertions)
                for path, assertions in host_evidence.baseline_assertions.items()
            },
            "candidate_assertions": {
                path: dict(assertions)
                for path, assertions in host_evidence.candidate_assertions.items()
            },
        }
        try:
            _validate_repair_schema(
                "maintenance-repair-patch-assessment.schema.json",
                patch_document,
                "repair_patch_artifact_invalid",
            )
            ledger_document = host_evidence.test_ledger.to_document(
                attempt_id=attempt.attempt_id,
                result_version=request.result_version,
                tested_code_sha256=current_code_hash,
                identity=identity,
                test_evidence=host_evidence.test_evidence,
            )
        except RepairContractError:
            raise
        except (TypeError, ValueError) as exc:
            raise RepairContractError("repair_artifact_invalid") from exc

        artifacts: dict[str, tuple[str, str]] = {}
        artifact_context = {
            **context,
            "attempt_id": attempt.attempt_id,
        }

        def save_json_artifact(
            filename: str, document: Mapping[str, object]
        ) -> tuple[str, str]:
            try:
                content = (
                    json.dumps(
                        document,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n"
                )
                saved = save_attempt_artifact(
                    request.output_root,
                    request.attempts_root,
                    artifact_context,
                    filename=filename,
                    content=content,
                    stage="repair",
                )
                artifacts[filename] = saved
                return saved
            except InvestigationContractError as exc:
                raise RepairContractError(exc.code) from exc
            except (TypeError, ValueError) as exc:
                raise RepairContractError("repair_artifact_invalid") from exc

        patch_path, patch_sha256 = save_json_artifact(
            "patch-assessment.json", patch_document
        )
        ledger_path, ledger_sha256 = save_json_artifact(
            "test-ledger.json", ledger_document
        )
        review_target = {
            "schema_version": "maintenance-repair-review-target-v1",
            "attestation_source": "host_collected",
            "identity": identity,
            "job_id": attempt.job_id,
            "attempt_id": attempt.attempt_id,
            "result_version": request.result_version,
            "code_sha256": current_code_hash,
            "full_regression_receipt_id": full_regression.receipt_id,
            "full_regression_receipt_sha256": full_regression.receipt_sha256,
            "output": {
                "patch_assessment": {"path": patch_path, "sha256": patch_sha256},
                "test_ledger": {"path": ledger_path, "sha256": ledger_sha256},
            },
        }
        _validate_repair_schema(
            "maintenance-repair-review-target.schema.json",
            review_target,
            "repair_review_target_invalid",
        )
        review_target_path, review_target_sha256 = save_json_artifact(
            "review-target.json", review_target
        )
        if self._repair_review_receipt_resolver is None:
            raise RepairContractError("repair_review_receipt_unavailable")
        review_context = RepairReviewContext(
            job_id=attempt.job_id,
            attempt_id=attempt.attempt_id,
            result_version=request.result_version,
            author_id=review.author_id,
            reviewer_id=review.reviewer_id,
            reviewed_output_sha256=review_target_sha256,
            code_sha256=current_code_hash,
            patch_sha256=patch_sha256,
            test_ledger_sha256=ledger_sha256,
        )
        try:
            review_evidence = self._repair_review_receipt_resolver(review_context)
        except Exception as exc:
            raise RepairContractError(
                getattr(exc, "code", "repair_review_receipt_unavailable")
            ) from exc
        if repository_code_sha256(host_evidence.checkout_root) != current_code_hash:
            raise RepairContractError("repair_code_hash_drift")
        if (
            not isinstance(review_evidence, RepairReviewEvidence)
            or review_evidence.attempt_id != review_context.attempt_id
            or review_evidence.result_version != review_context.result_version
            or review_evidence.author_id != review_context.author_id
            or review_evidence.reviewer_id != review_context.reviewer_id
            or review_evidence.reviewed_output_sha256
            != review_context.reviewed_output_sha256
            or review_evidence.code_sha256 != review_context.code_sha256
            or review_evidence.patch_sha256 != review_context.patch_sha256
            or review_evidence.test_ledger_sha256 != review_context.test_ledger_sha256
            or review_evidence.approved is not (review.status == "approved")
            or review_evidence.unresolved_critical_findings
            != review.unresolved_critical_findings
        ):
            raise RepairContractError("repair_review_receipt_mismatch")
        review_receipt = None
        if review.status == "approved":
            try:
                review_receipt = self._store.record_repair_review_receipt(
                    capability=self._store._repair_review_capability,
                    job_id=review_context.job_id,
                    attempt_id=review_context.attempt_id,
                    result_version=review_context.result_version,
                    author_id=review_context.author_id,
                    reviewer_id=review_context.reviewer_id,
                    host_receipt_id=review_evidence.host_receipt_id,
                    reviewed_output_sha256=review_context.reviewed_output_sha256,
                    code_sha256=review_context.code_sha256,
                    patch_sha256=review_context.patch_sha256,
                    test_ledger_sha256=review_context.test_ledger_sha256,
                    created_at=request.reviewed_at,
                )
            except MaintenanceStoreError as exc:
                raise RepairContractError(exc.code) from exc
        elif (
            review.status != "changes_requested"
            or not review.unresolved_critical_findings
            or not isinstance(review_evidence.reviewer_session_key, str)
            or not isinstance(review_evidence.reviewer_turn_id, str)
            or not isinstance(review_evidence.reviewer_output_sha256, str)
            or re.fullmatch(r"[a-f0-9]{64}", review_evidence.reviewer_output_sha256)
            is None
        ):
            raise RepairContractError("repair_review_receipt_mismatch")
        review_document = {
            "schema_version": "maintenance-repair-review-v1",
            "attestation_source": "host_collected",
            "status": review.status,
            "identity": identity,
            "attempt_id": attempt.attempt_id,
            "result_version": request.result_version,
            "author_id": review.author_id,
            "reviewer_id": review.reviewer_id,
            "tested_result_version": review.tested_result_version,
            "reviewed_result_version": review.reviewed_result_version,
            "unresolved_critical_findings": list(
                review_evidence.unresolved_critical_findings
            ),
            "tested_code_sha256": current_code_hash,
            "reviewed_code_sha256": current_code_hash,
            "host_receipt_id": review_evidence.host_receipt_id,
            "receipt_id": None if review_receipt is None else review_receipt.receipt_id,
            "receipt_sha256": (
                None if review_receipt is None else review_receipt.receipt_sha256
            ),
            "reviewed_output_sha256": review_context.reviewed_output_sha256,
            "patch_sha256": patch_sha256,
            "test_ledger_sha256": ledger_sha256,
        }
        _validate_repair_schema(
            "maintenance-repair-review.schema.json",
            review_document,
            "repair_review_artifact_invalid",
        )
        save_json_artifact("review.json", review_document)

        result_document = {
            "schema_version": "maintenance-repair-result-v1",
            "attestation_source": "host_collected",
            "identity": identity,
            "attempt_id": attempt.attempt_id,
            "result_version": request.result_version,
            "tested_code_sha256": current_code_hash,
            "repair_status": "complete",
            "completion_status": (
                "ready" if review.status == "approved" else "needs_changes"
            ),
            "review_status": review.status,
            "output": {
                "patch_assessment": {
                    "path": artifacts["patch-assessment.json"][0],
                    "sha256": artifacts["patch-assessment.json"][1],
                },
                "test_ledger": {
                    "path": artifacts["test-ledger.json"][0],
                    "sha256": artifacts["test-ledger.json"][1],
                },
                "review_target": {
                    "path": review_target_path,
                    "sha256": review_target_sha256,
                },
                "review": {
                    "path": artifacts["review.json"][0],
                    "sha256": artifacts["review.json"][1],
                },
            },
            "reviewer_id": review.reviewer_id,
            "reviewed_result_version": review.reviewed_result_version,
            "reviewed_code_sha256": review.reviewed_code_sha256,
            "reviewed_output_sha256": review_context.reviewed_output_sha256,
        }
        try:
            _validate_repair_schema(
                "maintenance-repair-result.schema.json",
                result_document,
                "repair_result_invalid",
            )
            result_content = (
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
                artifact_context,
                filename="result.json",
                content=result_content,
                stage="repair",
            )
        except RepairContractError:
            raise
        except InvestigationContractError as exc:
            raise RepairContractError(exc.code) from exc
        except (TypeError, ValueError) as exc:
            raise RepairContractError("repair_result_invalid") from exc

        if repository_code_sha256(host_evidence.checkout_root) != current_code_hash:
            raise RepairContractError("repair_code_hash_drift")

        try:
            self._store.complete_step_attempt_with_review(
                attempt_id=attempt.attempt_id,
                lease=request.lease,
                result_version=request.result_version,
                completed_at=request.completed_at,
                output_path=output_path,
                output_sha256=output_sha256,
                reviewer_id=review.reviewer_id,
                reviewed_version=review.reviewed_result_version,
                review_status=review.status,
                reviewed_at=request.reviewed_at,
                review_round=review_round,
                repair_review_receipt_id=(
                    None if review_receipt is None else review_receipt.receipt_id
                ),
                repair_review_expected=(
                    {
                        "reviewed_output_sha256": (
                            review_context.reviewed_output_sha256
                        ),
                        "code_sha256": current_code_hash,
                        "patch_sha256": patch_sha256,
                        "test_ledger_sha256": ledger_sha256,
                        "host_receipt_id": review_evidence.host_receipt_id,
                        "receipt_sha256": review_receipt.receipt_sha256,
                    }
                    if review_receipt is not None
                    else {
                        "host_receipt_id": review_evidence.host_receipt_id,
                        "reviewer_session_key": review_evidence.reviewer_session_key,
                        "reviewer_turn_id": review_evidence.reviewer_turn_id,
                        "reviewer_output_sha256": review_evidence.reviewer_output_sha256,
                        "reviewed_output_sha256": (
                            review_context.reviewed_output_sha256
                        ),
                        "code_sha256": current_code_hash,
                        "patch_sha256": patch_sha256,
                        "test_ledger_sha256": ledger_sha256,
                        "unresolved_critical_findings": list(
                            review_evidence.unresolved_critical_findings
                        ),
                    }
                ),
            )
        except MaintenanceStoreError as exc:
            raise RepairContractError(exc.code) from exc

        artifacts["result.json"] = (output_path, output_sha256)
        return RepairAttemptOutcome(
            attempt_id=attempt.attempt_id,
            result_version=request.result_version,
            code_sha256=current_code_hash,
            review_status=review.status,
            output_path=output_path,
            output_sha256=output_sha256,
            artifacts=artifacts,
        )

    @audit_maintenance_stage("daily_check")
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

    @audit_maintenance_stage("investigation", include_review=True)
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
        review_started_at = (
            request.time_provider()
            if request.time_provider is not None
            else request.review_started_at
        )
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=review_started_at,
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
        completed_at = (
            request.time_provider()
            if request.time_provider is not None
            else request.completed_at
        )
        reviewed_at = (
            request.time_provider()
            if request.time_provider is not None
            else request.reviewed_at
        )
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=completed_at,
            author_id=request.author_id,
            reviewer_id=request.reviewer_id,
        )
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=reviewed_at,
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
            completed_at=completed_at,
            output_path=output_path,
            output_sha256=output_sha256,
            reviewer_id=str(review["reviewer_id"]),
            reviewed_version=str(review["reviewed_version"]),
            review_status=store_review_status,
            reviewed_at=reviewed_at,
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
                accepted_at=(
                    request.time_provider()
                    if request.time_provider is not None
                    else request.accepted_at
                ),
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

    def _load_planning_attempt_identity(
        self, context: Mapping[str, Any]
    ) -> tuple[StepAttemptRecord, JobInputManifest]:
        try:
            attempt = self._store.get_step_attempt(str(context["attempt_id"]))
        except (KeyError, MaintenanceStoreError) as exc:
            raise PlanningContractError("planning_attempt_identity_invalid") from exc
        if (
            attempt.job_id != context.get("job_id")
            or attempt.source_run_id != context.get("source_run_id")
            or attempt.step_key != "maintenance_planning"
            or attempt.sequence != 2
            or attempt.status != "running"
        ):
            raise PlanningContractError("planning_attempt_identity_invalid")
        manifest = self._store.get_job_input_manifest(
            job_id=attempt.job_id,
            expected_identity=_investigation_identity(context),
        )
        if self._store.get_job_base_commit(job_id=attempt.job_id) != context.get(
            "base_commit"
        ):
            raise PlanningContractError("planning_base_commit_mismatch")
        return attempt, manifest

    def _load_approved_planning_investigation(
        self,
        request: PlanningAttemptRequest,
        context: Mapping[str, Any],
        attempt: StepAttemptRecord,
        manifest: JobInputManifest,
    ) -> tuple[StepAttemptRecord, dict[str, Any], dict[str, Any]]:
        investigation_attempt = self._get_approved_planning_investigation_attempt(
            context, attempt
        )
        result = self._read_planning_investigation_result(
            request, context, investigation_attempt, manifest
        )
        facts_ref = result["facts"]
        used_refs = result["evidence_refs"]
        facts_document = self._read_planning_investigation_facts(
            request,
            attempt,
            investigation_attempt,
            manifest.identity(),
            facts_ref,
            used_refs,
        )
        return investigation_attempt, facts_ref, facts_document

    def _get_approved_planning_investigation_attempt(
        self, context: Mapping[str, Any], attempt: StepAttemptRecord
    ) -> StepAttemptRecord:
        try:
            investigation_attempt = self._store.get_latest_step_attempt(
                job_id=attempt.job_id,
                sequence=1,
            )
            review_record = self._store.get_step_attempt_review(
                attempt_id=investigation_attempt.attempt_id
            )
        except MaintenanceStoreError as exc:
            raise PlanningContractError("planning_investigation_unavailable") from exc
        if (
            investigation_attempt.step_key != "maintenance_investigation"
            or investigation_attempt.attempt_id
            != context.get("investigation_attempt_id")
            or investigation_attempt.status != "handoff_accepted"
            or review_record is None
            or review_record["status"] != "approved"
            or review_record["reviewed_version"] != investigation_attempt.result_version
        ):
            raise PlanningContractError("planning_investigation_not_approved")
        if investigation_attempt.result_version != context.get(
            "investigation_result_version"
        ):
            raise PlanningContractError("planning_investigation_version_mismatch")
        return investigation_attempt

    def _read_planning_investigation_result(
        self,
        request: PlanningAttemptRequest,
        context: Mapping[str, Any],
        investigation_attempt: StepAttemptRecord,
        manifest: JobInputManifest,
    ) -> dict[str, Any]:
        try:
            result_bytes = self._store.read_u12_investigation_result(
                attempt_id=investigation_attempt.attempt_id,
                attempts_root=request.attempts_root,
            )
            result = json.loads(result_bytes)
        except (MaintenanceStoreError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlanningContractError(
                "planning_investigation_result_unavailable"
            ) from exc
        expected_identity = manifest.identity()
        if (
            not isinstance(result, dict)
            or result.get("schema_version") != "maintenance-investigation-result-v1"
            or result.get("identity") != expected_identity
            or result.get("attempt_id") != investigation_attempt.attempt_id
            or result.get("result_version") != investigation_attempt.result_version
            or result.get("investigation_status") != "complete"
            or result.get("base_commit") != context.get("base_commit")
            or result.get("input_refs") != context.get("input_refs")
            or not isinstance(result.get("facts"), dict)
        ):
            raise PlanningContractError("planning_investigation_result_invalid")
        review = result.get("review")
        if (
            not isinstance(review, dict)
            or review.get("status") != "approved"
            or review.get("gate_status") != "approved"
            or review.get("reviewed_version") != investigation_attempt.result_version
        ):
            raise PlanningContractError("planning_investigation_not_approved")
        if _investigation_identity(context) != expected_identity or context.get(
            "investigation_result_version"
        ) != result.get("result_version"):
            raise PlanningContractError("planning_investigation_version_mismatch")
        self._validate_planning_used_evidence_refs(context, result)
        return result

    @staticmethod
    def _validate_planning_used_evidence_refs(
        context: Mapping[str, Any], result: Mapping[str, Any]
    ) -> None:
        allowed_refs = context.get("evidence_refs")
        used_refs = result.get("evidence_refs")
        if (
            not isinstance(allowed_refs, list)
            or any(not isinstance(ref, str) for ref in allowed_refs)
            or len(set(allowed_refs)) != len(allowed_refs)
            or not isinstance(used_refs, list)
            or any(not isinstance(ref, str) for ref in used_refs)
            or len(set(used_refs)) != len(used_refs)
            or not set(used_refs).issubset(allowed_refs)
        ):
            raise PlanningContractError("planning_investigation_result_invalid")

    def _read_planning_investigation_facts(
        self,
        request: PlanningAttemptRequest,
        attempt: StepAttemptRecord,
        investigation_attempt: StepAttemptRecord,
        expected_identity: Mapping[str, Any],
        facts_ref: dict[str, Any],
        used_refs: list[str],
    ) -> dict[str, Any]:
        expected_facts_path = (
            f"{attempt.job_id}/{investigation_attempt.attempt_id}/facts.json"
        )
        if facts_ref.get("path") != expected_facts_path:
            raise PlanningContractError("planning_investigation_facts_invalid")
        try:
            facts_bytes = self._store.read_attempt_artifact(
                attempts_root=request.attempts_root,
                relative_path=expected_facts_path,
                expected_sha256=str(facts_ref.get("sha256", "")),
            )
            facts_document = json.loads(facts_bytes)
        except (MaintenanceStoreError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlanningContractError(
                "planning_investigation_facts_unavailable"
            ) from exc
        self._validate_planning_facts_document(
            facts_document, investigation_attempt, expected_identity, used_refs
        )
        return facts_document

    @staticmethod
    def _validate_planning_facts_document(
        facts_document: object,
        investigation_attempt: StepAttemptRecord,
        expected_identity: Mapping[str, Any],
        used_refs: list[str],
    ) -> None:
        if (
            not isinstance(facts_document, dict)
            or facts_document.get("schema_version")
            != "maintenance-investigation-facts-v1"
            or facts_document.get("identity") != expected_identity
            or facts_document.get("attempt_id") != investigation_attempt.attempt_id
            or facts_document.get("result_version")
            != investigation_attempt.result_version
            or not isinstance(facts_document.get("facts"), list)
        ):
            raise PlanningContractError("planning_investigation_facts_invalid")
        facts_refs = facts_document.get("evidence_refs")
        facts = facts_document["facts"]
        if (
            not isinstance(facts_refs, list)
            or any(not isinstance(ref, str) for ref in facts_refs)
            or facts_refs != used_refs
            or any(
                not isinstance(fact, Mapping)
                or not isinstance(fact.get("evidence_refs"), list)
                or any(not isinstance(ref, str) for ref in fact["evidence_refs"])
                or not set(fact["evidence_refs"]).issubset(used_refs)
                for fact in facts
            )
        ):
            raise PlanningContractError("planning_investigation_facts_invalid")

    def _validate_planning_review_request(
        self, request: PlanningAttemptRequest, attempt: StepAttemptRecord
    ) -> int:
        for value in (
            request.result_version,
            request.author_id,
            request.author_execution_id,
            request.reviewer_id,
            request.reviewer_execution_id,
        ):
            if not isinstance(value, str) or _SAFE_REVIEW_ID.fullmatch(value) is None:
                raise PlanningContractError("planning_review_identity_invalid")
        review_round = self._store.next_step_review_round(attempt.attempt_id)
        if (
            isinstance(request.review_round, bool)
            or not isinstance(request.review_round, int)
            or request.review_round != review_round
        ):
            raise MaintenanceStoreError("review_round_mismatch")
        if (
            request.author_id == request.reviewer_id
            or request.author_execution_id == request.reviewer_execution_id
        ):
            raise PlanningContractError("planning_review_not_independent")
        prior_review_ids = self._store.prior_step_review_execution_ids(
            job_id=attempt.job_id,
            step_key=attempt.step_key,
            sequence=attempt.sequence,
            current_attempt_id=attempt.attempt_id,
            attempts_root=request.attempts_root,
        )
        if request.reviewer_execution_id in prior_review_ids:
            raise MaintenanceStoreError("review_execution_id_reused")
        return review_round

    def _load_daily_completion(
        self, manifest: JobInputManifest
    ) -> tuple[dict[str, Any], str]:
        daily_launch = self._store.get_daily_launch(manifest.source_launch_id)
        if (
            manifest.source_launch_id.startswith("historical-")
            and daily_launch is not None
        ):
            raise PlanningContractError("planning_historical_daily_launch_present")
        if daily_launch is None:
            if (
                not manifest.source_launch_id.startswith("historical-")
                or manifest.source_run_id is not None
            ):
                raise PlanningContractError("planning_daily_completion_unavailable")
            try:
                from automation.inago.maintenance.historical import (
                    load_historical_work_item,
                )

                historical = load_historical_work_item(
                    self._store,
                    data_root=self._store.database_path.parent.parent,
                    source_launch_id=manifest.source_launch_id,
                )
            except Exception as exc:
                reason_code = getattr(exc, "code", None)
                if not isinstance(reason_code, str) or not _SAFE_DAILY_CODE.fullmatch(
                    reason_code
                ):
                    reason_code = "planning_historical_completion_invalid"
                raise PlanningContractError(reason_code) from exc
            if (
                historical.manifest.identity() != manifest.identity()
                or historical.manifest.manifest_sha256 != manifest.manifest_sha256
            ):
                raise PlanningContractError("planning_historical_manifest_mismatch")
            observation = historical.observation
            summary = {
                "schema_version": "historical-daily-completion-v1",
                "source_launch_id": historical.source_launch_id,
                "source_run_id": None,
                "target_date": historical.target_date,
                "status": "failed",
                "overall_status": "failed",
                "daily_status": observation["daily_status"],
                "launch_status": observation["launch_status"],
                "exit_code": observation["exit_code"],
                "reason_code": observation["reason_code"],
                "intake_ref": historical.intake_ref,
                "observation_ref": historical.observation_ref,
                "evidence_ref": historical.evidence_ref,
                "evidence_sha256": observation["evidence_sha256"],
                "observed_at": observation["observed_at"],
            }
            return (
                summary,
                hashlib.sha256(historical.intake_record_bytes).hexdigest(),
            )
        if (
            daily_launch.result_json is None
            or daily_launch.target_date != manifest.target_date
            or daily_launch.source_run_id != manifest.source_run_id
        ):
            raise PlanningContractError("planning_daily_completion_unavailable")
        try:
            summary = json.loads(daily_launch.result_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PlanningContractError("planning_daily_completion_invalid") from exc
        if (
            not isinstance(summary, dict)
            or set(summary) != _DAILY_COMPLETION_KEYS
            or summary.get("schema_version") != "daily-launch-completion-v1"
            or summary.get("source_launch_id") != manifest.source_launch_id
            or summary.get("target_date") != manifest.target_date
            or summary.get("source_run_id") != manifest.source_run_id
            or summary.get("status") != daily_launch.status
            or summary.get("exit_code") != daily_launch.exit_code
        ):
            raise PlanningContractError("planning_daily_completion_invalid")
        for field in ("overall_status", "daily_status"):
            value = summary.get(field)
            if not isinstance(value, str) or _SAFE_DAILY_CODE.fullmatch(value) is None:
                raise PlanningContractError("planning_daily_completion_invalid")
        for field in ("reason_code", "failure_phase"):
            value = summary.get(field)
            if value is not None and (
                not isinstance(value, str) or _SAFE_DAILY_CODE.fullmatch(value) is None
            ):
                raise PlanningContractError("planning_daily_completion_invalid")
        trace_status = summary.get("trace_status")
        if not isinstance(trace_status, str) or trace_status not in {
            "healthy",
            "degraded",
            "unavailable",
            "not_written",
        }:
            raise PlanningContractError("planning_daily_completion_invalid")
        return (
            summary,
            hashlib.sha256(daily_launch.result_json.encode("utf-8")).hexdigest(),
        )

    def _load_planning_dependencies(
        self, request: PlanningAttemptRequest
    ) -> dict[str, Any]:
        context = dict(request.context)
        attempt, manifest = self._load_planning_attempt_identity(context)
        (
            investigation_attempt,
            facts_ref,
            facts_document,
        ) = self._load_approved_planning_investigation(
            request, context, attempt, manifest
        )
        review_round = self._validate_planning_review_request(request, attempt)
        trusted_root, _ = _validate_planning_workspace(
            context, _module_workspace_root()
        )
        daily_completion, daily_hash = self._load_daily_completion(manifest)
        return {
            "context": context,
            "attempt": attempt,
            "manifest": manifest,
            "investigation_attempt": investigation_attempt,
            "facts_ref": facts_ref,
            "facts_document": facts_document,
            "facts": facts_document["facts"],
            "review_round": review_round,
            "trusted_root": trusted_root,
            "daily_completion": daily_completion,
            "daily_completion_sha256": daily_hash,
        }

    def _save_planning_artifact(
        self,
        request: PlanningAttemptRequest,
        context: Mapping[str, Any],
        filename: str,
        content: str | bytes,
    ) -> tuple[str, str]:
        artifact_context = {
            key: context[key]
            for key in (
                "incident_id",
                "source_launch_id",
                "source_run_id",
                "target_date",
                "job_id",
                "attempt_id",
                "base_commit",
                "input_refs",
                "evidence_refs",
            )
        }
        try:
            return save_attempt_artifact(
                request.output_root,
                request.attempts_root,
                artifact_context,
                filename=filename,
                content=content,
                stage="planning",
            )
        except InvestigationContractError as exc:
            raise PlanningContractError(exc.code) from exc

    def _save_planning_plan_artifacts(
        self,
        request: PlanningAttemptRequest,
        dependencies: Mapping[str, Any],
        validated: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan_markdown = render_plan_markdown(validated)
        plan_path, plan_hash = self._save_planning_artifact(
            request, dependencies["context"], "plan.md", plan_markdown
        )
        requirement_map = build_requirement_step_case_map(
            validated,
            result_version=request.result_version,
            investigation_version=str(
                dependencies["investigation_attempt"].result_version
            ),
        )
        map_json = (
            json.dumps(
                requirement_map,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        map_path, map_hash = self._save_planning_artifact(
            request,
            dependencies["context"],
            "requirement-step-case-map.json",
            map_json,
        )
        repair_scope_json = (
            json.dumps(
                {"repair_scope": validated["repair_scope"]},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        repair_scope_path, repair_scope_hash = self._save_planning_artifact(
            request,
            dependencies["context"],
            "repair-scope.json",
            repair_scope_json,
        )
        return {
            "plan_markdown": plan_markdown,
            "plan_path": plan_path,
            "plan_hash": plan_hash,
            "requirement_map": requirement_map,
            "map_path": map_path,
            "map_hash": map_hash,
            "repair_scope": validated["repair_scope"],
            "repair_scope_path": repair_scope_path,
            "repair_scope_hash": repair_scope_hash,
            "artifacts": {
                "plan.md": (plan_path, plan_hash),
                "requirement-step-case-map.json": (map_path, map_hash),
                "repair-scope.json": (repair_scope_path, repair_scope_hash),
            },
        }

    @staticmethod
    def _build_planning_review_input(
        request: PlanningAttemptRequest,
        dependencies: Mapping[str, Any],
        plan_artifacts: Mapping[str, Any],
    ) -> dict[str, Any]:
        investigation_attempt = dependencies["investigation_attempt"]
        attempt = dependencies["attempt"]
        return {
            "attempt_id": attempt.attempt_id,
            "result_version": request.result_version,
            "investigation_attempt_id": investigation_attempt.attempt_id,
            "investigation_result_version": investigation_attempt.result_version,
            "investigation_facts_path": dependencies["facts_ref"]["path"],
            "investigation_facts_sha256": dependencies["facts_ref"]["sha256"],
            "investigation_facts": dependencies["facts_document"],
            "daily_completion": dependencies["daily_completion"],
            "daily_completion_sha256": dependencies["daily_completion_sha256"],
            "plan_path": plan_artifacts["plan_path"],
            "plan_sha256": plan_artifacts["plan_hash"],
            "plan_markdown": plan_artifacts["plan_markdown"],
            "repair_scope": plan_artifacts["repair_scope"],
            "repair_scope_path": plan_artifacts["repair_scope_path"],
            "repair_scope_sha256": plan_artifacts["repair_scope_hash"],
            "requirement_step_case_map_path": plan_artifacts["map_path"],
            "requirement_step_case_map_sha256": plan_artifacts["map_hash"],
            "requirement_step_case_map": plan_artifacts["requirement_map"],
            "review_execution_id": request.reviewer_execution_id,
        }

    @staticmethod
    def _invoke_planning_review(
        request: PlanningAttemptRequest, review_input: Mapping[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        review_failure: str | None = None
        try:
            review = validate_planning_review(
                request.review_runner(review_input),
                result_version=request.result_version,
                author_id=request.author_id,
                author_execution_id=request.author_execution_id,
                reviewer_id=request.reviewer_id,
                reviewer_execution_id=request.reviewer_execution_id,
            )
        except Exception as exc:
            review_failure = (
                exc.code
                if isinstance(exc, PlanningContractError)
                else "review_runner_failed"
            )
            review = {
                "schema_version": "maintenance-planning-review-v1",
                "stage": "planning_review",
                "reviewer_id": request.reviewer_id,
                "review_execution_id": request.reviewer_execution_id,
                "reviewed_version": request.result_version,
                "status": "unavailable",
                "findings": [review_failure],
            }
        return review, review_failure

    @staticmethod
    def _resolve_planning_review_gate(
        plan_status: str,
        review_status: str,
        review_failure: str | None,
    ) -> tuple[str, str | None, str]:
        gate_status = review_status
        if plan_status in {"waiting_external", "blocked"} and gate_status == "approved":
            gate_status = "changes_requested"
            review_failure = review_failure or (
                "planning_external_wait"
                if plan_status == "waiting_external"
                else "planning_plan_blocked"
            )
        if plan_status == "blocked":
            next_action = "blocked"
        elif plan_status == "waiting_external" and review_status == "approved":
            next_action = "wait_external"
        elif gate_status == "approved":
            next_action = "handoff"
        elif gate_status == "changes_requested":
            next_action = "revise"
        else:
            next_action = "blocked"
        return gate_status, review_failure, next_action

    def _save_planning_review_artifacts(
        self,
        request: PlanningAttemptRequest,
        context: Mapping[str, Any],
        review: Mapping[str, Any],
        gate_status: str,
    ) -> dict[str, Any]:
        review_json = (
            json.dumps(
                review,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        review_path, review_hash = self._save_planning_artifact(
            request, context, "review.json", review_json
        )
        review_markdown_path, review_markdown_hash = self._save_planning_artifact(
            request,
            context,
            "review.md",
            render_planning_review_markdown(review, gate_status=gate_status),
        )
        return {
            "review.json": (review_path, review_hash),
            "review.md": (review_markdown_path, review_markdown_hash),
            "review_path": review_path,
            "review_hash": review_hash,
            "review_markdown_path": review_markdown_path,
            "review_markdown_hash": review_markdown_hash,
        }

    def _run_planning_review(
        self,
        request: PlanningAttemptRequest,
        dependencies: Mapping[str, Any],
        validated: Mapping[str, Any],
        plan_artifacts: Mapping[str, Any],
    ) -> dict[str, Any]:
        attempt = dependencies["attempt"]
        review_started_at = (
            request.time_provider()
            if request.time_provider is not None
            else request.review_started_at
        )
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=review_started_at,
            author_id=request.author_id,
            reviewer_id=request.reviewer_id,
        )
        review_input = self._build_planning_review_input(
            request, dependencies, plan_artifacts
        )
        review, review_failure = self._invoke_planning_review(request, review_input)
        reviewed_at = (
            request.time_provider()
            if request.time_provider is not None
            else request.reviewed_at
        )
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=reviewed_at,
            author_id=request.author_id,
            reviewer_id=request.reviewer_id,
        )
        gate_status, review_failure, next_action = self._resolve_planning_review_gate(
            str(validated["status"]), str(review["status"]), review_failure
        )
        artifacts = self._save_planning_review_artifacts(
            request, dependencies["context"], review, gate_status
        )
        return {
            "review": review,
            "gate_status": gate_status,
            "reason_code": review_failure,
            "next_action": next_action,
            "reviewed_at": reviewed_at,
            "artifacts": {
                "review.json": artifacts["review.json"],
                "review.md": artifacts["review.md"],
            },
            **{
                key: value
                for key, value in artifacts.items()
                if key not in {"review.json", "review.md"}
            },
        }

    @staticmethod
    def _planning_investigation_result(
        dependencies: Mapping[str, Any],
    ) -> dict[str, Any]:
        investigation_attempt = dependencies["investigation_attempt"]
        facts_ref = dependencies["facts_ref"]
        return {
            "attempt_id": investigation_attempt.attempt_id,
            "result_version": investigation_attempt.result_version,
            "output_path": investigation_attempt.output_path,
            "output_sha256": investigation_attempt.output_sha256,
            "facts_path": facts_ref["path"],
            "facts_sha256": facts_ref["sha256"],
            "evidence_refs": list(dependencies["facts_document"]["evidence_refs"]),
        }

    @staticmethod
    def _planning_output_result(
        plan_artifacts: Mapping[str, Any],
        review_outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "plan": {
                "path": plan_artifacts["plan_path"],
                "sha256": plan_artifacts["plan_hash"],
            },
            "repair_scope": {
                "path": plan_artifacts["repair_scope_path"],
                "sha256": plan_artifacts["repair_scope_hash"],
            },
            "requirement_step_case_map": {
                "path": plan_artifacts["map_path"],
                "sha256": plan_artifacts["map_hash"],
            },
            "review": {
                "path": review_outcome["review_path"],
                "sha256": review_outcome["review_hash"],
            },
            "review_markdown": {
                "path": review_outcome["review_markdown_path"],
                "sha256": review_outcome["review_markdown_hash"],
            },
        }

    @staticmethod
    def _planning_review_result(
        request: PlanningAttemptRequest,
        dependencies: Mapping[str, Any],
        review_outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        review = review_outcome["review"]
        return {
            "author_id": request.author_id,
            "author_execution_id": request.author_execution_id,
            "reviewer_id": review["reviewer_id"],
            "review_execution_id": review["review_execution_id"],
            "reviewed_version": review["reviewed_version"],
            "review_round": dependencies["review_round"],
            "status": review["status"],
            "gate_status": review_outcome["gate_status"],
            "reason_code": review_outcome["reason_code"],
            "findings": list(review["findings"]),
        }

    def _build_planning_result(
        self,
        request: PlanningAttemptRequest,
        dependencies: Mapping[str, Any],
        validated: Mapping[str, Any],
        plan_artifacts: Mapping[str, Any],
        review_outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        context = dependencies["context"]
        attempt = dependencies["attempt"]
        manifest = dependencies["manifest"]
        return {
            "schema_version": "maintenance-planning-result-v1",
            "identity": manifest.identity(),
            "source_run_id": manifest.source_run_id,
            "attempt_id": attempt.attempt_id,
            "result_version": request.result_version,
            "plan_status": validated["status"],
            "summary": validated["summary"],
            "workspace_root": str(dependencies["trusted_root"]),
            "plan_document": context["plan_document"],
            "repair_scope": validated["repair_scope"],
            "daily_completion_sha256": dependencies["daily_completion_sha256"],
            "investigation": self._planning_investigation_result(dependencies),
            "base_commit": context["base_commit"],
            "input_refs": list(context["input_refs"]),
            "evidence_refs": list(context["evidence_refs"]),
            "output": self._planning_output_result(plan_artifacts, review_outcome),
            "review": self._planning_review_result(
                request, dependencies, review_outcome
            ),
            "logging_contract": validated["logging_contract"],
            "hold_conditions": validated["hold_conditions"],
            "final_explanation": validated["final_explanation"],
            "next_action": review_outcome["next_action"],
        }

    def _persist_planning_completion(
        self,
        request: PlanningAttemptRequest,
        dependencies: Mapping[str, Any],
        result_document: Mapping[str, Any],
        review_outcome: Mapping[str, Any],
    ) -> tuple[str, str]:
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
        output_path, output_hash = self._save_planning_artifact(
            request, dependencies["context"], "result.json", result_json
        )
        attempt = dependencies["attempt"]
        completed_at = (
            request.time_provider()
            if request.time_provider is not None
            else request.completed_at
        )
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=completed_at,
            author_id=request.author_id,
            reviewer_id=request.reviewer_id,
        )
        review = review_outcome["review"]
        self._store.complete_step_attempt_with_review(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            result_version=request.result_version,
            completed_at=completed_at,
            output_path=output_path,
            output_sha256=output_hash,
            reviewer_id=review["reviewer_id"],
            reviewed_version=review["reviewed_version"],
            review_status=review_outcome["gate_status"],
            reviewed_at=review_outcome["reviewed_at"],
            review_round=dependencies["review_round"],
        )
        return output_path, output_hash

    @staticmethod
    def _planning_attempt_outcome(
        request: PlanningAttemptRequest,
        attempt_id: str,
        validated: Mapping[str, Any],
        plan_artifacts: Mapping[str, Any],
        review_outcome: Mapping[str, Any],
        output_path: str,
        output_hash: str,
    ) -> PlanningAttemptOutcome:
        review = review_outcome["review"]
        artifacts = dict(plan_artifacts["artifacts"])
        artifacts.update(review_outcome["artifacts"])
        artifacts["result.json"] = (output_path, output_hash)
        return PlanningAttemptOutcome(
            attempt_id=attempt_id,
            result_version=request.result_version,
            plan_status=validated["status"],
            review_status=review["status"],
            gate_status=review_outcome["gate_status"],
            next_action=review_outcome["next_action"],
            output_path=output_path,
            output_sha256=output_hash,
            artifacts=artifacts,
            review_findings=tuple(review["findings"]),
            review_reason_code=review_outcome["reason_code"],
        )

    @audit_maintenance_stage("planning", include_review=True)
    def complete_planning_attempt(
        self, request: PlanningAttemptRequest
    ) -> PlanningAttemptOutcome:
        """Persist a fact-bound plan and its independent review for U13."""

        dependencies = self._load_planning_dependencies(request)
        validated = validate_planning_response(
            request.response,
            dependencies["context"],
            dependencies["facts"],
            daily_completion=dependencies["daily_completion"],
        )
        attempt = dependencies["attempt"]
        self._store.validate_step_attempt_lease(
            attempt_id=attempt.attempt_id,
            lease=request.lease,
            at=request.created_at,
            author_id=request.author_id,
            reviewer_id=request.reviewer_id,
        )
        plan_artifacts = self._save_planning_plan_artifacts(
            request, dependencies, validated
        )
        review_outcome = self._run_planning_review(
            request, dependencies, validated, plan_artifacts
        )
        result_document = self._build_planning_result(
            request, dependencies, validated, plan_artifacts, review_outcome
        )
        output_path, output_hash = self._persist_planning_completion(
            request, dependencies, result_document, review_outcome
        )
        return self._planning_attempt_outcome(
            request,
            attempt.attempt_id,
            validated,
            plan_artifacts,
            review_outcome,
            output_path,
            output_hash,
        )

    @audit_maintenance_stage("investigation", start_event="stage_resumed")
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

    @audit_maintenance_stage(
        "model", start_event="model_requested", finish_event="model_requested"
    )
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

    @audit_maintenance_stage(
        "model", start_event="model_observed", finish_event="model_observed"
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

    @audit_maintenance_stage("incident")
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

    @audit_maintenance_stage("incident")
    def finalize_job(
        self,
        *,
        job_id: str,
        final_status: str,
        lease: LeaseToken,
        finalized_at: datetime | str,
        reason_code: str | None = None,
        incident_status: str | None = None,
    ) -> JobFinalizeResult:
        return self._store.finalize_job(
            job_id=job_id,
            final_status=final_status,
            lease=lease,
            finalized_at=finalized_at,
            reason_code=reason_code,
            incident_status=incident_status,
        )
