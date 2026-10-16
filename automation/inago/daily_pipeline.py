"""Shared daily execution lifecycle for production and isolated probes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
import re
import sqlite3
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
        except sqlite3.Error:
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
) -> dict[str, Any]:
    if ports.failure_result is None:
        return _default_failure_result(error, connection, target_date, phase)
    try:
        return dict(ports.failure_result(error, connection, target_date, phase))
    except Exception:
        return _default_failure_result(error, connection, target_date, phase)


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
        return {"status": "not_written", "event_count": 0, "reference": None}
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

    def phase_error(operation: str, code: str) -> None:
        nonlocal current_phase
        elapsed = max(0, round((time.monotonic() - phase_started) * 1000))
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
            )
        )
        current_phase = None

    connection: sqlite3.Connection | None = None
    context: Any = None
    monitor: Any = None
    monitor_attempted = False
    cleanup_errors: list[dict[str, str]] = []
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
                result = _normalize_export(ports.export_dashboard(context))
            except Exception as exc:
                commit_export_failures += 1
                return {"status": "failed", "error_code": type(exc).__name__}
            if result.get("status") != "ready":
                commit_export_failures += 1
            return result

        phase_begin("daily")
        outcome = ports.run_daily(connection, context, refresh_history_after_commit)
        daily_result = _as_result(outcome)
        daily_status = _result_status(daily_result)
        phase_end("daily", daily_status)
    except Exception as exc:
        phase = current_phase or "daily"
        code = _safe_error_code(getattr(exc, "code", None), type(exc).__name__)
        if current_phase is not None:
            phase_error(current_phase, code)
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
        )
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
        }
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
