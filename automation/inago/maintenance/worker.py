"""Claim completed daily launches only after their launcher processes stop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
import re
import subprocess
import threading
import tempfile
from typing import Callable, Protocol
import uuid

from automation.inago.maintenance.process_identity import (
    ProcessObservation,
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
from automation.inago.maintenance.workflow_reconciliation import (
    reconcile_workflow_terminal,
)


ProcessProbe = Callable[[int], ProcessObservation]
WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-19"})


class WorkflowLaunch(Protocol):
    source_launch_id: str
    target_date: str
    source_run_id: str | None
    status: str

    @property
    def result_summary(self) -> object | None: ...


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
        return True
    if not isinstance(observation, ProcessObservation):
        return True
    if observation.state == "stopped":
        return True
    if observation.state != "running" or recorded_start_time is None:
        return True
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
        lease = _acquire_worker_lease(
            store,
            owner_id=owner_id,
            owner_pid=owner_pid,
            owner_start_time=owner_start_time,
            now=now,
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
                    final_status="failed",
                    lease=lease,
                    finalized_at=(
                        datetime.now(timezone.utc) if not owns_lease else now
                    ),
                    reason_code=reason_code,
                    incident_status="in_progress",
                )
            except Exception:
                reason_code = "historical_job_failure_persist_failed"
            return WorkerRunResult(
                status="maintenance_incomplete",
                worker_job_id=historical_job_id,
                source_launch_id=queue.source_launch_id,
                check_id=check_id,
                reason_code=reason_code,
                maintenance_resolution_status=(
                    "failed" if resolution_status == "blocked" else resolution_status
                ),
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


def _acquire_worker_lease(
    store: MaintenanceStore,
    *,
    owner_id: str,
    owner_pid: int,
    owner_start_time: str,
    now: datetime,
) -> LeaseToken:
    """Create a per-invocation lease without serializing worker startups."""

    while True:
        try:
            return store.acquire_lease(
                lease_key=f"maintenance:worker:{uuid.uuid4().hex}",
                owner_id=owner_id,
                owner_pid=owner_pid,
                owner_start_time=owner_start_time,
                now=now,
                ttl_seconds=90,
            )
        except LeaseBusyError:
            continue


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


def _record_outer_workflow_failure(
    store: MaintenanceStore,
    *,
    job_id: str,
    launch: WorkflowLaunch,
    check: DailyCheckRecord,
    error: BaseException,
    reason_code: str,
) -> None:
    """Persist a substantive worker-boundary handoff without stopping reconciliation."""

    from automation.inago.maintenance.failure_journal import append_failure_event
    from automation.inago.maintenance.stage_handoff import dumps_handoff, make_stage_handoff

    root = store.database_path.parent.parent
    identity = store.get_job_input_manifest(job_id=job_id).identity()
    # A fresh fallback invocation preserves prior raw observations. Every stage
    # receives an explicit unknown/not-performed observation and handoff.
    invocation_id = uuid.uuid4().hex
    directory = root / "workflow-handoffs" / job_id / invocation_id
    temporary: str | None = None
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    for stage in (
        "check_incident", "evidence", "job", "investigation",
        "planning", "repair", "deployment", "report",
    ):
        handoff = make_stage_handoff(
            stage=stage,
            invocation_id=invocation_id,
            identity=identity,
            operation="worker.workflow_runner",
            operation_result="failed" if stage == "job" else "not_performed",
            summary=(
                "保守ワークフローの外側で例外または不正な結果を検出しました。終端照合へ進みます。"
                if stage == "job"
                else f"外側の実行が継続できず、{stage}工程の通常結果を取得できませんでした。"
            ),
            facts=[
                f"source_launch_id={launch.source_launch_id}",
                f"check_id={check.check_id}",
                f"reason_code={reason_code}",
            ],
            limitations=["工程結果、公開、原因修正は未確認です。"],
            next_actions=["同じjobを再開し、この工程の担当が結果を保存してください。"],
            error=error if stage == "job" else None,
            reason_code=reason_code,
            correlation_id=invocation_id,
        )
        handoff["stage_progress"] = "handed_off"
        final_path = directory / f"{stage}.json"
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=directory, prefix=".worker-handoff-", suffix=".tmp", delete=False
            ) as stream:
                temporary = stream.name
                stream.write(dumps_handoff(handoff).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final_path)
            temporary = None
        except (OSError, RuntimeError, ValueError, TypeError) as save_error:
            append_failure_event(
                root, phase="fallback", operation=f"worker.workflow_handoff_save.{stage}",
                correlation_id=invocation_id, job_id=job_id,
                incident_id=str(identity.get("incident_id") or ""),
                source_launch_id=launch.source_launch_id,
                source_run_id=launch.source_run_id,
                reason_code="worker_workflow_handoff_save_failed", error=save_error,
                capture_location="maintenance.worker._record_outer_workflow_failure",
                primary_sink="workflow_handoff_file",
            )
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        try:
            store.record_workflow_stage_observation(
                invocation_id=invocation_id, job_id=job_id,
                source_launch_id=launch.source_launch_id,
                source_run_id=launch.source_run_id, target_date=launch.target_date,
                stage_name=stage, status="unknown", reason_code=reason_code,
                observed_at=now,
            )
        except Exception as observation_error:
            append_failure_event(
                root, phase="failed", operation=f"worker.workflow_observation.{stage}",
                correlation_id=invocation_id, job_id=job_id,
                incident_id=str(identity.get("incident_id") or ""),
                source_launch_id=launch.source_launch_id,
                source_run_id=launch.source_run_id,
                reason_code="worker_workflow_observation_save_failed",
                error=observation_error,
                capture_location="maintenance.worker._record_outer_workflow_failure",
                primary_sink="maintenance_workflow_stage_observation",
            )
    append_failure_event(
        root,
        phase="failed",
        operation="worker.workflow_runner",
        correlation_id=invocation_id,
        job_id=job_id,
        incident_id=str(identity.get("incident_id") or ""),
        source_launch_id=launch.source_launch_id,
        source_run_id=launch.source_run_id,
        reason_code=reason_code,
        error=error,
        capture_location="maintenance.worker.run_worker_once",
        primary_sink="worker_workflow_runner",
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
    """Acknowledge a normal check or try the full incident workflow."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise MaintenanceStoreError("worker_clock_invalid")
    owner = owner_id or f"worker-{uuid.uuid4().hex}"
    pid = os.getpid() if owner_pid is None else owner_pid
    process_start = now.isoformat()

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
                job_id=historical_reference.job_id,
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
            "unknown",
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

    requires_workflow = check.check_status in {"abnormal", "incomplete", "unknown"}
    lease = _acquire_worker_lease(
        store,
        owner_id=owner,
        owner_pid=pid,
        owner_start_time=process_start,
        now=now,
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
                    return

        heartbeat_thread = threading.Thread(
            target=maintain_worker_lease,
            name="inago-maintenance-worker-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()

    resolution_status = "not_started"
    workflow_reason_code: str | None = None
    runner_failure: BaseException | None = None
    try:
        if requires_workflow:
            try:
                if workflow_runner is None:
                    raise RuntimeError("maintenance workflow provider is unavailable")
                outcome = workflow_runner(launch, check, queue, lease, cancel_workflow)
            except Exception as exc:
                runner_failure = exc
                safe_reason = getattr(exc, "code", None)
                if (
                    not isinstance(safe_reason, str)
                    or re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", safe_reason) is None
                ):
                    safe_reason = "maintenance_workflow_failed"
                workflow_reason_code = (
                    heartbeat_errors[0] if heartbeat_errors else safe_reason
                )
                outcome = WorkerWorkflowOutcome(
                    status="unknown", check_id=check.check_id,
                    reason_code=workflow_reason_code,
                )
            if not isinstance(outcome, WorkerWorkflowOutcome):
                runner_failure = TypeError(
                    f"workflow_runner returned {type(outcome).__name__}, expected WorkerWorkflowOutcome"
                )
                workflow_reason_code = "maintenance_workflow_result_invalid"
                outcome = WorkerWorkflowOutcome(
                    status="unknown", check_id=check.check_id,
                    reason_code=workflow_reason_code,
                )
            if outcome.check_id != check.check_id:
                runner_failure = ValueError(
                    "workflow_runner outcome check_id does not match the completed daily check"
                )
                workflow_reason_code = "maintenance_workflow_check_mismatch"
                outcome = WorkerWorkflowOutcome(
                    status="unknown", check_id=check.check_id,
                    reason_code=workflow_reason_code,
                )
            if historical_item is not None:
                try:
                    identity = store.get_job_input_manifest(
                        job_id=historical_item.job.job_id
                    ).identity()
                except Exception as exc:
                    identity = None
                    terminal_error = exc
                else:
                    terminal_error = None
                if runner_failure is not None and identity is not None:
                    try:
                        _record_outer_workflow_failure(
                            store,
                            job_id=historical_item.job.job_id,
                            launch=launch,
                            check=check,
                            error=runner_failure,
                            reason_code=workflow_reason_code or "maintenance_workflow_failed",
                        )
                    except Exception as handoff_error:
                        try:
                            from automation.inago.maintenance.failure_journal import (
                                append_failure_event,
                            )

                            append_failure_event(
                                store.database_path.parent.parent,
                                phase="failed",
                                operation="worker.workflow.fallback_handoff",
                                correlation_id=historical_item.source_launch_id,
                                job_id=historical_item.job.job_id,
                                incident_id=historical_item.job.incident_id,
                                source_launch_id=historical_item.source_launch_id,
                                source_run_id=None,
                                reason_code="worker_workflow_fallback_handoff_failed",
                                error=handoff_error,
                                capture_location="maintenance.worker.run_worker",
                                primary_sink="worker_fallback_handoff",
                            )
                        except Exception:
                            pass
                if identity is not None:
                    try:
                        reconcile_workflow_terminal(
                            store,
                            job_id=historical_item.job.job_id,
                            lease=lease,
                            expected_identity=identity,
                        )
                    except Exception as exc:
                        terminal_error = exc
                if terminal_error is not None:
                    try:
                        from automation.inago.maintenance.failure_journal import (
                            append_failure_event,
                        )

                        append_failure_event(
                            store.database_path.parent.parent,
                            phase="failed",
                            operation="worker.workflow.terminal_reconcile",
                            correlation_id=(historical_item.source_launch_id or "workflow-terminal-unknown"),
                            job_id=historical_item.job.job_id,
                            incident_id=historical_item.job.incident_id,
                            source_launch_id=historical_item.source_launch_id,
                            source_run_id=None,
                            reason_code="workflow_terminal_reconcile_failed",
                            error=terminal_error,
                            capture_location="maintenance.worker.run_worker",
                            primary_sink="workflow_terminal_reconcile",
                        )
                    except Exception:
                        pass
            if outcome.status != "complete":
                # Stage failure/wait states describe operations; they do not
                # prevent the worker from recording the result and advancing.
                workflow_reason_code = outcome.reason_code or (
                    "maintenance_workflow_stage_waiting"
                    if outcome.status == "waiting"
                    else "maintenance_workflow_stage_blocked"
                )
            if heartbeat_errors and workflow_reason_code is None:
                workflow_reason_code = heartbeat_errors[0]
            resolution_status = outcome.status

        completed_at = datetime.now(timezone.utc) if requires_workflow else now
        if historical_item is not None:
            completed_jobs = store.list_workflow_jobs_for_launch(
                source_launch_id=historical_item.source_launch_id,
                source_run_id=None,
                target_date=historical_item.target_date,
            )
            completed_job = next(
                (
                    job
                    for job in completed_jobs
                    if job.job_id == historical_item.job.job_id
                ),
                None,
            )
            if completed_job is None or completed_job.status != "complete":
                final_resolution_status = (
                    resolution_status
                    if resolution_status != "complete"
                    else "incomplete"
                )
                return WorkerRunResult(
                    status="maintenance_incomplete",
                    worker_job_id=historical_item.job.job_id,
                    source_launch_id=historical_item.source_launch_id,
                    check_id=check.check_id,
                    reason_code=(
                        workflow_reason_code or "historical_job_completion_unrecorded"
                    ),
                    maintenance_resolution_status=final_resolution_status,
                )
            return WorkerRunResult(
                status=(
                    "maintenance_complete"
                    if resolution_status == "complete"
                    else "maintenance_incomplete"
                ),
                worker_job_id=historical_item.job.job_id,
                source_launch_id=historical_item.source_launch_id,
                check_id=check.check_id,
                reason_code=workflow_reason_code,
                maintenance_resolution_status=resolution_status,
            )
        completed = store.complete_worker_queue(
            queue_id=queue.queue_id,
            check_id=check.check_id,
            lease=lease,
            completed_at=completed_at,
        )
        result = _result(completed, duplicate=False)
        return WorkerRunResult(
            status=(
                "maintenance_complete"
                if resolution_status == "complete"
                else "maintenance_incomplete"
            ),
            worker_job_id=result.worker_job_id,
            queue_id=result.queue_id,
            source_launch_id=result.source_launch_id,
            check_id=result.check_id,
            reason_code=workflow_reason_code,
            maintenance_resolution_status=resolution_status,
        )
    finally:
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)
        try:
            store.release_lease(
                lease=lease,
                released_at=(datetime.now(timezone.utc) if requires_workflow else now),
            )
        except Exception:
            # A failed lease release is observable in the lease row; it does
            # not erase a completed workflow/report result.
            pass


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
