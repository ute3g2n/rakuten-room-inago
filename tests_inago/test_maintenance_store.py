"""Contract cases for the isolated maintenance state store."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from automation.inago.maintenance import store as maintenance_store_module
from automation.inago.maintenance.store import (
    IdempotencyConflictError,
    MaintenanceStoreError,
)
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
    record_successful_deployment_fixture,
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


def test_fc11_operation_events_are_read_by_exact_job_launch_and_invocation(tmp_path):
    store = make_store(tmp_path)
    event = {
        "event_id": "event-fc11-report-input",
        "correlation_id": "correlation-fc11-report-input",
        "parent_operation": "workflow-fc11-report-input",
        "job_id": "job-fc11-report-input",
        "source_launch_id": "launch-fc11-report-input",
        "source_run_id": None,
        "incident_id": "incident-fc11-report-input",
        "invocation_id": "workflow-fc11-report-input",
        "stage": "planning",
        "attempt_id": "attempt-fc11-report-input",
        "operation": "workflow.stage.planning",
        "phase": "handoff",
        "observed_at": BASE_TIME.isoformat(),
        "code_revision": "maintenance-source-v1-" + "a" * 64,
        "reason_code": "ai_transport_failed",
        "exception_chain": ["MaintenanceAIBlockedError", "AppServerError"],
        "exception_errno": None,
        "exception_reason_codes": ["ai_transport_failed"],
        "operation_context": {},
        "stack_frames": [
            {"file": "production_workflow.py", "function": "run_planner", "line": 42}
        ],
        "capture_location": "maintenance.stage_handoff.make_stage_handoff",
        "input_refs": ["job-fc11-report-input/attempt-fc11-report-input/input.json"],
        "artifact_refs": [
            "job-fc11-report-input/attempt-fc11-report-input/stage-handoff.json"
        ],
        "primary_sink": "maintenance_operation_event",
        "fallback_status": None,
    }
    store.record_operation_event(event)

    found = store.list_operation_events(
        job_id="job-fc11-report-input",
        source_launch_id="launch-fc11-report-input",
        source_run_id=None,
        invocation_id="workflow-fc11-report-input",
    )

    assert len(found) == 1
    assert found[0]["event_id"] == event["event_id"]
    assert found[0]["reason_code"] == "ai_transport_failed"
    assert found[0]["exception_chain_json"] == json.dumps(
        ["MaintenanceAIBlockedError", "AppServerError"], separators=(",", ":")
    )
    assert (
        store.list_operation_events(
            job_id="job-fc11-report-input",
            source_launch_id="launch-fc11-report-input",
            source_run_id=None,
            invocation_id="workflow-other-invocation",
        )
        == ()
    )
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
    resumed = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=first_lease,
        started_at=(BASE_TIME + timedelta(seconds=1)).isoformat(),
    )
    assert resumed.reused is True
    assert resumed.attempt_id == completed.attempt_id
    assert resumed.attempt_no == completed.attempt_no
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
    assert not retry.reused
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
    unknown_owner_lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-b",
        owner_pid=4102,
        owner_start_time="2026-10-17T04:00:04+00:00",
        now=BASE_TIME + timedelta(seconds=4),
        ttl_seconds=30,
        prior_owner_observation="unknown",
    )
    alive_owner_lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-b",
        owner_pid=4102,
        owner_start_time="2026-10-17T04:00:34+00:00",
        now=BASE_TIME + timedelta(seconds=34),
        ttl_seconds=30,
        prior_owner_observation="alive",
    )
    assert unknown_owner_lease.generation == first_lease.generation + 1
    assert alive_owner_lease.generation == unknown_owner_lease.generation + 1

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
    assert not completed_again.reused
    assert completed_again.attempt_no == retry.attempt_no + 1
    store.complete_step_attempt(
        attempt_id=completed_again.attempt_id,
        lease=resumed_lease,
        result_version="investigation-v3",
        completed_at=(BASE_TIME + timedelta(seconds=35)).isoformat(),
    )

    resumed_step = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="implementation",
        sequence=2,
        lease=resumed_lease,
        started_at=(BASE_TIME + timedelta(seconds=36)).isoformat(),
    )
    assert not resumed_step.reused
    assert resumed_step.attempt_no == interrupted.attempt_no + 1
    repeated_step = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="implementation",
        sequence=2,
        lease=resumed_lease,
        started_at=(BASE_TIME + timedelta(seconds=37)).isoformat(),
    )
    assert repeated_step.reused
    assert repeated_step.attempt_id == resumed_step.attempt_id
    counts = store.counts()
    assert counts["step_attempt"] == 5
    assert store.get_step_attempt(interrupted.attempt_id).status == "interrupted"
    assert store.get_step_attempt(completed.attempt_id).status == "complete"
    assert store.get_step_attempt(retry.attempt_id).status == "complete"


def test_c07_later_step_can_start_with_missing_predecessor_sequence(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    first_lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=4191,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME,
        ttl_seconds=30,
    )
    planning = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_planning",
        sequence=2,
        lease=first_lease,
        started_at=BASE_TIME.isoformat(),
    )
    second_lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-b",
        owner_pid=4192,
        owner_start_time=(BASE_TIME + timedelta(seconds=31)).isoformat(),
        now=BASE_TIME + timedelta(seconds=31),
        ttl_seconds=30,
        prior_owner_observation="dead",
    )
    planning_retry = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_planning",
        sequence=2,
        lease=second_lease,
        started_at=(BASE_TIME + timedelta(seconds=31)).isoformat(),
    )
    report = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_report",
        sequence=4,
        lease=second_lease,
        started_at=(BASE_TIME + timedelta(seconds=32)).isoformat(),
    )

    assert store.get_step_attempt(planning.attempt_id).status == "interrupted"
    assert store.get_step_attempt(planning_retry.attempt_id).status == "running"
    assert report.status == "running"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT source_run_id FROM maintenance_job WHERE job_id = ?",
            (job.job_id,),
        ).fetchone() == (None,)
    assert [item.sequence for item in store.list_step_attempts(job_id=job.job_id)] == [
        2,
        2,
        4,
    ]


def test_c07_failed_step_attempt_closes_and_keeps_bound_artifact(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker-failed-attempt",
        owner_id="worker-failed-attempt",
        owner_pid=4193,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME,
        ttl_seconds=30,
    )
    attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_planning",
        sequence=2,
        lease=lease,
        started_at=BASE_TIME,
    )
    output_path = f"{job.job_id}/{attempt.attempt_id}/failure-handoff.json"
    output_sha256 = "a" * 64

    store.fail_step_attempt(
        attempt_id=attempt.attempt_id,
        lease=lease,
        result_version=f"failure-{attempt.attempt_id}",
        failed_at=BASE_TIME + timedelta(seconds=1),
        output_path=output_path,
        output_sha256=output_sha256,
    )

    failed = store.get_step_attempt(attempt.attempt_id)
    assert failed.status == "failed"
    assert failed.result_version == f"failure-{attempt.attempt_id}"
    assert failed.output_path == output_path
    assert failed.output_sha256 == output_sha256
    retry = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_planning",
        sequence=2,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=2),
    )
    assert retry.attempt_no == attempt.attempt_no + 1
    assert retry.status == "running"


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
    finalized = store.finalize_job(
        job_id=first_job.job_id,
        final_status="complete",
        lease=lease,
        finalized_at=BASE_TIME + timedelta(seconds=13),
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


def test_job_completion_updates_incident_and_recovers_idempotently(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    first_lease = store.acquire_lease(
        lease_key="maintenance-worker-terminal",
        owner_id="worker-a",
        owner_pid=4321,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=10),
        ttl_seconds=30,
    )
    attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=first_lease,
        started_at=BASE_TIME + timedelta(seconds=11),
    )
    store.complete_step_attempt(
        attempt_id=attempt.attempt_id,
        lease=first_lease,
        result_version="investigation-u21-v1",
        completed_at=BASE_TIME + timedelta(seconds=12),
    )
    store.record_step_review(
        attempt_id=attempt.attempt_id,
        reviewer_id="independent-reviewer",
        reviewed_version="investigation-u21-v1",
        status="approved",
        reviewed_at=BASE_TIME + timedelta(seconds=13),
    )

    finalized = store.finalize_job(
        job_id=job.job_id,
        final_status="complete",
        lease=first_lease,
        finalized_at=BASE_TIME + timedelta(seconds=14),
        incident_status="resolved",
    )
    assert finalized.status == "complete"
    with store._connect() as connection:
        job_status = connection.execute(
            "SELECT status FROM maintenance_job WHERE job_id = ?", (job.job_id,)
        ).fetchone()["status"]
        incident_status = connection.execute(
            "SELECT status FROM incident WHERE incident_id = ?", (incident_id,)
        ).fetchone()["status"]
    assert job_status == "complete"
    assert incident_status == "resolved"

    resumed_at = BASE_TIME + timedelta(seconds=41)
    resumed_lease = store.acquire_lease(
        lease_key="maintenance-worker-terminal",
        owner_id="worker-b",
        owner_pid=4322,
        owner_start_time=resumed_at.isoformat(),
        now=resumed_at,
        ttl_seconds=30,
        prior_owner_observation="dead",
    )
    resumed = store.finalize_job(
        job_id=job.job_id,
        final_status="complete",
        lease=resumed_lease,
        finalized_at=resumed_at + timedelta(seconds=1),
        incident_status="resolved",
    )
    assert resumed.status == "complete"
    assert store.get_step_attempt(attempt.attempt_id).status == "complete"
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT status FROM incident WHERE incident_id = ?", (incident_id,)
            ).fetchone()["status"]
            == "resolved"
        )


def test_report_nonpass_can_resume_the_same_job_without_resolving_incident(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    source_launch_id = "launch-1"
    store.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date="2026-10-17",
        job_id=job.job_id,
        created_at=BASE_TIME,
    )
    lease = store.acquire_lease(
        lease_key="maintenance:worker",
        owner_id="report-worker",
        owner_pid=9123,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME,
        ttl_seconds=60,
    )

    store.finalize_job(
        job_id=job.job_id,
        final_status="complete",
        lease=lease,
        finalized_at=BASE_TIME + timedelta(seconds=1),
        incident_status="in_progress",
    )
    resumed = store.resume_maintenance_job(
        job_id=job.job_id,
        source_launch_id=source_launch_id,
        resumed_at=BASE_TIME + timedelta(seconds=2),
    )
    assert resumed.job_id == job.job_id
    assert resumed.source_launch_id == source_launch_id
    assert resumed.resume_count == 1

    retried = store.finalize_job(
        job_id=job.job_id,
        final_status="failed",
        lease=lease,
        finalized_at=BASE_TIME + timedelta(seconds=3),
        reason_code="report_status_pending",
        incident_status="in_progress",
    )
    assert retried.status == "failed"
    assert retried.reason_code == "report_status_pending"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT status, terminal_reason_code FROM maintenance_job WHERE job_id = ?",
            (job.job_id,),
        ).fetchone() == ("failed", "report_status_pending")
        assert connection.execute(
            "SELECT resume_count FROM maintenance_job WHERE job_id = ?",
            (job.job_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT status FROM incident WHERE incident_id = ?", (incident_id,)
        ).fetchone() == ("in_progress",)


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


def test_c07_resume_count_does_not_limit_restarts(tmp_path):
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

    for resume_number in range(1, 8):
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

    assert store.counts()["step_attempt"] == 8
    finalized = store.finalize_job(
        job_id=job.job_id,
        final_status="blocked",
        lease=lease,
        finalized_at=resumed_at,
        reason_code="operator_review_required",
    )
    assert finalized.status == "blocked"
    next_job = create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-after-unbounded-resume",
        result_commit="commit-after-unbounded-resume",
        result_version="result-after-unbounded-resume",
        config_version="settings-after-unbounded-resume",
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
    assert denied.started
    assert store.counts()["maintenance_job"] == 1

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
    with pytest.raises(
        MaintenanceStoreError,
        match="deployment_success_requires_pending_flow",
    ):
        store.record_deployment(
            release_id=release.release_id,
            result_commit="other-commit",
            config_version="settings-v1",
            status="deployed",
            deployed_at=(BASE_TIME + timedelta(seconds=9)).isoformat(),
            source_commit="other-commit",
            base_commit="base-1",
            tested_commit="other-commit",
            pushed_commit="other-commit",
            active_commit="other-commit",
            remote_name="origin",
            target_branch="main",
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
    with pytest.raises(
        MaintenanceStoreError,
        match="deployment_success_requires_pending_flow",
    ):
        store.record_deployment(
            release_id=release.release_id,
            result_commit="commit-1",
            config_version="wrong-settings",
            status="deployed",
            deployed_at=(BASE_TIME + timedelta(seconds=12)).isoformat(),
            source_commit="commit-1",
            base_commit="base-1",
            tested_commit="commit-1",
            pushed_commit="commit-1",
            active_commit="commit-1",
            remote_name="origin",
            target_branch="main",
        )
    assert (
        store.evaluate_release_gate(
            release_id=release.release_id,
            evaluated_at=(BASE_TIME + timedelta(seconds=13)).isoformat(),
        ).status
        == "blocked"
    )

    record_successful_deployment_fixture(
        store,
        release_id=release.release_id,
        deployed_at=(BASE_TIME + timedelta(seconds=14)).isoformat(),
    )
    gate = store.evaluate_release_gate(
        release_id=release.release_id,
        evaluated_at=(BASE_TIME + timedelta(seconds=15)).isoformat(),
    )
    assert gate.status == "ready"
    ready_release = store.create_release(
        release_id="release-ready-no-gate",
        author_id="implementation-agent",
        base_commit="base-ready",
        result_commit="commit-ready",
        result_version="result-ready",
        config_version="settings-ready",
        required_case_ids=("case-ready",),
        created_at=(BASE_TIME + timedelta(seconds=16)).isoformat(),
    )
    started = store.start_fix_job(
        incident_id=incident_id,
        release_id=ready_release.release_id,
        job_id="ready-job",
        started_at=(BASE_TIME + timedelta(seconds=17)).isoformat(),
    )
    assert started.started
    assert started.job_id == "ready-job"
    assert started.reason_codes == ()
    assert store.counts()["maintenance_job"] == 2


def test_c53_review_rounds_are_unbounded(tmp_path):
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
    record_successful_deployment_fixture(
        store,
        release_id=second_release.release_id,
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
    assert busy.started
    assert busy.reason_codes == ()

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
    assert fake_denied.started
    assert fake_denied.gate_status == "not_evaluated"
    assert store.counts()["maintenance_job"] == 3


@pytest.mark.parametrize("review_status", ["changes_requested", "rejected"])
def test_c53_step_review_rounds_continue_across_retries(tmp_path, review_status):
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
            status=review_status,
            reviewed_at=BASE_TIME + timedelta(seconds=12 + round_no * 4),
        )

    fourth_attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=25),
    )

    assert fourth_attempt.status == "running"
    assert store.counts()["step_attempt"] == 4


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


def test_resume_maintenance_job_ignores_resume_preconditions(tmp_path):
    store = make_store(tmp_path)
    source_launch_id = "historical-resume-without-gates"
    check = store.record_daily_check(
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date="2026-10-17",
        check_status="abnormal",
        observed_at=BASE_TIME.isoformat(),
        reason_code="historical_daily_failure_observed",
        incident={
            "dedupe_key": source_launch_id,
            "component": "daily_pipeline",
            "event_code": "historical_daily_failure",
        },
    )
    assert check.incident_id is not None
    release_id = "release-resume-without-gates"
    job = create_ready_release(
        store,
        incident_id=check.incident_id,
        release_id=release_id,
    )
    store.bind_job_input_manifest(
        incident_id=check.incident_id,
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date="2026-10-17",
        job_id=job.job_id,
        created_at=BASE_TIME + timedelta(seconds=1),
    )
    store.record_test_result(
        release_id=release_id,
        case_id="contract-case-1",
        attempt=2,
        result_version="result-v1",
        status="FAIL",
        recorded_at=BASE_TIME + timedelta(seconds=2),
    )
    assert (
        store.evaluate_release_gate(
            release_id=release_id,
            evaluated_at=BASE_TIME + timedelta(seconds=3),
        ).status
        == "blocked"
    )

    worker_lease = store.acquire_lease(
        lease_key="maintenance:worker",
        owner_id="worker-before-resume",
        owner_pid=4401,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=4),
        ttl_seconds=60,
    )
    attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=worker_lease,
        started_at=BASE_TIME + timedelta(seconds=5),
    )
    store.finalize_job(
        job_id=job.job_id,
        final_status="blocked",
        lease=worker_lease,
        finalized_at=BASE_TIME + timedelta(seconds=6),
        reason_code="session_config_mismatch",
    )
    store.release_lease(
        lease=worker_lease,
        released_at=BASE_TIME + timedelta(seconds=7),
    )

    other_incident = create_incident(store, launch_id="other-queued-job")
    other_job = create_ready_release(
        store,
        incident_id=other_incident,
        release_id="release-other-queued-job",
    )
    assert other_job.started
    resumed_at = BASE_TIME + timedelta(seconds=8)
    active_worker_lease = store.acquire_lease(
        lease_key="maintenance:worker",
        owner_id="worker-for-other-job",
        owner_pid=4402,
        owner_start_time=resumed_at.isoformat(),
        now=resumed_at,
        ttl_seconds=60,
    )

    resumed = store.resume_maintenance_job(
        job_id=job.job_id,
        source_launch_id=source_launch_id,
        resumed_at=resumed_at,
    )
    assert resumed.job_id == job.job_id
    assert resumed.resume_count == 1
    assert store.get_step_attempt(attempt.attempt_id).status == "blocked"
    assert store.get_lease("maintenance:worker").state == "held"
    assert (
        store.list_historical_job_references(
            source_launch_id=source_launch_id,
            limit=10,
            include_blocked=True,
        )[0].status
        == "queued"
    )

    repeated = store.resume_maintenance_job(
        job_id=job.job_id,
        source_launch_id=source_launch_id,
        resumed_at=resumed_at + timedelta(seconds=1),
    )
    assert repeated.resume_event_id == resumed.resume_event_id
    assert repeated.resume_count == 1
    assert len(store.list_job_resume_events(job_id=job.job_id)) == 1
    assert active_worker_lease.owner_id == "worker-for-other-job"


def test_schema_15_upgrade_drops_only_optional_repair_predecessor_trigger(
    tmp_path, monkeypatch
):
    target_schema_version = 15

    # Recreate the currently deployed schema 14 without touching production.
    monkeypatch.setattr(maintenance_store_module, "SCHEMA_VERSION", 14)
    store = make_store(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (14,)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='step_attempt_repair_predecessor_all_or_none'"
        ).fetchone() == (1,)

    # The next normal initialize must run the registered migration 015.
    monkeypatch.setattr(
        maintenance_store_module, "SCHEMA_VERSION", target_schema_version
    )
    assert store.initialize() == target_schema_version
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (15,)
        assert connection.execute(
            "SELECT 1 FROM schema_migration WHERE version = 15"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='step_attempt_repair_predecessor_all_or_none'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='step_attempt_repair_predecessor_immutable'"
        ).fetchone() == (1,)

    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="fc09-no-predecessor",
        owner_pid=4199,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME,
        ttl_seconds=30,
    )
    repair_attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_repair",
        sequence=3,
        lease=lease,
        started_at=BASE_TIME,
    )
    assert repair_attempt.status == "running"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT repair_predecessor_attempt_id, "
            "repair_predecessor_result_version, "
            "repair_predecessor_output_sha256 FROM step_attempt "
            "WHERE attempt_id = ?",
            (repair_attempt.attempt_id,),
        ).fetchone() == (None, None, None)


def test_schema_19_upgrade_preserves_legacy_attestation_values_and_allows_nulls(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(maintenance_store_module, "SCHEMA_VERSION", 18)
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    legacy_job_id = "legacy-job-with-attestation"
    legacy_manifest_digest = "a" * 64
    legacy_role_digest = "b" * 64

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "INSERT INTO release "
            "(release_id, author_id, base_commit, result_commit, result_version, "
            "config_version, case_manifest_sha256, state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'frozen', ?)",
            (
                "legacy-release",
                "legacy-author",
                "base-v18",
                "result-v18",
                "legacy-version",
                "legacy-config",
                "c" * 64,
                BASE_TIME.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO maintenance_job "
            "(job_id, incident_id, release_id, source_run_id, result_version, "
            "status, created_at, updated_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
            (
                legacy_job_id,
                incident_id,
                "legacy-release",
                "legacy-version",
                "queued",
                BASE_TIME.isoformat(),
                BASE_TIME.isoformat(),
            ),
        )
        before_triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert "deployment_success_exact_version" in before_triggers
        assert "repair_campaign_result_receipt_bind" in before_triggers
        connection.execute(
            "INSERT INTO job_input_manifest "
            "(job_id, incident_id, source_launch_id, source_run_id, target_date, "
            "manifest_sha256, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?)",
            (
                legacy_job_id,
                incident_id,
                "legacy-launch",
                "2026-10-17",
                legacy_manifest_digest,
                BASE_TIME.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO maintenance_ai_session "
            "(job_id, session_key, parent_key, role_name, thread_id, "
            "manifest_sha256, requested_model, requested_effort, "
            "requested_service_tier, requested_fast_mode, role_config_sha256, "
            "sandbox_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                legacy_job_id,
                "legacy-review",
                "B",
                "common-reviewer",
                "thread-legacy-review",
                legacy_manifest_digest,
                "gpt-6-luna",
                "high",
                "default",
                0,
                legacy_role_digest,
                "{}",
                BASE_TIME.isoformat(),
            ),
        )

    monkeypatch.setattr(maintenance_store_module, "SCHEMA_VERSION", 19)
    assert store.initialize(applied_at=BASE_TIME) == 19

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (19,)
        retained_manifest = connection.execute(
            "SELECT manifest_sha256 FROM job_input_manifest WHERE job_id = ?",
            (legacy_job_id,),
        ).fetchone()
        retained_session = connection.execute(
            "SELECT manifest_sha256, role_config_sha256 "
            "FROM maintenance_ai_session WHERE job_id = ?",
            (legacy_job_id,),
        ).fetchone()
        assert retained_manifest == (legacy_manifest_digest,)
        assert retained_session == (legacy_manifest_digest, legacy_role_digest)
        remaining_triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert "deployment_success_exact_version" not in remaining_triggers
        assert "repair_campaign_result_receipt_bind" not in remaining_triggers

        new_job_id = "job-without-attestation"
        connection.execute(
            "INSERT INTO maintenance_job "
            "(job_id, incident_id, release_id, source_run_id, result_version, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, ?, ?, ?, ?)",
            (
                new_job_id,
                incident_id,
                "new-version",
                "failed",
                BASE_TIME.isoformat(),
                BASE_TIME.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO job_input_manifest "
            "(job_id, incident_id, source_launch_id, source_run_id, target_date, "
            "manifest_sha256, created_at) VALUES (?, ?, ?, NULL, ?, NULL, ?)",
            (
                new_job_id,
                incident_id,
                "new-launch",
                "2026-10-21",
                BASE_TIME.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO maintenance_ai_session "
            "(job_id, session_key, parent_key, role_name, thread_id, "
            "manifest_sha256, requested_model, requested_effort, "
            "requested_service_tier, requested_fast_mode, role_config_sha256, "
            "sandbox_json, created_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, ?, ?)",
            (
                new_job_id,
                "new-review",
                "B",
                "common-reviewer",
                "thread-new-review",
                "gpt-6-luna",
                "high",
                "default",
                0,
                "{}",
                BASE_TIME.isoformat(),
            ),
        )
        assert connection.execute(
            "SELECT manifest_sha256 FROM job_input_manifest WHERE job_id = ?",
            (new_job_id,),
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT manifest_sha256, role_config_sha256 "
            "FROM maintenance_ai_session WHERE job_id = ?",
            (new_job_id,),
        ).fetchone() == (None, None)
