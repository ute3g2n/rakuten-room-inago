"""Translate a durable next-day observation into the incident report contract."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from automation.inago.maintenance.store import DeploymentVerificationRecord


def report_source(
    verification: DeploymentVerificationRecord,
) -> dict[str, str | None]:
    """Return the report fields without claiming an unobserved run succeeded."""

    observed_at = None
    if verification.observed_at is not None:
        observed = datetime.fromisoformat(verification.observed_at)
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("verification_observation_timezone_missing")
        observed_at = observed.astimezone(ZoneInfo("Asia/Tokyo")).isoformat(
            timespec="seconds"
        )
    evidence_href = (
        verification.evidence_ref
        if verification.status in {"verified", "recurred", "blocked"}
        else None
    )
    return {
        "status": verification.status,
        "observed_at": observed_at,
        "evidence_href": evidence_href,
    }
