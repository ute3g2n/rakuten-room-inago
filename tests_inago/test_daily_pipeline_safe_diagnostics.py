from __future__ import annotations

from datetime import datetime
import hashlib
import linecache
import logging
from pathlib import Path
import sqlite3

from automation.inago.clock import FakeClock
from automation.inago import daily_pipeline
from automation.inago.daily_pipeline import (
    DailyExecutionPorts,
    run_daily_execution_core,
)
from automation.inago.trace import read_trace_events


TARGET_DATE = "2026-10-19"


def _ports(tmp_path: Path, *, run_daily, failure_result=None) -> DailyExecutionPorts:
    return DailyExecutionPorts(
        data_root=tmp_path,
        preflight=lambda _now: {},
        open_database=lambda: sqlite3.connect(":memory:"),
        prepare=lambda _connection, _now, _preflight: {},
        start_backup=lambda _connection, _context, _now: {"status": "ready"},
        operations_monitor=lambda _connection, _context, _target: None,
        run_daily=run_daily,
        export_dashboard=lambda _context: {"status": "ready"},
        preserve_end_of_day=lambda _context, _now, _run_id: {
            "status": "ready",
            "end_backup": {"status": "ready"},
            "monthly_restore": {"status": "already_checked", "already_checked": True},
        },
        failure_result=failure_result,
    )


def _clock() -> FakeClock:
    return FakeClock(datetime.fromisoformat("2026-10-19T12:00:00+09:00"))


def test_pipeline_diagnostic_logger_failures_do_not_change_daily_result(
    tmp_path: Path, monkeypatch
) -> None:
    original_log = logging.Logger.log

    def fail_diagnostic_handlers(self, level, message, *args, **kwargs):
        if message in {"daily_pipeline_failure", "daily_pipeline_trace_result"}:
            raise OSError("synthetic log sink failure")
        return original_log(self, level, message, *args, **kwargs)

    monkeypatch.setattr(logging.Logger, "log", fail_diagnostic_handlers)
    rolled_back: list[bool] = []

    def fail_after_write(connection, _context, _on_draft_commit):
        connection.execute("CREATE TABLE diagnostic_probe (value TEXT)")
        connection.execute("INSERT INTO diagnostic_probe VALUES ('pending')")
        raise RuntimeError("callback failed")

    def failure_result(_error, connection, target_date, phase):
        rolled_back.append(not connection.in_transaction)
        return {
            "status": "failed",
            "target_date": target_date,
            "run_id": None,
            "committed_count": 0,
            "attempt_used": 0,
            "metrics_status": "unknown",
            "resumed": False,
            "reason": "diagnostic_probe_failed",
            "primary_error": {"phase": phase, "code": "RuntimeError"},
        }

    failed = run_daily_execution_core(
        _ports(
            tmp_path / "failed",
            run_daily=fail_after_write,
            failure_result=failure_result,
        ),
        clock=_clock(),
    )
    assert failed["daily_status"] == "failed"
    assert rolled_back == [True]

    completed = run_daily_execution_core(
        _ports(
            tmp_path / "complete",
            run_daily=lambda _connection, _context, _on_draft_commit: {
                "status": "complete",
                "target_date": TARGET_DATE,
                "run_id": None,
                "committed_count": 1,
                "attempt_used": 1,
                "metrics_status": "complete",
                "resumed": False,
            },
        ),
        clock=_clock(),
    )
    assert completed["daily_status"] == "complete"


def test_pipeline_failure_records_its_own_capture_location(
    tmp_path: Path, caplog
) -> None:
    connection_state: list[bool] = []

    def callback_in_test_module(connection, _context, _on_draft_commit):
        connection.execute("CREATE TABLE capture_probe (value TEXT)")
        connection.execute("INSERT INTO capture_probe VALUES ('pending')")
        raise RuntimeError("callback failed")

    def failure_result(_error, connection, target_date, phase):
        connection_state.append(not connection.in_transaction)
        return {
            "status": "failed",
            "target_date": target_date,
            "run_id": "RUN-N23-CAPTURE",
            "committed_count": 0,
            "attempt_used": 0,
            "metrics_status": "unknown",
            "resumed": False,
            "reason": "capture_probe_failed",
            "primary_error": {"phase": phase, "code": "RuntimeError"},
        }

    caplog.set_level(logging.ERROR, logger=daily_pipeline.__name__)
    result = run_daily_execution_core(
        _ports(
            tmp_path,
            run_daily=callback_in_test_module,
            failure_result=failure_result,
        ),
        clock=_clock(),
    )

    assert result["daily_status"] == "failed"
    assert connection_state == [True]
    failure_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "daily_pipeline_failure"
    )
    expected_hash = hashlib.sha256(
        Path(daily_pipeline.__file__).read_bytes()
    ).hexdigest()
    assert failure_record.failure_module == daily_pipeline.__name__
    assert failure_record.failure_function == "run_daily_execution_core"
    assert failure_record.failure_line is not None
    assert "_pipeline_capture_location()" in linecache.getline(
        daily_pipeline.__file__, failure_record.failure_line
    )
    assert failure_record.code_sha256 == expected_hash

    trace_path = tmp_path / result["pipeline_trace"]["reference"]
    trace_events = read_trace_events(trace_path)
    phase_error = next(event for event in trace_events if event["phase"] == "error")
    assert phase_error["failure_module"] == daily_pipeline.__name__
    assert phase_error["failure_function"] == "run_daily_execution_core"
    assert phase_error["failure_line"] == failure_record.failure_line
    assert "_pipeline_capture_location()" in linecache.getline(
        daily_pipeline.__file__, phase_error["failure_line"]
    )
    assert phase_error["code_sha256"] == expected_hash
