"""Shared daily execution lifecycle for production and isolated probes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
import hashlib
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping
import uuid

from automation.inago.clock import Clock, JST


_BLOCKED_STATUSES = {
    "locked",
    "recovery_confirmation_required",
    "recovery_required",
}
_SAFE_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")


def _pipeline_code_sha256() -> str | None:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return None


def _pipeline_capture_location() -> tuple[str, str, int | None]:
    """Return the safe pipeline call site that observed the failure."""

    try:
        frame = sys._getframe(1)
        module = frame.f_globals.get("__name__")
        if not isinstance(module, str) or module != __name__:
            module = __name__
        return module, frame.f_code.co_name, frame.f_lineno
    except Exception:
        return __name__, "run_daily_execution_core", None


def _best_effort_log(level: int, message: str, *, extra: Mapping[str, Any]) -> None:
    """Keep diagnostic handlers from changing the daily execution result."""

    try:
        logging.getLogger(__name__).log(level, message, extra=dict(extra))
    except Exception:
        return


def _sqlite_failure_labels(exc: BaseException | None) -> tuple[str, str]:
    name = getattr(exc, "sqlite_errorname", None)
    known = {
        "SQLITE_BUSY",
        "SQLITE_LOCKED",
        "SQLITE_CONSTRAINT",
        "SQLITE_CONSTRAINT_CHECK",
        "SQLITE_CONSTRAINT_FOREIGNKEY",
        "SQLITE_CONSTRAINT_NOTNULL",
        "SQLITE_CONSTRAINT_PRIMARYKEY",
        "SQLITE_CONSTRAINT_UNIQUE",
        "SQLITE_MISMATCH",
    }
    if name not in known:
        name = "unknown"
    constraint = {
        "SQLITE_CONSTRAINT_UNIQUE": "unique",
        "SQLITE_CONSTRAINT_PRIMARYKEY": "primary_key",
        "SQLITE_CONSTRAINT_NOTNULL": "not_null",
        "SQLITE_CONSTRAINT_CHECK": "check",
        "SQLITE_CONSTRAINT_FOREIGNKEY": "foreign_key",
    }.get(name, "unknown")
    return name, constraint


def _safe_sqlite_error_code(exc: BaseException | None) -> int | None:
    """Keep only SQLite's bounded numeric code, without exception text."""

    try:
        value = getattr(exc, "sqlite_errorcode", None)
    except Exception:
        return None
    if type(value) is int and 0 <= value <= 65535:
        return value
    return None


def _safe_diagnostic_class(exc: BaseException) -> str:
    """Return an exception class name without copying its message or traceback."""

    name = type(exc).__name__
    return name if _SAFE_CODE.fullmatch(name) else "unknown"


def _safe_exception_chain_details(
    exc: BaseException,
) -> tuple[list[dict[str, str]], bool]:
    """Return safe exception links and whether the bounded list was truncated."""

    chain: list[dict[str, str]] = []
    pending: list[tuple[BaseException, str]] = [(exc, "raised")]
    visited: set[int] = set()
    while pending and len(chain) < 12:
        current, relation = pending.pop(0)
        if id(current) in visited:
            continue
        visited.add(id(current))
        chain.append(
            {
                "relation": relation,
                "exception_class": _safe_diagnostic_class(current),
            }
        )
        cause = current.__cause__
        context = current.__context__
        if cause is not None:
            pending.append((cause, "cause"))
        if context is not None and context is not cause:
            pending.append(
                (
                    context,
                    "context_suppressed" if current.__suppress_context__ else "context",
                )
            )
    truncated = any(id(current) not in visited for current, _ in pending)
    return chain, truncated


def _safe_exception_chain(exc: BaseException) -> list[dict[str, str]]:
    """Keep exception types and links while omitting messages and locals."""

    chain, _ = _safe_exception_chain_details(exc)
    return chain


def _safe_exception_frames(exc: BaseException) -> list[dict[str, str | int]]:
    """Return traceback frames only when their source is inside this repository."""

    try:
        repository_root = Path(__file__).resolve().parents[2]
        frames: list[dict[str, str | int]] = []
        pending: list[tuple[BaseException, str]] = [(exc, "raised")]
        visited: set[int] = set()
        while pending:
            current, relation = pending.pop(0)
            if id(current) in visited:
                continue
            visited.add(id(current))
            cause = current.__cause__
            context = current.__context__
            if cause is not None:
                pending.append((cause, "cause"))
            if context is not None and context is not cause:
                pending.append(
                    (
                        context,
                        "context_suppressed"
                        if current.__suppress_context__
                        else "context",
                    )
                )
            frame = current.__traceback__
            local_frame_count = 0
            while frame is not None and local_frame_count < 8:
                code = frame.tb_frame.f_code
                try:
                    source = Path(code.co_filename).resolve()
                    relative_source = source.relative_to(repository_root).as_posix()
                except (OSError, RuntimeError, TypeError, ValueError):
                    frame = frame.tb_next
                    continue
                function = code.co_name
                module = frame.tb_frame.f_globals.get("__name__")
                frames.append(
                    {
                        "exception_class": _safe_diagnostic_class(current),
                        "relation": relation,
                        "path": relative_source,
                        "module": (
                            module
                            if isinstance(module, str) and _SAFE_CODE.fullmatch(module)
                            else "unknown"
                        ),
                        "function": (
                            function if _SAFE_CODE.fullmatch(function) else "unknown"
                        ),
                        "line": int(frame.tb_lineno),
                    }
                )
                local_frame_count += 1
                frame = frame.tb_next
        return frames
    except Exception:
        return []


def _secondary_diagnostic(
    operation: str,
    exc: Exception,
    capture_location: tuple[str, str, int | None],
) -> dict[str, Any]:
    """Build a safe, pipeline-local diagnostic for the existing CLI L01 path."""

    failure_module, failure_function, failure_line = capture_location
    error_name, constraint_label = _sqlite_failure_labels(exc)
    exception_chain, exception_chain_truncated = _safe_exception_chain_details(exc)
    return {
        "phase": "daily_diagnostic",
        "code": "daily_secondary_operation_failed",
        "operation": operation,
        "exception_class": _safe_diagnostic_class(exc),
        "error_code_number": _safe_sqlite_error_code(exc),
        "error_name": error_name,
        "constraint_label": constraint_label,
        "failure_module": failure_module,
        "failure_function": failure_function,
        "failure_line": failure_line,
        "code_sha256": _pipeline_code_sha256(),
        "exception_chain": exception_chain,
        "exception_chain_truncated": exception_chain_truncated,
        "exception_frames": _safe_exception_frames(exc),
    }


@dataclass(frozen=True, slots=True)
class DailyExecutionPorts:
    """Explicit adapters around the shared daily execution lifecycle."""

    data_root: Path
    preflight: Callable[[datetime], Any]
    open_database: Callable[[], sqlite3.Connection]
    prepare: Callable[[sqlite3.Connection, datetime, Any], Any]
    start_backup: Callable[[sqlite3.Connection, Any, datetime], Mapping[str, Any]]
    operations_monitor: Callable[[sqlite3.Connection, Any, str], Any | None]
    run_daily: Callable[
        [sqlite3.Connection, Any, Callable[[sqlite3.Connection], Any]], Any
    ]
    export_dashboard: Callable[[Any], Mapping[str, Any]]
    preserve_end_of_day: Callable[[Any, datetime, str | None], Mapping[str, Any]]
    failure_result: (
        Callable[[Exception, sqlite3.Connection | None, str, str], Mapping[str, Any]]
        | None
    ) = None


@dataclass(frozen=True, slots=True)
class _PipelineEvent:
    operation: str
    phase: str
    at: str
    elapsed_ms: int = 0
    status: str | None = None
    error_code: str | None = None
    active_stage: str | None = None
    last_completed_stage: str | None = None
    error_class: str | None = None
    error_code_number: int | None = None
    error_name: str | None = None
    constraint_label: str = "unknown"
    failure_line: int | None = None
    failure_module: str | None = None
    failure_function: str | None = None
    failure_code_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class _RecordedClock:
    value: datetime

    def now_jst(self) -> datetime:
        return self.value


class _DailyPipelineError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def ensure_daily_start_backup(
    connection: sqlite3.Connection,
    *,
    data_root: str | Path,
    backup_root: str | Path,
    now: datetime,
    fixture_mode: bool,
) -> dict[str, Any]:
    """Create or reuse today's valid start backup through the shared backup API."""

    from automation.inago.backup import create_daily_backup, list_daily_backups

    selected_data_root = Path(data_root).expanduser().resolve()
    selected_backup_root = Path(backup_root).expanduser().resolve()
    expected = selected_backup_root / f"daily-{now.date().strftime('%Y%m%d')}"
    existing = list_daily_backups(
        selected_backup_root,
        generation_stage="start",
    )
    if expected in existing:
        return {"status": "ready", "path": str(expected), "reused": True}
    if expected.exists():
        raise RuntimeError("today's start backup exists but is invalid")
    backup = create_daily_backup(
        connection,
        data_root=selected_data_root,
        backup_root=selected_backup_root,
        now=now,
        fixture_mode=fixture_mode,
        generation_stage="start",
    )
    return {
        "status": "ready",
        "path": str(backup.directory),
        "operation_id": backup.operation_id,
        "artifact_count": backup.artifact_count,
        "reused": False,
    }


def _as_result(value: Any) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        result = asdict(value)
    elif isinstance(value, Mapping):
        result = dict(value)
    else:
        raise TypeError("daily execution adapter returned an invalid result")
    return result


def _safe_error_code(value: object, fallback: str) -> str:
    if isinstance(value, str) and _SAFE_CODE.fullmatch(value):
        return value
    return fallback


def _default_failure_result(
    error: Exception,
    connection: sqlite3.Connection | None,
    target_date: str,
    phase: str,
    secondary_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current_run = None
    if connection is not None:
        try:
            current_run = connection.execute(
                """
                SELECT run_id, target_date, committed_count, attempt_used
                  FROM run WHERE mode='daily' AND target_date=?
                """,
                (target_date,),
            ).fetchone()
        except sqlite3.Error as exc:
            if secondary_errors is not None:
                secondary_errors.append(
                    _secondary_diagnostic(
                        "failure_result_run_lookup",
                        exc,
                        _pipeline_capture_location(),
                    )
                )
            current_run = None
    code = _safe_error_code(getattr(error, "code", None), type(error).__name__)
    return {
        "status": "failed",
        "run_id": str(current_run["run_id"]) if current_run is not None else None,
        "target_date": (
            str(current_run["target_date"]) if current_run is not None else target_date
        ),
        "committed_count": (
            int(current_run["committed_count"]) if current_run is not None else 0
        ),
        "attempt_used": (
            int(current_run["attempt_used"]) if current_run is not None else 0
        ),
        "metrics_status": "unknown",
        "resumed": False,
        "reason": code,
        "error_count": 1,
        "primary_error": {"phase": phase, "code": code},
    }


def _result_mapping(
    ports: DailyExecutionPorts,
    error: Exception,
    connection: sqlite3.Connection | None,
    target_date: str,
    phase: str,
    secondary_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    def add_primary_diagnostic(result: dict[str, Any]) -> dict[str, Any]:
        primary_error = result.get("primary_error")
        if isinstance(primary_error, Mapping):
            primary_error = dict(primary_error)
        else:
            primary_error = {
                "phase": phase,
                "code": _safe_error_code(
                    getattr(error, "code", None), type(error).__name__
                ),
            }
        failure_module, failure_function, failure_line = _pipeline_capture_location()
        error_name, constraint_label = _sqlite_failure_labels(error)
        exception_chain, exception_chain_truncated = (
            _safe_exception_chain_details(error)
        )
        primary_diagnostic = {
            "operation": "daily_primary_failure",
            "exception_class": _safe_diagnostic_class(error),
            "exception_chain": exception_chain,
            "exception_chain_truncated": exception_chain_truncated,
            "exception_frames": _safe_exception_frames(error),
            "error_code_number": _safe_sqlite_error_code(error),
            "error_name": error_name,
            "constraint_label": constraint_label,
            "failure_module": failure_module,
            "failure_function": failure_function,
            "failure_line": failure_line,
            "code_sha256": _pipeline_code_sha256(),
        }
        if "diagnostic" not in primary_error:
            primary_error["diagnostic"] = primary_diagnostic
        else:
            diagnostic_key = "daily_primary_failure_diagnostic"
            suffix = 2
            while diagnostic_key in primary_error:
                diagnostic_key = f"daily_primary_failure_diagnostic_{suffix}"
                suffix += 1
            primary_error[diagnostic_key] = primary_diagnostic
        result["primary_error"] = primary_error
        return result

    if ports.failure_result is None:
        return add_primary_diagnostic(
            _default_failure_result(
                error, connection, target_date, phase, secondary_errors
            )
        )
    try:
        return add_primary_diagnostic(
            dict(ports.failure_result(error, connection, target_date, phase))
        )
    except Exception as exc:
        if secondary_errors is not None:
            secondary_errors.append(
                _secondary_diagnostic(
                    "failure_result_callback", exc, _pipeline_capture_location()
                )
            )
        return add_primary_diagnostic(
            _default_failure_result(
                error, connection, target_date, phase, secondary_errors
            )
        )


def _cursor_mappings(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description or ()]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _retry_summary_for_run(
    connection: sqlite3.Connection,
    run_id: str | None,
    secondary_errors: list[dict[str, Any]] | None = None,
) -> dict[str, int | None]:
    """Count safe retry outcomes from persisted ROOM and Trends attempts."""

    if not isinstance(run_id, str) or not run_id:
        return {
            "attempt_count": 0,
            "recovered_count": 0,
            "unresolved_count": 0,
            "contract_violation_count": 0,
        }

    operation = "retry_summary_room_attempts"
    try:
        room_rows = _cursor_mappings(
            connection.execute(
                "SELECT attempt_id, run_id, query, parent_refs_json, level, "
                "source_status, ended_at, retry_of FROM search_attempt WHERE run_id=?",
                (run_id,),
            )
        )
        operation = "retry_summary_trend_acquisitions"
        acquisitions = _cursor_mappings(
            connection.execute(
                "SELECT acquisition_id, status, attempts_used FROM trends_acquisition "
                "WHERE run_id=?",
                (run_id,),
            )
        )
        operation = "retry_summary_trend_attempts"
        trend_attempts = _cursor_mappings(
            connection.execute(
                "SELECT attempt.request_id, attempt.acquisition_id, attempt.attempt_no, "
                "attempt.retry_of, attempt.ended_at, attempt.status "
                "FROM trends_acquisition_attempt AS attempt "
                "JOIN trends_acquisition AS acquisition "
                "ON acquisition.acquisition_id=attempt.acquisition_id "
                "WHERE acquisition.run_id=? ORDER BY attempt.acquisition_id, attempt.attempt_no",
                (run_id,),
            )
        )
    except sqlite3.Error as exc:
        if secondary_errors is not None:
            secondary_errors.append(
                _secondary_diagnostic(operation, exc, _pipeline_capture_location())
            )
        return {
            "attempt_count": None,
            "recovered_count": None,
            "unresolved_count": None,
            "contract_violation_count": None,
        }

    room_by_id = {row["attempt_id"]: row for row in room_rows}
    room_retries = [row for row in room_rows if row["retry_of"] is not None]
    room_children: dict[str, list[str]] = {}
    room_roots: dict[str, list[dict[str, Any]]] = {}
    room_violations: set[str] = set()
    for row in room_retries:
        attempt_id = str(row["attempt_id"])
        parent = room_by_id.get(row["retry_of"])
        if (
            parent is None
            or parent["run_id"] != row["run_id"]
            or parent["query"] != row["query"]
            or parent["parent_refs_json"] != row["parent_refs_json"]
            or parent["level"] != row["level"]
            or parent["ended_at"] is None
            or parent["source_status"] not in {"partial", "unavailable", "invalid"}
        ):
            room_violations.add(attempt_id)
        else:
            room_children.setdefault(str(parent["attempt_id"]), []).append(attempt_id)

        node = row
        seen = {attempt_id}
        while node["retry_of"] is not None:
            parent_node = room_by_id.get(node["retry_of"])
            if parent_node is None:
                break
            parent_id = str(parent_node["attempt_id"])
            if parent_id in seen:
                room_violations.add(attempt_id)
                break
            seen.add(parent_id)
            node = parent_node
        root_id = str(node["attempt_id"])
        room_roots.setdefault(root_id, []).append(row)

    for child_ids in room_children.values():
        if len(child_ids) > 1:
            room_violations.update(child_ids)

    room_recovered = 0
    room_unresolved = 0
    for retries in room_roots.values():
        if len(retries) > 2:
            room_violations.update(str(row["attempt_id"]) for row in retries)
        if any(
            row["source_status"] == "observed" and row["ended_at"] is not None
            for row in retries
        ):
            room_recovered += 1
        else:
            room_unresolved += 1

    trend_by_acquisition: dict[str, list[dict[str, Any]]] = {}
    for row in trend_attempts:
        trend_by_acquisition.setdefault(str(row["acquisition_id"]), []).append(row)
    trend_recovered = 0
    trend_unresolved = 0
    trend_attempt_count = 0
    trend_violations = 0
    success_statuses = {"observed", "normal_zero"}
    for acquisition in acquisitions:
        acquisition_id = str(acquisition["acquisition_id"])
        attempts = trend_by_acquisition.pop(acquisition_id, [])
        attempts_used = acquisition["attempts_used"]
        if not isinstance(attempts_used, int) or isinstance(attempts_used, bool):
            trend_violations += 1
            continue
        if attempts_used != len(attempts):
            trend_violations += 1
        retries = [row for row in attempts if row["attempt_no"] > 1]
        trend_attempt_count += len(retries)
        previous_id: str | None = None
        for index, row in enumerate(attempts, start=1):
            if (
                row["attempt_no"] != index
                or row["retry_of"] != previous_id
                or (row["status"] == "started") != (row["ended_at"] is None)
            ):
                trend_violations += 1
            previous_id = str(row["request_id"])
        if retries:
            if acquisition["status"] in success_statuses:
                trend_recovered += 1
                if not attempts or attempts[-1]["status"] != "response_ready":
                    trend_violations += 1
            else:
                trend_unresolved += 1

    trend_violations += len(trend_by_acquisition)
    return {
        "attempt_count": len(room_retries) + trend_attempt_count,
        "recovered_count": room_recovered + trend_recovered,
        "unresolved_count": room_unresolved + trend_unresolved,
        "contract_violation_count": len(room_violations) + trend_violations,
    }


def _attach_retry_summary(
    daily_result: dict[str, Any],
    connection: sqlite3.Connection | None,
    secondary_errors: list[dict[str, Any]] | None = None,
) -> None:
    """Add counts only; never include retry inputs or source response content."""

    if "retry_summary" in daily_result:
        return
    if connection is None:
        daily_result["retry_summary"] = {
            "attempt_count": None,
            "recovered_count": None,
            "unresolved_count": None,
            "contract_violation_count": None,
        }
        return
    run_id = daily_result.get("run_id")
    daily_result["retry_summary"] = _retry_summary_for_run(
        connection,
        run_id if isinstance(run_id, str) else None,
        secondary_errors,
    )


def _result_status(result: Mapping[str, Any]) -> str:
    value = result.get("status")
    return value if isinstance(value, str) else "failed"


def _normalize_export(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    result = dict(value) if isinstance(value, Mapping) else {}
    if not isinstance(result.get("status"), str):
        result["status"] = "failed"
        result["error_code"] = result.get("error_code") or "dashboard_export_invalid"
    return result


def _normalize_preservation(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    result = dict(value) if isinstance(value, Mapping) else {}
    if not isinstance(result.get("status"), str):
        result["status"] = "failed"
    if not isinstance(result.get("end_backup"), Mapping):
        result["end_backup"] = {"status": "unknown", "error_code": None}
    if not isinstance(result.get("monthly_restore"), Mapping):
        result["monthly_restore"] = {
            "status": "unknown",
            "already_checked": False,
            "error_code": None,
        }
    else:
        result["monthly_restore"] = dict(result["monthly_restore"])
    result["end_backup"] = dict(result["end_backup"])
    return result


def _combine_result(
    daily_result: dict[str, Any],
    *,
    daily_status: str,
    start_backup: Mapping[str, Any],
    export_result: Mapping[str, Any],
    preservation_result: Mapping[str, Any],
    preflight_status: str,
    database_status: str,
    prepare_status: str,
    operations_status: str,
    database_close_status: str,
    commit_export_failures: int,
    cleanup_errors: list[dict[str, str]],
    trace_result: Mapping[str, Any],
) -> dict[str, Any]:
    metrics_status = str(daily_result.get("metrics_status", "unknown"))
    try:
        daily_error_count = int(daily_result.get("error_count", 0))
    except (TypeError, ValueError):
        daily_error_count = 1
    daily_primary_error = daily_result.get("primary_error")
    if not isinstance(daily_primary_error, dict):
        daily_primary_error = None
    if daily_primary_error is None and (
        daily_status == "failed"
        or daily_status == "locked"
        or daily_status.startswith("recovery_")
        or daily_error_count > 0
    ):
        daily_primary_error = {
            "phase": "daily",
            "code": str(daily_result.get("reason") or "daily_processing_error"),
        }

    end_backup = preservation_result.get("end_backup")
    monthly_restore = preservation_result.get("monthly_restore")
    end_backup_status = (
        end_backup.get("status") if isinstance(end_backup, Mapping) else "unknown"
    )
    monthly_restore_status = (
        monthly_restore.get("status")
        if isinstance(monthly_restore, Mapping)
        else "unknown"
    )
    blocked = daily_status in _BLOCKED_STATUSES
    start_backup_status = str(start_backup.get("status", "unknown"))
    phase_errors: list[dict[str, str]] = []
    if daily_primary_error is not None:
        phase_errors.append(dict(daily_primary_error))
    if daily_error_count > 0 and daily_primary_error is None:
        phase_errors.append({"phase": "daily", "code": "daily_processing_error"})
    if not blocked and start_backup_status != "ready":
        phase_errors.append(
            {
                "phase": "start_backup",
                "code": str(start_backup.get("error_code") or "start_backup_failed"),
            }
        )
    if not blocked and operations_status == "failed":
        monitor_error = next(
            (
                item["code"]
                for item in cleanup_errors
                if item.get("phase") == "operations_monitor"
            ),
            "operations_monitor_failed",
        )
        phase_errors.append(
            {"phase": "operations_monitor", "code": str(monitor_error)[:128]}
        )
    if metrics_status not in {"observed"} and not blocked:
        phase_errors.append({"phase": "metrics", "code": f"metrics_{metrics_status}"})
    if not blocked and export_result.get("status") not in {"ready", "not_run"}:
        export_code = (
            export_result.get("error_code")
            or export_result.get("reason_code")
            or "dashboard_export_failed"
        )
        phase_errors.append({"phase": "export", "code": str(export_code)[:128]})
    preservation_ready = (
        preservation_result.get("status") == "ready"
        and end_backup_status == "ready"
        and monthly_restore_status in {"ready", "already_checked"}
    )
    if not preservation_ready and not blocked:
        preserve_code = (
            (end_backup.get("error_code") if isinstance(end_backup, Mapping) else None)
            or (
                monthly_restore.get("error_code")
                if isinstance(monthly_restore, Mapping)
                else None
            )
            or "preservation_failed"
        )
        phase_errors.append({"phase": "preservation", "code": str(preserve_code)[:128]})

    daily_secondary_errors = daily_result.get("secondary_errors", [])
    primary_error = phase_errors[0] if phase_errors else None
    secondary_errors = list(phase_errors[1:])
    if isinstance(daily_secondary_errors, (list, tuple)):
        secondary_errors.extend(
            dict(item) for item in daily_secondary_errors if isinstance(item, dict)
        )
    secondary_errors.extend(cleanup_errors)

    if blocked:
        final_status = daily_status
    elif daily_status == "failed" or daily_error_count > 0:
        final_status = "failed"
    elif cleanup_errors or operations_status == "failed":
        final_status = "failed"
    elif start_backup_status != "ready":
        final_status = "preservation_failed"
    elif export_result.get("status") != "ready":
        final_status = "export_failed"
    elif not preservation_ready:
        final_status = "preservation_failed"
    elif metrics_status != "observed":
        final_status = "metrics_failed"
    elif daily_status in {"partial", "shortfall"}:
        final_status = daily_status
    elif daily_status == "complete":
        final_status = "complete"
    else:
        final_status = "failed"

    daily_reason = daily_result.get("reason")
    overall_reason = (
        str(primary_error.get("code"))
        if isinstance(primary_error, dict)
        else (str(daily_reason) if isinstance(daily_reason, str) else None)
    )
    phase_results = {
        "preflight": {"status": preflight_status},
        "database_open": {"status": database_status},
        "prepare": {"status": prepare_status},
        "start_backup": dict(start_backup),
        "daily": {
            "status": daily_status,
            "committed_count": daily_result.get("committed_count"),
            "shortfall_count": daily_result.get("shortfall_count"),
            "source_failures": daily_result.get("source_failures"),
            "error_count": daily_error_count,
            "primary_error": daily_primary_error,
            "secondary_errors": daily_secondary_errors,
        },
        "metrics": {"status": metrics_status},
        "operations_monitor": {"status": operations_status},
        "database_close": {"status": database_close_status},
        "export": {
            "status": export_result.get("status"),
            "commit_refresh_failures": commit_export_failures,
        },
        "preservation": {
            "status": preservation_result.get("status"),
            "end_backup_status": end_backup_status,
            "monthly_restore_status": monthly_restore_status,
        },
        "trace": dict(trace_result),
    }
    return {
        **daily_result,
        "status": final_status,
        "daily_status": daily_status,
        "daily_reason": daily_reason,
        "reason": overall_reason,
        "overall_status": final_status,
        "primary_error": primary_error,
        "secondary_errors": secondary_errors,
        "start_backup": dict(start_backup),
        "phase_results": phase_results,
        "export": dict(export_result),
        "preservation": dict(preservation_result),
        "pipeline_trace": dict(trace_result),
    }


def _write_pipeline_trace(
    data_root: Path,
    *,
    run_id: str | None,
    target_date: str,
    events: list[_PipelineEvent],
) -> dict[str, Any]:
    if run_id is None:
        return {
            "status": "not_written",
            "event_count": 0,
            "reference": None,
            "trace_requested": True,
            "trace_persisted": False,
        }
    from automation.inago.trace import TraceWriter, make_trace_event

    root_span = str(uuid.uuid5(uuid.NAMESPACE_URL, f"inago:run:{run_id}"))
    pipeline_span = str(uuid.uuid5(uuid.NAMESPACE_URL, f"inago:pipeline:{run_id}"))
    writer = TraceWriter(data_root, run_id, target_date)
    written = 0
    health = "healthy"
    error_code: str | None = None
    for item in events:
        fields: dict[str, Any] = {
            "elapsed_ms": item.elapsed_ms,
            "event_kind": "stage",
            "status": item.status,
            "active_stage": item.active_stage,
            "last_completed_stage": item.last_completed_stage,
        }
        if item.error_code is not None:
            fields["primary_error_code"] = item.error_code
        if item.error_class is not None:
            fields.update(
                {
                    "error_class": item.error_class,
                    "error_code_number": item.error_code_number,
                    "error_name": item.error_name or "unknown",
                    "processing_label": "daily_pipeline",
                    "table_label": "unknown",
                    "constraint_label": item.constraint_label,
                    "failure_module": item.failure_module or __name__,
                    "failure_function": item.failure_function or "phase_error",
                    "failure_line": item.failure_line,
                    "code_sha256": item.failure_code_sha256,
                    "trace_requested": True,
                }
            )
        event = make_trace_event(
            run_id=run_id,
            target_date=target_date,
            component="daily_pipeline",
            operation=item.operation,
            phase=item.phase,
            clock=_RecordedClock(datetime.fromisoformat(item.at)),
            span_id=pipeline_span,
            parent_span_id=root_span,
            **fields,
        )
        result = writer.write(event)
        if result.status in {"written", "appended"}:
            written += 1
        if result.status in {"degraded", "suppressed"}:
            health = "degraded"
            error_code = result.reason
        elif result.status == "unavailable":
            health = "unavailable"
            error_code = result.reason
    return {
        "status": health,
        "event_count": written,
        "reference": str(writer.path.relative_to(data_root)).replace("\\", "/"),
        "error_code": error_code,
        "trace_requested": True,
        "trace_persisted": written > 0,
    }


def run_daily_execution_core(
    ports: DailyExecutionPorts,
    *,
    clock: Clock,
) -> dict[str, Any]:
    """Run one day through preparation, daily work, export, preservation, and exit."""

    now = clock.now_jst()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("daily execution clock must return an aware datetime")
    now = now.astimezone(JST)
    target_date = now.date().isoformat()
    data_root = Path(ports.data_root).expanduser().resolve()
    timeline: list[_PipelineEvent] = []
    last_completed: str | None = None
    current_phase: str | None = None
    phase_started = 0.0

    def phase_begin(operation: str) -> None:
        nonlocal current_phase, phase_started
        current_phase = operation
        phase_started = time.monotonic()
        timeline.append(
            _PipelineEvent(
                operation=operation,
                phase="begin",
                at=clock.now_jst().astimezone(JST).isoformat(),
                active_stage=operation,
                last_completed_stage=last_completed,
            )
        )

    def phase_end(operation: str, status: str) -> None:
        nonlocal current_phase, last_completed
        elapsed = max(0, round((time.monotonic() - phase_started) * 1000))
        timeline.append(
            _PipelineEvent(
                operation=operation,
                phase="end",
                at=clock.now_jst().astimezone(JST).isoformat(),
                elapsed_ms=elapsed,
                status=status,
                active_stage=None,
                last_completed_stage=operation,
            )
        )
        current_phase = None
        last_completed = operation

    def phase_error(
        operation: str,
        code: str,
        exc: BaseException | None = None,
        *,
        failure_location: tuple[str, str, int | None] | None = None,
        failure_code_sha256: str | None = None,
    ) -> None:
        nonlocal current_phase
        elapsed = max(0, round((time.monotonic() - phase_started) * 1000))
        error_name, constraint_label = _sqlite_failure_labels(exc)
        location = failure_location or _pipeline_capture_location()
        failure_module, failure_function, failure_line = location
        timeline.append(
            _PipelineEvent(
                operation=operation,
                phase="error",
                at=clock.now_jst().astimezone(JST).isoformat(),
                elapsed_ms=elapsed,
                status="failed",
                error_code=code,
                active_stage=operation,
                last_completed_stage=last_completed,
                error_class=type(exc).__name__ if exc is not None else None,
                error_code_number=_safe_sqlite_error_code(exc),
                error_name=error_name,
                constraint_label=constraint_label,
                failure_line=failure_line,
                failure_module=failure_module,
                failure_function=failure_function,
                failure_code_sha256=failure_code_sha256,
            )
        )
        current_phase = None

    connection: sqlite3.Connection | None = None
    context: Any = None
    monitor: Any = None
    monitor_attempted = False
    cleanup_errors: list[dict[str, str]] = []
    secondary_diagnostics: list[dict[str, Any]] = []
    commit_export_failures = 0
    preflight_status = "not_run"
    database_status = "not_run"
    prepare_status = "not_run"
    operations_status = "not_run"
    database_close_status = "not_run"
    start_backup: dict[str, Any] = {"status": "not_run", "error_code": None}
    daily_result: dict[str, Any] = {
        "status": "failed",
        "target_date": target_date,
        "run_id": None,
        "committed_count": 0,
        "attempt_used": 0,
        "metrics_status": "unknown",
        "resumed": False,
    }
    daily_status = "failed"
    try:
        phase_begin("preflight")
        preflight_context = ports.preflight(now)
        preflight_status = "ready"
        phase_end("preflight", preflight_status)

        phase_begin("database_open")
        connection = ports.open_database()
        database_status = "ready"
        phase_end("database_open", "ready")

        phase_begin("prepare")
        context = ports.prepare(connection, now, preflight_context)
        prepare_status = "ready"
        phase_end("prepare", prepare_status)

        phase_begin("start_backup")
        start_backup = dict(ports.start_backup(connection, context, now))
        if start_backup.get("status") != "ready":
            code = _safe_error_code(
                start_backup.get("error_code"), "start_backup_failed"
            )
            raise _DailyPipelineError(code)
        phase_end("start_backup", "ready")

        phase_begin("operations_monitor")
        monitor = ports.operations_monitor(connection, context, target_date)
        if monitor is not None:
            monitor_attempted = True
            try:
                monitor.start()
            except Exception:
                operations_status = "failed"
                raise
        operations_status = "ready" if monitor is not None else "not_configured"
        phase_end("operations_monitor", operations_status)

        def refresh_history_after_commit(
            _connection: sqlite3.Connection,
        ) -> dict[str, Any]:
            nonlocal commit_export_failures
            try:
                export_value = ports.export_dashboard(context)
                observed_status = (
                    export_value.get("status")
                    if isinstance(export_value, Mapping)
                    else None
                )
                result = _normalize_export(export_value)
            except Exception as exc:
                commit_export_failures += 1
                secondary_diagnostics.append(
                    _secondary_diagnostic(
                        "dashboard_export_after_commit",
                        exc,
                        _pipeline_capture_location(),
                    )
                )
                return {"status": "failed", "error_code": type(exc).__name__}
            if result.get("status") != "ready":
                commit_export_failures += 1
                normalized_status = (
                    observed_status.strip().lower()
                    if isinstance(observed_status, str)
                    else "unknown"
                )
                if normalized_status not in {"failed", "not_run", "unknown"}:
                    normalized_status = "unknown"
                failure_module, failure_function, failure_line = (
                    _pipeline_capture_location()
                )
                secondary_diagnostics.append(
                    {
                        "phase": "daily_diagnostic",
                        "code": "dashboard_export_after_commit_not_ready",
                        "operation": "dashboard_export_after_commit",
                        "failure_kind": "returned_status",
                        "returned_status": normalized_status,
                        "exception_observed": False,
                        "exception_class": None,
                        "error_name": "unknown",
                        "constraint_label": "unknown",
                        "failure_module": failure_module,
                        "failure_function": failure_function,
                        "failure_line": failure_line,
                        "code_sha256": _pipeline_code_sha256(),
                        "exception_chain": [],
                        "exception_chain_truncated": False,
                        "exception_frames": [],
                    }
                )
            return result

        phase_begin("daily")
        outcome = ports.run_daily(connection, context, refresh_history_after_commit)
        daily_result = _as_result(outcome)
        _attach_retry_summary(daily_result, connection, secondary_diagnostics)
        daily_status = _result_status(daily_result)
        phase_end("daily", daily_status)
    except Exception as exc:
        phase = current_phase or "daily"
        code = _safe_error_code(getattr(exc, "code", None), type(exc).__name__)
        failure_module, failure_function, failure_line = _pipeline_capture_location()
        failure_code_sha256 = _pipeline_code_sha256()
        if current_phase is not None:
            phase_error(
                current_phase,
                code,
                exc,
                failure_location=(
                    failure_module,
                    failure_function,
                    failure_line,
                ),
                failure_code_sha256=failure_code_sha256,
            )
        error_name, constraint_label = _sqlite_failure_labels(exc)
        if connection is not None and connection.in_transaction:
            try:
                connection.rollback()
            except sqlite3.Error as rollback_error:
                cleanup_errors.append(
                    {
                        "phase": "rollback",
                        "code": type(rollback_error).__name__,
                    }
                )
        daily_result = _result_mapping(
            ports,
            exc,
            connection,
            target_date,
            phase,
            secondary_diagnostics,
        )
        _attach_retry_summary(daily_result, connection, secondary_diagnostics)
        daily_status = _result_status(daily_result)
        if phase == "preflight":
            preflight_status = "failed"
        elif phase == "database_open":
            database_status = "failed"
        elif phase == "prepare":
            prepare_status = "failed"
        elif phase == "operations_monitor":
            operations_status = "failed"
        if phase == "start_backup":
            start_backup = {
                **start_backup,
                "status": "failed",
                "error_code": code,
            }
        _best_effort_log(
            logging.ERROR,
            "daily_pipeline_failure",
            extra={
                "processing_label": "daily_pipeline",
                "phase": phase,
                "exception_class": type(exc).__name__,
                "primary_error_code": code,
                "error_code_number": _safe_sqlite_error_code(exc),
                "error_name": error_name,
                "constraint_label": constraint_label,
                "persisted_run_id": None,
                "run_persisted": None,
                "failure_module": failure_module,
                "failure_function": failure_function,
                "failure_line": failure_line,
                "code_sha256": failure_code_sha256,
                "trace_requested": True,
                "trace_persisted": None,
            },
        )
    finally:
        if monitor_attempted and monitor is not None:
            phase_begin("operations_monitor_stop")
            try:
                monitor.stop()
                if operations_status != "failed":
                    operations_status = "stopped"
                phase_end("operations_monitor_stop", "stopped")
            except Exception as exc:
                operations_status = "failed"
                phase_error(
                    "operations_monitor_stop",
                    _safe_error_code(type(exc).__name__, "operations_monitor_failed"),
                )
                cleanup_errors.append(
                    {"phase": "operations_monitor", "code": type(exc).__name__}
                )
        if connection is not None:
            phase_begin("database_close")
            try:
                connection.close()
                database_close_status = "closed"
                phase_end("database_close", "closed")
            except Exception as exc:
                database_close_status = "failed"
                phase_error(
                    "database_close",
                    _safe_error_code(type(exc).__name__, "database_close_failed"),
                )
                cleanup_errors.append(
                    {"phase": "database_close", "code": type(exc).__name__}
                )
                if daily_status not in _BLOCKED_STATUSES:
                    daily_status = "failed"
                    daily_result = {
                        **daily_result,
                        "status": "failed",
                        "reason": "DatabaseCloseError",
                        "error_count": max(
                            1, int(daily_result.get("error_count", 0) or 0)
                        ),
                        "primary_error": daily_result.get("primary_error")
                        or {"phase": "database_close", "code": "DatabaseCloseError"},
                    }

    blocked_before_postprocessing = daily_status in _BLOCKED_STATUSES
    if blocked_before_postprocessing:
        export_result: dict[str, Any] = {
            "status": "not_run",
            "reason_code": "daily_recovery_not_confirmed",
        }
    else:
        phase_begin("export")
        try:
            export_result = _normalize_export(ports.export_dashboard(context))
        except Exception as exc:
            export_result = {
                "status": "failed",
                "error_code": type(exc).__name__,
            }
        if export_result.get("status") == "ready":
            phase_end("export", "ready")
        else:
            phase_error(
                "export",
                _safe_error_code(
                    export_result.get("error_code"), "dashboard_export_failed"
                ),
            )

    preservation_result: dict[str, Any] = {
        "status": "not_run",
        "end_backup": {"status": "not_run", "error_code": None},
        "monthly_restore": {"status": "not_run", "already_checked": False},
    }
    if not blocked_before_postprocessing and start_backup.get("status") == "ready":
        phase_begin("preservation")
        run_id = daily_result.get("run_id")
        try:
            preservation_result = _normalize_preservation(
                ports.preserve_end_of_day(
                    context,
                    clock.now_jst().astimezone(JST),
                    run_id if isinstance(run_id, str) else None,
                )
            )
        except Exception as exc:
            preservation_result = {
                "status": "failed",
                "end_backup": {"status": "failed", "error_code": type(exc).__name__},
                "monthly_restore": {
                    "status": "not_run",
                    "already_checked": False,
                    "error_code": None,
                },
            }
        if preservation_result.get("status") == "ready":
            phase_end("preservation", "ready")
        else:
            end_backup = preservation_result.get("end_backup")
            code = (
                end_backup.get("error_code")
                if isinstance(end_backup, Mapping)
                else None
            ) or "preservation_failed"
            phase_error("preservation", _safe_error_code(code, "preservation_failed"))

    run_id_value = daily_result.get("run_id")
    try:
        trace_result = _write_pipeline_trace(
            data_root,
            run_id=run_id_value if isinstance(run_id_value, str) else None,
            target_date=str(daily_result.get("target_date") or target_date),
            events=timeline,
        )
    except Exception as exc:
        trace_result = {
            "status": "unavailable",
            "event_count": 0,
            "reference": None,
            "error_code": _safe_error_code(type(exc).__name__, "trace_unavailable"),
            "trace_requested": True,
            "trace_persisted": False,
        }
    persisted_run_id = daily_result.get("run_id")
    _best_effort_log(
        logging.INFO,
        "daily_pipeline_trace_result",
        extra={
            "processing_label": "daily_pipeline",
            "proposed_run_id": None,
            "persisted_run_id": (
                persisted_run_id if isinstance(persisted_run_id, str) else None
            ),
            "run_persisted": isinstance(persisted_run_id, str),
            "trace_requested": trace_result.get("trace_requested", True),
            "trace_persisted": trace_result.get("trace_persisted", False),
            "trace_status": trace_result.get("status", "unknown"),
            "trace_event_count": trace_result.get("event_count", 0),
            "code_sha256": _pipeline_code_sha256(),
        },
    )
    if secondary_diagnostics:
        existing_secondary = daily_result.get("secondary_errors", [])
        merged_secondary = (
            [dict(item) for item in existing_secondary if isinstance(item, Mapping)]
            if isinstance(existing_secondary, (list, tuple))
            else []
        )
        merged_secondary.extend(secondary_diagnostics)
        daily_result["secondary_errors"] = merged_secondary
    return _combine_result(
        daily_result,
        daily_status=daily_status,
        start_backup=start_backup,
        export_result=export_result,
        preservation_result=preservation_result,
        preflight_status=preflight_status,
        database_status=database_status,
        prepare_status=prepare_status,
        operations_status=operations_status,
        database_close_status=database_close_status,
        commit_export_failures=commit_export_failures,
        cleanup_errors=cleanup_errors,
        trace_result=trace_result,
    )
