"""Command line for inspecting and operating the isolated maintenance store."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from automation.inago.maintenance.controller import MaintenanceController
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


def _run(args: argparse.Namespace) -> int:
    if args.command == "init":
        store = _store_for(args, initialize=True)
        _emit({"status": "initialized", "schema_version": 1})
        return 0
    store = _store_for(args)
    controller = MaintenanceController(store)
    if args.command == "status":
        _emit({"status": "ok", "schema_version": 1, "counts": store.counts()})
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
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except (
        UnsafeMaintenancePathError,
        MaintenanceSettingsError,
        MaintenanceStoreError,
    ) as exc:
        _emit({"status": "error", "reason_code": exc.code})
        return 2
    except (OSError, sqlite3.Error, ValueError):
        _emit({"status": "error", "reason_code": "maintenance_operation_failed"})
        return 2


if __name__ == "__main__":
    sys.exit(main())
