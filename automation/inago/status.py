"""Read-only status synthesis for one inago daily run."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from automation.inago.run_lock import (
    LockOwnerObservation,
    ProcessObservation,
    observe_process_owner,
)


HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_STALE_AFTER_SECONDS = 120
PROGRESS_STALLED_AFTER_SECONDS = 600
MONITOR_STALE_AFTER_SECONDS = 120
_KNOWN_RUN_STATUSES = {
    "created",
    "running",
    "complete",
    "partial",
    "failed",
    "shortfall",
    "missed",
}


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("status timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        try:
            return _aware_utc(value)
        except ValueError:
            return None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        return _aware_utc(parsed)
    except ValueError:
        return None


def _timestamp_text(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def _age_field(
    *,
    value: datetime | str | None,
    now: datetime,
    stale_after_seconds: int,
    current_label: str,
    stale_label: str,
    unknown_label: str = "unknown",
) -> dict[str, Any]:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return {
            "status": unknown_label,
            "last_updated_at": None,
            "age_seconds": None,
            "stale_after_seconds": stale_after_seconds,
        }
    age = (now - timestamp).total_seconds()
    if age < 0:
        return {
            "status": unknown_label,
            "last_updated_at": _timestamp_text(timestamp),
            "age_seconds": round(age),
            "stale_after_seconds": stale_after_seconds,
        }
    return {
        "status": stale_label if age >= stale_after_seconds else current_label,
        "last_updated_at": _timestamp_text(timestamp),
        "age_seconds": int(age),
        "stale_after_seconds": stale_after_seconds,
    }


def _permissions() -> dict[str, bool]:
    return {
        "kill_process": False,
        "release_lock": False,
        "restore_database": False,
        "send_notification": False,
    }


def _unknown_fields(now: datetime, *, monitor_status: str) -> dict[str, Any]:
    observed_at = _timestamp_text(now)
    return {
        "heartbeat": {
            "status": "unknown",
            "last_updated_at": None,
            "age_seconds": None,
            "expected_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
            "stale_after_seconds": HEARTBEAT_STALE_AFTER_SECONDS,
        },
        "progress": {
            "status": "unknown",
            "last_updated_at": None,
            "age_seconds": None,
            "stalled_after_seconds": PROGRESS_STALLED_AFTER_SECONDS,
        },
        "process": {
            "status": "unknown",
            "pid": None,
            "expected_start_time": None,
            "observed_start_time": None,
            "reason": "status_observation_unavailable",
        },
        "lock": {
            "status": "unknown",
            "observation": "owner_file_metadata_only",
            "owner": None,
            "reason": "status_observation_unavailable",
        },
        "monitor": {
            "status": monitor_status,
            "observed_at": observed_at,
            "stale_after_seconds": MONITOR_STALE_AFTER_SECONDS,
        },
    }


def build_status_snapshot(
    *,
    run_id: str,
    run_status: str,
    now: datetime,
    heartbeat_at: datetime | str | None,
    progress_at: datetime | str | None,
    monitor_updated_at: datetime | str | None,
    lock_observation: LockOwnerObservation,
    process_start_time_reader: Callable[[int], str] | None = None,
    failure_kind: str | None = None,
    database_integrity: str = "ok",
    monitor_error: str | None = None,
    previous_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fresh status view; a failed observation never reuses old health."""

    current = _aware_utc(now)
    current_text = _timestamp_text(current)
    if monitor_error is not None:
        return {
            "run_id": run_id,
            "status": "unknown",
            "observed_at": current_text,
            "reason_codes": ["status_observation_unavailable"],
            "fields": _unknown_fields(current, monitor_status="unavailable"),
            "previous_snapshot_reused": False,
            "permissions": _permissions(),
        }

    heartbeat = _age_field(
        value=heartbeat_at,
        now=current,
        stale_after_seconds=HEARTBEAT_STALE_AFTER_SECONDS,
        current_label="fresh",
        stale_label="stale",
    )
    heartbeat["expected_interval_seconds"] = HEARTBEAT_INTERVAL_SECONDS
    progress = _age_field(
        value=progress_at,
        now=current,
        stale_after_seconds=PROGRESS_STALLED_AFTER_SECONDS,
        current_label="advancing",
        stale_label="stalled",
    )
    progress["stalled_after_seconds"] = progress.pop("stale_after_seconds")
    monitor = _age_field(
        value=monitor_updated_at,
        now=current,
        stale_after_seconds=MONITOR_STALE_AFTER_SECONDS,
        current_label="current",
        stale_label="stale",
        unknown_label="unavailable",
    )
    monitor["observed_at"] = monitor.pop("last_updated_at")

    owner = lock_observation.owner
    if lock_observation.status == "present" and owner is not None:
        process = observe_process_owner(
            owner, start_time_reader=process_start_time_reader
        )
        lock_status = (
            "owner_metadata_present" if owner.run_id == run_id else "run_mismatch"
        )
    else:
        process = ProcessObservation(
            "unknown", 0, "", None, "owner_metadata_unavailable"
        )
        lock_status = lock_observation.status

    fields = {
        "heartbeat": heartbeat,
        "progress": progress,
        "process": {
            "status": process.status,
            "pid": process.pid if owner is not None else None,
            "expected_start_time": process.expected_start_time or None,
            "observed_start_time": process.observed_start_time,
            "reason": process.reason,
        },
        "lock": {
            "status": lock_status,
            "observation": "owner_file_metadata_only",
            "owner": owner.to_dict() if owner is not None else None,
            "reason": lock_observation.reason,
        },
        "monitor": {
            **monitor,
            "stale_after_seconds": MONITOR_STALE_AFTER_SECONDS,
        },
    }
    reasons: list[str] = []
    if monitor["status"] != "current":
        status = "unknown"
        reasons.append("monitor_update_not_current")
    elif database_integrity != "ok":
        status = "recovery_needed"
        reasons.append("database_integrity_not_confirmed")
    elif failure_kind == "auth_wait":
        status = "auth_wait"
        reasons.append("authentication_requires_owner")
    elif failure_kind == "save_error":
        status = "save_error"
        reasons.append("database_save_failed")
    elif failure_kind is not None:
        status = "unknown"
        reasons.append("failure_kind_unknown")
    elif run_status not in _KNOWN_RUN_STATUSES:
        status = "unknown"
        reasons.append("run_status_unknown")
    elif run_status != "running":
        status = {
            "complete": "complete",
            "partial": "partial",
            "failed": "failed",
            "shortfall": "shortfall",
            "missed": "missed",
            "created": "not_started",
        }[run_status]
        reasons.append(f"run_{run_status}")
    elif lock_status != "owner_metadata_present":
        status = (
            "recovery_needed"
            if lock_status in {"missing", "run_mismatch"}
            else "unknown"
        )
        reasons.append("lock_owner_unavailable_or_mismatched")
    elif process.status == "dead":
        status = "recovery_needed"
        reasons.append("owner_process_not_running")
    elif process.status in {"unknown", "reused"}:
        status = "unknown"
        reasons.append(process.reason)
    elif heartbeat["status"] == "stale":
        status = "stalled"
        reasons.append("heartbeat_stale")
    elif progress["status"] == "stalled":
        status = "stalled"
        reasons.append("progress_stalled")
    elif heartbeat["status"] != "fresh" or progress["status"] != "advancing":
        status = "unknown"
        reasons.append("heartbeat_or_progress_unavailable")
    else:
        status = "healthy"
        reasons.append("run_observed_current")

    return {
        "run_id": run_id,
        "status": status,
        "observed_at": current_text,
        "reason_codes": reasons,
        "fields": fields,
        "previous_snapshot_reused": False,
        "permissions": _permissions(),
    }
