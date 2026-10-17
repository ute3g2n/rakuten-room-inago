"""Safe synthetic inputs shared by U11 detector contract tests."""

from __future__ import annotations

from copy import deepcopy

from automation.inago.maintenance.evidence import EVIDENCE_CLASS_IDS

TARGET_DATE = "2026-10-17"
LAUNCH_ID = "launch-20261017-130001"
RUN_ID = "run-20261017-001"
CHECKED_AT = "2026-10-17T21:45:00+09:00"

REQUIRED_EVIDENCE_CLASSES = {"L01", "L02", "M03", "S01", "V01", "B01", "B02"}


def make_case(*, status: str = "shortfall", run_id: str | None = RUN_ID):
    if status == "shortfall":
        overall_status = "partial"
        daily_status = "partial"
        launch_status = "complete"
        exit_code = 5
        reason_code = "daily_target_unmet"
        counts = {
            "daily_target": 15,
            "initial_allocation_count": 15,
            "supplemental_allocation_count": 3,
            "committed_count": 12,
            "shortfall_count": 3,
            "actual_posted_count": 0,
            "source_failures": 0,
            "error_count": 0,
        }
        daily_reason = "candidate_or_theme_exhausted"
    elif status == "complete":
        overall_status = "complete"
        daily_status = "complete"
        launch_status = "complete"
        exit_code = 0
        reason_code = None
        counts = {
            "daily_target": 15,
            "initial_allocation_count": 15,
            "supplemental_allocation_count": 3,
            "committed_count": 15,
            "shortfall_count": 0,
            "actual_posted_count": 0,
            "source_failures": 0,
            "error_count": 0,
        }
        daily_reason = "daily_target_reached"
    elif status == "failed":
        overall_status = "failed"
        daily_status = "failed"
        launch_status = "failed"
        exit_code = 4
        reason_code = "daily_runtime_error"
        counts = {
            "daily_target": 15,
            "initial_allocation_count": 15,
            "supplemental_allocation_count": 0,
            "committed_count": 0,
            "shortfall_count": 15,
            "actual_posted_count": 0,
            "source_failures": 0,
            "error_count": 1,
        }
        daily_reason = "daily_runtime_error"
    else:
        raise ValueError(status)

    completion = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": LAUNCH_ID,
        "target_date": TARGET_DATE,
        "status": launch_status,
        "exit_code": exit_code,
        "source_run_id": run_id,
        "overall_status": overall_status,
        "daily_status": daily_status,
        "reason_code": reason_code,
        "failure_phase": "daily" if status == "failed" else None,
        "failed_checks": [],
        "primary_error_class": "IntegrityError" if status == "failed" else None,
        "secondary_error_classes": [],
        "trace_status": "healthy" if status != "failed" else "not_written",
    }

    phase_results = {
        "daily": {
            "status": daily_status,
            "committed_count": counts["committed_count"],
            "shortfall_count": counts["shortfall_count"],
            "source_failures": counts["source_failures"],
            "error_count": counts["error_count"],
        },
        "metrics": {"status": "observed" if status != "failed" else "unknown"},
        "export": {"status": "ready"},
        "start_backup": {"status": "ready"},
        "preservation": {
            "status": "ready",
            "end_backup_status": "ready",
            "monthly_restore_status": "already_checked",
        },
        "trace": {
            "status": "healthy" if status != "failed" else "not_written",
            "event_count": 12 if status != "failed" else 0,
            "reference": (
                "logs/traces/2026-10-17/run-001.jsonl" if status != "failed" else None
            ),
        },
    }
    daily_result = {
        "schema_version": "daily-result-observation-v1",
        "target_date": TARGET_DATE,
        "run_id": run_id,
        "status": overall_status,
        "overall_status": overall_status,
        "daily_status": daily_status,
        "daily_reason": daily_reason,
        "reason": reason_code,
        **counts,
        "retry_summary": {
            "attempt_count": 0,
            "recovered_count": 0,
            "unresolved_count": 0,
            "contract_violation_count": 0,
        },
        "phase_results": phase_results,
    }

    classes = []
    for class_id in EVIDENCE_CLASS_IDS:
        available = class_id in REQUIRED_EVIDENCE_CLASSES
        classes.append(
            {
                "class_id": class_id,
                "status": "collected" if available else "not_listed",
                "artifact_count": 1 if available else 0,
                "record_count": 1 if available else None,
            }
        )
    manifest = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "captured_at": "2026-10-17T21:40:00+09:00",
        "correlation": {
            "source_launch_id": LAUNCH_ID,
            **({"source_run_id": run_id} if run_id is not None else {}),
        },
        "classes": classes,
        "artifacts": [],
        "events": [],
        "redaction": {
            "allowlist_version": 1,
            "free_text_included": False,
            "dropped_fields_count": 0,
        },
    }
    return completion, daily_result, manifest


def clone_case(*, status: str = "shortfall", run_id: str | None = RUN_ID):
    return tuple(deepcopy(value) for value in make_case(status=status, run_id=run_id))
