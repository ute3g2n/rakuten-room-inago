"""Command line for inspecting and operating the isolated maintenance store."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Sequence
import uuid
from zoneinfo import ZoneInfo

from automation.inago.maintenance.controller import MaintenanceController
from automation.inago.maintenance.diagnostic_trace import (
    MaintenanceAuditReceiptError,
    MaintenanceDiagnosticError,
    MaintenanceDiagnosticTrace,
    run_maintenance_retention,
)
from automation.inago.maintenance.paths import (
    UnsafeMaintenancePathError,
    resolve_maintenance_database_path,
    resolve_maintenance_settings_path,
)
from automation.inago.maintenance.settings import (
    MaintenanceSettingsError,
    ensure_settings_file,
    load_settings,
)
from automation.inago.maintenance.store import (
    LeaseToken,
    MaintenanceStore,
    MaintenanceStoreError,
    SCHEMA_VERSION,
)
from automation.inago.maintenance.process_identity import current_process_start_time
from automation.inago.maintenance.watchdog import (
    read_daily_scheduler_occurrences,
    run_watchdog_once,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inago-maintenance")
    parser.add_argument("--data-root", required=True, help="maintenance runtime root")
    parser.add_argument(
        "--daily-database",
        help="daily DB path used only for isolation checks (default: DATA_ROOT/state.sqlite)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="create the management DB and initial settings")
    commands.add_parser("status", help="show schema version and safe row counts")
    commands.add_parser(
        "watchdog", help="reconcile stale daily launch and scheduler records"
    )

    gate = commands.add_parser(
        "release-gate", help="recompute a release readiness gate"
    )
    gate.add_argument("--release-id", required=True)
    gate.add_argument("--evaluated-at", required=True)

    start = commands.add_parser(
        "start-job", help="start a fix job only when its gate is ready"
    )
    start.add_argument("--incident-id", required=True)
    start.add_argument("--release-id", required=True)
    start.add_argument("--job-id", required=True)
    start.add_argument("--started-at", required=True)
    start.add_argument("--source-run-id")

    finish = commands.add_parser(
        "finish-job", help="finalize an active job under its current lease"
    )
    finish.add_argument("--job-id", required=True)
    finish.add_argument(
        "--status",
        required=True,
        choices=("complete", "failed", "blocked", "cancelled"),
    )
    finish.add_argument("--finalized-at", required=True)
    finish.add_argument("--lease-key", required=True)
    finish.add_argument("--lease-owner", required=True)
    finish.add_argument("--lease-generation", required=True, type=int)
    finish.add_argument("--reason-code")
    return parser


def _resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    data_root = Path(args.data_root).expanduser()
    daily_database = (
        Path(args.daily_database).expanduser()
        if args.daily_database
        else data_root / "state.sqlite"
    )
    database_path = resolve_maintenance_database_path(
        data_root,
        daily_database_path=daily_database,
    )
    settings_path = resolve_maintenance_settings_path(
        data_root,
        daily_database_path=daily_database,
    )
    return data_root, database_path, settings_path


def _store_for(
    args: argparse.Namespace, *, initialize: bool = False
) -> MaintenanceStore:
    data_root, database_path, settings_path = _resolve_inputs(args)
    settings = (
        ensure_settings_file(settings_path)
        if initialize
        else load_settings(settings_path)
    )
    store = MaintenanceStore(
        data_root,
        daily_database_path=(
            Path(args.daily_database).expanduser()
            if args.daily_database
            else data_root / "state.sqlite"
        ),
        job_resume_limit=settings.job_resume_limit,
        review_round_limit=settings.review_round_limit,
    )
    if initialize:
        store.initialize()
    elif not database_path.is_file():
        raise MaintenanceStoreError("maintenance_database_unavailable")
    return store


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _safe_operation_result_summary(result: object) -> dict[str, object]:
    """Expose only compact, non-sensitive state when its receipt is missing."""

    if type(result) is int:
        return {"kind": "exit_code", "exit_code": result}

    result_type = type(result).__name__
    summary: dict[str, object] = {
        "kind": result_type
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", result_type)
        else "returned"
    }
    status_values = {
        "started",
        "complete",
        "degraded",
        "failed",
        "blocked",
        "waiting",
        "cancelled",
        "resumed",
        "unverified",
        "approved",
        "changes_requested",
        "passed",
        "not_run",
        "ready",
        "not_ready",
        "busy",
        "unknown",
    }
    for field in (
        "status",
        "gate_status",
        "plan_status",
        "investigation_status",
        "review_status",
        "next_action",
    ):
        value = getattr(result, field, None)
        if isinstance(value, str) and value in status_values:
            summary["status"] = value
            break
    reason_code = getattr(result, "reason_code", None)
    if isinstance(reason_code, str) and re.fullmatch(
        r"[a-z][a-z0-9_]{0,63}", reason_code
    ):
        summary["reason_code"] = reason_code
    for field in (
        "failures",
        "removed_files",
        "expired_log_files",
        "expired_evidence_files",
        "recovered_orphan_stages",
    ):
        value = getattr(result, field, None)
        if type(value) is int and value >= 0:
            summary[field] = value
    return summary


def _run(
    args: argparse.Namespace,
    *,
    diagnostic_trace: MaintenanceDiagnosticTrace | None = None,
) -> int:
    if args.command == "init":
        store = _store_for(args, initialize=True)
        _emit({"status": "initialized", "schema_version": SCHEMA_VERSION})
        return 0
    store = _store_for(args)
    if args.command == "watchdog":
        now = datetime.now(timezone.utc)
        owner_start_time = current_process_start_time()
        if owner_start_time is None:
            raise MaintenanceStoreError("process_start_time_unavailable")
        cursor = store.get_watchdog_cursor()
        pending_queues = store.list_watchdog_queue(reconciliation_only=True)
        occurrences = read_daily_scheduler_occurrences(
            now=now,
            after_date=cursor,
            include_dates=tuple(queue.target_date for queue in pending_queues),
        )
        result = run_watchdog_once(
            store,
            now=now,
            occurrences=occurrences,
            owner_id=uuid.uuid4().hex,
            owner_pid=os.getpid(),
            owner_start_time=owner_start_time,
        )
        _emit(
            {
                "status": "ok" if result.lease_acquired else "busy",
                "queued_source_launch_ids": list(result.queued_source_launch_ids),
                "queued_queue_ids": list(result.queued_queue_ids),
                "reconciled_queue_ids": list(result.reconciled_queue_ids),
            }
        )
        return 0
    controller = MaintenanceController(store, diagnostic_trace=diagnostic_trace)
    if args.command == "status":
        _emit(
            {
                "status": "ok",
                "schema_version": SCHEMA_VERSION,
                "counts": store.counts(),
            }
        )
        return 0
    if args.command == "release-gate":
        gate = controller.evaluate_release_gate(
            release_id=args.release_id,
            evaluated_at=args.evaluated_at,
        )
        _emit(
            {
                "status": gate.status,
                "release_id": gate.release_id,
                "result_commit": gate.result_commit,
                "reason_codes": gate.reason_codes,
            }
        )
        return 0
    if args.command == "start-job":
        started = controller.start_fix_job(
            incident_id=args.incident_id,
            release_id=args.release_id,
            job_id=args.job_id,
            started_at=args.started_at,
            source_run_id=args.source_run_id,
        )
        _emit(
            {
                "status": "started" if started.started else "blocked",
                "job_id": started.job_id,
                "gate_status": started.gate_status,
                "reason_codes": started.reason_codes,
            }
        )
        return 0 if started.started else 3
    if args.command == "finish-job":
        result = controller.finalize_job(
            job_id=args.job_id,
            final_status=args.status,
            lease=LeaseToken(
                lease_key=args.lease_key,
                owner_id=args.lease_owner,
                generation=args.lease_generation,
            ),
            finalized_at=args.finalized_at,
            reason_code=args.reason_code,
        )
        _emit(
            {
                "status": result.status,
                "job_id": result.job_id,
                "finalized_at": result.finalized_at,
                "reason_code": result.reason_code,
            }
        )
        return 0
    raise MaintenanceStoreError("command_unknown")


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _parser().parse_args(raw_args)
    except SystemExit as exc:
        if exc.code:
            data_root = _data_root_argument(raw_args)
            if data_root is not None:
                trace = MaintenanceDiagnosticTrace(data_root)
                try:
                    trace.require_written(
                        event="maintenance_started", stage="cli", status="started"
                    )
                    trace.require_written(
                        event="maintenance_finished",
                        stage="cli",
                        status="failed",
                        reason_code="cli_arguments_invalid",
                    )
                except MaintenanceDiagnosticError:
                    pass
        return int(exc.code or 0)
    trace = MaintenanceDiagnosticTrace(args.data_root)
    started = False
    try:
        trace.require_written(
            event="maintenance_started", stage="cli", status="started"
        )
        started = True
        retention = run_maintenance_retention(
            args.data_root, now=datetime.now(ZoneInfo("Asia/Tokyo"))
        )
        retention_ok = retention.failures == 0
        try:
            trace.require_written(
                event="retention_completed" if retention_ok else "retention_failed",
                stage="retention",
                status="complete" if retention_ok else "blocked",
                count=retention.failures,
                **({} if retention_ok else {"reason_code": "retention_incomplete"}),
            )
        except MaintenanceDiagnosticError as receipt_error:
            raise MaintenanceAuditReceiptError(
                receipt_error.code, operation_returned=True, operation_result=retention
            ) from receipt_error
        exit_code = _run(args, diagnostic_trace=trace)
        finish_status = "complete" if exit_code == 0 else "blocked"
        try:
            trace.require_written(
                event="maintenance_finished",
                stage="cli",
                status=finish_status,
                **(
                    {}
                    if exit_code == 0
                    else {"reason_code": "maintenance_command_blocked"}
                ),
            )
        except MaintenanceDiagnosticError as receipt_error:
            raise MaintenanceAuditReceiptError(
                receipt_error.code,
                operation_returned=True,
                operation_result=exit_code,
            ) from receipt_error
        return exit_code
    except MaintenanceAuditReceiptError as exc:
        _emit(
            {
                "status": "error",
                "reason_code": exc.code,
                "operation_returned": exc.operation_returned,
                "operation_result_summary": (
                    _safe_operation_result_summary(exc.operation_result)
                    if exc.operation_returned
                    else None
                ),
                "state_reconciliation_required": True,
            }
        )
        return 2
    except MaintenanceDiagnosticError as exc:
        _emit({"status": "error", "reason_code": exc.code})
        return 2
    except (
        UnsafeMaintenancePathError,
        MaintenanceSettingsError,
        MaintenanceStoreError,
    ) as exc:
        if started:
            try:
                trace.require_written(
                    event="maintenance_finished",
                    stage="cli",
                    status="failed",
                    reason_code=exc.code,
                )
            except MaintenanceDiagnosticError as trace_exc:
                _emit({"status": "error", "reason_code": trace_exc.code})
                return 2
        _emit({"status": "error", "reason_code": exc.code})
        return 2
    except (OSError, sqlite3.Error, ValueError, RuntimeError):
        if started:
            try:
                trace.require_written(
                    event="maintenance_finished",
                    stage="cli",
                    status="failed",
                    reason_code="maintenance_operation_failed",
                )
            except MaintenanceDiagnosticError as trace_exc:
                _emit({"status": "error", "reason_code": trace_exc.code})
                return 2
        _emit({"status": "error", "reason_code": "maintenance_operation_failed"})
        return 2


def _data_root_argument(argv: Sequence[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--data-root" and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith("--data-root="):
            return value.partition("=")[2]
    return None


if __name__ == "__main__":
    sys.exit(main())
