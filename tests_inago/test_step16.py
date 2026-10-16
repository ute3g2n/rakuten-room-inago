"""Read-only run status and safe recovery decision contracts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from automation.inago.recovery import (
    RecoveryPlanError,
    build_recovery_request,
    plan_resume,
)
from automation.inago.run_lock import (
    LockOwner,
    observe_process_owner,
    read_lock_owner,
)
from automation.inago.status import build_status_snapshot


NOW = datetime(2026, 10, 13, 13, 40, tzinfo=timezone.utc)
OWNER = LockOwner(4321, NOW.isoformat(), "RUN-2026-10-13")
JST = timezone(timedelta(hours=9))


def _owner_file(tmp_path, owner: LockOwner = OWNER):
    path = tmp_path / "daily-owner.json"
    path.write_text(json.dumps(owner.to_dict()), encoding="utf-8")
    return path


def _status(tmp_path, **overrides):
    args = {
        "run_id": OWNER.run_id,
        "run_status": "running",
        "now": NOW,
        "heartbeat_at": NOW - timedelta(seconds=30),
        "progress_at": NOW - timedelta(minutes=2),
        "monitor_updated_at": NOW,
        "lock_observation": read_lock_owner(_owner_file(tmp_path)),
        "process_start_time_reader": lambda _pid: OWNER.process_start_time,
    }
    args.update(overrides)
    return build_status_snapshot(**args)


def test_process_owner_uses_creation_time_and_keeps_permission_failures_unknown(
    tmp_path,
) -> None:
    observation = read_lock_owner(_owner_file(tmp_path))
    assert observation.status == "present"
    assert observation.owner == OWNER

    alive = observe_process_owner(
        OWNER, start_time_reader=lambda _pid: OWNER.process_start_time
    )
    reused = observe_process_owner(
        OWNER,
        start_time_reader=lambda _pid: "2026-10-12T13:40:00+00:00",
    )

    def inaccessible(_pid):
        raise PermissionError("access denied")

    unknown = observe_process_owner(OWNER, start_time_reader=inaccessible)

    def absent(_pid):
        raise ProcessLookupError("not found")

    dead = observe_process_owner(OWNER, start_time_reader=absent)
    assert alive.status == "alive"
    assert reused.status == "reused"
    assert unknown.status == "unknown"
    assert dead.status == "dead"
    assert read_lock_owner(_owner_file(tmp_path)).owner == OWNER


def test_running_status_separates_heartbeat_progress_process_lock_and_monitor(
    tmp_path,
) -> None:
    snapshot = _status(tmp_path)

    assert snapshot["status"] == "healthy"
    assert set(snapshot["fields"]) == {
        "heartbeat",
        "progress",
        "process",
        "lock",
        "monitor",
    }
    assert snapshot["fields"]["heartbeat"]["expected_interval_seconds"] == 30
    assert snapshot["fields"]["heartbeat"]["stale_after_seconds"] == 120
    assert snapshot["fields"]["progress"]["stalled_after_seconds"] == 600
    assert snapshot["fields"]["process"]["status"] == "alive"
    assert snapshot["fields"]["lock"]["status"] == "owner_metadata_present"
    assert snapshot["fields"]["monitor"]["status"] == "current"
    assert snapshot["permissions"] == {
        "kill_process": False,
        "release_lock": False,
        "restore_database": False,
        "send_notification": False,
    }


@pytest.mark.parametrize(
    ("heartbeat_age", "progress_age", "expected_reason"),
    [
        (121, 10, "heartbeat_stale"),
        (120, 10, "heartbeat_stale"),
        (10, 601, "progress_stalled"),
        (10, 600, "progress_stalled"),
    ],
)
def test_stale_heartbeat_or_progress_is_never_reported_healthy(
    tmp_path, heartbeat_age, progress_age, expected_reason
) -> None:
    snapshot = _status(
        tmp_path,
        heartbeat_at=NOW - timedelta(seconds=heartbeat_age),
        progress_at=NOW - timedelta(seconds=progress_age),
    )
    assert snapshot["status"] == "stalled"
    assert expected_reason in snapshot["reason_codes"]


@pytest.mark.parametrize(
    ("reader", "expected_status"),
    [
        (lambda _pid: "2026-10-12T13:40:00+00:00", "unknown"),
        (lambda _pid: (_ for _ in ()).throw(PermissionError()), "unknown"),
        (lambda _pid: (_ for _ in ()).throw(ProcessLookupError()), "recovery_needed"),
    ],
)
def test_unverifiable_or_dead_lock_owner_never_causes_automatic_unlock(
    tmp_path, reader, expected_status
) -> None:
    snapshot = _status(tmp_path, process_start_time_reader=reader)
    assert snapshot["status"] == expected_status
    assert snapshot["permissions"]["release_lock"] is False
    assert snapshot["permissions"]["kill_process"] is False


def test_missing_or_mismatched_owner_and_stale_monitor_fail_closed(tmp_path) -> None:
    missing = read_lock_owner(tmp_path / "missing-owner.json")
    no_owner = _status(tmp_path, lock_observation=missing)
    mismatch = _status(tmp_path, run_id="OTHER-RUN")
    failed_observation = _status(
        tmp_path,
        monitor_error="database_status_read_failed",
        previous_snapshot={"status": "healthy"},
    )
    old_monitor = _status(tmp_path, monitor_updated_at=NOW - timedelta(minutes=3))

    assert no_owner["status"] == "recovery_needed"
    assert mismatch["status"] == "recovery_needed"
    assert failed_observation["status"] == "unknown"
    assert failed_observation["fields"]["monitor"]["status"] == "unavailable"
    assert failed_observation["previous_snapshot_reused"] is False
    assert old_monitor["status"] == "unknown"
    assert old_monitor["fields"]["monitor"]["status"] == "stale"


@pytest.mark.parametrize(
    ("run_status", "failure_kind", "expected"),
    [
        ("failed", "auth_wait", "auth_wait"),
        ("failed", "save_error", "save_error"),
        ("partial", None, "partial"),
    ],
)
def test_run_failure_categories_remain_distinct(
    tmp_path, run_status, failure_kind, expected
) -> None:
    snapshot = _status(
        tmp_path,
        run_status=run_status,
        failure_kind=failure_kind,
    )
    assert snapshot["status"] == expected


def test_unrecognized_failure_category_never_falls_through_to_healthy(tmp_path) -> None:
    snapshot = _status(tmp_path, failure_kind="unrecognized_failure")
    assert snapshot["status"] == "unknown"
    assert "failure_kind_unknown" in snapshot["reason_codes"]


def test_recovery_request_is_explicit_redacted_and_diagnostic_only() -> None:
    manifest = {
        "checkpoint": {
            "phase": "copy_pending",
            "committed_count": 14,
            "api_token": "SECRET-FIELD-VALUE",
        },
        "logs": (
            "Authorization: Bearer SECRET-HEADER\n"
            "Authorization: Basic SECRET-BASIC\n"
            "Cookie: session=SECRET-COOKIE\n"
            "api_key=SECRET-INLINE"
        ),
        "integrity": {"database": "ok", "lock_owner_matches": True},
    }

    with pytest.raises(ValueError, match="explicit"):
        build_recovery_request(
            run_id=OWNER.run_id,
            target_date="2026-10-13",
            failed_step="copy",
            failure_manifest=manifest,
            explicit_user_request=False,
        )

    request = build_recovery_request(
        run_id=OWNER.run_id,
        target_date="2026-10-13",
        failed_step="copy",
        failure_manifest=manifest,
        explicit_user_request=True,
    )
    serialized = json.dumps(request, ensure_ascii=False)
    assert request["agent_id"] == "AG-RECOVERY"
    assert request["task_id"] == "T08"
    assert request["input_payload"]["failed_run_id"] == OWNER.run_id
    assert request["input_payload"]["failed_step"] == "copy"
    assert (
        request["input_payload"]["failure_manifest"]["evidence"]["checkpoint"][
            "committed_count"
        ]
        == 14
    )
    assert "SECRET-FIELD-VALUE" not in serialized
    assert "SECRET-HEADER" not in serialized
    assert "SECRET-BASIC" not in serialized
    assert "SECRET-COOKIE" not in serialized
    assert "SECRET-INLINE" not in serialized
    assert "[REDACTED]" in serialized
    assert request["side_effects"] == []

    large = build_recovery_request(
        run_id=OWNER.run_id,
        target_date="2026-10-13",
        failed_step="copy",
        failure_manifest={f"log-{index}": "safe-detail " * 500 for index in range(64)},
        explicit_user_request=True,
    )
    assert len(json.dumps(large, ensure_ascii=False).encode("utf-8")) < 32768
    assert large["input_payload"]["failure_manifest"]["evidence"]["truncated"] is True


def _resume_snapshot(**overrides):
    result = {
        "run_id": "R1",
        "target_date": "2026-10-13",
        "settings_version": "S1",
        "seed": "K1",
        "search_attempts": 8,
        "unfinished_generation_attempts": {"A15": 1},
        "committed_count": 14,
        "run_status": "running",
        "metrics_pending": True,
    }
    result.update(overrides)
    return result


def test_owner_confirmed_resume_preserves_run_and_consumed_budgets() -> None:
    decision = plan_resume(
        _resume_snapshot(),
        now=datetime(2026, 10, 13, 13, 35, tzinfo=JST),
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=True,
    )
    assert decision["action"] == "resume_remaining"
    assert decision["preserved"] == {
        "run_id": "R1",
        "target_date": "2026-10-13",
        "settings_version": "S1",
        "seed": "K1",
        "search_attempts": 8,
        "unfinished_generation_attempts": {"A15": 1},
        "committed_count": 14,
        "run_status": "running",
        "metrics_pending": True,
    }
    assert decision["allowed"] == {
        "search": True,
        "generation": True,
        "metrics": True,
        "saved_export": True,
        "report_intake": True,
        "kill_process": False,
        "release_lock": False,
        "restore_database": False,
        "send_notification": False,
    }


@pytest.mark.parametrize(
    ("now", "expected_action", "allow_search", "allow_generation", "allow_metrics"),
    [
        (
            datetime(2026, 10, 13, 17, 45, tzinfo=JST),
            "resume_remaining",
            True,
            True,
            True,
        ),
        (
            datetime(2026, 10, 13, 18, 0, tzinfo=JST),
            "resume_remaining",
            True,
            True,
            True,
        ),
    ],
)
def test_resume_after_1745_and_1800_keeps_daily_work_available(
    now, expected_action, allow_search, allow_generation, allow_metrics
) -> None:
    decision = plan_resume(
        _resume_snapshot(),
        now=now,
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=True,
    )
    assert decision["action"] == expected_action
    assert decision["allowed"]["search"] is allow_search
    assert decision["allowed"]["generation"] is allow_generation
    assert decision["allowed"]["metrics"] is allow_metrics
    assert decision["allowed"]["report_intake"] is True


def test_completed_and_export_only_runs_never_generate_again() -> None:
    completed = plan_resume(
        _resume_snapshot(run_status="complete"),
        now=datetime(2026, 10, 13, 15, 0, tzinfo=JST),
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=True,
    )
    export_retry = plan_resume(
        _resume_snapshot(run_status="complete", metrics_pending=False),
        now=datetime(2026, 10, 13, 18, 10, tzinfo=JST),
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=True,
        export_only_failed=True,
    )
    assert completed["action"] == "completed_run_status_or_export"
    assert completed["allowed"]["search"] is False
    assert completed["allowed"]["generation"] is False
    assert completed["allowed"]["metrics"] is True
    assert export_retry["action"] == "reexport_saved_only"
    assert export_retry["allowed"]["saved_export"] is True
    assert export_retry["allowed"]["generation"] is False
    assert export_retry["allowed"]["report_intake"] is True


def test_resume_plan_does_not_start_early_or_reopen_another_date() -> None:
    before_start = plan_resume(
        _resume_snapshot(),
        now=datetime(2026, 10, 13, 12, 59, tzinfo=JST),
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=True,
    )
    future = plan_resume(
        _resume_snapshot(),
        now=datetime(2026, 10, 12, 13, 0, tzinfo=JST),
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=True,
    )
    exhausted = plan_resume(
        _resume_snapshot(unfinished_generation_attempts={"A15": 3}),
        now=datetime(2026, 10, 13, 13, 0, tzinfo=JST),
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=True,
    )

    assert before_start["action"] == "daily_start_not_reached"
    assert before_start["allowed"]["search"] is False
    assert before_start["allowed"]["generation"] is False
    assert future["action"] == "future_run_not_started"
    assert future["allowed"]["search"] is False
    assert future["allowed"]["report_intake"] is True
    assert exhausted["allowed"]["generation"] is False


@pytest.mark.parametrize("integrity", ["corrupt", "unknown"])
def test_unconfirmed_or_corrupt_recovery_stops_without_restoration(integrity) -> None:
    unconfirmed = plan_resume(
        _resume_snapshot(),
        now=NOW,
        database_integrity="ok",
        owner_confirmed=False,
        cause_resolved=False,
    )
    corrupt = plan_resume(
        _resume_snapshot(),
        now=NOW,
        database_integrity=integrity,
        owner_confirmed=False,
        cause_resolved=False,
    )
    assert unconfirmed["action"] == "owner_action_required"
    assert unconfirmed["allowed"]["search"] is False
    assert corrupt["action"] == "database_integrity_review_required"
    assert corrupt["allowed"]["restore_database"] is False
    assert corrupt["allowed"]["search"] is False
    assert corrupt["allowed"]["report_intake"] is False


def test_resume_plan_rejects_missing_or_invalid_checkpoint_identity() -> None:
    with pytest.raises(RecoveryPlanError):
        plan_resume(
            _resume_snapshot(seed=""),
            now=NOW,
            database_integrity="ok",
            owner_confirmed=True,
            cause_resolved=True,
        )
