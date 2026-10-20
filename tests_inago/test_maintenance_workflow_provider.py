"""Focused synthetic checks for the worker workflow-provider boundary."""

from __future__ import annotations

import threading
import json
import hashlib
import subprocess
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from automation.inago.maintenance.cli import main as maintenance_cli_main
from automation.inago.maintenance.controller import MaintenanceController
from automation.inago.maintenance.daily_launcher import (
    DailyProcessResult,
    run_daily_launch,
)
from automation.inago.maintenance.planning import expected_missing_reason
from automation.inago.maintenance.repair import FullRegressionEvidence
from automation.inago.maintenance.store import (
    DailyCheckRecord,
    DailyLaunchRecord,
    LeaseToken,
    MaintenanceJobRecord,
    MaintenanceStore,
    WorkerQueueRecord,
)
from automation.inago.maintenance.settings import ensure_settings_file
import automation.inago.maintenance.production_workflow as production_workflow
from automation.inago.maintenance.production_workflow import (
    run_daily_check_incident_stage,
    run_daily_evidence_stage,
    prepare_daily_investigation_stage,
    run_daily_planning_stage,
    prepare_daily_repair_stage,
    run_daily_repair_author_stage,
    run_daily_repair_test_stage,
    run_daily_repair_review_input_stage,
    run_daily_repair_review_stage,
    run_daily_investigation_author_stage,
    run_daily_investigation_stage,
    run_daily_job_stage,
    prepare_daily_planning_stage,
)
from automation.inago.maintenance.workflow_provider import (
    WORKFLOW_STAGE_ORDER,
    MaintenanceWorkflowContext,
    MaintenanceWorkflowProvider,
    WorkflowResumeState,
    WorkflowStageResult,
)
from automation.inago.maintenance.stage_handoff import make_stage_handoff
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
    record_successful_deployment_fixture,
)


def test_provider_loads_existing_job_and_attempt_before_running_stages(tmp_path):
    store = make_store(tmp_path)
    launch_id = "launch-u21-0301"
    incident_id = create_incident(store, launch_id=launch_id)
    create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-u21-0301",
    )
    check = store.get_daily_check_by_launch(launch_id)
    assert check is not None
    launch = DailyLaunchRecord(
        source_launch_id=launch_id,
        target_date="2026-10-17",
        owner_pid=9401,
        started_at=BASE_TIME.isoformat(),
        status="complete",
        child_pid=9402,
        source_run_id=None,
        exit_code=4,
        completed_at=BASE_TIME.isoformat(),
        result_json=None,
        owner_start_time=BASE_TIME.isoformat(),
        child_start_time=BASE_TIME.isoformat(),
    )
    queue = WorkerQueueRecord(
        queue_id="queue-u21-0301",
        source_launch_id=launch_id,
        worker_job_id="worker-u21-0301",
        target_date="2026-10-17",
        status="queued",
        check_id=check.check_id,
        claim_owner_id=None,
        claim_generation=None,
        claimed_at=None,
        completed_at=None,
        reason_code=None,
        created_at=BASE_TIME.isoformat(),
        updated_at=BASE_TIME.isoformat(),
    )
    lease = store.acquire_lease(
        lease_key="maintenance:provider-test",
        owner_id="provider-test",
        owner_pid=9403,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME,
        ttl_seconds=90,
    )
    store.begin_step_attempt(
        job_id="job-release-u21-0301",
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME,
    )
    store.begin_step_attempt(
        job_id="job-release-u21-0301",
        step_key="maintenance_planning",
        sequence=2,
        lease=lease,
        started_at=BASE_TIME,
    )

    stage_calls = []
    contexts = []

    def runner(stage_name):
        def run(context):
            stage_calls.append(stage_name)
            contexts.append(context)
            if stage_name == "investigation":
                assert context.resume_state.job is not None
                assert context.resume_state.job.job_id == "job-release-u21-0301"
                attempt = context.resume_state.latest_attempts_by_sequence[1]
                assert attempt.step_key == "maintenance_investigation"
                assert attempt.status == "running"
            if stage_name == "planning":
                attempt = context.resume_state.latest_attempts_by_sequence[2]
                context.state["planning_stage_handoff"] = make_stage_handoff(
                    stage="planning",
                    invocation_id=context.state["invocation_id"],
                    identity={
                        "job_id": "job-release-u21-0301",
                        "incident_id": incident_id,
                        "source_launch_id": launch_id,
                        "source_run_id": None,
                        "target_date": "2026-10-17",
                        "attempt_id": attempt.attempt_id,
                    },
                    operation="planning.author.run_turn_or_validate",
                    operation_result="failed",
                    summary="計画担当から回答が得られませんでした。",
                    facts=["原因は回答失敗からは特定できません。"],
                    limitations=["計画内容は未確認です。"],
                    next_actions=["利用可能な記録から計画を続けます。"],
                    input_refs=["input/plan-context.json"],
                    output_refs=["attempts/author-failure.json"],
                    error=RuntimeError("synthetic planner failure"),
                    reason_code="ai_transport_failed",
                    correlation_id="planner-correlation-1",
                )
                return WorkflowStageResult(
                    status="unknown", reason_code="ai_transport_failed"
                )
            if stage_name == "report":
                return WorkflowStageResult(
                    status="blocked", reason_code="report_inputs_unavailable"
                )
            return WorkflowStageResult(status="complete")

        return run

    provider = MaintenanceWorkflowProvider(
        store,
        {stage: runner(stage) for stage in WORKFLOW_STAGE_ORDER},
    )
    outcome = provider(launch, check, queue, lease, threading.Event())

    assert outcome.status == "unknown"
    assert outcome.check_id == check.check_id
    assert outcome.reason_code == "report_inputs_unavailable"
    assert stage_calls == list(WORKFLOW_STAGE_ORDER)
    context = contexts[-1]
    planning_ref = (
        store.database_path.parent.parent
        / "workflow-handoffs"
        / "job-release-u21-0301"
        / context.state["invocation_id"]
        / "planning.json"
    )
    planning_handoff = json.loads(planning_ref.read_text(encoding="utf-8"))
    planning_attempt = next(
        item
        for item in store.list_step_attempts(job_id="job-release-u21-0301")
        if item.step_key == "maintenance_planning"
    )
    assert planning_handoff["attempt_id"] == planning_attempt.attempt_id
    assert "attempt_id" not in planning_handoff["identity"]
    assert planning_handoff["reason_code"] == "ai_transport_failed"
    assert planning_handoff["correlation_id"] == "planner-correlation-1"
    assert any(
        ref == "attempts/author-failure.json"
        for ref in planning_handoff["output_refs"]
    )
    assert any(ref.endswith("/planning.json") for ref in planning_handoff["output_refs"])
    assert planning_handoff["errors"][0]["exception_chain"] == ["RuntimeError"]


def test_identity_precondition_mismatch_hands_off_every_stage_and_starts_report(
    tmp_path, monkeypatch
):
    store = make_store(tmp_path)
    launch_id = "launch-u21-unbound"
    incident_id = create_incident(store, launch_id=launch_id)
    create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-u21-unbound",
    )
    check = store.get_daily_check_by_launch(launch_id)
    assert check is not None
    launch = DailyLaunchRecord(
        source_launch_id=launch_id,
        target_date="2026-10-17",
        owner_pid=9501,
        started_at=BASE_TIME.isoformat(),
        status="complete",
        child_pid=9502,
        source_run_id=None,
        exit_code=4,
        completed_at=BASE_TIME.isoformat(),
        result_json=None,
        owner_start_time=BASE_TIME.isoformat(),
        child_start_time=BASE_TIME.isoformat(),
    )
    mismatched_queue = WorkerQueueRecord(
        queue_id="queue-u21-unbound-mismatch",
        source_launch_id="different-launch-u21-unbound",
        worker_job_id="job-release-u21-unbound",
        target_date="2026-10-17",
        status="queued",
        check_id=check.check_id,
        claim_owner_id=None,
        claim_generation=None,
        claimed_at=None,
        completed_at=None,
        reason_code=None,
        created_at=BASE_TIME.isoformat(),
        updated_at=BASE_TIME.isoformat(),
    )
    lease = store.acquire_lease(
        lease_key="maintenance:provider-unbound-test",
        owner_id="provider-unbound-test",
        owner_pid=9503,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME,
        ttl_seconds=90,
    )
    before_jobs = store.list_workflow_jobs_for_launch(
        source_launch_id=launch_id,
        source_run_id=None,
        target_date="2026-10-17",
    )
    report_contexts = []

    def report_runner(context):
        report_contexts.append(context)
        return WorkflowStageResult(status="complete")

    def forbidden_job_lookup(**kwargs):
        raise AssertionError("mismatched invocation must not look up jobs")

    def forbidden_stage_persist(**kwargs):
        raise AssertionError("unbound stage result must not mutate a job")

    monkeypatch.setattr(store, "list_workflow_jobs_for_launch", forbidden_job_lookup)
    monkeypatch.setattr(store, "record_workflow_stage_observation", forbidden_stage_persist)
    provider = MaintenanceWorkflowProvider(
        store,
        {"report": report_runner},
    )

    outcome = provider(launch, check, mismatched_queue, lease, threading.Event())

    assert outcome.status == "unknown"
    assert outcome.reason_code == "maintenance_workflow_identity_invalid"
    assert len(report_contexts) == 1
    context = report_contexts[0]
    assert context.resume_state.job is None
    assert context.resume_state.attempts == ()
    assert context.queue.worker_job_id == ""
    assert context.queue.queue_id == ""
    assert report_contexts[0].state["workflow_stage_outcomes"]["report"][
        "operation_result"
    ] == "succeeded"
    assert mismatched_queue.source_launch_id == "different-launch-u21-unbound"

    handoff_root = store.database_path.parent.parent / "workflow-handoffs" / "unbound"
    invocation_dirs = list(handoff_root.iterdir())
    assert len(invocation_dirs) == 1
    invocation_dir = invocation_dirs[0]
    from automation.inago.maintenance.paths import (
        resolve_maintenance_failure_journal_path,
    )

    journal_path = resolve_maintenance_failure_journal_path(
        store.database_path.parent.parent
    )
    failure_events = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
    ]
    precondition_events = [
        event
        for event in failure_events
        if event["operation"] == "workflow.invocation.identity_precondition"
    ]
    assert len(precondition_events) == 1
    assert precondition_events[0]["invocation_id"] == invocation_dir.name
    assert precondition_events[0]["job_id"] is None
    assert precondition_events[0]["reason_code"] == "maintenance_workflow_identity_invalid"
    for stage_name in WORKFLOW_STAGE_ORDER:
        handoff_path = invocation_dir / f"{stage_name}.json"
        assert handoff_path.is_file(), stage_name
        payload = json.loads(handoff_path.read_text(encoding="utf-8"))
        assert payload["identity"]["job_id"] is None
        assert payload["stage_progress"] == "handed_off"
        if stage_name != "report":
            assert payload["operation_result"] == "not_performed"
    monkeypatch.undo()
    assert store.list_workflow_jobs_for_launch(
        source_launch_id=launch_id,
        source_run_id=None,
        target_date="2026-10-17",
    ) == before_jobs


def test_completed_job_resume_runs_only_report_stage(tmp_path):
    launch_id = "launch-u21-complete-resume"
    run_id = "run-u21-complete-resume"
    target_date = "2026-10-18"
    job = MaintenanceJobRecord(
        job_id="job-u21-complete-resume",
        incident_id="incident-u21-complete-resume",
        release_id="release-u21-complete-resume",
        source_run_id=run_id,
        result_version="release-u21-v1",
        resume_count=0,
        status="complete",
        created_at=BASE_TIME.isoformat(),
        updated_at=BASE_TIME.isoformat(),
        finished_at=BASE_TIME.isoformat(),
        terminal_reason_code=None,
    )
    launch = DailyLaunchRecord(
        source_launch_id=launch_id,
        target_date=target_date,
        owner_pid=9501,
        started_at=BASE_TIME.isoformat(),
        status="complete",
        child_pid=9502,
        source_run_id=run_id,
        exit_code=4,
        completed_at=BASE_TIME.isoformat(),
        result_json=None,
        owner_start_time=BASE_TIME.isoformat(),
        child_start_time=BASE_TIME.isoformat(),
    )
    check = DailyCheckRecord(
        check_id="check-u21-complete-resume",
        source_launch_id=launch_id,
        source_run_id=run_id,
        target_date=target_date,
        check_status="abnormal",
        reason_code="daily_error",
        observed_at=BASE_TIME.isoformat(),
    )
    queue = WorkerQueueRecord(
        queue_id="queue-u21-complete-resume",
        source_launch_id=launch_id,
        worker_job_id="worker-u21-complete-resume",
        target_date=target_date,
        status="queued",
        check_id=check.check_id,
        claim_owner_id=None,
        claim_generation=None,
        claimed_at=None,
        completed_at=None,
        reason_code=None,
        created_at=BASE_TIME.isoformat(),
        updated_at=BASE_TIME.isoformat(),
    )
    lease = LeaseToken(
        lease_key="maintenance:complete-resume",
        owner_id="worker-u21-complete-resume",
        generation=2,
    )

    class Store:
        database_path = tmp_path / "runtime" / "maintenance" / "state.sqlite"

        def reconcile_open_operation_events(self):
            return None

        def record_operation_event(self, _event):
            return None

        def list_workflow_jobs_for_launch(self, **_kwargs):
            return (job,)

        def list_step_attempts(self, *, job_id):
            assert job_id == job.job_id
            return ()

    stage_calls = []

    def runner(stage_name):
        def run(context):
            stage_calls.append(stage_name)
            assert context.resume_state.job == job
            assert context.resume_state.attempts == ()
            return WorkflowStageResult(status="complete")

        return run

    provider = MaintenanceWorkflowProvider(
        Store(), {stage: runner(stage) for stage in WORKFLOW_STAGE_ORDER}
    )
    outcome = provider(launch, check, queue, lease, threading.Event())

    assert outcome.status == "complete"
    assert outcome.check_id == check.check_id
    assert stage_calls == ["report"]


def test_repair_execution_stays_in_fixed_root_and_preserves_existing_edits(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    subprocess.run(["git", "init", str(project_root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(project_root), "config", "user.name", "Synthetic"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "config",
            "user.email",
            "synthetic@example.invalid",
        ],
        check=True,
        capture_output=True,
    )
    (project_root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(project_root), "add", "module.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(project_root), "commit", "-m", "base"],
        check=True,
        capture_output=True,
    )
    base_commit = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    fixed_root = project_root
    first_commit = production_workflow._ensure_repair_worktree(
        project_root=project_root,
        worktree_root=fixed_root,
        base_commit=base_commit,
    )
    (fixed_root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    resumed_commit = production_workflow._ensure_repair_worktree(
        project_root=project_root,
        worktree_root=fixed_root,
        base_commit=base_commit,
    )

    assert first_commit == base_commit
    assert resumed_commit == base_commit
    assert (fixed_root / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    status = subprocess.run(
        ["git", "-C", str(fixed_root), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    assert status.strip() == "M module.py"


def test_daily_evidence_stage_persists_and_reuses_redacted_bundle(
    tmp_path, monkeypatch
):
    data_root = tmp_path / "runtime"
    daily_database = tmp_path / "daily" / "state.sqlite"
    ensure_settings_file(data_root / "maintenance" / "settings.json")
    store = MaintenanceStore(data_root, daily_database_path=daily_database)
    store.initialize()

    def failed_daily_process(_command, _cwd, on_started):
        on_started(9601)
        return DailyProcessResult(
            returncode=4,
            stdout=b'{"status":"runtime_error","reason":"SyntheticError"}',
            stderr=b"",
            pid=9601,
        )

    result = run_daily_launch(
        store,
        data_root=data_root,
        config_path=tmp_path / "config.json",
        now=BASE_TIME,
        process_runner=failed_daily_process,
        command=("synthetic-daily-child",),
        cwd=tmp_path,
    )
    launch = store.get_daily_launch(result.launch_id)
    check = store.get_daily_check_by_launch(result.launch_id)
    queue = store.get_worker_queue(result.launch_id)
    assert launch is not None and check is not None and queue is not None
    lease_now = datetime.now(timezone.utc)
    lease = store.acquire_lease(
        lease_key="maintenance:evidence-test",
        owner_id="evidence-test",
        owner_pid=9602,
        owner_start_time=lease_now.isoformat(),
        now=lease_now,
    )
    context = MaintenanceWorkflowContext(
        launch=launch,
        check=check,
        queue=queue,
        lease=lease,
        cancel_event=threading.Event(),
        resume_state=WorkflowResumeState(job=None, attempts=()),
    )

    check_result = run_daily_check_incident_stage(
        context, MaintenanceController(store), store
    )
    assert check_result.status == "complete"
    first_result = run_daily_evidence_stage(context, store)
    assert first_result.status == "complete", first_result.reason_code
    manifest_ref = context.state["evidence_bundle_ref"]
    manifest_path = store.database_path.parent.joinpath(*manifest_ref.split("/"))
    manifest_before = manifest_path.read_bytes()
    manifest = json.loads(manifest_before)
    assert manifest["correlation"]["source_launch_id"] == result.launch_id
    assert manifest["classes"][1]["class_id"] == "L02"
    assert manifest["classes"][1]["status"] == "collected"

    second_result = run_daily_evidence_stage(context, store)

    assert second_result.status == "complete"
    assert context.state["evidence_bundle_ref"] == manifest_ref
    assert manifest_path.read_bytes() == manifest_before
    job_result = run_daily_job_stage(context, MaintenanceController(store), store)
    assert job_result.status == "complete", job_result.reason_code
    admitted_job = context.state["job"]
    assert admitted_job.job_id == context.state["job_id"]
    assert admitted_job.incident_id == context.state["incident_id"]
    assert admitted_job.source_run_id == launch.source_run_id
    jobs = store.list_workflow_jobs_for_launch(
        source_launch_id=launch.source_launch_id,
        source_run_id=launch.source_run_id,
        target_date=launch.target_date,
    )
    assert len(jobs) == 1
    assert jobs[0].job_id == admitted_job.job_id

    repeated_result = run_daily_job_stage(context, MaintenanceController(store), store)
    assert repeated_result.status == "complete", repeated_result.reason_code
    assert context.state["job"].job_id == admitted_job.job_id
    assert len(
        store.list_workflow_jobs_for_launch(
            source_launch_id=launch.source_launch_id,
            source_run_id=launch.source_run_id,
            target_date=launch.target_date,
        )
    ) == 1

def test_worker_cli_connects_provider_without_release_gate(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(
        "automation.inago.maintenance.skill_chain_runtime._codex",
        lambda: None,
    )
    data_root = tmp_path / "runtime"
    daily_database = tmp_path / "daily" / "state.sqlite"
    ensure_settings_file(data_root / "maintenance" / "settings.json")
    store = MaintenanceStore(data_root, daily_database_path=daily_database)
    store.initialize()

    def failed_daily_process(_command, _cwd, on_started):
        on_started(9501)
        return DailyProcessResult(
            returncode=4,
            stdout=b'{"status":"runtime_error","reason":"SyntheticError"}',
            stderr=b"",
            pid=9501,
        )

    launch = run_daily_launch(
        store,
        data_root=data_root,
        config_path=tmp_path / "config.json",
        now=datetime(2026, 10, 20, 4, tzinfo=timezone.utc),
        process_runner=failed_daily_process,
        command=("synthetic-daily-child",),
        cwd=tmp_path,
    )

    exit_code = maintenance_cli_main(
        [
            "--data-root",
            str(data_root),
            "--daily-database",
            str(daily_database),
            "worker",
            "--once",
            "--source-launch-id",
            launch.launch_id,
        ]
    )
    output = json.loads(capsys.readouterr().out)
    (tmp_path / "worker-result.json").write_text(
        json.dumps(output, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    queue = store.get_worker_queue(launch.launch_id)
    check = store.get_daily_check_by_launch(launch.launch_id)

    assert exit_code == 0, output
    assert output["status"] == "maintenance_incomplete"
    assert output["maintenance_resolution_status"] == "unknown"
    assert output["reason_code"] == "skill_chain_log_missing"
    assert check is not None and check.check_status == "incomplete"
    assert store.counts()["incident"] == 1, output
    assert queue is not None
    assert queue.status == "complete"
    jobs = store.list_workflow_jobs_for_launch(
        source_launch_id=launch.launch_id,
        source_run_id=None,
        target_date="2026-10-20",
    )
    assert len(jobs) == 1
    assert jobs[0].job_id != queue.worker_job_id
