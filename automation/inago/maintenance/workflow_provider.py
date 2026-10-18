"""Fail-closed sequential provider for abnormal daily maintenance work."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import re
import threading
from typing import TYPE_CHECKING

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
    """A durable stage handler's result; only complete advances the workflow."""

    status: str
    reason_code: str | None = None


WorkflowStageRunner = Callable[[MaintenanceWorkflowContext], WorkflowStageResult]


class MaintenanceWorkflowProvider:
    """Run configured stage handlers in order and fail closed on gaps."""

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

    def __call__(
        self,
        launch: WorkflowLaunch,
        check: DailyCheckRecord,
        queue: WorkerQueueRecord,
        lease: LeaseToken,
        cancel_event: threading.Event,
    ) -> WorkerWorkflowOutcome:
        historical_input = getattr(launch, "historical_input", None)
        if self._configuration_error is not None:
            return WorkerWorkflowOutcome(
                status="blocked",
                check_id=check.check_id,
                reason_code=self._configuration_error,
            )
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
            return WorkerWorkflowOutcome(
                status="blocked",
                check_id=check.check_id,
                reason_code="maintenance_workflow_identity_invalid",
            )
        if launch.target_date in WORKER_DISPATCH_EXCLUDED_DATES:
            return WorkerWorkflowOutcome(
                status="blocked",
                check_id=check.check_id,
                reason_code="target_date_worker_dispatch_disabled",
            )
        if check.check_status not in {"abnormal", "incomplete"}:
            return WorkerWorkflowOutcome(
                status="blocked",
                check_id=check.check_id,
                reason_code="maintenance_workflow_status_unsupported",
            )

        try:
            jobs = self._store.list_workflow_jobs_for_launch(
                source_launch_id=launch.source_launch_id,
                source_run_id=launch.source_run_id,
                target_date=launch.target_date,
            )
            if len(jobs) > 1:
                return WorkerWorkflowOutcome(
                    status="blocked",
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_job_ambiguous",
                )
            job = jobs[0] if jobs else None
            if historical_input is not None and (
                job is None
                or job.job_id != queue.worker_job_id
                or job.status not in {"queued", "running"}
            ):
                return WorkerWorkflowOutcome(
                    status="blocked",
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_job_identity_mismatch",
                )
            attempts = (
                self._store.list_step_attempts(job_id=job.job_id)
                if job is not None
                else ()
            )
            if any(
                attempt.job_id != job.job_id
                or attempt.source_run_id != launch.source_run_id
                for attempt in attempts
            ):
                return WorkerWorkflowOutcome(
                    status="blocked",
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_attempt_identity_mismatch",
                )
        except Exception as exc:
            reason_code = getattr(exc, "code", None)
            if (
                not isinstance(reason_code, str)
                or _REASON_CODE.fullmatch(reason_code) is None
            ):
                reason_code = "maintenance_workflow_resume_state_unavailable"
            return WorkerWorkflowOutcome(
                status="blocked",
                check_id=check.check_id,
                reason_code=reason_code,
            )

        context = MaintenanceWorkflowContext(
            launch=launch,
            check=check,
            queue=queue,
            lease=lease,
            cancel_event=cancel_event,
            resume_state=WorkflowResumeState(job=job, attempts=attempts),
            historical_input=historical_input,
        )
        if job is not None and job.status == "complete":
            if cancel_event.is_set():
                return WorkerWorkflowOutcome(
                    status="waiting",
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_cancelled",
                )
            report_runner = self._stage_runners.get("report")
            if report_runner is None:
                return WorkerWorkflowOutcome(
                    status="blocked",
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_stage_unavailable",
                )
            try:
                result = report_runner(context)
            except Exception as exc:
                reason_code = getattr(exc, "code", None)
                if (
                    not isinstance(reason_code, str)
                    or _REASON_CODE.fullmatch(reason_code) is None
                ):
                    reason_code = "maintenance_workflow_stage_failed"
                return WorkerWorkflowOutcome(
                    status="blocked",
                    check_id=check.check_id,
                    reason_code=reason_code,
                )
            if not isinstance(result, WorkflowStageResult) or result.status not in {
                "complete",
                "waiting",
                "blocked",
            }:
                return WorkerWorkflowOutcome(
                    status="blocked",
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_stage_result_invalid",
                )
            reason_code = result.reason_code
            if result.status != "complete" and (
                not isinstance(reason_code, str)
                or _REASON_CODE.fullmatch(reason_code) is None
            ):
                reason_code = "maintenance_workflow_incomplete"
            return WorkerWorkflowOutcome(
                status=result.status,
                check_id=check.check_id,
                reason_code=reason_code,
            )
        for stage_name in WORKFLOW_STAGE_ORDER:
            if cancel_event.is_set():
                return WorkerWorkflowOutcome(
                    status="waiting",
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_cancelled",
                )
            runner = self._stage_runners.get(stage_name)
            if runner is None:
                return WorkerWorkflowOutcome(
                    status="blocked",
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_stage_unavailable",
                )
            try:
                result = runner(context)
            except Exception as exc:
                reason_code = getattr(exc, "code", None)
                if (
                    not isinstance(reason_code, str)
                    or _REASON_CODE.fullmatch(reason_code) is None
                ):
                    reason_code = "maintenance_workflow_stage_failed"
                return WorkerWorkflowOutcome(
                    status="blocked",
                    check_id=check.check_id,
                    reason_code=reason_code,
                )
            if not isinstance(result, WorkflowStageResult):
                return WorkerWorkflowOutcome(
                    status="blocked",
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_stage_result_invalid",
                )
            if result.status not in {"complete", "waiting", "blocked"}:
                return WorkerWorkflowOutcome(
                    status="blocked",
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_stage_result_invalid",
                )
            if result.status != "complete":
                reason_code = result.reason_code or "maintenance_workflow_incomplete"
                if (
                    not isinstance(reason_code, str)
                    or _REASON_CODE.fullmatch(reason_code) is None
                ):
                    reason_code = "maintenance_workflow_stage_result_invalid"
                return WorkerWorkflowOutcome(
                    status=result.status,
                    check_id=check.check_id,
                    reason_code=reason_code,
                )

        return WorkerWorkflowOutcome(status="complete", check_id=check.check_id)
