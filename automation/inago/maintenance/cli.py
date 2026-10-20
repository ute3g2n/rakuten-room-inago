"""Command line for inspecting and operating the isolated maintenance store."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Sequence
import uuid
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
    MaintenanceStore,
    MaintenanceStoreError,
    SCHEMA_VERSION,
)
from automation.inago.maintenance.process_identity import current_process_start_time


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inago-maintenance")
    parser.add_argument("--data-root", help="maintenance runtime root")
    parser.add_argument(
        "--daily-database",
        help="daily DB path used only for isolation checks (default: DATA_ROOT/state.sqlite)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="create the management DB and initial settings")
    commands.add_parser("status", help="show schema version and safe row counts")

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
    worker.add_argument("--start-role", type=int, default=1)
    worker.add_argument("--request", default="")

    resume_worker = commands.add_parser(
        "resume-worker", help="requeue a blocked worker item"
    )
    resume_worker.add_argument("--source-launch-id", required=True)
    resume_worker.add_argument("--job-id")
    resume_worker.add_argument(
        "--expected-reason-code",
        help="deprecated compatibility option; resume no longer checks reason codes",
    )

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
        "config_path": str(config_path),
        "maintenance_database": "available",
        "database_health": "ok",
        "daily_database_separate": True,
    }


def _run(
    args: argparse.Namespace,
) -> int:
    if args.command == "init":
        store = _store_for(args, initialize=True)
        _emit({"status": "initialized", "schema_version": SCHEMA_VERSION})
        return 0
    store = _store_for(args)
    if args.command == "worker":
        from automation.inago.maintenance.worker import run_worker_once
        from automation.inago.maintenance.skill_chain import (
            build_skill_chain_workflow_provider,
        )

        # Apply known additive maintenance migrations to the existing store
        # before the selected launch is claimed. Never create a new DB here.
        if not store.database_path.is_file():
            raise MaintenanceStoreError("maintenance_database_unavailable")
        store.initialize()

        result = run_worker_once(
            store,
            now=datetime.now(timezone.utc),
            owner_id=f"worker-{uuid.uuid4().hex}",
            owner_pid=os.getpid(),
            owner_start_time=current_process_start_time(),
            source_launch_id=args.source_launch_id,
            workflow_runner=build_skill_chain_workflow_provider(
                store,
                repository_root=Path(__file__).resolve().parents[3],
                start_role=args.start_role,
                request=args.request,
            ),
        )
        _emit(result.to_dict())
        return (
            0
            if result.status
            in {
                "dispatch_complete",
                "maintenance_complete",
                "maintenance_incomplete",
                "idle",
                "waiting",
                "busy",
            }
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
    if args.command == "status":
        _emit(
            {
                "status": "ok",
                "schema_version": SCHEMA_VERSION,
                "counts": store.counts(),
            }
        )
        return 0
    raise MaintenanceStoreError("command_unknown")


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _parser().parse_args(raw_args)
    except SystemExit as exc:
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
    if args.command == "worker":
        # The assigned launch is already selected by source_launch_id. Do not
        # run unrelated retention; diagnostic persistence is not a start gate.
        return _run(args)
    try:
        return _run(args)
    except (
        UnsafeMaintenancePathError,
        MaintenanceSettingsError,
        MaintenanceStoreError,
    ) as exc:
        _emit({"status": "error", "reason_code": exc.code})
        return 2
    except (OSError, sqlite3.Error, ValueError, RuntimeError):
        _emit({"status": "error", "reason_code": "maintenance_operation_failed"})
        return 2


if __name__ == "__main__":
    sys.exit(main())
