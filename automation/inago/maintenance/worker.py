"""Claim completed daily launches only after their launcher processes stop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
import re
import subprocess
import threading
from typing import Callable, Protocol
import uuid

from automation.inago.maintenance.process_identity import (
    ProcessObservation,
    current_process_start_time,
    observe_process,
)
from automation.inago.maintenance.store import (
    DailyCheckRecord,
    DailyLaunchRecord,
    HistoricalJobReference,
    LeaseBusyError,
    LeaseToken,
    MaintenanceStore,
    MaintenanceStoreError,
    WorkerQueueRecord,
)


ProcessProbe = Callable[[int], ProcessObservation]
WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-19"})


class WorkflowLaunch(Protocol):
    source_launch_id: str
    target_date: str
    source_run_id: str | None
    status: str

    @property
    def result_summary(self) -> object | None:
        ...


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    status: str
    worker_job_id: str | None = None
    queue_id: str | None = None
    source_launch_id: str | None = None
    check_id: str | None = None
    reason_code: str | None = None
    duplicate: bool = False
    maintenance_resolution_status: str = "not_started"

    def to_dict(self) -> dict[str, object]:
        full_workflow_status = self.maintenance_resolution_status != "not_started"
        return {
            "schema_version": (
                "maintenance-worker-result-v3"
                if full_workflow_status
                else "maintenance-worker-result-v2"
            ),
            "scope": (
                "maintenance_workflow" if full_workflow_status else "dispatch_only"
            ),
            **asdict(self),
        }


@dataclass(frozen=True, slots=True)
class WorkerDispatchResult:
    status: str
    queue_id: str | None = None
    source_launch_id: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class WorkerWorkflowOutcome:
    """Durable result returned by the abnormal-day maintenance workflow."""

    status: str
    check_id: str | None = None
    reason_code: str | None = None


WorkflowRunner = Callable[
    [
        WorkflowLaunch,
        DailyCheckRecord,
        WorkerQueueRecord,
        LeaseToken,
        threading.Event,
    ],
    WorkerWorkflowOutcome,
]


@dataclass(frozen=True, slots=True)
class HistoricalLaunchContext:
    """In-memory workflow identity for a historical run-null intake."""

    source_launch_id: str
    target_date: str
    launch_status: str
    daily_status: str
    exit_code: int
    reason_code: str
    observed_at: str
    historical_input: object
    source_run_id: None = None
    status: str = "failed"

    @property
    def result_summary(self) -> dict[str, object]:
        return {
            "schema_version": "historical-daily-completion-v1",
            "source_launch_id": self.source_launch_id,
            "source_run_id": None,
            "target_date": self.target_date,
            "status": "failed",
            "overall_status": "failed",
            "daily_status": self.daily_status,
            "launch_status": self.launch_status,
            "exit_code": self.exit_code,
            "reason_code": self.reason_code,
        }


def _same_process_is_gone(
    probe: ProcessProbe, pid: int, recorded_start_time: str | None
) -> bool:
    try:
        observation = probe(pid)
    except Exception:
        return False
    if not isinstance(observation, ProcessObservation):
        return False
    if observation.state == "stopped":
        return True
    if observation.state != "running" or recorded_start_time is None:
        return False
    return observation.start_time != recorded_start_time


def _launch_processes_stopped(launch: DailyLaunchRecord, probe: ProcessProbe) -> bool:
    if not _same_process_is_gone(probe, launch.owner_pid, launch.owner_start_time):
        return False
    if launch.child_pid is None:
        return True
    return _same_process_is_gone(probe, launch.child_pid, launch.child_start_time)


def _result(
    queue: WorkerQueueRecord,
    *,
    duplicate: bool,
) -> WorkerRunResult:
    if queue.status != "complete" or queue.check_id is None:
        return WorkerRunResult(
            status="blocked",
            worker_job_id=queue.worker_job_id,
            queue_id=queue.queue_id,
            source_launch_id=queue.source_launch_id,
            reason_code=queue.reason_code or "worker_queue_not_complete",
            duplicate=duplicate,
        )
    return WorkerRunResult(
        status="dispatch_complete",
        worker_job_id=queue.worker_job_id,
        queue_id=queue.queue_id,
        source_launch_id=queue.source_launch_id,
        check_id=queue.check_id,
        duplicate=duplicate,
    )


def _next_queued_worker_queue(store: MaintenanceStore) -> WorkerQueueRecord | None:
    """Select eligible work past excluded dates, preserving an all-excluded hold."""

    dispatchable = store.list_worker_queue(
        status="queued",
        limit=1,
        exclude_target_dates=WORKER_DISPATCH_EXCLUDED_DATES,
    )
    if dispatchable:
        return dispatchable[0]
    return next(iter(store.list_worker_queue(status="queued", limit=1)), None)


def _blocked_result(
    store: MaintenanceStore,
    queue: WorkerQueueRecord,
    *,
    check_id: str | None,
    reason_code: str,
    owner_id: str,
    owner_pid: int,
    owner_start_time: str,
    now: datetime,
    lease: LeaseToken | None = None,
    resolution_status: str = "blocked",
    historical_job_id: str | None = None,
) -> WorkerRunResult:
    """Persist a hold and return only after its reason has been recorded."""

    if (
        not isinstance(reason_code, str)
        or re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", reason_code) is None
    ):
        reason_code = "maintenance_workflow_failed"
    owns_lease = lease is None
    if lease is None:
        try:
            lease = store.acquire_lease(
                lease_key="maintenance:worker",
                owner_id=owner_id,
                owner_pid=owner_pid,
                owner_start_time=owner_start_time,
                now=now,
                ttl_seconds=90,
            )
        except LeaseBusyError:
            return WorkerRunResult(
                status="busy",
                worker_job_id=queue.worker_job_id,
                queue_id=queue.queue_id,
                source_launch_id=queue.source_launch_id,
                reason_code="worker_lease_busy",
            )
    try:
        if historical_job_id is not None:
            if resolution_status == "waiting":
                return WorkerRunResult(
                    status="waiting",
                    worker_job_id=historical_job_id,
                    source_launch_id=queue.source_launch_id,
                    check_id=check_id,
                    reason_code=reason_code,
                    maintenance_resolution_status="waiting",
                )
            try:
                store.finalize_job(
                    job_id=historical_job_id,
                    final_status="blocked",
                    lease=lease,
                    finalized_at=(
                        datetime.now(timezone.utc) if not owns_lease else now
                    ),
                    reason_code=reason_code,
                )
            except Exception:
                reason_code = "historical_job_block_persist_failed"
            return WorkerRunResult(
                status="blocked",
                worker_job_id=historical_job_id,
                source_launch_id=queue.source_launch_id,
                check_id=check_id,
                reason_code=reason_code,
                maintenance_resolution_status=resolution_status,
            )
        blocked = store.block_worker_queue(
            queue_id=queue.queue_id,
            check_id=check_id,
            reason_code=reason_code,
            lease=lease,
            blocked_at=(datetime.now(timezone.utc) if not owns_lease else now),
        )
        return WorkerRunResult(
            status="blocked",
            worker_job_id=blocked.worker_job_id,
            queue_id=blocked.queue_id,
            source_launch_id=blocked.source_launch_id,
            check_id=check_id,
            reason_code=blocked.reason_code,
            maintenance_resolution_status=resolution_status,
        )
    finally:
        if owns_lease:
            store.release_lease(
                lease=lease,
                released_at=now,
            )


def _historical_queue_context(
    reference: HistoricalJobReference,
) -> WorkerQueueRecord:
    """Build a transient runner input; this identity is never persisted as a queue."""

    import hashlib

    dispatch_id = hashlib.sha256(reference.job_id.encode("utf-8")).hexdigest()[:24]
    return WorkerQueueRecord(
        queue_id=f"historical-dispatch-{dispatch_id}",
        source_launch_id=reference.source_launch_id,
        worker_job_id=reference.job_id,
        target_date=reference.target_date,
        status="queued",
        check_id=None,
        claim_owner_id=None,
        claim_generation=None,
        claimed_at=None,
        completed_at=None,
        reason_code=None,
        created_at=reference.created_at,
        updated_at=reference.created_at,
    )


def _historical_input_failure(
    store: MaintenanceStore,
    reference: HistoricalJobReference,
    *,
    reason_code: str,
    owner_id: str,
    owner_pid: int,
    owner_start_time: str,
    now: datetime,
) -> WorkerRunResult:
    return _blocked_result(
        store,
        _historical_queue_context(reference),
        check_id=None,
        reason_code=reason_code,
        owner_id=owner_id,
        owner_pid=owner_pid,
        owner_start_time=owner_start_time,
        now=now,
        historical_job_id=reference.job_id,
    )


def run_worker_once(
    store: MaintenanceStore,
    *,
    now: datetime,
    owner_id: str | None = None,
    owner_pid: int | None = None,
    owner_start_time: str | None = None,
    process_probe: ProcessProbe = observe_process,
    source_launch_id: str | None = None,
    workflow_runner: WorkflowRunner | None = None,
) -> WorkerRunResult:
    """Acknowledge a normal check or run one fail-closed incident workflow."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise MaintenanceStoreError("worker_clock_invalid")
    owner = owner_id or f"worker-{uuid.uuid4().hex}"
    pid = os.getpid() if owner_pid is None else owner_pid
    process_start = owner_start_time or current_process_start_time()
    if process_start is None:
        raise MaintenanceStoreError("process_start_time_unavailable")

    automatic_selection = source_launch_id is None
    queue = (
        store.get_worker_queue(source_launch_id)
        if not automatic_selection
        else _next_queued_worker_queue(store)
    )
    historical_item = None
    historical_reference = None
    if automatic_selection and (
        queue is None or queue.target_date in WORKER_DISPATCH_EXCLUDED_DATES
    ):
        references = store.list_historical_job_references(
            limit=1,
            exclude_target_dates=WORKER_DISPATCH_EXCLUDED_DATES,
        )
        if references:
            historical_reference = references[0]
        elif queue is None:
            historical_references = store.list_historical_job_references(limit=1)
            if historical_references:
                historical_reference = historical_references[0]
    elif queue is None:
        historical_references = store.list_historical_job_references(
            source_launch_id=source_launch_id,
            limit=1,
        )
        if historical_references:
            historical_reference = historical_references[0]

    if historical_reference is not None:
        if historical_reference.target_date in WORKER_DISPATCH_EXCLUDED_DATES:
            return _historical_input_failure(
                store,
                historical_reference,
                reason_code="target_date_worker_dispatch_disabled",
                owner_id=owner,
                owner_pid=pid,
                owner_start_time=process_start,
                now=now,
            )
        try:
            from automation.inago.maintenance.historical import (
                load_historical_work_item,
            )

            historical_item = load_historical_work_item(
                store,
                data_root=store.database_path.parent.parent,
                source_launch_id=historical_reference.source_launch_id,
            )
        except Exception as exc:
            reason_code = getattr(exc, "code", None)
            if (
                not isinstance(reason_code, str)
                or re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", reason_code) is None
            ):
                reason_code = "historical_job_input_invalid"
            return _historical_input_failure(
                store,
                historical_reference,
                reason_code=reason_code,
                owner_id=owner,
                owner_pid=pid,
                owner_start_time=process_start,
                now=now,
            )
        queue = _historical_queue_context(historical_reference)

    if queue is None:
        if source_launch_id is not None:
            launch = store.get_daily_launch(source_launch_id)
            if (
                historical_item is None
                and launch is not None
                and launch.status == "running"
            ):
                return WorkerRunResult(
                    status="waiting",
                    source_launch_id=launch.source_launch_id,
                    reason_code="daily_launch_running",
                )
        if queue is None:
            return WorkerRunResult(status="idle")
    if queue.status == "complete":
        return _result(queue, duplicate=True)
    if queue.status != "queued":
        return _result(queue, duplicate=False)
    if queue.target_date in WORKER_DISPATCH_EXCLUDED_DATES:
        if historical_reference is not None:
            return _historical_input_failure(
                store,
                historical_reference,
                reason_code="target_date_worker_dispatch_disabled",
                owner_id=owner,
                owner_pid=pid,
                owner_start_time=process_start,
                now=now,
            )
        return WorkerRunResult(
            status="blocked",
            worker_job_id=queue.worker_job_id,
            queue_id=queue.queue_id,
            source_launch_id=queue.source_launch_id,
            reason_code="target_date_worker_dispatch_disabled",
        )

    historical_job_id = (
        historical_item.job.job_id if historical_item is not None else None
    )
    if historical_item is None:
        launch = store.get_daily_launch(queue.source_launch_id)
        if launch is None or launch.target_date != queue.target_date:
            return _blocked_result(
                store,
                queue,
                check_id=None,
                reason_code="worker_launch_identity_invalid",
                owner_id=owner,
                owner_pid=pid,
                owner_start_time=process_start,
                now=now,
            )
        if launch.status == "running":
            return WorkerRunResult(
                status="waiting",
                worker_job_id=queue.worker_job_id,
                queue_id=queue.queue_id,
                source_launch_id=queue.source_launch_id,
                reason_code="daily_launch_running",
            )
        if not _launch_processes_stopped(launch, process_probe):
            return WorkerRunResult(
                status="waiting",
                worker_job_id=queue.worker_job_id,
                queue_id=queue.queue_id,
                source_launch_id=queue.source_launch_id,
                reason_code="daily_launch_process_state_unconfirmed",
            )
        check = store.get_daily_check_by_launch(queue.source_launch_id)
    else:
        launch = HistoricalLaunchContext(
            source_launch_id=historical_item.source_launch_id,
            target_date=historical_item.target_date,
            launch_status=str(historical_item.observation["launch_status"]),
            daily_status=str(historical_item.observation["daily_status"]),
            exit_code=int(historical_item.observation["exit_code"]),
            reason_code=str(historical_item.observation["reason_code"]),
            observed_at=str(historical_item.observation["observed_at"]),
            historical_input=historical_item,
        )
        check = historical_item.check
    if check is None or check.target_date != queue.target_date:
        return _blocked_result(
            store,
            queue,
            check_id=None,
            reason_code="daily_check_unavailable",
            owner_id=owner,
            owner_pid=pid,
            owner_start_time=process_start,
            now=now,
            historical_job_id=historical_job_id,
        )
    non_actionable_unknown = (
        check.check_status == "unknown"
        and check.reason_code == "daily_not_due"
        and launch.status == "complete"
        and launch.result_summary is not None
        and launch.result_summary.get("overall_status") == "not_due"
        and launch.result_summary.get("daily_status") == "not_due"
    )
    if (
        check.check_status
        not in {
            "normal",
            "normal_shortfall",
            "abnormal",
            "incomplete",
        }
        and not non_actionable_unknown
    ):
        reason_code = {
            "unknown": "daily_check_unknown",
        }.get(check.check_status, "daily_check_status_unsupported")
        return _blocked_result(
            store,
            queue,
            check_id=check.check_id,
            reason_code=reason_code,
            owner_id=owner,
            owner_pid=pid,
            owner_start_time=process_start,
            now=now,
            historical_job_id=historical_job_id,
        )

    requires_workflow = check.check_status in {"abnormal", "incomplete"}
    if requires_workflow and workflow_runner is None:
        return _blocked_result(
            store,
            queue,
            check_id=check.check_id,
            reason_code="maintenance_workflow_provider_unavailable",
            owner_id=owner,
            owner_pid=pid,
            owner_start_time=process_start,
            now=now,
            historical_job_id=historical_job_id,
        )

    try:
        lease = store.acquire_lease(
            lease_key="maintenance:worker",
            owner_id=owner,
            owner_pid=pid,
            owner_start_time=process_start,
            now=now,
            ttl_seconds=90,
        )
    except LeaseBusyError:
        return WorkerRunResult(
            status="busy",
            worker_job_id=queue.worker_job_id,
            queue_id=None if historical_job_id is not None else queue.queue_id,
            source_launch_id=queue.source_launch_id,
            reason_code="worker_lease_busy",
        )

    stop_heartbeat = threading.Event()
    cancel_workflow = threading.Event()
    heartbeat_errors: list[str] = []
    heartbeat_thread: threading.Thread | None = None

    if requires_workflow:

        def maintain_worker_lease() -> None:
            while not stop_heartbeat.wait(30):
                try:
                    store.heartbeat_lease(
                        lease=lease,
                        now=datetime.now(timezone.utc),
                        ttl_seconds=90,
                    )
                except Exception:
                    heartbeat_errors.append("worker_lease_heartbeat_failed")
                    cancel_workflow.set()
                    return

        heartbeat_thread = threading.Thread(
            target=maintain_worker_lease,
            name="inago-maintenance-worker-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()

    resolution_status = "not_started"
    try:
        if requires_workflow:
            assert workflow_runner is not None
            try:
                outcome = workflow_runner(launch, check, queue, lease, cancel_workflow)
            except Exception as exc:
                safe_reason = getattr(exc, "code", None)
                if (
                    not isinstance(safe_reason, str)
                    or re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", safe_reason) is None
                ):
                    safe_reason = "maintenance_workflow_failed"
                return _blocked_result(
                    store,
                    queue,
                    check_id=check.check_id,
                    reason_code=(
                        heartbeat_errors[0] if heartbeat_errors else safe_reason
                    ),
                    owner_id=owner,
                    owner_pid=pid,
                    owner_start_time=process_start,
                    now=now,
                    lease=lease,
                    historical_job_id=historical_job_id,
                )
            if heartbeat_errors:
                return _blocked_result(
                    store,
                    queue,
                    check_id=check.check_id,
                    reason_code=heartbeat_errors[0],
                    owner_id=owner,
                    owner_pid=pid,
                    owner_start_time=process_start,
                    now=now,
                    lease=lease,
                    historical_job_id=historical_job_id,
                )
            if not isinstance(outcome, WorkerWorkflowOutcome):
                return _blocked_result(
                    store,
                    queue,
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_result_invalid",
                    owner_id=owner,
                    owner_pid=pid,
                    owner_start_time=process_start,
                    now=now,
                    lease=lease,
                    historical_job_id=historical_job_id,
                )
            if outcome.status != "complete":
                resolution_status = (
                    "waiting" if outcome.status == "waiting" else "blocked"
                )
                reason = outcome.reason_code or "maintenance_workflow_incomplete"
                blocked_result = _blocked_result(
                    store,
                    queue,
                    check_id=check.check_id,
                    reason_code=reason,
                    owner_id=owner,
                    owner_pid=pid,
                    owner_start_time=process_start,
                    now=now,
                    lease=lease,
                    resolution_status=resolution_status,
                    historical_job_id=historical_job_id,
                )
                return (
                    WorkerRunResult(
                        status="waiting",
                        worker_job_id=blocked_result.worker_job_id,
                        queue_id=blocked_result.queue_id,
                        source_launch_id=blocked_result.source_launch_id,
                        check_id=blocked_result.check_id,
                        reason_code=blocked_result.reason_code,
                        maintenance_resolution_status="waiting",
                    )
                    if resolution_status == "waiting"
                    else blocked_result
                )
            if outcome.check_id != check.check_id:
                return _blocked_result(
                    store,
                    queue,
                    check_id=check.check_id,
                    reason_code="maintenance_workflow_check_mismatch",
                    owner_id=owner,
                    owner_pid=pid,
                    owner_start_time=process_start,
                    now=now,
                    lease=lease,
                    historical_job_id=historical_job_id,
                )
            resolution_status = "complete"

        completed_at = datetime.now(timezone.utc) if requires_workflow else now
        if historical_item is not None:
            completed_jobs = store.list_workflow_jobs_for_launch(
                source_launch_id=historical_item.source_launch_id,
                source_run_id=None,
                target_date=historical_item.target_date,
            )
            if (
                len(completed_jobs) != 1
                or completed_jobs[0].job_id != historical_item.job.job_id
                or completed_jobs[0].status != "complete"
            ):
                return _blocked_result(
                    store,
                    queue,
                    check_id=check.check_id,
                    reason_code="historical_job_completion_unconfirmed",
                    owner_id=owner,
                    owner_pid=pid,
                    owner_start_time=process_start,
                    now=now,
                    lease=lease,
                    historical_job_id=historical_item.job.job_id,
                )
            return WorkerRunResult(
                status="maintenance_complete",
                worker_job_id=historical_item.job.job_id,
                source_launch_id=historical_item.source_launch_id,
                check_id=check.check_id,
                maintenance_resolution_status="complete",
            )
        completed = store.complete_worker_queue(
            queue_id=queue.queue_id,
            check_id=check.check_id,
            lease=lease,
            completed_at=completed_at,
        )
        result = _result(completed, duplicate=False)
        if resolution_status == "complete":
            return WorkerRunResult(
                status="maintenance_complete",
                worker_job_id=result.worker_job_id,
                queue_id=result.queue_id,
                source_launch_id=result.source_launch_id,
                check_id=result.check_id,
                maintenance_resolution_status="complete",
            )
        return result
    finally:
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)
        store.release_lease(
            lease=lease,
            released_at=(datetime.now(timezone.utc) if requires_workflow else now),
        )


def dispatch_ready_worker(
    store: MaintenanceStore,
    *,
    process_probe: ProcessProbe = observe_process,
    task_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> WorkerDispatchResult:
    """Start the worker for a stopped daily launch or a queued historical job."""

    queue = _next_queued_worker_queue(store)
    historical_reference = None
    if queue is None or queue.target_date in WORKER_DISPATCH_EXCLUDED_DATES:
        references = store.list_historical_job_references(
            limit=1,
            exclude_target_dates=WORKER_DISPATCH_EXCLUDED_DATES,
        )
        if references:
            historical_reference = references[0]
            queue = None
        elif queue is None:
            excluded_references = store.list_historical_job_references(limit=1)
            if not excluded_references:
                return WorkerDispatchResult(status="idle")
            excluded_reference = excluded_references[0]
            return WorkerDispatchResult(
                status="blocked",
                source_launch_id=excluded_reference.source_launch_id,
                reason_code="target_date_worker_dispatch_disabled",
            )
    if historical_reference is not None:
        source_launch_id = historical_reference.source_launch_id
        target_date = historical_reference.target_date
    else:
        if queue is None:
            return WorkerDispatchResult(status="idle")
        source_launch_id = queue.source_launch_id
        target_date = queue.target_date
    queue_id = queue.queue_id if queue is not None else None
    if target_date in WORKER_DISPATCH_EXCLUDED_DATES:
        return WorkerDispatchResult(
            status="blocked",
            queue_id=queue_id,
            source_launch_id=source_launch_id,
            reason_code="target_date_worker_dispatch_disabled",
        )
    if historical_reference is None:
        launch = store.get_daily_launch(source_launch_id)
        if launch is None or launch.status == "running":
            return WorkerDispatchResult(
                status="waiting",
                queue_id=queue_id,
                source_launch_id=source_launch_id,
                reason_code="daily_launch_running",
            )
        if not _launch_processes_stopped(launch, process_probe):
            return WorkerDispatchResult(
                status="waiting",
                queue_id=queue_id,
                source_launch_id=source_launch_id,
                reason_code="daily_launch_process_state_unconfirmed",
            )
    try:
        completed = task_runner(
            ["schtasks.exe", "/Run", "/TN", "RakutenRoomInagoWorker"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return WorkerDispatchResult(
            status="unavailable",
            queue_id=queue_id,
            source_launch_id=source_launch_id,
            reason_code="worker_task_start_unavailable",
        )
    if completed.returncode != 0:
        return WorkerDispatchResult(
            status="unavailable",
            queue_id=queue_id,
            source_launch_id=source_launch_id,
            reason_code="worker_task_not_registered",
        )
    return WorkerDispatchResult(
        status="dispatched",
        queue_id=queue_id,
        source_launch_id=source_launch_id,
    )
