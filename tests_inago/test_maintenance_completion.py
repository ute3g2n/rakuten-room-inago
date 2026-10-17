"""Isolated contracts for the daily launcher and final completion notice."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from automation.inago.maintenance.daily_launcher import (
    DailyProcessResult,
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

    def preflight_failure(command, cwd):
        assert command[-1] == "automation.inago.cli"
        assert cwd == PROJECT_ROOT
        assert store.counts()["daily_launch"] == 1
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

    assert events == ["start_saved", "child_finished"]
    assert outcome.exit_code == 4
    assert record.status == "blocked"
    assert record.source_run_id is None
    assert record.failure_phase == "preflight"
    assert json.loads(record.result_json)["failed_checks"] == [
        "runtime_setup_missing"
    ]
    assert check["source_launch_id"] == outcome.launch_id
    assert check["source_run_id"] is None
    assert check["check_status"] == "incomplete"
    assert store.counts()["daily_check"] == 1


def test_c09_contract(tmp_path):
    store = make_store(tmp_path)
    sensitive_detail = "secret-config-path-and-sql-text"

    def run_integrity_error(command, cwd):
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

    def export_failure(command, cwd):
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

    def close_and_backup_failure(command, cwd):
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

    def successful_daily(command, cwd):
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

    def after_hours_daily(command, cwd):
        calls.append((command, cwd))
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
    assert task.findtext("task:Settings/task:ExecutionTimeLimit", namespaces=namespace) == "PT0S"
    arguments = task.findtext("task:Actions/task:Exec/task:Arguments", namespaces=namespace)
    assert "automation.inago.maintenance.daily_launcher" in arguments
