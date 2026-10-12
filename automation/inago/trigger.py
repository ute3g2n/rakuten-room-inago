"""Explicit daily entry point for an already configured scheduler."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Callable

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
) -> DailyRunOutcome:
    """Run one schedule invocation; this function does not register a task."""

    return run_daily(
        connection,
        data_root=data_root,
        ports=ports,
        clock=clock,
        run_id_factory=run_id_factory,
        seed_factory=seed_factory,
    )
