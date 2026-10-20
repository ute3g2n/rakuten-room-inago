from __future__ import annotations

from datetime import datetime
import hashlib
import json
import linecache
import logging
from pathlib import Path
import sqlite3
import pytest

from automation.inago.clock import FakeClock
from automation.inago import daily_pipeline
from automation.inago.daily_pipeline import (
    DailyExecutionPorts,
    run_daily_execution_core,
)
from automation.inago.trace import read_trace_events


TARGET_DATE = "2026-10-19"


def _ports(
    tmp_path: Path, *, run_daily, failure_result=None, export_dashboard=None
) -> DailyExecutionPorts:
    return DailyExecutionPorts(
        data_root=tmp_path,
        preflight=lambda _now: {},
        open_database=lambda: sqlite3.connect(":memory:"),
        prepare=lambda _connection, _now, _preflight: {},
        start_backup=lambda _connection, _context, _now: {"status": "ready"},
        operations_monitor=lambda _connection, _context, _target: None,
        run_daily=run_daily,
        export_dashboard=export_dashboard or (lambda _context: {"status": "ready"}),
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


class _QueryFailConnection(sqlite3.Connection):
    failing_operation = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        super().execute(
            "CREATE TABLE search_attempt (attempt_id TEXT, run_id TEXT, query TEXT, "
            "parent_refs_json TEXT, level INTEGER, source_status TEXT, ended_at TEXT, retry_of TEXT)"
        )
        super().execute(
            "CREATE TABLE trends_acquisition (acquisition_id TEXT, status TEXT, "
            "attempts_used INTEGER, run_id TEXT)"
        )
        super().execute(
            "CREATE TABLE trends_acquisition_attempt (request_id TEXT, acquisition_id TEXT, "
            "attempt_no INTEGER, retry_of TEXT, ended_at TEXT, status TEXT)"
        )

    def execute(self, query, parameters=()):
        operation_by_query = {
            "search_attempt": "retry_summary_room_attempts",
            "trends_acquisition WHERE": "retry_summary_trend_acquisitions",
            "trends_acquisition_attempt AS": "retry_summary_trend_attempts",
        }
        for marker, operation in operation_by_query.items():
            if marker in query and operation == self.failing_operation:
                raise sqlite3.OperationalError("SQL_CANARY private query text")
        return super().execute(query, parameters)


def _safe_result(*, run_id="synthetic-run"):
    return {
        "status": "complete",
        "target_date": TARGET_DATE,
        "run_id": run_id,
        "committed_count": 3,
        "attempt_used": 3,
        "metrics_status": "observed",
        "resumed": False,
    }


def _assert_safe_diagnostic(result, operation, exception_class):
    diagnostic = next(
        item for item in result["secondary_errors"] if item.get("operation") == operation
    )
    assert diagnostic["exception_class"] == exception_class
    assert diagnostic["phase"] == "daily_diagnostic"
    assert diagnostic["failure_module"] == daily_pipeline.__name__
    assert isinstance(diagnostic["failure_function"], str)
    assert isinstance(diagnostic["failure_line"], int)
    assert diagnostic["code_sha256"] == hashlib.sha256(
        Path(daily_pipeline.__file__).read_bytes()
    ).hexdigest()
    assert "SQL_CANARY" not in json.dumps(diagnostic)
    assert "callback_CANARY" not in json.dumps(diagnostic)
    assert "export_CANARY" not in json.dumps(diagnostic)


def test_callback_secondary_exception_preserves_primary_failure_and_records_safe_diagnostic(
    tmp_path: Path,
):
    def raise_primary(_connection, _context, _on_draft_commit):
        raise RuntimeError("primary_CANARY")

    def raise_callback(_error, _connection, _target_date, _phase):
        raise ValueError("callback_CANARY")

    result = run_daily_execution_core(
        _ports(
            tmp_path,
            run_daily=raise_primary,
            failure_result=raise_callback,
        ),
        clock=_clock(),
    )

    assert result["daily_status"] == "failed"
    assert result["daily_reason"] == "RuntimeError"
    assert result["error_count"] == 1
    assert result["committed_count"] == 0
    assert result["primary_error"]["code"] == "RuntimeError"
    _assert_safe_diagnostic(result, "failure_result_callback", "ValueError")


@pytest.mark.parametrize(
    ("operation", "expected_operation"),
    [
        ("retry_summary_room_attempts", "retry_summary_room_attempts"),
        ("retry_summary_trend_acquisitions", "retry_summary_trend_acquisitions"),
        ("retry_summary_trend_attempts", "retry_summary_trend_attempts"),
    ],
)
def test_each_retry_summary_query_failure_keeps_result_and_null_fallbacks(
    tmp_path: Path, monkeypatch, operation: str, expected_operation: str
):
    real_open = sqlite3.connect
    monkeypatch.setattr(_QueryFailConnection, "failing_operation", operation)
    monkeypatch.setattr(
        daily_pipeline.sqlite3,
        "connect",
        lambda *args, **kwargs: real_open(*args, factory=_QueryFailConnection, **kwargs),
    )
    ports = _ports(
        tmp_path,
        run_daily=lambda _connection, _context, _on_draft_commit: _safe_result(),
    )

    result = run_daily_execution_core(ports, clock=_clock())

    assert result["daily_status"] == "complete"
    assert result["committed_count"] == 3
    assert result["attempt_used"] == 3
    assert result["retry_summary"] == {
        "attempt_count": None,
        "recovered_count": None,
        "unresolved_count": None,
        "contract_violation_count": None,
    }
    _assert_safe_diagnostic(result, expected_operation, "OperationalError")
    serialized = json.dumps(result["secondary_errors"])
    assert "SQL_CANARY" not in serialized


def test_commit_then_export_exception_preserves_commit_and_draft_count(
    tmp_path: Path,
):
    export_calls = 0

    def export(_context):
        nonlocal export_calls
        export_calls += 1
        if export_calls == 1:
            raise OSError("export_CANARY")
        return {"status": "ready", "draft_count": 3}

    def commit_then_export(connection, _context, on_draft_commit):
        connection.commit()
        refreshed = on_draft_commit(connection)
        return {**_safe_result(run_id=None), "commit_refresh": refreshed}

    result = run_daily_execution_core(
        _ports(
            tmp_path,
            run_daily=commit_then_export,
            failure_result=None,
            export_dashboard=export,
        ),
        clock=_clock(),
    )
    assert result["daily_status"] == "complete"
    assert result["committed_count"] == 3
    assert result["attempt_used"] == 3
    assert result["export"]["status"] == "ready"
    assert result["phase_results"]["export"]["commit_refresh_failures"] == 1, result
    _assert_safe_diagnostic(result, "dashboard_export_after_commit", "OSError")


def test_secondary_errors_reach_isolated_cli_l01_record_payload(tmp_path: Path):
    from automation.inago.cli import _append_production_log

    def fail_primary(_connection, _context, _on_draft_commit):
        raise RuntimeError("primary_CANARY")

    def fail_callback(_error, _connection, _target_date, _phase):
        raise ValueError("callback_CANARY")

    result = run_daily_execution_core(
        _ports(tmp_path / "pipeline", run_daily=fail_primary, failure_result=fail_callback),
        clock=_clock(),
    )
    diagnostic = next(
        item for item in result["secondary_errors"]
        if item.get("operation") == "failure_result_callback"
    )
    assert diagnostic["operation"] == "failure_result_callback"
    assert diagnostic["exception_class"] == "ValueError"
    diagnostics = {
        "overall_status": result["overall_status"],
        "daily_status": result["daily_status"],
        "reason": result["reason"],
        "committed_count": result["committed_count"],
        "error_count": result["error_count"],
        "primary_error": result["primary_error"],
        "secondary_errors": result["secondary_errors"],
        "phase_results": result["phase_results"],
    }
    _append_production_log(
        tmp_path,
        mode="daily",
        status="failed",
        reason="daily_result_failed",
        diagnostics=diagnostics,
    )

    record_path = tmp_path / "logs" / "production-cli.jsonl"
    record = json.loads(record_path.read_text(encoding="utf-8").splitlines()[0])
    assert diagnostic in record["diagnostics"]["secondary_errors"]
    raw = record_path.read_text(encoding="utf-8")
    assert "SQL_CANARY" not in raw
    assert "callback_CANARY" not in raw
    assert "export_CANARY" not in raw
