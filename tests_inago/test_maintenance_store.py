"""Contract cases for the isolated maintenance state store."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from automation.inago.maintenance.store import LeaseBusyError
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
)


def test_c06_contract(tmp_path):
    store = make_store(tmp_path)

    def notify_same_launch():
        return store.record_daily_check(
            source_launch_id="launch-same",
            source_run_id=None,
            target_date="2026-10-17",
            check_status="abnormal",
            observed_at=BASE_TIME.isoformat(),
            reason_code="daily_error",
            incident={
                "dedupe_key": "daily:component-a:error-a",
                "component": "daily_pipeline",
                "event_code": "daily_error",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        same_launch_results = list(
            executor.map(lambda _index: notify_same_launch(), range(2))
        )

    assert len({result.check_id for result in same_launch_results}) == 1
    assert sum(result.created for result in same_launch_results) == 1
    assert len({result.incident_id for result in same_launch_results}) == 1

    other_launch = store.record_daily_check(
        source_launch_id="launch-other",
        source_run_id="run-other",
        target_date="2026-10-17",
        check_status="abnormal",
        observed_at=(BASE_TIME + timedelta(seconds=1)).isoformat(),
        reason_code="daily_error",
        incident={
            "dedupe_key": "daily:component-a:error-a",
            "component": "daily_pipeline",
            "event_code": "daily_error",
        },
    )
    assert other_launch.created
    assert other_launch.check_id != same_launch_results[0].check_id
    assert other_launch.incident_id == same_launch_results[0].incident_id
    counts = store.counts()
    assert counts["daily_check"] == 2
    assert counts["incident"] == 1
    assert counts["incident_run"] == 2

    failing_store = make_store(tmp_path / "atomic")
    with sqlite3.connect(failing_store.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_incident_insert
            BEFORE INSERT ON incident
            BEGIN SELECT RAISE(ABORT, 'injected transaction failure'); END;
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        failing_store.record_daily_check(
            source_launch_id="launch-atomic",
            source_run_id=None,
            target_date="2026-10-17",
            check_status="abnormal",
            observed_at=BASE_TIME.isoformat(),
            reason_code="daily_error",
            incident={
                "dedupe_key": "daily:component-b:error-b",
                "component": "daily_pipeline",
                "event_code": "daily_error",
            },
        )
    rolled_back = failing_store.counts()
    assert rolled_back["daily_check"] == 0
    assert rolled_back["incident"] == 0
    assert rolled_back["incident_run"] == 0


def test_c07_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    assert job.started

    first_lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=4101,
        owner_start_time="2026-10-17T04:00:00+00:00",
        now=BASE_TIME,
        ttl_seconds=30,
    )
    completed = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=first_lease,
        started_at=BASE_TIME.isoformat(),
    )
    store.complete_step_attempt(
        attempt_id=completed.attempt_id,
        lease=first_lease,
        result_version="investigation-v1",
        completed_at=(BASE_TIME + timedelta(seconds=2)).isoformat(),
    )
    interrupted = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="implementation",
        sequence=2,
        lease=first_lease,
        started_at=(BASE_TIME + timedelta(seconds=3)).isoformat(),
    )

    with pytest.raises(LeaseBusyError):
        store.acquire_lease(
            lease_key="maintenance-worker",
            owner_id="worker-b",
            owner_pid=4102,
            owner_start_time="2026-10-17T04:00:04+00:00",
            now=BASE_TIME + timedelta(seconds=4),
            ttl_seconds=30,
            prior_owner_observation="unknown",
        )
    with pytest.raises(LeaseBusyError):
        store.acquire_lease(
            lease_key="maintenance-worker",
            owner_id="worker-b",
            owner_pid=4102,
            owner_start_time="2026-10-17T04:00:34+00:00",
            now=BASE_TIME + timedelta(seconds=34),
            ttl_seconds=30,
            prior_owner_observation="alive",
        )

    resumed_lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-b",
        owner_pid=4102,
        owner_start_time="2026-10-17T04:00:34+00:00",
        now=BASE_TIME + timedelta(seconds=34),
        ttl_seconds=30,
        prior_owner_observation="dead",
    )
    completed_again = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=resumed_lease,
        started_at=(BASE_TIME + timedelta(seconds=35)).isoformat(),
    )
    assert completed_again.reused
    assert completed_again.attempt_id == completed.attempt_id

    resumed_step = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="implementation",
        sequence=2,
        lease=resumed_lease,
        started_at=(BASE_TIME + timedelta(seconds=36)).isoformat(),
    )
    assert not resumed_step.reused
    assert resumed_step.attempt_no == interrupted.attempt_no + 1
    counts = store.counts()
    assert counts["step_attempt"] == 3
    assert store.get_step_attempt(interrupted.attempt_id).status == "interrupted"
    assert store.get_step_attempt(completed.attempt_id).status == "complete"


def test_c53_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    release = store.create_release(
        release_id="release-gated",
        author_id="implementation-agent",
        base_commit="base-1",
        result_commit="commit-1",
        result_version="result-v1",
        config_version="settings-v1",
        required_case_ids=("case-a", "case-b"),
        created_at=BASE_TIME.isoformat(),
    )

    blocked = store.evaluate_release_gate(
        release_id=release.release_id,
        evaluated_at=(BASE_TIME + timedelta(seconds=1)).isoformat(),
    )
    assert blocked.status == "blocked"
    denied = store.start_fix_job(
        incident_id=incident_id,
        release_id=release.release_id,
        job_id="blocked-job",
        started_at=(BASE_TIME + timedelta(seconds=2)).isoformat(),
    )
    assert not denied.started
    assert store.counts()["maintenance_job"] == 0

    store.record_test_result(
        release_id=release.release_id,
        case_id="case-a",
        attempt=1,
        result_version="result-v1",
        status="FAIL",
        recorded_at=(BASE_TIME + timedelta(seconds=3)).isoformat(),
    )
    store.record_test_result(
        release_id=release.release_id,
        case_id="case-b",
        attempt=1,
        result_version="result-v1",
        status="SKIP",
        recorded_at=(BASE_TIME + timedelta(seconds=3)).isoformat(),
    )
    assert (
        store.evaluate_release_gate(
            release_id=release.release_id,
            evaluated_at=(BASE_TIME + timedelta(seconds=4)).isoformat(),
        ).status
        == "blocked"
    )

    store.record_test_result(
        release_id=release.release_id,
        case_id="case-a",
        attempt=2,
        result_version="result-v1",
        status="PASS",
        recorded_at=(BASE_TIME + timedelta(seconds=5)).isoformat(),
    )
    store.record_test_result(
        release_id=release.release_id,
        case_id="case-b",
        attempt=2,
        result_version="old-version",
        status="PASS",
        recorded_at=(BASE_TIME + timedelta(seconds=5)).isoformat(),
    )
    assert (
        store.evaluate_release_gate(
            release_id=release.release_id,
            evaluated_at=(BASE_TIME + timedelta(seconds=6)).isoformat(),
        ).status
        == "blocked"
    )

    store.record_test_result(
        release_id=release.release_id,
        case_id="case-b",
        attempt=3,
        result_version="result-v1",
        status="PASS",
        recorded_at=(BASE_TIME + timedelta(seconds=7)).isoformat(),
    )
    store.record_release_review(
        release_id=release.release_id,
        reviewer_id="independent-reviewer",
        reviewed_version="old-version",
        status="approved",
        reviewed_at=(BASE_TIME + timedelta(seconds=8)).isoformat(),
    )
    store.record_deployment(
        release_id=release.release_id,
        result_commit="other-commit",
        config_version="settings-v1",
        status="deployed",
        deployed_at=(BASE_TIME + timedelta(seconds=9)).isoformat(),
    )
    assert (
        store.evaluate_release_gate(
            release_id=release.release_id,
            evaluated_at=(BASE_TIME + timedelta(seconds=10)).isoformat(),
        ).status
        == "blocked"
    )

    store.record_release_review(
        release_id=release.release_id,
        reviewer_id="independent-reviewer",
        reviewed_version="result-v1",
        status="approved",
        reviewed_at=(BASE_TIME + timedelta(seconds=11)).isoformat(),
    )
    store.record_deployment(
        release_id=release.release_id,
        result_commit="commit-1",
        config_version="wrong-settings",
        status="deployed",
        deployed_at=(BASE_TIME + timedelta(seconds=12)).isoformat(),
    )
    assert (
        store.evaluate_release_gate(
            release_id=release.release_id,
            evaluated_at=(BASE_TIME + timedelta(seconds=13)).isoformat(),
        ).status
        == "blocked"
    )

    store.record_deployment(
        release_id=release.release_id,
        result_commit="commit-1",
        config_version="settings-v1",
        status="deployed",
        deployed_at=(BASE_TIME + timedelta(seconds=14)).isoformat(),
    )
    gate = store.evaluate_release_gate(
        release_id=release.release_id,
        evaluated_at=(BASE_TIME + timedelta(seconds=15)).isoformat(),
    )
    assert gate.status == "ready"
    started = store.start_fix_job(
        incident_id=incident_id,
        release_id=release.release_id,
        job_id="ready-job",
        started_at=(BASE_TIME + timedelta(seconds=16)).isoformat(),
    )
    assert started.started
    assert store.counts()["maintenance_job"] == 1

    fake = store.create_release(
        release_id="release-fake-ready",
        author_id="implementation-agent",
        base_commit="base-1",
        result_commit="commit-fake",
        result_version="fake-v1",
        config_version="settings-v1",
        required_case_ids=("case-required",),
        created_at=(BASE_TIME + timedelta(seconds=17)).isoformat(),
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "INSERT INTO release_gate (release_id, status, reasons_json, evaluated_at) "
            "VALUES (?, 'ready', '[]', ?)",
            (fake.release_id, (BASE_TIME + timedelta(seconds=18)).isoformat()),
        )
    fake_denied = store.start_fix_job(
        incident_id=incident_id,
        release_id=fake.release_id,
        job_id="fake-ready-job",
        started_at=(BASE_TIME + timedelta(seconds=19)).isoformat(),
    )
    assert not fake_denied.started
    assert store.counts()["maintenance_job"] == 1
