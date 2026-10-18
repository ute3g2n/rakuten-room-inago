"""Claim completed daily launches only after their launcher processes stop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import os
import subprocess
from typing import Callable
import uuid

from automation.inago.maintenance.process_identity import (
    ProcessObservation,
    current_process_start_time,
    observe_process,
)
from automation.inago.maintenance.store import (
    DailyLaunchRecord,
    LeaseBusyError,
    MaintenanceStore,
    MaintenanceStoreError,
    WorkerQueueRecord,
)


ProcessProbe = Callable[[int], ProcessObservation]


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    status: str
    worker_job_id: str | None = None
    queue_id: str | None = None
    source_launch_id: str | None = None
    check_id: str | None = None
    reason_code: str | None = None
    duplicate: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "maintenance-worker-result-v2",
            "scope": "dispatch_only",
            "maintenance_resolution_status": "not_started",
            **asdict(self),
        }


@dataclass(frozen=True, slots=True)
class WorkerDispatchResult:
    status: str
    queue_id: str | None = None
    source_launch_id: str | None = None
    reason_code: str | None = None


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


def run_worker_once(
    store: MaintenanceStore,
    *,
    now: datetime,
    owner_id: str | None = None,
    owner_pid: int | None = None,
    owner_start_time: str | None = None,
    process_probe: ProcessProbe = observe_process,
    source_launch_id: str | None = None,
) -> WorkerRunResult:
    """Complete one dispatch only; incident diagnosis and repair are separate."""

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
        else next(iter(store.list_worker_queue(status="queued", limit=1)), None)
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

    launch = store.get_daily_launch(queue.source_launch_id)
    if launch is None or launch.target_date != queue.target_date:
        return WorkerRunResult(
            status="blocked",
            worker_job_id=queue.worker_job_id,
            queue_id=queue.queue_id,
            source_launch_id=queue.source_launch_id,
            reason_code="worker_launch_identity_invalid",
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
        return WorkerRunResult(
            status="blocked",
            worker_job_id=queue.worker_job_id,
            queue_id=queue.queue_id,
            source_launch_id=queue.source_launch_id,
            reason_code="daily_check_unavailable",
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

    try:
        completed = store.complete_worker_queue(
            queue_id=queue.queue_id,
            check_id=check.check_id,
            lease=lease,
            completed_at=now,
        )
        return _result(completed, duplicate=False)
    finally:
        store.release_lease(lease=lease, released_at=now)


def dispatch_ready_worker(
    store: MaintenanceStore,
    *,
    process_probe: ProcessProbe = observe_process,
    task_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> WorkerDispatchResult:
    """Start the on-demand worker for one completed launch whose owners stopped."""

    queue = next(iter(store.list_worker_queue(status="queued", limit=1)), None)
    if queue is None:
        return WorkerDispatchResult(status="idle")
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
