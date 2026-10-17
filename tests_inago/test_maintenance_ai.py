"""Handoff identity, version, and artifact boundary contract cases."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pytest

from automation.inago.maintenance.store import HandoffContractError
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
)


def test_c28_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=4201,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=10),
        ttl_seconds=30,
    )
    attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=lease,
        started_at=(BASE_TIME + timedelta(seconds=11)).isoformat(),
    )

    handoff_root = tmp_path / "handoffs"
    output_path = handoff_root / "jobs" / job.job_id / "investigation.json"
    output_path.parent.mkdir(parents=True)
    output_bytes = json.dumps({"result": "safe", "version": "result-v1"}).encode(
        "utf-8"
    )
    output_path.write_bytes(output_bytes)
    output_hash = hashlib.sha256(output_bytes).hexdigest()
    store.complete_step_attempt(
        attempt_id=attempt.attempt_id,
        lease=lease,
        result_version="result-v1",
        output_path="jobs/job-release-1/investigation.json",
        output_sha256=output_hash,
        completed_at=(BASE_TIME + timedelta(seconds=12)).isoformat(),
    )
    store.record_step_review(
        attempt_id=attempt.attempt_id,
        reviewer_id="independent-reviewer",
        reviewed_version="old-result-v0",
        status="approved",
        reviewed_at=(BASE_TIME + timedelta(seconds=13)).isoformat(),
    )

    payload = {
        "schema_version": "maintenance-handoff-v1",
        "incident_id": incident_id,
        "job_id": job.job_id,
        "attempt_id": attempt.attempt_id,
        "source_run_id": None,
        "result_version": "result-v1",
        "reviewed_version": "result-v1",
        "output": {
            "path": "jobs/job-release-1/investigation.json",
            "sha256": output_hash,
        },
    }
    assert payload["source_run_id"] is None

    for field, value in (
        ("incident_id", "incident-other"),
        ("job_id", "job-other"),
        ("attempt_id", "attempt-other"),
        ("result_version", "other-version"),
        ("reviewed_version", "old-result-v0"),
    ):
        with pytest.raises(HandoffContractError):
            store.accept_handoff(
                {**payload, field: value},
                handoff_root=handoff_root,
                accepted_at=(BASE_TIME + timedelta(seconds=14)).isoformat(),
            )

    output_path.unlink()
    with pytest.raises(HandoffContractError):
        store.accept_handoff(
            payload,
            handoff_root=handoff_root,
            accepted_at=(BASE_TIME + timedelta(seconds=15)).isoformat(),
        )
    output_path.write_bytes(output_bytes + b"tampered")
    with pytest.raises(HandoffContractError):
        store.accept_handoff(
            payload,
            handoff_root=handoff_root,
            accepted_at=(BASE_TIME + timedelta(seconds=16)).isoformat(),
        )
    output_path.write_bytes(output_bytes)

    store.record_step_review(
        attempt_id=attempt.attempt_id,
        reviewer_id="independent-reviewer",
        reviewed_version="result-v1",
        status="approved",
        reviewed_at=(BASE_TIME + timedelta(seconds=17)).isoformat(),
    )
    accepted = store.accept_handoff(
        payload,
        handoff_root=handoff_root,
        accepted_at=(BASE_TIME + timedelta(seconds=18)).isoformat(),
    )
    assert accepted.accepted
    assert accepted.source_run_id is None
    assert store.get_step_attempt(attempt.attempt_id).status == "handoff_accepted"
