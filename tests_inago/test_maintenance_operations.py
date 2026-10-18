"""Isolated contracts for maintenance watchdog and non-recursive recovery."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from automation.inago.maintenance.store import IdempotencyConflictError
from automation.inago.maintenance import diagnostic_trace
from automation.inago.maintenance.diagnostic_trace import (
    MaintenanceAuditReceiptError,
    MaintenanceDiagnosticPolicy,
    MaintenanceDiagnosticTrace,
    read_diagnostic_events,
    run_maintenance_retention,
)
from automation.inago.maintenance.evidence import (
    EvidenceSource,
    collect_evidence,
    write_evidence_bundle,
)
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
)


def test_c47_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-c47",
        owner_pid=4701,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=10),
        ttl_seconds=60,
    )
    attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=11),
    )
    finalized = store.finalize_job(
        job_id=job.job_id,
        final_status="failed",
        lease=lease,
        finalized_at=BASE_TIME + timedelta(seconds=12),
        reason_code="worker_stage_failed",
    )
    assert finalized.status == "failed"

    try:
        watchdog = importlib.import_module("automation.inago.maintenance.watchdog")
    except ModuleNotFoundError as exc:
        pytest.fail(f"watchdog module unavailable: {exc.name}")

    result = watchdog.run_watchdog_once(
        store,
        now=BASE_TIME + timedelta(minutes=1),
        occurrences=(),
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-c47",
        owner_pid=4702,
        owner_start_time=(BASE_TIME + timedelta(minutes=1)).isoformat(),
    )

    connection = store._connect()
    try:
        job_row = connection.execute(
            "SELECT status, resume_count FROM maintenance_job WHERE job_id = ?",
            (job.job_id,),
        ).fetchone()
    finally:
        connection.close()

    assert result.queued_source_launch_ids == ()
    assert job_row["status"] == "failed"
    assert job_row["resume_count"] == 0
    assert store.get_step_attempt(attempt.attempt_id).status == "failed"
    assert store.counts()["maintenance_job"] == 1
    assert store.counts()["daily_check"] == 1
    assert store.counts()["watchdog_queue"] == 0


def test_c18_contract(tmp_path):
    now = datetime(2026, 10, 18, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    data_root = tmp_path / "runtime"
    data_root.mkdir()
    source = data_root / "daily" / "L02.jsonl"
    source.parent.mkdir()
    source_bytes = (
        json.dumps(
            {
                "event": "daily_complete",
                "run_id": "run-c18",
                "timestamp": "2026-10-18T10:30:00+09:00",
                "status": "complete",
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    source.write_bytes(source_bytes)

    known_expiry = "2027-10-18T12:00:00+09:00"
    collected = collect_evidence(
        source_root=data_root,
        sources=(
            EvidenceSource(
                "L02",
                "daily-trace-c18",
                "daily/L02.jsonl",
                "jsonl",
                retention_status="known",
                expires_at=known_expiry,
                present_at_discovery=True,
            ),
        ),
        target_date="2026-10-18",
        captured_at="2026-10-18T11:00:00+09:00",
        correlation={"run_id": "run-c18"},
    )
    artifact = collected.manifest["artifacts"][0]
    assert artifact["source_expires_at_utc"] == "2027-10-18T03:00:00Z"
    assert artifact["copy_expires_at_utc"] == "2027-01-16T02:00:00Z"

    no_expiry = collect_evidence(
        source_root=data_root,
        sources=(
            EvidenceSource(
                "L02", "daily-trace-90d", "daily/L02.jsonl", "jsonl"
            ),
        ),
        target_date="2026-10-18",
        captured_at="2026-10-18T11:00:00+09:00",
        correlation={"run_id": "run-c18"},
    )
    no_expiry_copy = no_expiry.manifest["artifacts"][0]["copy_expires_at_utc"]
    assert no_expiry_copy == "2027-01-16T02:00:00Z"

    legacy_root = tmp_path / "legacy-runtime"
    legacy_source = legacy_root / "daily" / "L02.jsonl"
    legacy_source.parent.mkdir(parents=True)
    legacy_source.write_bytes(source_bytes)
    legacy = collect_evidence(
        source_root=legacy_root,
        sources=(
            EvidenceSource(
                "L02",
                "legacy-daily-trace-c18",
                "daily/L02.jsonl",
                "jsonl",
                retention_status="known",
                expires_at=known_expiry,
                present_at_discovery=True,
            ),
        ),
        target_date="2026-10-18",
        captured_at="2026-10-18T11:00:00+09:00",
        correlation={"run_id": "run-c18"},
    )
    legacy.manifest["artifacts"][0].pop("copy_expires_at_utc")
    legacy_bundle = (
        legacy_root
        / "maintenance"
        / "evidence"
        / "2026-10-18"
        / "job-legacy-c18"
        / "attempt-legacy-c18"
    )
    legacy_bundle.parent.mkdir(parents=True)
    write_evidence_bundle(legacy_bundle, legacy)
    legacy_copy = legacy_bundle / legacy.manifest["artifacts"][0]["artifact_ref"]
    legacy_retention = run_maintenance_retention(
        legacy_root,
        now=datetime(2027, 1, 17, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )
    assert legacy_retention.expired_evidence_files == 1
    assert not legacy_copy.exists()
    assert legacy_source.read_bytes() == source_bytes

    with pytest.raises(diagnostic_trace.MaintenanceDiagnosticError):
        MaintenanceDiagnosticTrace(data_root, maintenance_id="run.part-0001")
    bundle = data_root / "maintenance" / "evidence" / "2026-10-18" / "job-c18" / "attempt-c18"
    bundle.parent.mkdir(parents=True)
    write_evidence_bundle(bundle, collected)
    artifact_path = bundle / artifact["artifact_ref"]
    assert artifact_path.is_file()

    expired_at = now - timedelta(days=90)
    expired_log = MaintenanceDiagnosticTrace(
        data_root, maintenance_id="expired-log-c18"
    ).write(
        event="stage_completed",
        stage="cli",
        status="complete",
        at=expired_at,
    )
    assert expired_log.status == "written"
    quota_policy = MaintenanceDiagnosticPolicy(
        max_file_bytes=1024,
        max_files_per_day=2,
        max_event_bytes=1024,
    )
    quota_writer = MaintenanceDiagnosticTrace(
        data_root, maintenance_id="quota-log-c18", policy=quota_policy
    )
    quota_results = [
        quota_writer.write(
            event="stage_started",
            stage="cli",
            status="started",
            at=now,
            count=index,
        )
        for index in range(20)
    ]
    assert any(
        result.status == "unavailable" and result.reason_code == "daily_file_quota"
        for result in quota_results
    )
    quota_files = list(
        (data_root / "maintenance" / "logs" / now.date().isoformat()).glob(
            "quota-log-c18*.jsonl"
        )
    )
    assert len(quota_files) == 2
    assert all(read_diagnostic_events(path, policy=quota_policy) for path in quota_files)

    old_log_stage = (
        data_root
        / "maintenance"
        / "logs"
        / now.date().isoformat()
        / f".maintenance-log-stage-{uuid4().hex}.tmp"
    )
    old_log_stage.write_bytes(b"incomplete")
    old_time = (now - timedelta(days=2)).timestamp()
    os.utime(old_log_stage, (old_time, old_time))

    orphan = (
        data_root
        / "maintenance"
        / "evidence"
        / "2026-10-17"
        / "job-orphan-c18"
        / "attempt-orphan-c18"
        / f".maintenance-evidence-stage-{uuid4().hex}"
    )
    orphan.mkdir(parents=True)
    (orphan / "partial.jsonl").write_text("partial", encoding="utf-8")
    os.utime(orphan, (old_time, old_time))
    unrelated = orphan.parent / ".unknown-stage-c18"
    unrelated.mkdir()

    outcome = run_maintenance_retention(data_root, now=now)
    assert outcome.status == "complete", outcome
    assert outcome.expired_log_files >= 2
    assert outcome.expired_evidence_files == 0
    assert outcome.recovered_orphan_stages == 1
    assert artifact_path.exists()
    assert not orphan.exists()
    assert unrelated.is_dir()
    assert source.read_bytes() == source_bytes
    updated_manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert updated_manifest["artifacts"][0]["status"] == "collected"


def test_c58_contract(tmp_path, monkeypatch):
    now = datetime(2026, 10, 18, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    trace = MaintenanceDiagnosticTrace(tmp_path / "runtime", maintenance_id="partial-c58")
    first = trace.write(
        event="stage_completed", stage="cli", status="complete", at=now
    )
    assert first.status == "written"
    assert first.path is not None
    original_bytes = first.path.read_bytes()

    original_write = diagnostic_trace.os.write
    calls = 0

    def partial_then_fail(descriptor, content):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, content[: max(1, len(content) // 2)])
        raise OSError("injected partial write")

    monkeypatch.setattr(diagnostic_trace.os, "write", partial_then_fail)
    partial = trace.write(
        event="stage_started", stage="repair", status="started", at=now
    )
    assert partial.status == "unavailable"
    assert first.path.read_bytes() == original_bytes
    assert len(read_diagnostic_events(first.path)) == 1
    assert not list(first.path.parent.glob(".maintenance-log-stage-*.tmp"))

    monkeypatch.setattr(diagnostic_trace.os, "write", original_write)
    fsync_trace = MaintenanceDiagnosticTrace(
        tmp_path / "runtime", maintenance_id="fsync-c58"
    )
    baseline = fsync_trace.write(
        event="stage_completed", stage="cli", status="complete", at=now
    )
    assert baseline.status == "written"
    assert baseline.path is not None
    fsync_baseline = baseline.path.read_bytes()

    def fail_fsync(_descriptor):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(diagnostic_trace.os, "fsync", fail_fsync)
    failed = fsync_trace.write(
        event="maintenance_finished", stage="cli", status="complete", at=now
    )
    assert failed.status == "unavailable"
    assert baseline.path.read_bytes() == fsync_baseline
    assert len(read_diagnostic_events(baseline.path)) == 1
    assert not list(baseline.path.parent.glob(".maintenance-log-stage-*.tmp"))


    class ReceiptTrace:
        def __init__(self):
            self.calls = 0

        def require_written(self, **_event):
            self.calls += 1
            if self.calls == 2:
                raise diagnostic_trace.MaintenanceDiagnosticError(
                    "diagnostic_log_unavailable"
                )
            return baseline.path

    class MutatingProbe:
        _diagnostic_trace = ReceiptTrace()

        @diagnostic_trace.audit_maintenance_stage("incident")
        def operation(self):
            return {"status": "complete", "job_id": "job-c58"}

    with pytest.raises(MaintenanceAuditReceiptError) as receipt_error:
        MutatingProbe().operation()
    assert receipt_error.value.operation_returned is True
    assert receipt_error.value.operation_result == {
        "status": "complete",
        "job_id": "job-c58",
    }
    assert receipt_error.value.state_reconciliation_required is True


def test_watchdog_lease_with_unknown_owner_blocks_without_queueing(tmp_path):
    store = make_store(tmp_path)
    store.acquire_lease(
        lease_key="daily-watchdog",
        owner_id="watchdog-existing",
        owner_pid=4801,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME,
        ttl_seconds=300,
    )
    watchdog = importlib.import_module("automation.inago.maintenance.watchdog")

    result = watchdog.run_watchdog_once(
        store,
        now=BASE_TIME + timedelta(minutes=1),
        occurrences=(),
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-contender",
        owner_pid=4802,
        owner_start_time=(BASE_TIME + timedelta(minutes=1)).isoformat(),
    )

    assert result.lease_acquired is False
    assert store.counts()["watchdog_queue"] == 0
    assert store.get_lease("daily-watchdog").owner_id == "watchdog-existing"


def test_c13_scheduler_reader_uses_task_boundaries_without_history(monkeypatch):
    from types import SimpleNamespace

    watchdog = importlib.import_module("automation.inago.maintenance.watchdog")
    daily_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
      <Triggers><CalendarTrigger><StartBoundary>2026-10-17T13:00:00+09:00</StartBoundary>
        <Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      </CalendarTrigger></Triggers><Settings><Enabled>true</Enabled></Settings>
    </Task>"""
    watchdog_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
      <Triggers><CalendarTrigger><StartBoundary>2026-10-17T00:00:00+09:00</StartBoundary>
        <Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      </CalendarTrigger></Triggers><Settings><Enabled>true</Enabled></Settings>
    </Task>"""

    def fake_run(args, **_kwargs):
        payload = daily_xml if "RakutenRoomInagoDaily" in args else watchdog_xml
        return SimpleNamespace(returncode=0, stdout=payload)

    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)
    occurrences = watchdog.read_daily_scheduler_occurrences(
        now=BASE_TIME.replace(hour=4, minute=10)
    )

    assert len(occurrences) == 1
    assert occurrences[0].target_date == "2026-10-17"
    assert occurrences[0].scheduled_at.isoformat() == "2026-10-17T13:00:00+09:00"
    assert occurrences[0].history_available is False


def test_c13_scheduler_reader_uses_cursor_and_open_dates(monkeypatch):
    from types import SimpleNamespace

    watchdog = importlib.import_module("automation.inago.maintenance.watchdog")
    daily_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
      <Triggers><CalendarTrigger><StartBoundary>2026-10-18T13:00:00+09:00</StartBoundary>
        <Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      </CalendarTrigger></Triggers><Settings><Enabled>true</Enabled></Settings>
    </Task>"""
    watchdog_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
      <Triggers><CalendarTrigger><StartBoundary>2026-10-01T00:00:00+09:00</StartBoundary>
        <Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      </CalendarTrigger></Triggers><Settings><Enabled>true</Enabled></Settings>
    </Task>"""

    def fake_run(args, **_kwargs):
        payload = daily_xml if "RakutenRoomInagoDaily" in args else watchdog_xml
        return SimpleNamespace(returncode=0, stdout=payload)

    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)
    occurrences = watchdog.read_daily_scheduler_occurrences(
        now=BASE_TIME.replace(hour=4, minute=10),
        after_date="2026-10-15",
        include_dates=("2026-10-03",),
    )

    assert [item.target_date for item in occurrences] == [
        "2026-10-03",
    ]


def test_watchdog_queue_rejects_launch_from_another_target_date(tmp_path):
    store = make_store(tmp_path)
    store.record_daily_launch_start(
        source_launch_id="launch-queue-date",
        target_date="2026-10-17",
        owner_pid=4803,
        owner_start_time=BASE_TIME.isoformat(),
        started_at=BASE_TIME,
    )

    with pytest.raises(IdempotencyConflictError, match="watchdog_launch_date_conflict"):
        store.record_watchdog_queue(
            occurrence_key="scheduled:daily:2026-10-18:20261018T040000000000Z",
            target_date="2026-10-18",
            reason_code="daily_launch_linked",
            source_launch_id="launch-queue-date",
            status="awaiting_completion",
            now=BASE_TIME,
        )


def test_watchdog_queue_rejects_duplicate_source_launch(tmp_path):
    store = make_store(tmp_path)
    store.record_daily_launch_start(
        source_launch_id="launch-queue-unique",
        target_date="2026-10-17",
        owner_pid=4804,
        owner_start_time=BASE_TIME.isoformat(),
        started_at=BASE_TIME,
    )
    store.record_watchdog_queue(
        occurrence_key="scheduled:daily:2026-10-17:20261017T040000000000Z",
        target_date="2026-10-17",
        reason_code="daily_launch_linked",
        source_launch_id="launch-queue-unique",
        status="awaiting_completion",
        now=BASE_TIME,
    )

    with pytest.raises(
        IdempotencyConflictError, match="watchdog_launch_queue_conflict"
    ):
        store.record_watchdog_queue(
            occurrence_key="launch:launch-queue-unique",
            target_date="2026-10-17",
            reason_code="daily_launch_terminated_without_notice",
            source_launch_id="launch-queue-unique",
            status="unknown",
            now=BASE_TIME,
        )

    assert store.counts()["watchdog_queue"] == 1


def test_watchdog_store_lists_records_without_per_row_lookups(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    for suffix, pid in (("a", 4811), ("b", 4812)):
        launch_id = f"launch-bulk-{suffix}"
        store.record_daily_launch_start(
            source_launch_id=launch_id,
            target_date="2026-10-17",
            owner_pid=pid,
            owner_start_time=BASE_TIME.isoformat(),
            started_at=BASE_TIME,
        )
        store.record_watchdog_queue(
            occurrence_key=f"launch:{launch_id}",
            target_date="2026-10-17",
            reason_code="daily_launch_terminated_without_notice",
            source_launch_id=launch_id,
            status="unknown",
            now=BASE_TIME,
        )

    def unexpected_lookup(*_args, **_kwargs):
        raise AssertionError("list methods must map rows without per-record lookups")

    monkeypatch.setattr(store, "get_daily_launch", unexpected_lookup)
    monkeypatch.setattr(store, "get_watchdog_queue", unexpected_lookup)

    assert len(store.list_daily_launches(target_date="2026-10-17")) == 2
    assert len(store.list_watchdog_queue(open_only=True)) == 2
    assert store.get_watchdog_queue_by_occurrence("launch:launch-bulk-a") is not None
    assert store.get_watchdog_queue_by_source_launch_id("launch-bulk-b") is not None


def test_watchdog_store_lists_date_ranges(tmp_path):
    store = make_store(tmp_path)
    for suffix, target_date in (
        ("a", "2026-10-17"),
        ("b", "2026-10-18"),
        ("c", "2026-10-19"),
    ):
        launch_id = f"launch-range-{suffix}"
        store.record_daily_launch_start(
            source_launch_id=launch_id,
            target_date=target_date,
            owner_pid=4820 + ord(suffix),
            owner_start_time=BASE_TIME.isoformat(),
            started_at=BASE_TIME,
        )
        store.record_watchdog_queue(
            occurrence_key=f"launch:{launch_id}",
            target_date=target_date,
            reason_code="daily_launch_terminated_without_notice",
            source_launch_id=launch_id,
            status="unknown",
            now=BASE_TIME,
        )

    launches = store.list_daily_launches(
        target_date_from="2026-10-17", target_date_to="2026-10-18"
    )
    queues = store.list_watchdog_queue(
        target_date_from="2026-10-17", target_date_to="2026-10-18"
    )

    assert {item.target_date for item in launches} == {"2026-10-17", "2026-10-18"}
    assert {item.target_date for item in queues} == {"2026-10-17", "2026-10-18"}


def test_watchdog_batches_occurrence_history_reads(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    watchdog = importlib.import_module("automation.inago.maintenance.watchdog")
    occurrences = tuple(
        watchdog.SchedulerOccurrence(
            task_key="daily",
            target_date=f"2026-10-{day:02d}",
            scheduled_at=BASE_TIME + timedelta(days=day - 17),
            enabled=True,
            history_available=False,
            last_run_time=None,
            last_result=None,
        )
        for day in (17, 18, 19)
    )
    calls = {"launches": 0, "queues": 0}
    list_launches = store.list_daily_launches
    list_queues = store.list_watchdog_queue

    def count_launch_reads(**kwargs):
        calls["launches"] += 1
        return list_launches(**kwargs)

    def count_queue_reads(**kwargs):
        calls["queues"] += 1
        return list_queues(**kwargs)

    def unexpected_lookup(*_args, **_kwargs):
        raise AssertionError("occurrence processing must use the batched row maps")

    monkeypatch.setattr(store, "list_daily_launches", count_launch_reads)
    monkeypatch.setattr(store, "list_watchdog_queue", count_queue_reads)
    monkeypatch.setattr(store, "get_watchdog_queue_by_occurrence", unexpected_lookup)
    monkeypatch.setattr(
        store, "get_watchdog_queue_by_source_launch_id", unexpected_lookup
    )

    result = watchdog.run_watchdog_once(
        store,
        now=BASE_TIME + timedelta(days=3),
        occurrences=occurrences,
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-batched-history",
        owner_pid=4830,
        owner_start_time=(BASE_TIME + timedelta(days=3)).isoformat(),
        start_grace=timedelta(minutes=5),
    )

    assert len(result.queued_queue_ids) == 3
    assert calls == {"launches": 2, "queues": 2}
    assert store.counts()["watchdog_queue"] == 3


def test_watchdog_cursor_limits_database_reads_to_new_and_pending_dates(
    tmp_path, monkeypatch
):
    store = make_store(tmp_path)
    watchdog = importlib.import_module("automation.inago.maintenance.watchdog")
    store.advance_watchdog_cursor(
        reconciled_through_date="2026-10-17", updated_at=BASE_TIME
    )
    occurrences = tuple(
        watchdog.SchedulerOccurrence(
            task_key="daily",
            target_date=f"2026-10-{day:02d}",
            scheduled_at=BASE_TIME + timedelta(days=day - 17),
            enabled=True,
            history_available=False,
            last_run_time=None,
            last_result=None,
        )
        for day in (1, 18, 19)
    )
    selected_dates = []
    list_launches = store.list_daily_launches
    list_queues = store.list_watchdog_queue

    def record_launch_reads(**kwargs):
        if kwargs.get("target_dates") is not None:
            selected_dates.append(set(kwargs["target_dates"]))
        return list_launches(**kwargs)

    def record_queue_reads(**kwargs):
        if kwargs.get("target_dates") is not None:
            selected_dates.append(set(kwargs["target_dates"]))
        return list_queues(**kwargs)

    monkeypatch.setattr(store, "list_daily_launches", record_launch_reads)
    monkeypatch.setattr(store, "list_watchdog_queue", record_queue_reads)

    result = watchdog.run_watchdog_once(
        store,
        now=BASE_TIME + timedelta(days=2),
        occurrences=occurrences,
        process_probe=lambda _pid: watchdog.ProcessObservation("unknown", None),
        owner_id="watchdog-cursor-bounded",
        owner_pid=4831,
        owner_start_time=(BASE_TIME + timedelta(days=2)).isoformat(),
        start_grace=timedelta(minutes=5),
    )

    assert len(result.queued_queue_ids) == 1
    assert selected_dates == [{"2026-10-18", "2026-10-19"}] * 2
    assert store.get_watchdog_cursor() == "2026-10-18"
    assert store.counts()["watchdog_queue"] == 1
