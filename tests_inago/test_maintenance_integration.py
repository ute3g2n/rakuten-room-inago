"""Isolated U18 end-to-end checks over the maintenance control core."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
import json
import sqlite3

from automation.inago.maintenance.controller import MaintenanceController
from automation.inago.maintenance.cli import main as maintenance_cli_main
from automation.inago.maintenance.daily_launcher import (
    DailyProcessResult,
    run_daily_launch,
)
from automation.inago.maintenance.process_identity import ProcessObservation
from automation.inago.maintenance.store import MaintenanceStore
from automation.inago.maintenance.settings import ensure_settings_file
from tests_inago.maintenance_investigation_support import (
    BASE_COMMIT,
    make_check_response,
    make_context,
)
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_ready_release,
    make_store,
    record_successful_deployment_fixture,
)
from tests_inago.test_maintenance_deployment import _case, _deploy, _git
from automation.inago.maintenance.deployment import commit_owned_paths
from automation.inago.maintenance.worker import (
    dispatch_ready_worker,
    run_worker_once,
)
from automation.inago.maintenance.verification import report_source


def test_c49_contract(tmp_path):
    store = make_store(tmp_path)
    controller = MaintenanceController(store)
    context = make_context(
        incident_id="incident-c49",
        source_launch_id="launch-c49",
        source_run_id="run-c49",
        target_date="2026-10-18",
        job_id="check-c49",
        attempt_id="attempt-c49",
        base_commit=BASE_COMMIT,
    )
    observed = []

    outcome = controller.run_daily_check_stage(
        context=context,
        preclassification="normal_candidate",
        check_runner=lambda job: observed.append(job) or make_check_response(job),
        observed_at=BASE_TIME + timedelta(minutes=1),
    )

    assert outcome.stage.status == "checked_ok"
    assert outcome.record.created is True
    assert len(observed) == 1
    assert observed[0]["stage"] == "check"
    counts = store.counts()
    assert counts["daily_check"] == 1
    assert counts["incident"] == 0
    assert counts["maintenance_job"] == 0
    assert counts["step_attempt"] == 0


def test_c51_contract(tmp_path):
    store = make_store(tmp_path)
    controller = MaintenanceController(store)
    cases = (
        ("external_wait", "waiting_external", "incomplete"),
        ("needs_investigation", "unknown", "incomplete"),
    )

    for index, (preclassification, assessment, expected_status) in enumerate(
        cases, start=1
    ):
        context = make_context(
            incident_id=f"incident-c51-{index}",
            source_launch_id=f"launch-c51-{index}",
            source_run_id=None,
            target_date="2026-10-18",
            job_id=f"check-c51-{index}",
            attempt_id=f"attempt-c51-{index}",
            base_commit=BASE_COMMIT,
        )
        outcome = controller.run_daily_check_stage(
            context=context,
            preclassification=preclassification,
            check_runner=lambda job, assessment=assessment: make_check_response(
                job, assessment=assessment
            ),
            observed_at=BASE_TIME + timedelta(minutes=index),
        )

        assert outcome.stage.status in {"waiting_external", "blocked"}
        assert outcome.record.incident_id is None
        with sqlite3.connect(store.database_path) as connection:
            row = connection.execute(
                "SELECT check_status FROM daily_check WHERE source_launch_id = ?",
                (context["source_launch_id"],),
            ).fetchone()
        assert row == (expected_status,)

    counts = store.counts()
    assert counts["daily_check"] == 2
    assert counts["incident"] == 0
    assert counts["maintenance_job"] == 0


def test_lc06_contract(tmp_path):
    case = _case(tmp_path)
    active = case["active"]

    class GitRuntime:
        def current_commit(self):
            return _git(active, "rev-parse", "HEAD")

        def activate(self, commit, checkout=None):
            _git(active, "fetch", "origin", "main")
            _git(active, "checkout", "--detach", commit)

        def healthy(self, expected_commit):
            return self.current_commit() == expected_commit

    case["coordinator"].runtime_switcher = GitRuntime()
    case["daily"]["active"] = True
    waiting = _deploy(case)
    assert waiting.status == "blocked"
    assert waiting.reason_code == "daily_owner_active_or_unknown"
    assert case["coordinator"].exclusion_guard.held is False
    assert case["store"].get_lease("maintenance:deployment").state == "released"

    case["daily"]["active"] = False
    deployed = _deploy(case)
    assert deployed.status == "deployed"
    assert deployed.pushed_commit == deployed.active_commit == case["result"]
    assert _git(case["remote"], "rev-parse", "refs/heads/main") == case["result"]
    assert _git(active, "rev-parse", "HEAD") == case["result"]
    assert case["coordinator"].exclusion_guard.held is False
    assert case["store"].get_lease("maintenance:deployment").state == "released"

    repo = case["repo"]
    _git(repo, "checkout", "-b", "codex/report")
    report = repo / "reports" / "synthetic-incident.md"
    report.parent.mkdir(parents=True)
    report.write_text("Synthetic reviewed report.\n", encoding="utf-8")
    documentation_commit = commit_owned_paths(
        repo,
        ("reports/synthetic-incident.md",),
        message="docs: publish synthetic maintenance report",
    )
    _git(repo, "push", "origin", f"{documentation_commit}:refs/heads/codex/report")
    assert documentation_commit != case["result"]
    assert (
        _git(
            repo,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            documentation_commit,
        )
        == "reports/synthetic-incident.md"
    )
    assert _git(active, "rev-parse", "HEAD") == deployed.active_commit
    assert case["pending"].count == 2


def test_c59_contract(tmp_path):
    store = make_store(tmp_path)
    observed_during_launch = []

    def process_probe(pid):
        launch = next(iter(store.list_daily_launches(target_date="2026-10-17")))
        if launch.status == "running" and pid == launch.owner_pid:
            return ProcessObservation("running", launch.owner_start_time)
        return ProcessObservation("stopped", None)

    def process_runner(_command, _cwd, on_started):
        on_started(9202)
        launch = next(iter(store.list_daily_launches(target_date="2026-10-17")))
        during = run_worker_once(
            store,
            now=BASE_TIME + timedelta(minutes=2),
            owner_id="worker-c59-during",
            owner_pid=9203,
            owner_start_time=(BASE_TIME + timedelta(minutes=2)).isoformat(),
            process_probe=process_probe,
            source_launch_id=launch.source_launch_id,
        )
        observed_during_launch.append(during)
        payload = {
            "schema_version": "daily-run-result-v1",
            "target_date": "2026-10-17",
            "status": "complete",
            "overall_status": "complete",
            "daily_status": "complete",
            "run_id": "run-c59",
        }
        return DailyProcessResult(
            returncode=0,
            stdout=json.dumps(payload).encode("utf-8"),
            stderr=b"",
            pid=9202,
        )

    launch_outcome = run_daily_launch(
        store,
        data_root=tmp_path / "runtime",
        config_path=tmp_path / "config.json",
        now=BASE_TIME,
        process_runner=process_runner,
        command=("synthetic-daily-child",),
        cwd=tmp_path,
    )
    assert launch_outcome.notification_status == "saved"
    assert observed_during_launch[0].status == "waiting"
    assert observed_during_launch[0].reason_code == "daily_launch_running"

    launch_id = launch_outcome.launch_id
    task_calls = []
    dispatched = dispatch_ready_worker(
        store,
        process_probe=lambda _pid: ProcessObservation("stopped", None),
        task_runner=lambda command, **_kwargs: task_calls.append(command)
        or type("Result", (), {"returncode": 0})(),
    )
    assert dispatched.status == "dispatched"
    assert task_calls == [["schtasks.exe", "/Run", "/TN", "RakutenRoomInagoWorker"]]
    complete = run_worker_once(
        store,
        now=BASE_TIME + timedelta(minutes=3),
        owner_id="worker-c59-after",
        owner_pid=9204,
        owner_start_time=(BASE_TIME + timedelta(minutes=3)).isoformat(),
        process_probe=lambda _pid: ProcessObservation("stopped", None),
        source_launch_id=launch_id,
    )
    duplicate = run_worker_once(
        store,
        now=BASE_TIME + timedelta(minutes=4),
        owner_id="worker-c59-duplicate",
        owner_pid=9205,
        owner_start_time=(BASE_TIME + timedelta(minutes=4)).isoformat(),
        process_probe=lambda _pid: ProcessObservation("stopped", None),
        source_launch_id=launch_id,
    )

    assert complete.status == "dispatch_complete"
    assert duplicate.status == "dispatch_complete"
    assert duplicate.duplicate is True
    assert duplicate.worker_job_id == complete.worker_job_id
    assert duplicate.check_id == complete.check_id
    assert store.counts()["daily_check"] == 1
    assert store.counts()["worker_queue"] == 1


def test_worker_block_and_explicit_resume_are_durable(tmp_path, capsys):
    store = make_store(tmp_path)

    def failed_daily_process(_command, _cwd, on_started):
        on_started(9321)
        return DailyProcessResult(
            returncode=4,
            stdout=b'{"status":"runtime_error","reason":"ExampleError"}',
            stderr=b"",
            pid=9321,
        )

    launch = run_daily_launch(
        store,
        data_root=tmp_path / "runtime",
        config_path=tmp_path / "config.json",
        now=BASE_TIME,
        process_runner=failed_daily_process,
        command=("synthetic-daily-child",),
        cwd=tmp_path,
    )

    blocked = run_worker_once(
        store,
        now=BASE_TIME + timedelta(minutes=3),
        owner_id="worker-blocked-c59",
        owner_pid=9322,
        owner_start_time=(BASE_TIME + timedelta(minutes=3)).isoformat(),
        process_probe=lambda _pid: ProcessObservation("stopped", None),
        source_launch_id=launch.launch_id,
    )
    queue = store.get_worker_queue(launch.launch_id)
    assert blocked.status == "blocked"
    assert blocked.reason_code == "maintenance_workflow_provider_unavailable"
    assert queue is not None
    assert queue.status == "blocked"
    assert queue.reason_code == "maintenance_workflow_provider_unavailable"
    assert queue.check_id is None
    events = store.list_worker_queue_events(queue.queue_id)
    assert [event.event_type for event in events] == ["blocked"]
    assert events[0].check_id == blocked.check_id

    data_root = tmp_path / "data"
    ensure_settings_file(data_root / "maintenance" / "settings.json")
    common_args = [
        "--data-root",
        str(data_root),
        "--daily-database",
        str(tmp_path / "daily-runtime" / "state.sqlite"),
        "resume-worker",
        "--source-launch-id",
        launch.launch_id,
    ]
    assert (
        maintenance_cli_main(
            common_args
        )
        == 0
    )
    capsys.readouterr()
    resumed = store.get_worker_queue(launch.launch_id)
    assert resumed is not None

    assert resumed.status == "queued"
    assert resumed.reason_code is None
    events = store.list_worker_queue_events(queue.queue_id)
    assert [event.event_type for event in events] == ["blocked", "resumed"]
    assert events[1].reason_code == "maintenance_workflow_provider_unavailable"


def test_c54_contract(tmp_path, capsys):
    import hashlib

    data_root = tmp_path / "maintenance-data"
    daily_database = tmp_path / "ordinary-daily" / "state.sqlite"
    assert (
        maintenance_cli_main(
            [
                "--data-root",
                str(data_root),
                "--daily-database",
                str(daily_database),
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    store = MaintenanceStore(data_root, daily_database_path=daily_database)
    store.create_release(
        release_id="release-c54",
        author_id="author-c54",
        base_commit="a" * 40,
        result_commit="b" * 40,
        result_version="result-c54",
        config_version="config-c54",
        required_case_ids=("case-c54",),
        created_at=BASE_TIME.isoformat(),
    )
    store.record_test_result(
        release_id="release-c54",
        case_id="case-c54",
        attempt=1,
        result_version="result-c54",
        status="PASS",
        recorded_at=(BASE_TIME + timedelta(seconds=1)).isoformat(),
    )
    store.record_release_review(
        release_id="release-c54",
        reviewer_id="reviewer-c54",
        reviewed_version="result-c54",
        status="approved",
        reviewed_at=(BASE_TIME + timedelta(seconds=2)).isoformat(),
    )
    record_successful_deployment_fixture(
        store,
        release_id="release-c54",
        deployed_at=(BASE_TIME + timedelta(seconds=3)).isoformat(),
    )
    gate = store.evaluate_release_gate(
        release_id="release-c54",
        evaluated_at=(BASE_TIME + timedelta(seconds=4)).isoformat(),
    )
    assert gate.status == "ready"

    evidence_bytes = b'{"launch_status":"failed","exit_code":4}\n'
    evidence_path = data_root / "E0" / "launch-record.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_bytes(evidence_bytes)
    observation = {
        "schema_version": "historical-daily-intake-v1",
        "original_source_launch_id": "original-launch-20261017",
        "source_run_id": None,
        "target_date": "2026-10-17",
        "launch_status": "failed",
        "daily_status": "failed",
        "exit_code": 4,
        "reason_code": "daily_runtime_error",
        "evidence_ref": "E0/launch-record.json",
        "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "observed_at": "2026-10-17T13:20:00+09:00",
    }
    observation_path = data_root / "intake" / "historical-2026-10-17.json"
    observation_path.parent.mkdir(parents=True)
    observation_path.write_text(
        json.dumps(observation, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    command = [
        "--data-root",
        str(data_root),
        "--daily-database",
        str(daily_database),
        "enqueue-historical",
        "--release-id",
        "release-c54",
        "--observation",
        "intake/historical-2026-10-17.json",
        "--enqueued-at",
        "2026-10-18T14:00:00+09:00",
    ]

    assert maintenance_cli_main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert maintenance_cli_main(command) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["status"] == second["status"] == "queued"
    assert first["target_date"] == second["target_date"] == "2026-10-17"
    assert first["source_run_id"] is second["source_run_id"] is None
    assert first["source_launch_id"] == second["source_launch_id"]
    assert first["check_id"] == second["check_id"]
    assert first["incident_id"] == second["incident_id"]
    assert first["job_id"] == second["job_id"]
    assert first["duplicate"] is False and second["duplicate"] is True
    assert store.counts()["daily_check"] == 1
    assert store.counts()["maintenance_job"] == 1
    assert store.counts()["incident"] == 1
    assert store.counts()["daily_launch"] == 0
    assert store.counts()["worker_queue"] == 0
    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            "SELECT source_run_id, target_date FROM daily_check WHERE check_id = ?",
            (first["check_id"],),
        ).fetchone()
        job_row = connection.execute(
            "SELECT status, source_run_id FROM maintenance_job WHERE job_id = ?",
            (first["job_id"],),
        ).fetchone()
    assert row == (None, "2026-10-17")
    assert job_row == ("queued", None)
    assert not daily_database.exists()
    evidence_path = data_root / first["evidence_ref"]
    assert evidence_path.is_file()


def test_c56_contract(tmp_path):
    def prepare_deployed_store(root):
        store = make_store(root)
        initial = store.record_daily_check(
            source_launch_id=f"launch-{root.name}",
            source_run_id=None,
            target_date="2026-10-17",
            check_status="abnormal",
            observed_at=BASE_TIME,
            reason_code="daily_result_incomplete",
            incident={
                "dedupe_key": f"daily:c56:{root.name}",
                "component": "daily_pipeline",
                "event_code": "daily_error",
            },
        )
        assert initial.incident_id is not None
        incident_id = initial.incident_id
        create_ready_release(
            store,
            incident_id=incident_id,
            release_id=f"release-{root.name}",
        )
        with store._connect() as connection:
            deployment_id = connection.execute(
                "SELECT deployment_id FROM deployment WHERE release_id = ?",
                (f"release-{root.name}",),
            ).fetchone()[0]
        pending = store.begin_deployment_verification(
            deployment_id=deployment_id,
            target_date="2026-10-17",
            created_at=BASE_TIME + timedelta(minutes=5),
        )
        assert pending.status == "pending"
        duplicate_pending = store.begin_deployment_verification(
            deployment_id=deployment_id,
            target_date="2026-10-17",
            created_at=BASE_TIME + timedelta(minutes=6),
        )
        assert duplicate_pending.verification_id == pending.verification_id
        return store, deployment_id, incident_id

    def complete_next_daily(store, *, launch_id, launch_status, check_status):
        started = BASE_TIME + timedelta(days=1, minutes=5)
        completed = BASE_TIME + timedelta(days=1, minutes=10)
        store.record_daily_launch_start(
            source_launch_id=launch_id,
            target_date="2026-10-18",
            owner_pid=9301,
            owner_start_time=started,
            started_at=started,
        )
        status_values = {
            "complete": ("complete", "complete", 0, "run-c56"),
            "failed": ("failed", "failed", 1, None),
            "blocked": ("blocked", "unknown", 4, None),
        }
        overall, daily, exit_code, run_id = status_values[launch_status]
        reason = None if check_status == "normal" else "daily_result_incomplete"
        launch = store.record_daily_launch_completion(
            source_launch_id=launch_id,
            target_date="2026-10-18",
            child_pid=9302,
            source_run_id=run_id,
            launch_status=launch_status,
            exit_code=exit_code,
            completed_at=completed,
            check_status=check_status,
            reason_code=reason,
            result_summary={
                "schema_version": "daily-launch-completion-v1",
                "source_launch_id": launch_id,
                "target_date": "2026-10-18",
                "status": launch_status,
                "exit_code": exit_code,
                "source_run_id": run_id,
                "overall_status": overall,
                "daily_status": daily,
                "reason_code": reason,
                "failure_phase": None,
                "failed_checks": [],
                "primary_error_class": None,
                "secondary_error_classes": [],
                "trace_status": "healthy",
            },
        )
        return launch, completed

    verified_store, verified_deployment_id, _ = prepare_deployed_store(
        tmp_path / "verified"
    )
    launch, completed = complete_next_daily(
        verified_store,
        launch_id="launch-c56-verified",
        launch_status="complete",
        check_status="normal",
    )
    check = verified_store.get_daily_check_by_launch(launch.source_launch_id)
    assert check is not None
    verified = verified_store.record_deployment_verification_observation(
        deployment_id=verified_deployment_id,
        source_launch_id=launch.source_launch_id,
        check_id=check.check_id,
        active_commit="commit-1",
        evidence_ref="evidence/C56/verified.json",
        observed_at=completed,
    )
    assert verified.status == "verified"
    assert report_source(verified) == {
        "status": "verified",
        "observed_at": "2026-10-18T13:10:00+09:00",
        "evidence_href": "evidence/C56/verified.json",
    }
    repeated = verified_store.record_deployment_verification_observation(
        deployment_id=verified_deployment_id,
        source_launch_id=launch.source_launch_id,
        check_id=check.check_id,
        active_commit="commit-1",
        evidence_ref="evidence/C56/verified.json",
        observed_at=completed,
    )
    assert repeated.verification_id == verified.verification_id
    assert verified_store.counts()["daily_launch"] == 1

    blocked_store, blocked_deployment_id, _ = prepare_deployed_store(
        tmp_path / "blocked"
    )
    blocked_launch, blocked_at = complete_next_daily(
        blocked_store,
        launch_id="launch-c56-blocked",
        launch_status="blocked",
        check_status="incomplete",
    )
    blocked_check = blocked_store.get_daily_check_by_launch(
        blocked_launch.source_launch_id
    )
    assert blocked_check is not None
    blocked = blocked_store.record_deployment_verification_observation(
        deployment_id=blocked_deployment_id,
        source_launch_id=blocked_launch.source_launch_id,
        check_id=blocked_check.check_id,
        active_commit=None,
        evidence_ref="evidence/C56/blocked.json",
        observed_at=blocked_at,
    )
    assert blocked.status == "blocked"
    assert blocked.reason_code == "next_daily_observation_incomplete"
    assert report_source(blocked)["status"] == "blocked"

    recurrent_store, recurrent_deployment_id, original_incident_id = (
        prepare_deployed_store(tmp_path / "recurrent")
    )
    recurrent_launch, recurrent_at = complete_next_daily(
        recurrent_store,
        launch_id="launch-c56-recurrent",
        launch_status="failed",
        check_status="incomplete",
    )
    recurrent_check = recurrent_store.get_daily_check_by_launch(
        recurrent_launch.source_launch_id
    )
    assert recurrent_check is not None
    recurrent_context = make_context(
        incident_id=original_incident_id,
        source_launch_id=recurrent_launch.source_launch_id,
        source_run_id=None,
        target_date="2026-10-18",
        job_id="job-c56-recurrent",
        attempt_id="attempt-c56-recurrent",
        base_commit=BASE_COMMIT,
    )
    classified = MaintenanceController(recurrent_store).run_daily_check_stage(
        context=recurrent_context,
        preclassification="needs_investigation",
        check_runner=lambda job: make_check_response(
            job, assessment="needs_investigation"
        ),
        observed_at=recurrent_at + timedelta(seconds=1),
        incident={
            "dedupe_key": "daily:c56:recurrent",
            "component": "daily_pipeline",
            "event_code": "daily_error",
        },
    )
    assert classified.record.created is False
    assert classified.record.incident_id == original_incident_id
    recurrent_check = recurrent_store.get_daily_check_by_launch(
        recurrent_launch.source_launch_id
    )
    assert recurrent_check is not None and recurrent_check.check_status == "abnormal"
    recurrent = recurrent_store.record_deployment_verification_observation(
        deployment_id=recurrent_deployment_id,
        source_launch_id=recurrent_launch.source_launch_id,
        check_id=recurrent_check.check_id,
        active_commit=None,
        evidence_ref="evidence/C56/recurrent.json",
        observed_at=recurrent_at + timedelta(seconds=1),
    )
    assert recurrent.status == "recurred"
    assert recurrent.recurrence_incident_id == original_incident_id
    assert report_source(recurrent)["status"] == "recurred"

    absent_store, absent_deployment_id, _ = prepare_deployed_store(tmp_path / "absent")
    not_observed = absent_store.mark_deployment_verification_not_observed(
        deployment_id=absent_deployment_id,
        observed_at=BASE_TIME + timedelta(days=1, hours=2),
    )
    assert not_observed.status == "not_observed"
    assert report_source(not_observed)["evidence_href"] is None
    assert absent_store.counts()["daily_launch"] == 0


def test_c50_contract(tmp_path):
    import hashlib
    import subprocess
    import sys
    from copy import deepcopy
    from pathlib import Path

    from automation.inago.maintenance.deployment import (
        DeploymentCoordinator,
        PendingSnapshot,
        commit_owned_paths,
    )
    from automation.inago.maintenance.reporting import (
        finalize_report_review,
        prepare_incident_report,
        publish_reviewed_report,
    )
    from automation.inago.maintenance.repair import RepairController, RepairReview
    from tests_inago.test_maintenance_planning import (
        PLAN_VERSION,
        _complete_plan,
        _make_plan_response,
        _start_planning_attempt,
    )
    from tests_inago.test_maintenance_repair import _with_repair_scope
    from tests_inago.test_maintenance_report import (
        _job_context,
        _report_response,
        _source_snapshot,
    )

    (
        store,
        controller,
        lease,
        plan_context,
        attempts_root,
        plan_output,
        _plan_attempt,
    ) = _start_planning_attempt(tmp_path / "workflow")
    plan_outcome = _complete_plan(
        controller,
        lease,
        plan_context,
        attempts_root,
        plan_output,
        _with_repair_scope(_make_plan_response(plan_context)),
    )
    repair_attempt = controller.begin_repair_attempt(
        job_id=plan_context["job_id"],
        attempts_root=attempts_root,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=18),
    )

    repo = tmp_path / "toy-repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.name", "C50 Fixture")
    _git(repo, "config", "user.email", "c50@example.invalid")
    fix_path = repo / "automation" / "inago" / "maintenance" / "fix.py"
    fix_path.parent.mkdir(parents=True)
    fix_path.write_text(
        "def calculate(value):\n    return value + 2\n", encoding="utf-8"
    )
    test_path = repo / "tests" / "test_fix.py"
    test_path.parent.mkdir()
    test_path.write_text(
        "from automation.inago.maintenance.fix import calculate\n\n"
        "def test_calculate():\n    assert calculate(0) == 1\n",
        encoding="utf-8",
    )
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", "test fixture baseline")
    base_commit = _git(repo, "rev-parse", "HEAD")
    remote = tmp_path / "toy-remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "--set-upstream", "origin", "main")
    active = tmp_path / "active-runtime"
    _git(tmp_path, "clone", "--branch", "main", str(remote), str(active))
    _git(active, "config", "user.name", "C50 Runtime")
    _git(active, "config", "user.email", "runtime@example.invalid")

    red = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_fix.py"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert red.returncode == 1
    _git(repo, "checkout", "-b", "codex/repair")
    fix_path.write_text(
        "def calculate(value):\n    return value + 1\n", encoding="utf-8"
    )
    green = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_fix.py"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert green.returncode == 0
    code_sha256 = hashlib.sha256(fix_path.read_bytes()).hexdigest()
    patch_assessment = RepairController().assess_patch(
        changed_paths=("automation/inago/maintenance/fix.py",),
        owned_paths=("automation/inago/maintenance/fix.py",),
        baseline_assertions={"tests/test_fix.py": {"expected": "calculate(0) == 1"}},
        candidate_assertions={"tests/test_fix.py": {"expected": "calculate(0) == 1"}},
    )
    review_controller = RepairController(
        identity_resolver=lambda identity: (
            "implementer" if identity == "repair-author-c50" else "read_only_reviewer"
        )
    )
    repair_review = RepairReview(
        author_id="repair-author-c50",
        reviewer_id="repair-reviewer-c50",
        tested_result_version="repair-c50-v1",
        reviewed_result_version="repair-c50-v1",
        tested_code_sha256=code_sha256,
        reviewed_code_sha256=code_sha256,
    )
    assert patch_assessment.accepted is True
    assert review_controller.validate_review(repair_review) is True
    repair_commit = commit_owned_paths(
        repo,
        ("automation/inago/maintenance/fix.py",),
        message="fix: correct synthetic daily result calculation",
    )
    _git(repo, "push", "origin", f"{repair_commit}:refs/heads/codex/repair")

    release_id = "release-c50-toy"
    result_version = "release-c50-v1"
    store.create_release(
        release_id=release_id,
        author_id="repair-author-c50",
        base_commit=base_commit,
        result_commit=repair_commit,
        result_version=result_version,
        config_version="config-c50-v1",
        required_case_ids=("case-c50-toy",),
        created_at=BASE_TIME + timedelta(minutes=30),
    )
    store.record_test_result(
        release_id=release_id,
        case_id="case-c50-toy",
        attempt=1,
        result_version=result_version,
        status="PASS",
        evidence_ref="evidence/C50/toy-pytest.json",
        recorded_at=BASE_TIME + timedelta(minutes=31),
    )
    store.record_release_review(
        release_id=release_id,
        reviewer_id="release-reviewer-c50",
        reviewed_version=result_version,
        status="approved",
        reviewed_at=BASE_TIME + timedelta(minutes=32),
    )

    class Runtime:
        def current_commit(self):
            return _git(active, "rev-parse", "HEAD")

        def activate(self, commit, checkout=None):
            _git(active, "fetch", "origin", "main")
            _git(active, "checkout", "--detach", commit)
            assert checkout is None or _git(checkout, "rev-parse", "HEAD") == commit

        def healthy(self, expected_commit):
            return self.current_commit() == expected_commit

    class Guard:
        held = False

        def acquire(self):
            if self.held:
                return False
            self.held = True
            return True

        def release(self):
            self.held = False

    deployment = DeploymentCoordinator(
        store=store,
        repository=repo,
        worktree_root=tmp_path / "managed-worktrees",
        daily_active_probe=lambda: False,
        pending_probe=lambda: PendingSnapshot(0, "pending-c50"),
        runtime_switcher=Runtime(),
        exclusion_guard=Guard(),
        clock=lambda: BASE_TIME + timedelta(hours=2),
    ).deploy(
        release_id=release_id,
        tested_version=result_version,
        reviewed_version=result_version,
        remote_name="origin",
        target_branch="main",
        active_checkout=active,
        expected_target_commit=base_commit,
        owner_id="deployer-c50",
    )
    assert deployment.status == "deployed"
    assert deployment.tested_commit == deployment.pushed_commit
    assert deployment.pushed_commit == deployment.active_commit == repair_commit

    identity = {
        "incident_id": plan_context["incident_id"],
        "source_launch_id": plan_context["source_launch_id"],
        "source_run_id": plan_context["source_run_id"],
        "target_date": plan_context["target_date"],
        "job_id": plan_context["job_id"],
    }
    refs = {
        "daily": "evidence/C50/daily-failure.json",
        "investigation": "evidence/C50/investigation.json",
        "plan": "evidence/C50/plan.json",
        "tests": "evidence/C50/toy-pytest.json",
        "deployment": "evidence/C50/deployment.json",
        "index": "evidence/C50/index.md",
    }
    job_context = _job_context()
    job_context.update(identity)
    job_context["attempt_id"] = "attempt-c50-report"
    job_context["base_commit"] = base_commit
    job_context["input_refs"] = [refs["daily"]]
    job_context["evidence_refs"] = list(refs.values())
    job_context["allowed_outputs"] = [
        f"{identity['job_id']}/attempt-c50-report/report.html",
        f"{identity['job_id']}/attempt-c50-report/result.json",
    ]
    sources = _source_snapshot()
    sources["next_day"] = {
        "status": "pending",
        "observed_at": None,
        "evidence_href": None,
    }
    sources["identity"] = deepcopy(identity)
    sources["investigation"].update(
        {"result_version": "investigation-v1", "href": refs["investigation"]}
    )
    sources["plan"].update({"result_version": PLAN_VERSION, "href": refs["plan"]})
    sources["implementation"].update(
        {
            "base_commit": base_commit,
            "result_commit": repair_commit,
        }
    )
    sources["tests"].update(
        {
            "status": "passed",
            "result_version": result_version,
            "summary": "通常のpytestで失敗1回・修正後PASSを確認しました。",
            "evidence_href": refs["tests"],
        }
    )
    sources["deployment"].update(
        {
            "status": "deployed",
            "tested_commit": deployment.tested_commit,
            "pushed_commit": deployment.pushed_commit,
            "active_commit": deployment.active_commit,
            "evidence_href": refs["deployment"],
        }
    )
    sources["evidence_index_href"] = refs["index"]
    response = _report_response()
    response["identity"] = deepcopy(identity)
    response["result_version"] = "report-c50-v1"
    response["created_at_jst"] = "2026-10-18T15:00:00+09:00"
    response["issues"][0]["evidence_ref"] = refs["daily"]
    response["checks"][0]["evidence_ref"] = refs["tests"]
    template = (
        Path(__file__).resolve().parents[1]
        / "docs/templates/incidents/日次不具合対応レポート_ひな形.html"
    )
    report = prepare_incident_report(
        job_context, sources, response, template_path=template
    )
    report_review = {
        "schema_version": "maintenance-report-review-v1",
        "reviewer_id": "report-reviewer-c50",
        "reviewed_version": "report-c50-v1",
        "reviewed_sha256": report.html_sha256,
        "status": "approved",
        "reviewed_at": "2026-10-18T15:01:00+09:00",
        "findings": [],
    }
    reviewed_report = finalize_report_review(
        report, report_review, author_id="report-author-c50"
    )
    report_repo = tmp_path / "report-repository"
    _git(tmp_path, "clone", "--branch", "main", str(remote), str(report_repo))
    _git(report_repo, "config", "user.name", "C50 Report")
    _git(report_repo, "config", "user.email", "report@example.invalid")
    report_relative_path = (
        f"reports/incidents/{identity['target_date']}-{identity['incident_id']}.html"
    )
    report_file = report_repo / Path(*report_relative_path.split("/"))
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_bytes(reviewed_report.html.encode("utf-8"))
    publication = publish_reviewed_report(
        report_repo,
        report_path=report_relative_path,
        expected_target_commit=repair_commit,
        source_code_commit=repair_commit,
        result_version="report-c50-v1",
        review_document=report_review,
        author_id="report-author-c50",
        remote_name="origin",
        target_branch="main",
        commit_message="docs: publish synthetic C50 incident report",
    )

    assert plan_outcome.review_status == "approved"
    assert repair_attempt.attempt_no == 1
    assert repair_attempt.status == "running"
    assert publication.code_commit == repair_commit
    assert _git(
        repo, "diff-tree", "--no-commit-id", "--name-only", "-r", repair_commit
    ) == ("automation/inago/maintenance/fix.py")
    assert (
        _git(
            report_repo,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            publication.commit,
        )
        == report_relative_path
    )
    assert (
        len(
            {
                "implementation-agent",
                "planner-author-c50",
                "repair-author-c50",
                "report-author-c50",
            }
        )
        == 4
    )
