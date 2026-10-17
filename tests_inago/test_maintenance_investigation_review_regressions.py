"""Store-level regressions for the U12 investigation handoff gate."""

from datetime import timedelta

import pytest

from automation.inago.maintenance.store import MaintenanceStoreError
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
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

    store.complete_step_attempt(
        attempt_id=attempt_id,
        lease=lease,
        result_version="investigation-v1",
        output_path=arbitrary_output,
        output_sha256="a" * 64,
        completed_at=BASE_TIME + timedelta(seconds=12),
    )
    store.record_step_review(
        attempt_id=attempt_id,
        reviewer_id="independent-reviewer",
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
