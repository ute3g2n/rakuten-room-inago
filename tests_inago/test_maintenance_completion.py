"""Isolated contracts for the daily launcher and final completion notice."""

from __future__ import annotations

import json
import io
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

from automation.inago.maintenance.daily_launcher import (
    DailyProcessResult,
    DailyLauncherError,
    main as daily_launcher_main,
    run_daily_process,
    run_daily_launch,
)
from automation.inago.maintenance.store import MaintenanceStoreError
from tests_inago.maintenance_support import make_store


JST = timezone(timedelta(hours=9))
START = datetime(2026, 10, 17, 13, 0, tzinfo=JST)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _json(value: dict[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _launch(store, runner, *, now=START):
    data_root = store.database_path.parent.parent
    return run_daily_launch(
        store,
        data_root=data_root,
        config_path=data_root / "config" / "settings.json",
        now=now,
        process_runner=runner,
        command=("python", "-B", "-m", "automation.inago.cli"),
        cwd=PROJECT_ROOT,
    )


def _record(store, outcome):
    return store.get_daily_launch(outcome.launch_id)


def _daily_check(store, launch_id):
    connection = store._connect()
    try:
        return connection.execute(
            "SELECT source_launch_id, source_run_id, target_date, check_status, "
            "reason_code FROM daily_check WHERE source_launch_id = ?",
            (launch_id,),
        ).fetchone()
    finally:
        connection.close()


def test_c08_contract(tmp_path):
    store = make_store(tmp_path)
    events = []
    original_start = store.record_daily_launch_start

    def record_start(**kwargs):
        result = original_start(**kwargs)
        events.append("start_saved")
        return result

    store.record_daily_launch_start = record_start

    def preflight_failure(command, cwd, on_started):
        assert command[-1] == "automation.inago.cli"
        assert cwd == PROJECT_ROOT
        assert store.counts()["daily_launch"] == 1
        on_started(8123)
        events.append("child_started")
        events.append("child_finished")
        return DailyProcessResult(
            returncode=4,
            stdout=_json(
                {
                    "status": "blocked",
                    "phase": "preflight",
                    "reason": "startup_preflight_blocked",
                    "target_date": "2026-10-17",
                    "failed_checks": ["runtime_setup_missing"],
                }
            ),
            stderr=b"",
            pid=8123,
        )

    outcome = _launch(store, preflight_failure)
    record = _record(store, outcome)
    check = _daily_check(store, outcome.launch_id)

    assert events == ["start_saved", "child_started", "child_finished"]
    assert record.child_pid == 8123
    assert outcome.exit_code == 4
    assert record.status == "blocked"
    assert record.source_run_id is None
    assert record.failure_phase == "preflight"
    assert json.loads(record.result_json)["failed_checks"] == ["runtime_setup_missing"]
    assert check["source_launch_id"] == outcome.launch_id
    assert check["source_run_id"] is None
    assert check["check_status"] == "incomplete"
    assert store.counts()["daily_check"] == 1


def test_c09_contract(tmp_path):
    store = make_store(tmp_path)
    sensitive_detail = "secret-config-path-and-sql-text"

    def run_integrity_error(command, cwd, on_started):
        on_started(8124)
        return DailyProcessResult(
            returncode=4,
            stdout=b"",
            stderr=_json(
                {
                    "status": "runtime_error",
                    "reason": "IntegrityError",
                    "detail": sensitive_detail,
                }
            ),
            pid=8124,
        )

    outcome = _launch(store, run_integrity_error)
    record = _record(store, outcome)
    check = _daily_check(store, outcome.launch_id)
    summary = json.loads(record.result_json)

    assert outcome.exit_code == 4
    assert record.status == "failed"
    assert record.primary_error_class == "IntegrityError"
    assert record.source_run_id is None
    assert record.trace_status == "not_written"
    assert check["source_run_id"] is None
    assert check["check_status"] == "incomplete"
    assert sensitive_detail not in json.dumps(summary)
    assert sensitive_detail not in outcome.stderr_summary


def test_c10_contract(tmp_path):
    store = make_store(tmp_path)
    draft_sentinel = tmp_path / "draft-sentinel.txt"
    draft_sentinel.write_text("existing draft stays", encoding="utf-8")

    def export_failure(command, cwd, on_started):
        on_started(8125)
        return DailyProcessResult(
            returncode=4,
            stdout=_json(
                {
                    "status": "export_failed",
                    "overall_status": "export_failed",
                    "daily_status": "complete",
                    "target_date": "2026-10-17",
                    "run_id": "run-c10",
                    "primary_error": {
                        "phase": "export",
                        "code": "DashboardExportError",
                    },
                    "export": {
                        "status": "failed",
                        "draft_count": 12,
                        "path": "private/path/must-not-be-copied",
                    },
                    "pipeline_trace": {"status": "healthy", "event_count": 6},
                }
            ),
            stderr=b"",
            pid=8125,
        )

    outcome = _launch(store, export_failure)
    record = _record(store, outcome)
    check = _daily_check(store, outcome.launch_id)
    summary = json.loads(record.result_json)

    assert outcome.exit_code == 4
    assert record.status == "failed"
    assert record.source_run_id == "run-c10"
    assert record.primary_error_class == "DashboardExportError"
    assert record.trace_status == "healthy"
    assert check["check_status"] == "incomplete"
    assert draft_sentinel.read_text(encoding="utf-8") == "existing draft stays"
    assert "private/path/must-not-be-copied" not in json.dumps(summary)
    assert "draft_count" not in summary


def test_c11_contract(tmp_path):
    store = make_store(tmp_path)
    completion_saved = []
    original_complete = store.record_daily_launch_completion

    def record_complete(**kwargs):
        completion_saved.append("after_child_return")
        return original_complete(**kwargs)

    store.record_daily_launch_completion = record_complete

    def close_and_backup_failure(command, cwd, on_started):
        on_started(8126)
        return DailyProcessResult(
            returncode=4,
            stdout=_json(
                {
                    "status": "failed",
                    "overall_status": "failed",
                    "daily_status": "failed",
                    "target_date": "2026-10-17",
                    "run_id": None,
                    "primary_error": {"phase": "daily", "code": "IntegrityError"},
                    "secondary_errors": [
                        {"phase": "database_close", "code": "DatabaseCloseError"},
                        {"phase": "preservation", "code": "BackupError"},
                    ],
                    "pipeline_trace": {"status": "not_written", "event_count": 0},
                }
            ),
            stderr=b"",
            pid=8126,
        )

    outcome = _launch(store, close_and_backup_failure)
    record = _record(store, outcome)

    assert completion_saved == ["after_child_return"]
    assert record.primary_error_class == "IntegrityError"
    assert record.secondary_error_classes == (
        "DatabaseCloseError",
        "BackupError",
    )
    assert record.status == "failed"
    assert store.counts()["daily_check"] == 1


def test_notification_save_failure_keeps_start_and_daily_exit_code(tmp_path):
    store = make_store(tmp_path)

    def fail_completion(**kwargs):
        raise MaintenanceStoreError("daily_launch_completion_unavailable")

    store.record_daily_launch_completion = fail_completion

    def successful_daily(command, cwd, on_started):
        on_started(8127)
        return DailyProcessResult(
            returncode=0,
            stdout=_json(
                {
                    "status": "complete",
                    "overall_status": "complete",
                    "daily_status": "complete",
                    "target_date": "2026-10-17",
                    "run_id": "run-complete",
                    "pipeline_trace": {"status": "healthy", "event_count": 4},
                }
            ),
            stderr=b"",
            pid=8127,
        )

    outcome = _launch(store, successful_daily)

    assert outcome.exit_code == 0
    assert outcome.notification_status == "unavailable"
    assert _record(store, outcome).status == "running"
    assert store.counts()["daily_check"] == 0


def test_c48_contract(tmp_path):
    store = make_store(tmp_path)
    calls = []

    def after_hours_daily(command, cwd, on_started):
        calls.append((command, cwd))
        on_started(8128)
        return DailyProcessResult(
            returncode=0,
            stdout=_json(
                {
                    "status": "complete",
                    "overall_status": "complete",
                    "daily_status": "complete",
                    "target_date": "2026-10-17",
                    "run_id": "run-after-hours",
                    "pipeline_trace": {"status": "healthy", "event_count": 4},
                }
            ),
            stderr=b"",
            pid=8128,
        )

    outcome = _launch(
        store,
        after_hours_daily,
        now=datetime(2026, 10, 17, 18, 1, tzinfo=JST),
    )

    assert len(calls) == 1
    assert outcome.exit_code == 0
    assert _record(store, outcome).status == "complete"

    task = ET.parse(
        PROJECT_ROOT / "automation" / "inago" / "task-xml" / "RakutenRoomInagoDaily.xml"
    ).getroot()
    namespace = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert (
        task.findtext("task:Settings/task:ExecutionTimeLimit", namespaces=namespace)
        == "PT0S"
    )
    arguments = task.findtext(
        "task:Actions/task:Exec/task:Arguments", namespaces=namespace
    )
    assert "automation.inago.maintenance.daily_launcher" in arguments
    run_script = (PROJECT_ROOT / "automation" / "inago" / "run.ps1").read_text(
        encoding="utf-8"
    )
    assert "automation.inago.maintenance.daily_launcher" in run_script
    assert "Register-ScheduledTask" not in run_script


def test_daily_child_pid_is_recorded_and_process_is_joined():
    started_pids = []
    result = run_daily_process(
        (
            sys.executable,
            "-c",
            "import sys; print('daily-result'); print('ignored', file=sys.stderr)",
        ),
        PROJECT_ROOT,
        started_pids.append,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [b"daily-result"]
    assert started_pids == [result.pid]


def test_child_pid_is_not_written_twice_after_callback(tmp_path):
    store = make_store(tmp_path)
    writes = []
    original_record_child = store.record_daily_launch_child

    def record_child(**kwargs):
        writes.append(kwargs["child_pid"])
        if len(writes) > 1:
            raise sqlite3.OperationalError("late write failed")
        return original_record_child(**kwargs)

    store.record_daily_launch_child = record_child

    def completed_daily(command, cwd, on_started):
        on_started(8129)
        return DailyProcessResult(
            returncode=5,
            stdout=_json(
                {
                    "status": "partial",
                    "overall_status": "partial",
                    "daily_status": "partial",
                    "target_date": "2026-10-17",
                    "run_id": "run-partial",
                }
            ),
            stderr=b"",
            pid=8129,
        )

    outcome = _launch(store, completed_daily)
    record = _record(store, outcome)

    assert writes == [8129]
    assert outcome.exit_code == 5
    assert record.status == "complete"
    assert record.child_pid == 8129


def test_sqlite_start_error_becomes_safe_launcher_error(tmp_path):
    store = make_store(tmp_path)

    def fail_start(**kwargs):
        raise sqlite3.OperationalError("database details are private")

    store.record_daily_launch_start = fail_start

    with pytest.raises(DailyLauncherError, match="daily_launch_start_unavailable"):
        _launch(store, lambda *args: pytest.fail("daily child must not start"))


def test_sqlite_completion_error_preserves_daily_exit_code(tmp_path):
    store = make_store(tmp_path)

    def fail_completion(**kwargs):
        raise sqlite3.OperationalError("database details are private")

    store.record_daily_launch_completion = fail_completion

    def shortfall_daily(command, cwd, on_started):
        on_started(8130)
        return DailyProcessResult(
            returncode=5,
            stdout=_json(
                {
                    "status": "partial",
                    "overall_status": "partial",
                    "daily_status": "partial",
                    "target_date": "2026-10-17",
                    "run_id": "run-partial",
                }
            ),
            stderr=b"",
            pid=8130,
        )

    outcome = _launch(store, shortfall_daily)

    assert outcome.exit_code == 5
    assert outcome.notification_status == "unavailable"
    assert _record(store, outcome).status == "running"
    assert store.counts()["daily_check"] == 0


def test_store_rejects_inconsistent_terminal_statuses(tmp_path):
    store = make_store(tmp_path)
    launch = store.record_daily_launch_start(
        source_launch_id="launch-inconsistent",
        target_date="2026-10-17",
        owner_pid=8131,
        started_at=START,
    )
    summary = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": launch.source_launch_id,
        "target_date": launch.target_date,
        "status": "complete",
        "exit_code": 0,
        "source_run_id": "run-inconsistent",
        "overall_status": "failed",
        "daily_status": "failed",
        "reason_code": "daily_runtime_error",
        "failure_phase": "daily",
        "failed_checks": [],
        "primary_error_class": "IntegrityError",
        "secondary_error_classes": [],
        "trace_status": "healthy",
    }

    with pytest.raises(MaintenanceStoreError):
        store.record_daily_launch_completion(
            source_launch_id=launch.source_launch_id,
            target_date=launch.target_date,
            child_pid=None,
            source_run_id="run-inconsistent",
            launch_status="complete",
            exit_code=0,
            completed_at=START + timedelta(minutes=5),
            check_status="incomplete",
            reason_code="daily_runtime_error",
            result_summary=summary,
        )


def test_store_rejects_blocked_launch_with_success_exit_code(tmp_path):
    store = make_store(tmp_path)
    launch = store.record_daily_launch_start(
        source_launch_id="launch-blocked-exit",
        target_date="2026-10-17",
        owner_pid=8132,
        started_at=START,
    )
    summary = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": launch.source_launch_id,
        "target_date": launch.target_date,
        "status": "blocked",
        "exit_code": 0,
        "source_run_id": None,
        "overall_status": "blocked",
        "daily_status": "unknown",
        "reason_code": "startup_preflight_blocked",
        "failure_phase": "preflight",
        "failed_checks": ["runtime_setup_missing"],
        "primary_error_class": None,
        "secondary_error_classes": [],
        "trace_status": "not_written",
    }

    with pytest.raises(MaintenanceStoreError):
        store.record_daily_launch_completion(
            source_launch_id=launch.source_launch_id,
            target_date=launch.target_date,
            child_pid=8133,
            source_run_id=None,
            launch_status="blocked",
            exit_code=0,
            completed_at=START + timedelta(minutes=5),
            check_status="incomplete",
            reason_code="startup_preflight_blocked",
            result_summary=summary,
        )


def _downgrade_fixture_to_v2(store):
    connection = sqlite3.connect(store.database_path)
    try:
        connection.execute(
            "INSERT INTO daily_check "
            "(check_id, source_launch_id, source_run_id, target_date, check_status, "
            "reason_code, observed_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "check-preserved",
                "launch-preserved",
                None,
                "2026-10-17",
                "incomplete",
                "daily_runtime_error",
                "2026-10-17T13:00:00+09:00",
                "2026-10-17T13:00:00+09:00",
            ),
        )
        connection.execute("DROP TABLE watchdog_queue")
        connection.execute("DROP TABLE daily_launch")
        connection.execute("DROP TABLE watchdog_cursor")
        connection.execute("DELETE FROM schema_migration WHERE version > 2")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    finally:
        connection.close()


def test_populated_v2_database_upgrades_to_v4_without_losing_checks(tmp_path):
    store = make_store(tmp_path)
    _downgrade_fixture_to_v2(store)

    assert store.initialize(applied_at=START + timedelta(minutes=1)) == 4
    assert store.counts()["daily_check"] == 1
    assert store.counts()["daily_launch"] == 0
    assert store.initialize(applied_at=START + timedelta(minutes=2)) == 4


def test_v3_daily_launch_migration_preserves_launch_and_marks_old_identity_unknown(
    tmp_path,
):
    store = make_store(tmp_path)
    connection = sqlite3.connect(store.database_path)
    try:
        connection.execute("DROP TABLE watchdog_queue")
        connection.execute("DROP TABLE daily_launch")
        connection.execute("DROP TABLE watchdog_cursor")
        migration = Path(__file__).resolve().parents[1] / (
            "automation/inago/maintenance/sql/003_daily_launch.sql"
        )
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO daily_launch "
            "(source_launch_id, target_date, owner_pid, started_at, status, child_pid) "
            "VALUES (?, ?, ?, ?, 'running', ?)",
            (
                "launch-v3-preserved",
                "2026-10-17",
                8301,
                "2026-10-17T04:00:00Z",
                8302,
            ),
        )
        connection.execute("DELETE FROM schema_migration WHERE version = 4")
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    finally:
        connection.close()

    assert store.initialize(applied_at=START + timedelta(minutes=1)) == 4
    launch = store.get_daily_launch("launch-v3-preserved")
    assert launch is not None
    assert launch.status == "running"
    assert launch.owner_start_time is None
    assert launch.child_start_time is None


def test_v4_migration_rolls_back_partial_ddl(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    connection = sqlite3.connect(store.database_path)
    try:
        connection.execute("DROP TABLE watchdog_queue")
        connection.execute("DROP TABLE daily_launch")
        connection.execute("DROP TABLE watchdog_cursor")
        migration = Path(__file__).resolve().parents[1] / (
            "automation/inago/maintenance/sql/003_daily_launch.sql"
        )
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO daily_launch "
            "(source_launch_id, target_date, owner_pid, started_at, status) "
            "VALUES (?, ?, ?, ?, 'running')",
            (
                "launch-v3-rollback",
                "2026-10-17",
                8303,
                "2026-10-17T04:00:00Z",
            ),
        )
        connection.execute("DELETE FROM schema_migration WHERE version = 4")
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    finally:
        connection.close()

    original_read_text = Path.read_text

    def broken_migration(path, *args, **kwargs):
        if path.name == "004_watchdog_confirmation_queue.sql":
            return original_read_text(path, *args, **kwargs) + "\nINVALID SQL;\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", broken_migration)
    with pytest.raises(sqlite3.OperationalError):
        store.initialize(applied_at=START + timedelta(minutes=1))

    connection = sqlite3.connect(store.database_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 4"
            ).fetchone()
            is None
        )
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(daily_launch)")
        }
        assert "owner_start_time" not in columns
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM daily_launch WHERE source_launch_id = ?",
                ("launch-v3-rollback",),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'watchdog_queue'"
            ).fetchone()
            is None
        )
    finally:
        connection.close()


def test_v3_migration_rolls_back_partial_ddl(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    _downgrade_fixture_to_v2(store)
    original_read_text = Path.read_text

    def broken_migration(path, *args, **kwargs):
        if path.name == "003_daily_launch.sql":
            return "CREATE TABLE daily_launch (source_launch_id TEXT);\nINVALID SQL;"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", broken_migration)

    with pytest.raises(sqlite3.OperationalError):
        store.initialize(applied_at=START + timedelta(minutes=1))

    connection = sqlite3.connect(store.database_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 3"
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'daily_launch'"
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT COUNT(*) FROM daily_check").fetchone()[0] == 1
    finally:
        connection.close()


def test_non_string_trace_status_does_not_interrupt_completion(tmp_path):
    store = make_store(tmp_path)

    def daily(command, cwd, on_started):
        on_started(8134)
        return DailyProcessResult(
            returncode=0,
            stdout=_json(
                {
                    "status": "complete",
                    "overall_status": "complete",
                    "daily_status": "complete",
                    "target_date": "2026-10-17",
                    "run_id": "run-trace-type",
                    "pipeline_trace": {"status": []},
                }
            ),
            stderr=b"",
            pid=8134,
        )

    outcome = _launch(store, daily)

    assert outcome.exit_code == 0
    assert outcome.notification_status == "saved"
    assert outcome.summary["trace_status"] == "not_written"
    assert _record(store, outcome).status == "complete"


@pytest.mark.parametrize(
    "identity_overrides,remove_fields",
    [
        ({}, {"target_date"}),
        ({}, {"run_id"}),
        ({"target_date": "2026-10-16"}, set()),
    ],
)
def test_success_result_requires_matching_target_and_run_identity(
    tmp_path, identity_overrides, remove_fields
):
    store = make_store(tmp_path)
    payload = {
        "status": "complete",
        "overall_status": "complete",
        "daily_status": "complete",
        "target_date": "2026-10-17",
        "run_id": "run-identity-required",
    }
    payload.update(identity_overrides)
    for field in remove_fields:
        payload.pop(field)

    def daily(command, cwd, on_started):
        on_started(8135)
        return DailyProcessResult(
            returncode=0,
            stdout=_json(payload),
            stderr=b"",
            pid=8135,
        )

    outcome = _launch(store, daily)

    assert outcome.exit_code == 4
    assert outcome.summary["status"] == "failed"
    assert _record(store, outcome).status == "failed"


def test_inconsistent_daily_summary_cannot_return_scheduler_success(tmp_path):
    store = make_store(tmp_path)

    def daily(command, cwd, on_started):
        on_started(8136)
        return DailyProcessResult(
            returncode=0,
            stdout=_json(
                {
                    "status": "complete",
                    "overall_status": "complete",
                    "daily_status": "failed",
                    "target_date": "2026-10-17",
                    "run_id": "run-inconsistent-child",
                }
            ),
            stderr=b"",
            pid=8136,
        )

    outcome = _launch(store, daily)

    assert outcome.exit_code == 4
    assert outcome.notification_status == "saved"
    assert outcome.summary["status"] == "failed"
    assert _record(store, outcome).status == "failed"


def test_missing_json_result_cannot_return_scheduler_success(tmp_path):
    store = make_store(tmp_path)

    def daily(command, cwd, on_started):
        on_started(8138)
        return DailyProcessResult(
            returncode=0,
            stdout=b"",
            stderr=b"",
            pid=8138,
        )

    outcome = _launch(store, daily)

    assert outcome.exit_code == 4
    assert outcome.summary["status"] == "failed"
    assert _record(store, outcome).status == "failed"


def test_failed_child_result_cannot_keep_complete_summary(tmp_path):
    store = make_store(tmp_path)

    def daily(command, cwd, on_started):
        on_started(8140)
        return DailyProcessResult(
            returncode=4,
            stdout=_json(
                {
                    "status": "complete",
                    "overall_status": "complete",
                    "daily_status": "complete",
                    "target_date": "2026-10-17",
                    "run_id": "run-failed-code-complete-summary",
                }
            ),
            stderr=b"",
            pid=8140,
        )

    outcome = _launch(store, daily)

    assert outcome.exit_code == 4
    assert outcome.summary["status"] == "failed"
    assert outcome.summary["overall_status"] == "failed"
    assert _record(store, outcome).status == "failed"


@pytest.mark.parametrize(
    "field,value",
    [("overall_status", None), ("daily_status", None)],
)
def test_store_rejects_missing_required_completion_statuses(tmp_path, field, value):
    store = make_store(tmp_path)
    launch = store.record_daily_launch_start(
        source_launch_id="launch-missing-status",
        target_date="2026-10-17",
        owner_pid=8137,
        started_at=START,
    )
    summary = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": launch.source_launch_id,
        "target_date": launch.target_date,
        "status": "failed",
        "exit_code": 4,
        "source_run_id": None,
        "overall_status": "failed",
        "daily_status": "failed",
        "reason_code": "daily_runtime_error",
        "failure_phase": "daily",
        "failed_checks": [],
        "primary_error_class": "RuntimeError",
        "secondary_error_classes": [],
        "trace_status": "not_written",
    }
    summary[field] = value

    with pytest.raises(MaintenanceStoreError):
        store.record_daily_launch_completion(
            source_launch_id=launch.source_launch_id,
            target_date=launch.target_date,
            child_pid=None,
            source_run_id=None,
            launch_status="failed",
            exit_code=4,
            completed_at=START + timedelta(minutes=5),
            check_status="incomplete",
            reason_code="daily_runtime_error",
            result_summary=summary,
        )


def test_store_rejects_non_string_trace_status_without_type_error(tmp_path):
    store = make_store(tmp_path)
    launch = store.record_daily_launch_start(
        source_launch_id="launch-invalid-trace-type",
        target_date="2026-10-17",
        owner_pid=8139,
        started_at=START,
    )
    summary = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": launch.source_launch_id,
        "target_date": launch.target_date,
        "status": "failed",
        "exit_code": 4,
        "source_run_id": None,
        "overall_status": "failed",
        "daily_status": "failed",
        "reason_code": "daily_runtime_error",
        "failure_phase": "daily",
        "failed_checks": [],
        "primary_error_class": "RuntimeError",
        "secondary_error_classes": [],
        "trace_status": [],
    }

    with pytest.raises(MaintenanceStoreError):
        store.record_daily_launch_completion(
            source_launch_id=launch.source_launch_id,
            target_date=launch.target_date,
            child_pid=None,
            source_run_id=None,
            launch_status="failed",
            exit_code=4,
            completed_at=START + timedelta(minutes=5),
            check_status="incomplete",
            reason_code="daily_runtime_error",
            result_summary=summary,
        )


@pytest.mark.parametrize(
    "overall_status,daily_status,exit_code,check_status,reason_code",
    [
        ("complete", "complete", 0, "normal", None),
        ("partial", "partial", 5, "normal_shortfall", "daily_target_unmet"),
    ],
)
def test_store_rejects_success_without_source_run_id(
    tmp_path, overall_status, daily_status, exit_code, check_status, reason_code
):
    store = make_store(tmp_path)
    launch = store.record_daily_launch_start(
        source_launch_id="launch-no-run-id-success",
        target_date="2026-10-17",
        owner_pid=8141,
        started_at=START,
    )
    summary = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": launch.source_launch_id,
        "target_date": launch.target_date,
        "status": "complete",
        "exit_code": exit_code,
        "source_run_id": None,
        "overall_status": overall_status,
        "daily_status": daily_status,
        "reason_code": reason_code,
        "failure_phase": None,
        "failed_checks": [],
        "primary_error_class": None,
        "secondary_error_classes": [],
        "trace_status": "healthy",
    }

    with pytest.raises(MaintenanceStoreError):
        store.record_daily_launch_completion(
            source_launch_id=launch.source_launch_id,
            target_date=launch.target_date,
            child_pid=None,
            source_run_id=None,
            launch_status="complete",
            exit_code=exit_code,
            completed_at=START + timedelta(minutes=5),
            check_status=check_status,
            reason_code=reason_code,
            result_summary=summary,
        )


def test_store_allows_not_due_completion_without_source_run_id(tmp_path):
    store = make_store(tmp_path)
    launch = store.record_daily_launch_start(
        source_launch_id="launch-not-due-no-run",
        target_date="2026-10-17",
        owner_pid=8142,
        started_at=START,
    )
    summary = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": launch.source_launch_id,
        "target_date": launch.target_date,
        "status": "complete",
        "exit_code": 0,
        "source_run_id": None,
        "overall_status": "not_due",
        "daily_status": "not_due",
        "reason_code": "daily_not_due",
        "failure_phase": None,
        "failed_checks": [],
        "primary_error_class": None,
        "secondary_error_classes": [],
        "trace_status": "not_written",
    }

    record = store.record_daily_launch_completion(
        source_launch_id=launch.source_launch_id,
        target_date=launch.target_date,
        child_pid=None,
        source_run_id=None,
        launch_status="complete",
        exit_code=0,
        completed_at=START + timedelta(minutes=5),
        check_status="unknown",
        reason_code="daily_not_due",
        result_summary=summary,
    )

    assert record.status == "complete"
    assert record.source_run_id is None


def test_store_rejects_failed_completion_with_successful_overall_status(tmp_path):
    store = make_store(tmp_path)
    launch = store.record_daily_launch_start(
        source_launch_id="launch-failed-but-complete-status",
        target_date="2026-10-17",
        owner_pid=8143,
        started_at=START,
    )
    summary = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": launch.source_launch_id,
        "target_date": launch.target_date,
        "status": "failed",
        "exit_code": 4,
        "source_run_id": "run-failed-but-complete-status",
        "overall_status": "complete",
        "daily_status": "complete",
        "reason_code": "daily_result_failed",
        "failure_phase": None,
        "failed_checks": [],
        "primary_error_class": None,
        "secondary_error_classes": [],
        "trace_status": "healthy",
    }

    with pytest.raises(MaintenanceStoreError):
        store.record_daily_launch_completion(
            source_launch_id=launch.source_launch_id,
            target_date=launch.target_date,
            child_pid=None,
            source_run_id="run-failed-but-complete-status",
            launch_status="failed",
            exit_code=4,
            completed_at=START + timedelta(minutes=5),
            check_status="incomplete",
            reason_code="daily_result_failed",
            result_summary=summary,
        )


def test_store_rejects_boolean_summary_exit_code(tmp_path):
    store = make_store(tmp_path)
    launch = store.record_daily_launch_start(
        source_launch_id="launch-bool-exit",
        target_date="2026-10-17",
        owner_pid=8144,
        started_at=START,
    )
    summary = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": launch.source_launch_id,
        "target_date": launch.target_date,
        "status": "failed",
        "exit_code": True,
        "source_run_id": None,
        "overall_status": "failed",
        "daily_status": "failed",
        "reason_code": "daily_runtime_error",
        "failure_phase": "daily",
        "failed_checks": [],
        "primary_error_class": "RuntimeError",
        "secondary_error_classes": [],
        "trace_status": "not_written",
    }

    with pytest.raises(MaintenanceStoreError):
        store.record_daily_launch_completion(
            source_launch_id=launch.source_launch_id,
            target_date=launch.target_date,
            child_pid=None,
            source_run_id=None,
            launch_status="failed",
            exit_code=1,
            completed_at=START + timedelta(minutes=5),
            check_status="incomplete",
            reason_code="daily_runtime_error",
            result_summary=summary,
        )


def test_main_reports_invalid_runtime_root_without_traceback(tmp_path, monkeypatch):
    invalid_root = tmp_path / "not-a-directory"
    invalid_root.write_text("file", encoding="utf-8")
    stderr = io.StringIO()
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stderr", stderr)
    monkeypatch.setattr("sys.stdout", stdout)

    exit_code = daily_launcher_main(
        ["--config", str(tmp_path / "config.json"), "--data-root", str(invalid_root)]
    )

    assert exit_code == 4
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue()) == {
        "status": "blocked",
        "reason_code": "data_root_not_directory",
    }


def _load_watchdog_module():
    import importlib

    try:
        return importlib.import_module("automation.inago.maintenance.watchdog")
    except ModuleNotFoundError as exc:
        pytest.fail(f"watchdog module unavailable: {exc.name}")


def test_c12_contract(tmp_path):
    watchdog = _load_watchdog_module()
    store = make_store(tmp_path)
    recorded_start = (START - timedelta(minutes=10)).isoformat()
    child_start = (START - timedelta(minutes=9)).isoformat()

    for launch_id, owner_pid in (
        ("launch-alive", 10101),
        ("launch-unknown", 10102),
        ("launch-reused", 10103),
        ("launch-stopped", 10104),
        ("launch-confirmed-child-stopped", 10105),
        ("launch-owner-stopped-child-alive", 10106),
    ):
        store.record_daily_launch_start(
            source_launch_id=launch_id,
            target_date="2026-10-17",
            owner_pid=owner_pid,
            owner_start_time=recorded_start,
            started_at=recorded_start,
        )

    store.record_daily_launch_child(
        source_launch_id="launch-alive",
        child_pid=20101,
        child_start_time=child_start,
    )
    store.record_daily_launch_child(
        source_launch_id="launch-unknown",
        child_pid=20102,
        child_start_time=child_start,
    )
    store.record_daily_launch_child(
        source_launch_id="launch-reused",
        child_pid=20103,
        child_start_time=child_start,
    )
    store.record_daily_launch_child(
        source_launch_id="launch-confirmed-child-stopped",
        child_pid=20105,
        child_start_time=child_start,
    )
    store.record_daily_launch_child(
        source_launch_id="launch-owner-stopped-child-alive",
        child_pid=20106,
        child_start_time=child_start,
    )

    def process_probe(pid):
        if pid == 10101:
            return watchdog.ProcessObservation("running", recorded_start)
        if pid == 10102:
            return watchdog.ProcessObservation("unknown", None)
        if pid == 10103:
            return watchdog.ProcessObservation("running", START.isoformat())
        if pid == 20101:
            return watchdog.ProcessObservation("running", child_start)
        if pid == 20102:
            return watchdog.ProcessObservation("stopped", None)
        if pid == 20103:
            return watchdog.ProcessObservation("stopped", None)
        if pid == 10104:
            return watchdog.ProcessObservation("stopped", None)
        if pid == 10105:
            return watchdog.ProcessObservation("stopped", None)
        if pid == 20105:
            return watchdog.ProcessObservation("stopped", None)
        if pid == 10106:
            return watchdog.ProcessObservation("stopped", None)
        if pid == 20106:
            return watchdog.ProcessObservation("running", child_start)
        raise AssertionError(f"unexpected process id: {pid}")

    result = watchdog.run_watchdog_once(
        store,
        now=START,
        occurrences=(),
        process_probe=process_probe,
        owner_id="watchdog-c12",
        owner_pid=30101,
        owner_start_time=START.isoformat(),
    )

    assert result.queued_source_launch_ids == (
        "launch-confirmed-child-stopped",
        "launch-reused",
    )
    assert store.counts()["watchdog_queue"] == 2
    queue_timestamps = {
        launch_id: store.get_watchdog_queue_by_source_launch_id(launch_id).updated_at
        for launch_id in result.queued_source_launch_ids
    }
    watchdog.run_watchdog_once(
        store,
        now=START + timedelta(minutes=1),
        occurrences=(),
        process_probe=process_probe,
        owner_id="watchdog-c12-repeat",
        owner_pid=30102,
        owner_start_time=(START + timedelta(minutes=1)).isoformat(),
    )
    assert store.counts()["watchdog_queue"] == 2
    assert {
        launch_id: store.get_watchdog_queue_by_source_launch_id(launch_id).updated_at
        for launch_id in result.queued_source_launch_ids
    } == queue_timestamps


def test_c13_contract(tmp_path):
    watchdog = _load_watchdog_module()
    store = make_store(tmp_path)
    occurrence = watchdog.SchedulerOccurrence(
        task_key="daily-1300",
        target_date="2026-10-17",
        scheduled_at=START,
        enabled=True,
        history_available=False,
        last_run_time=None,
        last_result=None,
    )
    rescheduled_occurrence = watchdog.SchedulerOccurrence(
        task_key=occurrence.task_key,
        target_date=occurrence.target_date,
        scheduled_at=occurrence.scheduled_at + timedelta(hours=1),
        enabled=occurrence.enabled,
        history_available=occurrence.history_available,
        last_run_time=occurrence.last_run_time,
        last_result=occurrence.last_result,
    )
    assert watchdog._occurrence_key(occurrence) == watchdog._occurrence_key(
        rescheduled_occurrence
    )

    result = watchdog.run_watchdog_once(
        store,
        now=START + timedelta(minutes=10),
        occurrences=(occurrence,),
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-c13",
        owner_pid=30201,
        owner_start_time=(START + timedelta(minutes=10)).isoformat(),
        start_grace=timedelta(minutes=5),
    )

    assert len(result.queued_queue_ids) == 1
    assert store.counts()["daily_launch"] == 0
    assert store.counts()["watchdog_queue"] == 1
    connection = store._connect()
    try:
        row = connection.execute(
            "SELECT occurrence_key, source_launch_id, status, reason_code FROM watchdog_queue"
        ).fetchone()
    finally:
        connection.close()
    assert row["occurrence_key"] == "scheduled:daily-1300:2026-10-17"
    assert row["status"] == "unknown"
    assert row["reason_code"] == "daily_launcher_not_started"

    watchdog.run_watchdog_once(
        store,
        now=START + timedelta(minutes=11),
        occurrences=(occurrence,),
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-c13-repeat",
        owner_pid=30202,
        owner_start_time=(START + timedelta(minutes=11)).isoformat(),
        start_grace=timedelta(minutes=5),
    )
    assert store.counts()["watchdog_queue"] == 1


def test_c13_does_not_enqueue_before_start_grace(tmp_path):
    watchdog = _load_watchdog_module()
    store = make_store(tmp_path)
    occurrence = watchdog.SchedulerOccurrence(
        task_key="daily-1300",
        target_date="2026-10-17",
        scheduled_at=START,
        enabled=True,
        history_available=False,
        last_run_time=None,
        last_result=None,
    )

    result = watchdog.run_watchdog_once(
        store,
        now=START + timedelta(minutes=4, seconds=59),
        occurrences=(occurrence,),
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-c13-grace",
        owner_pid=30203,
        owner_start_time=(START + timedelta(minutes=4)).isoformat(),
        start_grace=timedelta(minutes=5),
    )

    assert result.queued_queue_ids == ()
    assert store.counts()["watchdog_queue"] == 0


def test_c13_reconciles_late_daily_launch_to_existing_queue(tmp_path):
    watchdog = _load_watchdog_module()
    store = make_store(tmp_path)
    occurrence = watchdog.SchedulerOccurrence(
        task_key="daily-1300",
        target_date="2026-10-17",
        scheduled_at=START,
        enabled=True,
        history_available=False,
        last_run_time=None,
        last_result=None,
    )
    first = watchdog.run_watchdog_once(
        store,
        now=START + timedelta(minutes=10),
        occurrences=(occurrence,),
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-c13-late-first",
        owner_pid=30204,
        owner_start_time=(START + timedelta(minutes=10)).isoformat(),
        start_grace=timedelta(minutes=5),
    )
    assert len(first.queued_queue_ids) == 1
    store.record_daily_launch_start(
        source_launch_id="launch-c13-late",
        target_date="2026-10-17",
        owner_pid=30205,
        owner_start_time=(START + timedelta(minutes=11)).isoformat(),
        started_at=START + timedelta(minutes=11),
    )

    result = watchdog.run_watchdog_once(
        store,
        now=START + timedelta(minutes=12),
        occurrences=(occurrence,),
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-c13-late-second",
        owner_pid=30206,
        owner_start_time=(START + timedelta(minutes=12)).isoformat(),
        start_grace=timedelta(minutes=5),
    )

    assert result.reconciled_queue_ids == first.queued_queue_ids
    queue = store.get_watchdog_queue(first.queued_queue_ids[0])
    assert queue is not None
    assert queue.source_launch_id == "launch-c13-late"
    assert queue.status == "awaiting_completion"
    assert store.counts()["watchdog_queue"] == 1

    child_start = (START + timedelta(minutes=12)).isoformat()
    store.record_daily_launch_child(
        source_launch_id="launch-c13-late",
        child_pid=30207,
        child_start_time=child_start,
    )

    def stopped_process_probe(pid):
        if pid in {30205, 30207}:
            return watchdog.ProcessObservation("stopped", None)
        raise AssertionError(f"unexpected process id: {pid}")

    stopped = watchdog.run_watchdog_once(
        store,
        now=START + timedelta(minutes=20),
        occurrences=(occurrence,),
        process_probe=stopped_process_probe,
        owner_id="watchdog-c13-late-stopped",
        owner_pid=30208,
        owner_start_time=(START + timedelta(minutes=20)).isoformat(),
        start_grace=timedelta(minutes=5),
    )

    same_queue = store.get_watchdog_queue(first.queued_queue_ids[0])
    assert stopped.queued_source_launch_ids == ()
    assert stopped.queued_queue_ids == ()
    assert same_queue is not None
    assert same_queue.queue_id == queue.queue_id
    assert same_queue.source_launch_id == "launch-c13-late"
    assert same_queue.status == "awaiting_completion"
    assert same_queue.reason_code == "daily_launch_terminated_without_notice"
    assert store.counts()["watchdog_queue"] == 1


def test_c13_keeps_existing_launch_link_when_earlier_record_arrives(tmp_path):
    watchdog = _load_watchdog_module()
    store = make_store(tmp_path)
    occurrence = watchdog.SchedulerOccurrence(
        task_key="daily-1300",
        target_date="2026-10-17",
        scheduled_at=START,
        enabled=True,
        history_available=False,
        last_run_time=None,
        last_result=None,
    )
    rescheduled_occurrence = watchdog.SchedulerOccurrence(
        task_key=occurrence.task_key,
        target_date=occurrence.target_date,
        scheduled_at=occurrence.scheduled_at + timedelta(hours=1),
        enabled=occurrence.enabled,
        history_available=occurrence.history_available,
        last_run_time=occurrence.last_run_time,
        last_result=occurrence.last_result,
    )
    assert watchdog._occurrence_key(occurrence) == watchdog._occurrence_key(
        rescheduled_occurrence
    )
    first = watchdog.run_watchdog_once(
        store,
        now=START + timedelta(minutes=10),
        occurrences=(occurrence,),
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-c13-linked-first",
        owner_pid=30210,
        owner_start_time=(START + timedelta(minutes=10)).isoformat(),
        start_grace=timedelta(minutes=5),
    )
    store.record_daily_launch_start(
        source_launch_id="launch-c13-linked-first",
        target_date="2026-10-17",
        owner_pid=30211,
        owner_start_time=(START + timedelta(minutes=11)).isoformat(),
        started_at=START + timedelta(minutes=11),
    )
    watchdog.run_watchdog_once(
        store,
        now=START + timedelta(minutes=12),
        occurrences=(rescheduled_occurrence,),
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-c13-linked-second",
        owner_pid=30212,
        owner_start_time=(START + timedelta(minutes=12)).isoformat(),
        start_grace=timedelta(minutes=5),
    )
    store.record_daily_launch_start(
        source_launch_id="launch-c13-earlier-recorded-late",
        target_date="2026-10-17",
        owner_pid=30213,
        owner_start_time=(START + timedelta(minutes=10)).isoformat(),
        started_at=START + timedelta(minutes=10, seconds=30),
    )

    result = watchdog.run_watchdog_once(
        store,
        now=START + timedelta(minutes=13),
        occurrences=(rescheduled_occurrence,),
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-c13-linked-third",
        owner_pid=30214,
        owner_start_time=(START + timedelta(minutes=13)).isoformat(),
        start_grace=timedelta(minutes=5),
    )

    queue = store.get_watchdog_queue(first.queued_queue_ids[0])
    assert result.queued_queue_ids == ()
    assert queue is not None
    assert queue.source_launch_id == "launch-c13-linked-first"
    assert store.counts()["watchdog_queue"] == 1


def test_watchdog_cursor_advances_monotonically(tmp_path):
    store = make_store(tmp_path)
    assert store.get_watchdog_cursor() is None

    store.advance_watchdog_cursor(
        reconciled_through_date="2026-10-18", updated_at=START
    )
    store.advance_watchdog_cursor(
        reconciled_through_date="2026-10-17", updated_at=START + timedelta(days=1)
    )

    assert store.get_watchdog_cursor() == "2026-10-18"
