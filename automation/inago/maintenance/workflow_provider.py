"""Best-effort sequential provider for abnormal daily maintenance work."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid
from typing import TYPE_CHECKING

from automation.inago.maintenance.code_revision import (
    InvocationCodeRevision,
    bind_invocation_code_revision,
    capture_invocation_code_revision,
)
from automation.inago.maintenance.failure_journal import (
    append_failure_event,
    current_code_revision,
    current_code_revision_reason,
    reconcile_open_operations,
    replay_failure_journal,
)
from automation.inago.maintenance.stage_handoff import (
    dumps_handoff,
    make_stage_handoff,
)
from automation.inago.maintenance.store import (
    DailyCheckRecord,
    LeaseToken,
    MaintenanceJobRecord,
    MaintenanceStore,
    StepAttemptRecord,
    WorkerQueueRecord,
)
from automation.inago.maintenance.worker import (
    WORKER_DISPATCH_EXCLUDED_DATES,
    WorkflowLaunch,
    WorkerWorkflowOutcome,
)

if TYPE_CHECKING:
    from automation.inago.maintenance.historical import HistoricalWorkItem


WORKFLOW_STAGE_ORDER = (
    "check_incident",
    "evidence",
    "job",
    "investigation",
    "planning",
    "repair",
    "deployment",
    "report",
)
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def _invocation_precondition_error(
    *, launch: WorkflowLaunch, check: DailyCheckRecord, queue: WorkerQueueRecord
) -> str | None:
    historical_input = getattr(launch, "historical_input", None)
    if (
        launch.source_launch_id != queue.source_launch_id
        or check.source_launch_id != launch.source_launch_id
        or launch.target_date != queue.target_date
        or check.target_date != launch.target_date
        or check.source_run_id != launch.source_run_id
        or queue.check_id not in {None, check.check_id}
        or queue.status != "queued"
        or (
            historical_input is not None
            and (
                getattr(historical_input, "source_launch_id", None)
                != launch.source_launch_id
                or getattr(historical_input, "target_date", None)
                != launch.target_date
                or getattr(historical_input, "check", None) != check
                or getattr(getattr(historical_input, "job", None), "job_id", None)
                != queue.worker_job_id
                or launch.source_run_id is not None
            )
        )
    ):
        return "maintenance_workflow_identity_invalid"
    if launch.target_date in WORKER_DISPATCH_EXCLUDED_DATES:
        return "target_date_worker_dispatch_disabled"
    return None


def _valid_attempt_for_context(
    attempt: object,
    *,
    step_key: str,
    sequence: int,
    context: MaintenanceWorkflowContext,
    job_id: str | None,
) -> StepAttemptRecord | None:
    if not isinstance(attempt, StepAttemptRecord):
        return None
    if (
        attempt.step_key != step_key
        or attempt.sequence != sequence
        or attempt.job_id != job_id
        or attempt.source_run_id != context.launch.source_run_id
        or not isinstance(attempt.attempt_id, str)
        or _REASON_CODE.fullmatch(attempt.attempt_id) is None
    ):
        return None
    return attempt


def _stage_attempt_id(
    *, context: MaintenanceWorkflowContext, stage_name: str, job_id: str | None
) -> str | None:
    """Bind stage events only to an attempt whose step key and identity match."""

    if stage_name == "report":
        candidates = [context.state.get("report_attempt"), *context.resume_state.attempts]
        matching = [
            attempt
            for candidate in candidates
            if (
                attempt := _valid_attempt_for_context(
                    candidate,
                    step_key="maintenance_report",
                    sequence=4,
                    context=context,
                    job_id=job_id,
                )
            )
            is not None
        ]
        if not matching:
            return None
        return max(matching, key=lambda item: (item.attempt_no, item.started_at)).attempt_id

    if stage_name == "deployment":
        candidates = [context.state.get("repair_attempt"), *context.resume_state.attempts]
        repair_attempts = [
            attempt
            for candidate in candidates
            if (
                attempt := _valid_attempt_for_context(
                    candidate,
                    step_key="maintenance_repair",
                    sequence=3,
                    context=context,
                    job_id=job_id,
                )
            )
            is not None
            and attempt.status == "complete"
            and attempt.result_version is not None
        ]
        if not repair_attempts:
            return None
        latest_repair = max(
            repair_attempts, key=lambda item: (item.attempt_no, item.started_at)
        )
        candidate = context.state.get("deployment_candidate")
        if isinstance(candidate, Mapping):
            candidate_identity = candidate.get("identity")
            candidate_release = candidate.get("candidate_release")
            workflow_job = context.resume_state.job or getattr(
                context.historical_input, "job", None
            )
            identity_matches = isinstance(candidate_identity, Mapping) and all(
                candidate_identity.get(key) == expected
                for key, expected in (
                    ("job_id", job_id),
                    ("incident_id", getattr(workflow_job, "incident_id", None)),
                    ("source_launch_id", context.launch.source_launch_id),
                    ("source_run_id", context.launch.source_run_id),
                    ("target_date", context.launch.target_date),
                )
            )
            if (
                candidate.get("schema_version") == "maintenance-deployment-candidate-v1"
                and candidate.get("job_id") == job_id
                and identity_matches
                and isinstance(candidate_release, Mapping)
                and isinstance(candidate_release.get("release_id"), str)
                and isinstance(candidate_release.get("commit"), str)
                and any(
                    attempt.attempt_id == candidate.get("attempt_id")
                    for attempt in repair_attempts
                )
            ):
                return str(candidate["attempt_id"])
        return latest_repair.attempt_id

    step_identity = {
        "investigation": ("maintenance_investigation", 1),
        "planning": ("maintenance_planning", 2),
        "repair": ("maintenance_repair", 3),
    }.get(stage_name)
    if step_identity is None:
        return None
    step_key, sequence = step_identity
    candidates = [context.state.get(f"{stage_name}_attempt"), *context.resume_state.attempts]
    matching = [
        attempt
        for candidate in candidates
        if (
            attempt := _valid_attempt_for_context(
                candidate,
                step_key=step_key,
                sequence=sequence,
                context=context,
                job_id=job_id,
            )
        )
        is not None
    ]
    if not matching:
        return None
    return max(matching, key=lambda item: (item.attempt_no, item.started_at)).attempt_id


class MaintenanceWorkflowProviderError(RuntimeError):
    """A safe, stable reason why the provider could not continue."""

    def __init__(self, code: str) -> None:
        self.code = (
            code
            if isinstance(code, str) and _REASON_CODE.fullmatch(code)
            else "maintenance_workflow_failed"
        )
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class WorkflowResumeState:
    """Existing job and attempt evidence bound to the current daily launch."""

    job: MaintenanceJobRecord | None
    attempts: tuple[StepAttemptRecord, ...]

    @property
    def latest_attempts_by_sequence(self) -> Mapping[int, StepAttemptRecord]:
        latest: dict[int, StepAttemptRecord] = {}
        for attempt in self.attempts:
            latest[attempt.sequence] = attempt
        return latest


@dataclass(frozen=True, slots=True)
class MaintenanceWorkflowContext:
    """Identity-bound inputs shared by the ordered stage handlers."""

    launch: WorkflowLaunch
    check: DailyCheckRecord
    queue: WorkerQueueRecord
    lease: LeaseToken
    cancel_event: threading.Event
    resume_state: WorkflowResumeState
    state: dict[str, object] = field(default_factory=dict)
    historical_input: HistoricalWorkItem | None = None


@dataclass(frozen=True, slots=True)
class WorkflowStageResult:
    """A durable stage handler's observation; every result advances the workflow."""

    status: str
    reason_code: str | None = None


WorkflowStageRunner = Callable[[MaintenanceWorkflowContext], WorkflowStageResult]


class MaintenanceWorkflowProvider:
    """Try every stage in order and carry stage failures through to reporting."""

    def __init__(
        self,
        store: MaintenanceStore,
        stage_runners: Mapping[str, WorkflowStageRunner],
    ) -> None:
        self._store = store
        self._stage_runners = dict(stage_runners)
        unexpected = self._stage_runners.keys() - set(WORKFLOW_STAGE_ORDER)
        invalid = any(not callable(runner) for runner in self._stage_runners.values())
        self._configuration_error = (
            "maintenance_workflow_stage_invalid" if unexpected or invalid else None
        )

    def _record_operation(
        self,
        *,
        correlation_id: str,
        invocation_id: str,
        launch: WorkflowLaunch,
        stage_name: str,
        operation: str,
        phase: str,
        job_id: str | None,
        incident_id: str | None = None,
        attempt_id: str | None = None,
        reason_code: str | None = None,
        error: BaseException | None = None,
        artifact_refs: tuple[str, ...] = (),
    ) -> Mapping[str, object]:
        """Persist operation events; DB failures go to the independent spool."""

        from datetime import datetime, timezone

        from automation.inago.maintenance.failure_journal import _exception_details

        chain, frames = _exception_details(error)
        event = {
            "event_id": uuid.uuid4().hex,
            "correlation_id": correlation_id,
            "parent_operation": invocation_id,
            "job_id": job_id,
            "source_launch_id": launch.source_launch_id,
            "source_run_id": launch.source_run_id,
            "incident_id": incident_id,
            "invocation_id": invocation_id,
            "stage": stage_name,
            "attempt_id": attempt_id,
            "operation": operation,
            "phase": phase,
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "code_revision": current_code_revision(),
            "code_revision_reason": current_code_revision_reason() or None,
            "reason_code": reason_code,
            "exception_chain": chain,
            "stack_frames": frames,
            "capture_location": "maintenance.workflow_provider.MaintenanceWorkflowProvider.__call__",
            "input_refs": [],
            "artifact_refs": list(artifact_refs[:16]),
            "primary_sink": "maintenance_operation_event",
            "fallback_status": None,
        }
        try:
            self._store.record_operation_event(event)
        except Exception as sink_error:
            fallback = append_failure_event(
                self._store.database_path.parent.parent,
                phase="fallback",
                operation=operation,
                correlation_id=correlation_id,
                parent_operation=invocation_id,
                job_id=job_id,
                incident_id=incident_id,
                attempt_id=attempt_id,
                source_launch_id=launch.source_launch_id,
                source_run_id=launch.source_run_id,
                invocation_id=invocation_id,
                stage=stage_name,
                reason_code=reason_code or "operation_event_persist_failed",
                error=sink_error,
                capture_location="workflow_provider._record_operation",
                primary_sink="maintenance_operation_event",
            )
            return {
                **dict(fallback),
                "primary_sink": "maintenance_operation_event",
            }
        return {"status": "written", "primary_sink": "maintenance_operation_event"}

    def _save_stage_handoff(
        self,
        *,
        payload: dict[str, object],
        job_id: str | None,
        invocation_id: str,
        stage_name: str,
    ) -> str | None:
        """Atomically persist a stage's substantive result outside the DB sink."""

        if (
            (job_id is not None and (
                not isinstance(job_id, str) or _REASON_CODE.fullmatch(job_id) is None
            ))
            or not _REASON_CODE.fullmatch(invocation_id)
            or stage_name not in WORKFLOW_STAGE_ORDER
        ):
            return None
        root = self._store.database_path.parent.parent / "workflow-handoffs"
        # An unbound invocation gets its own namespace; never write beneath the
        # mismatched worker_job_id supplied by the queue.
        directory = root / (job_id if job_id is not None else "unbound") / invocation_id
        temporary: str | None = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            final_path = directory / f"{stage_name}.json"
            encoded = dumps_handoff(payload).encode("utf-8")
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=directory, prefix=".handoff-", suffix=".tmp", delete=False
            ) as stream:
                temporary = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final_path)
            temporary = None
            return final_path.relative_to(self._store.database_path.parent.parent).as_posix()
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            append_failure_event(
                self._store.database_path.parent.parent,
                phase="fallback",
                operation=f"workflow.stage.{stage_name}.handoff_save",
                correlation_id=str(payload.get("correlation_id") or ""),
                parent_operation=invocation_id,
                job_id=job_id,
                source_launch_id=str(payload.get("identity", {}).get("source_launch_id") or ""),
                source_run_id=payload.get("identity", {}).get("source_run_id"),
                incident_id=payload.get("identity", {}).get("incident_id"),
                invocation_id=invocation_id,
                stage=stage_name,
                attempt_id=payload.get("attempt_id"),
                reason_code="workflow_stage_handoff_save_failed",
                error=exc,
                capture_location="workflow_provider._save_stage_handoff",
                primary_sink="workflow_handoff_file",
            )
            return None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _stage_handoff(
        self,
        *,
        context: MaintenanceWorkflowContext,
        stage_name: str,
        invocation_id: str,
        correlation_id: str,
        job_id: str | None,
        raw_status: str,
        stage_status: str,
        reason_code: str | None,
        error: BaseException | None,
        operation_result: str,
    ) -> dict[str, object]:
        historical_job = context.historical_input.job if context.historical_input else None
        job = context.resume_state.job or historical_job
        stage_attempt_id = _stage_attempt_id(
            context=context, stage_name=stage_name, job_id=job_id
        )
        identity = {
            "job_id": job_id,
            "incident_id": getattr(job, "incident_id", None),
            "source_launch_id": context.launch.source_launch_id,
            "source_run_id": context.launch.source_run_id,
            "target_date": context.launch.target_date,
            "attempt_id": stage_attempt_id,
        }
        handoff = make_stage_handoff(
            stage=stage_name,
            identity=identity,
            operation=f"workflow.stage.{stage_name}",
            operation_result=operation_result,
            summary=(
                f"{stage_name}工程の実行結果を記録しました。工程結果は{stage_status}です。"
                if operation_result == "succeeded"
                else f"{stage_name}工程は通常完了しませんでした。理由と次の引継ぎを記録しました。"
            ),
            facts=[
                f"工程担当の生の結果: {raw_status or '応答なし'}。",
                f"工程の実行結果: {stage_status}。",
            ],
            limitations=(
                []
                if operation_result == "succeeded"
                else ["通常の工程成果が得られなかったため、根本原因や業務回復は未確認です。"]
            ),
            next_actions=[
                "後続工程はこの結果と不明点を受け取り、独立して実行を続けてください。",
                "最終報告担当は、この工程の成否と未確認事項を平易に説明してください。",
            ],
            error=error,
            reason_code=reason_code,
            correlation_id=correlation_id,
            artifact_origin="controller_fallback",
        )
        handoff["stage_progress"] = "handed_off"
        saved_ref = self._save_stage_handoff(
            payload=handoff,
            job_id=job_id,
            invocation_id=invocation_id,
            stage_name=stage_name,
        )
        if saved_ref is None:
            handoff["stage_progress"] = "handoff_unavailable"
            handoff["unknowns"].append(
                "通常の成果物保存先へ引継ぎを書けたか確認できません。独立記録先へ保存結果を記録しました。"
            )
        else:
            handoff["output_refs"] = [saved_ref]
        return handoff

    def __call__(
        self,
        launch: WorkflowLaunch,
        check: DailyCheckRecord,
        queue: WorkerQueueRecord,
        lease: LeaseToken,
        cancel_event: threading.Event,
    ) -> WorkerWorkflowOutcome:
        # This date is deliberately excluded from dispatch. Keep it ahead of
        # all recovery, history, report, and handoff work, even if other fields
        # in the supplied records are also inconsistent.
        if launch.target_date in WORKER_DISPATCH_EXCLUDED_DATES:
            return WorkerWorkflowOutcome(
                status="blocked",
                check_id=check.check_id,
                reason_code="target_date_worker_dispatch_disabled",
            )
        reason = _invocation_precondition_error(
            launch=launch, check=check, queue=queue
        )
        invocation_id = f"workflow-{uuid.uuid4().hex}"
        revision = capture_invocation_code_revision()
        data_root = self._store.database_path.parent.parent
        with bind_invocation_code_revision(revision):
            if revision.reason_code is not None:
                append_failure_event(
                    data_root,
                    phase="failed",
                    operation="maintenance.code_revision.capture_source_set",
                    correlation_id=invocation_id,
                    parent_operation=invocation_id,
                    job_id=queue.worker_job_id,
                    source_launch_id=launch.source_launch_id,
                    source_run_id=launch.source_run_id,
                    invocation_id=invocation_id,
                    stage="workflow",
                    reason_code=revision.reason_code,
                    error=revision.error,
                    capture_location="code_revision.capture_invocation_code_revision",
                    input_refs=(revision.failed_ref,) if revision.failed_ref else (),
                    primary_sink="maintenance_source_fingerprint",
                )
            if reason is not None:
                return self._run_unbound_invocation(
                    launch,
                    check,
                    queue,
                    lease,
                    cancel_event,
                    invocation_id=invocation_id,
                    revision=revision,
                    reason=reason,
                )
            return self._run_invocation(
                launch,
                check,
                queue,
                lease,
                cancel_event,
                invocation_id=invocation_id,
                revision=revision,
            )

    def _run_unbound_invocation(
        self,
        launch: WorkflowLaunch,
        check: DailyCheckRecord,
        queue: WorkerQueueRecord,
        lease: LeaseToken,
        cancel_event: threading.Event,
        *,
        invocation_id: str,
        revision: InvocationCodeRevision,
        reason: str,
    ) -> WorkerWorkflowOutcome:
        """Record every handoff and start reporting without binding a bad identity."""

        data_root = self._store.database_path.parent.parent
        error = MaintenanceWorkflowProviderError(reason)
        failure_correlation = uuid.uuid4().hex
        try:
            failure_observation = append_failure_event(
                data_root,
                phase="failed",
                operation="workflow.invocation.identity_precondition",
                correlation_id=failure_correlation,
                parent_operation=invocation_id,
                job_id=None,
                source_launch_id=launch.source_launch_id,
                source_run_id=launch.source_run_id,
                invocation_id=invocation_id,
                stage="workflow",
                reason_code=reason,
                error=error,
                capture_location="workflow_provider.MaintenanceWorkflowProvider.__call__",
                primary_sink="maintenance_invocation_precondition",
            )
        except Exception as journal_error:
            failure_observation = {
                "status": "unavailable",
                "reason": type(journal_error).__name__,
            }

        # Give the report runner only an in-memory, explicitly unbound view.
        # In particular, it cannot accidentally address the mismatched queue's
        # queue_id or worker_job_id through its context.
        safe_check = replace(
            check,
            check_id="",
            source_launch_id=launch.source_launch_id,
            source_run_id=launch.source_run_id,
            target_date=launch.target_date,
            check_status="unknown",
            reason_code=reason,
        )
        safe_queue = WorkerQueueRecord(
            queue_id="",
            source_launch_id=launch.source_launch_id,
            worker_job_id="",
            target_date=launch.target_date,
            status="queued",
            check_id=None,
            claim_owner_id=None,
            claim_generation=None,
            claimed_at=None,
            completed_at=None,
            reason_code=reason,
            created_at=queue.created_at,
            updated_at=queue.updated_at,
        )
        context = MaintenanceWorkflowContext(
            launch=launch,
            check=safe_check,
            queue=safe_queue,
            lease=lease,
            cancel_event=cancel_event,
            resume_state=WorkflowResumeState(job=None, attempts=()),
            historical_input=None,
        )
        context.state["code_revision"] = revision.code_revision
        context.state["code_revision_reason"] = revision.reason_code
        context.state["identity_precondition_error"] = reason
        context.state["identity_failure_record"] = failure_observation
        stage_outcomes: dict[str, dict[str, object]] = {}
        stage_handoffs: dict[str, dict[str, object]] = {}
        context.state["workflow_stage_outcomes"] = stage_outcomes
        context.state["workflow_stage_handoffs"] = stage_handoffs

        def safe_record_operation(**kwargs: object) -> None:
            try:
                self._record_operation(**kwargs)  # type: ignore[arg-type]
            except Exception as operation_log_error:
                try:
                    append_failure_event(
                        data_root,
                        phase="fallback",
                        operation="workflow.operation_event.record",
                        correlation_id=str(kwargs.get("correlation_id") or invocation_id),
                        parent_operation=invocation_id,
                        job_id=None,
                        source_launch_id=launch.source_launch_id,
                        source_run_id=launch.source_run_id,
                        invocation_id=invocation_id,
                        stage=str(kwargs.get("stage_name") or "workflow"),
                        reason_code="workflow_operation_event_record_failed",
                        error=operation_log_error,
                        capture_location="workflow_provider._run_unbound_invocation",
                        primary_sink="maintenance_operation_event",
                    )
                except Exception:
                    # The event sink must never prevent the final report runner.
                    pass

        def safe_reason(candidate: object, fallback: str) -> str:
            return (
                candidate
                if isinstance(candidate, str) and _REASON_CODE.fullmatch(candidate)
                else fallback
            )

        for stage_name in WORKFLOW_STAGE_ORDER:
            correlation_id = uuid.uuid4().hex
            stage_status = "unknown"
            raw_status = "identity_unbound"
            operation_result = "not_performed"
            stage_reason = reason
            stage_error: BaseException | None = error
            if stage_name == "report":
                safe_record_operation(
                    correlation_id=correlation_id,
                    invocation_id=invocation_id,
                    launch=launch,
                    stage_name=stage_name,
                    operation="workflow.stage.report",
                    phase="begin",
                    job_id=None,
                    reason_code=reason,
                )
                runner = self._stage_runners.get(stage_name)
                if callable(runner):
                    try:
                        result = runner(context)
                    except Exception as exc:
                        stage_error = exc
                        raw_status = "exception"
                        operation_result = "failed"
                        stage_reason = (
                            getattr(exc, "code", None)
                            if isinstance(getattr(exc, "code", None), str)
                            and _REASON_CODE.fullmatch(getattr(exc, "code"))
                            else "maintenance_workflow_stage_failed"
                        )
                        append_failure_event(
                            data_root,
                            phase="failed",
                            operation="workflow.stage.report",
                            correlation_id=correlation_id,
                            parent_operation=invocation_id,
                            job_id=None,
                            source_launch_id=launch.source_launch_id,
                            source_run_id=launch.source_run_id,
                            invocation_id=invocation_id,
                            stage=stage_name,
                            reason_code=stage_reason,
                            error=exc,
                            capture_location="workflow_provider._run_unbound_invocation",
                            primary_sink="stage_runner",
                        )
                    else:
                        if isinstance(result, WorkflowStageResult) and isinstance(
                            result.status, str
                        ):
                            raw_status = (
                                result.status
                                if _REASON_CODE.fullmatch(result.status)
                                else "invalid_status"
                            )
                            if raw_status == "complete":
                                operation_result = "succeeded"
                                stage_reason = reason
                            elif raw_status == "failed":
                                operation_result = "failed"
                                stage_reason = safe_reason(
                                    result.reason_code,
                                    "maintenance_workflow_stage_failed",
                                )
                            elif raw_status == "not_performed":
                                operation_result = "not_performed"
                                stage_reason = safe_reason(
                                    result.reason_code,
                                    "maintenance_workflow_stage_not_performed",
                                )
                            else:
                                operation_result = "unknown"
                                stage_reason = safe_reason(result.reason_code, reason)
                        else:
                            raw_status = "invalid_result"
                            operation_result = "failed"
                            stage_error = MaintenanceWorkflowProviderError(
                                "maintenance_workflow_stage_result_invalid"
                            )
                            stage_reason = "maintenance_workflow_stage_result_invalid"
                else:
                    raw_status = "missing"
                    operation_result = "not_performed"
                    stage_reason = "maintenance_workflow_stage_unavailable"
                    stage_error = MaintenanceWorkflowProviderError(stage_reason)

            safe_record_operation(
                correlation_id=correlation_id,
                invocation_id=invocation_id,
                launch=launch,
                stage_name=stage_name,
                operation=f"workflow.stage.{stage_name}",
                phase=("failed" if operation_result == "failed" else "handoff"),
                job_id=None,
                reason_code=stage_reason,
                error=stage_error,
            )
            try:
                handoff = self._stage_handoff(
                    context=context,
                    stage_name=stage_name,
                    invocation_id=invocation_id,
                    correlation_id=correlation_id,
                    job_id=None,
                    raw_status=raw_status,
                    stage_status=stage_status,
                    reason_code=stage_reason,
                    error=stage_error,
                    operation_result=operation_result,
                )
            except Exception as handoff_error:
                handoff = make_stage_handoff(
                    stage=stage_name,
                    identity={
                        "job_id": None,
                        "incident_id": None,
                        "source_launch_id": launch.source_launch_id,
                        "source_run_id": launch.source_run_id,
                        "target_date": launch.target_date,
                        "attempt_id": None,
                    },
                    operation=f"workflow.stage.{stage_name}",
                    operation_result="failed",
                    summary=(
                        "工程の引継ぎ保存処理で例外が発生したため、内容をメモリ上に残し、"
                        "最終報告担当の起動を続けました。"
                    ),
                    facts=[f"受け取った対象情報を安全に結び付けられませんでした。理由: {reason}。"],
                    limitations=["引継ぎファイルを保存できたか確認できません。"],
                    next_actions=["最終報告担当は保存失敗と未確認事項を説明してください。"],
                    error=handoff_error,
                    reason_code="workflow_stage_handoff_save_failed",
                    correlation_id=correlation_id,
                )
                handoff["stage_progress"] = "handoff_unavailable"
            stage_handoffs[stage_name] = handoff
            saved_ref = handoff.get("output_refs", [None])[0]
            stage_outcomes[stage_name] = {
                "status": "unknown",
                "raw_status": raw_status,
                "reason_code": stage_reason,
                "operation_result": operation_result,
                "correlation_id": correlation_id,
                "handoff_ref": saved_ref,
                "handoff_saved": isinstance(saved_ref, str),
                "artifact_origin": handoff.get("artifact_origin"),
            }

        return WorkerWorkflowOutcome(
            status="unknown",
            check_id=check.check_id,
            reason_code=reason,
        )

    def _run_invocation(
        self,
        launch: WorkflowLaunch,
        check: DailyCheckRecord,
        queue: WorkerQueueRecord,
        lease: LeaseToken,
        cancel_event: threading.Event,
        *,
        invocation_id: str,
        revision: InvocationCodeRevision,
    ) -> WorkerWorkflowOutcome:
        historical_input = getattr(launch, "historical_input", None)
        data_root = self._store.database_path.parent.parent
        reconcile_open_operations(data_root)
        try:
            replay_failure_journal(data_root, self._store)
            self._store.reconcile_open_operation_events()
        except Exception as exc:
            append_failure_event(
                data_root,
                phase="fallback",
                operation="workflow.recovery.reconcile",
                correlation_id=invocation_id,
                invocation_id=invocation_id,
                job_id=queue.worker_job_id,
                source_launch_id=launch.source_launch_id,
                source_run_id=launch.source_run_id,
                reason_code="workflow_recovery_reconcile_failed",
                error=exc,
                capture_location="workflow_provider.MaintenanceWorkflowProvider.__call__",
                primary_sink="maintenance_operation_event",
            )
        initial_outcome: dict[str, str] | None = None
        try:
            jobs = self._store.list_workflow_jobs_for_launch(
                source_launch_id=launch.source_launch_id,
                source_run_id=launch.source_run_id,
                target_date=launch.target_date,
            )
            job = next(
                (
                    candidate
                    for candidate in jobs
                    if candidate.job_id == queue.worker_job_id
                ),
                None,
            )
            if job is None and historical_input is None and len(jobs) == 1:
                job = jobs[0]
            if job is None and historical_input is None and len(jobs) > 1:
                initial_outcome = {
                    "status": "blocked",
                    "reason_code": "maintenance_workflow_job_ambiguous",
                }
            if historical_input is not None and (
                job is None or job.job_id != queue.worker_job_id
            ):
                initial_outcome = {
                    "status": "blocked",
                    "reason_code": "maintenance_workflow_job_identity_mismatch",
                }
                job = None
            all_attempts = (
                self._store.list_step_attempts(job_id=job.job_id)
                if job is not None
                else ()
            )
            identity_mismatch = any(
                attempt.job_id != job.job_id
                or attempt.source_run_id != launch.source_run_id
                for attempt in all_attempts
            )
            attempts = tuple(
                attempt
                for attempt in all_attempts
                if attempt.job_id == job.job_id
                and attempt.source_run_id == launch.source_run_id
            )
            if identity_mismatch:
                initial_outcome = {
                    "status": "blocked",
                    "reason_code": "maintenance_workflow_attempt_identity_mismatch",
                }
        except Exception as exc:
            reason_code = getattr(exc, "code", None)
            if (
                not isinstance(reason_code, str)
                or _REASON_CODE.fullmatch(reason_code) is None
            ):
                reason_code = "maintenance_workflow_resume_state_unavailable"
            job = None
            attempts = ()
            initial_outcome = {"status": "blocked", "reason_code": reason_code}

        context = MaintenanceWorkflowContext(
            launch=launch,
            check=check,
            queue=queue,
            lease=lease,
            cancel_event=cancel_event,
            resume_state=WorkflowResumeState(job=job, attempts=attempts),
            historical_input=historical_input,
        )
        context.state["code_revision"] = revision.code_revision
        context.state["code_revision_reason"] = revision.reason_code
        stage_outcomes: dict[str, dict[str, object]] = {}
        context.state["workflow_stage_outcomes"] = stage_outcomes
        context.state["workflow_stage_handoffs"] = {}
        persisted_stages: set[str] = set()
        if self._configuration_error is not None:
            stage_outcomes["configuration"] = {
                "status": "unknown",
                "raw_status": "blocked",
                "reason_code": self._configuration_error,
                "operation_result": "not_performed",
            }
        if initial_outcome is not None:
            stage_outcomes["resume_state"] = {
                **initial_outcome,
                "raw_status": initial_outcome.get("status"),
                "status": "unknown",
                "operation_result": "unknown",
            }
        if job is not None and job.status == "complete":
            stage_order = ("report",)
        else:
            stage_order = WORKFLOW_STAGE_ORDER
        for stage_name in stage_order:
            runner = self._stage_runners.get(stage_name)
            status = "unknown"
            reason_code = "maintenance_workflow_stage_unavailable"
            raw_status = "missing"
            operation_result = "not_performed"
            stage_error: BaseException | None = None
            stage_correlation_id = uuid.uuid4().hex
            current_operation_job_id = (
                job.job_id
                if job is not None
                else (
                    historical_input.job.job_id
                    if historical_input is not None
                    else queue.worker_job_id
                )
            )
            current_operation_incident_id = getattr(
                job
                or (historical_input.job if historical_input is not None else None),
                "incident_id",
                None,
            )
            current_operation_attempt_id = _stage_attempt_id(
                context=context,
                stage_name=stage_name,
                job_id=current_operation_job_id,
            )
            self._record_operation(
                correlation_id=stage_correlation_id,
                invocation_id=invocation_id,
                launch=launch,
                stage_name=stage_name,
                operation=f"workflow.stage.{stage_name}",
                phase="begin",
                job_id=current_operation_job_id,
                incident_id=current_operation_incident_id,
                attempt_id=current_operation_attempt_id,
            )
            if callable(runner):
                try:
                    result = runner(context)
                except Exception as exc:
                    stage_error = exc
                    raw_status = "exception"
                    operation_result = "failed"
                    append_failure_event(
                        self._store.database_path.parent.parent,
                        phase="failed",
                        operation=f"workflow.stage.{stage_name}",
                        correlation_id=stage_correlation_id,
                        parent_operation=invocation_id,
                        job_id=current_operation_job_id,
                        incident_id=current_operation_incident_id,
                        attempt_id=current_operation_attempt_id,
                        source_launch_id=launch.source_launch_id,
                        source_run_id=launch.source_run_id,
                        invocation_id=invocation_id,
                        stage=stage_name,
                        reason_code=(getattr(exc, "code", None) or "workflow_stage_failed"),
                        error=exc,
                        capture_location="workflow_provider.MaintenanceWorkflowProvider.__call__",
                        primary_sink="stage_runner",
                    )
                    self._record_operation(
                        correlation_id=stage_correlation_id,
                        invocation_id=invocation_id,
                        launch=launch,
                        stage_name=stage_name,
                        operation=f"workflow.stage.{stage_name}",
                        phase="failed",
                        job_id=current_operation_job_id,
                        incident_id=current_operation_incident_id,
                        attempt_id=current_operation_attempt_id,
                        reason_code=(getattr(exc, "code", None) or "workflow_stage_failed"),
                        error=exc,
                    )
                    candidate_reason = getattr(exc, "code", None)
                    reason_code = (
                        candidate_reason
                        if isinstance(candidate_reason, str)
                        and _REASON_CODE.fullmatch(candidate_reason) is not None
                        else "maintenance_workflow_stage_failed"
                    )
                else:
                    if (
                        isinstance(result, WorkflowStageResult)
                        and isinstance(result.status, str)
                    ):
                        raw_status = (
                            result.status
                            if _REASON_CODE.fullmatch(result.status) is not None
                            else "invalid_status"
                        )
                        candidate_reason = result.reason_code
                        reason_code = (
                            candidate_reason
                            if isinstance(candidate_reason, str)
                            and _REASON_CODE.fullmatch(candidate_reason) is not None
                            else ""
                        )
                        if raw_status == "complete":
                            status = "complete"
                            operation_result = "succeeded"
                        elif raw_status == "unknown":
                            status = "unknown"
                            operation_result = "unknown"
                        elif raw_status == "failed":
                            status = "failed"
                            operation_result = "failed"
                            reason_code = reason_code or "maintenance_workflow_stage_failed"
                        elif raw_status == "not_performed":
                            status = "unknown"
                            operation_result = "not_performed"
                            reason_code = reason_code or "maintenance_workflow_stage_not_performed"
                        elif raw_status in {"blocked", "waiting"}:
                            # Preserve the observation, but never use it as a
                            # future-stage result or a dispatch condition.
                            status = "unknown"
                            operation_result = "unknown"
                            reason_code = reason_code or (
                                "maintenance_workflow_stage_waiting"
                                if raw_status == "waiting"
                                else "maintenance_workflow_stage_unavailable"
                            )
                        else:
                            status = "unknown"
                            operation_result = "failed"
                            reason_code = "maintenance_workflow_stage_result_invalid"
                            stage_error = MaintenanceWorkflowProviderError(reason_code)
                    else:
                        raw_status = "invalid_result"
                        operation_result = "failed"
                        stage_error = MaintenanceWorkflowProviderError(
                            "maintenance_workflow_stage_result_invalid"
                        )
                        reason_code = "maintenance_workflow_stage_result_invalid"
            handoff = self._stage_handoff(
                context=context,
                stage_name=stage_name,
                invocation_id=invocation_id,
                correlation_id=stage_correlation_id,
                job_id=current_operation_job_id,
                raw_status=raw_status,
                stage_status=status,
                reason_code=reason_code or None,
                error=stage_error,
                operation_result=operation_result,
            )
            event_observation = self._record_operation(
                correlation_id=stage_correlation_id,
                invocation_id=invocation_id,
                launch=launch,
                stage_name=stage_name,
                operation=f"workflow.stage.{stage_name}",
                phase="handoff",
                job_id=current_operation_job_id,
                incident_id=current_operation_incident_id,
                attempt_id=handoff.get("attempt_id"),
                reason_code=reason_code or None,
                error=stage_error,
                artifact_refs=tuple(
                    item for item in handoff.get("output_refs", []) if isinstance(item, str)
                ),
            )
            handoff["journal_observation"] = {
                key: value
                for key, value in event_observation.items()
                if key in {"status", "path", "primary_sink", "exception_class", "capture_location", "reason"}
                and isinstance(value, str)
            }
            if event_observation.get("status") == "unavailable":
                handoff["stage_progress"] = "handoff_unavailable"
                handoff["unknowns"].append(
                    "工程操作ログと独立fallback記録のどちらにも保存できたことを確認できません。"
                )
            saved_ref = self._save_stage_handoff(
                payload=handoff,
                job_id=current_operation_job_id,
                invocation_id=invocation_id,
                stage_name=stage_name,
            )
            if saved_ref is not None:
                handoff["output_refs"] = [saved_ref]
            elif handoff.get("stage_progress") != "handoff_unavailable":
                handoff["stage_progress"] = "handoff_unavailable"
                handoff["unknowns"].append(
                    "ログ記録の観測を含む最終引継ぎを書き戻せませんでした。"
                )
            context.state["workflow_stage_handoffs"][stage_name] = handoff
            stage_outcomes[stage_name] = {
                "status": status,
                "raw_status": raw_status,
                "reason_code": reason_code,
                "operation_result": operation_result,
                "correlation_id": stage_correlation_id,
                "handoff_ref": saved_ref,
                "handoff_saved": saved_ref is not None,
                "artifact_origin": handoff.get("artifact_origin"),
            }
            # Persist each observation as soon as a job identity is available.
            # Persistence errors are themselves observations and do not stop
            # later stage attempts, including the final report.
            current_job_id = (
                job.job_id
                if job is not None
                else (
                    historical_input.job.job_id
                    if historical_input is not None
                    else None
                )
            )
            if current_job_id is None:
                identity_lookup_error: BaseException | None = None
                try:
                    matching_jobs = self._store.list_workflow_jobs_for_launch(
                        source_launch_id=launch.source_launch_id,
                        source_run_id=launch.source_run_id,
                        target_date=launch.target_date,
                    )
                    if historical_input is not None:
                        current_job_id = next(
                            (
                                candidate.job_id
                                for candidate in matching_jobs
                                if candidate.job_id == historical_input.job.job_id
                            ),
                            None,
                        )
                    elif queue.worker_job_id:
                        current_job_id = next(
                            (
                                candidate.job_id
                                for candidate in matching_jobs
                                if candidate.job_id == queue.worker_job_id
                            ),
                            (
                                matching_jobs[0].job_id
                                if len(matching_jobs) == 1
                                else None
                            ),
                        )
                    elif len(matching_jobs) == 1:
                        current_job_id = matching_jobs[0].job_id
                except Exception as exc:
                    identity_lookup_error = exc
                    current_job_id = None
                if current_job_id is None:
                    lookup_reason = (
                        "workflow_stage_observation_identity_lookup_failed"
                        if identity_lookup_error is not None
                        else "workflow_stage_observation_identity_unavailable"
                    )
                    for observed_stage, observation in stage_outcomes.items():
                        if observed_stage in persisted_stages:
                            continue
                        artifact_ref = observation.get("handoff_ref")
                        journal_result = append_failure_event(
                            data_root,
                            phase="failed" if identity_lookup_error else "unknown",
                            operation="workflow.stage_observation.identity_lookup",
                            correlation_id=str(
                                observation.get("correlation_id") or invocation_id
                            ),
                            parent_operation=invocation_id,
                            job_id=queue.worker_job_id,
                            source_launch_id=launch.source_launch_id,
                            source_run_id=launch.source_run_id,
                            invocation_id=invocation_id,
                            stage=observed_stage,
                            reason_code=lookup_reason,
                            error=identity_lookup_error,
                            capture_location="workflow_provider.MaintenanceWorkflowProvider.__call__",
                            artifact_refs=(
                                (artifact_ref,) if isinstance(artifact_ref, str) else ()
                            ),
                            primary_sink="workflow_stage_observation",
                        )
                        observation["persistence_status"] = "unavailable"
                        observation["persistence_reason_code"] = lookup_reason
                        handoff = context.state["workflow_stage_handoffs"].get(observed_stage)
                        if isinstance(handoff, dict):
                            handoff["unknowns"].append(
                                "対象jobを引けず、stage結果は独立ファイルへ残しましたがDBへ結び付けられませんでした。"
                            )
                            handoff["journal_observation"] = {
                                key: value
                                for key, value in journal_result.items()
                                if key in {"status", "path", "exception_class", "capture_location", "reason"}
                                and isinstance(value, str)
                            }
                            rewritten_ref = self._save_stage_handoff(
                                payload=handoff,
                                job_id=queue.worker_job_id,
                                invocation_id=invocation_id,
                                stage_name=(
                                    observed_stage
                                    if observed_stage in WORKFLOW_STAGE_ORDER
                                    else stage_order[-1]
                                ),
                            )
                            if rewritten_ref is not None:
                                observation["handoff_ref"] = rewritten_ref
                                observation["handoff_saved"] = True
                    context.state["workflow_stage_observation_identity_lookup"] = {
                        "status": "unavailable",
                        "reason_code": lookup_reason,
                        "exception_class": (
                            type(identity_lookup_error).__name__
                            if identity_lookup_error is not None
                            else None
                        ),
                    }
            if current_job_id is not None:
                for observed_stage, observation in stage_outcomes.items():
                    if observed_stage in persisted_stages:
                        continue
                    try:
                        self._store.record_workflow_stage_observation(
                            invocation_id=invocation_id,
                            job_id=current_job_id,
                            source_launch_id=launch.source_launch_id,
                            source_run_id=launch.source_run_id,
                            target_date=launch.target_date,
                            stage_name=observed_stage,
                            status=observation["status"],
                            reason_code=observation["reason_code"] or None,
                            observed_at=datetime.now(timezone.utc),
                        )
                    except Exception as exc:
                        observation["persistence_status"] = "unavailable"
                        persisted_reason = getattr(exc, "code", None)
                        persisted_reason = (
                            persisted_reason
                            if isinstance(persisted_reason, str)
                            and _REASON_CODE.fullmatch(persisted_reason)
                            else "workflow_stage_observation_persist_failed"
                        )
                        observation["persistence_reason_code"] = persisted_reason
                        journal_result = append_failure_event(
                            data_root,
                            phase="failed",
                            operation="workflow.stage_observation.persist",
                            correlation_id=str(
                                observation.get("correlation_id") or invocation_id
                            ),
                            parent_operation=invocation_id,
                            job_id=current_job_id,
                            source_launch_id=launch.source_launch_id,
                            source_run_id=launch.source_run_id,
                            incident_id=current_operation_incident_id,
                            invocation_id=invocation_id,
                            stage=observed_stage,
                            reason_code=persisted_reason,
                            error=exc,
                            capture_location="workflow_provider.MaintenanceWorkflowProvider.__call__",
                            artifact_refs=(
                                (observation["handoff_ref"],)
                                if isinstance(observation.get("handoff_ref"), str)
                                else ()
                            ),
                            primary_sink="workflow_stage_observation",
                        )
                        handoff = context.state["workflow_stage_handoffs"].get(
                            observed_stage
                        )
                        if isinstance(handoff, dict):
                            handoff["unknowns"].append(
                                "DBへの工程結果保存に失敗しました。例外の種類と処理箇所を独立ログへ残しました。"
                            )
                            handoff["journal_observation"] = {
                                key: value
                                for key, value in journal_result.items()
                                if key in {"status", "path", "exception_class", "capture_location", "reason"}
                                and isinstance(value, str)
                            }
                            rewritten_ref = self._save_stage_handoff(
                                payload=handoff,
                                job_id=current_job_id,
                                invocation_id=invocation_id,
                                stage_name=observed_stage,
                            )
                            if rewritten_ref is not None:
                                observation["handoff_ref"] = rewritten_ref
                                observation["handoff_saved"] = True
                    else:
                        persisted_stages.add(observed_stage)

        report_outcome = stage_outcomes.get("report")
        if report_outcome is None or report_outcome["status"] != "complete":
            reason_code = (
                report_outcome.get("reason_code")
                if report_outcome is not None
                else None
            )
            if not reason_code:
                reason_code = (
                    "maintenance_workflow_report_missing"
                    if report_outcome is None
                    else "maintenance_workflow_report_incomplete"
                )
            return WorkerWorkflowOutcome(
                status=("unknown" if report_outcome is None else report_outcome["status"]),
                check_id=check.check_id,
                reason_code=reason_code,
            )
        return WorkerWorkflowOutcome(status="complete", check_id=check.check_id)
