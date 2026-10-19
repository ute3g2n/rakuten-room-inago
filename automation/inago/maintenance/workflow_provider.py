"""Best-effort sequential provider for abnormal daily maintenance work."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import threading
import uuid
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

    def __call__(
        self,
        launch: WorkflowLaunch,
        check: DailyCheckRecord,
        queue: WorkerQueueRecord,
        lease: LeaseToken,
        cancel_event: threading.Event,
    ) -> WorkerWorkflowOutcome:
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
        stage_outcomes: dict[str, dict[str, str]] = {}
        context.state["workflow_stage_outcomes"] = stage_outcomes
        invocation_id = f"workflow-{uuid.uuid4().hex}"
        persisted_stages: set[str] = set()
        if self._configuration_error is not None:
            stage_outcomes["configuration"] = {
                "status": "blocked",
                "reason_code": self._configuration_error,
            }
        if initial_outcome is not None:
            stage_outcomes["resume_state"] = initial_outcome
        if job is not None and job.status == "complete":
            stage_order = ("report",)
        else:
            stage_order = WORKFLOW_STAGE_ORDER
        for stage_name in stage_order:
            runner = self._stage_runners.get(stage_name)
            status = "blocked"
            reason_code = "maintenance_workflow_stage_unavailable"
            if callable(runner):
                try:
                    result = runner(context)
                except Exception as exc:
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
                        and _REASON_CODE.fullmatch(result.status) is not None
                    ):
                        status = result.status
                        reason_code = result.reason_code or ""
                        if reason_code and _REASON_CODE.fullmatch(reason_code) is None:
                            reason_code = "maintenance_workflow_stage_result_invalid"
                    else:
                        reason_code = "maintenance_workflow_stage_result_invalid"
            stage_outcomes[stage_name] = {
                "status": status,
                "reason_code": reason_code,
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
                except Exception:
                    current_job_id = None
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
                        observation["persistence_reason_code"] = (
                            persisted_reason
                            if isinstance(persisted_reason, str)
                            and _REASON_CODE.fullmatch(persisted_reason)
                            else "workflow_stage_observation_persist_failed"
                        )
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
                reason_code = "maintenance_workflow_report_incomplete"
            return WorkerWorkflowOutcome(
                status=(
                    report_outcome["status"]
                    if report_outcome is not None
                    else "blocked"
                ),
                check_id=check.check_id,
                reason_code=reason_code,
            )
        return WorkerWorkflowOutcome(status="complete", check_id=check.check_id)
