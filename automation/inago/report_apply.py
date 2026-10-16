"""Apply the frozen prior-day human report cohort for a daily run."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from automation.inago.clock import Clock, SystemClock
from automation.inago.repository import (
    apply_human_action_in_transaction,
    pending_posted_offer_keys,
)
from automation.inago.report_trace import write_report_event


JST = ZoneInfo("Asia/Tokyo")
_CHECKPOINT_KEY = "report_apply"
_CHECKPOINT_VERSION = 1


@dataclass(frozen=True)
class ReportApplyResult:
    """Report a run's frozen cohort, committed work, and exploration gate."""

    run_id: str
    cohort_action_ids: tuple[str, ...]
    applied_action_ids: tuple[str, ...]
    pending_action_ids: tuple[str, ...]
    blocked_offer_keys: tuple[str, ...]
    exploration_allowed: bool
    error: str | None = None


class ReportApplyError(ValueError):
    """Raised for a report or checkpoint that cannot be safely reconciled."""


def _parse_aware_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ReportApplyError(f"{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReportApplyError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReportApplyError(f"{field}_timezone_missing")
    return parsed


def _read_checkpoint(value: str | None) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReportApplyError("run_checkpoint_invalid_json") from exc
    if not isinstance(payload, dict):
        raise ReportApplyError("run_checkpoint_not_object")
    return payload


def _cohort_from_checkpoint(
    checkpoint: Mapping[str, Any], target_date: str
) -> tuple[str, ...] | None:
    frozen = checkpoint.get(_CHECKPOINT_KEY)
    if frozen is None:
        return None
    if not isinstance(frozen, dict):
        raise ReportApplyError("report_cohort_checkpoint_invalid")
    if frozen.get("schema_version") != _CHECKPOINT_VERSION:
        raise ReportApplyError("report_cohort_checkpoint_version_unsupported")
    if frozen.get("target_date") != target_date:
        raise ReportApplyError("report_cohort_target_date_mismatch")
    action_ids = frozen.get("action_ids")
    if (
        not isinstance(action_ids, list)
        or any(
            not isinstance(action_id, str) or not action_id for action_id in action_ids
        )
        or len(action_ids) != len(set(action_ids))
    ):
        raise ReportApplyError("report_cohort_action_ids_invalid")
    return tuple(action_ids)


def _capture_cohort(
    connection: sqlite3.Connection,
    run_id: str,
    captured_at: str,
) -> tuple[str, ...]:
    if connection.in_transaction:
        raise ReportApplyError("database_transaction_already_open")
    connection.execute("BEGIN IMMEDIATE")
    try:
        run = connection.execute(
            "SELECT target_date, mode, status, checkpoint FROM run WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if run is None or run["mode"] != "daily":
            raise ReportApplyError("daily_run_not_found")
        if run["status"] not in {
            "created",
            "running",
            "partial",
            "failed",
            "shortfall",
        }:
            raise ReportApplyError("daily_run_not_resumable")
        try:
            target = date.fromisoformat(str(run["target_date"]))
        except ValueError as exc:
            raise ReportApplyError("daily_run_target_date_invalid") from exc
        target_date = target.isoformat()
        checkpoint = _read_checkpoint(run["checkpoint"])
        cohort = _cohort_from_checkpoint(checkpoint, target_date)
        if cohort is None:
            eligible: list[tuple[datetime, str, int, str]] = []
            pending = connection.execute(
                """
                SELECT action_id, action, offer_key, receipt_revision, reported_at
                  FROM human_action
                 WHERE apply_status='pending'
                """
            ).fetchall()
            for row in pending:
                reported_at = _parse_aware_timestamp(row["reported_at"], "reported_at")
                report_day = reported_at.astimezone(JST).date()
                if report_day < target:
                    eligible.append(
                        (
                            reported_at.astimezone(timezone.utc),
                            str(row["offer_key"]),
                            int(row["receipt_revision"]),
                            str(row["action_id"]),
                        )
                    )
            eligible.sort()
            cohort = tuple(item[3] for item in eligible)
            checkpoint[_CHECKPOINT_KEY] = {
                "schema_version": _CHECKPOINT_VERSION,
                "target_date": target_date,
                "captured_at": captured_at,
                "action_ids": list(cohort),
            }
            connection.execute(
                "UPDATE run SET checkpoint=? WHERE run_id=?",
                (
                    json.dumps(
                        checkpoint,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    run_id,
                ),
            )
        connection.commit()
        return cohort
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_action_in_transaction(
    connection: sqlite3.Connection,
    action: Mapping[str, Any],
    run_id: str,
    applied_at: str,
) -> None:
    """Delegate atomic management updates to the SQLite repository layer."""

    apply_human_action_in_transaction(connection, action, run_id, applied_at)


def _current_pending_for_cohort(
    connection: sqlite3.Connection, cohort: tuple[str, ...]
) -> tuple[str, ...]:
    if not cohort:
        return ()
    states: dict[str, str] = {}
    for offset in range(0, len(cohort), 500):
        batch = cohort[offset : offset + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"SELECT action_id, apply_status FROM human_action "
            f"WHERE action_id IN ({placeholders})",
            batch,
        ).fetchall()
        states.update({str(row["action_id"]): str(row["apply_status"]) for row in rows})
    return tuple(
        action_id for action_id in cohort if states.get(action_id) != "applied"
    )


def _record_apply_error(
    connection: sqlite3.Connection, action_id: str, error_code: str
) -> None:
    """Keep a safe diagnostic code while preserving the pending report."""

    try:
        if connection.in_transaction:
            connection.rollback()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE human_action SET apply_error=? "
            "WHERE action_id=? AND apply_status='pending'",
            (error_code, action_id),
        )
        connection.commit()
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()


def apply_pending_reports(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    clock: Clock | None = None,
    data_root: str | Path | None = None,
) -> ReportApplyResult:
    """Freeze and apply all pending reports from days before this daily run."""

    system_clock = clock or SystemClock()
    trace_target_date: str | None = None
    try:
        run = connection.execute(
            "SELECT target_date FROM run WHERE run_id=?", (run_id,)
        ).fetchone()
        trace_target_date = str(run["target_date"]) if run is not None else None
        captured_at = system_clock.now_jst()
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ReportApplyError("clock_timezone_missing")
        captured_at_text = captured_at.astimezone(JST).isoformat()
        cohort = _capture_cohort(connection, run_id, captured_at_text)
    except Exception as exc:
        return ReportApplyResult(
            run_id=run_id,
            cohort_action_ids=(),
            applied_action_ids=(),
            pending_action_ids=(),
            blocked_offer_keys=pending_posted_offer_keys(connection),
            exploration_allowed=False,
            error=(
                exc.args[0]
                if isinstance(exc, ReportApplyError) and exc.args
                else "cohort_capture_failed"
            ),
        )

    applied_this_call: list[str] = []
    error: str | None = None
    for action_id in cohort:
        action_hint: sqlite3.Row | None = None
        try:
            if connection.in_transaction:
                raise ReportApplyError("database_transaction_already_open")
            action_hint = connection.execute(
                "SELECT action_id, action, draft_id, draft_version, receipt_revision, "
                "apply_status FROM human_action WHERE action_id=?",
                (action_id,),
            ).fetchone()
            if (
                data_root is not None
                and trace_target_date is not None
                and action_hint is not None
                and action_hint["apply_status"] == "pending"
            ):
                write_report_event(
                    data_root=data_root,
                    run_id=run_id,
                    target_date=trace_target_date,
                    component="report_apply",
                    operation="report_apply",
                    phase="begin",
                    action_id=str(action_hint["action_id"]),
                    clock=system_clock,
                    draft_id=str(action_hint["draft_id"]),
                    draft_version=int(action_hint["draft_version"]),
                    report_action=str(action_hint["action"]),
                    receipt_revision=int(action_hint["receipt_revision"]),
                    apply_status="pending",
                )
            connection.execute("BEGIN IMMEDIATE")
            action = connection.execute(
                "SELECT * FROM human_action WHERE action_id=?",
                (action_id,),
            ).fetchone()
            if action is None:
                raise ReportApplyError("frozen_report_missing")
            if action["apply_status"] == "applied":
                connection.commit()
                continue
            if action["apply_status"] != "pending":
                raise ReportApplyError("frozen_report_not_pending")
            applied_at = system_clock.now_jst()
            if applied_at.tzinfo is None or applied_at.utcoffset() is None:
                raise ReportApplyError("clock_timezone_missing")
            _apply_action_in_transaction(
                connection,
                action,
                run_id,
                applied_at.astimezone(JST).isoformat(),
            )
            connection.commit()
            applied_this_call.append(action_id)
            if data_root is not None and trace_target_date is not None:
                write_report_event(
                    data_root=data_root,
                    run_id=run_id,
                    target_date=trace_target_date,
                    component="report_apply",
                    operation="report_apply",
                    phase="end",
                    action_id=str(action["action_id"]),
                    clock=system_clock,
                    draft_id=str(action["draft_id"]),
                    draft_version=int(action["draft_version"]),
                    report_action=str(action["action"]),
                    receipt_revision=int(action["receipt_revision"]),
                    apply_status="applied",
                )
        except Exception as exc:
            if connection.in_transaction:
                connection.rollback()
            error = (
                exc.args[0]
                if isinstance(exc, ReportApplyError) and exc.args
                else "report_apply_failed"
            )
            _record_apply_error(connection, action_id, error)
            if (
                data_root is not None
                and trace_target_date is not None
                and action_hint is not None
            ):
                write_report_event(
                    data_root=data_root,
                    run_id=run_id,
                    target_date=trace_target_date,
                    component="report_apply",
                    operation="report_apply",
                    phase="error",
                    action_id=str(action_hint["action_id"]),
                    clock=system_clock,
                    draft_id=str(action_hint["draft_id"]),
                    draft_version=int(action_hint["draft_version"]),
                    report_action=str(action_hint["action"]),
                    receipt_revision=int(action_hint["receipt_revision"]),
                    apply_status="pending",
                    error_code=error,
                )
            break

    pending = _current_pending_for_cohort(connection, cohort)
    return ReportApplyResult(
        run_id=run_id,
        cohort_action_ids=cohort,
        applied_action_ids=tuple(applied_this_call),
        pending_action_ids=pending,
        blocked_offer_keys=pending_posted_offer_keys(connection),
        exploration_allowed=not pending and error is None,
        error=error,
    )
