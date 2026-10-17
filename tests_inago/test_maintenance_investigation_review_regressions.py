"""Store-level regressions for the U12 investigation handoff gate."""

import json
from datetime import timedelta

import pytest

from automation.inago.maintenance.controller import (
    InvestigationAttemptRequest,
    MaintenanceController,
)
from automation.inago.maintenance.store import (
    LeaseOwnershipError,
    MaintenanceStoreError,
)
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
)
from tests_inago.maintenance_investigation_support import (
    make_context,
    make_investigation_response,
    make_review_response,
)


def _start_attempt(tmp_path):
    store = make_store(tmp_path / "store")
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=5101,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=6),
        ttl_seconds=300,
    )
    attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=7),
    )
    return store, job.job_id, attempt.attempt_id, lease


def test_u12_investigation_path_cannot_bypass_handoff_gate(tmp_path):
    store, job_id, attempt_id, lease = _start_attempt(tmp_path)
    arbitrary_output = f"{job_id}/{attempt_id}/other.json"

    with pytest.raises(
        MaintenanceStoreError, match="investigation_output_path_required"
    ):
        store.complete_step_attempt_with_review(
            attempt_id=attempt_id,
            lease=lease,
            result_version="investigation-v1",
            completed_at=BASE_TIME + timedelta(seconds=10),
            output_path=arbitrary_output,
            output_sha256="a" * 64,
            reviewer_id="independent-reviewer",
            reviewed_version="investigation-v1",
            review_status="approved",
            reviewed_at=BASE_TIME + timedelta(seconds=11),
        )

    with pytest.raises(
        MaintenanceStoreError, match="investigation_output_path_required"
    ):
        store.complete_step_attempt(
            attempt_id=attempt_id,
            lease=lease,
            result_version="investigation-v1",
            output_path=arbitrary_output,
            output_sha256="a" * 64,
            completed_at=BASE_TIME + timedelta(seconds=12),
        )

    store.complete_step_attempt(
        attempt_id=attempt_id,
        lease=lease,
        result_version="investigation-v1",
        output_path=f"{job_id}/{attempt_id}/result.json",
        output_sha256="a" * 64,
        completed_at=BASE_TIME + timedelta(seconds=12),
    )
    store.record_step_review(
        attempt_id=attempt_id,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v1",
        status="approved",
        reviewed_at=BASE_TIME + timedelta(seconds=13),
    )
    with pytest.raises(
        MaintenanceStoreError, match="step_predecessor_handoff_not_accepted"
    ):
        store.begin_step_attempt(
            job_id=job_id,
            step_key="change_planning",
            sequence=2,
            lease=lease,
            started_at=BASE_TIME + timedelta(seconds=14),
        )


def _start_controller_attempt(tmp_path):
    store = make_store(tmp_path / "store")
    controller = MaintenanceController(store)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=5201,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=6),
        ttl_seconds=300,
    )
    controller.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id="launch-1",
        source_run_id=None,
        target_date="2026-10-17",
        job_id=job.job_id,
        created_at=BASE_TIME + timedelta(seconds=6),
    )
    attempt = controller.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=7),
    )
    context = make_context(
        incident_id=incident_id,
        source_launch_id="launch-1",
        source_run_id=None,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
    )
    attempts_root = tmp_path / "attempts"
    output_root = attempts_root / job.job_id / attempt.attempt_id
    output_root.mkdir(parents=True)
    return store, controller, lease, context, attempts_root, output_root


def _complete_attempt(
    controller,
    *,
    context,
    attempts_root,
    output_root,
    lease,
    version,
    review,
    review_round,
    reviewer_calls,
    completed_at=10,
    accepted_at=12,
):
    return controller.complete_investigation_attempt(
        InvestigationAttemptRequest(
            context=context,
            response=make_investigation_response(context),
            attempts_root=attempts_root,
            output_root=output_root,
            result_version=version,
            created_at=BASE_TIME + timedelta(seconds=8),
            completed_at=BASE_TIME + timedelta(seconds=completed_at),
            review_started_at=BASE_TIME + timedelta(seconds=9),
            reviewed_at=BASE_TIME + timedelta(seconds=11),
            accepted_at=BASE_TIME + timedelta(seconds=accepted_at),
            lease=lease,
            author_id="implementation-agent",
            author_execution_id=f"investigator-{version}",
            reviewer_id="independent-reviewer",
            reviewer_execution_id="review-turn-001",
            review_runner=lambda metadata: (reviewer_calls.append(metadata) or review),
            review_round=review_round,
            handoff_root=attempts_root,
            synthetic_fixture=True,
        )
    )


def test_u12_review_round_must_match_store_history(tmp_path):
    store, controller, lease, context, attempts_root, output_root = (
        _start_controller_attempt(tmp_path)
    )
    first_review = make_review_response(
        context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v1",
        status="changes_requested",
        findings=["Revise the synthetic report."],
    )
    _complete_attempt(
        controller,
        context=context,
        attempts_root=attempts_root,
        output_root=output_root,
        lease=lease,
        version="investigation-v1",
        review=first_review,
        review_round=1,
        reviewer_calls=[],
    )

    retry = controller.begin_step_attempt(
        job_id=context["job_id"],
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=13),
    )
    retry_context = make_context(
        incident_id=context["incident_id"],
        source_launch_id=context["source_launch_id"],
        source_run_id=context["source_run_id"],
        job_id=context["job_id"],
        attempt_id=retry.attempt_id,
    )
    retry_root = attempts_root / context["job_id"] / retry.attempt_id
    retry_root.mkdir(parents=True)
    approved = make_review_response(
        retry_context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v2",
        status="approved",
    )
    reviewer_calls = []
    with pytest.raises(MaintenanceStoreError, match="review_round_mismatch"):
        _complete_attempt(
            controller,
            context=retry_context,
            attempts_root=attempts_root,
            output_root=retry_root,
            lease=lease,
            version="investigation-v2",
            review=approved,
            review_round=1,
            reviewer_calls=reviewer_calls,
        )
    assert reviewer_calls == []
    assert list(retry_root.iterdir()) == []


def test_u12_lease_expiry_after_review_prevents_result_write(tmp_path):
    store, controller, lease, context, attempts_root, output_root = (
        _start_controller_attempt(tmp_path)
    )
    approved = make_review_response(
        context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v1",
        status="approved",
    )
    reviewer_calls = []
    with pytest.raises(LeaseOwnershipError, match="lease_token_invalid_or_expired"):
        _complete_attempt(
            controller,
            context=context,
            attempts_root=attempts_root,
            output_root=output_root,
            lease=lease,
            version="investigation-v1",
            review=approved,
            review_round=1,
            reviewer_calls=reviewer_calls,
            completed_at=400,
        )
    assert len(reviewer_calls) == 1
    assert not (output_root / "result.json").exists()
    assert store.get_step_attempt(attempt_id=context["attempt_id"]).status == "running"


def test_u12_completed_approved_handoff_can_be_retried(tmp_path):
    store, controller, lease, context, attempts_root, output_root = (
        _start_controller_attempt(tmp_path)
    )
    approved = make_review_response(
        context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v1",
        status="approved",
    )
    with pytest.raises(LeaseOwnershipError, match="lease_token_invalid_or_expired"):
        _complete_attempt(
            controller,
            context=context,
            attempts_root=attempts_root,
            output_root=output_root,
            lease=lease,
            version="investigation-v1",
            review=approved,
            review_round=1,
            reviewer_calls=[],
            accepted_at=400,
        )
    assert store.get_step_attempt(attempt_id=context["attempt_id"]).status == "complete"
    retry_lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-b",
        owner_pid=5202,
        owner_start_time=BASE_TIME + timedelta(seconds=400),
        now=BASE_TIME + timedelta(seconds=400),
        ttl_seconds=300,
        prior_owner_observation="dead",
    )
    resumed = controller.resume_investigation_handoff(
        attempt_id=context["attempt_id"],
        attempts_root=attempts_root,
        handoff_root=attempts_root,
        lease=retry_lease,
        accepted_at=BASE_TIME + timedelta(seconds=401),
    )
    assert resumed.accepted is True
    assert store.get_step_attempt(attempt_id=context["attempt_id"]).status == "handoff_accepted"
