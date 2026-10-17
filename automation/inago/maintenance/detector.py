"""Deterministically preclassify a completed daily run for the check-only stage."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from automation.inago.maintenance.evidence import EVIDENCE_CLASS_IDS


CHECK_RESULT_SCHEMA_VERSION = "maintenance-check-result-v1"
DAILY_TARGET = 15
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUIRED_EVIDENCE_CLASSES = frozenset(
    {"L01", "L02", "M03", "S01", "V01", "B01", "B02"}
)
_EXTERNAL_STOP_CODES = frozenset(
    {
        "authentication_required",
        "shared_deadline_exhausted",
        "source_cancelled",
        "source_timeout",
        "transport_error",
    }
)
_COUNT_FIELDS = (
    "daily_target",
    "initial_allocation_count",
    "supplemental_allocation_count",
    "committed_count",
    "shortfall_count",
    "actual_posted_count",
    "source_failures",
    "error_count",
)
_OVERALL_STATUSES = frozenset(
    {
        "complete",
        "partial",
        "shortfall",
        "failed",
        "blocked",
        "unknown",
        "not_due",
        "metrics_failed",
        "export_failed",
        "preservation_failed",
    }
)
_DAILY_STATUSES = frozenset(
    {"complete", "partial", "shortfall", "failed", "blocked", "unknown", "not_due"}
)
_COMPLETION_STATUSES = frozenset({"complete", "blocked", "unknown", "failed"})
_RETRY_FIELDS = (
    "attempt_count",
    "recovered_count",
    "unresolved_count",
    "contract_violation_count",
)
_CLASS_STATUSES = frozenset(
    {
        "collected",
        "content_omitted",
        "empty",
        "empty_response",
        "invalid",
        "invalid_response",
        "not_saved",
        "expired",
        "unavailable",
        "suppressed",
        "disappeared",
        "omitted_size_limit",
        "no_correlated_records",
        "not_listed",
        "partial",
    }
)


class DailyPreclassificationError(ValueError):
    """An invalid caller-supplied identity or check timestamp."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@lru_cache(maxsize=1)
def _evidence_manifest_validator() -> Draft202012Validator:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "maintenance-evidence.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _safe_id(value: object) -> str | None:
    if isinstance(value, str) and _ID_PATTERN.fullmatch(value):
        return value
    return None


def _valid_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _checked_at(value: datetime | str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DailyPreclassificationError("checked_at_invalid") from exc
    else:
        raise DailyPreclassificationError("checked_at_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyPreclassificationError("checked_at_timezone_missing")
    return parsed.isoformat()


def _read_run_id(value: Mapping[str, Any]) -> tuple[str | None, bool]:
    present = [key for key in ("source_run_id", "run_id") if key in value]
    if not present:
        return None, False
    candidates = [value[key] for key in present]
    if any(item is not None and _safe_id(item) is None for item in candidates):
        return None, True
    normalized = {item for item in candidates if item is not None}
    if len(normalized) > 1:
        return None, True
    if any(item is None for item in candidates) and normalized:
        return None, True
    return (next(iter(normalized)) if normalized else None), False


def _optional_count(value: Mapping[str, Any], key: str) -> tuple[int | None, bool]:
    if key not in value or value[key] is None:
        return None, False
    item = value[key]
    if type(item) is not int or item < 0 or item > 2**31 - 1:
        return None, True
    return item, False


def _status(value: object, allowed: frozenset[str], *, default: str = "unknown") -> str:
    return value if isinstance(value, str) and value in allowed else default


def _phase_results(
    daily_result: Mapping[str, Any] | None,
) -> tuple[dict[str, str], int | None, bool]:
    allowed = {
        "daily": frozenset(
            {
                "complete",
                "partial",
                "shortfall",
                "failed",
                "blocked",
                "unknown",
                "not_due",
            }
        ),
        "metrics": frozenset(
            {
                "observed",
                "partial",
                "failed",
                "unavailable",
                "unknown",
                "not_run",
                "not_configured",
            }
        ),
        "export": frozenset(
            {"ready", "failed", "unknown", "not_run", "not_configured"}
        ),
        "preservation": frozenset(
            {"ready", "failed", "unknown", "not_run", "not_configured"}
        ),
        "start_backup": frozenset(
            {"ready", "failed", "unknown", "not_run", "not_configured"}
        ),
        "end_backup": frozenset(
            {"ready", "failed", "unknown", "not_run", "not_configured"}
        ),
        "monthly_restore": frozenset(
            {
                "ready",
                "already_checked",
                "failed",
                "unknown",
                "not_run",
                "not_configured",
            }
        ),
        "trace": frozenset(
            {"healthy", "degraded", "unavailable", "not_written", "unknown"}
        ),
    }
    result = {name: "unknown" for name in allowed}
    if daily_result is None:
        return result, None, False
    phases = daily_result.get("phase_results")
    if not isinstance(phases, Mapping):
        return result, None, False

    def pick_status(container: object, key: str, permitted: frozenset[str]) -> str:
        if not isinstance(container, Mapping):
            return "unknown"
        return _status(container.get(key), permitted)

    for name in ("daily", "metrics", "export", "preservation", "trace"):
        result[name] = pick_status(phases.get(name), "status", allowed[name])

    result["start_backup"] = pick_status(
        phases.get("start_backup"), "status", allowed["start_backup"]
    )
    preservation = phases.get("preservation")
    if isinstance(preservation, Mapping):
        end_backup = preservation.get("end_backup")
        if not isinstance(end_backup, Mapping):
            end_backup = {"status": preservation.get("end_backup_status")}
        result["end_backup"] = pick_status(end_backup, "status", allowed["end_backup"])
        monthly = preservation.get("monthly_restore")
        if not isinstance(monthly, Mapping):
            monthly = {"status": preservation.get("monthly_restore_status")}
        result["monthly_restore"] = pick_status(
            monthly, "status", allowed["monthly_restore"]
        )

    trace = phases.get("trace")
    event_count, invalid_count = (
        _optional_count(trace, "event_count")
        if isinstance(trace, Mapping)
        else (None, False)
    )
    return result, event_count, not invalid_count


def _evidence_state(
    manifest: object,
    *,
    expected_target_date: str,
    expected_source_launch_id: str,
    expected_source_run_id: str | None,
) -> tuple[dict[str, Any], bool]:
    evidence: dict[str, Any] = {"status": "missing", "unavailable_classes": []}
    if manifest is None:
        return evidence, False
    if not isinstance(manifest, Mapping):
        evidence["status"] = "invalid"
        return evidence, False
    if (
        type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
    ):
        evidence["status"] = "invalid"
        return evidence, False
    try:
        schema_valid = _evidence_manifest_validator().is_valid(manifest)
    except (OSError, UnicodeError, ValueError, SchemaError, TypeError):
        schema_valid = False
    if not schema_valid:
        evidence["status"] = "invalid"
        return evidence, False
    if manifest.get("target_date") != expected_target_date:
        evidence["status"] = "identity_mismatch"
        return evidence, True
    correlation = manifest.get("correlation")
    if not isinstance(correlation, Mapping):
        evidence["status"] = "invalid"
        return evidence, False
    manifest_launch_id = correlation.get("source_launch_id")
    manifest_run_id, invalid_run = _read_run_id(correlation)
    if (
        invalid_run
        or manifest_launch_id != expected_source_launch_id
        or manifest_run_id != expected_source_run_id
    ):
        evidence["status"] = "identity_mismatch"
        return evidence, True

    classes = manifest.get("classes")
    if not isinstance(classes, list):
        evidence["status"] = "invalid"
        return evidence, False
    by_id: dict[str, Mapping[str, Any]] = {}
    for entry in classes:
        if not isinstance(entry, Mapping):
            evidence["status"] = "invalid"
            return evidence, False
        class_id = entry.get("class_id")
        if (
            not isinstance(class_id, str)
            or class_id not in EVIDENCE_CLASS_IDS
            or class_id in by_id
        ):
            evidence["status"] = "invalid"
            return evidence, False
        by_id[class_id] = entry
    if set(by_id) != set(EVIDENCE_CLASS_IDS):
        evidence["status"] = "invalid"
        return evidence, False

    unavailable: list[dict[str, str]] = []
    for class_id in sorted(_REQUIRED_EVIDENCE_CLASSES):
        status = by_id[class_id].get("status")
        safe_status = (
            status
            if isinstance(status, str) and status in _CLASS_STATUSES
            else "unknown"
        )
        if safe_status != "collected":
            unavailable.append({"class_id": class_id, "status": safe_status})
    evidence["unavailable_classes"] = unavailable
    evidence["status"] = "incomplete" if unavailable else "complete"
    return evidence, False


def preclassify_daily_outcome(
    *,
    expected_target_date: str,
    expected_source_launch_id: str,
    completion: Mapping[str, Any] | None,
    daily_result: Mapping[str, Any] | None,
    evidence_manifest: Mapping[str, Any] | None,
    checked_at: datetime | str,
) -> dict[str, Any]:
    """Return a safe check-only classification without writing state or launching work."""

    if not _valid_date(expected_target_date):
        raise DailyPreclassificationError("target_date_invalid")
    expected_launch_id = _safe_id(expected_source_launch_id)
    if expected_launch_id is None:
        raise DailyPreclassificationError("source_launch_id_invalid")
    observed_at = _checked_at(checked_at)
    completion = completion if isinstance(completion, Mapping) else None
    daily_result = daily_result if isinstance(daily_result, Mapping) else None

    counts: dict[str, int | None] = {}
    count_invalid = False
    for key in _COUNT_FIELDS:
        counts[key], invalid = (
            _optional_count(daily_result, key)
            if daily_result is not None
            else (None, False)
        )
        count_invalid = count_invalid or invalid

    retry_summary: dict[str, int | None] = {}
    retry_invalid = False
    retry_source = (
        daily_result.get("retry_summary") if daily_result is not None else None
    )
    if not isinstance(retry_source, Mapping):
        retry_source = {}
    for key in _RETRY_FIELDS:
        retry_summary[key], invalid = _optional_count(retry_source, key)
        retry_invalid = retry_invalid or invalid

    completion_run, completion_run_invalid = (
        _read_run_id(completion) if completion is not None else (None, False)
    )
    daily_run, daily_run_invalid = (
        _read_run_id(daily_result) if daily_result is not None else (None, False)
    )
    run_identity_invalid = completion_run_invalid or daily_run_invalid
    if completion_run != daily_run and (
        completion is not None and daily_result is not None
    ):
        run_identity_invalid = True
    source_run_id = None if run_identity_invalid else completion_run

    evidence, manifest_identity_invalid = _evidence_state(
        evidence_manifest,
        expected_target_date=expected_target_date,
        expected_source_launch_id=expected_launch_id,
        expected_source_run_id=source_run_id,
    )

    phase_results, trace_event_count, trace_count_valid = _phase_results(daily_result)
    violations: list[str] = []
    if completion is not None:
        if completion.get("schema_version") != "daily-launch-completion-v1":
            violations.append("status_mismatch")
        if completion.get("source_launch_id") != expected_launch_id:
            violations.append("identity_mismatch")
        if completion.get("target_date") != expected_target_date:
            violations.append("identity_mismatch")
    if run_identity_invalid or manifest_identity_invalid:
        violations.append("identity_mismatch")
    if daily_result is not None:
        if daily_result.get("target_date") != expected_target_date:
            violations.append("identity_mismatch")
        daily_schema_version = daily_result.get("schema_version")
        if daily_schema_version is not None and (
            not isinstance(daily_schema_version, str)
            or daily_schema_version
            not in {"daily-result-observation-v1", "daily-run-result-v1"}
        ):
            violations.append("status_mismatch")
        raw_overall = daily_result.get("overall_status", daily_result.get("status"))
        for key in ("overall_status", "status"):
            value = daily_result.get(key)
            if key in daily_result and (
                not isinstance(value, str) or value not in _OVERALL_STATUSES
            ):
                violations.append("status_mismatch")
        if (
            "overall_status" in daily_result
            and "status" in daily_result
            and daily_result.get("overall_status") != daily_result.get("status")
        ):
            violations.append("status_mismatch")
        if completion is not None:
            if raw_overall != completion.get("overall_status"):
                violations.append("status_mismatch")
            if daily_result.get("daily_status") != completion.get("daily_status"):
                violations.append("status_mismatch")
        raw_daily_status = daily_result.get("daily_status")
        if "daily_status" in daily_result and (
            not isinstance(raw_daily_status, str)
            or raw_daily_status not in _DAILY_STATUSES
        ):
            violations.append("status_mismatch")
        raw_phases = daily_result.get("phase_results")
        if isinstance(raw_phases, Mapping):
            raw_daily_phase = raw_phases.get("daily")
            if isinstance(raw_daily_phase, Mapping) and "status" in raw_daily_phase:
                raw_daily_phase_status = raw_daily_phase.get("status")
                if (
                    not isinstance(raw_daily_phase_status, str)
                    or raw_daily_phase_status not in _DAILY_STATUSES
                ):
                    violations.append("status_mismatch")
                elif (
                    isinstance(raw_daily_status, str)
                    and raw_daily_status in _DAILY_STATUSES
                    and raw_daily_phase_status != raw_daily_status
                ):
                    violations.append("status_mismatch")

    if count_invalid or retry_invalid:
        violations.append("count_invariant_violation")
    if counts["daily_target"] is not None and counts["daily_target"] != DAILY_TARGET:
        violations.append("count_invariant_violation")
    if (
        counts["initial_allocation_count"] is not None
        and counts["initial_allocation_count"] != DAILY_TARGET
    ):
        violations.append("count_invariant_violation")
    if (
        counts["committed_count"] is not None
        and counts["committed_count"] > DAILY_TARGET
    ):
        violations.append("count_invariant_violation")
    if all(
        counts[key] is not None
        for key in ("daily_target", "committed_count", "shortfall_count")
    ):
        expected_shortfall = max(0, counts["daily_target"] - counts["committed_count"])
        if counts["shortfall_count"] != expected_shortfall:
            violations.append("count_invariant_violation")

    daily_phase = None
    if daily_result is not None:
        raw_phases = daily_result.get("phase_results")
        if isinstance(raw_phases, Mapping):
            value = raw_phases.get("daily")
            daily_phase = value if isinstance(value, Mapping) else None
    if daily_phase is not None:
        for key in ("committed_count", "shortfall_count", "source_failures"):
            phase_count, phase_invalid = _optional_count(daily_phase, key)
            if phase_invalid:
                violations.append("count_invariant_violation")
            if (
                counts[key] is not None
                and phase_count is not None
                and counts[key] != phase_count
            ):
                violations.append("count_invariant_violation")
        phase_error_count, phase_error_invalid = _optional_count(
            daily_phase, "error_count"
        )
        if phase_error_invalid or (
            phase_error_count is not None
            and counts["error_count"] is not None
            and phase_error_count > counts["error_count"]
        ):
            violations.append("count_invariant_violation")

    if retry_summary["contract_violation_count"] not in (None, 0):
        violations.append("retry_contract_violation")
    if all(retry_summary[key] is not None for key in _RETRY_FIELDS):
        if (
            retry_summary["recovered_count"] > retry_summary["attempt_count"]
            or retry_summary["unresolved_count"] > retry_summary["attempt_count"]
            or retry_summary["recovered_count"] + retry_summary["unresolved_count"]
            > retry_summary["attempt_count"]
        ):
            violations.append("retry_contract_violation")

    raw_completion_status = (
        completion.get("status") if completion is not None else "unknown"
    )
    completion_status = _status(raw_completion_status, _COMPLETION_STATUSES)
    overall_status = (
        daily_result.get("overall_status", daily_result.get("status"))
        if daily_result is not None
        else (completion.get("overall_status") if completion is not None else "unknown")
    )
    daily_status = (
        daily_result.get("daily_status")
        if daily_result is not None
        else (completion.get("daily_status") if completion is not None else "unknown")
    )
    overall_status = _status(
        overall_status,
        frozenset(
            {
                "complete",
                "partial",
                "shortfall",
                "failed",
                "blocked",
                "unknown",
                "not_due",
                "metrics_failed",
                "export_failed",
                "preservation_failed",
            }
        ),
    )
    daily_status = _status(
        daily_status,
        _DAILY_STATUSES,
    )

    if completion is not None:
        if (
            not isinstance(raw_completion_status, str)
            or raw_completion_status not in _COMPLETION_STATUSES
        ):
            violations.append("status_mismatch")
        exit_code, exit_invalid = _optional_count(completion, "exit_code")
        if exit_invalid or exit_code is None:
            violations.append("status_mismatch")
        elif completion_status == "complete":
            if overall_status == "complete" and (
                daily_status != "complete" or exit_code != 0
            ):
                violations.append("status_mismatch")
            elif overall_status in {"partial", "shortfall"} and (
                daily_status not in {"partial", "shortfall"} or exit_code != 5
            ):
                violations.append("status_mismatch")
            elif overall_status == "not_due" and (
                daily_status != "not_due" or exit_code != 0
            ):
                violations.append("status_mismatch")
            elif overall_status not in {"complete", "partial", "shortfall", "not_due"}:
                violations.append("status_mismatch")
        elif completion_status == "blocked":
            if (
                overall_status != "blocked"
                or daily_status != "unknown"
                or exit_code != 4
            ):
                violations.append("status_mismatch")
        elif completion_status == "unknown":
            if (
                overall_status != "unknown"
                or daily_status != "unknown"
                or exit_code == 0
            ):
                violations.append("status_mismatch")
        elif completion_status == "failed":
            if exit_code in {0, 5} or overall_status in {
                "blocked",
                "unknown",
                "complete",
                "partial",
                "shortfall",
                "not_due",
            }:
                violations.append("status_mismatch")
        else:
            violations.append("status_mismatch")

    if daily_status in {"complete", "partial", "shortfall"} and counts[
        "error_count"
    ] not in (None, 0):
        violations.append("status_mismatch")
    if overall_status == "complete" and counts["error_count"] not in (None, 0):
        violations.append("status_mismatch")
    if overall_status == "complete" and all(
        counts[key] is not None
        for key in ("daily_target", "committed_count", "shortfall_count")
    ):
        if counts["committed_count"] != DAILY_TARGET or counts["shortfall_count"] != 0:
            violations.append("count_invariant_violation")
    if overall_status in {"partial", "shortfall"} and all(
        counts[key] is not None
        for key in ("daily_target", "committed_count", "shortfall_count")
    ):
        if counts["committed_count"] >= DAILY_TARGET or counts["shortfall_count"] <= 0:
            violations.append("count_invariant_violation")

    raw_trace_phase = None
    if daily_result is not None:
        raw_phases = daily_result.get("phase_results")
        if isinstance(raw_phases, Mapping):
            candidate_trace = raw_phases.get("trace")
            raw_trace_phase = (
                candidate_trace if isinstance(candidate_trace, Mapping) else None
            )
    if (
        completion is not None
        and raw_trace_phase is not None
        and "status" in raw_trace_phase
        and "trace_status" in completion
        and phase_results["trace"]
        != _status(
            completion.get("trace_status"),
            frozenset({"healthy", "degraded", "unavailable", "not_written"}),
        )
    ):
        violations.append("phase_contract_violation")

    reason_codes = []
    if completion is not None and isinstance(completion.get("reason_code"), str):
        reason_codes.append(completion["reason_code"])
    if daily_result is not None:
        for key in ("reason", "daily_reason"):
            value = daily_result.get(key)
            if isinstance(value, str):
                reason_codes.append(value)
    is_terminal_failure = completion_status in {
        "failed",
        "blocked",
    } or overall_status in {
        "failed",
        "blocked",
        "metrics_failed",
        "export_failed",
        "preservation_failed",
    }
    has_external_stop = is_terminal_failure and any(
        code in _EXTERNAL_STOP_CODES for code in reason_codes
    )

    if "retry_contract_violation" in violations:
        reason_code = "retry_contract_violation"
    elif "identity_mismatch" in violations:
        reason_code = "identity_mismatch"
    elif "status_mismatch" in violations:
        reason_code = "status_mismatch"
    elif "count_invariant_violation" in violations:
        reason_code = "count_invariant_violation"
    elif "phase_contract_violation" in violations:
        reason_code = "phase_contract_violation"
    else:
        reason_code = "daily_observation_incomplete"

    if violations:
        preclassification = "contract_violation"
        check_status = "abnormal"
    elif has_external_stop:
        preclassification = "external_wait"
        check_status = "incomplete"
        reason_code = "external_dependency_wait"
    elif is_terminal_failure:
        preclassification = "needs_investigation"
        check_status = "abnormal"
        reason_code = "daily_failure_observed"
    elif completion_status == "complete" and overall_status == "not_due":
        preclassification = "unknown"
        check_status = "unknown"
        reason_code = "daily_not_due"
    else:
        retry_complete = all(retry_summary[key] is not None for key in _RETRY_FIELDS)
        counts_complete = all(counts[key] is not None for key in _COUNT_FIELDS)
        phases_complete = (
            daily_status in {"complete", "partial", "shortfall"}
            and phase_results["daily"] == daily_status
            and phase_results["metrics"] == "observed"
            and phase_results["export"] == "ready"
            and phase_results["preservation"] == "ready"
            and phase_results["start_backup"] == "ready"
            and phase_results["end_backup"] == "ready"
            and phase_results["monthly_restore"] in {"ready", "already_checked"}
            and phase_results["trace"] == "healthy"
            and trace_event_count is not None
            and trace_event_count > 0
            and trace_count_valid
        )
        run_required = overall_status in {"complete", "partial", "shortfall"}
        if (
            daily_result is None
            or completion is None
            or (run_required and source_run_id is None)
            or not counts_complete
            or not retry_complete
            or not phases_complete
            or evidence["status"] != "complete"
        ):
            preclassification = "unknown"
            check_status = "unknown"
            if daily_result is None:
                reason_code = "daily_result_missing"
            elif evidence["status"] == "invalid":
                reason_code = "manifest_invalid"
            elif evidence["status"] != "complete":
                reason_code = "evidence_incomplete"
            elif not retry_complete:
                reason_code = "retry_observation_missing"
            else:
                reason_code = "daily_observation_incomplete"
        elif overall_status in {"partial", "shortfall"}:
            daily_reason = daily_result.get("daily_reason", daily_result.get("reason"))
            if daily_reason != "candidate_or_theme_exhausted":
                preclassification = "unknown"
                check_status = "unknown"
                reason_code = "daily_observation_incomplete"
            else:
                preclassification = "candidate_shortfall"
                check_status = "normal_shortfall"
                reason_code = "daily_target_unmet"
        elif overall_status == "complete":
            if retry_summary["unresolved_count"] != 0:
                preclassification = "needs_investigation"
                check_status = "abnormal"
                reason_code = "daily_failure_observed"
            elif retry_summary["recovered_count"] > 0:
                preclassification = "recovered_retry"
                check_status = "normal"
                reason_code = "daily_recovered_retry"
            else:
                preclassification = "normal_candidate"
                check_status = "normal"
                reason_code = "daily_complete"
        else:
            preclassification = "unknown"
            check_status = "unknown"
            reason_code = "daily_observation_incomplete"

    if (
        evidence["status"] == "identity_mismatch"
        and "identity_mismatch" not in violations
    ):
        evidence["status"] = "identity_mismatch"
    return {
        "schema_version": CHECK_RESULT_SCHEMA_VERSION,
        "target_date": expected_target_date,
        "checked_at": observed_at,
        "source_launch_id": expected_launch_id,
        "source_run_id": source_run_id,
        "preclassification": preclassification,
        "check_status": check_status,
        "reason_code": reason_code,
        "overall_status": overall_status,
        "daily_status": daily_status,
        "counts": counts,
        "retry_summary": retry_summary,
        "phase_results": phase_results,
        "evidence": evidence,
        "routing": {
            "next_action": "check_only",
            "auto_dispatch": False,
            "steps_started": [],
            "incident_write_allowed": False,
        },
    }
