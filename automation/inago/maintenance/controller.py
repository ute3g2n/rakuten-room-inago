"""Application-level operations for the maintenance state machine."""

from __future__ import annotations

from datetime import datetime

from automation.inago.maintenance.store import (
    DailyCheckResult,
    GateResult,
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
