"""Focused synthetic failure-path checks for the FC07 completion flow."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace

import pytest

from automation.inago.maintenance.failure_journal import (
    append_failure_event,
    reconcile_open_operations,
    replay_failure_journal,
)
from automation.inago.maintenance.code_revision import InvocationCodeRevision
from automation.inago.maintenance.paths import resolve_maintenance_failure_journal_path
from automation.inago.maintenance.report_fallback import build_fallback_report
import automation.inago.maintenance.workflow_provider as workflow_provider_module
from automation.inago.maintenance.stage_handoff import (
    make_stage_handoff,
    render_instrumentation_plan,
    render_no_change_explanation_html,
)
from automation.inago.maintenance.store import (
    LeaseToken,
    MaintenanceStore,
    StepAttemptRecord,
    WorkerQueueRecord,
)
from automation.inago.maintenance.controller import MaintenanceController
from tests_inago.maintenance_support import BASE_TIME, create_incident, create_ready_release, make_store
from automation.inago.maintenance.worker import WORKER_DISPATCH_EXCLUDED_DATES
from automation.inago.maintenance.workflow_provider import (
    WORKFLOW_STAGE_ORDER,
    MaintenanceWorkflowContext,
    MaintenanceWorkflowProvider,
    WorkflowResumeState,
    WorkflowStageResult,
)


JOB_ID = "job-historical-9e7f0c175cc4c874c955a5dab29c4427"
LAUNCH_ID = "historical-9e7f0c175cc4c874c955a5dab29c4427"
INCIDENT_ID = "352492da-ac58-4680-b1fd-fe5f24000229"
TARGET_DATE = "2026-10-17"


class _WorkflowStore:
    def __init__(self, data_root: Path, *, primary_trace_failure: bool = False):
        self.database_path = data_root / "maintenance" / "state.sqlite"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.job = SimpleNamespace(
            job_id=JOB_ID,
            incident_id=INCIDENT_ID,
            status="running",
            source_run_id=None,
        )
        self.primary_trace_failure = primary_trace_failure
        self.events: list[dict[str, object]] = []
        self.observations: list[dict[str, object]] = []

    def reconcile_open_operation_events(self):
        return None

    def record_operation_event(self, event):
        if self.primary_trace_failure:
            raise OSError("SYNTHETIC_PRIVATE_TRACE_FAILURE")
        self.events.append(dict(event))

    def list_workflow_jobs_for_launch(self, **_kwargs):
        return (self.job,)

    def list_step_attempts(self, *, job_id):
        assert job_id == JOB_ID
        return ()

    def record_workflow_stage_observation(self, **observation):
        self.observations.append(dict(observation))


def _context(data_root: Path, *, primary_trace_failure: bool = False):
    store = _WorkflowStore(data_root, primary_trace_failure=primary_trace_failure)
    launch = SimpleNamespace(
        source_launch_id=LAUNCH_ID,
        source_run_id=None,
        target_date=TARGET_DATE,
        historical_input=None,
    )
    check = SimpleNamespace(
        check_id="check-fc07",
        source_launch_id=LAUNCH_ID,
        source_run_id=None,
        target_date=TARGET_DATE,
    )
    queue = WorkerQueueRecord(
        queue_id="queue-fc07",
        source_launch_id=LAUNCH_ID,
        worker_job_id=JOB_ID,
        target_date=TARGET_DATE,
        status="queued",
        check_id=check.check_id,
        claim_owner_id=None,
        claim_generation=None,
        claimed_at=None,
        completed_at=None,
        reason_code=None,
        created_at="2026-10-19T20:38:29+09:00",
        updated_at="2026-10-19T20:38:29+09:00",
    )
    lease = LeaseToken("maintenance:fc07", "worker-fc07", 1)
    return store, launch, check, queue, lease


@pytest.mark.parametrize("failed_stage", WORKFLOW_STAGE_ORDER)
def test_fc07_stage_failure_keeps_handoff_and_runs_every_later_stage(
    tmp_path: Path, failed_stage: str
):
    store, launch, check, queue, lease = _context(tmp_path)
    calls: list[str] = []
    contexts: list[MaintenanceWorkflowContext] = []

    def make_runner(stage_name: str):
        def run(context: MaintenanceWorkflowContext):
            calls.append(stage_name)
            contexts.append(context)
            if stage_name == failed_stage:
                return WorkflowStageResult(
                    status="blocked", reason_code="injected_legacy_result"
                )
            return WorkflowStageResult(status="complete")

        return run

    provider = MaintenanceWorkflowProvider(
        store, {name: make_runner(name) for name in WORKFLOW_STAGE_ORDER}
    )
    outcome = provider(launch, check, queue, lease, threading.Event())

    assert calls == list(WORKFLOW_STAGE_ORDER)
    assert len(store.observations) == len(WORKFLOW_STAGE_ORDER)
    assert outcome.status == ("unknown" if failed_stage == "report" else "complete")
    assert len(contexts) == len(WORKFLOW_STAGE_ORDER)
    state = contexts[-1].state
    assert state["workflow_stage_outcomes"][failed_stage]["raw_status"] == "blocked"
    assert state["workflow_stage_outcomes"][failed_stage]["status"] == "unknown"
    invocation_id = next(
        event["invocation_id"] for event in store.events if event["stage"] == failed_stage
    )
    handoffs = state["workflow_stage_handoffs"]
    assert set(handoffs) == set(WORKFLOW_STAGE_ORDER)
    for stage_name in WORKFLOW_STAGE_ORDER:
        payload = json.loads(
            (tmp_path / "workflow-handoffs" / JOB_ID / invocation_id / f"{stage_name}.json")
            .read_text(encoding="utf-8")
        )
        assert payload["identity"] == {
            "job_id": JOB_ID,
            "incident_id": INCIDENT_ID,
            "source_launch_id": LAUNCH_ID,
            "source_run_id": None,
            "target_date": TARGET_DATE,
        }
        assert payload["content_result"]["summary"]
        assert payload["content_result"]["next_actions"]
    failed = handoffs[failed_stage]
    assert failed["stage_progress"] == "handed_off"
    assert failed["errors"] == []
    assert store.observations[
        list(WORKFLOW_STAGE_ORDER).index(failed_stage)
    ]["status"] == "unknown"


def test_fc07_consecutive_stage_failures_keep_content_and_run_later_roles(tmp_path: Path):
    store, launch, check, queue, lease = _context(tmp_path)
    calls: list[str] = []
    contexts: list[MaintenanceWorkflowContext] = []
    failed_stages = set(WORKFLOW_STAGE_ORDER[:2])

    def make_runner(stage_name: str):
        def run(context: MaintenanceWorkflowContext):
            calls.append(stage_name)
            contexts.append(context)
            if stage_name in failed_stages:
                try:
                    raise OSError("SYNTHETIC_PRIVATE_SECRET")
                except OSError as cause:
                    raise RuntimeError("consecutive injected stage failure") from cause
            return WorkflowStageResult(status="complete")

        return run

    outcome = MaintenanceWorkflowProvider(
        store, {name: make_runner(name) for name in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())

    assert calls == list(WORKFLOW_STAGE_ORDER)
    assert outcome.status == "failed"
    assert len(store.observations) == len(WORKFLOW_STAGE_ORDER)
    state = contexts[-1].state
    handoffs = state["workflow_stage_handoffs"]
    assert set(handoffs) == set(WORKFLOW_STAGE_ORDER)
    failed_events = [
        event for event in store.events
        if event.get("phase") == "failed" and event.get("stage") in failed_stages
    ]
    assert {event["stage"] for event in failed_events} == failed_stages
    for stage_name in failed_stages:
        assert handoffs[stage_name]["content_result"]["summary"]
        assert handoffs[stage_name]["content_result"]["next_actions"]
    spool = resolve_maintenance_failure_journal_path(tmp_path)
    text = spool.read_text(encoding="utf-8")
    assert "SYNTHETIC_PRIVATE_SECRET" not in text
    events = [json.loads(line) for line in text.splitlines()]
    assert all(event["correlation_id"] for event in events)


@pytest.mark.parametrize("failure_kind", ["timeout", "malformed_response"])
def test_fc07_timeout_and_malformed_agent_errors_are_traced_and_handed_off(
    tmp_path: Path, failure_kind: str
):
    store, launch, check, queue, lease = _context(tmp_path)
    calls: list[str] = []
    contexts: list[MaintenanceWorkflowContext] = []

    def make_runner(stage_name: str):
        def run(context: MaintenanceWorkflowContext):
            calls.append(stage_name)
            contexts.append(context)
            if stage_name == "investigation":
                if failure_kind == "timeout":
                    raise TimeoutError("SYNTHETIC_PRIVATE_TIMEOUT_SECRET")
                raise json.JSONDecodeError("synthetic bad JSON", "{", 0)
            return WorkflowStageResult(status="complete")

        return run

    outcome = MaintenanceWorkflowProvider(
        store, {name: make_runner(name) for name in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())

    assert calls == list(WORKFLOW_STAGE_ORDER)
    assert outcome.status == "complete"
    operation_event = next(
        event for event in store.events
        if event.get("phase") == "failed" and event.get("stage") == "investigation"
    )
    assert operation_event["operation"] == "workflow.stage.investigation"
    assert operation_event["correlation_id"]
    assert operation_event["exception_chain"]
    assert operation_event["capture_location"]
    assert contexts[-1].state["workflow_stage_handoffs"]["investigation"][
        "content_result"
    ]["next_actions"]
    journal_text = resolve_maintenance_failure_journal_path(tmp_path).read_text(
        encoding="utf-8"
    )
    assert "SYNTHETIC_PRIVATE_TIMEOUT_SECRET" not in journal_text


def test_fc07_all_trace_sinks_unavailable_then_same_job_resume_records_again(
    tmp_path: Path, monkeypatch
):
    store, launch, check, queue, lease = _context(
        tmp_path, primary_trace_failure=True
    )
    original_journal = workflow_provider_module.append_failure_event
    unavailable_calls: list[str] = []

    def unavailable_journal(_root, **kwargs):
        unavailable_calls.append(str(kwargs.get("operation")))
        return {
            "status": "unavailable",
            "exception_class": "OSError",
            "capture_location": "synthetic_all_trace_sinks_unavailable",
        }

    monkeypatch.setattr(
        workflow_provider_module, "append_failure_event", unavailable_journal
    )
    first_calls: list[str] = []

    def first_runner(stage_name: str):
        def run(_context):
            first_calls.append(stage_name)
            return WorkflowStageResult(status="complete")

        return run

    first = MaintenanceWorkflowProvider(
        store, {name: first_runner(name) for name in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())
    assert first.status == "complete"
    assert first_calls == list(WORKFLOW_STAGE_ORDER)
    assert unavailable_calls
    assert not resolve_maintenance_failure_journal_path(tmp_path).exists()
    invocation_directories = list(
        (tmp_path / "workflow-handoffs" / JOB_ID).iterdir()
    )
    assert len(invocation_directories) == 1
    first_invocation = invocation_directories[0].name
    for stage_name in WORKFLOW_STAGE_ORDER:
        assert (
            tmp_path / "workflow-handoffs" / JOB_ID
            / first_invocation / f"{stage_name}.json"
        ).is_file()

    monkeypatch.setattr(workflow_provider_module, "append_failure_event", original_journal)
    second_calls: list[str] = []

    def second_runner(stage_name: str):
        def run(_context):
            second_calls.append(stage_name)
            return WorkflowStageResult(status="complete")

        return run

    resumed = MaintenanceWorkflowProvider(
        store, {name: second_runner(name) for name in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())
    assert resumed.status == "complete"
    assert second_calls == list(WORKFLOW_STAGE_ORDER)
    rows = [
        json.loads(line)
        for line in resolve_maintenance_failure_journal_path(tmp_path)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows
    assert all(row["correlation_id"] for row in rows)
    assert all(row["job_id"] == JOB_ID for row in rows if row.get("job_id"))


@pytest.mark.parametrize(
    ("failed_stage", "reason"),
    [
        ("report", "report_review_rejected"),
        ("report", "report_review_response_invalid"),
        ("deployment", "deployment_preflight_candidate_missing"),
    ],
)
def test_fc07_review_and_preflight_failures_still_reach_report_finalize_runner(
    tmp_path: Path, failed_stage: str, reason: str
):
    store, launch, check, queue, lease = _context(tmp_path)
    calls: list[str] = []
    contexts: list[MaintenanceWorkflowContext] = []

    def make_runner(stage_name: str):
        def run(context: MaintenanceWorkflowContext):
            calls.append(stage_name)
            contexts.append(context)
            if stage_name == failed_stage:
                return WorkflowStageResult(status="failed", reason_code=reason)
            return WorkflowStageResult(status="complete")

        return run

    outcome = MaintenanceWorkflowProvider(
        store, {name: make_runner(name) for name in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())

    assert calls == list(WORKFLOW_STAGE_ORDER)
    assert outcome.status == ("failed" if failed_stage == "report" else "complete")
    state = contexts[-1].state
    failure = state["workflow_stage_outcomes"][failed_stage]
    assert failure["status"] == "failed"
    assert failure["reason_code"] == reason
    assert "report" in calls[calls.index(failed_stage) :]
    report_handoff = state["workflow_stage_handoffs"]["report"]
    assert report_handoff["content_result"]["summary"]
    assert report_handoff["content_result"]["next_actions"]


def test_fc07_unknown_cause_produces_actionable_plan_and_controller_handoff():
    identity = {
        "job_id": JOB_ID,
        "incident_id": INCIDENT_ID,
        "source_launch_id": LAUNCH_ID,
        "source_run_id": None,
        "target_date": TARGET_DATE,
    }
    plan = render_instrumentation_plan(
        limits=["原因を判断できる詳細な例外記録が残っていません。"],
        owner_paths=["automation/inago/maintenance/workflow_provider.py"],
    )
    assert "workflow_provider.py" in plan
    assert "相関ID" in plan
    handoff = make_stage_handoff(
        stage="planning",
        invocation_id="fc07-plan-invocation",
        identity=identity,
        operation="planning.create_instrumentation_plan",
        operation_result="unknown",
        summary="原因は分からないため、失敗の追跡記録を増やす計画を引き継ぎます。",
        facts=["過去の例外詳細を十分に確認できませんでした。"],
        limitations=["10月17日の根本原因は未確認です。"],
        next_actions=["指定した失敗境界へ相関ログを追加して合成試験します。"],
        input_refs=["evidence-fc07.json"],
        output_refs=["instrumentation-plan-fc07.md"],
        reason_code="planning_author_unavailable",
        correlation_id="fc07-plan-correlation",
    )
    assert handoff["identity"]["source_run_id"] is None
    assert handoff["artifact_origin"] == "controller_fallback"
    assert handoff["content_result"]["next_actions"]
    assert handoff["output_refs"] == ["instrumentation-plan-fc07.md"]
    no_change_html = render_no_change_explanation_html(
        what_was_checked=["調査記録と失敗の記録を確認しました。"],
        why_unchanged=["修正も記録追加も役立つか判断する証拠が足りません。"],
        next_actions=["同じjobで報告を作り、原因は不明と説明します。"],
    )
    assert "障害対応の状況" in no_change_html
    assert "変更しなかった理由" in no_change_html
    assert "次に行うこと" in no_change_html
    assert "原因と業務の回復は未確認です" in no_change_html


@pytest.mark.parametrize("invalid_report_response", ["", None, "{not-json"])
def test_fc07_empty_or_malformed_reporter_response_keeps_fallback_and_next_handoff(
    tmp_path: Path, invalid_report_response: object
):
    store, launch, check, queue, lease = _context(tmp_path)
    calls: list[str] = []
    contexts: list[MaintenanceWorkflowContext] = []

    def make_runner(stage_name: str):
        def run(context: MaintenanceWorkflowContext):
            calls.append(stage_name)
            contexts.append(context)
            if stage_name == "report":
                return invalid_report_response
            return WorkflowStageResult(status="complete")

        return run

    outcome = MaintenanceWorkflowProvider(
        store, {name: make_runner(name) for name in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())

    assert calls == list(WORKFLOW_STAGE_ORDER)
    assert outcome.status == "unknown"
    state = contexts[-1].state
    report_handoff = state["workflow_stage_handoffs"]["report"]
    assert report_handoff["content_result"]["summary"]
    assert report_handoff["content_result"]["next_actions"]
    assert report_handoff["artifact_origin"] == "controller_fallback"
    assert report_handoff["errors"]
    assert store.observations[-1]["status"] == "unknown"


def _fc07_child_process_crash(data_root: str) -> None:
    identity = {
        "job_id": JOB_ID,
        "incident_id": INCIDENT_ID,
        "source_launch_id": LAUNCH_ID,
        "source_run_id": None,
        "target_date": TARGET_DATE,
    }
    output = Path(data_root) / "workflow-handoffs" / JOB_ID / "fc07-crash"
    output.mkdir(parents=True, exist_ok=True)
    handoff = make_stage_handoff(
        stage="planning",
        invocation_id="fc07-process-crash-invocation",
        identity=identity,
        operation="planning.create_handoff",
        operation_result="unknown",
        summary="Synthetic plan artifact saved before process interruption.",
        facts=["The fixed synthetic identity was preserved."],
        limitations=["The process stopped before the primary DB stage record."],
        next_actions=["Resume the same job and continue all remaining stages."],
        output_refs=["workflow-handoffs/fc07-crash/planning.json"],
        reason_code="synthetic_interruption",
        correlation_id="fc07-process-crash-correlation",
    )
    (output / "planning.json").write_text(
        json.dumps(handoff, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    append_failure_event(
        data_root,
        phase="begin",
        operation="workflow.stage.planning",
        correlation_id="fc07-process-crash-correlation",
        invocation_id="fc07-process-crash-invocation",
        job_id=JOB_ID,
        source_launch_id=LAUNCH_ID,
        source_run_id=None,
        incident_id=INCIDENT_ID,
        stage="planning",
        attempt_id="fc07-process-crash-attempt",
        reason_code="planning_started",
    )
    os._exit(19)


def test_fc07_process_crash_is_reconciled_then_same_job_runs_remaining_roles(
    tmp_path: Path,
):
    context = multiprocessing.get_context("spawn")
    child = context.Process(target=_fc07_child_process_crash, args=(str(tmp_path),))
    child.start()
    child.join(timeout=20)
    if child.is_alive():
        child.terminate()
        child.join(timeout=5)
        pytest.fail("synthetic child did not exit after its persisted begin event")
    assert child.exitcode == 19
    saved_handoff = json.loads(
        (tmp_path / "workflow-handoffs" / JOB_ID / "fc07-crash" / "planning.json")
        .read_text(encoding="utf-8")
    )
    assert saved_handoff["identity"] == {
        "job_id": JOB_ID,
        "incident_id": INCIDENT_ID,
        "source_launch_id": LAUNCH_ID,
        "source_run_id": None,
        "target_date": TARGET_DATE,
    }
    assert saved_handoff["content_result"]["next_actions"]

    recovered = reconcile_open_operations(tmp_path)
    assert recovered["reconciled"] == 1
    rows = [
        json.loads(line)
        for line in resolve_maintenance_failure_journal_path(tmp_path)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    observed = next(row for row in rows if row["phase"] == "interruption_observed")
    assert observed["correlation_id"] == "fc07-process-crash-correlation"
    assert observed["job_id"] == JOB_ID
    assert observed["source_launch_id"] == LAUNCH_ID
    assert observed["source_run_id"] is None

    store, launch, check, queue, lease = _context(tmp_path)
    calls: list[str] = []
    def runner(stage_name: str):
        def run(_context):
            calls.append(stage_name)
            return WorkflowStageResult(status="complete")

        return run

    outcome = MaintenanceWorkflowProvider(
        store, {name: runner(name) for name in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())
    assert outcome.status == "complete"
    assert calls == list(WORKFLOW_STAGE_ORDER)
    begin = next(
        row for row in store.events
        if row.get("phase") == "begin" and row.get("operation") == "workflow.stage.planning"
    )
    assert begin["job_id"] == JOB_ID
    assert begin["source_launch_id"] == LAUNCH_ID
    assert begin["source_run_id"] is None


def _fc07_child_save_report_then_exit(data_root: str, job_id: str, attempt_id: str) -> None:
    artifact = Path(data_root) / "attempts" / job_id / attempt_id / "report.html"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("<!doctype html><title>synthetic crash-window report</title>", encoding="utf-8")
    os._exit(23)


def test_fc07_sqlite_attempt_recovers_after_artifact_saved_before_terminal_registration(
    tmp_path: Path,
):
    store = make_store(tmp_path)
    incident_id = create_incident(store, launch_id=LAUNCH_ID)
    release_id = "historical-9e7f0c175cc4c874c955a5dab29c4427"
    job = create_ready_release(
        store,
        incident_id=incident_id,
        release_id=release_id,
        base_commit="a" * 40,
        result_commit="b" * 40,
    )
    assert job.job_id == JOB_ID
    store.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id=LAUNCH_ID,
        source_run_id=None,
        target_date=TARGET_DATE,
        job_id=JOB_ID,
        created_at=BASE_TIME,
    )
    original_lease = store.acquire_lease(
        lease_key=f"maintenance:{JOB_ID}",
        owner_id="fc07-crash-before-register",
        owner_pid=1,
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    controller = MaintenanceController(store)
    original = controller.begin_step_attempt(
        job_id=JOB_ID,
        step_key="maintenance_report",
        sequence=4,
        lease=original_lease,
        started_at=BASE_TIME,
    )
    data_root = str(store.database_path.parent.parent)
    child = multiprocessing.get_context("spawn").Process(
        target=_fc07_child_save_report_then_exit,
        args=(data_root, JOB_ID, original.attempt_id),
    )
    child.start()
    child.join(timeout=20)
    if child.is_alive():
        child.terminate()
        child.join(timeout=5)
        pytest.fail("synthetic artifact-save child did not exit")
    assert child.exitcode == 23

    artifact = Path(data_root) / "attempts" / JOB_ID / original.attempt_id / "report.html"
    assert artifact.is_file()
    assert "synthetic crash-window" in artifact.read_text(encoding="utf-8")
    before = store.get_step_attempt(original.attempt_id)
    assert before.status == "running"
    assert before.output_path is None
    assert before.output_sha256 is None

    reopened = MaintenanceStore(
        tmp_path / "data",
        daily_database_path=tmp_path / "daily-runtime" / "state.sqlite",
    )
    reopened.initialize()
    assert len(
        reopened.list_workflow_jobs_for_launch(
            source_launch_id=LAUNCH_ID,
            source_run_id=None,
            target_date=TARGET_DATE,
            job_id=JOB_ID,
        )
    ) == 1
    new_lease = reopened.acquire_lease(
        lease_key=f"maintenance:{JOB_ID}",
        owner_id="fc07-same-job-resume",
        owner_pid=2,
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    resumed_controller = MaintenanceController(reopened)
    resumed = resumed_controller.begin_step_attempt(
        job_id=JOB_ID,
        step_key="maintenance_report",
        sequence=4,
        lease=new_lease,
        started_at=BASE_TIME,
    )
    after_old = reopened.get_step_attempt(original.attempt_id)
    assert after_old.status == "interrupted"
    assert after_old.output_path is None
    assert after_old.output_sha256 is None
    assert resumed.attempt_id != original.attempt_id
    assert resumed.attempt_no == original.attempt_no + 1
    resumed_artifact = f"{JOB_ID}/{resumed.attempt_id}/result.json"
    resumed_bytes = b'{"synthetic":"resumed same job"}\n'
    resumed_artifact_file = Path(data_root) / "attempts" / resumed_artifact
    resumed_artifact_file.parent.mkdir(parents=True, exist_ok=True)
    resumed_artifact_file.write_bytes(resumed_bytes)
    import hashlib

    reopened.complete_step_attempt(
        attempt_id=resumed.attempt_id,
        lease=new_lease,
        result_version="report-resumed-v1",
        completed_at=BASE_TIME,
        output_path=resumed_artifact,
        output_sha256=hashlib.sha256(resumed_bytes).hexdigest(),
    )
    final_attempts = reopened.list_step_attempts(job_id=JOB_ID)
    assert len(final_attempts) == 2
    assert [attempt.status for attempt in final_attempts] == ["interrupted", "complete"]
    assert not [attempt for attempt in final_attempts if attempt.status == "running"]
    manifest = reopened.get_job_input_manifest(job_id=JOB_ID)
    assert manifest.identity()["source_launch_id"] == LAUNCH_ID
    assert manifest.identity()["source_run_id"] is None
    assert manifest.identity()["target_date"] == TARGET_DATE


def test_fc07_primary_trace_failure_falls_back_without_stopping_stages(tmp_path: Path):
    store, launch, check, queue, lease = _context(
        tmp_path, primary_trace_failure=True
    )
    calls: list[str] = []

    def runner(stage_name: str):
        def run(_context):
            calls.append(stage_name)
            return WorkflowStageResult(status="complete")

        return run

    outcome = MaintenanceWorkflowProvider(
        store, {name: runner(name) for name in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())

    assert calls == list(WORKFLOW_STAGE_ORDER)
    assert outcome.status == "complete"
    spool = resolve_maintenance_failure_journal_path(tmp_path)
    assert spool.is_file()
    text = spool.read_text(encoding="utf-8")
    assert "SYNTHETIC_PRIVATE_TRACE_FAILURE" not in text
    events = [json.loads(line) for line in text.splitlines()]
    assert events
    assert all(event["correlation_id"] for event in events)
    assert all(event["exception_chain"] == ["OSError"] for event in events)


def test_fc07_nested_failure_is_correlated_redacted_and_replayed(tmp_path: Path):
    try:
        try:
            raise OSError("SYNTHETIC_PRIVATE_SECRET")
        except OSError as cause:
            raise RuntimeError("outer synthetic failure") from cause
    except RuntimeError as error:
        result = append_failure_event(
            tmp_path,
            phase="failed",
            operation="workflow.stage.investigation",
            correlation_id="fc07-correlation",
            parent_operation="fc07-invocation",
            job_id=JOB_ID,
            source_launch_id=LAUNCH_ID,
            source_run_id=None,
            incident_id=INCIDENT_ID,
            stage="investigation",
            attempt_id="fc07-attempt",
            reason_code="synthetic_nested_failure",
            error=error,
            capture_location="test_maintenance_flow_completion_fc07",
            input_refs=(
                "evidence/u21/input.json",
                "../outside.json",
                "C:\\private\\secret.txt",
                "token-file.json",
                "evidence//bad.json",
            ),
            artifact_refs=(
                "attempts/fc07/output.json",
                "../../outside.json",
                "/private/output.json",
                "credentials.json",
            ),
            primary_sink="maintenance_operation_event",
        )

    assert result["status"] == "written"
    journal = Path(result["path"])
    serialized = journal.read_text(encoding="utf-8")
    assert "SYNTHETIC_PRIVATE_SECRET" not in serialized
    row = json.loads(serialized)
    assert row["exception_chain"] == ["RuntimeError", "OSError"]
    assert row["correlation_id"] == "fc07-correlation"
    assert row["parent_operation"] == "fc07-invocation"
    assert row["capture_location"] == "test_maintenance_flow_completion_fc07"
    assert row["input_refs"] == ["evidence/u21/input.json"]
    assert row["artifact_refs"] == ["attempts/fc07/output.json"]
    assert None not in row["input_refs"] + row["artifact_refs"]

    assert reconcile_open_operations(tmp_path)["reconciled"] == 0

    class ReplayStore:
        events: list[dict[str, object]] = []

        def record_operation_event(self, event):
            self.events.append(dict(event))

    replay = ReplayStore()
    replay_result = replay_failure_journal(tmp_path, replay)
    assert replay_result["replayed"] == 1
    assert replay.events[0]["correlation_id"] == "fc07-correlation"
    assert replay.events[0]["fallback_status"] == "replayed_from_fallback"


def test_fc07_fallback_report_and_october_19_exclusion_are_preserved():
    operation = "10月17日の自動処理が途中で終わりました。"
    outcome = "利用できる記録を調べ、今回の記録方法を改善しました。"
    unknown = "元の原因は確かめられていません。"
    next_action = "同じ記録を使って調査と報告を続けます。"
    report = build_fallback_report(
        identity={
            "job_id": JOB_ID,
            "source_launch_id": LAUNCH_ID,
            "target_date": TARGET_DATE,
            "incident_id": INCIDENT_ID,
        },
        operation=operation,
        outcome=outcome,
        unknowns=[unknown],
        next_action=next_action,
        technical_reference="job_idは参考情報です。",
    )
    for heading in (
        "何が起きたか",
        "何を調べたか、何を変えたか",
        "分かっていないこと",
        "次に行うこと",
    ):
        assert heading in report.html
    main_text, technical_appendix = report.html.split("<details", maxsplit=1)
    for plain_language_answer in (operation, outcome, unknown, next_action):
        assert plain_language_answer in main_text
    assert JOB_ID not in main_text
    assert "job_idは参考情報です。" in technical_appendix
    assert WORKER_DISPATCH_EXCLUDED_DATES == frozenset({"2026-10-19"})


def test_fc07_invocation_revision_fingerprint_changes_with_maintenance_source(
    tmp_path: Path, monkeypatch
):
    import automation.inago.maintenance.code_revision as code_revision_module

    source_root = Path(__file__).resolve().parents[1]
    isolated_root = tmp_path / "isolated-repository-copy"
    shutil.copytree(
        source_root / "automation" / "inago" / "maintenance",
        isolated_root / "automation" / "inago" / "maintenance",
    )
    shutil.copytree(
        source_root / "automation" / "inago" / "schemas",
        isolated_root / "automation" / "inago" / "schemas",
    )
    for filename in ("daily_pipeline.py", "orchestrator.py", "trace.py"):
        destination = isolated_root / "automation" / "inago" / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / "automation" / "inago" / filename, destination)
    copied_revision_module = (
        isolated_root / "automation" / "inago" / "maintenance" / "code_revision.py"
    )
    monkeypatch.setattr(code_revision_module, "__file__", str(copied_revision_module))

    before = code_revision_module.capture_invocation_code_revision()
    assert before.reason_code is None
    assert before.code_revision.startswith("maintenance-source-v1-")
    worker_copy = (
        isolated_root / "automation" / "inago" / "maintenance" / "worker.py"
    )
    worker_copy.write_bytes(worker_copy.read_bytes() + b"\n# isolated fingerprint mutation\n")
    after = code_revision_module.capture_invocation_code_revision()
    assert after.reason_code is None
    assert after.code_revision.startswith("maintenance-source-v1-")
    assert after.code_revision != before.code_revision
    assert after.source_count == before.source_count


def test_fc07_invocation_revision_is_shared_by_event_and_stage_handoff(
    tmp_path: Path, monkeypatch
):
    revision_value = "maintenance-source-v1-" + "c" * 64
    revision = InvocationCodeRevision(
        code_revision=revision_value,
        source_count=22,
        manifest_sha256="c" * 64,
    )
    capture_calls = []

    def capture_once():
        capture_calls.append("capture")
        return revision

    monkeypatch.setattr(
        workflow_provider_module,
        "capture_invocation_code_revision",
        capture_once,
    )
    store, launch, check, queue, lease = _context(tmp_path)
    calls = []

    def runner(stage_name: str):
        def run(_context):
            calls.append(stage_name)
            if stage_name == "evidence":
                raise RuntimeError("synthetic invocation event for revision binding")
            return WorkflowStageResult(status="complete")

        return run

    outcome = MaintenanceWorkflowProvider(
        store, {stage: runner(stage) for stage in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())

    assert outcome.status == "complete"
    assert calls == list(WORKFLOW_STAGE_ORDER)
    assert capture_calls == ["capture"]
    evidence_events = [
        event
        for event in store.events
        if event.get("stage") == "evidence"
        and event.get("phase") in {"failed", "begin"}
    ]
    assert evidence_events
    assert all(event.get("code_revision") == revision_value for event in evidence_events)
    invocation_id = str(evidence_events[0]["invocation_id"])
    journal_rows = [
        json.loads(line)
        for line in resolve_maintenance_failure_journal_path(tmp_path)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    failure_event = next(
        event
        for event in journal_rows
        if event.get("operation") == "workflow.stage.evidence"
        and event.get("phase") == "failed"
    )
    assert failure_event["invocation_id"] == invocation_id
    assert failure_event["code_revision"] == revision_value
    assert failure_event["exception_chain"] == ["RuntimeError"]
    handoffs = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (
            tmp_path / "workflow-handoffs" / JOB_ID / invocation_id
        ).glob("*.json")
    ]
    assert len(handoffs) == len(WORKFLOW_STAGE_ORDER)
    assert {handoff["code_revision"] for handoff in handoffs} == {revision_value}
    evidence_handoff = next(item for item in handoffs if item["stage"] == "evidence")
    assert evidence_handoff["identity"]["source_run_id"] is None


def test_fc07_revision_capture_failure_records_unknown_and_continues(
    tmp_path: Path, monkeypatch
):
    capture_failure = InvocationCodeRevision(
        code_revision="unknown",
        reason_code="code_revision_source_read_failed",
        failed_ref="automation/inago/maintenance/worker.py",
        error=OSError("synthetic fingerprint source denied"),
    )
    monkeypatch.setattr(
        workflow_provider_module,
        "capture_invocation_code_revision",
        lambda: capture_failure,
    )
    store, launch, check, queue, lease = _context(tmp_path)
    calls = []

    def runner(stage_name: str):
        def run(_context):
            calls.append(stage_name)
            return WorkflowStageResult(status="complete")

        return run

    outcome = MaintenanceWorkflowProvider(
        store, {stage: runner(stage) for stage in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())
    assert outcome.status == "complete"
    assert calls == list(WORKFLOW_STAGE_ORDER)
    rows = [
        json.loads(line)
        for line in resolve_maintenance_failure_journal_path(tmp_path)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    capture_event = next(
        event
        for event in rows
        if event.get("operation") == "maintenance.code_revision.capture_source_set"
    )
    assert capture_event["code_revision"] == "unknown"
    assert capture_event["code_revision_reason"] == "code_revision_source_read_failed"
    assert capture_event["reason_code"] == "code_revision_source_read_failed"
    assert capture_event["exception_chain"] == ["OSError"]
    assert capture_event["input_refs"] == ["automation/inago/maintenance/worker.py"]
    invocation_id = capture_event["invocation_id"]
    handoffs = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (
            tmp_path / "workflow-handoffs" / JOB_ID / invocation_id
        ).glob("*.json")
    ]
    assert len(handoffs) == len(WORKFLOW_STAGE_ORDER)
    assert all(item["code_revision"] == "unknown" for item in handoffs)
    assert all(
        item["code_revision_reason"] == "code_revision_source_read_failed"
        for item in handoffs
    )
    assert next(item for item in handoffs if item["stage"] == "report")


@pytest.mark.parametrize(
    ("repair_status", "expected_deployment_attempt"),
    [("complete", "repair"), ("running", None)],
)
def test_fc07_attempt_binding_keeps_deployment_repair_and_report_sequence_four(
    tmp_path: Path,
    repair_status: str,
    expected_deployment_attempt: str | None,
):
    repair = StepAttemptRecord(
        attempt_id="attempt-fc07-repair-seq3",
        job_id=JOB_ID,
        step_key="maintenance_repair",
        sequence=3,
        attempt_no=2,
        source_run_id=None,
        status=repair_status,
        result_version="repair-v2",
        output_path=f"{JOB_ID}/attempt-fc07-repair-seq3/result.json",
        output_sha256="a" * 64,
        started_at="2026-10-20T00:00:00Z",
        finished_at=("2026-10-20T00:01:00Z" if repair_status == "complete" else None),
    )
    report = StepAttemptRecord(
        attempt_id="attempt-fc07-report-seq4",
        job_id=JOB_ID,
        step_key="maintenance_report",
        sequence=4,
        attempt_no=3,
        source_run_id=None,
        status="complete",
        result_version="report-v3",
        output_path=f"{JOB_ID}/attempt-fc07-report-seq4/result.json",
        output_sha256="b" * 64,
        started_at="2026-10-20T00:02:00Z",
        finished_at="2026-10-20T00:03:00Z",
    )
    decoy_sequence_four = StepAttemptRecord(
        attempt_id="attempt-fc07-other-seq4",
        job_id=JOB_ID,
        step_key="maintenance_repair",
        sequence=4,
        attempt_no=4,
        source_run_id=None,
        status="complete",
        result_version="decoy-v4",
        output_path=f"{JOB_ID}/attempt-fc07-other-seq4/result.json",
        output_sha256="c" * 64,
        started_at="2026-10-20T00:04:00Z",
        finished_at="2026-10-20T00:05:00Z",
    )

    class AttemptStore(_WorkflowStore):
        def list_step_attempts(self, *, job_id):
            assert job_id == JOB_ID
            return (repair, report, decoy_sequence_four)

    store = AttemptStore(tmp_path)
    launch = SimpleNamespace(
        source_launch_id=LAUNCH_ID,
        source_run_id=None,
        target_date=TARGET_DATE,
        historical_input=None,
    )
    check = SimpleNamespace(
        check_id="check-fc07-attempt-binding",
        source_launch_id=LAUNCH_ID,
        source_run_id=None,
        target_date=TARGET_DATE,
    )
    queue = WorkerQueueRecord(
        queue_id="queue-fc07-attempt-binding",
        source_launch_id=LAUNCH_ID,
        worker_job_id=JOB_ID,
        target_date=TARGET_DATE,
        status="queued",
        check_id=check.check_id,
        claim_owner_id=None,
        claim_generation=None,
        claimed_at=None,
        completed_at=None,
        reason_code=None,
        created_at="2026-10-20T00:00:00Z",
        updated_at="2026-10-20T00:00:00Z",
    )
    lease = LeaseToken("maintenance:fc07-attempt-binding", "worker-fc07", 1)
    calls = []

    def make_runner(stage_name: str):
        def run(context: MaintenanceWorkflowContext):
            calls.append(stage_name)
            if stage_name == "deployment":
                raise RuntimeError("synthetic deployment attempt trace")
            return WorkflowStageResult(status="complete")

        return run

    outcome = MaintenanceWorkflowProvider(
        store, {stage: make_runner(stage) for stage in WORKFLOW_STAGE_ORDER}
    )(launch, check, queue, lease, threading.Event())
    assert outcome.status == "complete"
    assert calls == list(WORKFLOW_STAGE_ORDER)
    invocation_id = store.observations[0]["invocation_id"]
    deployment_event = next(
        event
        for event in store.events
        if event.get("stage") == "deployment" and event.get("phase") == "begin"
    )
    report_event = next(
        event
        for event in store.events
        if event.get("stage") == "report" and event.get("phase") == "begin"
    )
    assert deployment_event["attempt_id"] == (
        repair.attempt_id if expected_deployment_attempt == "repair" else None
    )
    assert report_event["attempt_id"] == report.attempt_id
    deployment_handoff = json.loads(
        (
            tmp_path
            / "workflow-handoffs"
            / JOB_ID
            / invocation_id
            / "deployment.json"
        ).read_text(encoding="utf-8")
    )
    report_handoff = json.loads(
        (
            tmp_path
            / "workflow-handoffs"
            / JOB_ID
            / invocation_id
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert deployment_handoff["attempt_id"] == deployment_event["attempt_id"]
    assert deployment_handoff["attempt_id"] != report.attempt_id
    assert report_handoff["attempt_id"] == report.attempt_id
