"""Command line for inspecting and operating the isolated maintenance store."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
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
    _has_reparse_point,
    resolve_maintenance_database_path,
    resolve_maintenance_settings_path,
)
from automation.inago.maintenance.settings import (
    MaintenanceSettingsError,
    ensure_settings_file,
    load_settings,
)
from automation.inago.maintenance.store import (
    IdempotencyConflictError,
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
    parser.add_argument("--data-root", help="maintenance runtime root")
    parser.add_argument(
        "--daily-database",
        help="daily DB path used only for isolation checks (default: DATA_ROOT/state.sqlite)",
    )
    parser.add_argument(
        "--maintenance-id",
        help="reuse an existing maintenance trace identity for a resumed operation",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="create the management DB and initial settings")
    commands.add_parser("status", help="show schema version and safe row counts")
    commands.add_parser(
        "watchdog", help="reconcile stale daily launch and scheduler records"
    )

    preflight = commands.add_parser(
        "preflight", help="read-only production maintenance health check"
    )
    preflight.add_argument("--environment", required=True, choices=("production",))
    preflight.add_argument("--config", required=True)
    preflight.add_argument("--data-root", default=argparse.SUPPRESS)
    preflight.add_argument("--daily-database", default=argparse.SUPPRESS)

    worker = commands.add_parser(
        "worker", help="claim a completed daily launch after its processes stop"
    )
    worker.add_argument("--once", action="store_true", required=True)
    worker.add_argument("--source-launch-id")

    resume_worker = commands.add_parser(
        "resume-worker", help="requeue a blocked worker item"
    )
    resume_worker.add_argument("--source-launch-id", required=True)
    resume_worker.add_argument("--job-id")
    resume_worker.add_argument(
        "--expected-reason-code",
        help="deprecated compatibility option; resume no longer checks reason codes",
    )

    probe = commands.add_parser(
        "probe-ai", help="probe the configured model with synthetic isolated data"
    )
    probe.add_argument("--environment", required=True, choices=("isolated",))
    probe.add_argument("--profile", required=True, choices=("investigation", "repair"))
    probe.add_argument("--timeout-seconds", type=int, default=300)

    gate = commands.add_parser(
        "release-gate", help="recompute a release readiness gate"
    )
    gate.add_argument("--release-id", required=True)
    gate.add_argument("--evaluated-at", required=True)

    historical = commands.add_parser(
        "enqueue-historical",
        help="enqueue a verified run-null historical daily failure once",
    )
    historical.add_argument("--release-id", required=True)
    historical.add_argument(
        "--observation",
        required=True,
        help="JSON observation path relative to DATA_ROOT",
    )
    historical.add_argument("--enqueued-at", required=True)

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
    )
    if initialize:
        store.initialize()
    elif not database_path.is_file():
        raise MaintenanceStoreError("maintenance_database_unavailable")
    return store


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _run_read_only_preflight(args: argparse.Namespace) -> dict[str, object]:
    """Inspect production inputs without initializing, repairing, or tracing them."""

    data_root = Path(args.data_root).expanduser()
    config_path = Path(args.config).expanduser()
    daily_database = (
        Path(args.daily_database).expanduser()
        if getattr(args, "daily_database", None)
        else data_root / "state.sqlite"
    )
    database_path = resolve_maintenance_database_path(
        data_root,
        daily_database_path=daily_database,
    )
    try:
        resolved_root = data_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise UnsafeMaintenancePathError("maintenance_data_root_unavailable") from exc
    try:
        resolved_config = config_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise MaintenanceStoreError("production_config_unavailable") from exc
    expected_config = (resolved_root / "config" / "settings.json").resolve(strict=False)
    if (
        resolved_config != expected_config
        or _has_reparse_point(config_path.parent)
        or _has_reparse_point(config_path)
    ):
        raise UnsafeMaintenancePathError("production_config_path_invalid")
    if not config_path.is_file():
        raise MaintenanceStoreError("production_config_unavailable")
    config_bytes = config_path.read_bytes()
    if len(config_bytes) > 1024 * 1024:
        raise MaintenanceStoreError("production_config_too_large")
    try:
        config_document = json.loads(config_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaintenanceStoreError("production_config_invalid") from exc
    if not isinstance(config_document, dict):
        raise MaintenanceStoreError("production_config_invalid")

    settings_path = resolve_maintenance_settings_path(
        data_root,
        daily_database_path=daily_database,
    )
    settings = load_settings(settings_path)
    if not database_path.is_file():
        raise MaintenanceStoreError("maintenance_database_unavailable")
    try:
        with sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro", uri=True
        ) as connection:
            connection.execute("PRAGMA query_only = ON")
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            schema_version = connection.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error as exc:
        raise MaintenanceStoreError("maintenance_database_unavailable") from exc
    if integrity != ("ok",):
        raise MaintenanceStoreError("maintenance_database_unhealthy")
    if schema_version is None or int(schema_version[0]) != SCHEMA_VERSION:
        raise MaintenanceStoreError("maintenance_schema_version_mismatch")
    return {
        "status": "ready",
        "environment": "production",
        "schema_version": SCHEMA_VERSION,
        "settings_version": settings.settings_version,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "maintenance_database": "available",
        "database_health": "ok",
        "daily_database_separate": True,
    }


def _safe_operation_result_summary(result: object) -> dict[str, object]:
    """Expose only compact, non-sensitive state when its receipt is missing."""

    if type(result) is int:
        return {"kind": "exit_code", "exit_code": result}

    result_type = type(result).__name__
    summary: dict[str, object] = {
        "kind": (
            result_type
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", result_type)
            else "returned"
        )
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
    if args.command == "probe-ai":
        from automation.inago.maintenance.probe import (
            MaintenanceProbeError,
            run_ai_probe,
        )

        try:
            result, exit_code = run_ai_probe(
                data_root=args.data_root,
                profile=args.profile,
                environment=args.environment,
                timeout_seconds=args.timeout_seconds,
                validated_data_root=getattr(args, "validated_probe_root", None),
            )
        except MaintenanceProbeError as exc:
            _emit({"status": "failed", "reason_code": exc.code})
            return 3
        _emit(result)
        return exit_code
    store = _store_for(args)
    if args.command == "enqueue-historical":
        from automation.inago.maintenance.historical import (
            HistoricalIntakeError,
            enqueue_historical_observation,
        )

        try:
            result = enqueue_historical_observation(
                MaintenanceController(store, diagnostic_trace=diagnostic_trace),
                data_root=args.data_root,
                release_id=args.release_id,
                observation_ref=args.observation,
                enqueued_at=args.enqueued_at,
            )
        except HistoricalIntakeError as exc:
            _emit({"status": "failed", "reason_code": exc.code})
            return 3
        except IdempotencyConflictError as exc:
            _emit({"status": "failed", "reason_code": exc.code})
            return 3
        _emit(result)
        return 0 if result.get("status") == "queued" else 3
    if args.command == "worker":
        from automation.inago.maintenance.worker import run_worker_once
        from automation.inago.maintenance.production_workflow import (
            build_production_workflow_provider,
        )

        result = run_worker_once(
            store,
            now=datetime.now(timezone.utc),
            owner_id=f"worker-{uuid.uuid4().hex}",
            owner_pid=os.getpid(),
            owner_start_time=current_process_start_time(),
            source_launch_id=args.source_launch_id,
            workflow_runner=build_production_workflow_provider(
                store, diagnostic_trace=diagnostic_trace
            ),
        )
        _emit(result.to_dict())
        return (
            0
            if result.status
            in {"dispatch_complete", "maintenance_complete", "idle", "waiting", "busy"}
            else 3
        )
    if args.command == "resume-worker":
        now = datetime.now(timezone.utc)
        queue = (
            None
            if args.job_id is not None
            else store.get_worker_queue(args.source_launch_id)
        )
        if args.job_id is None and queue is None:
            _emit({"status": "blocked", "reason_code": "worker_queue_not_found"})
            return 3
        try:
            if args.job_id is not None:
                resumed = store.resume_maintenance_job(
                    job_id=args.job_id,
                    source_launch_id=args.source_launch_id,
                    resumed_at=now,
                )
            else:
                assert queue is not None
                resumed = store.resume_blocked_worker_queue(
                    queue_id=queue.queue_id,
                    resumed_at=now,
                )
        except MaintenanceStoreError as exc:
            _emit({"status": "blocked", "reason_code": exc.code})
            return 3
        if args.job_id is not None:
            _emit(
                {
                    "status": "queued",
                    "job_id": resumed.job_id,
                    "resume_event_id": resumed.resume_event_id,
                    "resume_count": resumed.resume_count,
                    "source_launch_id": resumed.source_launch_id,
                }
            )
        else:
            _emit(
                {
                    "status": "queued",
                    "queue_id": resumed.queue_id,
                    "source_launch_id": resumed.source_launch_id,
                    "target_date": resumed.target_date,
                }
            )
        return 0
    if args.command == "watchdog":
        now = datetime.now(timezone.utc)
        owner_start_time = current_process_start_time() or now.isoformat()
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
        if result.lease_acquired:
            from automation.inago.maintenance.worker import dispatch_ready_worker

            worker_dispatch = dispatch_ready_worker(store)
        else:
            worker_dispatch = None
        _emit(
            {
                "status": "ok" if result.lease_acquired else "busy",
                "queued_source_launch_ids": list(result.queued_source_launch_ids),
                "queued_queue_ids": list(result.queued_queue_ids),
                "reconciled_queue_ids": list(result.reconciled_queue_ids),
                "worker_dispatch_status": (
                    worker_dispatch.status if worker_dispatch is not None else "skipped"
                ),
                "worker_queue_id": (
                    worker_dispatch.queue_id if worker_dispatch is not None else None
                ),
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
            if "probe-ai" in raw_args:
                return int(exc.code or 0)
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
    if args.command == "preflight":
        if not isinstance(args.data_root, str) or not args.data_root.strip():
            _emit({"status": "blocked", "reason_code": "data_root_required"})
            return 3
        try:
            result = _run_read_only_preflight(args)
        except (
            MaintenanceSettingsError,
            MaintenanceStoreError,
            UnsafeMaintenancePathError,
        ) as exc:
            _emit({"status": "blocked", "reason_code": exc.code})
            return 3
        except OSError:
            _emit({"status": "blocked", "reason_code": "preflight_input_unavailable"})
            return 3
        _emit(result)
        return 0
    if not isinstance(args.data_root, str) or not args.data_root.strip():
        _emit({"status": "failed", "reason_code": "data_root_required"})
        return 3
    if args.command == "probe-ai":
        from automation.inago.maintenance.probe import (
            MaintenanceProbeError,
            validate_ai_probe_target,
        )

        try:
            args.validated_probe_root = validate_ai_probe_target(
                args.data_root, profile=args.profile
            )
        except MaintenanceProbeError as exc:
            _emit({"status": "failed", "reason_code": exc.code})
            return 3
    trace = MaintenanceDiagnosticTrace(
        args.data_root, maintenance_id=args.maintenance_id
    )
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
