"""Write allowlisted lifecycle events for human report actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from automation.inago.trace import TraceSession, TraceWriter, TraceWriteResult


def write_report_event(
    *,
    data_root: str | Path | None,
    run_id: str,
    target_date: str,
    component: str,
    operation: str,
    phase: str,
    action_id: str,
    clock: Any | None = None,
    draft_id: str | None = None,
    draft_version: int | None = None,
    report_action: str | None = None,
    receipt_revision: int | None = None,
    apply_status: str | None = None,
    error_code: str | None = None,
) -> TraceWriteResult | None:
    """Append one report lifecycle event without changing report persistence."""

    if data_root is None:
        return None
    try:
        writer = TraceWriter(data_root, run_id, target_date, clock=clock)
        session = TraceSession(
            writer,
            component=component,
            request_id=action_id,
            task_id=f"{component}_{operation}",
            clock=clock,
        )
        fields: dict[str, Any] = {"action_id": action_id}
        optional = {
            "draft_id": draft_id,
            "draft_version": draft_version,
            "report_action": report_action,
            "receipt_revision": receipt_revision,
            "apply_status": apply_status,
            "primary_error_code": error_code,
        }
        fields.update(
            {key: value for key, value in optional.items() if value is not None}
        )
        return session.record_stage(
            operation,
            phase=phase,
            event_kind=f"report_{phase}",
            **fields,
        )
    except Exception:
        # A saved receipt or applied report remains authoritative if diagnostics
        # cannot be appended; report persistence must keep its original result.
        return TraceWriteResult("unavailable", "report_trace_write_unavailable")
