"""Compose the public source and AI-O into the Python daily execution ports."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from automation.inago.clock import Clock, SystemClock
from automation.inago.daily_theme_planner import DurableTrendsThemePlanner
from automation.inago.offer_intake import SQLitePublicOfferIntake
from automation.inago.orchestrator import DailyPorts


class DailyRuntimeCompositionError(ValueError):
    """The public UI, AI root, and data directory do not form one runtime."""


def compose_public_daily_ports(
    *,
    data_root: str | Path,
    source: Any,
    ai_root: Any,
    copy_spec_factory: Callable[..., Any],
    metrics_port: Any,
    cancel_metrics_job: Callable[[str], bool],
    product_page_ocr: Any | None = None,
    clock: Clock | None = None,
) -> DailyPorts:
    """Build the sole public-UI daily composition without starting a run/task.

    The scheduler still owns invocation. This factory shares one AI-O root
    between the KEY planner and copy jobs, and binds ROOM evidence to the same
    daily runtime before it can enter ``offer``.
    """

    root = Path(data_root).expanduser().resolve()
    evidence_root = getattr(source, "evidence_root", None)
    if evidence_root is None:
        config = getattr(source, "config", None)
        evidence_root = getattr(config, "evidence_root", None)
    if (
        evidence_root is None
        or Path(evidence_root).expanduser().resolve()
        != (root / "observations").resolve()
    ):
        raise DailyRuntimeCompositionError(
            "public evidence root must be data_root/observations"
        )
    for name in ("search", "fetch", "product_fact", "cancel_owned_jobs"):
        if not callable(getattr(source, name, None)):
            raise DailyRuntimeCompositionError(f"public source lacks {name}")
    for name in ("run", "cancel_owned_jobs"):
        if not callable(getattr(ai_root, name, None)):
            raise DailyRuntimeCompositionError(f"AI-O root lacks {name}")
    for name, value in (
        ("copy_spec_factory", copy_spec_factory),
        ("metrics_port.collect", getattr(metrics_port, "collect", None)),
        ("cancel_metrics_job", cancel_metrics_job),
    ):
        if not callable(value):
            raise DailyRuntimeCompositionError(f"runtime dependency lacks {name}")

    selected_clock = clock or SystemClock()
    fixture_only = all(
        getattr(value, "fixture_only", False) is True
        for value in (
            source,
            ai_root,
            copy_spec_factory,
            metrics_port,
            cancel_metrics_job,
        )
    )

    def current_settings_version(connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT version FROM settings_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None or not isinstance(row[0], str) or not row[0].strip():
            raise DailyRuntimeCompositionError("active_settings_version_missing")
        return str(row[0])

    def cancel_owned_jobs(run_id: str) -> bool:
        source_stopped = False
        root_stopped = False
        try:
            source_stopped = source.cancel_owned_jobs(run_id) is True
        except Exception:
            pass
        try:
            root_stopped = ai_root.cancel_owned_jobs(run_id) is True
        except Exception:
            pass
        return source_stopped and root_stopped

    def cancel_room_request(run_id: str, request_id: str) -> bool:
        callback = getattr(source, "cancel_active_request", None)
        return callback(run_id, request_id) is True if callable(callback) else False

    def cancel_ai_request(run_id: str, request_id: str) -> bool:
        callback = getattr(ai_root, "cancel_active_request", None)
        return callback(run_id, request_id) is True if callable(callback) else False

    current_settings_version.fixture_only = fixture_only
    cancel_owned_jobs.fixture_only = fixture_only
    cancel_room_request.fixture_only = fixture_only
    cancel_ai_request.fixture_only = fixture_only
    return DailyPorts(
        current_settings_version=current_settings_version,
        theme_planner=DurableTrendsThemePlanner(source, ai_root, selected_clock),
        room_port=source,
        copy_spec_factory=copy_spec_factory,
        copy_runner=ai_root,
        metrics_port=metrics_port,
        cancel_owned_jobs=cancel_owned_jobs,
        cancel_metrics_job=cancel_metrics_job,
        offer_intake=SQLitePublicOfferIntake(source),
        fixture_only=fixture_only,
        cancel_room_request=cancel_room_request,
        cancel_ai_request=cancel_ai_request,
        product_page_ocr=product_page_ocr,
        product_page_capture=source,
    )
