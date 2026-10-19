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

    stage_calls = []

    def runner(stage_name):
        def run(context):
            stage_calls.append(stage_name)
            if stage_name == "investigation":
                assert context.resume_state.job is not None
                assert context.resume_state.job.job_id == "job-release-u21-0301"
                attempt = context.resume_state.latest_attempts_by_sequence[1]
                assert attempt.step_key == "maintenance_investigation"
                assert attempt.status == "running"
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


def test_repair_worktree_is_pinned_and_resumable(tmp_path):
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
    worktree_root = tmp_path / "runtime" / "worktrees" / "job-synthetic"
    worktree_root.mkdir(parents=True)

    first_commit = production_workflow._ensure_repair_worktree(
        project_root=project_root,
        worktree_root=worktree_root,
        base_commit=base_commit,
    )
    (worktree_root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    resumed_commit = production_workflow._ensure_repair_worktree(
        project_root=project_root,
        worktree_root=worktree_root,
        base_commit=base_commit,
    )

    assert first_commit == base_commit
    assert resumed_commit == base_commit
    assert (worktree_root / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"


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
    bundle_hash = context.state["evidence_bundle_sha256"]
    manifest_path = store.database_path.parent.joinpath(*manifest_ref.split("/"))
    manifest_before = manifest_path.read_bytes()
    manifest = json.loads(manifest_before)
    assert manifest["correlation"]["source_launch_id"] == result.launch_id
    assert manifest["classes"][1]["class_id"] == "L02"
    assert manifest["classes"][1]["status"] == "collected"

    second_result = run_daily_evidence_stage(context, store)

    assert second_result.status == "complete"
    assert context.state["evidence_bundle_ref"] == manifest_ref
    assert context.state["evidence_bundle_sha256"] == bundle_hash
    assert manifest_path.read_bytes() == manifest_before

    candidate_commit = "a" * 40
    monkeypatch.setattr(
        production_workflow, "_repository_head", lambda: candidate_commit
    )
    release_id = "release-u21-evidence"
    store.create_release(
        release_id=release_id,
        author_id="implementation-agent",
        base_commit="b" * 40,
        result_commit=candidate_commit,
        result_version="result-u21-evidence",
        config_version="settings-u21-evidence",
        required_case_ids=("evidence-case-u21",),
        created_at=BASE_TIME.isoformat(),
    )
    store.record_test_result(
        release_id=release_id,
        case_id="evidence-case-u21",
        attempt=1,
        result_version="result-u21-evidence",
        status="PASS",
        recorded_at=BASE_TIME.isoformat(),
    )
    store.record_release_review(
        release_id=release_id,
        reviewer_id="independent-reviewer",
        reviewed_version="result-u21-evidence",
        status="approved",
        reviewed_at=BASE_TIME.isoformat(),
    )
    record_successful_deployment_fixture(
        store,
        release_id=release_id,
        deployed_at=BASE_TIME.isoformat(),
    )
    assert (
        store.evaluate_release_gate(
            release_id=release_id,
            evaluated_at=BASE_TIME,
        ).status
        == "ready"
    )

    job_result = run_daily_job_stage(context, MaintenanceController(store), store)
    assert job_result.status == "complete", job_result.reason_code
    jobs = store.list_workflow_jobs_for_launch(
        source_launch_id=launch.source_launch_id,
        source_run_id=launch.source_run_id,
        target_date=launch.target_date,
    )
    assert len(jobs) == 1
    manifest = store.get_job_input_manifest(
        job_id=jobs[0].job_id,
        expected_identity={
            "incident_id": context.state["incident_id"],
            "source_launch_id": launch.source_launch_id,
            "source_run_id": launch.source_run_id,
            "target_date": launch.target_date,
            "job_id": jobs[0].job_id,
        },
    )
    assert manifest.manifest_sha256 == context.state["input_manifest_sha256"]
    second_job_result = run_daily_job_stage(
        context, MaintenanceController(store), store
    )
    assert second_job_result.status == "complete", second_job_result.reason_code
    assert (
        len(
            store.list_workflow_jobs_for_launch(
                source_launch_id=launch.source_launch_id,
                source_run_id=launch.source_run_id,
                target_date=launch.target_date,
            )
        )
        == 1
    )
    assert store.get_job_input_manifest(job_id=jobs[0].job_id) == manifest

    monkeypatch.setattr(
        production_workflow.shutil, "which", lambda _name: "C:/synthetic/codex.exe"
    )
    investigation_result = prepare_daily_investigation_stage(
        context, MaintenanceController(store), store
    )
    assert investigation_result.status == "complete", investigation_result.reason_code
    attempt = context.state["investigation_attempt"]
    session_plan = context.state["investigation_session_plan"]
    assert attempt.step_key == "maintenance_investigation"
    assert attempt.sequence == 1
    assert session_plan.role_name == "rr-inago-incident-investigator"
    assert session_plan.requested_model == "gpt-6.1-sol"
    assert session_plan.requested_effort == "high"
    assert session_plan.requested_service_tier == "default"
    assert session_plan.requested_fast_mode is False

    controller = MaintenanceController(store)
    controller.bind_ai_parent_session(
        job_id=manifest.job_id,
        session_key=session_plan.session_key,
        parent_key="A",
        role_name=session_plan.role_name,
        thread_id="thread-synthetic-resume",
        manifest_sha256=manifest.manifest_sha256,
        requested_model=session_plan.requested_model,
        requested_effort=session_plan.requested_effort,
        requested_service_tier=session_plan.requested_service_tier,
        requested_fast_mode=session_plan.requested_fast_mode,
        role_config_sha256=session_plan.role_config_sha256,
        sandbox=session_plan.sandbox_policy,
        created_at=datetime.now(timezone.utc),
    )
    first_attempt_id = attempt.attempt_id
    release_time = datetime.now(timezone.utc)
    store.release_lease(lease=lease, released_at=release_time)
    resumed_lease = store.acquire_lease(
        lease_key=lease.lease_key,
        owner_id=lease.owner_id,
        owner_pid=9602,
        owner_start_time=release_time.isoformat(),
        now=release_time,
    )
    context = replace(context, lease=resumed_lease)
    resumed_result = prepare_daily_investigation_stage(context, controller, store)
    assert resumed_result.status == "complete", resumed_result.reason_code
    prior_attempt = store.get_step_attempt(attempt_id=first_attempt_id)
    attempt = context.state["investigation_attempt"]
    session_plan = context.state["investigation_session_plan"]
    assert prior_attempt.status == "interrupted"
    assert attempt.attempt_no == 2
    assert session_plan.resume_thread_id == "thread-synthetic-resume"

    investigation_context = production_workflow._investigation_context(
        context,
        store,
        context.state["investigation_manifest"],
        attempt,
    )
    evidence_ref = investigation_context["evidence_refs"][0]

    def make_investigation_response(current_context):
        return {
            **current_context,
            "schema_version": "maintenance-investigation-response-v2",
            "stage": "investigation",
            "summary": "日次結果の安全な観測値を確認しました。",
            "report_markdown": "\n".join(
                ["# 調査報告", *(f"## {number}. 章" for number in range(1, 10)), ""]
            ),
            "facts": [
                {
                    "fact_id": "daily-failure-1",
                    "classification": "incident",
                    "phase": "daily",
                    "target_date": launch.target_date,
                    "operation": "日次処理を実行する",
                    "expected": "日次処理が完了する",
                    "actual": "日次処理が失敗した",
                    "impact": "対象日の処理が完了していない",
                    "recovery": "未確認",
                    "cause_confidence": "unknown",
                    "cause_statement": None,
                    "evidence_refs": [evidence_ref],
                }
            ],
            "investigation_limits": ["収集済みの伏字済み日次観測値だけを確認した。"],
            "non_incident_observations": [],
            "verification_candidates": ["不足証拠を確認する。"],
            "missing_evidence": [],
            "review_required": True,
        }

    response = make_investigation_response(investigation_context)

    fake_errors = []
    author_turns = [0]
    review_turns = [0]

    class FakeInvestigationSession:
        def __init__(self, role_name):
            self.role_name = role_name

        def run_turn(self, prompt, output_schema, **kwargs):
            assert "outputSchema" not in kwargs
            assert "allOf" not in kwargs["server_output_schema"]
            if self.role_name == "rr-inago-incident-investigator":
                author_turns[0] += 1
                if author_turns[0] == 3:
                    assert "Review requires correcting the report." in prompt
                current_attempt = context.state["investigation_attempt"]
                current_context = production_workflow._investigation_context(
                    context,
                    store,
                    context.state["investigation_manifest"],
                    current_attempt,
                )
                turn_response = make_investigation_response(current_context)
                allowed_outputs = (
                    "facts.json",
                    "investigation.md",
                    "result.json",
                )
                output_digest = "c" * 64
                turn_id = f"turn-synthetic-investigation-{len(created_sessions)}"
            else:
                review_turns[0] += 1
                assert self.role_name == "rr-inago-incident-investigation-reviewer"
                assert "REVIEW_INPUT_JSON" in prompt
                current_context = context.state["investigation_context"]
                server_properties = kwargs["server_output_schema"]["properties"]
                turn_response = {
                    **current_context,
                    "schema_version": "maintenance-investigation-review-v1",
                    "stage": "investigation_review",
                    "reviewer_id": server_properties["reviewer_id"]["enum"][0],
                    "review_execution_id": server_properties["review_execution_id"][
                        "enum"
                    ][0],
                    "reviewed_version": server_properties["reviewed_version"]["enum"][
                        0
                    ],
                    "status": (
                        "changes_requested" if review_turns[0] == 1 else "approved"
                    ),
                    "findings": (
                        ["Review requires correcting the report."]
                        if review_turns[0] == 1
                        else []
                    ),
                }
                allowed_outputs = ("review.json", "review.md")
                output_digest = "d" * 64
                turn_id = f"turn-synthetic-independent-review-{review_turns[0]}"
            try:
                Draft202012Validator(output_schema).validate(turn_response)
                Draft202012Validator.check_schema(kwargs["server_output_schema"])
                Draft202012Validator(kwargs["server_output_schema"]).validate(
                    turn_response
                )
            except Exception as exc:
                fake_errors.append(exc)
                raise
            return SimpleNamespace(
                response=turn_response,
                output_path=Path(f"{self.role_name}-response.json"),
                output_sha256=output_digest,
                observation_status="configured_unverified",
                turn=SimpleNamespace(
                    turn_id=turn_id,
                    thread_id=f"thread-synthetic-{self.role_name}",
                ),
            )

        def close(self):
            pass

    created_sessions = []

    def fake_create_session(_adapter, _plan):
        if _plan.role_name == "rr-inago-incident-investigator":
            assert _plan.resume_thread_id == "thread-synthetic-resume"
        session = FakeInvestigationSession(_plan.role_name)
        created_sessions.append(session)
        return session

    monkeypatch.setattr(
        production_workflow.MaintenanceAIAdapter,
        "create_session",
        fake_create_session,
    )
    author_result = run_daily_investigation_author_stage(
        context,
        MaintenanceController(store),
        store,
    )
    assert author_result.status == "complete", (author_result.reason_code, fake_errors)
    assert len(created_sessions) == 1
    assert context.state["investigation_response"] == response | {"status": "complete"}
    assert context.state["investigation_response_sha256"] == "c" * 64
    assert context.state["investigation_result_version"] == (
        f"investigation:{attempt.attempt_id}:v1"
    )

    manifest_path = store.database_path.parent.joinpath(*evidence_ref.split("/"))
    manifest_bytes = manifest_path.read_bytes()
    manifest_path.write_bytes(manifest_bytes + b" ")
    tampered_result = run_daily_investigation_author_stage(
        context,
        MaintenanceController(store),
        store,
    )
    assert tampered_result.status == "blocked"
    assert (
        tampered_result.reason_code == "investigation_evidence_manifest_hash_mismatch"
    )
    assert len(created_sessions) == 1
    assert "investigation_response" not in context.state
    manifest_path.write_bytes(manifest_bytes)

    complete_result = run_daily_investigation_stage(
        context,
        controller,
        store,
    )
    assert complete_result.status == "complete", (
        complete_result.reason_code,
        fake_errors,
    )
    assert len(created_sessions) == 3
    outcome = context.state["investigation_outcome"]
    assert outcome.investigation_status == "complete"
    assert outcome.review_status == "changes_requested"
    assert outcome.next_action == "handoff"
    assert outcome.handoff is not None
    completed_attempt = store.get_step_attempt(attempt_id=outcome.attempt_id)
    assert completed_attempt.status == "handoff_accepted"
    assert completed_attempt.output_path == (
        f"{jobs[0].job_id}/{outcome.attempt_id}/result.json"
    )

    planning_setup = prepare_daily_planning_stage(context, controller, store)
    assert planning_setup.status == "complete", planning_setup.reason_code
    planning_attempt = context.state["planning_attempt"]
    planning_plan = context.state["planning_session_plan"]
    planning_context = context.state["planning_context"]
    assert planning_attempt.step_key == "maintenance_planning"
    assert planning_attempt.sequence == 2
    assert planning_context["investigation_attempt_id"] == outcome.attempt_id
    assert planning_context["requirements"] == [
        "MR-009",
        "MR-011",
        "MR-016",
        "FACTREQ-" + hashlib.sha256(b"daily-failure-1").hexdigest()[:24],
    ]
    assert planning_context["case_manifest"][1] == {
        "case_id": "C32",
        "node": "tests_inago/test_maintenance_planning.py::test_c32_contract",
    }
    assert planning_context["case_manifest"][2]["case_id"].startswith("FACTCASE-")
    assert planning_context["case_manifest"][2]["node"].startswith(
        "tests_inago/test_maintenance_regressions.py::test_daily_fact_"
    )
    assert planning_plan.role_name == "common-execution-planner"
    assert planning_plan.requested_model == "gpt-6.1-sol"
    assert planning_plan.requested_effort == "high"
    assert planning_plan.requested_service_tier == "default"
    assert planning_plan.requested_fast_mode is False

    planning_facts = context.state["planning_facts"]
    planning_daily = context.state["planning_daily_completion"]
    planning_cases = planning_context["case_manifest"]
    planning_requirements = planning_context["requirements"]
    fact_ids = [fact["fact_id"] for fact in planning_facts]
    evidence_refs = sorted(
        {ref for fact in planning_facts for ref in fact["evidence_refs"]}
    )
    case_ids = [case["case_id"] for case in planning_cases]

    def make_plan_response():
        prompt_details = {
            "作業ルート:": planning_context["work_root"],
            "計画文書:": planning_context["plan_document"],
            "対象STEP:": "STEP 1 verify the observed daily failure",
            "入力・版:": " ".join(
                [
                    planning_context["investigation_result_version"],
                    *fact_ids,
                    planning_context["base_commit"],
                    *planning_context["input_refs"],
                    *planning_context["evidence_refs"],
                ]
            ),
            "開始条件:": "approved investigation handoff and current base commit",
            "作業内容:": "verify the observed failure and preserve unknown cause status",
            "所有ファイル:": "automation/inago/daily_pipeline.py",
            "変更禁止:": "production database, credentials, user postings, and out-of-scope files",
            "検証:": "run only the planned regression cases after implementation",
            "合格条件:": "all requirements, facts, cases, and log fields are bound",
            "停止・再開:": "hold on missing evidence and reuse the same approved investigation",
            "証拠:": "save plan, mapping, review, result, and command evidence",
            "Git:": "stage only owned files and preserve unrelated changes",
            "次工程:": "handoff to repair only after independent review approval",
        }
        full_prompt = "\n".join(
            f"{label} {value}" for label, value in prompt_details.items()
        )
        requirements_per_step = list(planning_requirements)
        cases = []
        for case in planning_cases:
            cases.append(
                {
                    "case_id": case["case_id"],
                    "node": case["node"],
                    "requirement_step": [
                        {"requirement_id": requirement, "step_number": 1}
                        for requirement in requirements_per_step
                    ],
                    "purpose": "Check the approved behavior and maintenance contract.",
                    "kind_method": "focused pytest regression case",
                    "preconditions": "the approved investigation and exact base commit are pinned",
                    "inputs_operations": "apply only the approved product change",
                    "execution_configuration": "ordinary local pytest with synthetic fixtures",
                    "expected_output": "the observed defect is corrected without fabricated success",
                    "expected_state_logs": "daily completion values remain unchanged",
                    "forbidden_side_effects": "no production database or posting operation",
                    "pass_criteria": "the exact case passes on the reviewed candidate",
                    "evidence": "record node, command, result, and candidate hash",
                    "cleanup_retry": "retain failures and retry only after an owned fix",
                }
            )
        logging_fields = (
            "source_run_id",
            "overall_status",
            "daily_status",
            "reason_code",
            "failure_phase",
            "trace_status",
            "missing_reason",
        )
        response = {
            "schema_version": "maintenance-planning-response-v1",
            "stage": "planning",
            "status": "ready",
            "summary": "Plan is bound to approved facts and daily completion.",
            "sections": [
                {
                    "number": number,
                    "title": f"Section {number}",
                    "content": f"Evidence-bound plan section {number}.",
                }
                for number in range(15)
            ],
            "steps": [
                {
                    "identity": {
                        "number": 1,
                        "name": "verify and repair the confirmed behavior",
                        "status": "planned",
                    },
                    "purpose": "Correct only the behavior established by approved facts.",
                    "requirements": requirements_per_step,
                    "predecessors": [planning_context["investigation_result_version"]],
                    "inputs": [
                        planning_context["base_commit"],
                        *planning_context["input_refs"],
                        *planning_context["evidence_refs"],
                        *fact_ids,
                    ],
                    "ownership": ["automation/inago/daily_pipeline.py"],
                    "procedure": "Verify each fact, implement only a supported correction, and run the exact cases.",
                    "outputs": ["reviewed product diff and test results"],
                    "constraints": ["Do not guess when the cause is unknown."],
                    "review": ["Review product code only; do not review test code."],
                    "acceptance_evidence": [
                        "All listed cases pass on the candidate hash."
                    ],
                    "failure_recovery": [
                        "Keep the queue blocked with the missing evidence."
                    ],
                    "handoff": [
                        "Continue after independent plan review approves this version."
                    ],
                    "full_prompt": full_prompt,
                }
            ],
            "cases": cases,
            "coverage": [
                {
                    "requirement_id": requirement,
                    "fact_ids": fact_ids,
                    "evidence_refs": evidence_refs,
                    "step_numbers": [1],
                    "case_ids": case_ids,
                }
                for requirement in requirements_per_step
            ],
            "cause_actions": [
                {
                    "fact_id": fact["fact_id"],
                    "action": (
                        "repair_verified"
                        if fact["cause_confidence"] == "confirmed"
                        else (
                            "verify_only"
                            if fact["cause_confidence"] == "unknown"
                            else "hold"
                        )
                    ),
                    "basis": "Use only the approved finding and its evidence references.",
                }
                for fact in planning_facts
            ],
            "repair_scope": {
                "owned_paths": sorted(
                    {
                        "automation/inago/daily_pipeline.py",
                        *(case["node"].split("::", 1)[0] for case in cases),
                    }
                )
            },
            "logging_contract": {
                **{
                    field: (
                        expected_missing_reason(planning_daily)
                        if field == "missing_reason"
                        else planning_daily[field]
                    )
                    for field in logging_fields
                },
                "field_bindings": [
                    {
                        "field": field,
                        "schema_ref": f"#/properties/logging_contract/properties/{field}",
                        "source_ref": (
                            "automation/inago/maintenance/planning.py::expected_missing_reason"
                            if field == "missing_reason"
                            else f"automation/inago/maintenance/daily_launcher.py::{field}"
                        ),
                        "case_id": "C32",
                    }
                    for field in logging_fields
                ],
            },
            "hold_conditions": [],
            "final_explanation": "The plan retains unknown causes and limits changes to the approved scope.",
            "review_required": True,
        }
        return response

    planner_turns = [0]
    planning_review_turns = [0]

    class FakePlanningSession:
        def __init__(self, role_name):
            self.role_name = role_name

        def run_turn(self, prompt, output_schema, **kwargs):
            if self.role_name == "common-execution-planner":
                planner_turns[0] += 1
                assert "PLANNING_INPUT_JSON" in prompt
                if planner_turns[0] == 2:
                    assert (
                        "Review requires clarifying the owned product path." in prompt
                    )
                response = make_plan_response()
                output_path = Path("plan.json")
                output_digest = "e" * 64
                turn_id = "turn-synthetic-planning"
                allowed_outputs = ("plan.json",)
            else:
                assert self.role_name == "common-execution-plan-reviewer"
                planning_review_turns[0] += 1
                review_payload = json.loads(
                    prompt.partition("PLANNING_REVIEW_INPUT_JSON:\n")[2]
                )
                response = {
                    "schema_version": "maintenance-planning-review-v1",
                    "stage": "planning_review",
                    "reviewer_id": review_payload["reviewer_id"],
                    "review_execution_id": review_payload["review_execution_id"],
                    "reviewed_version": review_payload["reviewed_version"],
                    "status": (
                        "changes_requested"
                        if planning_review_turns[0] == 1
                        else "approved"
                    ),
                    "findings": (
                        ["Review requires clarifying the owned product path."]
                        if planning_review_turns[0] == 1
                        else []
                    ),
                }
                output_path = Path("review-response.json")
                output_digest = "f" * 64
                turn_id = f"turn-synthetic-planning-review-{planning_review_turns[0]}"
                allowed_outputs = ("review-response.json",)
            Draft202012Validator(output_schema).validate(response)
            assert kwargs["allowed_outputs"] == allowed_outputs
            return SimpleNamespace(
                response=response,
                output_path=output_path,
                output_sha256=output_digest,
                observation_status="configured_unverified",
                turn=SimpleNamespace(
                    turn_id=turn_id,
                    thread_id=f"thread-synthetic-{self.role_name}",
                ),
            )

        def close(self):
            pass

    monkeypatch.setattr(
        production_workflow.MaintenanceAIAdapter,
        "create_session",
        lambda _adapter, plan: FakePlanningSession(plan.role_name),
    )
    planning_result = run_daily_planning_stage(context, controller, store)
    assert planning_result.status == "complete", (
        planning_result.reason_code,
        getattr(context.state.get("planning_outcome"), "review_reason_code", None),
        planning_review_turns,
    )
    assert planning_review_turns[0] == 1
    assert planner_turns[0] == 1
    assert context.state["planning_response"]["stage"] == "planning"
    assert context.state["planning_response_sha256"] == "e" * 64
    assert context.state["planning_turn_id"] == "turn-synthetic-planning"
    planning_outcome = context.state["planning_outcome"]
    assert planning_outcome.plan_status == "ready"
    assert planning_outcome.review_status == "changes_requested"
    assert planning_outcome.gate_status == "changes_requested"
    first_planning_attempt = store.get_step_attempt(
        attempt_id=planning_outcome.attempt_id
    )
    assert first_planning_attempt.attempt_no == 1
    monkeypatch.setattr(
        production_workflow,
        "_ensure_repair_worktree",
        lambda **kwargs: kwargs["base_commit"],
    )
    repair_setup_result = prepare_daily_repair_stage(context, controller, store)
    assert repair_setup_result.status == "complete", repair_setup_result.reason_code
    repair_attempt = controller.get_step_attempt(
        attempt_id=context.state["repair_attempt"].attempt_id
    )
    assert repair_attempt.sequence == 3
    setup_path = (store.database_path.parent / "attempts").joinpath(
        *context.state["repair_setup_path"].split("/")
    )
    saved_setup = json.loads(setup_path.read_text(encoding="utf-8"))
    assert saved_setup["base_commit"] == store.get_latest_deployed_commit()
    assert saved_setup["plan_attempt_id"] == planning_outcome.attempt_id
    assert (
        saved_setup["owned_paths"]
        == context.state["planning_response"]["repair_scope"]["owned_paths"]
    )
    assert saved_setup["case_manifest"]["case_nodes"]
    repair_attempt_id = repair_attempt.attempt_id
    setup_sha256 = context.state["repair_setup_sha256"]
    resumed_setup_result = prepare_daily_repair_stage(context, controller, store)
    assert resumed_setup_result.status == "complete"
    assert context.state["repair_attempt"].attempt_id == repair_attempt_id
    assert context.state["repair_setup_sha256"] == setup_sha256

    changed_product_path = "automation/inago/daily_pipeline.py"
    monkeypatch.setattr(
        production_workflow,
        "_changed_paths_since",
        lambda _root, _base: (changed_product_path,),
    )
    monkeypatch.setattr(
        production_workflow,
        "_run_git",
        lambda _root, *_arguments: b"synthetic product diff",
    )
    monkeypatch.setattr(
        production_workflow, "repository_code_sha256", lambda _root: "c" * 64
    )
    product_file = context.state["repair_worktree_root"] / changed_product_path
    product_file.parent.mkdir(parents=True, exist_ok=True)
    product_file.write_bytes(b"synthetic candidate product code\n")

    class FakeRepairSession:
        def __init__(self, plan):
            self.plan = plan

        def run_turn(self, prompt, output_schema, **kwargs):
            assert "REPAIR_INPUT_JSON" in prompt
            assert kwargs["allowed_outputs"] == [changed_product_path]
            response = {
                **self.plan.manifest.identity(),
                "schema_version": "maintenance-repair-author-response-v1",
                "attempt_id": self.plan.attempt_id,
                "result_version": f"repair:{self.plan.attempt_id}:implementation-v1",
                "status": "implemented",
                "summary": "Applied the approved product change.",
                "changed_files": [changed_product_path],
            }
            Draft202012Validator(output_schema).validate(response)
            output_path = self.plan.attempt_output_root / (
                "B--common-implementer--" + "a" * 32 + ".json"
            )
            envelope = {
                "schema_version": "maintenance-ai-response-v1",
                "identity": self.plan.manifest.identity(),
                "attempt_id": self.plan.attempt_id,
                "owner": {
                    "parent_key": "B",
                    "role_name": "common-implementer",
                    "role_config_sha256": self.plan.role_config_sha256,
                },
                "allowed_outputs": [changed_product_path],
                "thread_id": "thread-synthetic-repair",
                "turn_id": "turn-synthetic-repair",
                "observation_status": "configured_unverified",
                "response": response,
            }
            output_bytes = (
                json.dumps(
                    envelope,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            output_path.write_bytes(output_bytes)
            return SimpleNamespace(
                response=response,
                output_path=output_path,
                output_sha256=hashlib.sha256(output_bytes).hexdigest(),
                observation_status="configured_unverified",
                turn=SimpleNamespace(
                    turn_id="turn-synthetic-repair",
                    thread_id="thread-synthetic-repair",
                ),
            )

        def close(self):
            pass

    monkeypatch.setattr(
        production_workflow.MaintenanceAIAdapter,
        "create_session",
        lambda _adapter, plan: FakeRepairSession(plan),
    )
    repair_author_result = run_daily_repair_author_stage(context, controller, store)
    assert repair_author_result.status == "complete", repair_author_result.reason_code
    assert context.state["repair_author_response"]["status"] == "implemented"
    assert context.state["repair_code_sha256"] == "c" * 64
    assert context.state["repair_author_thread_id"] == "thread-synthetic-repair"
    implementation_result_path = (
        store.database_path.parent
        / "attempts"
        / context.state["repair_implementation_result_path"]
    )
    saved_implementation_result = json.loads(
        implementation_result_path.read_text(encoding="utf-8")
    )
    pinned_repair_attempt = store.get_step_attempt(attempt_id=repair_attempt_id)
    assert pinned_repair_attempt.output_path == (
        f"{pinned_repair_attempt.job_id}/{repair_attempt_id}/implementation-result.json"
    )
    assert (
        pinned_repair_attempt.output_sha256
        == hashlib.sha256(implementation_result_path.read_bytes()).hexdigest()
    )
    assert saved_implementation_result["changed_product_paths"] == [
        changed_product_path
    ]
    assert saved_implementation_result["product_file_sha256"] == {
        changed_product_path: hashlib.sha256(
            b"synthetic candidate product code\n"
        ).hexdigest()
    }
    for key in (
        "repair_author_response",
        "repair_author_output_path",
        "repair_author_output_sha256",
        "repair_author_turn_id",
        "repair_author_thread_id",
        "repair_author_observation_status",
        "repair_changed_paths",
        "repair_code_sha256",
        "repair_author_id",
        "repair_implementation_result_path",
        "repair_implementation_result_sha256",
    ):
        context.state.pop(key)
    approved_test_path = next(
        item["node"].split("::", 1)[0]
        for item in context.state["repair_setup"]["case_manifest"]["case_nodes"]
        if production_workflow._is_test_path(item["node"].split("::", 1)[0])
    )
    monkeypatch.setattr(
        production_workflow,
        "_changed_paths_since",
        lambda _root, _base: tuple(sorted((changed_product_path, approved_test_path))),
    )
    monkeypatch.setattr(
        production_workflow.MaintenanceAIAdapter,
        "create_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("saved repair implementation should be reused")
        ),
    )
    resumed_author_result = run_daily_repair_author_stage(context, controller, store)
    assert resumed_author_result.status == "complete", resumed_author_result.reason_code
    assert context.state["repair_author_response"]["status"] == "implemented"
    assert context.state["repair_author_turn_id"] == "turn-synthetic-repair"
    implementation_bytes = implementation_result_path.read_bytes()
    implementation_result_path.write_bytes(implementation_bytes + b"tamper")
    tampered_author_result = run_daily_repair_author_stage(context, controller, store)
    assert tampered_author_result.status == "blocked"
    assert (
        tampered_author_result.reason_code == "repair_implementation_artifact_invalid"
    )
    implementation_result_path.write_bytes(implementation_bytes)
    monkeypatch.setattr(
        production_workflow,
        "_changed_paths_since",
        lambda _root, _base: tuple(
            sorted((changed_product_path, "tests_inago/test_unapproved.py"))
        ),
    )
    unapproved_test_result = run_daily_repair_author_stage(context, controller, store)
    assert unapproved_test_result.status == "blocked"
    assert unapproved_test_result.reason_code == "repair_implementation_scope_mismatch"

    monkeypatch.setattr(
        production_workflow,
        "_changed_paths_since",
        lambda _root, _base: (changed_product_path,),
    )
    approved_cases = {
        item["case_id"]: item["node"]
        for item in context.state["repair_setup"]["case_manifest"]["case_nodes"]
    }
    signatures = {case_id: f"signature-{case_id}" for case_id in approved_cases}
    ledger = production_workflow.RepairTestLedger(
        campaign_id=f"repair-{repair_attempt_id}-synthetic",
        required_cases=approved_cases,
        semantic_signatures=signatures,
        code_sha256="c" * 64,
        invocation_parent_id=None,
        _job_id=repair_attempt.job_id,
        _attempt_id=repair_attempt_id,
    )
    repair_evidence = production_workflow.RepairHostEvidence(
        changed_paths=(changed_product_path,),
        owned_paths=tuple(context.state["repair_setup"]["owned_paths"]),
        baseline_assertions={},
        candidate_assertions={},
        test_ledger=ledger,
        current_code_sha256="c" * 64,
        test_evidence=(),
        approved_cases=approved_cases,
        job_id=repair_attempt.job_id,
        attempt_id=repair_attempt_id,
        base_commit=context.state["repair_setup"]["base_commit"],
        plan_attempt_id=context.state["repair_setup"]["plan_attempt_id"],
        plan_result_version=context.state["repair_setup"]["plan_result_version"],
        plan_output_sha256=context.state["repair_setup"]["plan_output_sha256"],
        checkout_root=str(context.state["repair_worktree_root"]),
    )

    full_regression_calls = []

    class FakeRepairCampaignController:
        def prepare_repair_campaign(self, *, attempt_id, attempts_root):
            assert attempt_id == repair_attempt_id
            assert attempts_root == context.state["repair_attempts_root"]
            return repair_evidence

        def run_repair_full_regression(self, current_ledger):
            full_regression_calls.append(current_ledger.campaign_id)
            cases = current_ledger.full_regression_cases
            assert cases is not None
            return FullRegressionEvidence(
                run_id="synthetic-repair-full-run",
                token=current_ledger._full_regression_token,
                receipt_id="synthetic-repair-receipt",
                code_sha256=current_ledger.code_sha256,
                cases=cases,
                results={case_id: "PASS" for case_id, _node in cases},
                exit_code=0,
                collected=len(cases),
                errors=0,
                skips=0,
                collection_errors=0,
                invocation_parent_id=None,
                receipt_sha256="e" * 64,
            )

    repair_test_result = run_daily_repair_test_stage(
        context,
        controller,
        store,
        repair_controller=FakeRepairCampaignController(),
    )
    assert repair_test_result.status == "complete", repair_test_result.reason_code
    assert context.state["repair_campaign_id"] == ledger.campaign_id
    assert context.state["repair_code_sha256"] == "c" * 64
    repair_review_input_result = run_daily_repair_review_input_stage(
        context,
        controller,
        store,
        repair_controller=FakeRepairCampaignController(),
    )
    assert (
        repair_review_input_result.status == "complete"
    ), repair_review_input_result.reason_code
    review_input = context.state["repair_review_input"]
    assert review_input["candidate_code_sha256"] == "c" * 64
    assert review_input["product_paths"] == [changed_product_path]
    assert review_input["approved_cases"] == [
        {"case_id": case_id, "node": node}
        for case_id, node in sorted(approved_cases.items())
    ]
    assert (
        review_input["review_target"]["sha256"]
        == context.state["repair_review_target_sha256"]
    )
    review_input_path = (
        store.database_path.parent
        / "attempts"
        / context.state["repair_review_input_path"]
    )
    review_input_bytes = review_input_path.read_bytes()
    review_input_sha256 = context.state["repair_review_input_sha256"]
    saved_review_input = json.loads(review_input_path.read_text(encoding="utf-8"))
    assert saved_review_input == review_input
    for key in (
        "repair_review_input",
        "repair_review_input_path",
        "repair_review_input_sha256",
        "repair_review_target_path",
        "repair_review_target_sha256",
        "repair_patch_assessment_path",
        "repair_patch_assessment_sha256",
    ):
        context.state.pop(key)
    resumed_review_input_result = run_daily_repair_review_input_stage(
        context,
        controller,
        store,
        repair_controller=FakeRepairCampaignController(),
    )
    assert (
        resumed_review_input_result.status == "complete"
    ), resumed_review_input_result.reason_code
    assert review_input_path.read_bytes() == review_input_bytes
    assert context.state["repair_review_input_sha256"] == review_input_sha256
    assert full_regression_calls == [ledger.campaign_id]
    review_input_path.write_bytes(review_input_bytes + b"tamper")
    tampered_review_input_result = run_daily_repair_review_input_stage(
        context,
        controller,
        store,
        repair_controller=FakeRepairCampaignController(),
    )
    assert tampered_review_input_result.status == "blocked"
    assert (
        tampered_review_input_result.reason_code
        == "investigation_artifact_already_exists"
    )
    review_input_path.write_bytes(review_input_bytes)
    saved_test_ledger = (
        store.database_path.parent
        / "attempts"
        / context.state["repair_test_ledger_path"]
    )
    saved_test_ledger_document = json.loads(
        saved_test_ledger.read_text(encoding="utf-8")
    )
    saved_test_ledger_bytes = saved_test_ledger.read_bytes()
    saved_test_ledger_sha256 = context.state["repair_test_ledger_sha256"]
    assert saved_test_ledger_document["code_sha256"] == "c" * 64
    assert saved_test_ledger_document["required_cases"] == [
        {"semantic_case_id": case_id, "node": node}
        for case_id, node in approved_cases.items()
    ]
    assert full_regression_calls == [ledger.campaign_id]
    context.state.pop("repair_test_ledger")
    context.state.pop("repair_test_ledger_path")
    context.state.pop("repair_test_ledger_sha256")
    resumed_test_result = run_daily_repair_test_stage(
        context,
        controller,
        store,
        repair_controller=FakeRepairCampaignController(),
    )
    assert resumed_test_result.status == "complete", resumed_test_result.reason_code
    assert full_regression_calls == [ledger.campaign_id]
    assert saved_test_ledger.read_bytes() == saved_test_ledger_bytes
    assert context.state["repair_test_ledger_sha256"] == saved_test_ledger_sha256

    review_turn_calls = []

    class FakeRepairReviewSession:
        def __init__(self, session_plan):
            self.plan = session_plan

        def run_turn(
            self,
            _prompt,
            response_schema,
            *,
            allowed_outputs,
            cwd,
            deadline,
            cancel_event,
        ):
            assert allowed_outputs == ["review-response.json"]
            assert cwd == self.plan.cwd
            assert deadline > 0
            assert not cancel_event.is_set()
            review_turn_calls.append(self.plan.session_key)
            response = {
                key: value["const"]
                for key, value in response_schema["properties"].items()
                if "const" in value
            }
            response.update(
                status="approved",
                summary="Approved product diff and recorded case outcomes.",
                findings=[],
            )
            thread_id = "thread-synthetic-repair-review"
            if self.plan.resume_thread_id is None:
                controller.bind_ai_parent_session(
                    job_id=self.plan.manifest.job_id,
                    session_key=self.plan.session_key,
                    parent_key=self.plan.parent_key,
                    role_name=self.plan.role_name,
                    thread_id=thread_id,
                    manifest_sha256=self.plan.manifest.manifest_sha256,
                    requested_model=self.plan.requested_model,
                    requested_effort=self.plan.requested_effort,
                    requested_service_tier=self.plan.requested_service_tier,
                    requested_fast_mode=self.plan.requested_fast_mode,
                    role_config_sha256=self.plan.role_config_sha256,
                    sandbox=self.plan.sandbox_policy,
                    created_at=datetime.now(timezone.utc),
                )
                controller.record_ai_session_observation(
                    job_id=self.plan.manifest.job_id,
                    session_key=self.plan.session_key,
                    thread_id=thread_id,
                    status="verified",
                    observed_model=self.plan.requested_model,
                    observed_effort=self.plan.requested_effort,
                    observed_service_tier=self.plan.requested_service_tier,
                    observed_at=datetime.now(timezone.utc),
                )
            else:
                assert self.plan.resume_thread_id == thread_id
            turn_id = "turn-synthetic-repair-review"
            output_path = self.plan.attempt_output_root / (
                "B--common-reviewer--" + "f" * 32 + ".json"
            )
            envelope = {
                "schema_version": "maintenance-ai-response-v1",
                "identity": self.plan.manifest.identity(),
                "attempt_id": self.plan.attempt_id,
                "owner": {
                    "parent_key": self.plan.parent_key,
                    "role_name": self.plan.role_name,
                    "role_config_sha256": self.plan.role_config_sha256,
                },
                "allowed_outputs": allowed_outputs,
                "thread_id": thread_id,
                "turn_id": turn_id,
                "observation_status": "verified",
                "response": response,
            }
            output_bytes = (
                json.dumps(
                    envelope,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            output_path.write_bytes(output_bytes)
            return SimpleNamespace(
                response=response,
                output_path=output_path,
                output_sha256=hashlib.sha256(output_bytes).hexdigest(),
                observation_status="verified",
                turn=SimpleNamespace(turn_id=turn_id, thread_id=thread_id),
            )

        def close(self):
            pass

    monkeypatch.setattr(
        production_workflow.MaintenanceAIAdapter,
        "create_session",
        lambda adapter, session_plan: FakeRepairReviewSession(session_plan),
    )
    repair_review_result = run_daily_repair_review_stage(
        context,
        controller,
        store,
        repair_controller=FakeRepairCampaignController(),
    )
    assert repair_review_result.status == "complete", repair_review_result.reason_code
    assert context.state["repair_review_status"] == "approved"
    assert context.state["repair_reviewer_id"] == "thread-synthetic-repair-review"
    assert review_turn_calls == ["B:common-reviewer"]
    saved_review_result_path = (
        store.database_path.parent
        / "attempts"
        / context.state["repair_review_result_path"]
    )
    saved_review_result_bytes = saved_review_result_path.read_bytes()
    assert json.loads(saved_review_result_bytes)["response"]["status"] == "approved"
    monkeypatch.setattr(
        production_workflow.MaintenanceAIAdapter,
        "create_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("saved repair review should be reused")
        ),
    )
    resumed_repair_review_result = run_daily_repair_review_stage(
        context,
        controller,
        store,
        repair_controller=FakeRepairCampaignController(),
    )
    assert (
        resumed_repair_review_result.status == "complete"
    ), resumed_repair_review_result.reason_code
    assert review_turn_calls == ["B:common-reviewer"]
    assert saved_review_result_path.read_bytes() == saved_review_result_bytes


def test_worker_cli_connects_provider_and_blocks_without_ready_release(
    tmp_path, capsys
):
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
        now=BASE_TIME,
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
    queue = store.get_worker_queue(launch.launch_id)
    check = store.get_daily_check_by_launch(launch.launch_id)

    assert exit_code == 3, output
    assert output["status"] == "blocked"
    assert output["reason_code"] == "maintenance_release_for_commit_missing"
    assert check is not None and check.check_status == "abnormal"
    assert store.counts()["incident"] == 1
    assert queue is not None
    assert queue.status == "blocked"
    assert queue.reason_code == "maintenance_release_for_commit_missing"
