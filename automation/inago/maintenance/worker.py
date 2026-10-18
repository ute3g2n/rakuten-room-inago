"""Claim completed daily launches only after their launcher processes stop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
import re
import subprocess
import threading
from typing import Callable
import uuid

from automation.inago.maintenance.process_identity import (
    ProcessObservation,
    current_process_start_time,
    observe_process,
)
from automation.inago.maintenance.store import (
    DailyCheckRecord,
    DailyLaunchRecord,
    LeaseBusyError,
    LeaseToken,
    MaintenanceStore,
    MaintenanceStoreError,
    WorkerQueueRecord,
)


ProcessProbe = Callable[[int], ProcessObservation]
WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-18", "2026-10-19"})


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
        DailyLaunchRecord,
        DailyCheckRecord,
        WorkerQueueRecord,
        LeaseToken,
        threading.Event,
    ],
    WorkerWorkflowOutcome,
]


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

    queue = (
        store.get_worker_queue(source_launch_id)
        if source_launch_id is not None
        else _next_queued_worker_queue(store)
    )
    if queue is None:
        if source_launch_id is not None:
            launch = store.get_daily_launch(source_launch_id)
            if launch is not None and launch.status == "running":
                return WorkerRunResult(
                    status="waiting",
                    source_launch_id=launch.source_launch_id,
                    reason_code="daily_launch_running",
                )
        return WorkerRunResult(status="idle")
    if queue.status == "complete":
        return _result(queue, duplicate=True)
    if queue.status != "queued":
        return _result(queue, duplicate=False)
    if queue.target_date in WORKER_DISPATCH_EXCLUDED_DATES:
        return WorkerRunResult(
            status="blocked",
            worker_job_id=queue.worker_job_id,
            queue_id=queue.queue_id,
            source_launch_id=queue.source_launch_id,
            reason_code="target_date_worker_dispatch_disabled",
        )

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
            queue_id=queue.queue_id,
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
                )
            resolution_status = "complete"

        completed_at = datetime.now(timezone.utc) if requires_workflow else now
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
    """Start the on-demand worker for one completed launch whose owners stopped."""

    queue = _next_queued_worker_queue(store)
    if queue is None:
        return WorkerDispatchResult(status="idle")
    if queue.target_date in WORKER_DISPATCH_EXCLUDED_DATES:
        return WorkerDispatchResult(
            status="blocked",
            queue_id=queue.queue_id,
            source_launch_id=queue.source_launch_id,
            reason_code="target_date_worker_dispatch_disabled",
        )
    launch = store.get_daily_launch(queue.source_launch_id)
    if launch is None or launch.status == "running":
        return WorkerDispatchResult(
            status="waiting",
            queue_id=queue.queue_id,
            source_launch_id=queue.source_launch_id,
            reason_code="daily_launch_running",
        )
    if not _launch_processes_stopped(launch, process_probe):
        return WorkerDispatchResult(
            status="waiting",
            queue_id=queue.queue_id,
            source_launch_id=queue.source_launch_id,
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
            queue_id=queue.queue_id,
            source_launch_id=queue.source_launch_id,
            reason_code="worker_task_start_unavailable",
        )
    if completed.returncode != 0:
        return WorkerDispatchResult(
            status="unavailable",
            queue_id=queue.queue_id,
            source_launch_id=queue.source_launch_id,
            reason_code="worker_task_not_registered",
        )
    return WorkerDispatchResult(
        status="dispatched",
        queue_id=queue.queue_id,
        source_launch_id=queue.source_launch_id,
    )
