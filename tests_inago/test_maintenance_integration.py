"""Isolated U18 end-to-end checks over the maintenance control core."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
import json
import sqlite3
from pathlib import Path

from automation.inago.maintenance.controller import MaintenanceController
from automation.inago.app_server import (
    AppServerError,
    AppServerSession,
)
from automation.inago.maintenance.ai_adapter import (
    MaintenanceAIBlockedError,
    MaintenanceAISession,
)
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
    WorkerWorkflowOutcome,
    dispatch_ready_worker,
    run_worker_once,
)
from automation.inago.maintenance.verification import report_source


def test_model_schema_rejection_is_sent_strictly_and_journaled_safely(tmp_path):
    identity = {
        "job_id": "job-schema-failure",
        "incident_id": "incident-schema-failure",
        "source_launch_id": "launch-schema-failure",
        "source_run_id": None,
        "target_date": "2026-10-17",
    }
    manifest = type(
        "Manifest",
        (),
        {
            **identity,
            "job_id": identity["job_id"],
            "identity": lambda self: identity,
        },
    )()
    plan = type(
        "Plan",
        (),
        {
            "manifest": manifest,
            "requested_model": "gpt-6-luna",
            "requested_effort": "high",
            "requested_service_tier": "priority",
            "session_key": "session-schema-failure",
            "attempt_id": "attempt-schema-failure",
        },
    )()
    store = type(
        "Store", (), {"database_path": tmp_path / "runtime" / "maintenance" / "state.sqlite"}
    )()
    controller = type(
        "Controller",
        (),
        {
            "_diagnostic_trace": None,
            "_store": store,
            "get_job_input_manifest": lambda self, *, job_id: manifest,
            "record_ai_session_observation": lambda self, **_kwargs: None,
        },
    )()
    class RejectedRPC(AppServerSession):
        def __init__(self):
            self._next_id = 0
            self.process = None

        def _send(self, _message):
            return None

        def _log(self, *_args, **_kwargs):
            return None

        def _receive(self, _deadline, _cancel_event=None):
            return {
                "id": 1,
                "error": {
                    "code": -32600,
                    "message": "PRIVATE_PROVIDER_BODY_SENTINEL",
                    "data": {
                        "error": {
                            "type": "invalid_request_error",
                            "code": "invalid_json_schema",
                            "param": "text.format.schema.unevaluatedProperties",
                        }
                    },
                },
            }

    try:
        RejectedRPC()._call("turn/start", {}, deadline=1)
    except AppServerError as exc:
        rejected = exc
    else:
        raise AssertionError("synthetic provider rejection must raise AppServerError")

    class FakeAppServer:
        sent_schema = None

        def run_turn(self, prompt, output_schema, **_kwargs):
            self.sent_schema = output_schema
            assert "PRIVATE_PROMPT_SENTINEL" in prompt
            raise rejected

    class TestSession(MaintenanceAISession):
        def _validate_allowed_outputs(self, _outputs):
            return ()

        def _revalidate_write_roots(self):
            return None

        def _validate_turn_cwd(self, _cwd):
            return tmp_path

    app_server = FakeAppServer()
    session = TestSession(controller, plan, app_server)
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "automation"
            / "inago"
            / "schemas"
            / "maintenance-planning-response.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema["unevaluatedProperties"] is False

    try:
        session.run_turn(
            "PRIVATE_PROMPT_SENTINEL",
            schema,
            allowed_outputs=(),
            deadline=9999999999,
        )
    except MaintenanceAIBlockedError as exc:
        assert exc.reason_code == "ai_transport_failed"
    else:
        raise AssertionError("synthetic provider schema rejection must be recorded")

    assert app_server.sent_schema is not None
    pending = [app_server.sent_schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            assert "unevaluatedProperties" not in node
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    assert app_server.sent_schema["additionalProperties"] is False
    assert rejected.provider_error_type == "invalid_request_error"
    assert rejected.provider_error_code == "invalid_json_schema"
    assert rejected.provider_error_param == "text.format.schema.unevaluatedProperties"
    assert "unevaluatedProperties" in schema
    local_schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "unevaluatedProperties": False,
    }
    validator = session._prepare_output_schema(local_schema)
    try:
        validator.validate({**identity, "answer": "ok", "extra": "rejected locally"})
    except Exception:
        pass
    else:
        raise AssertionError("the original local validator must still reject extra fields")

    from automation.inago.maintenance.paths import (
        resolve_maintenance_failure_journal_path,
    )

    journal = resolve_maintenance_failure_journal_path(tmp_path / "runtime")
    journal_text = journal.read_text(encoding="utf-8")
    event = next(
        item
        for item in map(json.loads, journal_text.splitlines())
        if item.get("operation") == "ai_transport.run_turn"
    )
    assert event["provider_error_type"] == "invalid_request_error"
    assert event["provider_error_code"] == "invalid_json_schema"
    assert event["provider_error_param"] == "text.format.schema.unevaluatedProperties"
    assert event["exception_chain"] == [
        "AppServerError",
        "AppServerProviderRejection",
    ]
    assert "PRIVATE_PROMPT_SENTINEL" not in journal_text
    assert "PRIVATE_PROVIDER_BODY_SENTINEL" not in journal_text


def test_failure_journal_primary_failure_uses_independent_spool_and_replays_once(
    tmp_path, monkeypatch
):
    from automation.inago.maintenance import failure_journal
    from automation.inago.maintenance.paths import (
        resolve_maintenance_failure_journal_path,
    )

    data_root = tmp_path / "runtime"
    primary = resolve_maintenance_failure_journal_path(data_root)
    fallback = primary.parent.parent / "failure-journal-secondary" / primary.name
    original_open = failure_journal.os.open

    def fail_primary_open(path, flags, mode=0o777):
        if Path(path) == primary:
            raise OSError(28, "synthetic primary disk full")
        return original_open(path, flags, mode)

    monkeypatch.setattr(failure_journal.os, "open", fail_primary_open)
    result = failure_journal.append_failure_event(
        data_root,
        phase="failed",
        operation="integration.injected_primary_journal_failure",
        correlation_id="corr-primary-failure",
        reason_code="synthetic_primary_failure",
        capture_location="test_failure_journal_primary_failure_uses_independent_spool_and_replays_once",
    )
    assert result["status"] == "written"
    assert result["primary_status"] == "unavailable"
    assert result["fallback_status"] == "written"
    assert fallback.exists()
    event = json.loads(fallback.read_text(encoding="utf-8").splitlines()[0])
    assert event["event_id"] == result["event_id"]
    assert event["primary_sink_failure"]["exception_chain"] == ["OSError"]
    assert event["primary_sink_failure"]["errno"] == 28

    failure_journal.append_failure_event(
        data_root,
        phase="begin",
        operation="integration.interrupted_operation",
        correlation_id="corr-fallback-open",
    )
    reconciled = failure_journal.reconcile_open_operations(data_root)
    assert reconciled["reconciled"] == 1
    fallback_rows = [
        json.loads(line) for line in fallback.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        row.get("correlation_id") == "corr-fallback-open"
        and row.get("phase") == "interruption_observed"
        for row in fallback_rows
    )

    monkeypatch.setattr(failure_journal.os, "open", original_open)
    primary_result = failure_journal.append_failure_event(
        data_root,
        phase="end",
        operation="integration.primary_source_event",
        correlation_id="corr-primary-source",
    )
    assert primary_result["primary_status"] == "written"
    primary_rows = [
        json.loads(line) for line in primary.read_text(encoding="utf-8").splitlines()
    ]
    store = make_store(tmp_path / "replay-db")
    first = failure_journal.replay_failure_journal(data_root, store)
    second = failure_journal.replay_failure_journal(data_root, store)
    assert first["status"] == second["status"] == "complete"
    expected_event_ids = {
        row["event_id"] for row in (*fallback_rows, *primary_rows)
    }
    with store._connect() as connection:
        stored_event_ids = {
            row[0]
            for row in connection.execute(
                "SELECT event_id FROM maintenance_operation_event"
            ).fetchall()
        }
    assert stored_event_ids == expected_event_ids
    assert result["event_id"] in stored_event_ids


def test_failure_journal_replays_legacy_leading_underscore_capture_location(
    tmp_path,
):
    from automation.inago.maintenance import failure_journal

    data_root = tmp_path / "runtime"
    result = failure_journal.append_failure_event(
        data_root,
        phase="failed",
        operation="integration.legacy_capture_location",
        correlation_id="corr-legacy-capture-location",
        reason_code="synthetic_legacy_capture_location",
        capture_location="_persist_minimal_daily_report",
    )
    store = make_store(tmp_path / "replay-db")

    replay = failure_journal.replay_failure_journal(data_root, store)
    replay_again = failure_journal.replay_failure_journal(data_root, store)

    assert replay == {
        "status": "complete",
        "replayed": 1,
        "unknown": 0,
        "source_errors": [],
    }
    assert replay_again == {
        "status": "complete",
        "replayed": 1,
        "unknown": 0,
        "source_errors": [],
    }
    with store._connect() as connection:
        stored = connection.execute(
            "SELECT capture_location FROM maintenance_operation_event WHERE event_id = ?",
            (result["event_id"],),
        ).fetchone()
    assert stored is not None
    assert stored[0] == "symbol:_persist_minimal_daily_report"


def test_failure_journal_dual_sink_failure_does_not_gate_tracked_operation(
    tmp_path, monkeypatch
):
    from automation.inago.maintenance import failure_journal

    def fail_all_opens(*_args, **_kwargs):
        raise OSError(28, "synthetic both journal sinks full")

    monkeypatch.setattr(failure_journal.os, "open", fail_all_opens)
    entered = []
    with failure_journal.tracked_operation(
        tmp_path / "runtime", operation="integration.dual_sink_failure"
    ):
        entered.append("business operation ran")
    assert entered == ["business operation ran"]


def test_failure_journal_primary_resolution_failure_keeps_secondary_replay_available(
    tmp_path, monkeypatch
):
    from automation.inago.maintenance import failure_journal
    from automation.inago.maintenance.paths import resolve_maintenance_database_path

    data_root = tmp_path / "runtime"
    fallback = (
        resolve_maintenance_database_path(data_root).parent
        / "failure-journal-secondary"
        / "events.jsonl"
    )

    def fail_primary_resolution(*_args, **_kwargs):
        raise ValueError("synthetic primary path reparse point")

    monkeypatch.setattr(
        failure_journal,
        "resolve_maintenance_failure_journal_path",
        fail_primary_resolution,
    )
    result = failure_journal.append_failure_event(
        data_root,
        phase="failed",
        operation="integration.primary_path_resolution_failure",
        correlation_id="corr-primary-resolution-failure",
        reason_code="synthetic_primary_path_failure",
    )
    assert result["status"] == "written"
    assert result["primary_status"] == "unavailable"
    assert result["fallback_status"] == "written"
    assert fallback.is_file()

    store = make_store(tmp_path / "replay-db")
    replay = failure_journal.replay_failure_journal(data_root, store)
    assert replay["replayed"] == 1
    assert replay["unknown"] == 1
    with store._connect() as connection:
        event_ids = {
            row[0]
            for row in connection.execute(
                "SELECT event_id FROM maintenance_operation_event"
            ).fetchall()
        }
    assert result["event_id"] in event_ids


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

    assert complete.status == "maintenance_incomplete"
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

    result = run_worker_once(
        store,
        now=BASE_TIME + timedelta(minutes=3),
        owner_id="worker-blocked-c59",
        owner_pid=9322,
        owner_start_time=(BASE_TIME + timedelta(minutes=3)).isoformat(),
        process_probe=lambda _pid: ProcessObservation("stopped", None),
        source_launch_id=launch.launch_id,
    )
    queue = store.get_worker_queue(launch.launch_id)
    assert result.status == "maintenance_incomplete"
    assert result.maintenance_resolution_status == "unknown"
    assert result.reason_code == "maintenance_workflow_failed"
    assert queue is not None
    assert queue.status == "complete"
    assert queue.reason_code is None
    assert queue.check_id == result.check_id
    events = store.list_worker_queue_events(queue.queue_id)
    assert all(event.event_type != "blocked" for event in events)


def test_workflow_block_status_does_not_block_worker_path(tmp_path):
    store = make_store(tmp_path)

    def failed_daily_process(_command, _cwd, on_started):
        on_started(9331)
        return DailyProcessResult(
            returncode=4,
            stdout=b'{"status":"runtime_error","reason":"ExampleError"}',
            stderr=b"",
            pid=9331,
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

    def workflow_runner(_launch, check, _queue, _lease, _cancel_event):
        return WorkerWorkflowOutcome(
            status="blocked",
            check_id=check.check_id,
            reason_code="investigation_inputs_incomplete",
        )

    result = run_worker_once(
        store,
        now=BASE_TIME + timedelta(minutes=3),
        owner_id="worker-record-blocked-stage",
        owner_pid=9332,
        owner_start_time=(BASE_TIME + timedelta(minutes=3)).isoformat(),
        process_probe=lambda _pid: ProcessObservation("stopped", None),
        source_launch_id=launch.launch_id,
        workflow_runner=workflow_runner,
    )

    queue = store.get_worker_queue(launch.launch_id)
    assert result.status == "maintenance_incomplete"
    assert result.maintenance_resolution_status == "blocked"
    assert result.reason_code == "investigation_inputs_incomplete"
    assert queue is not None and queue.status == "complete"


def test_legacy_enqueue_historical_cli_is_removed(tmp_path, capsys):
    data_root = tmp_path / "maintenance-data"
    result = maintenance_cli_main(
        ["--data-root", str(data_root), "enqueue-historical"]
    )

    assert result == 2
    assert "invalid choice" in capsys.readouterr().err
    assert not (data_root / "maintenance" / "state.sqlite").exists()


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


def _skill_chain_provider_fixture(tmp_path, monkeypatch, *, exit_code=0, finish_error=None):
    from types import SimpleNamespace
    from automation.inago.maintenance import skill_chain_runtime

    events = []
    sessions = []

    class Process:
        def __init__(self, code):
            self.returncode = code
            self.waited = False

        def wait(self, timeout=None):
            self.waited = True
            return self.returncode

    class Session:
        def __init__(self, **kwargs):
            self.role_name = kwargs["role_name"]
            self.process_id = 4100 + len(sessions)
            self.process = Process(exit_code)
            self.safe_stderr_output = []
            self.closed = False
            sessions.append(self)

        def run_turn(self, *_args, **_kwargs):
            return SimpleNamespace(
                final_text="",
                thread_id=f"thread-{self.role_name}",
                turn_id=f"turn-{self.role_name}",
                events=({"method": "turn/completed", "params": {"turn": {"status": "completed"}}},),
            )

        def close(self):
            self.closed = True
            self.process.wait()

    monkeypatch.setattr(skill_chain_runtime, "_codex", lambda: "codex-fixture")
    monkeypatch.setattr(skill_chain_runtime, "_session", lambda _root, _job, role, _factory: Session(role_name=role.name))

    job = SimpleNamespace(job_id="job-c18-fixture", status="queued")

    class Store:
        database_path = (
            tmp_path / "repo" / "rakuten-room-inago" / "runtime"
            / "maintenance" / "state.sqlite"
        )

        def ensure_skill_chain_job_for_launch(self, **kwargs):
            events.append(("intake", kwargs))
            return job

        def finalize_job(self, **kwargs):
            events.append(("finalize", kwargs))
            if finish_error is not None:
                raise finish_error

    store = Store()
    provider = skill_chain_runtime.build_skill_chain_workflow_provider(
        store, repository_root=tmp_path / "repo"
    )
    launch = SimpleNamespace(
        source_launch_id="launch-c18-fixture",
        source_run_id=None,
        target_date="2026-10-20",
        status="failed",
        exit_code=4,
    )
    check = SimpleNamespace(check_id="check-c18-fixture")
    queue = SimpleNamespace(queue_id="queue-c18-fixture")
    return provider, store, launch, check, queue, sessions, events


def test_queued_job_finishes_after_workers_exit_without_prior_db_complete(
    tmp_path, monkeypatch
):
    from automation.inago.maintenance.worker import WorkerWorkflowOutcome

    provider, store, launch, check, queue, sessions, events = _skill_chain_provider_fixture(
        tmp_path, monkeypatch
    )
    outcome = provider(launch, check, queue, object(), None)
    assert isinstance(outcome, WorkerWorkflowOutcome)
    assert outcome.status == "complete"
    assert events[0][0] == "intake"
    assert events[-1][0] == "finalize"
    assert len(sessions) == 7 and all(session.closed and session.process.waited for session in sessions)
    assert store.database_path.parent.parent.joinpath(
        "maintenance-flow", "job-c18-fixture", "calls.jsonl"
    ).is_file()


def test_db_finish_failure_keeps_actual_worker_exit_record(tmp_path, monkeypatch):
    from automation.inago.maintenance import skill_chain_runtime
    from automation.inago.maintenance.worker import WorkerWorkflowOutcome

    provider, _store, launch, check, queue, sessions, _events = _skill_chain_provider_fixture(
        tmp_path, monkeypatch, exit_code=23, finish_error=OSError("synthetic database finish failure")
    )
    outcome = provider(launch, check, queue, object(), None)
    assert isinstance(outcome, WorkerWorkflowOutcome)
    assert outcome.status == "complete"
    ledger = (
        _store.database_path.parent.parent
        / "maintenance-flow"
        / "job-c18-fixture"
        / "calls.jsonl"
    )
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    role_records = [record for record in records if record.get("event") == "worker_finished"]
    assert len(role_records) == 7
    assert role_records[0]["process_returncode"] == 23
    assert all(session.closed and session.process.waited for session in sessions)
    finalization = next(record for record in records if record.get("event") == "job_finalization_observed")
    assert finalization["turn_status"] == "not_recorded:OSError"
