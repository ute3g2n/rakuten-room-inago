"""Contract cases for the isolated maintenance state store."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from automation.inago.maintenance.store import (
    IdempotencyConflictError,
    LeaseBusyError,
    MaintenanceStoreError,
)
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
    with pytest.raises(IdempotencyConflictError, match="launch_incident_conflict"):
        store.record_daily_check(
            source_launch_id="launch-same",
            source_run_id=None,
            target_date="2026-10-17",
            check_status="abnormal",
            observed_at=BASE_TIME.isoformat(),
            reason_code="daily_error",
            incident={
                "dedupe_key": "daily:component-a:error-a",
                "component": "daily_pipeline",
                "event_code": "different_daily_error",
            },
        )

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
    with pytest.raises(MaintenanceStoreError, match="step_predecessor_incomplete"):
        store.begin_step_attempt(
            job_id=job.job_id,
            step_key="implementation",
            sequence=2,
            lease=first_lease,
            started_at=BASE_TIME.isoformat(),
        )
    completed = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=first_lease,
        started_at=BASE_TIME.isoformat(),
    )
    with pytest.raises(MaintenanceStoreError, match="step_attempt_already_running"):
        store.begin_step_attempt(
            job_id=job.job_id,
            step_key="investigation",
            sequence=1,
            lease=first_lease,
            started_at=(BASE_TIME + timedelta(seconds=1)).isoformat(),
        )
    store.complete_step_attempt(
        attempt_id=completed.attempt_id,
        lease=first_lease,
        result_version="investigation-v1",
        completed_at=(BASE_TIME + timedelta(seconds=2)).isoformat(),
    )
    with pytest.raises(MaintenanceStoreError, match="step_predecessor_not_approved"):
        store.begin_step_attempt(
            job_id=job.job_id,
            step_key="implementation",
            sequence=2,
            lease=first_lease,
            started_at=(BASE_TIME + timedelta(seconds=3)).isoformat(),
        )
    store.record_step_review(
        attempt_id=completed.attempt_id,
        reviewer_id="independent-reviewer",
        reviewed_version="investigation-v1",
        status="changes_requested",
        reviewed_at=(BASE_TIME + timedelta(seconds=3)).isoformat(),
    )
    retry = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=first_lease,
        started_at=(BASE_TIME + timedelta(seconds=4)).isoformat(),
    )
    assert retry.attempt_no == completed.attempt_no + 1
    store.complete_step_attempt(
        attempt_id=retry.attempt_id,
        lease=first_lease,
        result_version="investigation-v2",
        completed_at=(BASE_TIME + timedelta(seconds=5)).isoformat(),
    )
    store.record_step_review(
        attempt_id=retry.attempt_id,
        reviewer_id="independent-reviewer",
        reviewed_version="investigation-v2",
        status="approved",
        reviewed_at=(BASE_TIME + timedelta(seconds=6)).isoformat(),
    )
    interrupted = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="implementation",
        sequence=2,
        lease=first_lease,
        started_at=(BASE_TIME + timedelta(seconds=7)).isoformat(),
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
    assert completed_again.attempt_id == retry.attempt_id

    resumed_step = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="implementation",
        sequence=2,
        lease=resumed_lease,
        started_at=(BASE_TIME + timedelta(seconds=36)).isoformat(),
    )
    assert not resumed_step.reused
    assert resumed_step.attempt_no == interrupted.attempt_no + 1
    with pytest.raises(MaintenanceStoreError, match="step_attempt_already_running"):
        store.begin_step_attempt(
            job_id=job.job_id,
            step_key="implementation",
            sequence=2,
            lease=resumed_lease,
            started_at=(BASE_TIME + timedelta(seconds=37)).isoformat(),
        )
    counts = store.counts()
    assert counts["step_attempt"] == 4
    assert store.get_step_attempt(interrupted.attempt_id).status == "interrupted"
    assert store.get_step_attempt(completed.attempt_id).status == "complete"
    assert store.get_step_attempt(retry.attempt_id).status == "complete"


def test_c07_job_finalization_releases_active_slot(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    first_job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=4301,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=10),
        ttl_seconds=30,
    )
    attempt = store.begin_step_attempt(
        job_id=first_job.job_id,
        step_key="investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=11),
    )
    store.complete_step_attempt(
        attempt_id=attempt.attempt_id,
        lease=lease,
        result_version="result-v1",
        completed_at=BASE_TIME + timedelta(seconds=12),
    )
    with pytest.raises(MaintenanceStoreError, match="job_steps_not_approved"):
        store.finalize_job(
            job_id=first_job.job_id,
            final_status="complete",
            lease=lease,
            finalized_at=BASE_TIME + timedelta(seconds=13),
        )
    store.record_step_review(
        attempt_id=attempt.attempt_id,
        reviewer_id="independent-reviewer",
        reviewed_version="result-v1",
        status="approved",
        reviewed_at=BASE_TIME + timedelta(seconds=13),
    )
    finalized = store.finalize_job(
        job_id=first_job.job_id,
        final_status="complete",
        lease=lease,
        finalized_at=BASE_TIME + timedelta(seconds=14),
    )
    assert finalized.status == "complete"

    second_job = create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-2",
        result_commit="commit-2",
        result_version="result-v2",
        config_version="settings-v2",
    )
    assert second_job.started


def test_c07_terminal_job_closes_running_attempt(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=4351,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=10),
        ttl_seconds=30,
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
        reason_code="worker_failed",
    )
    assert finalized.status == "failed"
    assert store.get_step_attempt(attempt.attempt_id).status == "failed"
    with pytest.raises(MaintenanceStoreError, match="job_not_active"):
        store.complete_step_attempt(
            attempt_id=attempt.attempt_id,
            lease=lease,
            result_version="late-result",
            completed_at=BASE_TIME + timedelta(seconds=13),
        )


def test_c07_resume_limit_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-0",
        owner_pid=4200,
        owner_start_time="2026-10-17T04:00:00+00:00",
        now=BASE_TIME,
        ttl_seconds=30,
    )
    first = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME,
    )

    for resume_number in range(1, 4):
        resumed_at = BASE_TIME + timedelta(seconds=resume_number * 31)
        lease = store.acquire_lease(
            lease_key="maintenance-worker",
            owner_id=f"worker-{resume_number}",
            owner_pid=4200 + resume_number,
            owner_start_time=resumed_at.isoformat(),
            now=resumed_at,
            ttl_seconds=30,
            prior_owner_observation="dead",
        )
        attempt = store.begin_step_attempt(
            job_id=job.job_id,
            step_key="investigation",
            sequence=1,
            lease=lease,
            started_at=resumed_at,
        )
        assert attempt.attempt_no == resume_number + 1
        assert not attempt.reused

    denied_at = BASE_TIME + timedelta(seconds=124)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-4",
        owner_pid=4204,
        owner_start_time=denied_at.isoformat(),
        now=denied_at,
        ttl_seconds=30,
        prior_owner_observation="dead",
    )
    with pytest.raises(MaintenanceStoreError, match="job_resume_limit_exceeded"):
        store.begin_step_attempt(
            job_id=job.job_id,
            step_key="investigation",
            sequence=1,
            lease=lease,
            started_at=denied_at,
        )
    assert store.counts()["step_attempt"] == 4
    finalized = store.finalize_job(
        job_id=job.job_id,
        final_status="blocked",
        lease=lease,
        finalized_at=denied_at,
        reason_code="job_resume_limit_exceeded",
    )
    assert finalized.status == "blocked"
    next_job = create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-after-resume-limit",
        result_commit="commit-after-resume-limit",
        result_version="result-after-resume-limit",
        config_version="settings-after-resume-limit",
    )
    assert next_job.started


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


def test_c53_review_round_limit_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    store.create_release(
        release_id="release-review-limit",
        author_id="implementation-agent",
        base_commit="base-review-limit",
        result_commit="commit-review-limit",
        result_version="version-review-limit",
        config_version="settings-review-limit",
        required_case_ids=("case-review-limit",),
        created_at=BASE_TIME,
    )
    for round_no in range(1, 4):
        store.record_release_review(
            release_id="release-review-limit",
            reviewer_id="independent-reviewer",
            reviewed_version="version-review-limit",
            status="changes_requested",
            reviewed_at=BASE_TIME + timedelta(seconds=round_no),
        )
    with pytest.raises(MaintenanceStoreError, match="review_round_limit_exceeded"):
        store.record_release_review(
            release_id="release-review-limit",
            reviewer_id="independent-reviewer",
            reviewed_version="version-review-limit",
            status="approved",
            reviewed_at=BASE_TIME + timedelta(seconds=4),
        )

    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=4401,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=10),
        ttl_seconds=30,
    )
    attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=11),
    )
    store.complete_step_attempt(
        attempt_id=attempt.attempt_id,
        lease=lease,
        result_version="result-v1",
        completed_at=BASE_TIME + timedelta(seconds=12),
    )
    for round_no in range(1, 4):
        store.record_step_review(
            attempt_id=attempt.attempt_id,
            reviewer_id="independent-reviewer",
            reviewed_version="result-v1",
            status="changes_requested",
            reviewed_at=BASE_TIME + timedelta(seconds=12 + round_no),
        )
    with pytest.raises(MaintenanceStoreError, match="review_round_limit_exceeded"):
        store.record_step_review(
            attempt_id=attempt.attempt_id,
            reviewer_id="independent-reviewer",
            reviewed_version="result-v1",
            status="approved",
            reviewed_at=BASE_TIME + timedelta(seconds=16),
        )

    second_release = store.create_release(
        release_id="release-while-busy",
        author_id="implementation-agent",
        base_commit="base-2",
        result_commit="commit-2",
        result_version="result-v2",
        config_version="settings-v1",
        required_case_ids=("case-c",),
        created_at=(BASE_TIME + timedelta(seconds=17)).isoformat(),
    )
    store.record_test_result(
        release_id=second_release.release_id,
        case_id="case-c",
        attempt=1,
        result_version="result-v2",
        status="PASS",
        recorded_at=(BASE_TIME + timedelta(seconds=18)).isoformat(),
    )
    store.record_release_review(
        release_id=second_release.release_id,
        reviewer_id="independent-reviewer",
        reviewed_version="result-v2",
        status="approved",
        reviewed_at=(BASE_TIME + timedelta(seconds=19)).isoformat(),
    )
    store.record_deployment(
        release_id=second_release.release_id,
        result_commit="commit-2",
        config_version="settings-v1",
        status="deployed",
        deployed_at=(BASE_TIME + timedelta(seconds=20)).isoformat(),
    )
    assert (
        store.evaluate_release_gate(
            release_id=second_release.release_id,
            evaluated_at=(BASE_TIME + timedelta(seconds=21)).isoformat(),
        ).status
        == "ready"
    )
    busy = store.start_fix_job(
        incident_id=incident_id,
        release_id=second_release.release_id,
        job_id="busy-job",
        started_at=(BASE_TIME + timedelta(seconds=22)).isoformat(),
    )
    assert not busy.started
    assert busy.reason_codes == ("maintenance_job_busy",)

    fake = store.create_release(
        release_id="release-fake-ready",
        author_id="implementation-agent",
        base_commit="base-1",
        result_commit="commit-fake",
        result_version="fake-v1",
        config_version="settings-v1",
        required_case_ids=("case-required",),
        created_at=(BASE_TIME + timedelta(seconds=23)).isoformat(),
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "INSERT INTO release_gate "
            "(release_id, status, reasons_json, evaluated_at, result_commit) "
            "VALUES (?, 'ready', '[]', ?, ?)",
            (
                fake.release_id,
                (BASE_TIME + timedelta(seconds=24)).isoformat(),
                "commit-fake",
            ),
        )
    fake_denied = store.start_fix_job(
        incident_id=incident_id,
        release_id=fake.release_id,
        job_id="fake-ready-job",
        started_at=(BASE_TIME + timedelta(seconds=25)).isoformat(),
    )
    assert not fake_denied.started
    assert store.counts()["maintenance_job"] == 1


def test_c53_step_review_limit_spans_retries_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-step-review-retries",
    )
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-review-retries",
        owner_pid=4402,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=10),
        ttl_seconds=60,
    )

    for round_no in range(1, 4):
        attempt = store.begin_step_attempt(
            job_id=job.job_id,
            step_key="investigation",
            sequence=1,
            lease=lease,
            started_at=BASE_TIME + timedelta(seconds=10 + round_no * 4),
        )
        result_version = f"investigation-v{round_no}"
        store.complete_step_attempt(
            attempt_id=attempt.attempt_id,
            lease=lease,
            result_version=result_version,
            completed_at=BASE_TIME + timedelta(seconds=11 + round_no * 4),
        )
        store.record_step_review(
            attempt_id=attempt.attempt_id,
            reviewer_id="independent-reviewer",
            reviewed_version=result_version,
            status="changes_requested",
            reviewed_at=BASE_TIME + timedelta(seconds=12 + round_no * 4),
        )

    with pytest.raises(MaintenanceStoreError, match="review_round_limit_exceeded"):
        store.begin_step_attempt(
            job_id=job.job_id,
            step_key="investigation",
            sequence=1,
            lease=lease,
            started_at=BASE_TIME + timedelta(seconds=25),
        )

    assert store.counts()["step_attempt"] == 3


def test_c23_contract(tmp_path):
    store = make_store(tmp_path)
    same_cause = {
        "dedupe_key": "daily:component-a:error-a",
        "component": "daily_pipeline",
        "event_code": "daily_error",
    }
    first = store.record_daily_check(
        source_launch_id="c23-day-one",
        source_run_id="run-day-one",
        target_date="2026-10-17",
        check_status="abnormal",
        observed_at=BASE_TIME.isoformat(),
        reason_code="daily_error",
        incident=same_cause,
    )
    duplicate = store.record_daily_check(
        source_launch_id="c23-day-one",
        source_run_id="run-day-one",
        target_date="2026-10-17",
        check_status="abnormal",
        observed_at=BASE_TIME.isoformat(),
        reason_code="daily_error",
        incident=same_cause,
    )
    assert duplicate.check_id == first.check_id
    assert not duplicate.created
    assert duplicate.incident_id == first.incident_id

    next_day = store.record_daily_check(
        source_launch_id="c23-day-two",
        source_run_id="run-day-two",
        target_date="2026-10-18",
        check_status="abnormal",
        observed_at=(BASE_TIME + timedelta(days=1)).isoformat(),
        reason_code="daily_error",
        incident=same_cause,
    )
    assert next_day.check_id != first.check_id
    assert next_day.incident_id == first.incident_id

    different_cause = store.record_daily_check(
        source_launch_id="c23-different-cause",
        source_run_id="run-different-cause",
        target_date="2026-10-18",
        check_status="abnormal",
        observed_at=(BASE_TIME + timedelta(days=1, seconds=1)).isoformat(),
        reason_code="daily_error",
        incident={
            "dedupe_key": "daily:component-a:error-b",
            "component": "daily_pipeline",
            "event_code": "different_daily_error",
        },
    )
    assert different_cause.incident_id != first.incident_id

    connection = store._connect()
    try:
        connection.execute(
            "UPDATE incident SET status = 'resolved' WHERE incident_id = ?",
            (first.incident_id,),
        )
    finally:
        connection.close()

    recurrence = store.record_daily_check(
        source_launch_id="c23-recurrence",
        source_run_id="run-recurrence",
        target_date="2026-10-19",
        check_status="abnormal",
        observed_at=(BASE_TIME + timedelta(days=2)).isoformat(),
        reason_code="daily_error",
        incident=same_cause,
    )
    assert recurrence.incident_id not in {
        None,
        first.incident_id,
        different_cause.incident_id,
    }
    counts = store.counts()
    assert counts["daily_check"] == 4
    assert counts["incident_run"] == 4
    assert counts["incident"] == 3
