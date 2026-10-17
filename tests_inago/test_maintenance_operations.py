"""Isolated contracts for maintenance watchdog and non-recursive recovery."""

from __future__ import annotations

import importlib
import sqlite3
from datetime import timedelta

import pytest

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
