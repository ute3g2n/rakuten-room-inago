"""Explicit fixture and repository-local production entry points."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, TextIO

from automation.inago.clock import Clock
from automation.inago.orchestrator import DailyPorts, DailyRunOutcome
from automation.inago.paths import PathContractError, resolve_runtime_paths
from automation.inago.trigger import run_scheduled_daily


SUPPORTED_MODES = (
    "setup",
    "daily",
    "status",
    "recovery",
    "config-change",
    "export",
    "serve",
    "migrate",
    "backup",
    "restore-check",
    "acceptance",
)
EXIT_INVALID_INPUT = 2
EXIT_NOT_IMPLEMENTED = 3
EXIT_RUNTIME_ERROR = 4


@dataclass(frozen=True, slots=True)
class DailyCLIContext:
    """Explicit isolated dependencies required by the fixture-only daily CLI."""

    connection: sqlite3.Connection
    data_root: Path
    ports: DailyPorts
    clock: Clock | None = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inago")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in SUPPORTED_MODES:
        mode_parser = subparsers.add_parser(mode)
        mode_parser.add_argument("--config", required=True)
        mode_parser.add_argument("--data-root", required=True)
        mode_parser.add_argument(
            "--environment", choices=("fixture", "production"), default="fixture"
        )
    return parser


def _emit(stream: TextIO, payload: dict[str, object]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    stream.write("\n")


def _validate_fixture_database(
    connection: sqlite3.Connection, *, data_root: Path
) -> None:
    """Reject injected connections whose SQLite main file escapes fixture storage."""

    databases = connection.execute("PRAGMA database_list").fetchall()
    main_files = [str(row[2]) for row in databases if str(row[1]) == "main"]
    if len(main_files) != 1 or not main_files[0]:
        raise PathContractError("daily fixture database must be a file under data_root")
    database_path = Path(main_files[0]).expanduser().resolve()
    if not database_path.is_relative_to(data_root):
        raise PathContractError("daily fixture database must stay under data_root")


def _validate_fixture_ports(ports: DailyPorts) -> None:
    """Require every injected adapter to declare fixture-only behavior."""

    if ports.fixture_only is not True:
        raise PathContractError("daily CLI accepts fixture-only ports")
    adapters = {
        "current_settings_version": ports.current_settings_version,
        "theme_planner": ports.theme_planner,
        "room_port": ports.room_port,
        "copy_spec_factory": ports.copy_spec_factory,
        "copy_runner": ports.copy_runner,
        "metrics_port": ports.metrics_port,
        "cancel_owned_jobs": ports.cancel_owned_jobs,
        "cancel_metrics_job": ports.cancel_metrics_job,
        "offer_intake": ports.offer_intake,
    }
    for name, adapter in adapters.items():
        if adapter is not None and getattr(adapter, "fixture_only", False) is not True:
            raise PathContractError(f"daily CLI adapter {name} is not fixture-only")


def _append_production_log(
    root: Path, *, mode: str, status: str, reason: str | None = None
) -> None:
    """Append a private lifecycle event without arguments, URLs, or source text."""

    from datetime import timezone
    from automation.inago.production_runtime import _checked_private_path

    log_path = _checked_private_path(
        root / "logs" / "production-cli.jsonl", "lifecycle log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = _checked_private_path(log_path, "lifecycle log")
    event: dict[str, object] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "status": status,
    }
    if reason:
        event["reason"] = reason
    encoded = (json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n").encode(
        "utf-8"
    )
    with log_path.open("ab") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _run_production(args, *, output: TextIO, error_output: TextIO) -> int:
    from automation.inago.production_runtime import (
        ProductionRuntimeError,
        initialize_production_runtime,
        load_production_config,
        run_production_daily,
        serve_human_api,
    )
    from automation.inago.clock import SystemClock

    config = None

    def record(status: str, reason: str | None = None) -> None:
        try:
            _append_production_log(
                config.runtime_root, mode=args.mode, status=status, reason=reason
            )
        except (OSError, ProductionRuntimeError) as exc:
            _emit(
                error_output,
                {
                    "status": "log_unavailable",
                    "mode": args.mode,
                    "reason": type(exc).__name__,
                },
            )

    try:
        config = load_production_config(args.config, data_root=args.data_root)
        if args.mode == "setup":
            result = initialize_production_runtime(
                config,
                now=SystemClock().now_jst().isoformat(),
            )
            record("ready")
            _emit(output, {"status": "ready", **result})
            return 0
        if args.mode == "daily":
            result = run_production_daily(config)
            status = str(result.get("status", "failed"))
            record(
                status=status,
                reason=(
                    result.get("reason")
                    if isinstance(result.get("reason"), str)
                    else None
                ),
            )
            _emit(output, result)
            return EXIT_RUNTIME_ERROR if status in {"failed", "locked"} else 0
        if args.mode == "serve":
            record("starting")
            serve_human_api(config)
            return 0
        _emit(output, {"status": "not_implemented", "mode": args.mode})
        return EXIT_NOT_IMPLEMENTED
    except Exception as exc:
        if config is not None:
            record("failed", reason=type(exc).__name__)
        _emit(error_output, {"status": "runtime_error", "reason": type(exc).__name__})
        return EXIT_RUNTIME_ERROR


def main(
    argv: Sequence[str] | None = None,
    *,
    ports: object | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Validate fixture-only paths, then run only an explicitly injected daily mode.

    This entry point does not open a database, create production adapters, or
    register a scheduler. Without a ``DailyCLIContext``, every mode remains a
    safe not-implemented response.
    """

    output = stdout if stdout is not None else sys.stdout
    error_output = stderr if stderr is not None else sys.stderr
    args = _parser().parse_args(argv)

    if args.environment == "production":
        return _run_production(args, output=output, error_output=error_output)

    try:
        paths = resolve_runtime_paths(
            data_root=args.data_root,
            config_path=args.config,
            fixture_mode=True,
        )
        config = json.loads(paths.config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, PathContractError) as exc:
        _emit(error_output, {"status": "invalid_input", "reason": str(exc)})
        return EXIT_INVALID_INPUT

    if not isinstance(config, dict):
        _emit(
            error_output,
            {"status": "invalid_input", "reason": "config must be an object"},
        )
        return EXIT_INVALID_INPUT
    if (
        config.get("environment") != "fixture"
        or config.get("network_allowed") is not False
    ):
        _emit(
            error_output,
            {
                "status": "invalid_input",
                "reason": "fixture mode must disable network access",
            },
        )
        return EXIT_INVALID_INPUT

    if args.mode == "daily" and isinstance(ports, DailyCLIContext):
        try:
            context_root = ports.data_root.expanduser().resolve()
            if context_root != paths.data_root:
                raise PathContractError(
                    "daily context data_root must match the validated fixture path"
                )
            _validate_fixture_database(ports.connection, data_root=context_root)
            _validate_fixture_ports(ports.ports)
            result: DailyRunOutcome = run_scheduled_daily(
                ports.connection,
                data_root=context_root,
                ports=ports.ports,
                clock=ports.clock,
            )
        except Exception as exc:
            _emit(
                error_output,
                {"status": "runtime_error", "reason": type(exc).__name__},
            )
            return EXIT_RUNTIME_ERROR
        _emit(
            output,
            {
                "status": result.status,
                "run_id": result.run_id,
                "target_date": result.target_date,
                "committed_count": result.committed_count,
                "attempt_used": result.attempt_used,
                "metrics_status": result.metrics_status,
                "resumed": result.resumed,
                "reason": result.reason,
            },
        )
        return EXIT_RUNTIME_ERROR if result.status in {"failed", "locked"} else 0

    _emit(output, {"status": "not_implemented", "mode": args.mode})
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":
    raise SystemExit(main())
