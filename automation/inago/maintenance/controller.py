"""Application-level operations for the maintenance state machine."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from automation.inago.maintenance.store import (
    AIParentSessionRecord,
    DailyCheckResult,
    GateResult,
    JobInputManifest,
    JobFinalizeResult,
    JobStartResult,
    LeaseToken,
    MaintenanceStore,
)


class MaintenanceController:
    """Expose safe workflow operations while keeping persistence in the store."""

    def __init__(self, store: MaintenanceStore) -> None:
        self._store = store

    def record_daily_check(
        self,
        *,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
        check_status: str,
        observed_at: datetime | str,
        reason_code: str | None,
        incident: dict[str, object] | None,
    ) -> DailyCheckResult:
        return self._store.record_daily_check(
            source_launch_id=source_launch_id,
            source_run_id=source_run_id,
            target_date=target_date,
            check_status=check_status,
            observed_at=observed_at,
            reason_code=reason_code,
            incident=incident,
        )

    def evaluate_release_gate(
        self, *, release_id: str, evaluated_at: datetime | str
    ) -> GateResult:
        return self._store.evaluate_release_gate(
            release_id=release_id,
            evaluated_at=evaluated_at,
        )

    def bind_job_input_manifest(
        self,
        *,
        incident_id: str,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
        job_id: str,
        created_at: datetime | str,
    ) -> JobInputManifest:
        return self._store.bind_job_input_manifest(
            incident_id=incident_id,
            source_launch_id=source_launch_id,
            source_run_id=source_run_id,
            target_date=target_date,
            job_id=job_id,
            created_at=created_at,
        )

    def get_job_input_manifest(
        self,
        *,
        job_id: str,
        expected_identity: dict[str, object] | None = None,
    ) -> JobInputManifest:
        return self._store.get_job_input_manifest(
            job_id=job_id,
            expected_identity=expected_identity,
        )

    def bind_ai_parent_session(
        self,
        *,
        job_id: str,
        session_key: str,
        parent_key: str,
        role_name: str,
        thread_id: str,
        manifest_sha256: str,
        requested_model: str,
        requested_effort: str,
        requested_service_tier: str,
        requested_fast_mode: bool,
        role_config_sha256: str,
        sandbox: Mapping[str, object],
        created_at: datetime | str,
    ) -> AIParentSessionRecord:
        return self._store.bind_ai_parent_session(
            job_id=job_id,
            session_key=session_key,
            parent_key=parent_key,
            role_name=role_name,
            thread_id=thread_id,
            manifest_sha256=manifest_sha256,
            requested_model=requested_model,
            requested_effort=requested_effort,
            requested_service_tier=requested_service_tier,
            requested_fast_mode=requested_fast_mode,
            role_config_sha256=role_config_sha256,
            sandbox=sandbox,
            created_at=created_at,
        )

    def get_ai_parent_session(
        self, *, job_id: str, session_key: str
    ) -> AIParentSessionRecord | None:
        return self._store.get_ai_parent_session(
            job_id=job_id,
            session_key=session_key,
        )

    def record_ai_session_observation(
        self,
        *,
        job_id: str,
        session_key: str,
        thread_id: str,
        status: str,
        observed_at: datetime | str,
        observed_model: str | None = None,
        observed_effort: str | None = None,
        observed_service_tier: str | None = None,
        reason_code: str | None = None,
    ) -> str:
        return self._store.record_ai_session_observation(
            job_id=job_id,
            session_key=session_key,
            thread_id=thread_id,
            status=status,
            observed_at=observed_at,
            observed_model=observed_model,
            observed_effort=observed_effort,
            observed_service_tier=observed_service_tier,
            reason_code=reason_code,
        )

    def start_fix_job(
        self,
        *,
        incident_id: str,
        release_id: str,
        job_id: str,
        started_at: datetime | str,
        source_run_id: str | None = None,
    ) -> JobStartResult:
        # The store recomputes the gate inside the same write transaction.
        return self._store.start_fix_job(
            incident_id=incident_id,
            release_id=release_id,
            job_id=job_id,
            started_at=started_at,
            source_run_id=source_run_id,
        )

    def finalize_job(
        self,
        *,
        job_id: str,
        final_status: str,
        lease: LeaseToken,
        finalized_at: datetime | str,
        reason_code: str | None = None,
    ) -> JobFinalizeResult:
        return self._store.finalize_job(
            job_id=job_id,
            final_status=final_status,
            lease=lease,
            finalized_at=finalized_at,
            reason_code=reason_code,
        )
