"""Claim completed daily launches only after their launcher processes stop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
import re
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
class WorkerWorkflowOutcome:
    """Durable result returned by the abnormal-day maintenance workflow."""

    status: str
    check_id: str | None = None
    reason_code: str | None = None
    terminal_adapter: str = "legacy"


WorkflowRunner = Callable[
    [
        WorkflowLaunch,
        DailyCheckRecord,
        WorkerQueueRecord,
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
    resolution_status: str = "blocked",
    historical_job_id: str | None = None,
) -> WorkerRunResult:
    """Persist a hold and return only after its reason has been recorded."""

    if (
        not isinstance(reason_code, str)
        or re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", reason_code) is None
    ):
        reason_code = "maintenance_workflow_failed"
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
                finalized_at=now,
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
        blocked_at=now,
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


def _historical_queue_context(
    reference: HistoricalJobReference,
) -> WorkerQueueRecord:
    """Build a transient runner input; this identity is never persisted as a queue."""
    return WorkerQueueRecord(
        queue_id=f"historical-dispatch-{uuid.uuid4().hex}",
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
    root = store.database_path.parent.parent
    # Keep a readable controller fallback. This is content for the next worker,
    # not a machine-readable next-owner proof or a claim that a stage ran.
    directory = root / "workflow-handoffs" / job_id
    temporary: str | None = None
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    final_path = directory / "workflow-fallback.md"
    stages = (
        "調査", "実装", "試験", "独立製品レビュー", "本番反映", "HTML作成", "独立HTMLレビュー",
    )
    body = [
        "# 保守ワークフローの引継ぎ",
        "",
        "runnerの外側でエラーが発生しました。下記の工程結果は取得できておらず、実施済みとは扱いません。",
        "",
        "## 対象",
        "",
        f"- job: {job_id}",
        f"- 日次起動: {launch.source_launch_id}",
        f"- 日次run: {launch.source_run_id if launch.source_run_id is not None else '未生成（null）'}",
        f"- 対象日: {launch.target_date}",
        f"- check: {check.check_id}",
        f"- 記録時刻: {now.isoformat()}",
        f"- runnerの結果: {reason_code}",
        f"- 捕捉した例外種別: {type(error).__name__}",
        "",
        "## 各担当の実行結果",
        "",
        *[f"- {stage}: 未確認。通常成果を取得できていません。" for stage in stages],
        "",
        "## 次の作業",
        "",
        "同じjobの実行記録を確認し、可能な担当工程から続けてください。原因修正、反映、HTML作成、レビューは未確認です。",
        "",
    ]
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=directory,
            prefix=".worker-handoff-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = stream.name
            stream.write("\n".join(body))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, final_path)
        temporary = None
    except (OSError, RuntimeError, ValueError, TypeError) as save_error:
        append_failure_event(
            root, phase="fallback", operation="worker.workflow_handoff_save",
            correlation_id=job_id, job_id=job_id,
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
    append_failure_event(
        root,
        phase="failed",
        operation="worker.workflow_runner",
        correlation_id=job_id,
        job_id=job_id,
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
        if source_launch_id is None and not _launch_processes_stopped(launch, process_probe):
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
    cancel_workflow = threading.Event()

    resolution_status = "not_started"
    workflow_reason_code: str | None = None
    runner_failure: BaseException | None = None
    if requires_workflow:
        try:
            if workflow_runner is None:
                raise RuntimeError("maintenance workflow provider is unavailable")
            outcome = workflow_runner(launch, check, queue, cancel_workflow)
        except Exception as exc:
            runner_failure = exc
            safe_reason = getattr(exc, "code", None)
            if (
                not isinstance(safe_reason, str)
                or re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", safe_reason) is None
            ):
                safe_reason = "maintenance_workflow_failed"
            workflow_reason_code = safe_reason
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
                if outcome.terminal_adapter == "skill-chain-markdown":
                    try:
                        from automation.inago.maintenance.skill_chain import (
                            inspect_skill_chain_terminal,
                        )

                        terminal = inspect_skill_chain_terminal(
                            job_root=(
                                store.database_path.parent.parent
                                / "maintenance-flow"
                                / historical_item.job.job_id
                            ),
                            repository_root=store.database_path.parent.parent.parent.parent,
                            identity=identity,
                        )
                        if terminal.get("status") != "complete":
                            terminal_code = terminal.get("reason_code")
                            safe_code = (
                                terminal_code
                                if isinstance(terminal_code, str)
                                and re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", terminal_code)
                                else "skill_chain_terminal_incomplete"
                            )
                            terminal_error = RuntimeError(safe_code)
                            workflow_reason_code = safe_code
                            outcome = WorkerWorkflowOutcome(
                                status="unknown",
                                check_id=check.check_id,
                                reason_code=safe_code,
                                terminal_adapter="skill-chain-markdown",
                            )
                    except Exception as exc:
                        terminal_error = exc
                        workflow_reason_code = "skill_chain_terminal_readback_failed"
                        outcome = WorkerWorkflowOutcome(
                            status="unknown",
                            check_id=check.check_id,
                            reason_code=workflow_reason_code,
                            terminal_adapter="skill-chain-markdown",
                        )
                else:
                    terminal_error = RuntimeError(
                        "legacy_workflow_terminal_reconciliation_retired"
                    )
                    workflow_reason_code = (
                        "legacy_workflow_terminal_reconciliation_retired"
                    )
                    outcome = WorkerWorkflowOutcome(
                        status="unknown",
                        check_id=check.check_id,
                        reason_code=workflow_reason_code,
                    )
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
