from __future__ import annotations

from dataclasses import replace
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
from automation.inago.contracts import IdempotencyConflict
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
    assert not hasattr(failure_record, "code_sha256")
    assert failure_record.failure_module == daily_pipeline.__name__
    assert failure_record.failure_function == "run_daily_execution_core"
    assert failure_record.failure_line is not None
    assert "_pipeline_capture_location()" in linecache.getline(
        daily_pipeline.__file__, failure_record.failure_line
    )

    trace_path = tmp_path / result["pipeline_trace"]["reference"]
    trace_events = read_trace_events(trace_path)
    phase_error = next(event for event in trace_events if event["phase"] == "error")
    assert "code_sha256" not in phase_error
    assert phase_error["failure_module"] == daily_pipeline.__name__
    assert phase_error["failure_function"] == "run_daily_execution_core"
    assert phase_error["failure_line"] == failure_record.failure_line
    assert "_pipeline_capture_location()" in linecache.getline(
        daily_pipeline.__file__, phase_error["failure_line"]
    )


def test_result_mapping_adds_primary_diagnostic_on_all_callback_paths(
    tmp_path: Path,
) -> None:
    primary_message = "primary_EXCEPTION_CANARY"
    secondary_message = "secondary_CALLBACK_CANARY"

    def raise_primary(_connection, _context, _on_draft_commit):
        raise RuntimeError(primary_message)

    def make_callback(kind: str):
        if kind == "none":
            return None
        if kind == "success":
            return lambda _error, _connection, target_date, phase: {
                "status": "failed",
                "target_date": target_date,
                "run_id": None,
                "committed_count": 0,
                "attempt_used": 0,
                "metrics_status": "unknown",
                "resumed": False,
                "reason": "primary_failure",
                "error_count": 1,
                "primary_error": {"phase": phase, "code": "RuntimeError", "kept": True},
            }

        def raise_secondary(_error, _connection, _target_date, _phase):
            raise ValueError(secondary_message)

        return raise_secondary

    for kind in ("none", "success", "secondary_error"):
        result = run_daily_execution_core(
            _ports(
                tmp_path / kind,
                run_daily=raise_primary,
                failure_result=make_callback(kind),
            ),
            clock=FakeClock(datetime.fromisoformat("2026-10-20T10:00:00+09:00")),
        )

        assert result["daily_status"] == "failed"
        assert result["daily_reason"] == (
            "primary_failure" if kind == "success" else "RuntimeError"
        )
        assert result["run_id"] is None
        assert result["committed_count"] == 0
        assert result["error_count"] == 1
        primary = result["primary_error"]
        assert primary["phase"] == "daily"
        assert primary["code"] == "RuntimeError"
        if kind == "success":
            assert primary["kept"] is True

        diagnostic = primary["diagnostic"]
        assert diagnostic["operation"] == "daily_primary_failure"
        assert diagnostic["exception_class"] == "RuntimeError"
        assert diagnostic["exception_chain"][0] == {
            "relation": "raised",
            "exception_class": "RuntimeError",
        }
        assert diagnostic["exception_chain_truncated"] is False
        assert diagnostic["exception_frames"]
        assert all(
            not Path(frame["path"]).is_absolute()
            for frame in diagnostic["exception_frames"]
        )
        assert diagnostic["error_name"] == "unknown"
        assert diagnostic["constraint_label"] == "unknown"
        assert diagnostic["failure_module"] == daily_pipeline.__name__
        assert diagnostic["failure_function"] == "add_primary_diagnostic"
        assert isinstance(diagnostic["failure_line"], int)

        encoded_primary = json.dumps(primary)
        assert primary_message not in encoded_primary
        assert secondary_message not in encoded_primary
        operations = [
            item.get("operation")
            for item in result["secondary_errors"]
            if item.get("operation")
        ]
        assert operations == (
            ["failure_result_callback", "failure_result_run_lookup"]
            if kind == "secondary_error"
            else [] if kind == "success" else ["failure_result_run_lookup"]
        )

        from automation.inago.cli import _append_production_log

        case_root = tmp_path / kind / "l01"
        _append_production_log(
            case_root,
            mode="daily",
            status="failed",
            reason="synthetic_primary_failure",
            diagnostics={
                "daily_status": result["daily_status"],
                "daily_reason": result["daily_reason"],
                "committed_count": result["committed_count"],
                "error_count": result["error_count"],
                "primary_error": result["primary_error"],
                "secondary_errors": result["secondary_errors"],
                "phase_results": result["phase_results"],
            },
        )
        record_path = case_root / "logs" / "production-cli.jsonl"
        persisted = json.loads(record_path.read_text(encoding="utf-8").splitlines()[0])
        assert persisted["diagnostics"]["primary_error"]["diagnostic"] == diagnostic
        persisted_text = record_path.read_text(encoding="utf-8")
        assert primary_message not in persisted_text
        assert secondary_message not in persisted_text


def test_result_mapping_preserves_existing_diagnostic_and_adds_primary_diagnostic(
    tmp_path: Path,
) -> None:
    existing_diagnostic = {"source": "callback", "detail": "keep this value"}

    def fail_primary(_connection, _context, _on_draft_commit):
        raise RuntimeError("primary failure")

    def failure_result(_error, _connection, target_date, phase):
        return {
            "status": "failed",
            "target_date": target_date,
            "run_id": None,
            "committed_count": 0,
            "attempt_used": 0,
            "metrics_status": "unknown",
            "resumed": False,
            "reason": "primary_failure",
            "error_count": 1,
            "primary_error": {
                "phase": phase,
                "code": "RuntimeError",
                "diagnostic": existing_diagnostic.copy(),
            },
        }

    result = run_daily_execution_core(
        _ports(
            tmp_path,
            run_daily=fail_primary,
            failure_result=failure_result,
        ),
        clock=FakeClock(datetime.fromisoformat("2026-10-20T10:00:00+09:00")),
    )

    primary = result["primary_error"]
    assert primary["diagnostic"] == existing_diagnostic
    added = primary["daily_primary_failure_diagnostic"]
    assert added["operation"] == "daily_primary_failure"
    assert added["exception_class"] == "RuntimeError"


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
    assert "code_sha256" not in diagnostic
    assert diagnostic["exception_class"] == exception_class
    assert diagnostic["phase"] == "daily_diagnostic"
    assert diagnostic["failure_module"] == daily_pipeline.__name__
    assert isinstance(diagnostic["failure_function"], str)
    assert isinstance(diagnostic["failure_line"], int)
    assert diagnostic["exception_chain"]
    assert diagnostic["exception_chain"][0] == {
        "relation": "raised",
        "exception_class": exception_class,
    }
    assert isinstance(diagnostic["exception_frames"], list)
    assert all(not Path(frame["path"]).is_absolute() for frame in diagnostic["exception_frames"])
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
    _assert_safe_diagnostic(result, "failure_result_run_lookup", "OperationalError")
    assert [
        item["operation"]
        for item in result["secondary_errors"]
        if item.get("operation")
    ] == ["failure_result_callback", "failure_result_run_lookup"]


def test_prepare_idempotency_conflict_records_safe_operation_context(tmp_path: Path):
    sentinel = "IDEMPOTENCY_CONFLICT_SECRET_SENTINEL"
    calls: list[str] = []

    def raise_prepare_conflict(_connection, _now, _preflight):
        calls.append("prepare")
        raise IdempotencyConflict(f"conflicting private value: {sentinel}")

    def backup_must_not_run(_connection, _context, _now):
        calls.append("start_backup")
        raise RuntimeError("backup failure sentinel")

    def preservation_must_not_run(_context, _now, _run_id):
        calls.append("preservation")
        raise RuntimeError("preservation failure sentinel")

    ports = replace(
        _ports(tmp_path, run_daily=lambda *_args: pytest.fail("daily must not run")),
        prepare=raise_prepare_conflict,
        start_backup=backup_must_not_run,
        preserve_end_of_day=preservation_must_not_run,
    )
    result = run_daily_execution_core(
        ports,
        clock=FakeClock(datetime.fromisoformat("2026-10-20T13:00:00+09:00")),
    )

    assert calls == ["prepare"]
    assert result["daily_status"] == "failed"
    assert result["phase_results"]["prepare"]["status"] == "failed"
    assert result["run_id"] is None
    primary = result["primary_error"]
    diagnostic = primary["diagnostic"]
    assert diagnostic["operation"] == "daily_primary_failure"
    assert diagnostic["failed_operation"] == "prepare"
    assert diagnostic["exception_class"] == "IdempotencyConflict"
    assert diagnostic["failure_module"] == daily_pipeline.__name__
    assert diagnostic["failure_function"] is not None
    phase_error = next(
        item
        for item in result["pipeline_trace"]["diagnostic_context"]["phase_events"]
        if item["phase"] == "error"
    )
    assert phase_error["operation"] == "prepare"
    assert phase_error["error_class"] == "IdempotencyConflict"
    assert phase_error["failure_module"] == daily_pipeline.__name__
    assert phase_error["failure_function"] == "run_daily_execution_core"
    assert result["pipeline_trace"]["diagnostic_context"]["persisted_run_id"] is None
    serialized = json.dumps(result, sort_keys=True)
    assert sentinel not in serialized
    assert "conflicting private value" not in serialized


def test_failure_result_run_lookup_error_is_safe_and_distinct_from_no_row(
    tmp_path: Path,
):
    def raise_primary(_connection, _context, _on_draft_commit):
        raise RuntimeError("primary_CANARY")

    failed_lookup = run_daily_execution_core(
        _ports(tmp_path / "lookup-error", run_daily=raise_primary),
        clock=_clock(),
    )
    assert failed_lookup["daily_status"] == "failed"
    assert failed_lookup["daily_reason"] == "RuntimeError"
    assert failed_lookup["run_id"] is None
    assert failed_lookup["committed_count"] == 0
    assert [
        item["operation"]
        for item in failed_lookup["secondary_errors"]
        if item.get("operation")
    ] == ["failure_result_run_lookup"]
    _assert_safe_diagnostic(
        failed_lookup, "failure_result_run_lookup", "OperationalError"
    )

    def create_empty_run_table_then_raise(connection, _context, _on_draft_commit):
        connection.execute(
            "CREATE TABLE run (run_id TEXT, mode TEXT, target_date TEXT, "
            "status TEXT, committed_count INTEGER, attempt_used INTEGER)"
        )
        raise RuntimeError("primary_CANARY")

    no_row = run_daily_execution_core(
        _ports(tmp_path / "lookup-empty", run_daily=create_empty_run_table_then_raise),
        clock=_clock(),
    )
    assert no_row["daily_status"] == "failed"
    assert no_row["daily_reason"] == "RuntimeError"
    assert no_row["run_id"] is None
    assert no_row["committed_count"] == 0
    assert not [
        item for item in no_row["secondary_errors"] if item.get("operation")
    ]

    serialized = json.dumps(failed_lookup["secondary_errors"])
    assert "primary_CANARY" not in serialized


def test_secondary_exception_chain_preserves_cause_context_and_suppression():
    def raise_chain(kind):
        try:
            raise OSError("MESSAGE_CANARY")
        except OSError as inner:
            if kind == "cause":
                raise ValueError("OUTER_CANARY") from inner
            if kind == "suppressed":
                raise ValueError("OUTER_CANARY") from None
            raise ValueError("OUTER_CANARY")

    expected_relations = {
        "cause": ["raised", "cause"],
        "context": ["raised", "context"],
        "suppressed": ["raised", "context_suppressed"],
    }
    for kind, relations in expected_relations.items():
        with pytest.raises(ValueError) as raised:
            raise_chain(kind)

        chain = daily_pipeline._safe_exception_chain(raised.value)
        assert [item["relation"] for item in chain] == relations
        assert [item["exception_class"] for item in chain] == [
            "ValueError",
            "OSError",
        ]
        assert "CANARY" not in json.dumps(chain)

        frames = daily_pipeline._safe_exception_frames(raised.value)
        assert frames
        assert all(not Path(item["path"]).is_absolute() for item in frames)
        assert all("CANARY" not in json.dumps(item) for item in frames)

    try:
        json.loads("{")
    except json.JSONDecodeError as external_error:
        external_frames = daily_pipeline._safe_exception_frames(external_error)
    assert external_frames
    assert all(frame["path"].startswith("tests_inago/") for frame in external_frames)


def test_exception_chain_truncation_boundary_is_reported_without_unsafe_data():
    def cause_chain(length: int) -> BaseException:
        nodes = [RuntimeError(f"MESSAGE_CANARY_{index}") for index in range(length)]
        for current, following in zip(nodes, nodes[1:]):
            current.__cause__ = following
        local_canary = "LOCAL_CANARY"
        try:
            raise nodes[0]
        except RuntimeError as exception:
            assert local_canary
            return exception

    fewer_than_limit = cause_chain(11)
    exactly_limit = cause_chain(12)
    beyond_limit = cause_chain(13)

    duplicate_pending = cause_chain(12)
    current = duplicate_pending
    for _ in range(11):
        current = current.__cause__
    current.__context__ = duplicate_pending

    cases = [
        ("fewer_than_limit", fewer_than_limit, False, 11),
        ("exactly_limit", exactly_limit, False, 12),
        ("beyond_limit", beyond_limit, True, 12),
        ("duplicate_pending_only", duplicate_pending, False, 12),
    ]
    for operation, exception, expected_truncated, expected_length in cases:
        chain, truncated = daily_pipeline._safe_exception_chain_details(exception)
        assert len(chain) == expected_length
        assert truncated is expected_truncated
        assert daily_pipeline._safe_exception_chain(exception) == chain

        diagnostic = daily_pipeline._secondary_diagnostic(
            operation, exception, (daily_pipeline.__name__, "test", 1)
        )
        assert len(diagnostic["exception_chain"]) == expected_length
        assert diagnostic["exception_chain_truncated"] is expected_truncated
        serialized = json.dumps(diagnostic)
        assert "MESSAGE_CANARY" not in serialized
        assert "LOCAL_CANARY" not in serialized
        assert "traceback" not in serialized.lower()
        assert all("message" not in key.lower() for key in diagnostic)


def test_exception_frames_have_bounded_node_and_frame_totals():
    def raise_deep(error: RuntimeError, depth: int) -> None:
        if depth:
            raise_deep(error, depth - 1)
            return
        raise error

    def capture_deep(error: RuntimeError) -> BaseException:
        try:
            raise_deep(error, 80)
        except RuntimeError as raised:
            return raised

    nodes = [capture_deep(RuntimeError(f"MESSAGE_CANARY_{index}")) for index in range(13)]
    for current, following in zip(nodes, nodes[1:]):
        current.__cause__ = following

    frames, truncated = daily_pipeline._safe_exception_frame_details(nodes[0])

    assert len(frames) <= 12 * 8
    assert len(frames) == 12 * 8
    assert truncated is True
    assert "MESSAGE_CANARY" not in json.dumps(frames)


def test_pipeline_source_fingerprint_helper_is_removed():
    assert not hasattr(daily_pipeline, "_pipeline_code_sha256")


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
    tmp_path.mkdir(parents=True, exist_ok=True)
    database_path = tmp_path / "daily.sqlite"
    database_counts = {"commit": 0, "rollback": 0}

    class CountedConnection(sqlite3.Connection):
        def commit(self):
            database_counts["commit"] += 1
            return super().commit()

        def rollback(self):
            database_counts["rollback"] += 1
            return super().rollback()

    def export(_context):
        nonlocal export_calls
        export_calls += 1
        if export_calls == 1:
            raise OSError("export_CANARY")
        return {"status": "ready", "draft_count": 1}

    def commit_then_export(connection, _context, on_draft_commit):
        connection.execute("CREATE TABLE committed_draft (draft_id INTEGER)")
        connection.execute("INSERT INTO committed_draft VALUES (1)")
        connection.commit()
        refreshed = on_draft_commit(connection)
        return {
            **_safe_result(run_id=None),
            "committed_count": 1,
            "attempt_used": 1,
            "commit_refresh": refreshed,
        }

    ports = _ports(
        tmp_path,
        run_daily=commit_then_export,
        failure_result=None,
        export_dashboard=export,
    )
    ports = replace(
        ports,
        open_database=lambda: sqlite3.connect(
            database_path, factory=CountedConnection
        ),
    )
    result = run_daily_execution_core(
        ports,
        clock=_clock(),
    )
    with sqlite3.connect(database_path) as connection:
        draft_count = connection.execute(
            "SELECT COUNT(*) FROM committed_draft"
        ).fetchone()[0]

    assert result["daily_status"] == "complete"
    assert result["committed_count"] == 1
    assert result["attempt_used"] == 1
    assert draft_count == 1
    assert database_counts == {"commit": 1, "rollback": 0}
    assert result["export"]["status"] == "ready"
    assert result["phase_results"]["export"]["commit_refresh_failures"] == 1, result
    _assert_safe_diagnostic(result, "dashboard_export_after_commit", "OSError")


@pytest.mark.parametrize(
    ("after_commit_result", "expected_status", "expected_failure_count"),
    [
        ({"status": "failed", "error_code": "ERROR_CANARY", "path": "PATH_CANARY"}, "failed", 1),
        ({"status": "not_run"}, "not_run", 1),
        ({"status": "PRIVATE_STATUS_CANARY", "reason": "REASON_CANARY"}, "unknown", 1),
        ({"reason": "MISSING_STATUS_CANARY"}, "unknown", 1),
        ({"status": "ready"}, None, 0),
    ],
)
def test_commit_then_export_non_ready_return_is_safe_and_persisted(
    tmp_path: Path, after_commit_result, expected_status, expected_failure_count
):
    from automation.inago.cli import _append_production_log

    export_calls = 0
    database_path = tmp_path / "daily.sqlite"
    database_counts = {"commit": 0, "rollback": 0}

    class CountedConnection(sqlite3.Connection):
        def commit(self):
            database_counts["commit"] += 1
            return super().commit()

        def rollback(self):
            database_counts["rollback"] += 1
            return super().rollback()

    def export(_context):
        nonlocal export_calls
        export_calls += 1
        if export_calls == 1:
            return after_commit_result
        return {"status": "ready", "draft_count": 1}

    def commit_then_export(connection, _context, on_draft_commit):
        connection.execute("CREATE TABLE committed_draft (draft_id INTEGER)")
        connection.execute("INSERT INTO committed_draft VALUES (1)")
        connection.commit()
        refreshed = on_draft_commit(connection)
        return {
            **_safe_result(run_id=None),
            "committed_count": 1,
            "attempt_used": 1,
            "commit_refresh": refreshed,
        }

    ports = _ports(
        tmp_path,
        run_daily=commit_then_export,
        export_dashboard=export,
    )
    ports = replace(
        ports,
        open_database=lambda: sqlite3.connect(
            database_path, factory=CountedConnection
        ),
    )
    result = run_daily_execution_core(
        ports,
        clock=FakeClock(datetime.fromisoformat("2026-10-20T10:00:00+09:00")),
    )

    with sqlite3.connect(database_path) as connection:
        draft_count = connection.execute(
            "SELECT COUNT(*) FROM committed_draft"
        ).fetchone()[0]

    assert result["daily_status"] == "complete"
    assert result["committed_count"] == 1
    assert result["attempt_used"] == 1
    assert draft_count == 1
    assert database_counts == {"commit": 1, "rollback": 0}
    assert result["export"]["status"] == "ready"
    assert result["phase_results"]["export"]["commit_refresh_failures"] == expected_failure_count

    matching = [
        item for item in result["secondary_errors"]
        if item.get("operation") == "dashboard_export_after_commit"
    ]
    if expected_status is None:
        assert matching == []
    else:
        assert len(matching) == 1
        diagnostic = matching[0]
        assert diagnostic["code"] == "dashboard_export_after_commit_not_ready"
        assert diagnostic["failure_kind"] == "returned_status"
        assert diagnostic["returned_status"] == expected_status
        assert diagnostic["exception_observed"] is False
        assert diagnostic["exception_class"] is None
        assert diagnostic["exception_chain"] == []
        assert diagnostic["exception_frames"] == []
        assert diagnostic["failure_module"] == daily_pipeline.__name__
        assert isinstance(diagnostic["failure_function"], str)
        assert isinstance(diagnostic["failure_line"], int)

        case_root = tmp_path / "safe-log"
        diagnostics = {
            "overall_status": result["overall_status"],
            "daily_status": result["daily_status"],
            "reason": result["reason"],
            "committed_count": result["committed_count"],
            "primary_error": result["primary_error"],
            "secondary_errors": result["secondary_errors"],
            "phase_results": result["phase_results"],
        }
        _append_production_log(
            case_root,
            mode="daily",
            status="complete",
            reason="synthetic_result",
            diagnostics=diagnostics,
        )
        record_path = case_root / "logs" / "production-cli.jsonl"
        persisted = json.loads(record_path.read_text(encoding="utf-8").splitlines()[0])
        assert diagnostic in persisted["diagnostics"]["secondary_errors"]
        safe_serialized = record_path.read_text(encoding="utf-8")
        for canary in (
            "ERROR_CANARY",
            "PATH_CANARY",
            "PRIVATE_STATUS_CANARY",
            "REASON_CANARY",
            "MISSING_STATUS_CANARY",
        ):
            assert canary not in safe_serialized


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
    assert result["run_id"] is None
    assert sum(
        item.get("operation") == "failure_result_callback"
        for item in result["secondary_errors"]
    ) == 1
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
    assert sum(
        item.get("operation") == "failure_result_callback"
        for item in record["diagnostics"]["secondary_errors"]
    ) == 1
    persisted_diagnostic = next(
        item for item in record["diagnostics"]["secondary_errors"]
        if item.get("operation") == "failure_result_callback"
    )
    assert persisted_diagnostic["exception_chain"] == diagnostic["exception_chain"]
    assert persisted_diagnostic["exception_frames"] == diagnostic["exception_frames"]
    raw = record_path.read_text(encoding="utf-8")
    assert "SQL_CANARY" not in raw
    assert "callback_CANARY" not in raw
    assert "export_CANARY" not in raw


def test_sqlite_error_code_reaches_primary_secondary_and_l01_payload(tmp_path: Path):
    from types import SimpleNamespace

    from automation.inago.cli import _append_production_log

    def missing_table_error(connection):
        try:
            connection.execute("SELECT value FROM synthetic_missing_table")
        except sqlite3.Error as exc:
            return exc
        raise AssertionError("synthetic SQLite query unexpectedly succeeded")

    def fail_primary(connection, _context, _on_draft_commit):
        raise missing_table_error(connection)

    def fail_callback(_error, connection, _target_date, _phase):
        raise missing_table_error(connection)

    result = run_daily_execution_core(
        _ports(
            tmp_path / "pipeline",
            run_daily=fail_primary,
            failure_result=fail_callback,
        ),
        clock=FakeClock(datetime.fromisoformat("2026-10-20T10:00:00+09:00")),
    )

    assert result["daily_status"] == "failed"
    assert result["run_id"] is None
    assert result["committed_count"] == 0
    primary = result["primary_error"]["diagnostic"]
    secondary = next(
        item
        for item in result["secondary_errors"]
        if item.get("operation") == "failure_result_callback"
    )
    assert primary["error_code_number"] == 1
    assert secondary["error_code_number"] == 1
    assert primary["operation"] == "daily_primary_failure"
    assert secondary["operation"] == "failure_result_callback"
    for diagnostic in (primary, secondary):
        assert diagnostic["exception_class"] == "OperationalError"
        assert diagnostic["failure_module"] == daily_pipeline.__name__
        assert isinstance(diagnostic["failure_function"], str)
        assert isinstance(diagnostic["failure_line"], int)
        assert diagnostic["exception_chain"][0] == {
            "relation": "raised",
            "exception_class": "OperationalError",
        }
        assert diagnostic["exception_frames"]

    for value in (True, -1, 65536, "1", None):
        assert daily_pipeline._safe_sqlite_error_code(
            SimpleNamespace(sqlite_errorcode=value)
        ) is None
    assert daily_pipeline._safe_sqlite_error_code(
        SimpleNamespace(sqlite_errorcode=1)
    ) == 1

    diagnostics = {
        "daily_status": result["daily_status"],
        "daily_reason": result["daily_reason"],
        "committed_count": result["committed_count"],
        "error_count": result["error_count"],
        "primary_error": result["primary_error"],
        "secondary_errors": result["secondary_errors"],
        "phase_results": result["phase_results"],
    }
    _append_production_log(
        tmp_path / "l01",
        mode="daily",
        status="failed",
        reason="synthetic_failure",
        diagnostics=diagnostics,
    )
    record_path = tmp_path / "l01" / "logs" / "production-cli.jsonl"
    record = json.loads(record_path.read_text(encoding="utf-8").splitlines()[0])
    persisted = record["diagnostics"]
    assert persisted["primary_error"]["diagnostic"]["error_code_number"] == 1
    persisted_secondary = next(
        item
        for item in persisted["secondary_errors"]
        if item.get("operation") == "failure_result_callback"
    )
    assert persisted_secondary["error_code_number"] == 1
    safe_record = record_path.read_text(encoding="utf-8")
    assert "synthetic_missing_table" not in safe_record
    assert "no such table" not in safe_record


class SqliteErrorCodePropertyFailure(Exception):
    @property
    def sqlite_errorcode(self):
        raise RuntimeError("synthetic sqlite_errorcode property failure")


def test_sqlite_error_code_property_failure_preserves_failed_result_and_logs_null(
    tmp_path: Path,
):
    from automation.inago.cli import _append_production_log

    def fail_primary(_connection, _context, _on_draft_commit):
        raise SqliteErrorCodePropertyFailure("synthetic primary failure")

    result = run_daily_execution_core(
        _ports(tmp_path / "pipeline", run_daily=fail_primary),
        clock=FakeClock(datetime.fromisoformat("2026-10-20T10:00:00+09:00")),
    )

    assert result["daily_status"] == "failed"
    assert result["daily_reason"] == "SqliteErrorCodePropertyFailure"
    assert result["run_id"] is None
    assert result["committed_count"] == 0
    diagnostic = result["primary_error"]["diagnostic"]
    assert diagnostic["exception_class"] == "SqliteErrorCodePropertyFailure"
    assert diagnostic["error_code_number"] is None
    assert diagnostic["operation"] == "daily_primary_failure"

    _append_production_log(
        tmp_path / "l01",
        mode="daily",
        status="failed",
        reason="synthetic_failure",
        diagnostics={
            "daily_status": result["daily_status"],
            "daily_reason": result["daily_reason"],
            "committed_count": result["committed_count"],
            "primary_error": result["primary_error"],
            "secondary_errors": result["secondary_errors"],
        },
    )
    record_path = tmp_path / "l01" / "logs" / "production-cli.jsonl"
    raw = record_path.read_text(encoding="utf-8")
    record = json.loads(raw.splitlines()[0])
    persisted = record["diagnostics"]["primary_error"]["diagnostic"]
    assert persisted["error_code_number"] is None
    assert persisted["exception_class"] == "SqliteErrorCodePropertyFailure"
    assert "synthetic sqlite_errorcode property failure" not in raw
    assert "synthetic primary failure" not in raw
