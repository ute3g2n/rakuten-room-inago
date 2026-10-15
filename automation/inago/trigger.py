"""Explicit daily entry point for an already configured scheduler."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Callable

from automation.inago.clock import Clock
from automation.inago.orchestrator import DailyPorts, DailyRunOutcome, run_daily


def run_scheduled_daily(
    connection: sqlite3.Connection,
    *,
    data_root: str | Path,
    ports: DailyPorts,
    clock: Clock | None = None,
    run_id_factory: Callable[[date], str] | None = None,
    seed_factory: Callable[[], str] | None = None,
    on_draft_commit: Callable[[sqlite3.Connection], None] | None = None,
) -> DailyRunOutcome:
    """Run one schedule invocation; this function does not register a task."""

    return run_daily(
        connection,
        data_root=data_root,
        ports=ports,
        clock=clock,
        run_id_factory=run_id_factory,
        seed_factory=seed_factory,
        on_draft_commit=on_draft_commit,
    )


def run_public_scheduled_daily(
    connection: sqlite3.Connection,
    *,
    data_root: str | Path,
    source: Any,
    ai_root: Any,
    copy_spec_factory: Callable[..., Any],
    metrics_port: Any,
    cancel_metrics_job: Callable[[str], bool],
    clock: Clock | None = None,
    run_id_factory: Callable[[date], str] | None = None,
    seed_factory: Callable[[], str] | None = None,
    on_draft_commit: Callable[[sqlite3.Connection], None] | None = None,
) -> DailyRunOutcome:
    """Compose public runtime ports and invoke one scheduled daily run.

    This is the scheduler-facing production entry point. It does not register
    or start a Windows task; callers decide when to invoke it.
    """

    from automation.inago.daily_runtime import compose_public_daily_ports

    ports = compose_public_daily_ports(
        data_root=data_root,
        source=source,
        ai_root=ai_root,
        copy_spec_factory=copy_spec_factory,
        metrics_port=metrics_port,
        cancel_metrics_job=cancel_metrics_job,
        clock=clock,
    )
    return run_scheduled_daily(
        connection,
        data_root=data_root,
        ports=ports,
        clock=clock,
        run_id_factory=run_id_factory,
        seed_factory=seed_factory,
        on_draft_commit=on_draft_commit,
    )
