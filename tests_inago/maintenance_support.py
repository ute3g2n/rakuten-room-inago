"""Fixtures shared by the isolated maintenance storage contract cases."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from automation.inago.maintenance.store import MaintenanceStore


BASE_TIME = datetime(2026, 10, 17, 4, 0, tzinfo=timezone.utc)
DAILY_STATE_NAME = "daily-runtime/state.sqlite"


def make_store(tmp_path) -> MaintenanceStore:
    data_root = tmp_path / "data"
    daily_state = tmp_path / DAILY_STATE_NAME
    store = MaintenanceStore(data_root, daily_database_path=daily_state)
    store.initialize()
    return store


def record_successful_deployment_fixture(
    store: MaintenanceStore, *, release_id: str, deployed_at: str
):
    """Create a synthetic successful deployment through the guarded lifecycle."""

    basis = store.get_release_deployment_basis(release_id=release_id)
    deployment_id = store.begin_deployment(
        release_id=release_id,
        source_commit=basis["result_commit"],
        base_commit=basis["base_commit"],
        tested_commit=basis["result_commit"],
        remote_name="origin",
        target_branch="main",
        started_at=deployed_at,
    )
    return store.finish_deployment(
        deployment_id=deployment_id,
        status="deployed",
        pushed_commit=basis["result_commit"],
        active_commit=basis["result_commit"],
        reason_code=None,
        finished_at=deployed_at,
        capability=store._deployment_capability,
    )


def create_incident(store: MaintenanceStore, *, launch_id: str = "launch-1") -> str:
    result = store.record_daily_check(
        source_launch_id=launch_id,
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
    assert result.incident_id is not None
    return result.incident_id


def create_ready_release(
    store: MaintenanceStore,
    *,
    incident_id: str,
    release_id: str = "release-1",
    base_commit: str = "base-1",
    result_commit: str = "commit-1",
    result_version: str = "result-v1",
    config_version: str = "settings-v1",
):
    release = store.create_release(
        release_id=release_id,
        author_id="implementation-agent",
        base_commit=base_commit,
        result_commit=result_commit,
        result_version=result_version,
        config_version=config_version,
        required_case_ids=("contract-case-1",),
        created_at=BASE_TIME.isoformat(),
    )
    store.record_test_result(
        release_id=release_id,
        case_id="contract-case-1",
        attempt=1,
        result_version=result_version,
        status="PASS",
        recorded_at=(BASE_TIME + timedelta(seconds=1)).isoformat(),
    )
    store.record_release_review(
        release_id=release_id,
        reviewer_id="independent-reviewer",
        reviewed_version=result_version,
        status="approved",
        reviewed_at=(BASE_TIME + timedelta(seconds=2)).isoformat(),
    )
    record_successful_deployment_fixture(
        store,
        release_id=release_id,
        deployed_at=(BASE_TIME + timedelta(seconds=3)).isoformat(),
    )
    gate = store.evaluate_release_gate(
        release_id=release_id,
        evaluated_at=(BASE_TIME + timedelta(seconds=4)).isoformat(),
    )
    assert gate.status == "ready"
    return store.start_fix_job(
        incident_id=incident_id,
        release_id=release_id,
        job_id=f"job-{release_id}",
        started_at=(BASE_TIME + timedelta(seconds=5)).isoformat(),
    )
