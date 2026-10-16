"""R03 regression cases for common trace correlation and protection."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta
import importlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from types import SimpleNamespace

from automation.inago.clock import FakeClock
from automation.inago.repository import open_database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _trace_api():
    return importlib.import_module("automation.inago.trace")


def _write_cross_process_quota_probe(data_root: str, run_id: str) -> str:
    trace = _trace_api()
    writer = trace.TraceWriter(
        data_root,
        run_id,
        "2026-01-01",
        policy=trace.TracePolicy(max_files_per_root=2),
        clock=FakeClock(datetime.fromisoformat("2026-01-01T12:00:00+09:00")),
    )
    event = trace.make_trace_event(
        run_id=run_id,
        target_date="2026-01-01",
        component="orchestrator",
        operation="cross_process_quota_probe",
        phase="status",
        clock=FakeClock(datetime.fromisoformat("2026-01-01T12:00:00+09:00")),
    )
    return writer.write(event).status


def test_n03(tmp_path: Path) -> None:
    """Keep active widget wait and its parent/request correlation on timeout."""

    trace = _trace_api()
    clock = FakeClock(datetime.fromisoformat("2026-10-16T16:00:00+09:00"))
    writer = trace.TraceWriter(
        tmp_path / "data",
        "RUN-N03-TRACE",
        "2026-10-16",
        clock=clock,
    )
    run_root = trace.TraceSession(
        writer,
        component="orchestrator",
        request_id="ROOT-N03-TRACE",
        task_id="daily",
        clock=clock,
    )
    run_root.start(operation="daily_run")
    request = trace.TraceSession(
        writer,
        component="public_source",
        request_id="TR-N03-001",
        job_id="JOB-N03-001",
        task_id="daily",
        attempt=1,
        attempt_limit=5,
        deadline_at="2026-10-16T16:00:20+09:00",
        clock=clock,
    )
    assert request.parent_span_id == run_root.span_id
    request.start(operation="trends_request")
    request.record_stage("trends_request", phase="end", elapsed_ms=100)
    request.record_stage("trends_period_reflected", phase="begin", elapsed_ms=700)
    request.record_stage("trends_period_reflected", phase="end", elapsed_ms=800)
    request.record_stage("trends_top_rows_wait_begin", phase="begin", elapsed_ms=900)

    other = trace.TraceSession(
        writer,
        component="public_source",
        request_id="TR-N03-OTHER",
        job_id="JOB-N03-OTHER",
        task_id="daily",
        attempt=1,
        attempt_limit=5,
        deadline_at="2026-10-16T16:01:00+09:00",
        clock=clock,
    )
    other.start(operation="trends_request")
    other.record_stage("other_job_wait", phase="begin", elapsed_ms=0)

    clock.advance(1.25)
    request.fail(
        TimeoutError("https://trends.example/explore?token=private-value"),
        error_code="source_timeout",
        failed_operation="trends_top_rows_wait_begin",
        retryable=True,
        scope="query",
    )

    snapshot = request.snapshot()
    assert snapshot["active_stage"] == "trends_top_rows_wait_begin"
    assert snapshot["last_completed_stage"] == "trends_period_reflected"
    assert snapshot["remaining_ms"] == 18_750

    events = trace.read_trace_events(writer.path)
    root_event = next(event for event in events if event["span_id"] == run_root.span_id)
    assert root_event["component"] == "orchestrator"
    assert "parent_span_id" not in root_event
    request_events = [
        event for event in events if event.get("request_id") == "TR-N03-001"
    ]
    assert request_events
    assert {event["run_id"] for event in request_events} == {"RUN-N03-TRACE"}
    assert {event["job_id"] for event in request_events} == {"JOB-N03-001"}
    assert len({event["span_id"] for event in request_events}) == 1
    assert {event["parent_span_id"] for event in request_events} == {
        request.parent_span_id
    }
    failed = request_events[-1]
    assert failed["phase"] == "error"
    assert failed["active_stage"] == "trends_top_rows_wait_begin"
    assert failed["last_completed_stage"] == "trends_period_reflected"
    assert failed["elapsed_ms"] == 1250
    assert failed["remaining_ms"] == 18_750
    assert all(event.get("request_id") != "TR-N03-OTHER" for event in request_events)

    live_sources = importlib.import_module("automation.inago.live_sources")
    node_metadata = live_sources.PublicBrowserSource._metadata_events(
        "\n".join(
            json.dumps(event)
            for event in (
                {
                    "event": "public_source_stage",
                    "stage": "trends_top_rows_wait_begin",
                    "elapsed_ms": 900,
                },
                {
                    "event": "public_source_trace_suppressed",
                    "stage": "trace_event_limit",
                    "suppression_marker_count": 1,
                },
            )
        )
    )
    assert [event["stage"] for event in node_metadata] == [
        "trends_top_rows_wait_begin",
        "trace_event_limit",
    ]
    assert node_metadata[-1]["event"] == "public_source_trace_suppressed"

    adapter_writer = trace.TraceWriter(
        tmp_path / "adapter-data",
        "RUN-N03-ADAPTER",
        "2026-10-16",
        clock=clock,
    )
    adapter_session = trace.TraceSession(
        adapter_writer,
        component="public_source",
        request_id="TR-N03-ADAPTER",
        job_id="JOB-N03-ADAPTER",
        clock=clock,
    )
    adapter = object.__new__(live_sources.PublicBrowserSource)
    started_at = clock.now_jst()
    adapter._trace_node_events(
        adapter_session,
        [
            {
                "event": "public_source_stage",
                "stage": "trends_period_reflection_wait_begin",
                "elapsed_ms": 100,
            },
            {
                "event": "public_source_stage",
                "stage": "trends_period_reflected",
                "elapsed_ms": 300,
            },
            {
                "event": "public_source_stage",
                "stage": "trends_top_rows_wait_begin",
                "elapsed_ms": 400,
            },
            {
                "event": "public_source_stage",
                "stage": "trends_top_rows_updated",
                "elapsed_ms": 600,
            },
        ],
        started_at=started_at,
    )
    assert adapter_session.snapshot()["last_completed_stage"] == (
        "trends_top_rows_updated"
    )
    assert adapter_session.snapshot()["active_stage"] is None
    adapter_failure = trace.TraceSession(
        adapter_writer,
        component="public_source",
        request_id="TR-N03-ADAPTER-FAIL",
        job_id="JOB-N03-ADAPTER-FAIL",
        clock=clock,
    )
    adapter._trace_node_events(
        adapter_failure,
        [
            {
                "event": "public_source_stage",
                "stage": "trends_period_reflection_wait_begin",
                "elapsed_ms": 0,
            },
            {
                "event": "public_source_stage",
                "stage": "trends_period_reflected",
                "elapsed_ms": 100,
            },
            {
                "event": "public_source_stage",
                "stage": "trends_top_rows_wait_begin",
                "elapsed_ms": 200,
            },
            {
                "event": "public_source_failure",
                "stage": "trends_top_rows_wait_begin",
                "elapsed_ms": 1450,
                "error_code": "source_timeout",
                "error_class": "TimeoutError",
                "error_kind": "timeout",
            },
        ],
        started_at=started_at,
    )
    adapter_failure_snapshot = adapter_failure.snapshot()
    assert adapter_failure_snapshot["active_stage"] == "trends_top_rows_wait_begin"
    assert adapter_failure_snapshot["last_completed_stage"] == "trends_period_reflected"


def test_n04(tmp_path: Path) -> None:
    """Store primary, cause and cleanup classes while dropping sensitive text."""

    trace = _trace_api()
    clock = FakeClock(datetime.fromisoformat("2026-10-16T16:00:00+09:00"))
    writer = trace.TraceWriter(
        tmp_path / "data",
        "RUN-N04-TRACE",
        "2026-10-16",
        clock=clock,
    )
    request = trace.TraceSession(
        writer,
        component="public_source",
        request_id="ROOM-N04-001",
        job_id="JOB-N04-001",
        deadline_at="2026-10-16T16:02:00+09:00",
        clock=clock,
    )
    request.start(operation="room_detail_request")
    request.record_stage("room_detail_button_click_begin", phase="begin")
    primary = TimeoutError(
        "https://room.example/detail?session=private-session Authorization: Bearer secret-token"
    )
    cause = ValueError("Cookie: auth=private-cookie body=private-product-text")
    primary.__cause__ = cause
    cleanup = OSError("https://room.example/?api_key=private-key")
    request.fail(
        primary,
        error_code="source_timeout",
        failed_operation="room_detail_button_click_begin",
        cleanup_error=cleanup,
        cleanup_error_code="cleanup_failed",
        retryable=True,
        scope="row",
    )

    events = trace.read_trace_events(writer.path)
    failure = events[-1]
    serialized = writer.path.read_text(encoding="utf-8")
    for secret in (
        "private-session",
        "secret-token",
        "private-cookie",
        "private-product-text",
        "private-key",
    ):
        assert secret not in serialized
    assert failure["primary_error_code"] == "source_timeout"
    assert failure["error_class"] == "TimeoutError"
    assert failure["cause_class"] == "ValueError"
    assert failure["cleanup_error_code"] == "cleanup_failed"
    assert failure["cleanup_error_class"] == "OSError"
    assert failure["failed_operation"] == "room_detail_button_click_begin"
    assert failure["scope"] == "row"
    assert "message" not in failure
    assert (
        trace.redact_sensitive_text(
            "https://room.example/?token=private-value Authorization: Bearer another-secret"
        ).find("private-value")
        == -1
    )


def test_n29(tmp_path: Path, monkeypatch) -> None:
    """Bound events, bytes and files; expire at 90 days and preserve DB on I/O failure."""

    trace = _trace_api()
    now = datetime.fromisoformat("2026-01-01T12:00:00+09:00")
    clock = FakeClock(now)
    data_root = tmp_path / "data"
    policy = trace.TracePolicy(
        max_events_per_run=5,
        max_bytes_per_run=8192,
        max_files_per_root=1,
        max_event_bytes=8192,
    )
    writer = trace.TraceWriter(
        data_root,
        "RUN-N29-QUOTA",
        "2026-01-01",
        policy=policy,
        clock=clock,
    )
    session = trace.TraceSession(
        writer,
        component="orchestrator",
        request_id=None,
        deadline_at="2026-01-01T12:01:00+09:00",
        clock=clock,
    )
    session.start(operation="daily_run")
    session.record_stage("daily_run", phase="end")
    session.record_stage("copy_commit", phase="begin")
    session.record_stage("copy_commit", phase="end")
    suppressed = session.record_stage("post_commit_export", phase="begin")
    assert suppressed.status == "suppressed"
    assert session.health == "degraded"
    events = trace.read_trace_events(writer.path)
    assert len(events) == 5
    assert events[-1]["event_kind"] == "trace_suppressed"
    assert events[-1]["suppression_reason"] == "event_limit"
    assert events[-1]["suppression_marker_count"] == 1
    assert events[0]["correlation_status"] == "unresolved"
    assert "request_id" not in events[0]

    database = open_database(SimpleNamespace(data_root=data_root / "db"))
    try:
        database.execute(
            "CREATE TABLE daily_fix_sentinel (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        database.execute(
            "INSERT INTO daily_fix_sentinel (id, value) VALUES (1, 'committed')"
        )
        database.commit()
        before = database.execute("SELECT id, value FROM daily_fix_sentinel").fetchall()

        # The cap is one JSONL file under this root. Expire the existing file at
        # the exact 90-day boundary, then verify a new run can own the slot.
        before_expiry = trace.expire_trace_events(
            data_root,
            now=now + timedelta(days=89),
            policy=trace.TracePolicy(max_files_per_root=1),
        )
        assert before_expiry.expired_events == 0
        at_expiry = trace.expire_trace_events(
            data_root,
            now=now + timedelta(days=90),
            policy=trace.TracePolicy(max_files_per_root=1),
        )
        assert at_expiry.expired_events == 5
        assert at_expiry.removed_files == 1

        failing_writer = trace.TraceWriter(
            data_root,
            "RUN-N29-IOFAIL",
            "2026-01-01",
            policy=trace.TracePolicy(max_files_per_root=1),
            clock=clock,
        )
        append_calls = 0

        def fail_append(path: Path, encoded: bytes) -> None:
            nonlocal append_calls
            append_calls += 1
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as stream:
                stream.write(encoded[:3])
            raise PermissionError("private-path-with-secret=not-recorded")

        monkeypatch.setattr(failing_writer, "_append_payload", fail_append)
        unavailable = failing_writer.write(
            trace.make_trace_event(
                run_id="RUN-N29-IOFAIL",
                target_date="2026-01-01",
                component="orchestrator",
                operation="trace_io_probe",
                phase="status",
                correlation_status="unresolved",
                clock=clock,
            )
        )
        assert unavailable.status == "unavailable"
        assert failing_writer.health == "unavailable"
        assert failing_writer.path.stat().st_size == 3
        repeated = failing_writer.write(
            trace.make_trace_event(
                run_id="RUN-N29-IOFAIL",
                target_date="2026-01-01",
                component="orchestrator",
                operation="trace_io_probe_again",
                phase="status",
                correlation_status="unresolved",
                clock=clock,
            )
        )
        assert repeated.status == "unavailable"
        assert append_calls == 1
        assert failing_writer.path.stat().st_size == 3
        reopened_writer = trace.TraceWriter(
            data_root,
            "RUN-N29-IOFAIL",
            "2026-01-01",
            policy=trace.TracePolicy(max_files_per_root=1),
            clock=clock,
        )
        reopened = reopened_writer.write(
            trace.make_trace_event(
                run_id="RUN-N29-IOFAIL",
                target_date="2026-01-01",
                component="orchestrator",
                operation="trace_io_reopen_probe",
                phase="status",
                correlation_status="unresolved",
                clock=clock,
            )
        )
        assert reopened.status == "unavailable"
        assert reopened_writer.path.stat().st_size == 3
        after = database.execute("SELECT id, value FROM daily_fix_sentinel").fetchall()
        assert after == before
    finally:
        database.close()

    size_policy = trace.TracePolicy(
        max_events_per_run=100,
        max_bytes_per_run=1800,
        max_files_per_root=2,
        max_event_bytes=1200,
        suppression_reserve_bytes=1024,
    )
    size_writer = trace.TraceWriter(
        tmp_path / "size-data",
        "RUN-N29-BYTES",
        "2026-01-01",
        policy=size_policy,
        clock=clock,
    )
    byte_results = [
        size_writer.write(
            trace.make_trace_event(
                run_id="RUN-N29-BYTES",
                target_date="2026-01-01",
                component="orchestrator",
                operation=f"bounded_event_{index}",
                phase="status",
                clock=clock,
            )
        )
        for index in range(10)
    ]
    assert any(result.status == "suppressed" for result in byte_results)
    assert size_writer.path.stat().st_size <= size_policy.max_bytes_per_run

    rewrite_root = tmp_path / "rewrite-data"
    rewrite_clock = FakeClock(now)
    rewrite_writer = trace.TraceWriter(
        rewrite_root, "RUN-N29-REWRITE", "2026-01-01", clock=rewrite_clock
    )
    for operation in ("expired_event",):
        assert (
            rewrite_writer.write(
                trace.make_trace_event(
                    run_id="RUN-N29-REWRITE",
                    target_date="2026-01-01",
                    component="orchestrator",
                    operation=operation,
                    phase="status",
                    clock=rewrite_clock,
                )
            ).status
            == "written"
        )
    rewrite_clock.advance(24 * 60 * 60)
    assert (
        rewrite_writer.write(
            trace.make_trace_event(
                run_id="RUN-N29-REWRITE",
                target_date="2026-01-01",
                component="orchestrator",
                operation="retained_event",
                phase="status",
                clock=rewrite_clock,
            )
        ).status
        == "written"
    )
    rewrite_result = trace.expire_trace_events(
        rewrite_root, now=now + timedelta(days=90)
    )
    assert rewrite_result.expired_events == 1
    assert rewrite_result.rewritten_files == 1
    assert [
        event["operation"] for event in trace.read_trace_events(rewrite_writer.path)
    ] == ["retained_event"]
    if os.name != "nt":
        assert stat.S_IMODE(rewrite_writer.path.stat().st_mode) == 0o600

    failed_rewrite_root = tmp_path / "failed-rewrite-data"
    failed_rewrite_clock = FakeClock(now)
    failed_rewrite_writer = trace.TraceWriter(
        failed_rewrite_root,
        "RUN-N29-REWRITE-FAIL",
        "2026-01-01",
        clock=failed_rewrite_clock,
    )
    for operation in ("expired_event",):
        assert (
            failed_rewrite_writer.write(
                trace.make_trace_event(
                    run_id="RUN-N29-REWRITE-FAIL",
                    target_date="2026-01-01",
                    component="orchestrator",
                    operation=operation,
                    phase="status",
                    clock=failed_rewrite_clock,
                )
            ).status
            == "written"
        )
    failed_rewrite_clock.advance(24 * 60 * 60)
    assert (
        failed_rewrite_writer.write(
            trace.make_trace_event(
                run_id="RUN-N29-REWRITE-FAIL",
                target_date="2026-01-01",
                component="orchestrator",
                operation="retained_event",
                phase="status",
                clock=failed_rewrite_clock,
            )
        ).status
        == "written"
    )

    def fail_replace(_source: Path, _target: Path) -> None:
        raise PermissionError("replace blocked")

    with monkeypatch.context() as patcher:
        patcher.setattr(trace.os, "replace", fail_replace)
        failed_rewrite = trace.expire_trace_events(
            failed_rewrite_root, now=now + timedelta(days=90)
        )
    assert failed_rewrite.expired_events == 0
    assert failed_rewrite.failures == 1
    assert len(trace.read_trace_events(failed_rewrite_writer.path)) == 2

    concurrent_root = tmp_path / "concurrent-data"
    concurrent_policy = trace.TracePolicy(max_files_per_root=2)
    concurrent_writers = [
        trace.TraceWriter(
            concurrent_root,
            f"RUN-N29-C{index}",
            "2026-01-01",
            policy=concurrent_policy,
            clock=clock,
        )
        for index in range(8)
    ]

    def write_concurrent(index: int) -> str:
        writer = concurrent_writers[index]
        event = trace.make_trace_event(
            run_id=writer.run_id,
            target_date="2026-01-01",
            component="orchestrator",
            operation="file_quota_race_probe",
            phase="status",
            clock=clock,
        )
        return writer.write(event).status

    with ThreadPoolExecutor(max_workers=len(concurrent_writers)) as pool:
        concurrent_results = list(
            pool.map(write_concurrent, range(len(concurrent_writers)))
        )
    assert concurrent_results.count("written") == 2
    assert len(list((concurrent_root / "logs" / "traces").rglob("*.jsonl"))) == 2

    cross_process_root = tmp_path / "cross-process-data"
    cross_process_ids = [f"RUN-N29-P{index}" for index in range(8)]
    with ProcessPoolExecutor(max_workers=len(cross_process_ids)) as pool:
        process_results = list(
            pool.map(
                _write_cross_process_quota_probe,
                [str(cross_process_root)] * len(cross_process_ids),
                cross_process_ids,
            )
        )
    assert process_results.count("written") == 2
    assert len(list((cross_process_root / "logs" / "traces").rglob("*.jsonl"))) == 2

    compact_policy = trace.TracePolicy(
        max_events_per_run=2,
        max_bytes_per_run=4096,
        max_event_bytes=1024,
        suppression_reserve_bytes=1024,
    )
    compact_writer = trace.TraceWriter(
        tmp_path / "compact-data",
        "R" * 128,
        "2026-01-01",
        policy=compact_policy,
        clock=clock,
    )
    for event_index in range(2):
        compact_event = trace.make_trace_event(
            run_id="R" * 128,
            target_date="2026-01-01",
            component="orchestrator",
            operation=f"compact_marker_probe_{event_index}",
            phase="status",
            clock=clock,
            correlation_status="complete",
            request_id="Q" * 128,
            job_id="J" * 128,
            root_job_id="B" * 128,
            task_id="T" * 128,
        )
        compact_writer.write(compact_event)
    compact_events = trace.read_trace_events(compact_writer.path, policy=compact_policy)
    compact_marker = compact_events[-1]
    assert compact_marker["event_kind"] == "trace_suppressed"
    assert compact_marker["suppression_marker_count"] == 1
    assert compact_marker["correlation_status"] == "unresolved"
    assert "request_id" not in compact_marker
    assert len(compact_writer.path.read_bytes()) <= compact_policy.max_bytes_per_run
    assert (
        len(
            json.dumps(
                compact_marker, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            + b"\n"
        )
        <= compact_policy.suppression_reserve_bytes
    )

    file_policy = trace.TracePolicy(max_files_per_root=1)
    first = trace.TraceWriter(
        tmp_path / "file-data",
        "RUN-N29-F1",
        "2026-01-01",
        policy=file_policy,
        clock=clock,
    )
    second = trace.TraceWriter(
        tmp_path / "file-data",
        "RUN-N29-F2",
        "2026-01-01",
        policy=file_policy,
        clock=clock,
    )
    event_fields = dict(
        target_date="2026-01-01",
        component="orchestrator",
        operation="file_quota_probe",
        phase="status",
        clock=clock,
    )
    assert (
        first.write(trace.make_trace_event(run_id="RUN-N29-F1", **event_fields)).status
        == "written"
    )
    assert (
        second.write(trace.make_trace_event(run_id="RUN-N29-F2", **event_fields)).status
        == "unavailable"
    )
