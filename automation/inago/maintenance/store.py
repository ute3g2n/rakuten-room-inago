"""Transactional maintenance state, lease, handoff, and release-gate store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Iterable, Iterator, Mapping
from zoneinfo import ZoneInfo

from automation.inago.maintenance.safe_file import open_file_beneath
from automation.inago.maintenance.paths import (
    UnsafeMaintenancePathError,
    _has_reparse_point,
    resolve_maintenance_database_path,
)


SCHEMA_VERSION = 15
APPLICATION_ID = 0x494E474D  # INGM
_MIGRATIONS = {
    2: (
        "002_job_manifest_ai_sessions.sql",
        "immutable job manifest and AI session ledger",
    ),
    3: ("003_daily_launch.sql", "daily launcher start and completion ledger"),
    4: (
        "004_watchdog_confirmation_queue.sql",
        "process identity and idempotent watchdog confirmation queue",
    ),
    5: (
        "005_repair_campaigns.sql",
        "pinned repair predecessors and durable repair campaigns",
    ),
    6: (
        "006_deployment_observations.sql",
        "exact tested, pushed, and active deployment commit observations",
    ),
    7: (
        "007_worker_dispatch_queue.sql",
        "idempotent post-launch worker dispatch queue",
    ),
    8: (
        "008_deployment_verification.sql",
        "next-natural-daily verification of deployed maintenance changes",
    ),
    9: (
        "009_worker_queue_events.sql",
        "durable worker holds, daily observations and deployment resume identity",
    ),
    10: (
        "010_historical_job_resume_events.sql",
        "audited resumption of blocked historical maintenance jobs",
    ),
    11: (
        "011_multiple_resumable_jobs.sql",
        "allow blocked jobs to resume into the shared queued state",
    ),
    12: (
        "012_unrestricted_job_start_resume.sql",
        "remove incident and lease gates from explicit job start and resume",
    ),
    13: (
        "013_record_nonpass_regression_outcomes.sql",
        "record non-pass regression and workflow stage outcomes without gates",
    ),
    14: (
        "014_flow_completion_trace.sql",
        "append-only correlated maintenance operation trace",
    ),
    15: (
        "015_repair_predecessor_pin_optional.sql",
        "allow repair continuation when predecessor evidence is unavailable",
    ),
}
# Migration 12 rebuilds maintenance_job, a parent table referenced by several
# existing tables. SQLite keeps deferred-FK counters from DROP TABLE even after
# the replacement table is restored, so this migration checks the final schema
# with foreign_key_check while enforcement is temporarily disabled.
_MIGRATIONS_REQUIRING_FOREIGN_KEY_CHECK = frozenset({12})
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ERROR_CLASS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_DAILY_CHECK_STATUSES = {
    "normal",
    "normal_shortfall",
    "abnormal",
    "unknown",
    "incomplete",
}
_TEST_STATUSES = {
    "PASS",
    "FAIL",
    "SKIP",
    "XFAIL",
    "ERROR",
    "NOT_RUN",
    "UNVERIFIED",
}
_REVIEW_STATUSES = {"approved", "changes_requested", "rejected", "unavailable"}
_DEPLOYMENT_STATUSES = {"pending", "deployed", "failed", "blocked", "rolled_back"}
_STEP_TERMINAL_STATUSES = {"complete", "handoff_accepted"}
_JOB_TERMINAL_STATUSES = {"complete", "failed", "blocked", "cancelled"}
_HANDOFF_KEYS = {
    "schema_version",
    "incident_id",
    "job_id",
    "attempt_id",
    "source_run_id",
    "result_version",
    "reviewed_version",
    "output",
}
_SCHEMA_TABLES = {
    "schema_migration",
    "daily_check",
    "incident",
    "incident_run",
    "release",
    "release_required_test",
    "test_ledger",
    "review_result",
    "deployment",
    "release_gate",
    "maintenance_job",
    "lease",
    "step_attempt",
    "handoff_artifact",
    "job_input_manifest",
    "maintenance_ai_session",
    "maintenance_ai_observation",
    "daily_launch",
    "watchdog_queue",
    "watchdog_cursor",
    "worker_queue",
    "worker_queue_event",
    "daily_result_observation",
    "deployment_verification",
    "repair_campaign",
    "repair_campaign_case",
    "repair_campaign_result",
    "repair_case_run_start",
    "repair_run_receipt",
    "repair_host_review_execution",
    "repair_review_receipt",
    "maintenance_job_resume_event",
    "maintenance_workflow_stage_observation",
    "maintenance_operation_event",
}


def _host_process_identity(pid: int) -> tuple[str, bool] | None:
    """Return a process start identity and liveness, or None when it has exited."""

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise MaintenanceStoreError("repair_runner_process_identity_invalid")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class _FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            0x1000, False, pid
        )  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: no process has this PID.
                return None
            raise MaintenanceStoreError("repair_runner_process_liveness_unknown")
        try:
            created = _FileTime()
            exited = _FileTime()
            kernel = _FileTime()
            user = _FileTime()
            exit_code = wintypes.DWORD()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ) or not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                raise MaintenanceStoreError("repair_runner_process_liveness_unknown")
            created_ticks = (int(created.high) << 32) | int(created.low)
            return f"windows:{created_ticks}", exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    if sys.platform.startswith("linux") and Path("/proc").is_dir():
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            boot_id = (
                Path("/proc/sys/kernel/random/boot_id")
                .read_text(encoding="ascii")
                .strip()
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise MaintenanceStoreError(
                "repair_runner_process_liveness_unknown"
            ) from exc
        closing_paren = stat_text.rfind(")")
        if closing_paren < 0 or not boot_id:
            raise MaintenanceStoreError("repair_runner_process_liveness_unknown")
        fields = stat_text[closing_paren + 1 :].split()
        if len(fields) <= 19:
            raise MaintenanceStoreError("repair_runner_process_liveness_unknown")
        state = fields[0]
        start_ticks = fields[19]
        return f"linux:{boot_id}:{start_ticks}", state not in {"Z", "X"}
    raise MaintenanceStoreError("repair_runner_process_liveness_unknown")


class MaintenanceStoreError(RuntimeError):
    """A safe, non-content-bearing maintenance storage error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class UnknownMaintenanceSchemaError(MaintenanceStoreError):
    """The target database is not this maintenance schema or is too new."""


class IdempotencyConflictError(MaintenanceStoreError):
    """A repeated identity arrived with incompatible source facts."""


class LeaseBusyError(MaintenanceStoreError):
    """A held lease cannot be reclaimed without safe owner confirmation."""


class LeaseOwnershipError(MaintenanceStoreError):
    """A lease token is stale, expired, or belongs to another owner."""


class HandoffContractError(MaintenanceStoreError):
    """A handoff identity, version, review, path, or digest is invalid."""


@dataclass(frozen=True, slots=True)
class DailyCheckResult:
    check_id: str
    created: bool
    incident_id: str | None


@dataclass(frozen=True, slots=True)
class DailyCheckRecord:
    check_id: str
    source_launch_id: str
    source_run_id: str | None
    target_date: str
    check_status: str
    reason_code: str | None
    observed_at: str


@dataclass(frozen=True, slots=True)
class WorkerQueueRecord:
    queue_id: str
    source_launch_id: str
    worker_job_id: str
    target_date: str
    status: str
    check_id: str | None
    claim_owner_id: str | None
    claim_generation: int | None
    claimed_at: str | None
    completed_at: str | None
    reason_code: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class WorkerQueueEventRecord:
    event_id: str
    queue_id: str
    event_type: str
    check_id: str | None
    reason_code: str
    owner_id: str
    generation: int
    occurred_at: str


@dataclass(frozen=True, slots=True)
class DailyResultObservationRecord:
    source_launch_id: str
    target_date: str
    source_run_id: str | None
    schema_version: str
    observation_json: str
    observation_sha256: str
    captured_at: str

    @property
    def observation(self) -> Mapping[str, object]:
        value = json.loads(self.observation_json)
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True, slots=True)
class DailyLaunchRecord:
    source_launch_id: str
    target_date: str
    owner_pid: int
    started_at: str
    status: str
    child_pid: int | None
    source_run_id: str | None
    exit_code: int | None
    completed_at: str | None
    result_json: str | None
    owner_start_time: str | None
    child_start_time: str | None

    @property
    def result_summary(self) -> Mapping[str, object] | None:
        if self.result_json is None:
            return None
        value = json.loads(self.result_json)
        return value if isinstance(value, dict) else None

    @property
    def failure_phase(self) -> str | None:
        summary = self.result_summary
        value = summary.get("failure_phase") if summary is not None else None
        return str(value) if value is not None else None

    @property
    def primary_error_class(self) -> str | None:
        summary = self.result_summary
        value = summary.get("primary_error_class") if summary is not None else None
        return str(value) if value is not None else None

    @property
    def secondary_error_classes(self) -> tuple[str, ...]:
        summary = self.result_summary
        values = (
            summary.get("secondary_error_classes", []) if summary is not None else []
        )
        return tuple(str(value) for value in values) if isinstance(values, list) else ()

    @property
    def trace_status(self) -> str:
        summary = self.result_summary
        value = summary.get("trace_status") if summary is not None else None
        return str(value) if value is not None else "not_written"


@dataclass(frozen=True, slots=True)
class LeaseToken:
    lease_key: str
    owner_id: str
    generation: int


@dataclass(frozen=True, slots=True)
class LeaseRecord:
    lease_key: str
    owner_id: str
    owner_pid: int
    owner_start_time: str
    generation: int
    state: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class WatchdogQueueRecord:
    queue_id: str
    occurrence_key: str
    target_date: str
    source_launch_id: str | None
    status: str
    reason_code: str
    created_at: str
    updated_at: str


def _daily_launch_from_row(row: sqlite3.Row) -> DailyLaunchRecord:
    return DailyLaunchRecord(
        source_launch_id=str(row["source_launch_id"]),
        target_date=str(row["target_date"]),
        owner_pid=int(row["owner_pid"]),
        started_at=str(row["started_at"]),
        status=str(row["status"]),
        child_pid=int(row["child_pid"]) if row["child_pid"] is not None else None,
        source_run_id=(
            str(row["source_run_id"]) if row["source_run_id"] is not None else None
        ),
        exit_code=int(row["exit_code"]) if row["exit_code"] is not None else None,
        completed_at=(
            str(row["completed_at"]) if row["completed_at"] is not None else None
        ),
        result_json=str(row["result_json"]) if row["result_json"] is not None else None,
        owner_start_time=(
            str(row["owner_start_time"])
            if row["owner_start_time"] is not None
            else None
        ),
        child_start_time=(
            str(row["child_start_time"])
            if row["child_start_time"] is not None
            else None
        ),
    )


def _worker_queue_from_row(row: sqlite3.Row) -> WorkerQueueRecord:
    return WorkerQueueRecord(
        queue_id=str(row["queue_id"]),
        source_launch_id=str(row["source_launch_id"]),
        worker_job_id=str(row["worker_job_id"]),
        target_date=str(row["target_date"]),
        status=str(row["status"]),
        check_id=str(row["check_id"]) if row["check_id"] is not None else None,
        claim_owner_id=(
            str(row["claim_owner_id"]) if row["claim_owner_id"] is not None else None
        ),
        claim_generation=(
            int(row["claim_generation"])
            if row["claim_generation"] is not None
            else None
        ),
        claimed_at=str(row["claimed_at"]) if row["claimed_at"] is not None else None,
        completed_at=(
            str(row["completed_at"]) if row["completed_at"] is not None else None
        ),
        reason_code=(
            str(row["reason_code"]) if row["reason_code"] is not None else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _worker_queue_event_from_row(row: sqlite3.Row) -> WorkerQueueEventRecord:
    return WorkerQueueEventRecord(
        event_id=str(row["event_id"]),
        queue_id=str(row["queue_id"]),
        event_type=str(row["event_type"]),
        check_id=str(row["check_id"]) if row["check_id"] is not None else None,
        reason_code=str(row["reason_code"]),
        owner_id=str(row["owner_id"]),
        generation=int(row["generation"]),
        occurred_at=str(row["occurred_at"]),
    )


def _daily_result_observation_from_row(
    row: sqlite3.Row,
) -> DailyResultObservationRecord:
    return DailyResultObservationRecord(
        source_launch_id=str(row["source_launch_id"]),
        target_date=str(row["target_date"]),
        source_run_id=(
            str(row["source_run_id"]) if row["source_run_id"] is not None else None
        ),
        schema_version=str(row["schema_version"]),
        observation_json=str(row["observation_json"]),
        observation_sha256=str(row["observation_sha256"]),
        captured_at=str(row["captured_at"]),
    )


def _validated_daily_result_observation(
    value: Mapping[str, object],
    *,
    source_launch_id: str,
    target_date: str,
    source_run_id: str | None,
) -> tuple[dict[str, object], str, str]:
    """Accept only allowlisted, identity-bound aggregate daily observations."""

    count_fields = (
        "daily_target",
        "initial_allocation_count",
        "supplemental_allocation_count",
        "committed_count",
        "shortfall_count",
        "actual_posted_count",
        "source_failures",
        "error_count",
    )
    retry_fields = (
        "attempt_count",
        "recovered_count",
        "unresolved_count",
        "contract_violation_count",
    )
    root_keys = {
        "schema_version",
        "source_launch_id",
        "source_run_id",
        "target_date",
        "status",
        "overall_status",
        "daily_status",
        "reason",
        "daily_reason",
        *count_fields,
        "retry_summary",
        "phase_results",
    }
    if not isinstance(value, Mapping) or set(value) != root_keys:
        raise MaintenanceStoreError("daily_result_observation_schema_invalid")
    document = dict(value)
    if (
        document["schema_version"] != "daily-result-observation-v1"
        or document["source_launch_id"] != source_launch_id
        or document["target_date"] != target_date
        or document["source_run_id"] != source_run_id
    ):
        raise MaintenanceStoreError("daily_result_observation_identity_invalid")
    for field in ("status", "overall_status", "daily_status"):
        document[field] = _safe_code(
            document[field], "daily_result_observation_value_invalid"
        )
    for field in ("reason", "daily_reason"):
        if document[field] is not None:
            document[field] = _safe_code(
                document[field], "daily_result_observation_value_invalid"
            )
    for field in count_fields:
        item = document[field]
        if item is not None and (
            isinstance(item, bool)
            or not isinstance(item, int)
            or item < 0
            or item > 2**31 - 1
        ):
            raise MaintenanceStoreError("daily_result_observation_count_invalid")
    retry = document["retry_summary"]
    if not isinstance(retry, Mapping) or set(retry) != set(retry_fields):
        raise MaintenanceStoreError("daily_result_observation_retry_invalid")
    retry_document = dict(retry)
    for field in retry_fields:
        item = retry_document[field]
        if item is not None and (
            isinstance(item, bool)
            or not isinstance(item, int)
            or item < 0
            or item > 2**31 - 1
        ):
            raise MaintenanceStoreError("daily_result_observation_retry_invalid")
    document["retry_summary"] = retry_document
    phases = document["phase_results"]
    phase_keys = {
        "daily",
        "metrics",
        "export",
        "preservation",
        "start_backup",
        "trace",
    }
    if not isinstance(phases, Mapping) or set(phases) != phase_keys:
        raise MaintenanceStoreError("daily_result_observation_phases_invalid")
    phase_shapes = {
        "daily": {
            "status",
            "committed_count",
            "shortfall_count",
            "source_failures",
            "error_count",
        },
        "metrics": {"status"},
        "export": {"status"},
        "preservation": {"status", "end_backup_status", "monthly_restore_status"},
        "start_backup": {"status"},
        "trace": {"status", "event_count"},
    }
    phase_document: dict[str, object] = {}
    for phase_name, keys in phase_shapes.items():
        phase = phases[phase_name]
        if not isinstance(phase, Mapping) or set(phase) != keys:
            raise MaintenanceStoreError("daily_result_observation_phases_invalid")
        normalized = dict(phase)
        normalized["status"] = _safe_code(
            normalized["status"], "daily_result_observation_phases_invalid"
        )
        for status_key in ("end_backup_status", "monthly_restore_status"):
            if status_key in normalized:
                normalized[status_key] = _safe_code(
                    normalized[status_key],
                    "daily_result_observation_phases_invalid",
                )
        for count_key in keys & set(count_fields + ("event_count",)):
            item = normalized[count_key]
            if item is not None and (
                isinstance(item, bool)
                or not isinstance(item, int)
                or item < 0
                or item > 2**31 - 1
            ):
                raise MaintenanceStoreError("daily_result_observation_count_invalid")
        phase_document[phase_name] = normalized
    document["phase_results"] = phase_document
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise MaintenanceStoreError("daily_result_observation_too_large")
    return document, encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _watchdog_queue_from_row(row: sqlite3.Row) -> WatchdogQueueRecord:
    return WatchdogQueueRecord(
        queue_id=str(row["queue_id"]),
        occurrence_key=str(row["occurrence_key"]),
        target_date=str(row["target_date"]),
        source_launch_id=(
            str(row["source_launch_id"])
            if row["source_launch_id"] is not None
            else None
        ),
        status=str(row["status"]),
        reason_code=str(row["reason_code"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _deployment_from_row(row: sqlite3.Row) -> DeploymentRecord:
    return DeploymentRecord(
        deployment_id=str(row["deployment_id"]),
        release_id=str(row["release_id"]),
        attempt=int(row["attempt"]),
        result_commit=str(row["result_commit"]),
        config_version=str(row["config_version"]),
        status=str(row["status"]),
        source_commit=(
            str(row["source_commit"]) if row["source_commit"] is not None else None
        ),
        base_commit=(
            str(row["base_commit"]) if row["base_commit"] is not None else None
        ),
        tested_commit=(
            str(row["tested_commit"]) if row["tested_commit"] is not None else None
        ),
        pushed_commit=(
            str(row["pushed_commit"]) if row["pushed_commit"] is not None else None
        ),
        active_commit=(
            str(row["active_commit"]) if row["active_commit"] is not None else None
        ),
        previous_active_commit=(
            str(row["previous_active_commit"])
            if row["previous_active_commit"] is not None
            else None
        ),
        remote_name=(
            str(row["remote_name"]) if row["remote_name"] is not None else None
        ),
        target_branch=(
            str(row["target_branch"]) if row["target_branch"] is not None else None
        ),
        reason_code=(
            str(row["reason_code"]) if row["reason_code"] is not None else None
        ),
        deployed_at=str(row["deployed_at"]),
    )


def _deployment_verification_from_row(
    row: sqlite3.Row,
) -> DeploymentVerificationRecord:
    return DeploymentVerificationRecord(
        verification_id=str(row["verification_id"]),
        deployment_id=str(row["deployment_id"]),
        target_date=str(row["target_date"]),
        status=str(row["status"]),
        source_launch_id=(
            str(row["source_launch_id"])
            if row["source_launch_id"] is not None
            else None
        ),
        check_id=str(row["check_id"]) if row["check_id"] is not None else None,
        observed_commit=(
            str(row["observed_commit"]) if row["observed_commit"] is not None else None
        ),
        evidence_ref=(
            str(row["evidence_ref"]) if row["evidence_ref"] is not None else None
        ),
        recurrence_incident_id=(
            str(row["recurrence_incident_id"])
            if row["recurrence_incident_id"] is not None
            else None
        ),
        reason_code=(
            str(row["reason_code"]) if row["reason_code"] is not None else None
        ),
        observed_at=(
            str(row["observed_at"]) if row["observed_at"] is not None else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


@dataclass(frozen=True, slots=True)
class StepAttemptResult:
    attempt_id: str
    attempt_no: int
    status: str
    reused: bool


@dataclass(frozen=True, slots=True)
class StepAttemptRecord:
    attempt_id: str
    job_id: str
    step_key: str
    sequence: int
    attempt_no: int
    source_run_id: str | None
    status: str
    result_version: str | None
    output_path: str | None
    output_sha256: str | None
    started_at: str
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class MaintenanceJobRecord:
    job_id: str
    incident_id: str
    release_id: str
    source_run_id: str | None
    result_version: str
    resume_count: int
    status: str
    created_at: str
    updated_at: str
    finished_at: str | None
    terminal_reason_code: str | None


@dataclass(frozen=True, slots=True)
class HistoricalJobReference:
    """A run-null historical job candidate joined to its exact check identity."""

    job_id: str
    incident_id: str
    release_id: str
    source_launch_id: str
    source_run_id: None
    target_date: str
    status: str
    created_at: str


@dataclass(frozen=True, slots=True)
class JobResumeRecord:
    resume_event_id: str
    job_id: str
    source_launch_id: str
    resume_count: int
    reason_code: str
    owner_id: str
    generation: int
    resumed_at: str


@dataclass(frozen=True, slots=True)
class RepairRunReceiptRecord:
    receipt_id: str
    campaign_id: str
    attempt_id: str
    run_kind: str
    semantic_case_id: str | None
    invocation_id: str | None
    code_sha256: str
    cases: tuple[tuple[str, str], ...]
    results: Mapping[str, str]
    exit_code: int
    collected: int
    assertion_failures: int
    errors: int
    skips: int
    collection_errors: int
    invocation_parent_id: str | None
    receipt_sha256: str
    consumed: bool


@dataclass(frozen=True, slots=True)
class RepairHostReviewExecutionRecord:
    host_receipt_id: str
    job_id: str
    attempt_id: str
    result_version: str
    author_id: str
    reviewer_id: str
    reviewer_session_key: str
    reviewer_turn_id: str
    reviewer_output_sha256: str
    reviewed_output_sha256: str
    code_sha256: str
    patch_sha256: str
    test_ledger_sha256: str
    approved: bool
    unresolved_critical_findings: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class RepairReviewReceiptRecord:
    receipt_id: str
    attempt_id: str
    job_id: str
    result_version: str
    author_id: str
    reviewer_id: str
    reviewer_role: str
    host_receipt_id: str
    reviewed_output_sha256: str
    code_sha256: str
    patch_sha256: str
    test_ledger_sha256: str
    receipt_sha256: str
    consumed: bool


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    release_id: str
    result_commit: str
    result_version: str
    config_version: str
    state: str


@dataclass(frozen=True, slots=True)
class GateResult:
    release_id: str
    status: str
    reason_codes: tuple[str, ...]
    evaluated_at: str
    result_commit: str


@dataclass(frozen=True, slots=True)
class DeploymentRecord:
    deployment_id: str
    release_id: str
    attempt: int
    result_commit: str
    config_version: str
    status: str
    source_commit: str | None
    base_commit: str | None
    tested_commit: str | None
    pushed_commit: str | None
    active_commit: str | None
    previous_active_commit: str | None
    remote_name: str | None
    target_branch: str | None
    reason_code: str | None
    deployed_at: str


@dataclass(frozen=True, slots=True)
class DeploymentVerificationRecord:
    verification_id: str
    deployment_id: str
    target_date: str
    status: str
    source_launch_id: str | None
    check_id: str | None
    observed_commit: str | None
    evidence_ref: str | None
    recurrence_incident_id: str | None
    reason_code: str | None
    observed_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class JobStartResult:
    started: bool
    job_id: str | None
    gate_status: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JobInputManifest:
    job_id: str
    incident_id: str
    source_launch_id: str
    source_run_id: str | None
    target_date: str
    manifest_sha256: str
    created_at: str

    def identity(self) -> dict[str, object]:
        return {
            "incident_id": self.incident_id,
            "source_launch_id": self.source_launch_id,
            "source_run_id": self.source_run_id,
            "target_date": self.target_date,
            "job_id": self.job_id,
        }


@dataclass(frozen=True, slots=True)
class AIParentSessionRecord:
    job_id: str
    session_key: str
    parent_key: str
    role_name: str
    thread_id: str
    manifest_sha256: str
    requested_model: str
    requested_effort: str
    requested_service_tier: str
    requested_fast_mode: bool
    role_config_sha256: str
    sandbox: Mapping[str, object]
    created_at: str


@dataclass(frozen=True, slots=True)
class JobFinalizeResult:
    job_id: str
    status: str
    finalized_at: str
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class HandoffResult:
    accepted: bool
    attempt_id: str
    job_id: str
    incident_id: str
    source_run_id: str | None
    result_version: str
    output_path: str
    output_sha256: str


def _safe_id(value: object, code: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise MaintenanceStoreError(code)
    return value


def _safe_code(value: object, code: str) -> str:
    if not isinstance(value, str) or not _CODE_PATTERN.fullmatch(value):
        raise MaintenanceStoreError(code)
    return value


def _timestamp(value: datetime | str, code: str = "timestamp_invalid") -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MaintenanceStoreError(code) from exc
    else:
        raise MaintenanceStoreError(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MaintenanceStoreError(code)
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _target_date(value: object) -> str:
    if not isinstance(value, str):
        raise MaintenanceStoreError("target_date_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise MaintenanceStoreError("target_date_invalid") from exc
    if parsed.isoformat() != value:
        raise MaintenanceStoreError("target_date_invalid")
    return value


def _relative_output_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise HandoffContractError("handoff_output_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HandoffContractError("handoff_output_path_invalid")
    return path.as_posix()


def _read_handoff_artifact(
    root: Path, relative_path: str, expected_sha256: str, code: str
) -> bytes:
    """Read one regular handoff file under root with a pinned parent handle."""

    try:
        with open_file_beneath(root, relative_path, "rb") as stream:
            content = stream.read()
    except (OSError, RuntimeError, ValueError, UnsafeMaintenancePathError) as exc:
        raise HandoffContractError(f"{code}_unavailable") from exc
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise HandoffContractError(f"{code}_digest_mismatch")
    return content


class MaintenanceStore:
    """Owns the isolated ``data_root/maintenance/state.sqlite`` database."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        daily_database_path: str | Path | None = None,
        review_round_limit: int | None = None,
    ) -> None:
        # Deprecated compatibility parameter; review cycles have no cap.
        _ = review_round_limit
        if daily_database_path is None and data_root is not None:
            daily_database_path = Path(data_root).expanduser() / "state.sqlite"
        self.database_path = resolve_maintenance_database_path(
            data_root,
            daily_database_path=daily_database_path,
        )
        # Store-only capabilities gate host evidence and successful deployment
        # transitions; caller-supplied evidence cannot authorize those writes.
        self._repair_runner_capability = object()
        self._repair_review_capability = object()
        self._deployment_capability = object()

    def initialize(self, *, applied_at: datetime | str | None = None) -> int:
        """Create the maintenance database or verify its known schema version."""

        if self.database_path.exists() and not self.database_path.is_file():
            raise UnsafeMaintenancePathError("maintenance_database_not_file")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if self.database_path.is_symlink():
            raise UnsafeMaintenancePathError("maintenance_database_reparse_point")
        connection = sqlite3.connect(
            self.database_path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            application_id = int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            )
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if application_id not in {0, APPLICATION_ID}:
                raise UnknownMaintenanceSchemaError("database_application_id_unknown")
            if version < 0 or version > SCHEMA_VERSION:
                raise UnknownMaintenanceSchemaError("database_schema_is_newer")
            if version > 0 and application_id != APPLICATION_ID:
                raise UnknownMaintenanceSchemaError("database_application_id_missing")
            if version == 0:
                existing = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if existing:
                    raise UnknownMaintenanceSchemaError("database_schema_unrecognized")
            connection.execute("PRAGMA journal_mode = WAL")
            if version == 0:
                migration_text = (
                    Path(__file__).resolve().parent / "sql" / "001_initial.sql"
                ).read_text(encoding="utf-8")
                connection.execute("BEGIN IMMEDIATE")
                statement = ""
                for line in migration_text.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                if statement.strip():
                    raise UnknownMaintenanceSchemaError("migration_incomplete")
                connection.execute(
                    "INSERT INTO schema_migration (version, applied_at, description) "
                    "VALUES (?, ?, ?)",
                    (
                        1,
                        _timestamp(applied_at or datetime.now(timezone.utc)),
                        "initial isolated maintenance state",
                    ),
                )
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute("PRAGMA user_version = 1")
                connection.commit()
                version = 1
            elif application_id != APPLICATION_ID:
                raise UnknownMaintenanceSchemaError("database_application_id_missing")
            while version < SCHEMA_VERSION:
                migration_version = version + 1
                migration = _MIGRATIONS.get(migration_version)
                if migration is None:
                    raise UnknownMaintenanceSchemaError("migration_missing")
                migration_filename, migration_description = migration
                migration_path = (
                    Path(__file__).resolve().parent / "sql" / migration_filename
                )
                if not migration_path.is_file():
                    raise UnknownMaintenanceSchemaError("migration_missing")
                migration_text = migration_path.read_text(encoding="utf-8")
                check_foreign_keys = (
                    migration_version in _MIGRATIONS_REQUIRING_FOREIGN_KEY_CHECK
                )
                if check_foreign_keys:
                    connection.execute("PRAGMA foreign_keys = OFF")
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    statement = ""
                    for line in migration_text.splitlines(keepends=True):
                        statement += line
                        if sqlite3.complete_statement(statement):
                            connection.execute(statement)
                            statement = ""
                    if statement.strip():
                        raise UnknownMaintenanceSchemaError("migration_incomplete")
                    if check_foreign_keys:
                        integrity = connection.execute("PRAGMA quick_check").fetchone()[
                            0
                        ]
                        if integrity != "ok":
                            raise UnknownMaintenanceSchemaError(
                                "database_integrity_failed"
                            )
                        if connection.execute("PRAGMA foreign_key_check").fetchone():
                            raise UnknownMaintenanceSchemaError(
                                "migration_foreign_key_check_failed"
                            )
                    connection.execute(
                        "INSERT INTO schema_migration (version, applied_at, description) "
                        "VALUES (?, ?, ?)",
                        (
                            migration_version,
                            _timestamp(applied_at or datetime.now(timezone.utc)),
                            migration_description,
                        ),
                    )
                    connection.execute(f"PRAGMA user_version = {migration_version}")
                    connection.commit()
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
                finally:
                    if check_foreign_keys:
                        connection.execute("PRAGMA foreign_keys = ON")
                version = migration_version
            self._assert_schema(connection)
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise UnknownMaintenanceSchemaError("database_integrity_failed")
            return SCHEMA_VERSION
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        if not self.database_path.is_file() or self.database_path.is_symlink():
            raise MaintenanceStoreError("maintenance_database_unavailable")
        try:
            uri = f"{self.database_path.resolve(strict=True).as_uri()}?mode=rw"
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=5.0,
                isolation_level=None,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise MaintenanceStoreError("maintenance_database_unavailable") from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.create_function("maintenance_repair_runner_authorized", 0, lambda: 0)
        connection.create_function("maintenance_repair_review_authorized", 0, lambda: 0)
        connection.create_function("maintenance_deployment_authorized", 0, lambda: 0)
        self._assert_schema(connection)
        return connection

    @staticmethod
    def _assert_schema(connection: sqlite3.Connection) -> None:
        try:
            application_id = int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            )
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            migration = connection.execute(
                "SELECT version FROM schema_migration WHERE version = ?",
                (SCHEMA_VERSION,),
            ).fetchone()
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
        except sqlite3.Error as exc:
            raise UnknownMaintenanceSchemaError("database_schema_unrecognized") from exc
        if (
            application_id != APPLICATION_ID
            or version != SCHEMA_VERSION
            or migration is None
            or tables != _SCHEMA_TABLES
        ):
            raise UnknownMaintenanceSchemaError("database_schema_unrecognized")

    @contextmanager
    def _transaction(
        self,
        *,
        repair_runner_capability: object | None = None,
        repair_review_capability: object | None = None,
        deployment_capability: object | None = None,
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if repair_runner_capability is not None:
                if repair_runner_capability is not self._repair_runner_capability:
                    raise MaintenanceStoreError("repair_runner_capability_invalid")
                connection.create_function(
                    "maintenance_repair_runner_authorized", 0, lambda: 1
                )
            if repair_review_capability is not None:
                if repair_review_capability is not self._repair_review_capability:
                    raise MaintenanceStoreError("repair_review_capability_invalid")
                connection.create_function(
                    "maintenance_repair_review_authorized", 0, lambda: 1
                )
            if deployment_capability is not None:
                if deployment_capability is not self._deployment_capability:
                    raise MaintenanceStoreError("deployment_capability_invalid")
                connection.create_function(
                    "maintenance_deployment_authorized", 0, lambda: 1
                )
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def counts(self) -> dict[str, int]:
        connection = self._connect()
        try:
            names = (
                "daily_check",
                "incident",
                "incident_run",
                "maintenance_job",
                "step_attempt",
                "review_result",
                "test_ledger",
                "deployment",
                "release_gate",
                "lease",
                "job_input_manifest",
                "maintenance_ai_session",
                "maintenance_ai_observation",
                "daily_launch",
                "watchdog_queue",
                "watchdog_cursor",
                "worker_queue",
                "worker_queue_event",
                "deployment_verification",
            )
            return {
                name: int(
                    connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                )
                for name in names
            }
        finally:
            connection.close()

    @staticmethod
    def _manifest_payload(
        *,
        job_id: str,
        incident_id: str,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
    ) -> str:
        return json.dumps(
            {
                "schema_version": "maintenance-job-input-v1",
                "incident_id": incident_id,
                "source_launch_id": source_launch_id,
                "source_run_id": source_run_id,
                "target_date": target_date,
                "job_id": job_id,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def _manifest_digest(cls, **identity: object) -> str:
        payload = cls._manifest_payload(**identity)  # type: ignore[arg-type]
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_manifest_binding(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        incident_id: str,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
    ) -> None:
        rows = connection.execute(
            "SELECT j.incident_id AS job_incident_id, "
            "j.source_run_id AS job_source_run_id, "
            "ir.incident_id AS run_incident_id, "
            "ir.source_launch_id AS run_source_launch_id, "
            "ir.source_run_id AS run_source_run_id, "
            "dc.source_launch_id AS check_source_launch_id, "
            "dc.source_run_id AS check_source_run_id, "
            "dc.target_date AS check_target_date "
            "FROM maintenance_job AS j "
            "JOIN incident_run AS ir ON ir.incident_id = j.incident_id "
            "JOIN daily_check AS dc ON dc.check_id = ir.check_id "
            "WHERE j.job_id = ? AND j.incident_id = ? "
            "AND ir.incident_id = ? AND ir.source_launch_id = ?",
            (job_id, incident_id, incident_id, source_launch_id),
        ).fetchall()
        if not rows:
            job_exists = connection.execute(
                "SELECT 1 FROM maintenance_job WHERE job_id = ?", (job_id,)
            ).fetchone()
            if job_exists is None:
                raise MaintenanceStoreError("job_input_manifest_job_missing")
            raise MaintenanceStoreError("job_input_manifest_binding_mismatch")
        if len(rows) != 1:
            raise MaintenanceStoreError("job_input_manifest_binding_ambiguous")
        row = rows[0]
        if (
            row["job_incident_id"] != incident_id
            or row["run_incident_id"] != incident_id
            or row["run_source_launch_id"] != source_launch_id
            or row["check_source_launch_id"] != source_launch_id
            or row["job_source_run_id"] != source_run_id
            or row["run_source_run_id"] != source_run_id
            or row["check_source_run_id"] != source_run_id
            or row["check_target_date"] != target_date
        ):
            raise MaintenanceStoreError("job_input_manifest_binding_mismatch")

    def bind_job_input_manifest(
        self,
        *,
        incident_id: str,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
        job_id: str,
        created_at: datetime | str,
    ) -> JobInputManifest:
        """Bind the exact daily-check identity to one job, without inference."""

        identity = {
            "incident_id": _safe_id(incident_id, "incident_id_invalid"),
            "source_launch_id": _safe_id(source_launch_id, "source_launch_id_invalid"),
            "source_run_id": (
                None
                if source_run_id is None
                else _safe_id(source_run_id, "source_run_id_invalid")
            ),
            "target_date": _target_date(target_date),
            "job_id": _safe_id(job_id, "job_id_invalid"),
        }
        when = _timestamp(created_at)
        digest = self._manifest_digest(**identity)
        with self._transaction() as connection:
            self._validate_manifest_binding(connection, **identity)
            existing = connection.execute(
                "SELECT incident_id, source_launch_id, source_run_id, target_date, "
                "manifest_sha256, created_at FROM job_input_manifest WHERE job_id = ?",
                (identity["job_id"],),
            ).fetchone()
            if existing is not None:
                existing_identity = {
                    key: existing[key] for key in identity if key != "job_id"
                }
                if (
                    existing_identity
                    != {key: identity[key] for key in identity if key != "job_id"}
                    or existing["manifest_sha256"] != digest
                ):
                    raise IdempotencyConflictError("job_input_manifest_conflict")
                return JobInputManifest(
                    job_id=str(identity["job_id"]),
                    incident_id=str(existing["incident_id"]),
                    source_launch_id=str(existing["source_launch_id"]),
                    source_run_id=existing["source_run_id"],
                    target_date=str(existing["target_date"]),
                    manifest_sha256=str(existing["manifest_sha256"]),
                    created_at=str(existing["created_at"]),
                )
            connection.execute(
                "INSERT INTO job_input_manifest "
                "(job_id, incident_id, source_launch_id, source_run_id, target_date, "
                "manifest_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    identity["job_id"],
                    identity["incident_id"],
                    identity["source_launch_id"],
                    identity["source_run_id"],
                    identity["target_date"],
                    digest,
                    when,
                ),
            )
        return JobInputManifest(
            job_id=str(identity["job_id"]),
            incident_id=str(identity["incident_id"]),
            source_launch_id=str(identity["source_launch_id"]),
            source_run_id=identity["source_run_id"],
            target_date=str(identity["target_date"]),
            manifest_sha256=digest,
            created_at=when,
        )

    def get_job_input_manifest(
        self,
        *,
        job_id: str,
        expected_identity: Mapping[str, object] | None = None,
    ) -> JobInputManifest:
        job = _safe_id(job_id, "job_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT job_id, incident_id, source_launch_id, source_run_id, "
                "target_date, manifest_sha256, created_at "
                "FROM job_input_manifest WHERE job_id = ?",
                (job,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("job_input_manifest_missing")
            manifest = JobInputManifest(
                job_id=str(row["job_id"]),
                incident_id=str(row["incident_id"]),
                source_launch_id=str(row["source_launch_id"]),
                source_run_id=row["source_run_id"],
                target_date=str(row["target_date"]),
                manifest_sha256=str(row["manifest_sha256"]),
                created_at=str(row["created_at"]),
            )
            expected_digest = self._manifest_digest(**manifest.identity())
            if manifest.manifest_sha256 != expected_digest:
                raise MaintenanceStoreError("job_input_manifest_digest_invalid")
            self._validate_manifest_binding(connection, **manifest.identity())
            if expected_identity is not None:
                required_fields = {
                    "incident_id",
                    "source_launch_id",
                    "source_run_id",
                    "target_date",
                    "job_id",
                }
                if set(expected_identity) != required_fields:
                    raise MaintenanceStoreError("job_input_manifest_identity_invalid")
                if dict(expected_identity) != manifest.identity():
                    raise MaintenanceStoreError("job_input_manifest_identity_mismatch")
            return manifest
        finally:
            connection.close()

    def list_workflow_jobs_for_launch(
        self,
        *,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
        job_id: str | None = None,
    ) -> tuple[MaintenanceJobRecord, ...]:
        """Load jobs already bound to one exact daily-launch identity."""

        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        run_id = (
            None
            if source_run_id is None
            else _safe_id(source_run_id, "source_run_id_invalid")
        )
        target = _target_date(target_date)
        selected_job_id = (
            _safe_id(job_id, "job_id_invalid") if job_id is not None else None
        )
        filter_clause = "AND j.job_id = ?" if selected_job_id is not None else ""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT j.*, ir.source_launch_id AS incident_source_launch_id, "
                "ir.source_run_id AS incident_source_run_id, "
                "dc.source_launch_id AS check_source_launch_id, "
                "dc.source_run_id AS check_source_run_id, "
                "dc.target_date AS check_target_date, "
                "m.source_launch_id AS manifest_source_launch_id, "
                "m.source_run_id AS manifest_source_run_id, "
                "m.target_date AS manifest_target_date "
                "FROM maintenance_job AS j "
                "JOIN incident_run AS ir ON ir.incident_id = j.incident_id "
                "JOIN daily_check AS dc ON dc.check_id = ir.check_id "
                "LEFT JOIN job_input_manifest AS m ON m.job_id = j.job_id "
                f"WHERE ir.source_launch_id = ? {filter_clause} "
                "ORDER BY j.created_at, j.job_id",
                (
                    (launch_id, selected_job_id)
                    if selected_job_id is not None
                    else (launch_id,)
                ),
            ).fetchall()
            jobs: list[MaintenanceJobRecord] = []
            for row in rows:
                if (
                    row["incident_source_launch_id"] != launch_id
                    or row["check_source_launch_id"] != launch_id
                    or row["incident_source_run_id"] != run_id
                    or row["check_source_run_id"] != run_id
                    or row["source_run_id"] != run_id
                    or row["check_target_date"] != target
                ):
                    raise MaintenanceStoreError(
                        "maintenance_workflow_job_identity_mismatch"
                    )
                if row["manifest_source_launch_id"] is not None and (
                    row["manifest_source_launch_id"] != launch_id
                    or row["manifest_source_run_id"] != run_id
                    or row["manifest_target_date"] != target
                ):
                    raise MaintenanceStoreError(
                        "maintenance_workflow_manifest_identity_mismatch"
                    )
                jobs.append(
                    MaintenanceJobRecord(
                        job_id=str(row["job_id"]),
                        incident_id=str(row["incident_id"]),
                        release_id=str(row["release_id"]),
                        source_run_id=(
                            str(row["source_run_id"])
                            if row["source_run_id"] is not None
                            else None
                        ),
                        result_version=str(row["result_version"]),
                        resume_count=int(row["resume_count"]),
                        status=str(row["status"]),
                        created_at=str(row["created_at"]),
                        updated_at=str(row["updated_at"]),
                        finished_at=(
                            str(row["finished_at"])
                            if row["finished_at"] is not None
                            else None
                        ),
                        terminal_reason_code=(
                            str(row["terminal_reason_code"])
                            if row["terminal_reason_code"] is not None
                            else None
                        ),
                    )
                )
            return tuple(jobs)
        finally:
            connection.close()

    def list_historical_job_references(
        self,
        *,
        source_launch_id: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
        exclude_target_dates: Iterable[str] | None = None,
        include_blocked: bool = False,
    ) -> tuple[HistoricalJobReference, ...]:
        """List historical jobs without manufacturing a daily launch."""

        if not isinstance(include_blocked, bool):
            raise MaintenanceStoreError("historical_job_include_blocked_invalid")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise MaintenanceStoreError("historical_job_limit_invalid")
        excluded_dates = (
            tuple(dict.fromkeys(_target_date(item) for item in exclude_target_dates))
            if exclude_target_dates is not None
            else ()
        )
        launch_id = None
        if source_launch_id is not None:
            launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
            if not launch_id.startswith("historical-"):
                return ()
        selected_job_id = (
            _safe_id(job_id, "job_id_invalid") if job_id is not None else None
        )
        filters = [
            "i.component = 'daily_pipeline'",
            "i.event_code = 'historical_daily_failure'",
            (
                "j.status IN ('queued', 'running', 'blocked')"
                if include_blocked
                else "j.status IN ('queued', 'running')"
            ),
            "j.source_run_id IS NULL",
            "ir.source_run_id IS NULL",
            "dc.source_run_id IS NULL",
            "dc.check_status = 'abnormal'",
            "ir.source_launch_id LIKE 'historical-%'",
        ]
        parameters: list[object] = []
        if launch_id is not None:
            filters.append("ir.source_launch_id = ?")
            parameters.append(launch_id)
        if selected_job_id is not None:
            filters.append("j.job_id = ?")
            parameters.append(selected_job_id)
        if excluded_dates:
            placeholders = ", ".join("?" for _ in excluded_dates)
            filters.append(f"dc.target_date NOT IN ({placeholders})")
            parameters.extend(excluded_dates)
        parameters.append(limit)
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT DISTINCT j.job_id, j.incident_id, j.release_id, j.status, "
                "j.created_at, ir.source_launch_id, dc.target_date "
                "FROM maintenance_job AS j "
                "JOIN incident AS i ON i.incident_id = j.incident_id "
                "JOIN incident_run AS ir ON ir.incident_id = i.incident_id "
                "JOIN daily_check AS dc ON dc.check_id = ir.check_id "
                f"WHERE {' AND '.join(filters)} "
                "ORDER BY j.created_at, j.job_id LIMIT ?",
                parameters,
            ).fetchall()
            return tuple(
                HistoricalJobReference(
                    job_id=str(row["job_id"]),
                    incident_id=str(row["incident_id"]),
                    release_id=str(row["release_id"]),
                    source_launch_id=str(row["source_launch_id"]),
                    source_run_id=None,
                    target_date=str(row["target_date"]),
                    status=str(row["status"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            )
        finally:
            connection.close()

    def resume_maintenance_job(
        self,
        *,
        job_id: str,
        source_launch_id: str,
        lease: LeaseToken | None = None,
        resumed_at: datetime | str,
    ) -> JobResumeRecord:
        """Requeue an unfinished job without lease or review eligibility gates."""

        _ = lease
        job = _safe_id(job_id, "job_id_invalid")
        launch = _safe_id(source_launch_id, "source_launch_id_invalid")
        when = _timestamp(resumed_at)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT j.job_id, j.incident_id, j.release_id, j.source_run_id, "
                "j.status, j.resume_count, j.lease_key, j.lease_generation, "
                "j.finished_at, j.updated_at, j.terminal_reason_code, "
                "i.status AS incident_status, "
                "ir.source_launch_id AS incident_source_launch_id, "
                "m.source_launch_id AS manifest_source_launch_id "
                "FROM maintenance_job AS j "
                "JOIN incident AS i ON i.incident_id = j.incident_id "
                "JOIN incident_run AS ir ON ir.incident_id = i.incident_id "
                "JOIN job_input_manifest AS m ON m.job_id = j.job_id "
                "WHERE j.job_id = ? AND m.source_launch_id = ? "
                "AND ir.source_launch_id = m.source_launch_id "
                "AND ir.source_run_id IS m.source_run_id "
                "AND m.source_run_id IS j.source_run_id",
                (job, launch),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("maintenance_job_not_found")

            if row["status"] == "queued":
                existing = connection.execute(
                    "SELECT resume_event_id, job_id, source_launch_id, resume_count, "
                    "reason_code, owner_id, generation, resumed_at "
                    "FROM maintenance_job_resume_event WHERE job_id = ? "
                    "ORDER BY resume_count DESC LIMIT 1",
                    (job,),
                ).fetchone()
                if existing is not None:
                    return JobResumeRecord(
                        resume_event_id=str(existing["resume_event_id"]),
                        job_id=str(existing["job_id"]),
                        source_launch_id=str(existing["source_launch_id"]),
                        resume_count=int(existing["resume_count"]),
                        reason_code=str(existing["reason_code"]),
                        owner_id=str(existing["owner_id"]),
                        generation=int(existing["generation"]),
                        resumed_at=str(existing["resumed_at"]),
                    )
                return JobResumeRecord(
                    resume_event_id="",
                    job_id=str(row["job_id"]),
                    source_launch_id=launch,
                    resume_count=int(row["resume_count"]),
                    reason_code="already_queued",
                    owner_id="resume-cli",
                    generation=1,
                    resumed_at=when,
                )
            reason_value = row["terminal_reason_code"]
            reason = (
                reason_value
                if isinstance(reason_value, str)
                and _CODE_PATTERN.fullmatch(reason_value)
                else "unspecified"
            )

            resume_count = int(row["resume_count"]) + 1
            event_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO maintenance_job_resume_event "
                "(resume_event_id, job_id, source_launch_id, resume_count, reason_code, "
                "owner_id, generation, resumed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    job,
                    launch,
                    resume_count,
                    reason,
                    "resume-cli",
                    resume_count,
                    when,
                ),
            )
            connection.execute(
                "UPDATE maintenance_job SET status = 'queued', lease_key = NULL, "
                "lease_generation = NULL, resume_count = ?, finished_at = NULL, "
                "terminal_reason_code = NULL, updated_at = ? WHERE job_id = ?",
                (
                    resume_count,
                    when,
                    job,
                ),
            )
            if row["incident_status"] != "in_progress":
                connection.execute(
                    "UPDATE incident SET status = 'in_progress', updated_at = ? "
                    "WHERE incident_id = ?",
                    (when, row["incident_id"]),
                )
            return JobResumeRecord(
                resume_event_id=event_id,
                job_id=job,
                source_launch_id=launch,
                resume_count=resume_count,
                reason_code=reason,
                owner_id="resume-cli",
                generation=1,
                resumed_at=when,
            )

    def list_job_resume_events(self, *, job_id: str) -> tuple[JobResumeRecord, ...]:
        job = _safe_id(job_id, "job_id_invalid")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT resume_event_id, job_id, source_launch_id, resume_count, "
                "reason_code, owner_id, generation, resumed_at "
                "FROM maintenance_job_resume_event WHERE job_id = ? "
                "ORDER BY resume_count",
                (job,),
            ).fetchall()
            return tuple(
                JobResumeRecord(
                    resume_event_id=str(row["resume_event_id"]),
                    job_id=str(row["job_id"]),
                    source_launch_id=str(row["source_launch_id"]),
                    resume_count=int(row["resume_count"]),
                    reason_code=str(row["reason_code"]),
                    owner_id=str(row["owner_id"]),
                    generation=int(row["generation"]),
                    resumed_at=str(row["resumed_at"]),
                )
                for row in rows
            )
        finally:
            connection.close()

    def list_step_attempts(self, *, job_id: str) -> tuple[StepAttemptRecord, ...]:
        """Return all durable attempts for a job in stage/attempt order."""

        job = _safe_id(job_id, "job_id_invalid")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM step_attempt WHERE job_id = ? "
                "ORDER BY sequence, attempt_no",
                (job,),
            ).fetchall()
            return tuple(
                StepAttemptRecord(
                    attempt_id=str(row["attempt_id"]),
                    job_id=str(row["job_id"]),
                    step_key=str(row["step_key"]),
                    sequence=int(row["sequence"]),
                    attempt_no=int(row["attempt_no"]),
                    source_run_id=(
                        str(row["source_run_id"])
                        if row["source_run_id"] is not None
                        else None
                    ),
                    status=str(row["status"]),
                    result_version=(
                        str(row["result_version"])
                        if row["result_version"] is not None
                        else None
                    ),
                    output_path=(
                        str(row["output_path"])
                        if row["output_path"] is not None
                        else None
                    ),
                    output_sha256=(
                        str(row["output_sha256"])
                        if row["output_sha256"] is not None
                        else None
                    ),
                    started_at=str(row["started_at"]),
                    finished_at=(
                        str(row["finished_at"])
                        if row["finished_at"] is not None
                        else None
                    ),
                )
                for row in rows
            )
        finally:
            connection.close()

    def bind_ai_parent_session(
        self,
        *,
        job_id: str,
        session_key: str,
        parent_key: str,
        role_name: str,
        thread_id: str,
        manifest_sha256: str,
        requested_model: str,
        requested_effort: str,
        requested_service_tier: str,
        requested_fast_mode: bool,
        role_config_sha256: str,
        sandbox: Mapping[str, object],
        created_at: datetime | str,
    ) -> AIParentSessionRecord:
        job = _safe_id(job_id, "job_id_invalid")
        key = _safe_id(session_key, "ai_session_key_invalid")
        role = _safe_id(role_name, "ai_role_name_invalid")
        thread = _safe_id(thread_id, "ai_thread_id_invalid")
        if parent_key not in {"A", "B"}:
            raise MaintenanceStoreError("ai_parent_key_invalid")
        if not isinstance(manifest_sha256, str) or not _SHA256_PATTERN.fullmatch(
            manifest_sha256
        ):
            raise MaintenanceStoreError("ai_manifest_digest_invalid")
        for value, code in (
            (requested_model, "ai_model_invalid"),
            (requested_effort, "ai_effort_invalid"),
        ):
            _safe_code(value, code)
        if requested_service_tier not in {"default", "priority"}:
            raise MaintenanceStoreError("ai_service_tier_invalid")
        if type(requested_fast_mode) is not bool:
            raise MaintenanceStoreError("ai_fast_mode_invalid")
        if not isinstance(role_config_sha256, str) or not _SHA256_PATTERN.fullmatch(
            role_config_sha256
        ):
            raise MaintenanceStoreError("ai_role_config_digest_invalid")
        if not isinstance(sandbox, Mapping):
            raise MaintenanceStoreError("ai_sandbox_invalid")
        sandbox_value = dict(sandbox)
        if sandbox_value.get("type") not in {"readOnly", "workspaceWrite"}:
            raise MaintenanceStoreError("ai_sandbox_invalid")
        try:
            sandbox_json = json.dumps(
                sandbox_value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise MaintenanceStoreError("ai_sandbox_invalid") from exc
        when = _timestamp(created_at)
        with self._transaction() as connection:
            manifest = connection.execute(
                "SELECT manifest_sha256 FROM job_input_manifest WHERE job_id = ?",
                (job,),
            ).fetchone()
            if manifest is None:
                raise MaintenanceStoreError("job_input_manifest_missing")
            if manifest["manifest_sha256"] != manifest_sha256:
                raise MaintenanceStoreError("job_input_manifest_digest_mismatch")
            existing = connection.execute(
                "SELECT * FROM maintenance_ai_session "
                "WHERE job_id = ? AND session_key = ?",
                (job, key),
            ).fetchone()
            if existing is not None:
                record = self._ai_parent_session_from_row(existing)
                requested = (
                    record.parent_key,
                    record.role_name,
                    record.thread_id,
                    record.manifest_sha256,
                    record.requested_model,
                    record.requested_effort,
                    record.requested_service_tier,
                    record.requested_fast_mode,
                    record.role_config_sha256,
                    json.dumps(
                        record.sandbox,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
                incoming = (
                    parent_key,
                    role,
                    thread,
                    manifest_sha256,
                    requested_model,
                    requested_effort,
                    requested_service_tier,
                    requested_fast_mode,
                    role_config_sha256,
                    sandbox_json,
                )
                if requested != incoming:
                    raise IdempotencyConflictError("ai_session_identity_conflict")
                return record
            connection.execute(
                "INSERT INTO maintenance_ai_session "
                "(job_id, session_key, parent_key, role_name, thread_id, "
                "manifest_sha256, requested_model, requested_effort, "
                "requested_service_tier, requested_fast_mode, role_config_sha256, "
                "sandbox_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job,
                    key,
                    parent_key,
                    role,
                    thread,
                    manifest_sha256,
                    requested_model,
                    requested_effort,
                    requested_service_tier,
                    int(requested_fast_mode),
                    role_config_sha256,
                    sandbox_json,
                    when,
                ),
            )
        return AIParentSessionRecord(
            job_id=job,
            session_key=key,
            parent_key=parent_key,
            role_name=role,
            thread_id=thread,
            manifest_sha256=manifest_sha256,
            requested_model=requested_model,
            requested_effort=requested_effort,
            requested_service_tier=requested_service_tier,
            requested_fast_mode=requested_fast_mode,
            role_config_sha256=role_config_sha256,
            sandbox=sandbox_value,
            created_at=when,
        )

    @staticmethod
    def _ai_parent_session_from_row(row: sqlite3.Row) -> AIParentSessionRecord:
        try:
            sandbox = json.loads(row["sandbox_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise MaintenanceStoreError("ai_sandbox_invalid") from exc
        if not isinstance(sandbox, dict):
            raise MaintenanceStoreError("ai_sandbox_invalid")
        return AIParentSessionRecord(
            job_id=str(row["job_id"]),
            session_key=str(row["session_key"]),
            parent_key=str(row["parent_key"]),
            role_name=str(row["role_name"]),
            thread_id=str(row["thread_id"]),
            manifest_sha256=str(row["manifest_sha256"]),
            requested_model=str(row["requested_model"]),
            requested_effort=str(row["requested_effort"]),
            requested_service_tier=str(row["requested_service_tier"]),
            requested_fast_mode=bool(row["requested_fast_mode"]),
            role_config_sha256=str(row["role_config_sha256"]),
            sandbox=sandbox,
            created_at=str(row["created_at"]),
        )

    def get_ai_parent_session(
        self, *, job_id: str, session_key: str
    ) -> AIParentSessionRecord | None:
        job = _safe_id(job_id, "job_id_invalid")
        key = _safe_id(session_key, "ai_session_key_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM maintenance_ai_session "
                "WHERE job_id = ? AND session_key = ?",
                (job, key),
            ).fetchone()
            return None if row is None else self._ai_parent_session_from_row(row)
        finally:
            connection.close()

    def resolve_repair_identity_role(self, thread_id: str) -> str | None:
        """Resolve a verified implementer/reviewer role from immutable host state."""

        thread = _safe_id(thread_id, "ai_thread_id_invalid")
        connection = self._connect()
        try:
            return self._resolve_repair_identity_role(connection, thread)
        finally:
            connection.close()

    def _record_host_repair_review_execution(
        self,
        *,
        capability: object,
        job_id: str,
        attempt_id: str,
        result_version: str,
        author_id: str,
        reviewer_id: str,
        reviewer_session_key: str,
        reviewer_turn_id: str,
        reviewer_output_sha256: str,
        reviewed_output_sha256: str,
        code_sha256: str,
        patch_sha256: str,
        test_ledger_sha256: str,
        approved: bool,
        unresolved_critical_findings: tuple[str, ...],
        created_at: datetime | str,
    ) -> RepairHostReviewExecutionRecord:
        """Persist one host-observed reviewer turn behind the Store capability."""

        if capability is not self._repair_review_capability:
            raise MaintenanceStoreError("repair_review_capability_invalid")
        job = _safe_id(job_id, "job_id_invalid")
        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        version = _safe_id(result_version, "result_version_invalid")
        author = _safe_id(author_id, "step_author_id_invalid")
        reviewer = _safe_id(reviewer_id, "reviewer_id_invalid")
        session_key = _safe_id(reviewer_session_key, "ai_session_key_invalid")
        turn_id = _safe_id(reviewer_turn_id, "repair_review_turn_invalid")
        hashes = {
            "reviewer_output_sha256": reviewer_output_sha256,
            "reviewed_output_sha256": reviewed_output_sha256,
            "code_sha256": code_sha256,
            "patch_sha256": patch_sha256,
            "test_ledger_sha256": test_ledger_sha256,
        }
        if any(
            not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value)
            for value in hashes.values()
        ):
            raise MaintenanceStoreError("repair_review_receipt_invalid")
        if (
            type(approved) is not bool
            or not isinstance(unresolved_critical_findings, tuple)
            or any(
                not isinstance(item, str) or not item.strip()
                for item in unresolved_critical_findings
            )
            or len(set(unresolved_critical_findings))
            != len(unresolved_critical_findings)
        ):
            raise MaintenanceStoreError("repair_review_execution_invalid")
        when = _timestamp(created_at)
        payload = {
            "job_id": job,
            "attempt_id": attempt,
            "result_version": version,
            "author_id": author,
            "reviewer_id": reviewer,
            "reviewer_session_key": session_key,
            "reviewer_turn_id": turn_id,
            **hashes,
            "approved": approved,
            "unresolved_critical_findings": list(unresolved_critical_findings),
        }
        host_receipt_id = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        with self._transaction(repair_review_capability=capability) as connection:
            attempt_row = connection.execute(
                "SELECT s.job_id, s.step_key, s.sequence, s.status, r.author_id "
                "FROM step_attempt s JOIN maintenance_job j ON j.job_id = s.job_id "
                "JOIN release r ON r.release_id = j.release_id "
                "WHERE s.attempt_id = ?",
                (attempt,),
            ).fetchone()
            if (
                attempt_row is None
                or attempt_row["job_id"] != job
                or attempt_row["step_key"] != "maintenance_repair"
                or attempt_row["sequence"] != 3
                or attempt_row["status"] != "running"
                or attempt_row["author_id"] != author
                or reviewer == author
            ):
                raise MaintenanceStoreError("repair_review_receipt_attempt_mismatch")
            if (
                self._resolve_repair_identity_role(connection, author) != "implementer"
                or self._resolve_repair_identity_role(connection, reviewer)
                != "read_only_reviewer"
            ):
                raise MaintenanceStoreError("repair_review_role_invalid")
            existing = connection.execute(
                "SELECT * FROM repair_host_review_execution "
                "WHERE job_id = ? AND reviewer_session_key = ? AND reviewer_turn_id = ?",
                (job, session_key, turn_id),
            ).fetchone()
            if existing is not None:
                existing_document = self._repair_host_review_execution_document(
                    existing
                )
                if existing["host_receipt_id"] != host_receipt_id or any(
                    existing_document.get(key) != value
                    for key, value in payload.items()
                ):
                    raise MaintenanceStoreError("repair_review_execution_conflict")
                return self._repair_host_review_execution_record(existing)
            connection.execute(
                "INSERT INTO repair_host_review_execution "
                "(host_receipt_id, job_id, attempt_id, result_version, author_id, "
                "reviewer_id, reviewer_session_key, reviewer_turn_id, "
                "reviewer_output_sha256, reviewed_output_sha256, code_sha256, "
                "patch_sha256, test_ledger_sha256, approved, "
                "unresolved_critical_findings_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    host_receipt_id,
                    job,
                    attempt,
                    version,
                    author,
                    reviewer,
                    session_key,
                    turn_id,
                    reviewer_output_sha256,
                    reviewed_output_sha256,
                    code_sha256,
                    patch_sha256,
                    test_ledger_sha256,
                    int(approved),
                    json.dumps(
                        list(unresolved_critical_findings),
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    when,
                ),
            )
            row = connection.execute(
                "SELECT * FROM repair_host_review_execution WHERE host_receipt_id = ?",
                (host_receipt_id,),
            ).fetchone()
            assert row is not None
            return self._repair_host_review_execution_record(row)

    @staticmethod
    def _repair_host_review_execution_document(row: sqlite3.Row) -> dict[str, object]:
        payload = {
            "job_id": str(row["job_id"]),
            "attempt_id": str(row["attempt_id"]),
            "result_version": str(row["result_version"]),
            "author_id": str(row["author_id"]),
            "reviewer_id": str(row["reviewer_id"]),
            "reviewer_session_key": str(row["reviewer_session_key"]),
            "reviewer_turn_id": str(row["reviewer_turn_id"]),
            "reviewer_output_sha256": str(row["reviewer_output_sha256"]),
            "reviewed_output_sha256": str(row["reviewed_output_sha256"]),
            "code_sha256": str(row["code_sha256"]),
            "patch_sha256": str(row["patch_sha256"]),
            "test_ledger_sha256": str(row["test_ledger_sha256"]),
            "approved": bool(row["approved"]),
        }
        try:
            findings = json.loads(str(row["unresolved_critical_findings_json"]))
        except json.JSONDecodeError as exc:
            raise MaintenanceStoreError("repair_review_execution_corrupt") from exc
        if (
            not isinstance(findings, list)
            or any(not isinstance(item, str) or not item.strip() for item in findings)
            or len(set(findings)) != len(findings)
        ):
            raise MaintenanceStoreError("repair_review_execution_corrupt")
        payload["unresolved_critical_findings"] = findings
        receipt_id = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        if receipt_id != row["host_receipt_id"]:
            raise MaintenanceStoreError("repair_review_execution_corrupt")
        return {"host_receipt_id": receipt_id, **payload}

    @classmethod
    def _repair_host_review_execution_record(
        cls, row: sqlite3.Row
    ) -> RepairHostReviewExecutionRecord:
        document = cls._repair_host_review_execution_document(row)
        return RepairHostReviewExecutionRecord(
            host_receipt_id=document["host_receipt_id"],
            job_id=document["job_id"],
            attempt_id=document["attempt_id"],
            result_version=document["result_version"],
            author_id=document["author_id"],
            reviewer_id=document["reviewer_id"],
            reviewer_session_key=document["reviewer_session_key"],
            reviewer_turn_id=document["reviewer_turn_id"],
            reviewer_output_sha256=document["reviewer_output_sha256"],
            reviewed_output_sha256=document["reviewed_output_sha256"],
            code_sha256=document["code_sha256"],
            patch_sha256=document["patch_sha256"],
            test_ledger_sha256=document["test_ledger_sha256"],
            approved=bool(document["approved"]),
            unresolved_critical_findings=tuple(
                document["unresolved_critical_findings"]
            ),
            created_at=str(row["created_at"]),
        )

    def get_repair_host_review_execution(
        self, *, host_receipt_id: str
    ) -> RepairHostReviewExecutionRecord:
        """Read and integrity-check one persisted repair reviewer execution."""

        receipt = _safe_id(host_receipt_id, "repair_review_host_receipt_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM repair_host_review_execution WHERE host_receipt_id = ?",
                (receipt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("repair_host_review_execution_unavailable")
            return self._repair_host_review_execution_record(row)
        finally:
            connection.close()

    def record_repair_review_receipt(
        self,
        *,
        capability: object,
        job_id: str,
        attempt_id: str,
        result_version: str,
        author_id: str,
        reviewer_id: str,
        host_receipt_id: str,
        reviewed_output_sha256: str,
        code_sha256: str,
        patch_sha256: str,
        test_ledger_sha256: str,
        created_at: datetime | str,
    ) -> RepairReviewReceiptRecord:
        """Persist one receipt only for an already stored host reviewer execution."""

        if capability is not self._repair_review_capability:
            raise MaintenanceStoreError("repair_review_capability_invalid")
        job = _safe_id(job_id, "job_id_invalid")
        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        version = _safe_id(result_version, "result_version_invalid")
        author = _safe_id(author_id, "step_author_id_invalid")
        reviewer = _safe_id(reviewer_id, "reviewer_id_invalid")
        host_receipt = _safe_id(host_receipt_id, "repair_review_host_receipt_invalid")
        hashes = {
            "reviewed_output_sha256": reviewed_output_sha256,
            "code_sha256": code_sha256,
            "patch_sha256": patch_sha256,
            "test_ledger_sha256": test_ledger_sha256,
        }
        if any(
            not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value)
            for value in hashes.values()
        ):
            raise MaintenanceStoreError("repair_review_receipt_invalid")
        when = _timestamp(created_at)
        with self._transaction(repair_review_capability=capability) as connection:
            attempt_row = connection.execute(
                "SELECT s.job_id, s.step_key, s.sequence, s.status, r.author_id "
                "FROM step_attempt s JOIN maintenance_job j ON j.job_id = s.job_id "
                "JOIN release r ON r.release_id = j.release_id "
                "WHERE s.attempt_id = ?",
                (attempt,),
            ).fetchone()
            if (
                attempt_row is None
                or attempt_row["job_id"] != job
                or attempt_row["step_key"] != "maintenance_repair"
                or attempt_row["sequence"] != 3
                or attempt_row["status"] != "running"
                or attempt_row["author_id"] != author
                or author == reviewer
            ):
                raise MaintenanceStoreError("repair_review_receipt_attempt_mismatch")
            author_role = self._resolve_repair_identity_role(connection, author)
            reviewer_role = self._resolve_repair_identity_role(connection, reviewer)
            if author_role != "implementer" or reviewer_role != "read_only_reviewer":
                raise MaintenanceStoreError("repair_review_role_invalid")
            source_row = connection.execute(
                "SELECT * FROM repair_host_review_execution WHERE host_receipt_id = ?",
                (host_receipt,),
            ).fetchone()
            if source_row is None:
                raise MaintenanceStoreError("repair_host_review_execution_unavailable")
            source = self._repair_host_review_execution_document(source_row)
            expected_source = {
                "job_id": job,
                "attempt_id": attempt,
                "result_version": version,
                "author_id": author,
                "reviewer_id": reviewer,
                "reviewed_output_sha256": reviewed_output_sha256,
                "code_sha256": code_sha256,
                "patch_sha256": patch_sha256,
                "test_ledger_sha256": test_ledger_sha256,
            }
            if any(source.get(key) != value for key, value in expected_source.items()):
                raise MaintenanceStoreError("repair_review_execution_mismatch")
            if (
                source.get("approved") is not True
                or source.get("unresolved_critical_findings") != []
            ):
                raise MaintenanceStoreError("repair_review_not_approved")
            existing = connection.execute(
                "SELECT * FROM repair_review_receipt "
                "WHERE attempt_id = ? AND result_version = ?",
                (attempt, version),
            ).fetchone()
            payload = {
                "job_id": job,
                "attempt_id": attempt,
                "result_version": version,
                "author_id": author,
                "reviewer_id": reviewer,
                "author_role": author_role,
                "reviewer_role": reviewer_role,
                "host_receipt_id": host_receipt,
                **hashes,
            }
            if existing is not None:
                document = self._repair_review_receipt_document(existing)
                if any(document.get(key) != value for key, value in payload.items()):
                    raise MaintenanceStoreError("repair_review_receipt_conflict")
                return self._repair_review_receipt_record(existing, document)

            receipt_id = str(uuid.uuid4())
            payload["receipt_id"] = receipt_id
            receipt_sha256 = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ).hexdigest()
            connection.execute(
                "INSERT INTO repair_review_receipt "
                "(receipt_id, job_id, attempt_id, result_version, author_id, reviewer_id, "
                "author_role, reviewer_role, host_receipt_id, reviewed_output_sha256, "
                "code_sha256, patch_sha256, test_ledger_sha256, receipt_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt_id,
                    job,
                    attempt,
                    version,
                    author,
                    reviewer,
                    author_role,
                    reviewer_role,
                    host_receipt,
                    reviewed_output_sha256,
                    code_sha256,
                    patch_sha256,
                    test_ledger_sha256,
                    receipt_sha256,
                    when,
                ),
            )
            row = connection.execute(
                "SELECT * FROM repair_review_receipt WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
            assert row is not None
            document = self._repair_review_receipt_document(row)
            return self._repair_review_receipt_record(row, document)

    @staticmethod
    def _repair_review_receipt_record(
        row: sqlite3.Row, document: Mapping[str, object]
    ) -> RepairReviewReceiptRecord:
        return RepairReviewReceiptRecord(
            receipt_id=str(document["receipt_id"]),
            attempt_id=str(document["attempt_id"]),
            job_id=str(document["job_id"]),
            result_version=str(document["result_version"]),
            author_id=str(document["author_id"]),
            reviewer_id=str(document["reviewer_id"]),
            reviewer_role=str(document["reviewer_role"]),
            host_receipt_id=str(document["host_receipt_id"]),
            reviewed_output_sha256=str(document["reviewed_output_sha256"]),
            code_sha256=str(document["code_sha256"]),
            patch_sha256=str(document["patch_sha256"]),
            test_ledger_sha256=str(document["test_ledger_sha256"]),
            receipt_sha256=str(document["receipt_sha256"]),
            consumed=row["consumed_at"] is not None,
        )

    def get_repair_review_receipt(
        self, *, receipt_id: str
    ) -> RepairReviewReceiptRecord:
        """Read and integrity-check one persisted repair approval receipt."""

        receipt = _safe_id(receipt_id, "repair_review_receipt_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM repair_review_receipt WHERE receipt_id = ?",
                (receipt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("repair_review_receipt_unavailable")
            document = self._repair_review_receipt_document(row)
            return self._repair_review_receipt_record(row, document)
        finally:
            connection.close()

    @staticmethod
    def _repair_review_receipt_document(
        row: sqlite3.Row,
    ) -> dict[str, object]:
        payload = {
            "receipt_id": str(row["receipt_id"]),
            "job_id": str(row["job_id"]),
            "attempt_id": str(row["attempt_id"]),
            "result_version": str(row["result_version"]),
            "author_id": str(row["author_id"]),
            "reviewer_id": str(row["reviewer_id"]),
            "author_role": str(row["author_role"]),
            "reviewer_role": str(row["reviewer_role"]),
            "host_receipt_id": str(row["host_receipt_id"]),
            "reviewed_output_sha256": str(row["reviewed_output_sha256"]),
            "code_sha256": str(row["code_sha256"]),
            "patch_sha256": str(row["patch_sha256"]),
            "test_ledger_sha256": str(row["test_ledger_sha256"]),
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        if digest != row["receipt_sha256"]:
            raise MaintenanceStoreError("repair_review_receipt_corrupt")
        return {
            **payload,
            "receipt_sha256": digest,
            "consumed": row["consumed_at"] is not None,
        }

    @staticmethod
    def _resolve_repair_identity_role(
        connection: sqlite3.Connection, thread: str
    ) -> str | None:
        row = connection.execute(
            "SELECT session.parent_key, session.role_name, session.sandbox_json, "
            "observation.status FROM maintenance_ai_session session "
            "JOIN maintenance_ai_observation observation "
            "ON observation.job_id = session.job_id "
            "AND observation.session_key = session.session_key "
            "AND observation.thread_id = session.thread_id "
            "WHERE session.thread_id = ? "
            "ORDER BY observation.observed_at DESC, observation.observation_id DESC "
            "LIMIT 1",
            (thread,),
        ).fetchone()
        if row is None or row["parent_key"] != "B" or row["status"] != "verified":
            return None
        try:
            sandbox = json.loads(str(row["sandbox_json"]))
        except json.JSONDecodeError:
            return None
        if row["role_name"] == "common-implementer" and sandbox == {
            "type": "workspaceWrite",
            "networkAccess": False,
        }:
            return "implementer"
        if row["role_name"] == "common-reviewer" and sandbox == {
            "type": "readOnly",
            "networkAccess": False,
        }:
            return "read_only_reviewer"
        return None

    def record_ai_session_observation(
        self,
        *,
        job_id: str,
        session_key: str,
        thread_id: str,
        status: str,
        observed_at: datetime | str,
        observed_model: str | None = None,
        observed_effort: str | None = None,
        observed_service_tier: str | None = None,
        reason_code: str | None = None,
    ) -> str:
        job = _safe_id(job_id, "job_id_invalid")
        key = _safe_id(session_key, "ai_session_key_invalid")
        thread = _safe_id(thread_id, "ai_thread_id_invalid")
        if status not in {"configured_unverified", "verified", "blocked"}:
            raise MaintenanceStoreError("ai_observation_status_invalid")
        observed = (observed_model, observed_effort, observed_service_tier)
        if any(
            value is not None and (not isinstance(value, str) or not value)
            for value in observed
        ):
            raise MaintenanceStoreError("ai_observation_value_invalid")
        if status == "configured_unverified" and any(
            value is not None for value in observed
        ):
            raise MaintenanceStoreError("ai_observation_status_mismatch")
        if reason_code is not None:
            reason_code = _safe_code(reason_code, "ai_observation_reason_invalid")
        when = _timestamp(observed_at)
        observation_id = uuid.uuid4().hex
        with self._transaction() as connection:
            session = connection.execute(
                "SELECT thread_id, requested_model, requested_effort, "
                "requested_service_tier FROM maintenance_ai_session "
                "WHERE job_id = ? AND session_key = ?",
                (job, key),
            ).fetchone()
            if session is None:
                raise MaintenanceStoreError("ai_session_missing")
            if session["thread_id"] != thread:
                raise MaintenanceStoreError("ai_session_thread_mismatch")
            if status == "verified" and (
                observed_model != session["requested_model"]
                or observed_effort != session["requested_effort"]
                or observed_service_tier != session["requested_service_tier"]
                or reason_code is not None
            ):
                raise MaintenanceStoreError("ai_observation_status_mismatch")
            connection.execute(
                "INSERT INTO maintenance_ai_observation "
                "(observation_id, job_id, session_key, thread_id, status, observed_model, "
                "observed_effort, observed_service_tier, reason_code, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    observation_id,
                    job,
                    key,
                    thread,
                    status,
                    observed_model,
                    observed_effort,
                    observed_service_tier,
                    reason_code,
                    when,
                ),
            )
        return observation_id

    def record_daily_launch_start(
        self,
        *,
        source_launch_id: str,
        target_date: str,
        owner_pid: int,
        started_at: datetime | str,
        owner_start_time: datetime | str | None = None,
    ) -> DailyLaunchRecord:
        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        target = _target_date(target_date)
        if (
            isinstance(owner_pid, bool)
            or not isinstance(owner_pid, int)
            or owner_pid <= 0
        ):
            raise MaintenanceStoreError("daily_launch_owner_pid_invalid")
        started = _timestamp(started_at)
        owner_started = (
            _timestamp(owner_start_time, "owner_start_time_invalid")
            if owner_start_time is not None
            else None
        )
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT source_launch_id FROM daily_launch WHERE source_launch_id = ?",
                (launch_id,),
            ).fetchone()
            if existing is not None:
                raise IdempotencyConflictError("daily_launch_id_already_used")
            connection.execute(
                "INSERT INTO daily_launch "
                "(source_launch_id, target_date, owner_pid, owner_start_time, started_at, status) "
                "VALUES (?, ?, ?, ?, ?, 'running')",
                (launch_id, target, owner_pid, owner_started, started),
            )
        record = self.get_daily_launch(launch_id)
        if record is None:
            raise MaintenanceStoreError("daily_launch_start_unavailable")
        return record

    def record_daily_launch_child(
        self,
        *,
        source_launch_id: str,
        child_pid: int,
        child_start_time: datetime | str | None = None,
    ) -> DailyLaunchRecord:
        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        if (
            isinstance(child_pid, bool)
            or not isinstance(child_pid, int)
            or child_pid <= 0
        ):
            raise MaintenanceStoreError("daily_launch_child_pid_invalid")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status, child_pid, child_start_time FROM daily_launch "
                "WHERE source_launch_id = ?",
                (launch_id,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("daily_launch_not_started")
            if row["status"] != "running":
                raise MaintenanceStoreError("daily_launch_not_running")
            if row["child_pid"] is not None and int(row["child_pid"]) != child_pid:
                raise IdempotencyConflictError("daily_launch_child_pid_conflict")
            child_started = (
                _timestamp(child_start_time, "child_start_time_invalid")
                if child_start_time is not None
                else None
            )
            if (
                row["child_start_time"] is not None
                and child_started is not None
                and str(row["child_start_time"]) != child_started
            ):
                raise IdempotencyConflictError("daily_launch_child_start_conflict")
            connection.execute(
                "UPDATE daily_launch SET child_pid = ?, child_start_time = "
                "COALESCE(child_start_time, ?) "
                "WHERE source_launch_id = ? AND status = 'running'",
                (child_pid, child_started, launch_id),
            )
        record = self.get_daily_launch(launch_id)
        if record is None:
            raise MaintenanceStoreError("daily_launch_child_unavailable")
        return record

    def get_daily_launch(self, source_launch_id: str) -> DailyLaunchRecord | None:
        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT source_launch_id, target_date, owner_pid, owner_start_time, started_at, status, "
                "child_pid, child_start_time, source_run_id, exit_code, completed_at, result_json "
                "FROM daily_launch WHERE source_launch_id = ?",
                (launch_id,),
            ).fetchone()
            return _daily_launch_from_row(row) if row is not None else None
        finally:
            connection.close()

    def get_daily_check_by_launch(
        self, source_launch_id: str
    ) -> DailyCheckRecord | None:
        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT check_id, source_launch_id, source_run_id, target_date, "
                "check_status, reason_code, observed_at FROM daily_check "
                "WHERE source_launch_id = ?",
                (launch_id,),
            ).fetchone()
            if row is None:
                return None
            return DailyCheckRecord(
                check_id=str(row["check_id"]),
                source_launch_id=str(row["source_launch_id"]),
                source_run_id=(
                    str(row["source_run_id"])
                    if row["source_run_id"] is not None
                    else None
                ),
                target_date=str(row["target_date"]),
                check_status=str(row["check_status"]),
                reason_code=(
                    str(row["reason_code"]) if row["reason_code"] is not None else None
                ),
                observed_at=str(row["observed_at"]),
            )
        finally:
            connection.close()

    def get_daily_result_observation(
        self, source_launch_id: str
    ) -> DailyResultObservationRecord | None:
        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT source_launch_id, target_date, source_run_id, schema_version, "
                "observation_json, observation_sha256, captured_at "
                "FROM daily_result_observation WHERE source_launch_id = ?",
                (launch_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        record = _daily_result_observation_from_row(row)
        digest = hashlib.sha256(record.observation_json.encode("utf-8")).hexdigest()
        if digest != record.observation_sha256:
            raise MaintenanceStoreError("daily_result_observation_hash_mismatch")
        observation = record.observation
        if (
            record.schema_version != "daily-result-observation-v1"
            or observation.get("source_launch_id") != record.source_launch_id
            or observation.get("target_date") != record.target_date
            or observation.get("source_run_id") != record.source_run_id
        ):
            raise MaintenanceStoreError("daily_result_observation_identity_invalid")
        return record

    @staticmethod
    def _ensure_worker_queue(
        connection: sqlite3.Connection,
        *,
        source_launch_id: str,
        target_date: str,
        created_at: str,
    ) -> WorkerQueueRecord:
        existing = connection.execute(
            "SELECT * FROM worker_queue WHERE source_launch_id = ?",
            (source_launch_id,),
        ).fetchone()
        if existing is not None:
            if existing["target_date"] != target_date:
                raise IdempotencyConflictError("worker_queue_identity_conflict")
            return _worker_queue_from_row(existing)
        connection.execute(
            "INSERT INTO worker_queue "
            "(queue_id, source_launch_id, worker_job_id, target_date, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, ?)",
            (
                uuid.uuid4().hex,
                source_launch_id,
                f"worker-{uuid.uuid4().hex}",
                target_date,
                created_at,
                created_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM worker_queue WHERE source_launch_id = ?",
            (source_launch_id,),
        ).fetchone()
        if row is None:
            raise MaintenanceStoreError("worker_queue_unavailable")
        return _worker_queue_from_row(row)

    def get_worker_queue(self, source_launch_id: str) -> WorkerQueueRecord | None:
        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM worker_queue WHERE source_launch_id = ?",
                (launch_id,),
            ).fetchone()
            return _worker_queue_from_row(row) if row is not None else None
        finally:
            connection.close()

    def list_worker_queue(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        exclude_target_dates: Iterable[str] | None = None,
    ) -> tuple[WorkerQueueRecord, ...]:
        if status is not None and status not in {"queued", "complete", "blocked"}:
            raise MaintenanceStoreError("worker_queue_status_invalid")
        excluded_dates = (
            tuple(dict.fromkeys(_target_date(item) for item in exclude_target_dates))
            if exclude_target_dates is not None
            else ()
        )
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise MaintenanceStoreError("worker_queue_limit_invalid")
        connection = self._connect()
        try:
            filters: list[str] = []
            parameters: list[object] = []
            if status is not None:
                filters.append("status = ?")
                parameters.append(status)
            if excluded_dates:
                placeholders = ", ".join("?" for _ in excluded_dates)
                filters.append(f"target_date NOT IN ({placeholders})")
                parameters.extend(excluded_dates)
            where_clause = f" WHERE {' AND '.join(filters)}" if filters else ""
            parameters.append(limit)
            rows = connection.execute(
                "SELECT * FROM worker_queue"
                f"{where_clause} ORDER BY created_at, queue_id LIMIT ?",
                parameters,
            ).fetchall()
            return tuple(_worker_queue_from_row(row) for row in rows)
        finally:
            connection.close()

    def complete_worker_queue(
        self,
        *,
        queue_id: str,
        check_id: str,
        lease: LeaseToken,
        completed_at: datetime | str,
    ) -> WorkerQueueRecord:
        queue = _safe_id(queue_id, "worker_queue_id_invalid")
        check = _safe_id(check_id, "check_id_invalid")
        when = _timestamp(completed_at)
        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            row = connection.execute(
                "SELECT * FROM worker_queue WHERE queue_id = ?", (queue,)
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("worker_queue_not_found")
            if row["status"] == "complete":
                if row["check_id"] != check:
                    raise IdempotencyConflictError("worker_queue_result_conflict")
                return _worker_queue_from_row(row)
            if row["status"] != "queued":
                raise MaintenanceStoreError("worker_queue_not_actionable")
            check_row = connection.execute(
                "SELECT source_launch_id, target_date FROM daily_check WHERE check_id = ?",
                (check,),
            ).fetchone()
            if (
                check_row is None
                or check_row["source_launch_id"] != row["source_launch_id"]
                or check_row["target_date"] != row["target_date"]
            ):
                raise MaintenanceStoreError("worker_queue_check_mismatch")
            connection.execute(
                "UPDATE worker_queue SET status = 'complete', check_id = ?, "
                "claim_owner_id = ?, claim_generation = ?, claimed_at = ?, "
                "completed_at = ?, updated_at = ? WHERE queue_id = ? AND status = 'queued'",
                (
                    check,
                    lease.owner_id,
                    lease.generation,
                    when,
                    when,
                    when,
                    queue,
                ),
            )
            completed = connection.execute(
                "SELECT * FROM worker_queue WHERE queue_id = ?", (queue,)
            ).fetchone()
            assert completed is not None
            return _worker_queue_from_row(completed)

    def block_worker_queue(
        self,
        *,
        queue_id: str,
        check_id: str | None,
        reason_code: str,
        lease: LeaseToken,
        blocked_at: datetime | str,
    ) -> WorkerQueueRecord:
        """Persist a fail-closed stop without marking the queue complete."""

        queue = _safe_id(queue_id, "worker_queue_id_invalid")
        check = None if check_id is None else _safe_id(check_id, "check_id_invalid")
        reason = _safe_code(reason_code, "worker_queue_reason_invalid")
        when = _timestamp(blocked_at)
        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            row = connection.execute(
                "SELECT * FROM worker_queue WHERE queue_id = ?", (queue,)
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("worker_queue_not_found")
            if row["status"] == "blocked":
                if row["reason_code"] != reason:
                    raise IdempotencyConflictError("worker_queue_block_conflict")
                return _worker_queue_from_row(row)
            if row["status"] != "queued":
                raise MaintenanceStoreError("worker_queue_not_actionable")
            if check is not None:
                check_row = connection.execute(
                    "SELECT source_launch_id, target_date FROM daily_check "
                    "WHERE check_id = ?",
                    (check,),
                ).fetchone()
                if (
                    check_row is None
                    or check_row["source_launch_id"] != row["source_launch_id"]
                    or check_row["target_date"] != row["target_date"]
                ):
                    raise MaintenanceStoreError("worker_queue_check_mismatch")
            connection.execute(
                "UPDATE worker_queue SET status = 'blocked', claim_owner_id = ?, "
                "claim_generation = ?, claimed_at = ?, reason_code = ?, "
                "updated_at = ? WHERE queue_id = ? AND status = 'queued'",
                (lease.owner_id, lease.generation, when, reason, when, queue),
            )
            connection.execute(
                "INSERT INTO worker_queue_event "
                "(event_id, queue_id, event_type, check_id, reason_code, "
                "owner_id, generation, occurred_at) "
                "VALUES (?, ?, 'blocked', ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    queue,
                    check,
                    reason,
                    lease.owner_id,
                    lease.generation,
                    when,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM worker_queue WHERE queue_id = ?", (queue,)
            ).fetchone()
            assert updated is not None
            return _worker_queue_from_row(updated)

    def resume_blocked_worker_queue(
        self,
        *,
        queue_id: str,
        lease: LeaseToken | None = None,
        resumed_at: datetime | str,
    ) -> WorkerQueueRecord:
        """Requeue a blocked item without checking its reason code."""

        _ = lease
        queue = _safe_id(queue_id, "worker_queue_id_invalid")
        when = _timestamp(resumed_at)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM worker_queue WHERE queue_id = ?", (queue,)
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("worker_queue_not_found")
            if row["status"] == "queued":
                return _worker_queue_from_row(row)
            if row["status"] != "blocked":
                raise MaintenanceStoreError("worker_queue_not_blocked")
            reason_value = row["reason_code"]
            reason = (
                reason_value
                if isinstance(reason_value, str)
                and _CODE_PATTERN.fullmatch(reason_value)
                else "unspecified"
            )
            blocked_event = connection.execute(
                "SELECT check_id FROM worker_queue_event "
                "WHERE queue_id = ? AND event_type = 'blocked' "
                "ORDER BY occurred_at DESC, rowid DESC LIMIT 1",
                (queue,),
            ).fetchone()
            check = blocked_event["check_id"] if blocked_event is not None else None
            connection.execute(
                "UPDATE worker_queue SET status = 'queued', check_id = NULL, "
                "claim_owner_id = NULL, claim_generation = NULL, claimed_at = NULL, "
                "completed_at = NULL, reason_code = NULL, updated_at = ? "
                "WHERE queue_id = ? AND status = 'blocked'",
                (when, queue),
            )
            connection.execute(
                "INSERT INTO worker_queue_event "
                "(event_id, queue_id, event_type, check_id, reason_code, "
                "owner_id, generation, occurred_at) "
                "VALUES (?, ?, 'resumed', ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    queue,
                    check,
                    reason,
                    "resume-cli",
                    1,
                    when,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM worker_queue WHERE queue_id = ?", (queue,)
            ).fetchone()
            assert updated is not None
            return _worker_queue_from_row(updated)

    def list_worker_queue_events(
        self, queue_id: str
    ) -> tuple[WorkerQueueEventRecord, ...]:
        queue = _safe_id(queue_id, "worker_queue_id_invalid")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT event_id, queue_id, event_type, check_id, reason_code, "
                "owner_id, generation, occurred_at FROM worker_queue_event "
                "WHERE queue_id = ? ORDER BY occurred_at, rowid",
                (queue,),
            ).fetchall()
            return tuple(_worker_queue_event_from_row(row) for row in rows)
        finally:
            connection.close()

    def list_daily_launches(
        self,
        *,
        target_date: str | None = None,
        target_date_from: str | None = None,
        target_date_to: str | None = None,
        target_dates: Iterable[str] | None = None,
        running_only: bool = False,
    ) -> tuple[DailyLaunchRecord, ...]:
        target = _target_date(target_date) if target_date is not None else None
        date_from = (
            _target_date(target_date_from) if target_date_from is not None else None
        )
        date_to = _target_date(target_date_to) if target_date_to is not None else None
        selected_dates = (
            tuple(dict.fromkeys(_target_date(item) for item in target_dates))
            if target_dates is not None
            else None
        )
        selector_count = sum(
            (
                target is not None,
                date_from is not None or date_to is not None,
                selected_dates is not None,
            )
        )
        if selector_count > 1 or (date_from is None) != (date_to is None):
            raise MaintenanceStoreError("daily_launch_date_range_invalid")
        if date_from is not None and date_from > date_to:
            raise MaintenanceStoreError("daily_launch_date_range_invalid")
        if selected_dates == ():
            return ()
        conditions: list[str] = []
        parameters: list[object] = []
        if target is not None:
            conditions.append("target_date = ?")
            parameters.append(target)
        if date_from is not None:
            conditions.extend(("target_date >= ?", "target_date <= ?"))
            parameters.extend((date_from, date_to))
        if selected_dates is not None:
            conditions.append(
                "target_date IN (" + ", ".join("?" for _ in selected_dates) + ")"
            )
            parameters.extend(selected_dates)
        if running_only:
            conditions.append("status = 'running'")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT source_launch_id, target_date, owner_pid, owner_start_time, started_at, status, "
                "child_pid, child_start_time, source_run_id, exit_code, completed_at, result_json "
                "FROM daily_launch" + where + " ORDER BY started_at, source_launch_id",
                parameters,
            ).fetchall()
        finally:
            connection.close()
        return tuple(_daily_launch_from_row(row) for row in rows)

    def record_watchdog_queue(
        self,
        *,
        occurrence_key: str,
        target_date: str,
        reason_code: str,
        now: datetime | str,
        source_launch_id: str | None = None,
        status: str = "unknown",
    ) -> tuple[WatchdogQueueRecord, bool]:
        key = _safe_id(occurrence_key, "watchdog_occurrence_key_invalid")
        target = _target_date(target_date)
        reason = _safe_code(reason_code, "watchdog_reason_code_invalid")
        when = _timestamp(now)
        launch_id = (
            _safe_id(source_launch_id, "source_launch_id_invalid")
            if source_launch_id is not None
            else None
        )
        if status not in {"unknown", "awaiting_completion", "resolved", "blocked"}:
            raise MaintenanceStoreError("watchdog_queue_status_invalid")
        if launch_id is None and status in {"awaiting_completion", "resolved"}:
            raise MaintenanceStoreError("watchdog_queue_launch_required")
        created = False
        result: WatchdogQueueRecord | None = None
        with self._transaction() as connection:
            if launch_id is not None:
                launch_row = connection.execute(
                    "SELECT target_date FROM daily_launch WHERE source_launch_id = ?",
                    (launch_id,),
                ).fetchone()
                if launch_row is None:
                    raise MaintenanceStoreError("watchdog_launch_not_found")
                if str(launch_row["target_date"]) != target:
                    raise IdempotencyConflictError("watchdog_launch_date_conflict")
                duplicate_launch = connection.execute(
                    "SELECT occurrence_key FROM watchdog_queue "
                    "WHERE source_launch_id = ? AND occurrence_key <> ? LIMIT 1",
                    (launch_id, key),
                ).fetchone()
                if duplicate_launch is not None:
                    raise IdempotencyConflictError("watchdog_launch_queue_conflict")
            current = connection.execute(
                "SELECT * FROM watchdog_queue WHERE occurrence_key = ?", (key,)
            ).fetchone()
            if current is None:
                queue_id = uuid.uuid4().hex
                connection.execute(
                    "INSERT INTO watchdog_queue "
                    "(queue_id, occurrence_key, target_date, source_launch_id, status, "
                    "reason_code, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (queue_id, key, target, launch_id, status, reason, when, when),
                )
                created = True
                result = WatchdogQueueRecord(
                    queue_id=queue_id,
                    occurrence_key=key,
                    target_date=target,
                    source_launch_id=launch_id,
                    status=status,
                    reason_code=reason,
                    created_at=when,
                    updated_at=when,
                )
            else:
                if str(current["target_date"]) != target:
                    raise IdempotencyConflictError("watchdog_occurrence_date_conflict")
                existing_launch_id = (
                    str(current["source_launch_id"])
                    if current["source_launch_id"] is not None
                    else None
                )
                if (
                    existing_launch_id is not None
                    and launch_id is not None
                    and existing_launch_id != launch_id
                ):
                    raise IdempotencyConflictError(
                        "watchdog_occurrence_launch_conflict"
                    )
                resolved_launch_id = existing_launch_id or launch_id
                current_status = str(current["status"])
                desired_status = status
                if current_status in {"resolved", "blocked"}:
                    desired_status = current_status
                elif current_status == "awaiting_completion" and status == "unknown":
                    desired_status = current_status
                needs_update = (
                    current["source_launch_id"] != resolved_launch_id
                    or str(current["status"]) != desired_status
                    or str(current["reason_code"]) != reason
                )
                if needs_update:
                    connection.execute(
                        "UPDATE watchdog_queue SET source_launch_id = ?, status = ?, "
                        "reason_code = ?, updated_at = ? WHERE queue_id = ?",
                        (
                            resolved_launch_id,
                            desired_status,
                            reason,
                            when,
                            str(current["queue_id"]),
                        ),
                    )
                    result = WatchdogQueueRecord(
                        queue_id=str(current["queue_id"]),
                        occurrence_key=key,
                        target_date=target,
                        source_launch_id=resolved_launch_id,
                        status=desired_status,
                        reason_code=reason,
                        created_at=str(current["created_at"]),
                        updated_at=when,
                    )
                else:
                    result = _watchdog_queue_from_row(current)
                queue_id = str(current["queue_id"])
        if result is None:
            raise MaintenanceStoreError("watchdog_queue_unavailable")
        return result, created

    def get_watchdog_cursor(self) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT reconciled_through_date FROM watchdog_cursor "
                "WHERE cursor_key = 'daily'"
            ).fetchone()
        finally:
            connection.close()
        return (
            _target_date(str(row["reconciled_through_date"]))
            if row is not None
            else None
        )

    def advance_watchdog_cursor(
        self, *, reconciled_through_date: str, updated_at: datetime | str
    ) -> str:
        target = _target_date(reconciled_through_date)
        when = _timestamp(updated_at)
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO watchdog_cursor "
                "(cursor_key, reconciled_through_date, updated_at) VALUES ('daily', ?, ?) "
                "ON CONFLICT(cursor_key) DO UPDATE SET "
                "reconciled_through_date = excluded.reconciled_through_date, "
                "updated_at = excluded.updated_at "
                "WHERE excluded.reconciled_through_date > "
                "watchdog_cursor.reconciled_through_date",
                (target, when),
            )
            row = connection.execute(
                "SELECT reconciled_through_date FROM watchdog_cursor "
                "WHERE cursor_key = 'daily'"
            ).fetchone()
        if row is None:
            raise MaintenanceStoreError("watchdog_cursor_unavailable")
        return _target_date(str(row["reconciled_through_date"]))

    def get_watchdog_queue(self, queue_id: str) -> WatchdogQueueRecord | None:
        key = _safe_id(queue_id, "watchdog_queue_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT queue_id, occurrence_key, target_date, source_launch_id, status, "
                "reason_code, created_at, updated_at FROM watchdog_queue WHERE queue_id = ?",
                (key,),
            ).fetchone()
            return _watchdog_queue_from_row(row) if row is not None else None
        finally:
            connection.close()

    def get_watchdog_queue_by_occurrence(
        self, occurrence_key: str
    ) -> WatchdogQueueRecord | None:
        key = _safe_id(occurrence_key, "watchdog_occurrence_key_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT queue_id, occurrence_key, target_date, source_launch_id, status, "
                "reason_code, created_at, updated_at FROM watchdog_queue "
                "WHERE occurrence_key = ?",
                (key,),
            ).fetchone()
        finally:
            connection.close()
        return _watchdog_queue_from_row(row) if row is not None else None

    def get_watchdog_queue_by_source_launch_id(
        self, source_launch_id: str
    ) -> WatchdogQueueRecord | None:
        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT queue_id, occurrence_key, target_date, source_launch_id, status, "
                "reason_code, created_at, updated_at FROM watchdog_queue "
                "WHERE source_launch_id = ?",
                (launch_id,),
            ).fetchone()
        finally:
            connection.close()
        return _watchdog_queue_from_row(row) if row is not None else None

    def list_watchdog_queue(
        self,
        *,
        open_only: bool = False,
        reconciliation_only: bool = False,
        target_date_from: str | None = None,
        target_date_to: str | None = None,
        target_dates: Iterable[str] | None = None,
    ) -> tuple[WatchdogQueueRecord, ...]:
        date_from = (
            _target_date(target_date_from) if target_date_from is not None else None
        )
        date_to = _target_date(target_date_to) if target_date_to is not None else None
        selected_dates = (
            tuple(dict.fromkeys(_target_date(item) for item in target_dates))
            if target_dates is not None
            else None
        )
        if (
            (date_from is None) != (date_to is None)
            or (
                selected_dates is not None
                and (date_from is not None or date_to is not None)
            )
            or (selected_dates is not None and open_only)
            or (reconciliation_only and open_only)
        ):
            raise MaintenanceStoreError("watchdog_queue_date_range_invalid")
        if date_from is not None and date_from > date_to:
            raise MaintenanceStoreError("watchdog_queue_date_range_invalid")
        if selected_dates == ():
            return ()
        connection = self._connect()
        try:
            conditions: list[str] = []
            parameters: list[object] = []
            if open_only:
                conditions.append(
                    "status IN ('unknown', 'awaiting_completion', 'blocked')"
                )
            if reconciliation_only:
                conditions.append("status IN ('unknown', 'awaiting_completion')")
            if date_from is not None:
                conditions.extend(("target_date >= ?", "target_date <= ?"))
                parameters.extend((date_from, date_to))
            if selected_dates is not None:
                conditions.append(
                    "target_date IN (" + ", ".join("?" for _ in selected_dates) + ")"
                )
                parameters.extend(selected_dates)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            rows = connection.execute(
                "SELECT queue_id, occurrence_key, target_date, source_launch_id, status, "
                "reason_code, created_at, updated_at FROM watchdog_queue"
                + where
                + " ORDER BY target_date, created_at, queue_id",
                parameters,
            ).fetchall()
        finally:
            connection.close()
        return tuple(_watchdog_queue_from_row(row) for row in rows)

    def record_daily_launch_completion(
        self,
        *,
        source_launch_id: str,
        target_date: str,
        child_pid: int | None,
        source_run_id: str | None,
        launch_status: str,
        exit_code: int,
        completed_at: datetime | str,
        check_status: str,
        reason_code: str | None,
        result_summary: Mapping[str, object],
        daily_result_observation: Mapping[str, object] | None = None,
    ) -> DailyLaunchRecord:
        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        target = _target_date(target_date)
        run_id = (
            None
            if source_run_id is None
            else _safe_id(source_run_id, "source_run_id_invalid")
        )
        if child_pid is not None and (
            isinstance(child_pid, bool)
            or not isinstance(child_pid, int)
            or child_pid <= 0
        ):
            raise MaintenanceStoreError("daily_launch_child_pid_invalid")
        if launch_status not in {"complete", "failed", "blocked", "unknown"}:
            raise MaintenanceStoreError("daily_launch_status_invalid")
        if (
            isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
            or not 0 <= exit_code <= 255
        ):
            raise MaintenanceStoreError("daily_launch_exit_code_invalid")
        if check_status not in {"normal", "normal_shortfall", "unknown", "incomplete"}:
            raise MaintenanceStoreError("daily_launch_check_status_invalid")
        if reason_code is not None:
            reason_code = _safe_code(reason_code, "reason_code_invalid")
        completed = _timestamp(completed_at)

        summary_keys = {
            "schema_version",
            "source_launch_id",
            "target_date",
            "status",
            "exit_code",
            "source_run_id",
            "overall_status",
            "daily_status",
            "reason_code",
            "failure_phase",
            "failed_checks",
            "primary_error_class",
            "secondary_error_classes",
            "trace_status",
        }
        if (
            not isinstance(result_summary, Mapping)
            or set(result_summary) != summary_keys
        ):
            raise MaintenanceStoreError("daily_launch_summary_schema_invalid")
        summary = dict(result_summary)
        summary_exit_code = summary["exit_code"]
        if (
            isinstance(summary_exit_code, bool)
            or not isinstance(summary_exit_code, int)
            or not 0 <= summary_exit_code <= 255
        ):
            raise MaintenanceStoreError("daily_launch_summary_exit_code_invalid")
        if (
            summary["schema_version"] != "daily-launch-completion-v1"
            or summary["source_launch_id"] != launch_id
            or summary["target_date"] != target
            or summary["status"] != launch_status
            or summary["exit_code"] != exit_code
            or summary["source_run_id"] != run_id
            or summary["reason_code"] != reason_code
        ):
            raise MaintenanceStoreError("daily_launch_summary_identity_invalid")
        for key in ("overall_status", "daily_status"):
            value = summary[key]
            if value is None:
                raise MaintenanceStoreError("daily_launch_summary_value_invalid")
            summary[key] = _safe_code(value, "daily_launch_summary_value_invalid")
        if summary["failure_phase"] is not None:
            summary["failure_phase"] = _safe_code(
                summary["failure_phase"], "daily_launch_summary_value_invalid"
            )
        failed_checks = summary["failed_checks"]
        if not isinstance(failed_checks, (list, tuple)) or len(failed_checks) > 32:
            raise MaintenanceStoreError("daily_launch_failed_checks_invalid")
        summary["failed_checks"] = [
            _safe_code(value, "daily_launch_failed_checks_invalid")
            for value in failed_checks
        ]
        primary_error_class = summary["primary_error_class"]
        if primary_error_class is not None and (
            not isinstance(primary_error_class, str)
            or not _ERROR_CLASS_PATTERN.fullmatch(primary_error_class)
        ):
            raise MaintenanceStoreError("daily_launch_error_class_invalid")
        secondary_error_classes = summary["secondary_error_classes"]
        if (
            not isinstance(secondary_error_classes, (list, tuple))
            or len(secondary_error_classes) > 32
        ):
            raise MaintenanceStoreError("daily_launch_error_class_invalid")
        summary["secondary_error_classes"] = []
        for value in secondary_error_classes:
            if not isinstance(value, str) or not _ERROR_CLASS_PATTERN.fullmatch(value):
                raise MaintenanceStoreError("daily_launch_error_class_invalid")
            summary["secondary_error_classes"].append(value)
        trace_status = summary["trace_status"]
        if not isinstance(trace_status, str) or trace_status not in {
            "healthy",
            "degraded",
            "unavailable",
            "not_written",
        }:
            raise MaintenanceStoreError("daily_launch_trace_status_invalid")
        overall_status = summary["overall_status"]
        daily_status = summary["daily_status"]
        if launch_status == "complete":
            if check_status == "incomplete":
                consistent = (
                    overall_status in {"complete", "partial", "shortfall"}
                    and daily_status in {"complete", "partial", "shortfall"}
                    and exit_code in {0, 5}
                    and run_id is not None
                    and reason_code
                    in {
                        "daily_retry_observation_invalid",
                        "daily_retry_observation_incomplete",
                        "daily_retry_contract_violation",
                        "daily_retry_unresolved",
                    }
                )
            elif check_status == "normal":
                consistent = (
                    overall_status == "complete"
                    and daily_status == "complete"
                    and exit_code == 0
                    and run_id is not None
                    and reason_code is None
                )
            elif check_status == "normal_shortfall":
                consistent = (
                    overall_status in {"partial", "shortfall"}
                    and daily_status in {"partial", "shortfall"}
                    and exit_code == 5
                    and run_id is not None
                )
            elif check_status == "unknown":
                consistent = (
                    overall_status == "not_due"
                    and daily_status == "not_due"
                    and exit_code == 0
                    and reason_code == "daily_not_due"
                )
            else:
                consistent = False
        else:
            consistent = check_status == "incomplete"
            if launch_status == "blocked":
                consistent = (
                    consistent
                    and overall_status == "blocked"
                    and daily_status == "unknown"
                    and exit_code == 4
                )
            elif launch_status == "unknown":
                consistent = (
                    consistent
                    and overall_status == "unknown"
                    and daily_status == "unknown"
                    and exit_code != 0
                )
            else:
                consistent = (
                    consistent
                    and overall_status
                    not in {
                        "blocked",
                        "unknown",
                        "complete",
                        "partial",
                        "shortfall",
                        "not_due",
                    }
                    and exit_code not in {0, 5}
                )
        if not consistent:
            raise MaintenanceStoreError("daily_launch_completion_status_mismatch")
        encoded_summary = json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        observation_document: dict[str, object] | None = None
        encoded_observation: str | None = None
        observation_sha256: str | None = None
        if daily_result_observation is not None:
            (
                observation_document,
                encoded_observation,
                observation_sha256,
            ) = _validated_daily_result_observation(
                daily_result_observation,
                source_launch_id=launch_id,
                target_date=target,
                source_run_id=run_id,
            )

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT target_date, status, child_pid, source_run_id, exit_code, "
                "completed_at, result_json FROM daily_launch "
                "WHERE source_launch_id = ?",
                (launch_id,),
            ).fetchone()
            if existing is None:
                raise MaintenanceStoreError("daily_launch_not_started")
            if existing["target_date"] != target:
                raise IdempotencyConflictError("daily_launch_target_date_conflict")
            current_child_pid = (
                int(existing["child_pid"])
                if existing["child_pid"] is not None
                else None
            )
            if current_child_pid is not None and current_child_pid != child_pid:
                raise IdempotencyConflictError("daily_launch_child_pid_conflict")

            check = connection.execute(
                "SELECT source_run_id, target_date, check_status, reason_code, observed_at "
                "FROM daily_check WHERE source_launch_id = ?",
                (launch_id,),
            ).fetchone()
            if existing["status"] != "running":
                existing_observation = connection.execute(
                    "SELECT observation_sha256 FROM daily_result_observation "
                    "WHERE source_launch_id = ?",
                    (launch_id,),
                ).fetchone()
                if observation_sha256 is not None and (
                    existing_observation is None
                    or existing_observation["observation_sha256"] != observation_sha256
                ):
                    raise IdempotencyConflictError("daily_launch_observation_conflict")
                compatible_check = (
                    check is not None
                    and check["source_run_id"] == run_id
                    and check["target_date"] == target
                    and (
                        (
                            check["check_status"] == check_status
                            and check["reason_code"] == reason_code
                            and check["observed_at"] == completed
                        )
                        or (
                            existing["status"] == "failed"
                            and check_status == "incomplete"
                            and check["check_status"] == "abnormal"
                        )
                    )
                )
                if (
                    existing["status"] != launch_status
                    or existing["source_run_id"] != run_id
                    or existing["exit_code"] != exit_code
                    or existing["completed_at"] != completed
                    or existing["result_json"] != encoded_summary
                    or not compatible_check
                ):
                    raise IdempotencyConflictError("daily_launch_completion_conflict")
            else:
                if check is not None:
                    if (
                        check["source_run_id"] != run_id
                        or check["target_date"] != target
                        or check["check_status"] != check_status
                        or check["reason_code"] != reason_code
                        or check["observed_at"] != completed
                    ):
                        raise IdempotencyConflictError("daily_launch_check_conflict")
                else:
                    connection.execute(
                        "INSERT INTO daily_check "
                        "(check_id, source_launch_id, source_run_id, target_date, "
                        "check_status, reason_code, observed_at, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            launch_id,
                            run_id,
                            target,
                            check_status,
                            reason_code,
                            completed,
                            completed,
                        ),
                    )
                connection.execute(
                    "UPDATE daily_launch SET status = ?, child_pid = ?, source_run_id = ?, "
                    "exit_code = ?, completed_at = ?, result_json = ? "
                    "WHERE source_launch_id = ? AND status = 'running'",
                    (
                        launch_status,
                        child_pid if child_pid is not None else current_child_pid,
                        run_id,
                        exit_code,
                        completed,
                        encoded_summary,
                        launch_id,
                    ),
                )
                if observation_document is not None:
                    assert encoded_observation is not None
                    assert observation_sha256 is not None
                    connection.execute(
                        "INSERT INTO daily_result_observation "
                        "(source_launch_id, target_date, source_run_id, schema_version, "
                        "observation_json, observation_sha256, captured_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            launch_id,
                            target,
                            run_id,
                            observation_document["schema_version"],
                            encoded_observation,
                            observation_sha256,
                            completed,
                        ),
                    )
            self._ensure_worker_queue(
                connection,
                source_launch_id=launch_id,
                target_date=target,
                created_at=completed,
            )
        record = self.get_daily_launch(launch_id)
        if record is None:
            raise MaintenanceStoreError("daily_launch_completion_unavailable")
        return record

    def record_daily_check(
        self,
        *,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
        check_status: str,
        observed_at: datetime | str,
        reason_code: str | None,
        incident: Mapping[str, object] | None,
    ) -> DailyCheckResult:
        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        run_id = (
            None
            if source_run_id is None
            else _safe_id(source_run_id, "source_run_id_invalid")
        )
        target = _target_date(target_date)
        if check_status not in _DAILY_CHECK_STATUSES:
            raise MaintenanceStoreError("check_status_invalid")
        observed = _timestamp(observed_at)
        if reason_code is not None:
            reason_code = _safe_code(reason_code, "reason_code_invalid")
        incident_values: tuple[str, str, str] | None = None
        if incident is not None:
            if set(incident) != {"dedupe_key", "component", "event_code"}:
                raise MaintenanceStoreError("incident_input_invalid")
            incident_values = (
                _safe_id(incident["dedupe_key"], "incident_dedupe_key_invalid"),
                _safe_code(incident["component"], "incident_component_invalid"),
                _safe_code(incident["event_code"], "incident_event_code_invalid"),
            )
        if check_status == "abnormal" and incident_values is None:
            raise MaintenanceStoreError("abnormal_check_requires_incident")
        if (
            check_status in {"normal", "normal_shortfall"}
            and incident_values is not None
        ):
            raise MaintenanceStoreError("normal_check_cannot_create_incident")

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT check_id, source_run_id, target_date, check_status, reason_code, observed_at "
                "FROM daily_check WHERE source_launch_id = ?",
                (launch_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["source_run_id"] != run_id
                    or existing["target_date"] != target
                ):
                    raise IdempotencyConflictError("launch_identity_conflict")
                same_check = (
                    existing["check_status"] == check_status
                    and existing["reason_code"] == reason_code
                    and existing["observed_at"] == observed
                )
                upgrade_to_abnormal = (
                    existing["check_status"] == "incomplete"
                    and check_status == "abnormal"
                )
                if upgrade_to_abnormal and not same_check:
                    launch = connection.execute(
                        "SELECT status FROM daily_launch WHERE source_launch_id = ?",
                        (launch_id,),
                    ).fetchone()
                    retry_incomplete_reasons = {
                        "daily_retry_observation_invalid",
                        "daily_retry_observation_incomplete",
                        "daily_retry_contract_violation",
                        "daily_retry_unresolved",
                    }
                    detector_abnormal_reasons = {
                        "daily_failure_observed",
                        "preclassification_contract_violation",
                        "retry_contract_violation",
                        "count_invariant_violation",
                        "status_mismatch",
                    }
                    retry_based_upgrade = (
                        launch is not None
                        and launch["status"] == "complete"
                        and existing["reason_code"] in retry_incomplete_reasons
                        and reason_code in detector_abnormal_reasons
                    )
                    if launch is None or not (
                        launch["status"] == "failed" or retry_based_upgrade
                    ):
                        raise IdempotencyConflictError("launch_identity_conflict")
                    connection.execute(
                        "UPDATE daily_check SET check_status = 'abnormal', reason_code = ?, "
                        "observed_at = ? WHERE check_id = ? AND check_status = 'incomplete'",
                        (reason_code, observed, existing["check_id"]),
                    )
                elif not same_check:
                    raise IdempotencyConflictError("launch_identity_conflict")
                incident_id = None
                associated = connection.execute(
                    "SELECT i.incident_id, i.dedupe_key, i.component, i.event_code "
                    "FROM incident_run ir "
                    "JOIN incident i ON i.incident_id = ir.incident_id "
                    "WHERE ir.check_id = ?",
                    (existing["check_id"],),
                ).fetchone()
                if associated is not None:
                    if (
                        incident_values is None
                        or (
                            associated["dedupe_key"],
                            associated["component"],
                            associated["event_code"],
                        )
                        != incident_values
                    ):
                        raise IdempotencyConflictError("launch_incident_conflict")
                    incident_id = str(associated["incident_id"])
                elif incident_values is None:
                    return DailyCheckResult(str(existing["check_id"]), False, None)
                elif not upgrade_to_abnormal:
                    raise IdempotencyConflictError("launch_incident_conflict")
                if associated is not None:
                    return DailyCheckResult(
                        str(existing["check_id"]), False, incident_id
                    )
                check_id = str(existing["check_id"])
                created = False
                created_at = _timestamp(datetime.now(timezone.utc))

            if existing is None:
                check_id = str(uuid.uuid4())
                created_at = _timestamp(datetime.now(timezone.utc))
                connection.execute(
                    "INSERT INTO daily_check "
                    "(check_id, source_launch_id, source_run_id, target_date, check_status, "
                    "reason_code, observed_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        check_id,
                        launch_id,
                        run_id,
                        target,
                        check_status,
                        reason_code,
                        observed,
                        created_at,
                    ),
                )
                created = True
            incident_id: str | None = None
            if incident_values is not None:
                dedupe_key, component, event_code = incident_values
                existing_incident = connection.execute(
                    "SELECT incident_id, component, event_code FROM incident WHERE dedupe_key = ? "
                    "AND status IN ('open', 'in_progress', 'blocked')",
                    (dedupe_key,),
                ).fetchone()
                if existing_incident is None:
                    incident_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO incident "
                        "(incident_id, dedupe_key, component, event_code, status, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, 'open', ?, ?)",
                        (
                            incident_id,
                            dedupe_key,
                            component,
                            event_code,
                            created_at,
                            created_at,
                        ),
                    )
                else:
                    if (
                        existing_incident["component"] != component
                        or existing_incident["event_code"] != event_code
                    ):
                        raise IdempotencyConflictError("incident_identity_conflict")
                    incident_id = str(existing_incident["incident_id"])
                connection.execute(
                    "INSERT INTO incident_run "
                    "(incident_id, check_id, source_launch_id, source_run_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (incident_id, check_id, launch_id, run_id, created_at),
                )
            return DailyCheckResult(check_id, created, incident_id)

    def list_releases_for_result_commit(
        self, result_commit: str
    ) -> tuple[ReleaseRecord, ...]:
        commit = _safe_id(result_commit, "result_commit_invalid")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT release_id, result_commit, result_version, config_version, state "
                "FROM release WHERE result_commit = ? ORDER BY created_at, release_id",
                (commit,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            ReleaseRecord(
                release_id=str(row["release_id"]),
                result_commit=str(row["result_commit"]),
                result_version=str(row["result_version"]),
                config_version=str(row["config_version"]),
                state=str(row["state"]),
            )
            for row in rows
        )

    def create_release(
        self,
        *,
        release_id: str,
        author_id: str,
        base_commit: str,
        result_commit: str,
        result_version: str,
        config_version: str,
        required_case_ids: tuple[str, ...],
        created_at: datetime | str,
    ) -> ReleaseRecord:
        release = _safe_id(release_id, "release_id_invalid")
        author = _safe_id(author_id, "release_author_invalid")
        base = _safe_id(base_commit, "base_commit_invalid")
        result = _safe_id(result_commit, "result_commit_invalid")
        version = _safe_id(result_version, "result_version_invalid")
        config = _safe_id(config_version, "config_version_invalid")
        cases = tuple(
            _safe_id(case_id, "required_case_id_invalid")
            for case_id in required_case_ids
        )
        if len(set(cases)) != len(cases):
            raise MaintenanceStoreError("required_case_set_invalid")
        created = _timestamp(created_at)
        manifest_hash = hashlib.sha256(
            json.dumps(cases, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO release "
                "(release_id, author_id, base_commit, result_commit, result_version, "
                "config_version, case_manifest_sha256, state, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'frozen', ?)",
                (
                    release,
                    author,
                    base,
                    result,
                    version,
                    config,
                    manifest_hash,
                    created,
                ),
            )
            connection.executemany(
                "INSERT INTO release_required_test (release_id, case_id, sequence) "
                "VALUES (?, ?, ?)",
                ((release, case_id, index) for index, case_id in enumerate(cases, 1)),
            )
        return ReleaseRecord(release, result, version, config, "frozen")

    def record_test_result(
        self,
        *,
        release_id: str,
        case_id: str,
        attempt: int,
        result_version: str,
        status: str,
        recorded_at: datetime | str,
        evidence_ref: str | None = None,
    ) -> str:
        release = _safe_id(release_id, "release_id_invalid")
        case = _safe_id(case_id, "case_id_invalid")
        version = _safe_id(result_version, "result_version_invalid")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise MaintenanceStoreError("test_attempt_invalid")
        if status not in _TEST_STATUSES:
            raise MaintenanceStoreError("test_status_invalid")
        evidence = None if evidence_ref is None else _relative_output_path(evidence_ref)
        result_id = str(uuid.uuid4())
        with self._transaction() as connection:
            required = connection.execute(
                "SELECT 1 FROM release_required_test WHERE release_id = ? AND case_id = ?",
                (release, case),
            ).fetchone()
            if required is None:
                raise MaintenanceStoreError("test_case_not_in_frozen_release")
            connection.execute(
                "INSERT INTO test_ledger "
                "(test_result_id, release_id, case_id, attempt, result_version, status, "
                "evidence_ref, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    release,
                    case,
                    attempt,
                    version,
                    status,
                    evidence,
                    _timestamp(recorded_at),
                ),
            )
        return result_id

    def record_release_review(
        self,
        *,
        release_id: str,
        reviewer_id: str,
        reviewed_version: str,
        status: str,
        reviewed_at: datetime | str,
    ) -> str:
        release = _safe_id(release_id, "release_id_invalid")
        reviewer = _safe_id(reviewer_id, "reviewer_id_invalid")
        version = _safe_id(reviewed_version, "reviewed_version_invalid")
        if status not in _REVIEW_STATUSES:
            raise MaintenanceStoreError("review_status_invalid")
        review_id = str(uuid.uuid4())
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT author_id FROM release WHERE release_id = ?", (release,)
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("release_not_found")
            if reviewer == row["author_id"]:
                raise MaintenanceStoreError("reviewer_must_be_independent")
            connection.execute(
                "INSERT INTO review_result "
                "(review_id, scope_type, scope_id, reviewer_id, reviewed_version, status, reviewed_at) "
                "VALUES (?, 'release', ?, ?, ?, ?, ?)",
                (
                    review_id,
                    release,
                    reviewer,
                    version,
                    status,
                    _timestamp(reviewed_at),
                ),
            )
        return review_id

    def register_repair_candidate_release(
        self,
        *,
        release_id: str,
        author_id: str,
        reviewer_id: str,
        base_commit: str,
        result_commit: str,
        result_version: str,
        config_version: str,
        required_case_ids: tuple[str, ...],
        evidence_ref: str,
        recorded_at: datetime | str,
    ) -> ReleaseRecord:
        """Persist a repair candidate's already verified test/review basis once."""

        release = _safe_id(release_id, "release_id_invalid")
        author = _safe_id(author_id, "release_author_invalid")
        reviewer = _safe_id(reviewer_id, "reviewer_id_invalid")
        base = _safe_id(base_commit, "base_commit_invalid")
        result = _safe_id(result_commit, "result_commit_invalid")
        version = _safe_id(result_version, "result_version_invalid")
        config = _safe_id(config_version, "config_version_invalid")
        cases = tuple(
            _safe_id(case_id, "required_case_id_invalid")
            for case_id in required_case_ids
        )
        evidence = _relative_output_path(evidence_ref)
        when = _timestamp(recorded_at)
        if (
            not cases
            or len(set(cases)) != len(cases)
            or author == reviewer
            or result == base
        ):
            raise MaintenanceStoreError("repair_candidate_release_identity_invalid")
        manifest_hash = hashlib.sha256(
            json.dumps(cases, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()

        with self._transaction() as connection:
            release_row = connection.execute(
                "SELECT author_id, base_commit, result_commit, result_version, "
                "config_version, case_manifest_sha256, state FROM release "
                "WHERE release_id = ?",
                (release,),
            ).fetchone()
            if release_row is None:
                connection.execute(
                    "INSERT INTO release "
                    "(release_id, author_id, base_commit, result_commit, result_version, "
                    "config_version, case_manifest_sha256, state, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'frozen', ?)",
                    (
                        release,
                        author,
                        base,
                        result,
                        version,
                        config,
                        manifest_hash,
                        when,
                    ),
                )
                connection.executemany(
                    "INSERT INTO release_required_test (release_id, case_id, sequence) "
                    "VALUES (?, ?, ?)",
                    (
                        (release, case_id, index)
                        for index, case_id in enumerate(cases, 1)
                    ),
                )
            elif (
                release_row["author_id"] != author
                or release_row["base_commit"] != base
                or release_row["result_commit"] != result
                or release_row["result_version"] != version
                or release_row["config_version"] != config
                or release_row["case_manifest_sha256"] != manifest_hash
                or release_row["state"] != "frozen"
            ):
                raise MaintenanceStoreError(
                    "repair_candidate_release_identity_conflict"
                )

            stored_cases = tuple(
                str(row["case_id"])
                for row in connection.execute(
                    "SELECT case_id FROM release_required_test "
                    "WHERE release_id = ? ORDER BY sequence",
                    (release,),
                ).fetchall()
            )
            if stored_cases != cases:
                raise MaintenanceStoreError(
                    "repair_candidate_release_case_set_conflict"
                )
            for case_id in cases:
                latest_test = connection.execute(
                    "SELECT status, result_version, evidence_ref FROM test_ledger "
                    "WHERE release_id = ? AND case_id = ? "
                    "ORDER BY attempt DESC, rowid DESC LIMIT 1",
                    (release, case_id),
                ).fetchone()
                if latest_test is None:
                    connection.execute(
                        "INSERT INTO test_ledger "
                        "(test_result_id, release_id, case_id, attempt, result_version, "
                        "status, evidence_ref, recorded_at) "
                        "VALUES (?, ?, ?, 1, ?, 'PASS', ?, ?)",
                        (str(uuid.uuid4()), release, case_id, version, evidence, when),
                    )
                elif (
                    latest_test["status"] != "PASS"
                    or latest_test["result_version"] != version
                    or latest_test["evidence_ref"] != evidence
                ):
                    raise MaintenanceStoreError(
                        "repair_candidate_release_test_conflict"
                    )

            latest_review = connection.execute(
                "SELECT reviewer_id, reviewed_version, status FROM review_result "
                "WHERE scope_type = 'release' AND scope_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (release,),
            ).fetchone()
            if latest_review is None:
                connection.execute(
                    "INSERT INTO review_result "
                    "(review_id, scope_type, scope_id, reviewer_id, reviewed_version, "
                    "status, reviewed_at) VALUES (?, 'release', ?, ?, ?, 'approved', ?)",
                    (str(uuid.uuid4()), release, reviewer, version, when),
                )
            elif (
                latest_review["reviewer_id"] != reviewer
                or latest_review["reviewed_version"] != version
                or latest_review["status"] != "approved"
            ):
                raise MaintenanceStoreError("repair_candidate_release_review_conflict")

            self._deployment_authorization(connection, release)

        return ReleaseRecord(release, result, version, config, "frozen")

    def record_deployment(
        self,
        *,
        release_id: str,
        result_commit: str,
        config_version: str,
        status: str,
        deployed_at: datetime | str,
        source_commit: str | None = None,
        base_commit: str | None = None,
        tested_commit: str | None = None,
        pushed_commit: str | None = None,
        active_commit: str | None = None,
        remote_name: str | None = None,
        target_branch: str | None = None,
        reason_code: str | None = None,
    ) -> str:
        release = _safe_id(release_id, "release_id_invalid")
        commit = _safe_id(result_commit, "result_commit_invalid")
        config = _safe_id(config_version, "config_version_invalid")
        if status not in _DEPLOYMENT_STATUSES:
            raise MaintenanceStoreError("deployment_status_invalid")
        if status == "deployed":
            raise MaintenanceStoreError("deployment_success_requires_pending_flow")
        source = (
            None
            if source_commit is None
            else _safe_id(source_commit, "source_commit_invalid")
        )
        base = (
            None
            if base_commit is None
            else _safe_id(base_commit, "base_commit_invalid")
        )
        tested = (
            None
            if tested_commit is None
            else _safe_id(tested_commit, "tested_commit_invalid")
        )
        pushed = (
            None
            if pushed_commit is None
            else _safe_id(pushed_commit, "pushed_commit_invalid")
        )
        active = (
            None
            if active_commit is None
            else _safe_id(active_commit, "active_commit_invalid")
        )
        remote = (
            None
            if remote_name is None
            else _safe_id(remote_name, "remote_name_invalid")
        )
        branch = (
            None
            if target_branch is None
            else _safe_id(target_branch, "target_branch_invalid")
        )
        reason = (
            None
            if reason_code is None
            else _safe_code(reason_code, "deployment_reason_invalid")
        )
        if status == "deployed" and (
            source != commit
            or tested != commit
            or pushed != commit
            or active != commit
            or base is None
            or remote is None
            or branch is None
        ):
            raise MaintenanceStoreError("deployment_success_identity_invalid")
        deployment_id = str(uuid.uuid4())
        with self._transaction() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM release WHERE release_id = ?", (release,)
                ).fetchone()
                is None
            ):
                raise MaintenanceStoreError("release_not_found")
            attempt = int(
                connection.execute(
                    "SELECT COALESCE(MAX(attempt), 0) + 1 FROM deployment WHERE release_id = ?",
                    (release,),
                ).fetchone()[0]
            )
            connection.execute(
                "INSERT INTO deployment "
                "(deployment_id, release_id, attempt, result_commit, config_version, status, deployed_at, "
                "source_commit, base_commit, tested_commit, pushed_commit, active_commit, "
                "remote_name, target_branch, reason_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    deployment_id,
                    release,
                    attempt,
                    commit,
                    config,
                    status,
                    _timestamp(deployed_at),
                    source,
                    base,
                    tested,
                    pushed,
                    active,
                    remote,
                    branch,
                    reason,
                ),
            )
        return deployment_id

    @staticmethod
    def _deployment_authorization(
        connection: sqlite3.Connection, release_id: str
    ) -> Mapping[str, str]:
        release = connection.execute(
            "SELECT release_id, author_id, base_commit, result_commit, result_version, "
            "config_version, case_manifest_sha256, state FROM release WHERE release_id = ?",
            (release_id,),
        ).fetchone()
        if release is None:
            raise MaintenanceStoreError("release_not_found")
        # Release/test/review states are recorded by evaluate_release_gate, but
        # they do not authorize or block downstream deployment attempts.
        return MappingProxyType(
            {
                "release_id": str(release["release_id"]),
                "author_id": str(release["author_id"]),
                "base_commit": str(release["base_commit"]),
                "result_commit": str(release["result_commit"]),
                "result_version": str(release["result_version"]),
                "config_version": str(release["config_version"]),
            }
        )

    def get_release_deployment_basis(self, *, release_id: str) -> Mapping[str, str]:
        """Return the identified release basis without review/test state gates."""

        release = _safe_id(release_id, "release_id_invalid")
        connection = self._connect()
        try:
            return self._deployment_authorization(connection, release)
        finally:
            connection.close()

    def begin_deployment(
        self,
        *,
        release_id: str,
        source_commit: str,
        base_commit: str,
        tested_commit: str,
        remote_name: str,
        target_branch: str,
        started_at: datetime | str,
    ) -> str:
        """Create a pending attempt pinned to the exact authorized release."""

        release = _safe_id(release_id, "release_id_invalid")
        source = _safe_id(source_commit, "source_commit_invalid")
        base = _safe_id(base_commit, "base_commit_invalid")
        tested = _safe_id(tested_commit, "tested_commit_invalid")
        remote = _safe_id(remote_name, "remote_name_invalid")
        branch = _safe_id(target_branch, "target_branch_invalid")
        when = _timestamp(started_at)
        deployment_id = str(uuid.uuid4())
        with self._transaction() as connection:
            basis = self._deployment_authorization(connection, release)
            if (
                source != basis["result_commit"]
                or tested != basis["result_commit"]
                or base != basis["base_commit"]
            ):
                raise MaintenanceStoreError("deployment_release_identity_mismatch")
            attempt = int(
                connection.execute(
                    "SELECT COALESCE(MAX(attempt), 0) + 1 FROM deployment WHERE release_id = ?",
                    (release,),
                ).fetchone()[0]
            )
            connection.execute(
                "INSERT INTO deployment (deployment_id, release_id, attempt, result_commit, "
                "config_version, status, deployed_at, source_commit, base_commit, tested_commit, "
                "remote_name, target_branch) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
                (
                    deployment_id,
                    release,
                    attempt,
                    basis["result_commit"],
                    basis["config_version"],
                    when,
                    source,
                    base,
                    tested,
                    remote,
                    branch,
                ),
            )
        return deployment_id

    def finish_deployment(
        self,
        *,
        deployment_id: str,
        status: str,
        pushed_commit: str | None,
        active_commit: str | None,
        reason_code: str | None,
        finished_at: datetime | str,
        capability: object | None = None,
    ) -> DeploymentRecord:
        """Close a pending attempt without changing its pinned identity."""

        deployment = _safe_id(deployment_id, "deployment_id_invalid")
        if status not in {"deployed", "failed", "blocked", "rolled_back"}:
            raise MaintenanceStoreError("deployment_final_status_invalid")
        pushed = (
            None
            if pushed_commit is None
            else _safe_id(pushed_commit, "pushed_commit_invalid")
        )
        active = (
            None
            if active_commit is None
            else _safe_id(active_commit, "active_commit_invalid")
        )
        reason = (
            None
            if reason_code is None
            else _safe_code(reason_code, "deployment_reason_invalid")
        )
        if status == "deployed":
            if capability is not self._deployment_capability:
                raise MaintenanceStoreError("deployment_capability_invalid")
        elif capability is not None:
            raise MaintenanceStoreError("deployment_capability_unexpected")
        when = _timestamp(finished_at)
        transaction_options = (
            {"deployment_capability": capability} if status == "deployed" else {}
        )
        with self._transaction(**transaction_options) as connection:
            row = connection.execute(
                "SELECT * FROM deployment WHERE deployment_id = ?", (deployment,)
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("deployment_not_found")
            if row["status"] != "pending":
                raise MaintenanceStoreError("deployment_already_final")
            if status == "deployed" and (
                pushed != row["result_commit"] or active != row["result_commit"]
            ):
                raise MaintenanceStoreError("deployment_success_identity_invalid")
            if status == "deployed":
                basis = self._deployment_authorization(
                    connection, str(row["release_id"])
                )
                if (
                    row["result_commit"] != basis["result_commit"]
                    or row["config_version"] != basis["config_version"]
                    or row["source_commit"] != basis["result_commit"]
                    or row["tested_commit"] != basis["result_commit"]
                    or row["base_commit"] != basis["base_commit"]
                    or row["remote_name"] is None
                    or row["target_branch"] is None
                ):
                    raise MaintenanceStoreError("deployment_release_identity_mismatch")
            connection.execute(
                "UPDATE deployment SET status = ?, pushed_commit = ?, active_commit = ?, "
                "reason_code = ?, deployed_at = ? WHERE deployment_id = ? AND status = 'pending'",
                (status, pushed, active, reason, when, deployment),
            )
            final = connection.execute(
                "SELECT * FROM deployment WHERE deployment_id = ?", (deployment,)
            ).fetchone()
            assert final is not None
            return _deployment_from_row(final)

    def get_deployment(self, *, deployment_id: str) -> DeploymentRecord | None:
        deployment = _safe_id(deployment_id, "deployment_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM deployment WHERE deployment_id = ?", (deployment,)
            ).fetchone()
            return None if row is None else _deployment_from_row(row)
        finally:
            connection.close()

    def list_deployments_for_release(
        self, *, release_id: str
    ) -> tuple[DeploymentRecord, ...]:
        """Return deployment attempts for one frozen release, oldest first."""

        release = _safe_id(release_id, "release_id_invalid")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM deployment WHERE release_id = ? ORDER BY attempt",
                (release,),
            ).fetchall()
            return tuple(_deployment_from_row(row) for row in rows)
        finally:
            connection.close()

    def bind_deployment_previous_active(
        self,
        *,
        deployment_id: str,
        lease: LeaseToken,
        previous_active_commit: str,
        recorded_at: datetime | str,
    ) -> DeploymentRecord:
        """Pin the pre-switch commit once so a restarted deployment can roll back."""

        deployment = _safe_id(deployment_id, "deployment_id_invalid")
        previous = _safe_id(
            previous_active_commit, "deployment_previous_active_invalid"
        )
        if re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", previous) is None:
            raise MaintenanceStoreError("deployment_previous_active_invalid")
        when = _timestamp(recorded_at)
        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            row = connection.execute(
                "SELECT * FROM deployment WHERE deployment_id = ?", (deployment,)
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("deployment_not_found")
            if row["status"] != "pending":
                raise MaintenanceStoreError("deployment_not_pending")
            if previous != row["base_commit"]:
                raise MaintenanceStoreError("deployment_previous_active_mismatch")
            existing = row["previous_active_commit"]
            if existing is not None and existing != previous:
                raise MaintenanceStoreError("deployment_previous_active_conflict")
            if existing is None:
                connection.execute(
                    "UPDATE deployment SET previous_active_commit = ? "
                    "WHERE deployment_id = ? AND status = 'pending' "
                    "AND previous_active_commit IS NULL",
                    (previous, deployment),
                )
            updated = connection.execute(
                "SELECT * FROM deployment WHERE deployment_id = ?", (deployment,)
            ).fetchone()
            assert updated is not None
            return _deployment_from_row(updated)

    def begin_deployment_verification(
        self,
        *,
        deployment_id: str,
        target_date: str,
        created_at: datetime | str,
    ) -> DeploymentVerificationRecord:
        """Create the pending slot without launching another daily run."""

        deployment = _safe_id(deployment_id, "deployment_id_invalid")
        target = _target_date(target_date)
        when = _timestamp(created_at)
        with self._transaction() as connection:
            deployed = connection.execute(
                "SELECT status FROM deployment WHERE deployment_id = ?",
                (deployment,),
            ).fetchone()
            if deployed is None:
                raise MaintenanceStoreError("deployment_not_found")
            if deployed["status"] != "deployed":
                raise MaintenanceStoreError("verification_deployment_not_deployed")
            existing = connection.execute(
                "SELECT * FROM deployment_verification WHERE deployment_id = ?",
                (deployment,),
            ).fetchone()
            if existing is not None:
                if existing["target_date"] != target:
                    raise IdempotencyConflictError("verification_target_date_conflict")
                return _deployment_verification_from_row(existing)
            verification_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO deployment_verification "
                "(verification_id, deployment_id, target_date, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (verification_id, deployment, target, when, when),
            )
            row = connection.execute(
                "SELECT * FROM deployment_verification WHERE verification_id = ?",
                (verification_id,),
            ).fetchone()
            assert row is not None
            return _deployment_verification_from_row(row)

    def get_deployment_verification(
        self, *, deployment_id: str
    ) -> DeploymentVerificationRecord | None:
        deployment = _safe_id(deployment_id, "deployment_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM deployment_verification WHERE deployment_id = ?",
                (deployment,),
            ).fetchone()
            return None if row is None else _deployment_verification_from_row(row)
        finally:
            connection.close()

    def record_deployment_verification_observation(
        self,
        *,
        deployment_id: str,
        source_launch_id: str,
        check_id: str,
        active_commit: str | None,
        evidence_ref: str,
        observed_at: datetime | str,
    ) -> DeploymentVerificationRecord:
        """Classify one existing completed daily observation for a deployment."""

        deployment = _safe_id(deployment_id, "deployment_id_invalid")
        source = _safe_id(source_launch_id, "source_launch_id_invalid")
        check = _safe_id(check_id, "check_id_invalid")
        active = (
            None
            if active_commit is None
            else _safe_id(active_commit, "verification_active_commit_invalid")
        )
        evidence = _relative_output_path(evidence_ref)
        when = _timestamp(observed_at)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT v.*, d.active_commit AS deployed_commit, "
                "d.release_id, dl.target_date AS observed_target_date, "
                "dl.status AS launch_status, "
                "dc.check_status, dc.reason_code AS observed_reason_code, "
                "ir.incident_id AS observed_incident_id "
                "FROM deployment_verification v "
                "JOIN deployment d ON d.deployment_id = v.deployment_id "
                "JOIN daily_launch dl ON dl.source_launch_id = ? "
                "JOIN daily_check dc ON dc.check_id = ? "
                "LEFT JOIN incident_run ir ON ir.check_id = dc.check_id "
                "WHERE v.deployment_id = ? AND dl.source_launch_id = dc.source_launch_id",
                (source, check, deployment),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("verification_observation_unavailable")
            if row["status"] != "pending":
                if (
                    row["source_launch_id"] == source
                    and row["check_id"] == check
                    and row["observed_commit"] == active
                    and row["evidence_ref"] == evidence
                    and row["observed_at"] == when
                ):
                    return _deployment_verification_from_row(row)
                raise IdempotencyConflictError("verification_observation_conflict")
            next_date = (
                date.fromisoformat(str(row["target_date"])) + timedelta(days=1)
            ).isoformat()
            if row["observed_target_date"] != next_date:
                raise MaintenanceStoreError("verification_target_date_mismatch")
            if row["launch_status"] == "running":
                raise MaintenanceStoreError("verification_daily_launch_incomplete")
            recurrence_incident = None
            reason_code = None
            original_incident_id = None
            original_reason_code = None
            original_job_count = 0
            if row["check_status"] == "incomplete" and row["launch_status"] == "failed":
                original_jobs = connection.execute(
                    "SELECT incident_id FROM maintenance_job WHERE release_id = ? "
                    "ORDER BY created_at, job_id",
                    (row["release_id"],),
                ).fetchall()
                original_job_count = len(original_jobs)
                if original_job_count == 1:
                    original_incident_id = str(original_jobs[0]["incident_id"])
                    prior_check = connection.execute(
                        "SELECT dc.reason_code FROM incident_run ir "
                        "JOIN daily_check dc ON dc.check_id = ir.check_id "
                        "WHERE ir.incident_id = ? "
                        "ORDER BY dc.observed_at DESC, dc.created_at DESC, dc.check_id DESC "
                        "LIMIT 1",
                        (original_incident_id,),
                    ).fetchone()
                    if prior_check is not None:
                        original_reason_code = prior_check["reason_code"]
            if row["check_status"] == "abnormal":
                status = "recurred"
                recurrence_incident = row["observed_incident_id"]
                if recurrence_incident is None:
                    raise MaintenanceStoreError(
                        "verification_recurrence_incident_missing"
                    )
                reason_code = "next_daily_incident_recurred"
            elif (
                row["check_status"] == "incomplete"
                and row["launch_status"] == "failed"
                and row["observed_reason_code"] is not None
                and row["observed_reason_code"] == original_reason_code
                and original_incident_id is not None
            ):
                status = "recurred"
                recurrence_incident = original_incident_id
                reason_code = "next_daily_reason_recurred"
            elif (
                row["check_status"] == "incomplete"
                and row["launch_status"] == "failed"
                and original_job_count != 1
            ):
                status = "blocked"
                reason_code = (
                    "deployment_original_incident_ambiguous"
                    if original_job_count > 1
                    else "deployment_original_incident_missing"
                )
            elif row["check_status"] in {"normal", "normal_shortfall"}:
                if active is not None and active == row["deployed_commit"]:
                    status = "verified"
                else:
                    status = "blocked"
                    reason_code = "active_commit_unverified"
            else:
                status = "blocked"
                reason_code = (
                    "next_daily_observation_unknown"
                    if row["check_status"] == "unknown"
                    else "next_daily_observation_incomplete"
                )
            connection.execute(
                "UPDATE deployment_verification SET status = ?, source_launch_id = ?, "
                "check_id = ?, observed_commit = ?, evidence_ref = ?, "
                "recurrence_incident_id = ?, reason_code = ?, observed_at = ?, updated_at = ? "
                "WHERE verification_id = ? AND status = 'pending'",
                (
                    status,
                    source,
                    check,
                    active,
                    evidence,
                    recurrence_incident,
                    reason_code,
                    when,
                    when,
                    row["verification_id"],
                ),
            )
            final = connection.execute(
                "SELECT * FROM deployment_verification WHERE verification_id = ?",
                (row["verification_id"],),
            ).fetchone()
            assert final is not None
            return _deployment_verification_from_row(final)

    def mark_deployment_verification_not_observed(
        self,
        *,
        deployment_id: str,
        observed_at: datetime | str,
        reason_code: str = "next_daily_not_observed",
    ) -> DeploymentVerificationRecord:
        """Record that the next natural daily run was absent; never start one."""

        deployment = _safe_id(deployment_id, "deployment_id_invalid")
        when = _timestamp(observed_at)
        reason = _safe_code(reason_code, "verification_reason_invalid")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM deployment_verification WHERE deployment_id = ?",
                (deployment,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("verification_not_found")
            if row["status"] != "pending":
                if row["status"] == "not_observed" and row["observed_at"] == when:
                    return _deployment_verification_from_row(row)
                raise IdempotencyConflictError("verification_observation_conflict")
            next_date = date.fromisoformat(str(row["target_date"])) + timedelta(days=1)
            next_daily_start = datetime.combine(
                next_date,
                time(hour=13),
                tzinfo=ZoneInfo("Asia/Tokyo"),
            )
            if (
                _parse_timestamp(when).astimezone(ZoneInfo("Asia/Tokyo"))
                < next_daily_start
            ):
                raise MaintenanceStoreError("verification_observation_not_due")
            daily_exists = connection.execute(
                "SELECT 1 FROM daily_launch WHERE target_date = ? LIMIT 1",
                (next_date.isoformat(),),
            ).fetchone()
            if daily_exists is not None:
                raise MaintenanceStoreError("verification_daily_run_exists")
            connection.execute(
                "UPDATE deployment_verification SET status = 'not_observed', "
                "reason_code = ?, observed_at = ?, updated_at = ? "
                "WHERE verification_id = ? AND status = 'pending'",
                (reason, when, when, row["verification_id"]),
            )
            final = connection.execute(
                "SELECT * FROM deployment_verification WHERE verification_id = ?",
                (row["verification_id"],),
            ).fetchone()
            assert final is not None
            return _deployment_verification_from_row(final)

    @staticmethod
    def _evaluate_release_gate(
        connection: sqlite3.Connection,
        release_id: str,
        evaluated_at: str,
    ) -> GateResult:
        release = connection.execute(
            "SELECT * FROM release WHERE release_id = ?", (release_id,)
        ).fetchone()
        if release is None:
            raise MaintenanceStoreError("release_not_found")
        reasons: list[str] = []
        if release["state"] != "frozen":
            reasons.append("release_not_frozen")
        required = connection.execute(
            "SELECT case_id FROM release_required_test WHERE release_id = ? ORDER BY sequence",
            (release_id,),
        ).fetchall()
        if not required:
            reasons.append("required_test_set_empty")
        manifest = tuple(str(row["case_id"]) for row in required)
        manifest_hash = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=True, separators=(",", ":")).encode(
                "ascii"
            )
        ).hexdigest()
        if manifest_hash != release["case_manifest_sha256"]:
            reasons.append("required_test_manifest_mismatch")
        for required_row in required:
            case_id = str(required_row["case_id"])
            test = connection.execute(
                "SELECT status, result_version FROM test_ledger "
                "WHERE release_id = ? AND case_id = ? ORDER BY attempt DESC LIMIT 1",
                (release_id, case_id),
            ).fetchone()
            if test is None:
                reasons.append("required_test_missing")
            else:
                if test["status"] != "PASS":
                    reasons.append("required_test_not_pass")
                if test["result_version"] != release["result_version"]:
                    reasons.append("required_test_version_mismatch")

        review = connection.execute(
            "SELECT reviewer_id, reviewed_version, status FROM review_result "
            "WHERE scope_type = 'release' AND scope_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (release_id,),
        ).fetchone()
        if review is None:
            reasons.append("release_review_missing")
        else:
            if review["status"] != "approved":
                reasons.append("release_review_not_approved")
            if review["reviewed_version"] != release["result_version"]:
                reasons.append("release_review_version_mismatch")
            if review["reviewer_id"] == release["author_id"]:
                reasons.append("release_review_not_independent")

        deployment = connection.execute(
            "SELECT result_commit, config_version, status FROM deployment "
            "WHERE release_id = ? ORDER BY attempt DESC LIMIT 1",
            (release_id,),
        ).fetchone()
        if deployment is None:
            reasons.append("deployment_missing")
        else:
            if deployment["status"] != "deployed":
                reasons.append("deployment_not_successful")
            if deployment["result_commit"] != release["result_commit"]:
                reasons.append("deployment_commit_mismatch")
            if deployment["config_version"] != release["config_version"]:
                reasons.append("deployment_config_mismatch")

        reason_codes = tuple(sorted(set(reasons)))
        status = "ready" if not reason_codes else "blocked"
        connection.execute(
            "INSERT INTO release_gate (release_id, status, reasons_json, evaluated_at, result_commit) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(release_id) DO UPDATE SET "
            "status = excluded.status, reasons_json = excluded.reasons_json, "
            "evaluated_at = excluded.evaluated_at, result_commit = excluded.result_commit",
            (
                release_id,
                status,
                json.dumps(reason_codes, separators=(",", ":")),
                evaluated_at,
                release["result_commit"],
            ),
        )
        return GateResult(
            release_id,
            status,
            reason_codes,
            evaluated_at,
            str(release["result_commit"]),
        )

    def evaluate_release_gate(
        self,
        *,
        release_id: str,
        evaluated_at: datetime | str,
    ) -> GateResult:
        release = _safe_id(release_id, "release_id_invalid")
        when = _timestamp(evaluated_at)
        with self._transaction() as connection:
            return self._evaluate_release_gate(connection, release, when)

    def start_fix_job(
        self,
        *,
        incident_id: str,
        release_id: str,
        job_id: str,
        started_at: datetime | str,
        source_run_id: str | None = None,
    ) -> JobStartResult:
        incident = _safe_id(incident_id, "incident_id_invalid")
        release = _safe_id(release_id, "release_id_invalid")
        job = _safe_id(job_id, "job_id_invalid")
        run_id = (
            None
            if source_run_id is None
            else _safe_id(source_run_id, "source_run_id_invalid")
        )
        when = _timestamp(started_at)
        with self._transaction() as connection:
            gate = self._evaluate_release_gate(connection, release, when)
            incident_row = connection.execute(
                "SELECT status FROM incident WHERE incident_id = ?", (incident,)
            ).fetchone()
            if incident_row is None:
                raise MaintenanceStoreError("incident_not_found")
            job_identity = connection.execute(
                "SELECT incident_id, release_id FROM maintenance_job WHERE job_id = ?",
                (job,),
            ).fetchone()
            if job_identity is not None:
                if (
                    job_identity["incident_id"] != incident
                    or job_identity["release_id"] != release
                ):
                    raise IdempotencyConflictError("job_identity_conflict")
                return JobStartResult(False, job, gate.status, ("job_already_exists",))
            connection.execute(
                "INSERT INTO maintenance_job "
                "(job_id, incident_id, release_id, source_run_id, result_version, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
                (
                    job,
                    incident,
                    release,
                    run_id,
                    self._release_version(connection, release),
                    when,
                    when,
                ),
            )
            if incident_row["status"] != "in_progress":
                connection.execute(
                    "UPDATE incident SET status = 'in_progress', updated_at = ? "
                    "WHERE incident_id = ?",
                    (when, incident),
                )
            return JobStartResult(True, job, gate.status, ())

    @staticmethod
    def _release_version(connection: sqlite3.Connection, release_id: str) -> str:
        row = connection.execute(
            "SELECT result_version FROM release WHERE release_id = ?", (release_id,)
        ).fetchone()
        if row is None:
            raise MaintenanceStoreError("release_not_found")
        return str(row["result_version"])

    def acquire_lease(
        self,
        *,
        lease_key: str,
        owner_id: str,
        owner_pid: int,
        owner_start_time: datetime | str,
        now: datetime | str,
        ttl_seconds: int = 90,
        prior_owner_observation: str = "unknown",
    ) -> LeaseToken:
        key = _safe_id(lease_key, "lease_key_invalid")
        owner = _safe_id(owner_id, "lease_owner_invalid")
        start = _timestamp(owner_start_time, "owner_start_time_invalid")
        when = _timestamp(now)
        if (
            isinstance(owner_pid, bool)
            or not isinstance(owner_pid, int)
            or owner_pid < 1
        ):
            raise MaintenanceStoreError("owner_pid_invalid")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 1 <= ttl_seconds <= 300
        ):
            raise MaintenanceStoreError("lease_ttl_invalid")
        if prior_owner_observation not in {"unknown", "alive", "dead", "reused"}:
            raise MaintenanceStoreError("owner_observation_invalid")
        acquired = _parse_timestamp(when)
        expires = _timestamp(acquired + timedelta(seconds=ttl_seconds))
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT * FROM lease WHERE lease_key = ?", (key,)
            ).fetchone()
            if current is not None:
                # A lease row is retained for audit, but an old held row must
                # not prevent a requested local workflow from starting again.
                generation = int(current["generation"]) + 1
            else:
                generation = 1
            connection.execute(
                "INSERT INTO lease "
                "(lease_key, owner_id, owner_pid, owner_start_time, generation, state, "
                "acquired_at, heartbeat_at, expires_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'held', ?, ?, ?, ?) "
                "ON CONFLICT(lease_key) DO UPDATE SET owner_id = excluded.owner_id, "
                "owner_pid = excluded.owner_pid, owner_start_time = excluded.owner_start_time, "
                "generation = excluded.generation, state = 'held', acquired_at = excluded.acquired_at, "
                "heartbeat_at = excluded.heartbeat_at, expires_at = excluded.expires_at, "
                "updated_at = excluded.updated_at",
                (key, owner, owner_pid, start, generation, when, when, expires, when),
            )
        return LeaseToken(key, owner, generation)

    def get_lease(self, lease_key: str) -> LeaseRecord | None:
        key = _safe_id(lease_key, "lease_key_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT lease_key, owner_id, owner_pid, owner_start_time, generation, "
                "state, expires_at FROM lease WHERE lease_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            return LeaseRecord(
                lease_key=str(row["lease_key"]),
                owner_id=str(row["owner_id"]),
                owner_pid=int(row["owner_pid"]),
                owner_start_time=str(row["owner_start_time"]),
                generation=int(row["generation"]),
                state=str(row["state"]),
                expires_at=str(row["expires_at"]),
            )
        finally:
            connection.close()

    @staticmethod
    def _assert_lease(
        connection: sqlite3.Connection,
        lease: LeaseToken,
        now: str,
    ) -> None:
        # Leases remain useful as audit observations, but must not gate a
        # user-directed local workflow or its later stages.
        _ = connection, lease, now

    def heartbeat_lease(
        self,
        *,
        lease: LeaseToken,
        now: datetime | str,
        ttl_seconds: int = 90,
    ) -> LeaseToken:
        when = _timestamp(now)
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 1 <= ttl_seconds <= 300
        ):
            raise MaintenanceStoreError("lease_ttl_invalid")
        expires = _timestamp(_parse_timestamp(when) + timedelta(seconds=ttl_seconds))
        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            connection.execute(
                "UPDATE lease SET heartbeat_at = ?, expires_at = ?, updated_at = ? "
                "WHERE lease_key = ? AND generation = ?",
                (when, expires, when, lease.lease_key, lease.generation),
            )
        return lease

    def release_lease(self, *, lease: LeaseToken, released_at: datetime | str) -> None:
        when = _timestamp(released_at)
        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            connection.execute(
                "UPDATE lease SET state = 'released', updated_at = ? "
                "WHERE lease_key = ? AND owner_id = ? AND generation = ?",
                (when, lease.lease_key, lease.owner_id, lease.generation),
            )

    def begin_step_attempt(
        self,
        *,
        job_id: str,
        step_key: str,
        sequence: int,
        lease: LeaseToken,
        started_at: datetime | str,
        repair_predecessor_attempt_id: str | None = None,
        repair_predecessor_result_version: str | None = None,
        repair_predecessor_output_sha256: str | None = None,
    ) -> StepAttemptResult:
        job = _safe_id(job_id, "job_id_invalid")
        step = _safe_code(step_key, "step_key_invalid")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise MaintenanceStoreError("step_sequence_invalid")
        when = _timestamp(started_at)
        predecessor_values = (
            repair_predecessor_attempt_id,
            repair_predecessor_result_version,
            repair_predecessor_output_sha256,
        )
        if step == "maintenance_repair":
            if sequence != 3:
                raise MaintenanceStoreError("repair_step_sequence_invalid")
            if all(value is not None for value in predecessor_values):
                repair_predecessor_attempt_id = _safe_id(
                    repair_predecessor_attempt_id, "repair_predecessor_pin_invalid"
                )
                repair_predecessor_result_version = _safe_id(
                    repair_predecessor_result_version, "repair_predecessor_pin_invalid"
                )
                if not isinstance(repair_predecessor_output_sha256, str) or not (
                    _SHA256_PATTERN.fullmatch(repair_predecessor_output_sha256)
                ):
                    raise MaintenanceStoreError("repair_predecessor_pin_invalid")
            elif any(value is not None for value in predecessor_values):
                repair_predecessor_attempt_id = None
                repair_predecessor_result_version = None
                repair_predecessor_output_sha256 = None
        elif any(value is not None for value in predecessor_values):
            raise MaintenanceStoreError("repair_predecessor_pin_invalid")
        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            job_row = connection.execute(
                "SELECT source_run_id, status, lease_key, lease_generation, resume_count "
                "FROM maintenance_job WHERE job_id = ?",
                (job,),
            ).fetchone()
            if job_row is None:
                raise MaintenanceStoreError("job_not_found")
            if job_row["status"] not in {"queued", "running"}:
                raise MaintenanceStoreError("job_not_active")
            if step == "maintenance_repair" and repair_predecessor_attempt_id is not None:
                predecessor = connection.execute(
                    "SELECT s.attempt_id, s.step_key, s.status AS attempt_status, "
                    "s.result_version, s.output_sha256, r.reviewer_id, "
                    "r.reviewed_version, r.status AS review_status, "
                    "release.author_id "
                    "FROM step_attempt s LEFT JOIN review_result r "
                    "ON r.scope_type = 'step_attempt' AND r.scope_id = s.attempt_id "
                    "JOIN maintenance_job job ON job.job_id = s.job_id "
                    "JOIN release ON release.release_id = job.release_id "
                    "WHERE s.job_id = ? AND s.sequence = 2 "
                    "ORDER BY s.attempt_no DESC, r.rowid DESC LIMIT 1",
                    (job,),
                ).fetchone()
                if (
                    predecessor is None
                    or predecessor["attempt_id"] != repair_predecessor_attempt_id
                    or predecessor["step_key"] != "maintenance_planning"
                    or predecessor["result_version"]
                    != repair_predecessor_result_version
                    or predecessor["output_sha256"] != repair_predecessor_output_sha256
                ):
                    raise MaintenanceStoreError("repair_predecessor_pin_mismatch")
            connection.execute(
                "UPDATE maintenance_job SET lease_key = ?, lease_generation = ?, "
                "updated_at = ? WHERE job_id = ?",
                (lease.lease_key, lease.generation, when, job),
            )
            latest = connection.execute(
                "SELECT * FROM step_attempt WHERE job_id = ? AND step_key = ? "
                "ORDER BY attempt_no DESC LIMIT 1",
                (job, step),
            ).fetchone()
            if latest is not None and int(latest["sequence"]) != sequence:
                raise MaintenanceStoreError("step_sequence_conflict")
            if (
                latest is not None
                and step == "maintenance_repair"
                and repair_predecessor_attempt_id is not None
                and (
                    latest["repair_predecessor_attempt_id"]
                    != repair_predecessor_attempt_id
                    or latest["repair_predecessor_result_version"]
                    != repair_predecessor_result_version
                    or latest["repair_predecessor_output_sha256"]
                    != repair_predecessor_output_sha256
                )
            ):
                raise MaintenanceStoreError("repair_predecessor_pin_mismatch")
            if latest is not None and latest["status"] in _STEP_TERMINAL_STATUSES:
                # Prior terminal outcomes are history, not a gate on an
                # explicitly requested local continuation.
                pass
            if latest is not None and latest["status"] == "running":
                if (
                    latest["lease_key"] == lease.lease_key
                    and int(latest["lease_generation"]) == lease.generation
                ):
                    return StepAttemptResult(
                        str(latest["attempt_id"]),
                        int(latest["attempt_no"]),
                        str(latest["status"]),
                        True,
                    )
                connection.execute(
                    "UPDATE step_attempt SET status = 'interrupted', finished_at = ? "
                    "WHERE attempt_id = ? AND status = 'running'",
                    (when, latest["attempt_id"]),
                )
            prior_sequence = connection.execute(
                "SELECT step_key FROM step_attempt WHERE job_id = ? AND sequence = ? "
                "AND step_key <> ? LIMIT 1",
                (job, sequence, step),
            ).fetchone()
            if prior_sequence is not None:
                raise MaintenanceStoreError("step_sequence_conflict")
            attempt_no = 1 if latest is None else int(latest["attempt_no"]) + 1
            attempt_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO step_attempt "
                "(attempt_id, job_id, step_key, sequence, attempt_no, source_run_id, "
                "lease_key, lease_generation, status, started_at, "
                "repair_predecessor_attempt_id, repair_predecessor_result_version, "
                "repair_predecessor_output_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)",
                (
                    attempt_id,
                    job,
                    step,
                    sequence,
                    attempt_no,
                    job_row["source_run_id"],
                    lease.lease_key,
                    lease.generation,
                    when,
                    repair_predecessor_attempt_id,
                    repair_predecessor_result_version,
                    repair_predecessor_output_sha256,
                ),
            )
            connection.execute(
                "UPDATE maintenance_job SET status = 'running', updated_at = ? WHERE job_id = ?",
                (when, job),
            )
            return StepAttemptResult(attempt_id, attempt_no, "running", False)

    def get_repair_step_predecessor(
        self, *, attempt_id: str
    ) -> dict[str, str | None]:
        """Return available predecessor references without gating continuation."""

        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT repair_predecessor_attempt_id, "
                "repair_predecessor_result_version, "
                "repair_predecessor_output_sha256 FROM step_attempt "
                "WHERE attempt_id = ? AND step_key = 'maintenance_repair' "
                "AND sequence = 3",
                (attempt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("repair_attempt_not_found")
            return {
                "attempt_id": (
                    str(row["repair_predecessor_attempt_id"])
                    if row["repair_predecessor_attempt_id"] is not None
                    else None
                ),
                "result_version": (
                    str(row["repair_predecessor_result_version"])
                    if row["repair_predecessor_result_version"] is not None
                    else None
                ),
                "output_sha256": (
                    str(row["repair_predecessor_output_sha256"])
                    if row["repair_predecessor_output_sha256"] is not None
                    else None
                ),
            }
        finally:
            connection.close()

    def complete_step_attempt(
        self,
        *,
        attempt_id: str,
        lease: LeaseToken,
        result_version: str,
        completed_at: datetime | str,
        output_path: str | None = None,
        output_sha256: str | None = None,
    ) -> None:
        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        version = _safe_id(result_version, "result_version_invalid")
        when = _timestamp(completed_at)
        if (output_path is None) != (output_sha256 is None):
            raise MaintenanceStoreError("attempt_output_incomplete")
        output = None if output_path is None else _relative_output_path(output_path)
        digest = output_sha256
        if digest is not None and (
            not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest)
        ):
            raise MaintenanceStoreError("attempt_output_digest_invalid")
        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            row = connection.execute(
                "SELECT s.status, s.lease_key, s.lease_generation, s.job_id, "
                "s.step_key, j.status AS job_status "
                "FROM step_attempt s JOIN maintenance_job j ON j.job_id = s.job_id "
                "WHERE s.attempt_id = ?",
                (attempt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("step_attempt_not_found")
            if row["job_status"] not in {"queued", "running"}:
                raise MaintenanceStoreError("job_not_active")
            if row["status"] != "running":
                raise MaintenanceStoreError("step_attempt_not_running")
            if row["step_key"] == "maintenance_investigation" and (
                output is None or output != f"{row['job_id']}/{attempt}/result.json"
            ):
                raise MaintenanceStoreError("investigation_output_path_required")
            if (
                row["lease_key"] != lease.lease_key
                or int(row["lease_generation"]) != lease.generation
            ):
                raise MaintenanceStoreError("step_attempt_lease_mismatch")
            connection.execute(
                "UPDATE step_attempt SET status = 'complete', result_version = ?, "
                "output_path = ?, output_sha256 = ?, finished_at = ? WHERE attempt_id = ?",
                (version, output, digest, when, attempt),
            )

    def bind_running_repair_artifact(
        self,
        *,
        attempt_id: str,
        lease: LeaseToken,
        output_path: str,
        output_sha256: str,
        recorded_at: datetime | str,
    ) -> None:
        """Pin the immutable repair implementation artifact while its attempt runs."""

        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        when = _timestamp(recorded_at)
        output = _relative_output_path(output_path)
        if not isinstance(output_sha256, str) or not _SHA256_PATTERN.fullmatch(
            output_sha256
        ):
            raise MaintenanceStoreError("attempt_output_digest_invalid")
        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            row = connection.execute(
                "SELECT s.status, s.lease_key, s.lease_generation, s.job_id, "
                "s.step_key, s.sequence, s.output_path, s.output_sha256, "
                "j.status AS job_status FROM step_attempt s "
                "JOIN maintenance_job j ON j.job_id = s.job_id "
                "WHERE s.attempt_id = ?",
                (attempt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("step_attempt_not_found")
            if row["job_status"] not in {"queued", "running"}:
                raise MaintenanceStoreError("job_not_active")
            if row["status"] != "running":
                raise MaintenanceStoreError("step_attempt_not_running")
            if row["step_key"] != "maintenance_repair" or int(row["sequence"]) != 3:
                raise MaintenanceStoreError("repair_attempt_identity_invalid")
            expected_path = f"{row['job_id']}/{attempt}/implementation-result.json"
            if output != expected_path:
                raise MaintenanceStoreError("repair_artifact_path_invalid")
            if (
                row["lease_key"] != lease.lease_key
                or int(row["lease_generation"]) != lease.generation
            ):
                raise MaintenanceStoreError("step_attempt_lease_mismatch")
            if row["output_path"] is not None or row["output_sha256"] is not None:
                if (
                    row["output_path"] == output
                    and row["output_sha256"] == output_sha256
                ):
                    return
                raise MaintenanceStoreError("repair_artifact_binding_conflict")
            connection.execute(
                "UPDATE step_attempt SET output_path = ?, output_sha256 = ? "
                "WHERE attempt_id = ? AND status = 'running'",
                (output, output_sha256, attempt),
            )

    def validate_step_attempt_lease(
        self,
        *,
        attempt_id: str,
        lease: LeaseToken,
        at: datetime | str,
        author_id: str | None = None,
        reviewer_id: str | None = None,
    ) -> None:
        """Fail before side effects if an attempt no longer owns a live lease."""

        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        when = _timestamp(at)
        author = (
            _safe_id(author_id, "step_author_id_invalid")
            if author_id is not None
            else None
        )
        reviewer = (
            _safe_id(reviewer_id, "reviewer_id_invalid")
            if reviewer_id is not None
            else None
        )
        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            row = connection.execute(
                "SELECT s.status, s.lease_key, s.lease_generation, "
                "j.status AS job_status, j.lease_key AS job_lease_key, "
                "j.lease_generation AS job_lease_generation, r.author_id, "
                "s.job_id, s.step_key, s.sequence "
                "FROM step_attempt s JOIN maintenance_job j ON j.job_id = s.job_id "
                "JOIN release r ON r.release_id = j.release_id "
                "WHERE s.attempt_id = ?",
                (attempt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("step_attempt_not_found")
            if row["job_status"] not in {"queued", "running"}:
                raise MaintenanceStoreError("job_not_active")
            if row["status"] != "running":
                raise MaintenanceStoreError("step_attempt_not_running")
            if (
                row["lease_key"] != lease.lease_key
                or int(row["lease_generation"]) != lease.generation
                or row["job_lease_key"] != lease.lease_key
                or row["job_lease_generation"] is None
                or int(row["job_lease_generation"]) != lease.generation
            ):
                raise MaintenanceStoreError("step_attempt_lease_mismatch")
            if author is not None and author != row["author_id"]:
                raise MaintenanceStoreError("step_author_identity_mismatch")
            if reviewer is not None:
                if reviewer == row["author_id"]:
                    raise MaintenanceStoreError("reviewer_must_be_independent")

    def complete_step_attempt_with_review(
        self,
        *,
        attempt_id: str,
        lease: LeaseToken,
        result_version: str,
        completed_at: datetime | str,
        output_path: str,
        output_sha256: str,
        reviewer_id: str,
        reviewed_version: str,
        review_status: str,
        reviewed_at: datetime | str,
        review_round: int | None = None,
        repair_review_receipt_id: str | None = None,
        repair_review_expected: Mapping[str, object] | None = None,
    ) -> str:
        """Atomically finish a step and persist its review gate record."""

        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        version = _safe_id(result_version, "result_version_invalid")
        reviewer = _safe_id(reviewer_id, "reviewer_id_invalid")
        reviewed = _safe_id(reviewed_version, "reviewed_version_invalid")
        if version != reviewed:
            raise MaintenanceStoreError("step_review_version_mismatch")
        if review_status not in _REVIEW_STATUSES:
            raise MaintenanceStoreError("review_status_invalid")
        output = _relative_output_path(output_path)
        if not isinstance(output_sha256, str) or not _SHA256_PATTERN.fullmatch(
            output_sha256
        ):
            raise MaintenanceStoreError("attempt_output_digest_invalid")
        completed = _timestamp(completed_at)
        reviewed_time = _timestamp(reviewed_at)
        lease_checked_at = max(completed, reviewed_time)
        review_id = str(uuid.uuid4())
        if repair_review_expected is not None and not isinstance(
            repair_review_expected, Mapping
        ):
            raise MaintenanceStoreError("repair_review_receipt_invalid")
        with self._transaction(
            repair_review_capability=self._repair_review_capability
        ) as connection:
            self._assert_lease(connection, lease, lease_checked_at)
            row = connection.execute(
                "SELECT s.status, s.lease_key, s.lease_generation, s.job_id, "
                "s.step_key, s.sequence, j.status AS job_status, "
                "r.author_id FROM step_attempt s "
                "JOIN maintenance_job j ON j.job_id = s.job_id "
                "JOIN release r ON r.release_id = j.release_id "
                "WHERE s.attempt_id = ?",
                (attempt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("step_attempt_not_found")
            if row["job_status"] not in {"queued", "running"}:
                raise MaintenanceStoreError("job_not_active")
            if row["status"] != "running":
                raise MaintenanceStoreError("step_attempt_not_running")
            if (
                row["step_key"] == "maintenance_investigation"
                and output != f"{row['job_id']}/{attempt}/result.json"
            ):
                raise MaintenanceStoreError("investigation_output_path_required")
            if (
                row["lease_key"] != lease.lease_key
                or int(row["lease_generation"]) != lease.generation
            ):
                raise MaintenanceStoreError("step_attempt_lease_mismatch")
            if reviewer == row["author_id"]:
                raise MaintenanceStoreError("reviewer_must_be_independent")
            review_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM review_result r "
                    "JOIN step_attempt s ON s.attempt_id = r.scope_id "
                    "WHERE r.scope_type = 'step_attempt' AND s.job_id = ? "
                    "AND s.step_key = ? AND s.sequence = ?",
                    (row["job_id"], row["step_key"], row["sequence"]),
                ).fetchone()[0]
            )
            if row["step_key"] == "maintenance_investigation" and (
                isinstance(review_round, bool)
                or not isinstance(review_round, int)
                or review_round != review_count + 1
            ):
                raise MaintenanceStoreError("review_round_mismatch")
            if review_round is not None and (
                isinstance(review_round, bool)
                or not isinstance(review_round, int)
                or review_round != review_count + 1
            ):
                raise MaintenanceStoreError("review_round_mismatch")
            if (
                row["step_key"] == "maintenance_repair"
                and repair_review_expected is not None
            ):
                if review_status == "approved":
                    if repair_review_receipt_id is None:
                        raise MaintenanceStoreError("repair_review_receipt_required")
                    receipt_id = _safe_id(
                        repair_review_receipt_id, "repair_review_receipt_id_invalid"
                    )
                    receipt_row = connection.execute(
                        "SELECT * FROM repair_review_receipt WHERE receipt_id = ?",
                        (receipt_id,),
                    ).fetchone()
                    if receipt_row is None:
                        raise MaintenanceStoreError("repair_review_receipt_unavailable")
                    receipt = self._repair_review_receipt_document(receipt_row)
                    current_author_role = self._resolve_repair_identity_role(
                        connection, str(receipt["author_id"])
                    )
                    current_reviewer_role = self._resolve_repair_identity_role(
                        connection, str(receipt["reviewer_id"])
                    )
                    comparisons = {
                        "receipt_id": receipt_id,
                        "attempt_id": attempt,
                        "job_id": str(row["job_id"]),
                        "result_version": version,
                        "author_id": str(row["author_id"]),
                        "reviewer_id": reviewer,
                        "author_role": "implementer",
                        "reviewer_role": "read_only_reviewer",
                        "reviewed_output_sha256": repair_review_expected.get(
                            "reviewed_output_sha256"
                        ),
                        "code_sha256": repair_review_expected.get("code_sha256"),
                        "patch_sha256": repair_review_expected.get("patch_sha256"),
                        "test_ledger_sha256": repair_review_expected.get(
                            "test_ledger_sha256"
                        ),
                        "host_receipt_id": repair_review_expected.get(
                            "host_receipt_id"
                        ),
                        "receipt_sha256": repair_review_expected.get("receipt_sha256"),
                        "consumed": False,
                    }
                    if (
                        any(
                            receipt.get(key) != value
                            for key, value in comparisons.items()
                        )
                        or receipt["author_id"] == receipt["reviewer_id"]
                        or current_author_role != "implementer"
                        or current_reviewer_role != "read_only_reviewer"
                    ):
                        raise MaintenanceStoreError("repair_review_receipt_mismatch")
                    connection.execute(
                        "UPDATE repair_review_receipt SET consumed_at = ? "
                        "WHERE receipt_id = ? AND consumed_at IS NULL",
                        (reviewed_time, receipt_id),
                    )
                elif review_status == "changes_requested":
                    if repair_review_receipt_id is not None:
                        raise MaintenanceStoreError("repair_review_receipt_unexpected")
                    host_receipt_id = _safe_id(
                        repair_review_expected.get("host_receipt_id"),
                        "repair_review_host_receipt_invalid",
                    )
                    execution_row = connection.execute(
                        "SELECT * FROM repair_host_review_execution "
                        "WHERE host_receipt_id = ?",
                        (host_receipt_id,),
                    ).fetchone()
                    if execution_row is None:
                        raise MaintenanceStoreError(
                            "repair_host_review_execution_unavailable"
                        )
                    execution = self._repair_host_review_execution_document(
                        execution_row
                    )
                    current_author_role = self._resolve_repair_identity_role(
                        connection, str(execution["author_id"])
                    )
                    current_reviewer_role = self._resolve_repair_identity_role(
                        connection, str(execution["reviewer_id"])
                    )
                    expected_findings = repair_review_expected.get(
                        "unresolved_critical_findings"
                    )
                    comparisons = {
                        "host_receipt_id": host_receipt_id,
                        "attempt_id": attempt,
                        "job_id": str(row["job_id"]),
                        "result_version": version,
                        "author_id": str(row["author_id"]),
                        "reviewer_id": reviewer,
                        "reviewer_session_key": repair_review_expected.get(
                            "reviewer_session_key"
                        ),
                        "reviewer_turn_id": repair_review_expected.get(
                            "reviewer_turn_id"
                        ),
                        "reviewer_output_sha256": repair_review_expected.get(
                            "reviewer_output_sha256"
                        ),
                        "reviewed_output_sha256": repair_review_expected.get(
                            "reviewed_output_sha256"
                        ),
                        "code_sha256": repair_review_expected.get("code_sha256"),
                        "patch_sha256": repair_review_expected.get("patch_sha256"),
                        "test_ledger_sha256": repair_review_expected.get(
                            "test_ledger_sha256"
                        ),
                        "approved": False,
                        "unresolved_critical_findings": expected_findings,
                    }
                    if (
                        not isinstance(expected_findings, list)
                        or not expected_findings
                        or any(
                            execution.get(key) != value
                            for key, value in comparisons.items()
                        )
                        or current_author_role != "implementer"
                        or current_reviewer_role != "read_only_reviewer"
                    ):
                        raise MaintenanceStoreError("repair_review_execution_mismatch")
                else:
                    raise MaintenanceStoreError("repair_review_receipt_required")
            elif (
                repair_review_receipt_id is not None
                or repair_review_expected is not None
            ):
                raise MaintenanceStoreError("repair_review_receipt_unexpected")
            connection.execute(
                "UPDATE step_attempt SET status = 'complete', result_version = ?, "
                "output_path = ?, output_sha256 = ?, finished_at = ? "
                "WHERE attempt_id = ?",
                (version, output, output_sha256, completed, attempt),
            )
            connection.execute(
                "INSERT INTO review_result "
                "(review_id, scope_type, scope_id, reviewer_id, reviewed_version, status, reviewed_at) "
                "VALUES (?, 'step_attempt', ?, ?, ?, ?, ?)",
                (review_id, attempt, reviewer, reviewed, review_status, reviewed_time),
            )
        return review_id

    def record_step_review(
        self,
        *,
        attempt_id: str,
        reviewer_id: str,
        reviewed_version: str,
        status: str,
        reviewed_at: datetime | str,
    ) -> str:
        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        reviewer = _safe_id(reviewer_id, "reviewer_id_invalid")
        version = _safe_id(reviewed_version, "reviewed_version_invalid")
        if status not in _REVIEW_STATUSES:
            raise MaintenanceStoreError("review_status_invalid")
        review_id = str(uuid.uuid4())
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT r.author_id, s.job_id, s.step_key, s.sequence, "
                "s.status AS attempt_status, "
                "s.result_version AS attempt_result_version FROM step_attempt s "
                "JOIN maintenance_job j ON j.job_id = s.job_id "
                "JOIN release r ON r.release_id = j.release_id "
                "WHERE s.attempt_id = ?",
                (attempt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("step_attempt_not_found")
            if row["attempt_status"] == "handoff_accepted":
                raise MaintenanceStoreError("step_handoff_already_accepted")
            if row["attempt_status"] != "complete":
                raise MaintenanceStoreError("step_attempt_not_complete")
            if row["attempt_result_version"] != version:
                raise MaintenanceStoreError("step_review_version_mismatch")
            if reviewer == row["author_id"]:
                raise MaintenanceStoreError("reviewer_must_be_independent")
            connection.execute(
                "INSERT INTO review_result "
                "(review_id, scope_type, scope_id, reviewer_id, reviewed_version, status, reviewed_at) "
                "VALUES (?, 'step_attempt', ?, ?, ?, ?, ?)",
                (
                    review_id,
                    attempt,
                    reviewer,
                    version,
                    status,
                    _timestamp(reviewed_at),
                ),
            )
        return review_id

    def accept_handoff(
        self,
        payload: object,
        *,
        handoff_root: str | Path,
        lease: LeaseToken,
        accepted_at: datetime | str,
        resume_completed_u12: bool = False,
    ) -> HandoffResult:
        if not isinstance(payload, dict) or set(payload) != _HANDOFF_KEYS:
            raise HandoffContractError("handoff_envelope_invalid")
        if payload["schema_version"] != "maintenance-handoff-v1":
            raise HandoffContractError("handoff_schema_version_mismatch")
        incident = _safe_id(payload["incident_id"], "handoff_incident_id_invalid")
        job = _safe_id(payload["job_id"], "handoff_job_id_invalid")
        attempt = _safe_id(payload["attempt_id"], "handoff_attempt_id_invalid")
        run_id = (
            None
            if payload["source_run_id"] is None
            else _safe_id(payload["source_run_id"], "handoff_source_run_id_invalid")
        )
        result_version = _safe_id(
            payload["result_version"], "handoff_result_version_invalid"
        )
        reviewed_version = _safe_id(
            payload["reviewed_version"], "handoff_reviewed_version_invalid"
        )
        output = payload["output"]
        if (
            not isinstance(output, dict)
            or not {"path", "sha256"}.issubset(output)
            or set(output) - {"path", "sha256", "artifacts"}
        ):
            raise HandoffContractError("handoff_output_invalid")
        output_path = _relative_output_path(output["path"])
        output_sha256 = output["sha256"]
        if not isinstance(output_sha256, str) or not _SHA256_PATTERN.fullmatch(
            output_sha256
        ):
            raise HandoffContractError("handoff_output_digest_invalid")
        artifacts = output.get("artifacts")
        if artifacts is not None:
            if not isinstance(artifacts, dict) or set(artifacts) != {
                "facts",
                "report",
            }:
                raise HandoffContractError("handoff_artifacts_invalid")
            normalized_artifacts: dict[str, dict[str, str]] = {}
            for key, filename in (
                ("facts", "facts.json"),
                ("report", "investigation.md"),
            ):
                item = artifacts[key]
                if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                    raise HandoffContractError("handoff_artifacts_invalid")
                item_path = _relative_output_path(item["path"])
                item_digest = item["sha256"]
                if (
                    not isinstance(item_digest, str)
                    or not _SHA256_PATTERN.fullmatch(item_digest)
                    or item_path != f"{job}/{attempt}/{filename}"
                ):
                    raise HandoffContractError("handoff_artifacts_invalid")
                normalized_artifacts[key] = {
                    "path": item_path,
                    "sha256": item_digest,
                }
        else:
            normalized_artifacts = {}
        when = _timestamp(accepted_at)

        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            row = connection.execute(
                "SELECT s.*, j.incident_id, j.source_run_id AS job_source_run_id, "
                "j.status AS job_status, j.lease_key AS job_lease_key, "
                "j.lease_generation AS job_lease_generation, "
                "r.author_id "
                "FROM step_attempt s "
                "JOIN maintenance_job j ON j.job_id = s.job_id "
                "JOIN release r ON r.release_id = j.release_id "
                "WHERE s.attempt_id = ?",
                (attempt,),
            ).fetchone()
            if row is None:
                raise HandoffContractError("handoff_attempt_not_found")
            if row["job_status"] not in {"queued", "running"}:
                raise HandoffContractError("handoff_job_not_active")
            u12_investigation = row["step_key"] == "maintenance_investigation"
            if resume_completed_u12 and not u12_investigation:
                raise HandoffContractError("handoff_resume_not_supported")
            if resume_completed_u12:
                if (
                    row["job_lease_key"] != lease.lease_key
                    or row["job_lease_generation"] is None
                    or lease.generation < int(row["job_lease_generation"])
                    or row["lease_key"] != row["job_lease_key"]
                    or int(row["lease_generation"]) > int(row["job_lease_generation"])
                ):
                    raise HandoffContractError("handoff_job_lease_mismatch")
            elif (
                row["job_lease_key"] != lease.lease_key
                or row["job_lease_generation"] is None
                or int(row["job_lease_generation"]) != lease.generation
                or row["lease_key"] != row["job_lease_key"]
                or int(row["lease_generation"]) > int(row["job_lease_generation"])
            ):
                raise HandoffContractError("handoff_job_lease_mismatch")
            latest_attempt = connection.execute(
                "SELECT attempt_id FROM step_attempt WHERE job_id = ? AND step_key = ? "
                "ORDER BY attempt_no DESC LIMIT 1",
                (job, row["step_key"]),
            ).fetchone()
            if latest_attempt is None or latest_attempt["attempt_id"] != attempt:
                raise HandoffContractError("handoff_attempt_not_current")
            if row["incident_id"] != incident:
                raise HandoffContractError("handoff_incident_mismatch")
            if row["job_id"] != job:
                raise HandoffContractError("handoff_job_mismatch")
            if u12_investigation and output_path != f"{job}/{attempt}/result.json":
                raise HandoffContractError("handoff_output_path_invalid")
            if u12_investigation and not normalized_artifacts:
                raise HandoffContractError("handoff_artifacts_missing")
            if row["source_run_id"] != run_id or row["job_source_run_id"] != run_id:
                raise HandoffContractError("handoff_source_run_mismatch")
            acceptable_statuses = (
                {"complete", "handoff_accepted"}
                if resume_completed_u12
                else {"complete"}
            )
            if row["status"] not in acceptable_statuses:
                raise HandoffContractError("handoff_attempt_not_complete")
            if row["result_version"] != result_version:
                raise HandoffContractError("handoff_result_version_mismatch")
            if (
                row["output_path"] != output_path
                or row["output_sha256"] != output_sha256
            ):
                raise HandoffContractError("handoff_output_identity_mismatch")
            if reviewed_version != result_version:
                raise HandoffContractError("handoff_review_version_mismatch")
            review = connection.execute(
                "SELECT reviewer_id, reviewed_version, status FROM review_result "
                "WHERE scope_type = 'step_attempt' AND scope_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (attempt,),
            ).fetchone()
            if review is not None and review["reviewed_version"] != reviewed_version:
                raise HandoffContractError("handoff_review_version_mismatch")

            requested_root = Path(handoff_root).expanduser()
            try:
                if _has_reparse_point(requested_root):
                    raise HandoffContractError("handoff_root_reparse_point")
                root = requested_root.resolve(strict=True)
            except HandoffContractError:
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                raise HandoffContractError("handoff_root_unavailable") from exc
            if not root.is_dir():
                raise HandoffContractError("handoff_root_unavailable")
            if u12_investigation:
                if output_path != f"{job}/{attempt}/result.json":
                    raise HandoffContractError("handoff_output_path_invalid")
                manifest = connection.execute(
                    "SELECT source_launch_id, target_date FROM job_input_manifest "
                    "WHERE job_id = ?",
                    (job,),
                ).fetchone()
                if manifest is None:
                    raise HandoffContractError("handoff_manifest_missing")
                for name, item in normalized_artifacts.items():
                    _read_handoff_artifact(
                        root,
                        item["path"],
                        item["sha256"],
                        f"handoff_{name}",
                    )
            output_content = _read_handoff_artifact(
                root, output_path, output_sha256, "handoff_output"
            )
            if u12_investigation:
                try:
                    result_document = json.loads(output_content)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise HandoffContractError("handoff_result_invalid") from exc
                review_document = (
                    result_document.get("review")
                    if isinstance(result_document, dict)
                    else None
                )
                expected_identity = {
                    "incident_id": incident,
                    "source_launch_id": manifest["source_launch_id"],
                    "source_run_id": run_id,
                    "target_date": manifest["target_date"],
                    "job_id": job,
                }
                if (
                    not isinstance(result_document, dict)
                    or result_document.get("schema_version")
                    != "maintenance-investigation-result-v1"
                    or result_document.get("attempt_id") != attempt
                    or result_document.get("result_version") != result_version
                    or result_document.get("investigation_status") != "complete"
                    or result_document.get("next_action") != "handoff"
                    or not isinstance(review_document, dict)
                    or review_document.get("reviewed_version") != result_version
                ):
                    raise HandoffContractError("handoff_result_invalid")
                identity = result_document.get("identity")
                if not isinstance(identity, dict) or any(
                    identity.get(key) != value
                    for key, value in expected_identity.items()
                ):
                    raise HandoffContractError("handoff_result_identity_mismatch")
                for key in ("facts", "report"):
                    if result_document.get(key) != normalized_artifacts[key]:
                        raise HandoffContractError("handoff_result_artifacts_mismatch")
            existing_handoff = connection.execute(
                "SELECT source_run_id, result_version, output_path, output_sha256, "
                "accepted_at FROM handoff_artifact WHERE attempt_id = ?",
                (attempt,),
            ).fetchone()
            if row["status"] == "handoff_accepted":
                if (
                    not resume_completed_u12
                    or existing_handoff is None
                    or existing_handoff["source_run_id"] != run_id
                    or existing_handoff["result_version"] != result_version
                    or existing_handoff["output_path"] != output_path
                    or existing_handoff["output_sha256"] != output_sha256
                ):
                    raise HandoffContractError("handoff_accepted_identity_mismatch")
                if lease.generation > int(row["job_lease_generation"]):
                    connection.execute(
                        "UPDATE maintenance_job SET lease_generation = ?, "
                        "resume_count = resume_count + 1, updated_at = ? WHERE job_id = ?",
                        (lease.generation, when, job),
                    )
                return HandoffResult(
                    True,
                    attempt,
                    job,
                    incident,
                    run_id,
                    result_version,
                    output_path,
                    output_sha256,
                )
            if existing_handoff is not None:
                raise HandoffContractError("handoff_existing_record_for_incomplete")
            if resume_completed_u12 and lease.generation > int(
                row["job_lease_generation"]
            ):
                connection.execute(
                    "UPDATE maintenance_job SET lease_generation = ?, "
                    "resume_count = resume_count + 1, updated_at = ? WHERE job_id = ?",
                    (lease.generation, when, job),
                )
            connection.execute(
                "INSERT INTO handoff_artifact "
                "(attempt_id, source_run_id, result_version, output_path, output_sha256, accepted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (attempt, run_id, result_version, output_path, output_sha256, when),
            )
            connection.execute(
                "UPDATE step_attempt SET status = 'handoff_accepted', finished_at = ? "
                "WHERE attempt_id = ? AND status = 'complete'",
                (when, attempt),
            )
            return HandoffResult(
                True,
                attempt,
                job,
                incident,
                run_id,
                result_version,
                output_path,
                output_sha256,
            )

    def read_u12_investigation_result(
        self, *, attempt_id: str, attempts_root: str | Path
    ) -> bytes:
        """Read the pinned result artifact for a completed or accepted U12 attempt."""

        attempt = self.get_step_attempt(attempt_id)
        expected_path = f"{attempt.job_id}/{attempt.attempt_id}/result.json"
        if (
            attempt.step_key != "maintenance_investigation"
            or attempt.status not in {"complete", "handoff_accepted"}
            or attempt.output_path != expected_path
            or not attempt.output_sha256
        ):
            raise HandoffContractError("investigation_handoff_resume_invalid")
        return _read_handoff_artifact(
            Path(attempts_root).expanduser(),
            expected_path,
            attempt.output_sha256,
            "handoff_output",
        )

    def prior_u12_review_execution_ids(
        self,
        *,
        job_id: str,
        sequence: int,
        current_attempt_id: str,
        attempts_root: str | Path,
    ) -> frozenset[str]:
        """Read prior persisted reviewer execution identities for this U12 step."""

        job = _safe_id(job_id, "job_id_invalid")
        current_attempt = _safe_id(current_attempt_id, "attempt_id_invalid")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise MaintenanceStoreError("step_sequence_invalid")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT attempt_id, output_path, output_sha256 FROM step_attempt "
                "WHERE job_id = ? AND step_key = 'maintenance_investigation' "
                "AND sequence = ? AND attempt_id <> ? "
                "AND status IN ('complete', 'handoff_accepted') "
                "ORDER BY attempt_no",
                (job, sequence, current_attempt),
            ).fetchall()
        finally:
            connection.close()

        identities: set[str] = set()
        root = Path(attempts_root).expanduser()
        for row in rows:
            attempt = str(row["attempt_id"])
            expected_path = f"{job}/{attempt}/result.json"
            if (
                row["output_path"] != expected_path
                or not isinstance(row["output_sha256"], str)
                or not _SHA256_PATTERN.fullmatch(row["output_sha256"])
            ):
                raise MaintenanceStoreError("prior_review_result_unavailable")
            content = _read_handoff_artifact(
                root,
                expected_path,
                str(row["output_sha256"]),
                "prior_review_result",
            )
            try:
                document = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MaintenanceStoreError("prior_review_result_invalid") from exc
            review = document.get("review") if isinstance(document, dict) else None
            execution_id = (
                review.get("review_execution_id") if isinstance(review, dict) else None
            )
            if not isinstance(execution_id, str) or not _ID_PATTERN.fullmatch(
                execution_id
            ):
                raise MaintenanceStoreError("prior_review_result_invalid")
            identities.add(execution_id)
        return frozenset(identities)

    def finalize_job(
        self,
        *,
        job_id: str,
        final_status: str,
        lease: LeaseToken,
        finalized_at: datetime | str,
        reason_code: str | None = None,
        incident_status: str | None = None,
    ) -> JobFinalizeResult:
        """Finish a job while preserving stage and review outcomes as observations."""

        job = _safe_id(job_id, "job_id_invalid")
        if final_status not in _JOB_TERMINAL_STATUSES:
            raise MaintenanceStoreError("job_final_status_invalid")
        if reason_code is not None:
            reason_code = _safe_code(reason_code, "reason_code_invalid")
        if final_status != "complete" and reason_code is None:
            raise MaintenanceStoreError("job_terminal_reason_required")
        allowed_incident_statuses = {"in_progress", "resolved", "blocked"}
        if (
            incident_status is not None
            and incident_status not in allowed_incident_statuses
        ):
            raise MaintenanceStoreError("job_incident_status_invalid")
        when = _timestamp(finalized_at)
        with self._transaction() as connection:
            self._assert_lease(connection, lease, when)
            row = connection.execute(
                "SELECT * FROM maintenance_job WHERE job_id = ?", (job,)
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("job_not_found")
            if row["status"] in _JOB_TERMINAL_STATUSES:
                if (
                    row["status"] == final_status
                    and row["terminal_reason_code"] == reason_code
                ):
                    if incident_status is not None:
                        incident = connection.execute(
                            "SELECT status FROM incident WHERE incident_id = ?",
                            (row["incident_id"],),
                        ).fetchone()
                        if incident is None:
                            raise MaintenanceStoreError("incident_not_found")
                        current_incident_status = str(incident["status"])
                        if current_incident_status != incident_status:
                            connection.execute(
                                "UPDATE incident SET status = ?, updated_at = ? "
                                "WHERE incident_id = ?",
                                (incident_status, when, row["incident_id"]),
                            )
                    return JobFinalizeResult(
                        job,
                        str(row["status"]),
                        str(row["finished_at"]),
                        row["terminal_reason_code"],
                    )
                raise MaintenanceStoreError("job_already_finalized")
            if row["status"] not in {"queued", "running"}:
                raise MaintenanceStoreError("job_not_active")
            final_lease_key = lease.lease_key
            final_lease_generation = lease.generation
            final_resume_count = int(row["resume_count"])

            if incident_status is not None:
                incident = connection.execute(
                    "SELECT status FROM incident WHERE incident_id = ?",
                    (row["incident_id"],),
                ).fetchone()
                if incident is None:
                    raise MaintenanceStoreError("incident_not_found")
                current_incident_status = str(incident["status"])
                if current_incident_status == incident_status:
                    pass
                else:
                    connection.execute(
                        "UPDATE incident SET status = ?, updated_at = ? "
                        "WHERE incident_id = ?",
                        (incident_status, when, row["incident_id"]),
                    )

            if final_status in {"failed", "blocked", "cancelled"}:
                connection.execute(
                    "UPDATE step_attempt SET status = ?, finished_at = ? "
                    "WHERE job_id = ? AND status = 'running'",
                    (final_status, when, job),
                )
            connection.execute(
                "UPDATE maintenance_job SET status = ?, lease_key = ?, "
                "lease_generation = ?, resume_count = ?, finished_at = ?, "
                "terminal_reason_code = ?, updated_at = ? WHERE job_id = ?",
                (
                    final_status,
                    final_lease_key,
                    final_lease_generation,
                    final_resume_count,
                    when,
                    reason_code,
                    when,
                    job,
                ),
            )
            return JobFinalizeResult(job, final_status, when, reason_code)

    def record_workflow_stage_observation(
        self,
        *,
        invocation_id: str,
        job_id: str,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
        stage_name: str,
        status: str,
        reason_code: str | None,
        observed_at: datetime | str,
    ) -> None:
        """Persist a stage's actual result without interpreting it as a gate."""

        invocation = _safe_id(invocation_id, "workflow_invocation_id_invalid")
        job = _safe_id(job_id, "job_id_invalid")
        launch = _safe_id(source_launch_id, "source_launch_id_invalid")
        if source_run_id is not None:
            source_run_id = _safe_id(source_run_id, "source_run_id_invalid")
        try:
            date.fromisoformat(target_date)
        except (TypeError, ValueError) as exc:
            raise MaintenanceStoreError("target_date_invalid") from exc
        stage = _safe_id(stage_name, "workflow_stage_name_invalid")
        if not isinstance(status, str) or _CODE_PATTERN.fullmatch(status) is None:
            raise MaintenanceStoreError("workflow_stage_status_invalid")
        if reason_code is not None:
            reason_code = _safe_code(reason_code, "reason_code_invalid")
        when = _timestamp(observed_at)
        observation_id = f"workflow-stage-{uuid.uuid4().hex}"
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO maintenance_workflow_stage_observation "
                "(observation_id, invocation_id, job_id, source_launch_id, "
                "source_run_id, target_date, stage_name, status, reason_code, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(invocation_id, stage_name) DO UPDATE SET "
                "status = excluded.status, reason_code = excluded.reason_code, "
                "observed_at = excluded.observed_at",
                (
                    observation_id,
                    invocation,
                    job,
                    launch,
                    source_run_id,
                    target_date,
                    stage,
                    status,
                    reason_code,
                    when,
                ),
            )

    def list_workflow_stage_observations(
        self, *, job_id: str
    ) -> tuple[dict[str, object], ...]:
        """Return persisted stage outcomes in recorded order for one job."""

        job = _safe_id(job_id, "job_id_invalid")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT invocation_id, source_launch_id, source_run_id, target_date, "
                "stage_name, status, reason_code, observed_at "
                "FROM maintenance_workflow_stage_observation WHERE job_id = ? "
                "ORDER BY observed_at, invocation_id, stage_name",
                (job,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            {
                "invocation_id": str(row["invocation_id"]),
                "source_launch_id": str(row["source_launch_id"]),
                "source_run_id": row["source_run_id"],
                "target_date": str(row["target_date"]),
                "stage_name": str(row["stage_name"]),
                "status": str(row["status"]),
                "reason_code": row["reason_code"],
                "observed_at": str(row["observed_at"]),
            }
            for row in rows
        )

    def record_operation_event(self, event: Mapping[str, object]) -> None:
        """Append one sanitized operation event without changing prior events."""

        if not isinstance(event, Mapping):
            raise MaintenanceStoreError("operation_event_invalid")

        def optional_id(name: str) -> str | None:
            value = event.get(name)
            return _safe_id(value, f"operation_{name}_invalid") if value is not None else None

        event_id = _safe_id(event.get("event_id"), "operation_event_id_invalid")
        correlation_id = _safe_id(
            event.get("correlation_id"), "operation_correlation_id_invalid"
        )
        operation = _safe_id(event.get("operation"), "operation_name_invalid")
        phase = _safe_id(event.get("phase"), "operation_phase_invalid")
        observed_at = event.get("observed_at")
        if not isinstance(observed_at, str):
            raise MaintenanceStoreError("operation_timestamp_invalid")
        when = _timestamp(observed_at)
        reason = event.get("reason_code")
        if reason is not None:
            reason = _safe_code(reason, "operation_reason_invalid")
        exception_errno = event.get("exception_errno")
        if exception_errno is not None and (
            type(exception_errno) is not int or not 0 <= exception_errno <= 65535
        ):
            raise MaintenanceStoreError("operation_exception_errno_invalid")
        for name in ("exception_chain", "stack_frames", "input_refs", "artifact_refs"):
            if not isinstance(event.get(name, []), list):
                raise MaintenanceStoreError("operation_event_invalid")
        try:
            exception_chain = json.dumps(event.get("exception_chain", []), separators=(",", ":"))
            stack_frames = json.dumps(event.get("stack_frames", []), separators=(",", ":"))
            input_refs = json.dumps(event.get("input_refs", []), separators=(",", ":"))
            artifact_refs = json.dumps(event.get("artifact_refs", []), separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise MaintenanceStoreError("operation_event_invalid") from exc
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO maintenance_operation_event "
                "(event_id, correlation_id, parent_operation, job_id, source_launch_id, "
                "source_run_id, incident_id, invocation_id, stage, attempt_id, operation, "
                "phase, observed_at, code_revision, reason_code, exception_chain_json, exception_errno, "
                "stack_frames_json, capture_location, input_refs_json, artifact_refs_json, "
                "primary_sink, fallback_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    correlation_id,
                    optional_id("parent_operation"),
                    optional_id("job_id"),
                    optional_id("source_launch_id"),
                    optional_id("source_run_id"),
                    optional_id("incident_id"),
                    optional_id("invocation_id"),
                    optional_id("stage"),
                    optional_id("attempt_id"),
                    operation,
                    phase,
                    when,
                    optional_id("code_revision"),
                    reason,
                    exception_chain,
                    exception_errno,
                    stack_frames,
                    optional_id("capture_location"),
                    input_refs,
                    artifact_refs,
                    optional_id("primary_sink"),
                    optional_id("fallback_status"),
                ),
            )

    def list_open_operation_events(self) -> tuple[dict[str, object], ...]:
        """Return the latest appended event per correlation when it is still a begin.

        SQLite rowid breaks ties when event timestamps share millisecond precision.
        """

        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT event.* FROM maintenance_operation_event event "
                "WHERE event.phase = 'begin' AND NOT EXISTS ("
                "SELECT 1 FROM maintenance_operation_event later "
                "WHERE later.correlation_id = event.correlation_id AND ("
                "later.observed_at > event.observed_at OR ("
                "later.observed_at = event.observed_at AND later.rowid > event.rowid))) "
                "ORDER BY event.observed_at, event.rowid"
            ).fetchall()
        finally:
            connection.close()
        return tuple(dict(row) for row in rows)

    def reconcile_open_operation_events(self) -> int:
        """Append interruption observations for operations left at begin."""

        pending = self.list_open_operation_events()
        reconciled = 0
        for row in pending:
            event = {
                "event_id": f"reconcile-{uuid.uuid4().hex}",
                "correlation_id": row["correlation_id"],
                "parent_operation": row["parent_operation"],
                "job_id": row["job_id"],
                "source_launch_id": row["source_launch_id"],
                "source_run_id": row["source_run_id"],
                "incident_id": row["incident_id"],
                "invocation_id": row["invocation_id"],
                "stage": row["stage"],
                "attempt_id": row["attempt_id"],
                "operation": row["operation"],
                "phase": "interruption_observed",
                "observed_at": _timestamp(datetime.now(timezone.utc)),
                "reason_code": "process_interruption_observed",
                "exception_chain": [],
                "stack_frames": [],
                "capture_location": "store.MaintenanceStore.reconcile_open_operation_events",
                "input_refs": json.loads(row["input_refs_json"]),
                "artifact_refs": json.loads(row["artifact_refs_json"]),
                "primary_sink": "maintenance_operation_event",
                "fallback_status": None,
            }
            self.record_operation_event(event)
            reconciled += 1
        return reconciled

    def get_step_attempt(self, attempt_id: str) -> StepAttemptRecord:
        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM step_attempt WHERE attempt_id = ?", (attempt,)
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("step_attempt_not_found")
            return StepAttemptRecord(
                str(row["attempt_id"]),
                str(row["job_id"]),
                str(row["step_key"]),
                int(row["sequence"]),
                int(row["attempt_no"]),
                row["source_run_id"],
                str(row["status"]),
                row["result_version"],
                row["output_path"],
                row["output_sha256"],
                str(row["started_at"]),
                row["finished_at"],
            )
        finally:
            connection.close()

    def get_latest_step_attempt(
        self, *, job_id: str, sequence: int
    ) -> StepAttemptRecord:
        """Return the newest attempt for a job sequence, if one exists."""

        job = _safe_id(job_id, "job_id_invalid")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise MaintenanceStoreError("step_sequence_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM step_attempt WHERE job_id = ? AND sequence = ? "
                "ORDER BY attempt_no DESC LIMIT 1",
                (job, sequence),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("step_attempt_not_found")
            return StepAttemptRecord(
                str(row["attempt_id"]),
                str(row["job_id"]),
                str(row["step_key"]),
                int(row["sequence"]),
                int(row["attempt_no"]),
                row["source_run_id"],
                str(row["status"]),
                row["result_version"],
                row["output_path"],
                row["output_sha256"],
                str(row["started_at"]),
                row["finished_at"],
            )
        finally:
            connection.close()

    def get_step_attempt_review(self, *, attempt_id: str) -> dict[str, str] | None:
        """Return the latest version-bound review record for a step attempt."""

        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT reviewer_id, reviewed_version, status, reviewed_at "
                "FROM review_result WHERE scope_type = 'step_attempt' "
                "AND scope_id = ? ORDER BY rowid DESC LIMIT 1",
                (attempt,),
            ).fetchone()
            if row is None:
                return None
            return {
                "reviewer_id": str(row["reviewer_id"]),
                "reviewed_version": str(row["reviewed_version"]),
                "status": str(row["status"]),
                "reviewed_at": str(row["reviewed_at"]),
            }
        finally:
            connection.close()

    def create_or_load_repair_campaign(
        self,
        *,
        campaign_id: str,
        job_id: str,
        attempt_id: str,
        code_sha256: str,
        invocation_parent_id: str | None,
        required_cases: Mapping[str, str],
        semantic_signatures: Mapping[str, str],
        created_at: datetime | str,
    ) -> dict[str, object]:
        """Create a campaign once or recover its immutable state after restart."""

        campaign = _safe_id(campaign_id, "campaign_id_invalid")
        job = _safe_id(job_id, "job_id_invalid")
        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        if not isinstance(code_sha256, str) or not _SHA256_PATTERN.fullmatch(
            code_sha256
        ):
            raise MaintenanceStoreError("repair_campaign_code_hash_invalid")
        parent = (
            None
            if invocation_parent_id is None
            else _safe_id(invocation_parent_id, "repair_invocation_parent_invalid")
        )
        if not isinstance(required_cases, Mapping) or not required_cases:
            raise MaintenanceStoreError("repair_campaign_cases_invalid")
        if not isinstance(semantic_signatures, Mapping) or set(
            semantic_signatures
        ) != set(required_cases):
            raise MaintenanceStoreError("repair_campaign_signatures_invalid")
        normalized_cases: list[tuple[str, str, str]] = []
        for case_id_raw, node_raw in required_cases.items():
            case_id = _safe_id(case_id_raw, "repair_semantic_case_id_invalid")
            if (
                not isinstance(node_raw, str)
                or not node_raw.strip()
                or node_raw != node_raw.strip()
                or "\x00" in node_raw
                or "\n" in node_raw
                or "\r" in node_raw
                or "::" not in node_raw
            ):
                raise MaintenanceStoreError("repair_campaign_node_invalid")
            signature_raw = semantic_signatures[case_id_raw]
            if not isinstance(signature_raw, str) or not signature_raw.strip():
                raise MaintenanceStoreError("repair_campaign_signature_invalid")
            signature = " ".join(signature_raw.split())
            normalized_cases.append((case_id, node_raw, signature))
        case_ids = [item[0] for item in normalized_cases]
        nodes = [item[1] for item in normalized_cases]
        signatures = [item[2] for item in normalized_cases]
        if (
            len(case_ids) != len(set(case_ids))
            or len(nodes) != len(set(nodes))
            or len(signatures) != len(set(signatures))
        ):
            raise MaintenanceStoreError("repair_campaign_case_duplicate")
        when = _timestamp(created_at)
        with self._transaction(
            repair_runner_capability=self._repair_runner_capability
        ) as connection:
            attempt_row = connection.execute(
                "SELECT job_id, step_key, sequence, status, "
                "repair_predecessor_attempt_id FROM step_attempt WHERE attempt_id = ?",
                (attempt,),
            ).fetchone()
            if (
                attempt_row is None
                or attempt_row["job_id"] != job
                or attempt_row["step_key"] != "maintenance_repair"
                or int(attempt_row["sequence"]) != 3
                or attempt_row["status"] != "running"
            ):
                raise MaintenanceStoreError("repair_campaign_attempt_invalid")
            existing = connection.execute(
                "SELECT * FROM repair_campaign WHERE campaign_id = ?",
                (campaign,),
            ).fetchone()
            if existing is None:
                prior_campaigns = connection.execute(
                    "SELECT state FROM repair_campaign WHERE attempt_id = ?",
                    (attempt,),
                ).fetchall()
                if prior_campaigns:
                    raise MaintenanceStoreError("repair_campaign_fork_required")
                connection.execute(
                    "INSERT INTO repair_campaign "
                    "(campaign_id, job_id, attempt_id, parent_campaign_id, code_sha256, "
                    "invocation_parent_id, state, created_at, updated_at) "
                    "VALUES (?, ?, ?, NULL, ?, ?, 'active', ?, ?)",
                    (campaign, job, attempt, code_sha256, parent, when, when),
                )
                connection.executemany(
                    "INSERT INTO repair_campaign_case "
                    "(campaign_id, semantic_case_id, node, semantic_signature, ordinal) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (campaign, case_id, node, signature, ordinal)
                        for ordinal, (case_id, node, signature) in enumerate(
                            normalized_cases
                        )
                    ],
                )
            elif (
                existing["job_id"] != job
                or existing["attempt_id"] != attempt
                or existing["code_sha256"] != code_sha256
                or existing["invocation_parent_id"] != parent
            ):
                raise MaintenanceStoreError("repair_campaign_identity_conflict")
            stored_cases = connection.execute(
                "SELECT semantic_case_id, node, semantic_signature "
                "FROM repair_campaign_case WHERE campaign_id = ? "
                "ORDER BY ordinal",
                (campaign,),
            ).fetchall()
            if [
                (row["semantic_case_id"], row["node"], row["semantic_signature"])
                for row in stored_cases
                if not row["semantic_case_id"].startswith("__frozen__:")
            ] != normalized_cases:
                raise MaintenanceStoreError("repair_campaign_case_set_conflict")
            return self._repair_campaign_snapshot(connection, campaign)

    @staticmethod
    def _repair_campaign_snapshot(
        connection: sqlite3.Connection,
        campaign_id: str,
        _visited: frozenset[str] = frozenset(),
    ) -> dict[str, object]:
        if campaign_id in _visited:
            raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
        visited = _visited | {campaign_id}
        campaign = connection.execute(
            "SELECT * FROM repair_campaign WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        if campaign is None:
            raise MaintenanceStoreError("repair_campaign_not_found")
        case_rows = connection.execute(
            "SELECT semantic_case_id, node, semantic_signature, frozen, ordinal "
            "FROM repair_campaign_case WHERE campaign_id = ? ORDER BY ordinal",
            (campaign_id,),
        ).fetchall()
        result_rows = connection.execute(
            "SELECT result_id, semantic_case_id, outcome, code_sha256, receipt_id, created_at "
            "FROM repair_campaign_result WHERE campaign_id = ? "
            "ORDER BY created_at, result_id",
            (campaign_id,),
        ).fetchall()
        receipt_rows = connection.execute(
            "SELECT * FROM repair_run_receipt WHERE campaign_id = ? "
            "ORDER BY created_at, receipt_id",
            (campaign_id,),
        ).fetchall()
        run_receipts = tuple(
            MaintenanceStore._repair_receipt_document(row) for row in receipt_rows
        )
        receipt_by_id = {item["receipt_id"]: item for item in run_receipts}
        case_by_id = {str(row["semantic_case_id"]): row for row in case_rows}

        local_outcomes: dict[str, str] = {}
        local_pass_proofs: dict[str, dict[str, object]] = {}
        for result_row in result_rows:
            result_receipt = receipt_by_id.get(str(result_row["receipt_id"]))
            case_id = str(result_row["semantic_case_id"])
            case_row = case_by_id.get(case_id)
            if (
                result_receipt is None
                or case_row is None
                or not result_receipt["consumed"]
                or result_receipt["campaign_id"] != campaign_id
                or result_receipt["attempt_id"] != campaign["attempt_id"]
                or result_receipt["code_sha256"] != result_row["code_sha256"]
                or result_receipt["results"].get(case_id) != result_row["outcome"]
            ):
                raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
            local_outcomes[case_id] = str(result_row["outcome"])
            if result_row["outcome"] == "PASS":
                local_pass_proofs[case_id] = {
                    "semantic_case_id": case_id,
                    "node": str(case_row["node"]),
                    "semantic_signature": str(case_row["semantic_signature"]),
                    "campaign_id": campaign_id,
                    "attempt_id": str(campaign["attempt_id"]),
                    "code_sha256": str(result_row["code_sha256"]),
                    "receipt_id": str(result_row["receipt_id"]),
                    "receipt_sha256": str(result_receipt["receipt_sha256"]),
                    "outcome": "PASS",
                }

        parent_pass_proofs: dict[str, dict[str, object]] = {}
        if campaign["parent_campaign_id"] is not None:
            parent_id = str(campaign["parent_campaign_id"])
            parent_snapshot = MaintenanceStore._repair_campaign_snapshot(
                connection, parent_id, visited
            )
            parent_pass_proofs = dict(parent_snapshot["pass_proofs"])

        pass_proofs = dict(parent_pass_proofs)
        for case_id, outcome in local_outcomes.items():
            if outcome == "PASS":
                pass_proofs[case_id] = local_pass_proofs[case_id]
            else:
                pass_proofs.pop(case_id, None)

        frozen_proofs: dict[str, dict[str, object]] = {}
        for case_id, case_row in case_by_id.items():
            if not bool(case_row["frozen"]):
                continue
            proof = pass_proofs.get(case_id)
            if (
                proof is None
                or proof["node"] != case_row["node"]
                or proof["semantic_signature"] != case_row["semantic_signature"]
                or proof["outcome"] != "PASS"
                or not proof["receipt_id"]
                or not _SHA256_PATTERN.fullmatch(str(proof["receipt_sha256"]))
            ):
                raise MaintenanceStoreError("repair_campaign_frozen_proof_missing")
            frozen_proofs[case_id] = proof
        receipt = None
        if campaign["full_run_receipt_id"] is not None:
            receipt = receipt_by_id.get(str(campaign["full_run_receipt_id"]))
            if receipt is None:
                raise MaintenanceStoreError("repair_campaign_receipt_corrupt")

        full_snapshot = None
        if campaign["full_snapshot_json"] is not None:
            try:
                snapshot_data = json.loads(str(campaign["full_snapshot_json"]))
            except json.JSONDecodeError as exc:
                raise MaintenanceStoreError("repair_campaign_receipt_corrupt") from exc
            if not isinstance(snapshot_data, list) or any(
                not isinstance(item, dict)
                or set(item) != {"semantic_case_id", "node"}
                or not isinstance(item["semantic_case_id"], str)
                or not isinstance(item["node"], str)
                for item in snapshot_data
            ):
                raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
            canonical_snapshot = json.dumps(
                snapshot_data,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            if (
                canonical_snapshot != campaign["full_snapshot_json"]
                or hashlib.sha256(canonical_snapshot.encode("ascii")).hexdigest()
                != campaign["full_snapshot_sha256"]
            ):
                raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
            full_snapshot = tuple(
                (str(item["semantic_case_id"]), str(item["node"]))
                for item in snapshot_data
            )
            if (
                not full_snapshot
                or len({case_id for case_id, _node in full_snapshot})
                != len(full_snapshot)
                or len({node for _case_id, node in full_snapshot}) != len(full_snapshot)
            ):
                raise MaintenanceStoreError("repair_campaign_receipt_corrupt")

        campaign_state = str(campaign["state"])
        token_id = campaign["full_regression_token_hash"]
        if campaign_state == "active":
            if full_snapshot is not None or token_id is not None or receipt is not None:
                raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
        else:
            if (
                full_snapshot is None
                or not isinstance(token_id, str)
                or not _SHA256_PATTERN.fullmatch(token_id)
            ):
                raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
            stored_cases = {
                str(row["semantic_case_id"]): str(row["node"]) for row in case_rows
            }
            if any(
                stored_cases.get(case_id) != node for case_id, node in full_snapshot
            ):
                raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
            if campaign_state == "full_regression_started":
                snapshot_cases = tuple(
                    (str(row["semantic_case_id"]), str(row["node"]))
                    for row in case_rows
                )
                if (
                    full_snapshot != snapshot_cases
                    or receipt is not None
                    or campaign["full_run_receipt_id"] is not None
                ):
                    raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
            elif campaign_state == "full_regression_failed":
                snapshot_cases = tuple(
                    (str(row["semantic_case_id"]), str(row["node"]))
                    for row in case_rows
                )
                failure_code = campaign["full_regression_failure_code"]
                if (
                    full_snapshot != snapshot_cases
                    or receipt is not None
                    or campaign["full_run_receipt_id"] is not None
                    or (
                        (campaign["full_regression_nonce_hash"] is None)
                        != (campaign["full_regression_claimed_at"] is None)
                    )
                    or not isinstance(failure_code, str)
                    or not _CODE_PATTERN.fullmatch(failure_code)
                ):
                    raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
            elif campaign_state == "full_regression_recorded":
                if (
                    receipt is None
                    or receipt["run_kind"] != "full_regression"
                    or receipt["receipt_id"] != campaign["full_run_receipt_id"]
                    or receipt["campaign_id"] != campaign_id
                    or receipt["attempt_id"] != campaign["attempt_id"]
                    or receipt["token_hash"] != token_id
                    or receipt["code_sha256"] != campaign["code_sha256"]
                    or receipt["cases"] != full_snapshot
                    or set(receipt["results"])
                    != {case_id for case_id, _ in full_snapshot}
                    or any(
                        value
                        not in {
                            "PASS",
                            "FAIL",
                            "ERROR",
                            "SKIP",
                            "XFAIL",
                            "NOT_RUN",
                            "UNVERIFIED",
                        }
                        for value in receipt["results"].values()
                    )
                    or not isinstance(receipt["exit_code"], int)
                    or not 0 <= receipt["exit_code"] <= 255
                    or not isinstance(receipt["collected"], int)
                    or receipt["collected"] < 0
                    or not isinstance(receipt["assertion_failures"], int)
                    or receipt["assertion_failures"] < 0
                    or not isinstance(receipt["errors"], int)
                    or receipt["errors"] < 0
                    or not isinstance(receipt["skips"], int)
                    or receipt["skips"] < 0
                    or not isinstance(receipt["collection_errors"], int)
                    or receipt["collection_errors"] < 0
                    or receipt["invocation_parent_id"] is not None
                    or not receipt["consumed"]
                ):
                    raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
                full_case_ids = {case_id for case_id, _node in full_snapshot}
                for row in case_rows:
                    case_id = str(row["semantic_case_id"])
                    proof = frozen_proofs.get(case_id)
                    if case_id in full_case_ids:
                        passed = receipt["results"][case_id] == "PASS"
                        if bool(row["frozen"]) != passed or (passed and proof is None):
                            raise MaintenanceStoreError(
                                "repair_campaign_receipt_corrupt"
                            )
                        if passed and proof["receipt_id"] != receipt["receipt_id"]:
                            raise MaintenanceStoreError(
                                "repair_campaign_receipt_corrupt"
                            )
                    elif not bool(row["frozen"]) or proof is None:
                        raise MaintenanceStoreError("repair_campaign_receipt_corrupt")
                    elif proof["receipt_id"] == receipt["receipt_id"]:
                        raise MaintenanceStoreError("repair_campaign_receipt_corrupt")

        return {
            "campaign_id": str(campaign["campaign_id"]),
            "job_id": str(campaign["job_id"]),
            "attempt_id": str(campaign["attempt_id"]),
            "parent_campaign_id": campaign["parent_campaign_id"],
            "code_sha256": str(campaign["code_sha256"]),
            "invocation_parent_id": campaign["invocation_parent_id"],
            "state": str(campaign["state"]),
            "full_snapshot": full_snapshot,
            "full_regression_token_hash": campaign["full_regression_token_hash"],
            "full_run_receipt_id": campaign["full_run_receipt_id"],
            "full_regression_failure_code": campaign["full_regression_failure_code"],
            "frozen_proofs": frozen_proofs,
            "pass_proofs": pass_proofs,
            "cases": tuple(
                {
                    "semantic_case_id": str(row["semantic_case_id"]),
                    "node": str(row["node"]),
                    "semantic_signature": str(row["semantic_signature"]),
                    "frozen": bool(row["frozen"]),
                    "ordinal": int(row["ordinal"]),
                }
                for row in case_rows
            ),
            "results": tuple(
                {
                    "result_id": str(row["result_id"]),
                    "semantic_case_id": str(row["semantic_case_id"]),
                    "outcome": str(row["outcome"]),
                    "code_sha256": str(row["code_sha256"]),
                    "receipt_id": str(row["receipt_id"]),
                    "created_at": str(row["created_at"]),
                }
                for row in result_rows
            ),
            "full_regression": receipt,
            "run_receipts": run_receipts,
        }

    def load_repair_campaign(self, *, campaign_id: str) -> dict[str, object]:
        campaign = _safe_id(campaign_id, "campaign_id_invalid")
        connection = self._connect()
        try:
            return self._repair_campaign_snapshot(connection, campaign)
        finally:
            connection.close()

    def add_repair_campaign_case(
        self,
        *,
        campaign_id: str,
        semantic_case_id: str,
        node: str,
        semantic_signature: str,
    ) -> None:
        """Persist one new semantic case before the campaign is sealed."""

        campaign = _safe_id(campaign_id, "campaign_id_invalid")
        case_id = _safe_id(semantic_case_id, "repair_semantic_case_id_invalid")
        if (
            not isinstance(node, str)
            or not node.strip()
            or node != node.strip()
            or "\x00" in node
            or "\n" in node
            or "\r" in node
            or "::" not in node
        ):
            raise MaintenanceStoreError("repair_campaign_node_invalid")
        if not isinstance(semantic_signature, str) or not semantic_signature.strip():
            raise MaintenanceStoreError("repair_campaign_signature_invalid")
        signature = " ".join(semantic_signature.split())
        with self._transaction(
            repair_runner_capability=self._repair_runner_capability
        ) as connection:
            row = connection.execute(
                "SELECT state FROM repair_campaign WHERE campaign_id = ?",
                (campaign,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("repair_campaign_not_found")
            if row["state"] != "active":
                raise MaintenanceStoreError("case_added_after_full_regression_started")
            try:
                ordinal = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM repair_campaign_case "
                        "WHERE campaign_id = ?",
                        (campaign,),
                    ).fetchone()[0]
                )
                connection.execute(
                    "INSERT INTO repair_campaign_case "
                    "(campaign_id, semantic_case_id, node, semantic_signature, ordinal) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (campaign, case_id, node, signature, ordinal),
                )
            except sqlite3.IntegrityError as exc:
                raise MaintenanceStoreError(
                    "semantic_case_id_already_registered"
                ) from exc

    @staticmethod
    def _assert_repair_campaign_child_stopped(row: sqlite3.Row) -> None:
        child_state = str(row["full_regression_child_state"])
        if child_state == "not_started":
            if row["full_regression_child_pid"] is not None:
                raise MaintenanceStoreError("repair_runner_child_state_invalid")
            return
        if (
            child_state not in {"started", "exited"}
            or row["full_regression_child_pid"] is None
        ):
            raise MaintenanceStoreError("repair_runner_child_state_invalid")
        identity = _host_process_identity(int(row["full_regression_child_pid"]))
        if (
            identity is not None
            and identity[0] == row["full_regression_child_start_id"]
            and identity[1]
        ):
            raise MaintenanceStoreError("repair_runner_child_still_alive")

    def fork_repair_campaign_with_case(
        self,
        *,
        parent_campaign_id: str,
        campaign_id: str,
        code_sha256: str,
        semantic_case_id: str,
        node: str,
        semantic_signature: str,
        created_at: datetime | str,
    ) -> dict[str, object]:
        """Fork with a changed code hash and require all inherited cases again."""

        parent_id = _safe_id(parent_campaign_id, "campaign_id_invalid")
        campaign = _safe_id(campaign_id, "campaign_id_invalid")
        case_id = _safe_id(semantic_case_id, "repair_semantic_case_id_invalid")
        if not isinstance(code_sha256, str) or not _SHA256_PATTERN.fullmatch(
            code_sha256
        ):
            raise MaintenanceStoreError("repair_campaign_code_hash_invalid")
        if (
            not isinstance(node, str)
            or not node.strip()
            or node != node.strip()
            or "\x00" in node
            or "\n" in node
            or "\r" in node
            or "::" not in node
        ):
            raise MaintenanceStoreError("repair_campaign_node_invalid")
        if not isinstance(semantic_signature, str) or not semantic_signature.strip():
            raise MaintenanceStoreError("repair_campaign_signature_invalid")
        signature = " ".join(semantic_signature.split())
        when = _timestamp(created_at)
        with self._transaction(
            repair_runner_capability=self._repair_runner_capability
        ) as connection:
            parent = connection.execute(
                "SELECT * FROM repair_campaign WHERE campaign_id = ?", (parent_id,)
            ).fetchone()
            if parent is None:
                raise MaintenanceStoreError("repair_campaign_not_found")
            if parent["state"] not in {"active", "full_regression_recorded"}:
                raise MaintenanceStoreError("repair_campaign_fork_state_invalid")
            if parent["state"] == "full_regression_recorded":
                # Validate the completed parent before carrying case
                # definitions. PASS proofs are not inherited across a hash change.
                self._repair_campaign_snapshot(connection, parent_id)
                self._assert_repair_campaign_child_stopped(parent)
            elif connection.execute(
                "SELECT 1 FROM repair_case_run_start WHERE campaign_id = ? "
                "AND state = 'started' LIMIT 1",
                (parent_id,),
            ).fetchone():
                raise MaintenanceStoreError("repair_case_run_already_active")
            if parent["code_sha256"] == code_sha256:
                raise MaintenanceStoreError("campaign_code_hash_not_changed")
            old_cases = connection.execute(
                "SELECT semantic_case_id, node, semantic_signature, ordinal, frozen "
                "FROM repair_campaign_case WHERE campaign_id = ? ORDER BY ordinal",
                (parent_id,),
            ).fetchall()
            if not old_cases:
                raise MaintenanceStoreError("repair_campaign_cases_invalid")
            if any(
                row["semantic_case_id"] == case_id
                or row["node"] == node
                or row["semantic_signature"] == signature
                for row in old_cases
            ):
                raise MaintenanceStoreError("semantic_case_already_registered")
            if connection.execute(
                "SELECT 1 FROM repair_campaign WHERE campaign_id = ?", (campaign,)
            ).fetchone():
                raise MaintenanceStoreError("campaign_id_already_registered")
            connection.execute(
                "INSERT INTO repair_campaign "
                "(campaign_id, job_id, attempt_id, parent_campaign_id, code_sha256, "
                "invocation_parent_id, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, 'active', ?, ?)",
                (
                    campaign,
                    parent["job_id"],
                    parent["attempt_id"],
                    parent_id,
                    code_sha256,
                    when,
                    when,
                ),
            )
            copied = []
            for old in old_cases:
                copied.append(
                    (
                        campaign,
                        old["semantic_case_id"],
                        old["node"],
                        old["semantic_signature"],
                        int(old["ordinal"]),
                        0,
                    )
                )
            copied.append((campaign, case_id, node, signature, len(old_cases), 0))
            connection.executemany(
                "INSERT INTO repair_campaign_case "
                "(campaign_id, semantic_case_id, node, semantic_signature, ordinal, frozen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                copied,
            )
            return self._repair_campaign_snapshot(connection, campaign)

    def fork_failed_repair_campaign(
        self,
        *,
        parent_campaign_id: str,
        campaign_id: str,
        code_sha256: str,
        created_at: datetime | str,
    ) -> dict[str, object]:
        """Resume a failed one-use run in a new campaign without resetting it."""

        parent_id = _safe_id(parent_campaign_id, "campaign_id_invalid")
        campaign = _safe_id(campaign_id, "campaign_id_invalid")
        if not isinstance(code_sha256, str) or not _SHA256_PATTERN.fullmatch(
            code_sha256
        ):
            raise MaintenanceStoreError("repair_campaign_code_hash_invalid")
        when = _timestamp(created_at)
        with self._transaction(
            repair_runner_capability=self._repair_runner_capability
        ) as connection:
            parent = connection.execute(
                "SELECT * FROM repair_campaign WHERE campaign_id = ?", (parent_id,)
            ).fetchone()
            if parent is None:
                raise MaintenanceStoreError("repair_campaign_not_found")
            if parent["state"] != "full_regression_failed":
                raise MaintenanceStoreError("repair_campaign_fork_state_invalid")
            self._repair_campaign_snapshot(connection, parent_id)
            self._assert_repair_campaign_child_stopped(parent)
            if connection.execute(
                "SELECT 1 FROM repair_campaign WHERE campaign_id = ?", (campaign,)
            ).fetchone():
                raise MaintenanceStoreError("campaign_id_already_registered")
            old_cases = connection.execute(
                "SELECT semantic_case_id, node, semantic_signature, ordinal, frozen "
                "FROM repair_campaign_case WHERE campaign_id = ? ORDER BY ordinal",
                (parent_id,),
            ).fetchall()
            if not old_cases:
                raise MaintenanceStoreError("repair_campaign_cases_invalid")
            preserve_frozen = parent["code_sha256"] == code_sha256
            connection.execute(
                "INSERT INTO repair_campaign "
                "(campaign_id, job_id, attempt_id, parent_campaign_id, code_sha256, "
                "invocation_parent_id, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, 'active', ?, ?)",
                (
                    campaign,
                    parent["job_id"],
                    parent["attempt_id"],
                    parent_id,
                    code_sha256,
                    when,
                    when,
                ),
            )
            connection.executemany(
                "INSERT INTO repair_campaign_case "
                "(campaign_id, semantic_case_id, node, semantic_signature, ordinal, frozen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        campaign,
                        row["semantic_case_id"],
                        row["node"],
                        row["semantic_signature"],
                        int(row["ordinal"]),
                        int(preserve_frozen and bool(row["frozen"])),
                    )
                    for row in old_cases
                ],
            )
            return self._repair_campaign_snapshot(connection, campaign)

    def _begin_repair_case_run(
        self,
        *,
        campaign_id: str,
        semantic_case_id: str,
        capability: object,
        started_at: datetime | str,
    ) -> dict[str, object]:
        """Create a one-use invocation record for the built-in test runner."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        campaign = _safe_id(campaign_id, "campaign_id_invalid")
        case_id = _safe_id(semantic_case_id, "repair_semantic_case_id_invalid")
        when = _timestamp(started_at)
        lease_expires_at = _timestamp(_parse_timestamp(when) + timedelta(seconds=600))
        owner_identity = _host_process_identity(os.getpid())
        owner_start_id = (
            owner_identity[0]
            if owner_identity is not None and owner_identity[1]
            else f"local-{os.getpid()}"
        )
        invocation_id = str(uuid.uuid4())
        nonce = secrets.token_urlsafe(32)
        nonce_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT campaign_id, attempt_id, code_sha256, invocation_parent_id, state "
                "FROM repair_campaign WHERE campaign_id = ?",
                (campaign,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("repair_campaign_not_found")
            if row["state"] != "active":
                raise MaintenanceStoreError("repair_case_run_campaign_closed")
            if row["invocation_parent_id"] is not None:
                raise MaintenanceStoreError("nested_test_invocation_rejected")
            case = connection.execute(
                "SELECT node, frozen FROM repair_campaign_case "
                "WHERE campaign_id = ? AND semantic_case_id = ?",
                (campaign, case_id),
            ).fetchone()
            if case is None or case["frozen"]:
                raise MaintenanceStoreError("repair_case_not_runnable")
            prior_start = connection.execute(
                "SELECT invocation_id FROM repair_case_run_start "
                "WHERE campaign_id = ? AND semantic_case_id = ? AND state = 'started'",
                (campaign, case_id),
            ).fetchone()
            if prior_start is not None:
                raise MaintenanceStoreError("repair_case_run_already_active")
            case_document = [{"semantic_case_id": case_id, "node": str(case["node"])}]
            cases_json = json.dumps(
                case_document,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                "INSERT INTO repair_case_run_start "
                "(invocation_id, campaign_id, attempt_id, semantic_case_id, node, "
                "cases_json, code_sha256, nonce_hash, invocation_parent_id, owner_pid, "
                "owner_start_id, lease_expires_at, child_pid, child_start_id, child_state, "
                "state, receipt_id, failure_code, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, "
                "'not_started', 'started', NULL, NULL, ?)",
                (
                    invocation_id,
                    campaign,
                    row["attempt_id"],
                    case_id,
                    case["node"],
                    cases_json,
                    row["code_sha256"],
                    nonce_hash,
                    os.getpid(),
                    owner_start_id,
                    lease_expires_at,
                    when,
                ),
            )
            return {
                "invocation_id": invocation_id,
                "nonce": nonce,
                "attempt_id": str(row["attempt_id"]),
                "code_sha256": str(row["code_sha256"]),
                "semantic_case_id": case_id,
                "node": str(case["node"]),
                "invocation_parent_id": row["invocation_parent_id"],
                "lease_expires_at": lease_expires_at,
            }

    def _record_repair_case_child_start(
        self,
        *,
        invocation_id: str,
        child_pid: int,
        capability: object,
        started_at: datetime | str,
    ) -> str:
        """Bind a suspended exact-case runner child before it is resumed."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        invocation = _safe_id(invocation_id, "repair_run_invocation_invalid")
        if child_pid == os.getpid():
            raise MaintenanceStoreError("repair_runner_child_identity_invalid")
        identity = _host_process_identity(child_pid)
        if identity is None or not identity[1]:
            raise MaintenanceStoreError("repair_runner_child_identity_unavailable")
        when = _timestamp(started_at)
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT state, owner_pid, owner_start_id, child_state "
                "FROM repair_case_run_start WHERE invocation_id = ?",
                (invocation,),
            ).fetchone()
            owner = (
                _host_process_identity(int(row["owner_pid"]))
                if row is not None
                else None
            )
            if (
                row is None
                or row["state"] != "started"
                or row["child_state"] != "not_started"
                or int(row["owner_pid"]) != os.getpid()
                or owner is None
                or not owner[1]
                or owner[0] != row["owner_start_id"]
            ):
                raise MaintenanceStoreError("repair_runner_child_start_invalid")
            connection.execute(
                "UPDATE repair_case_run_start SET child_pid = ?, child_start_id = ?, "
                "child_state = 'started' WHERE invocation_id = ?",
                (child_pid, identity[0], invocation),
            )
            return identity[0]

    def _record_repair_case_child_exit(
        self,
        *,
        invocation_id: str,
        capability: object,
        exited_at: datetime | str,
    ) -> None:
        """Persist child exit only after process identity confirms termination."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        invocation = _safe_id(invocation_id, "repair_run_invocation_invalid")
        _timestamp(exited_at)
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT state, child_pid, child_start_id, child_state "
                "FROM repair_case_run_start WHERE invocation_id = ?",
                (invocation,),
            ).fetchone()
            if (
                row is None
                or row["state"] != "started"
                or row["child_state"] != "started"
            ):
                raise MaintenanceStoreError("repair_runner_child_exit_invalid")
            identity = _host_process_identity(int(row["child_pid"]))
            if (
                identity is not None
                and identity[0] == row["child_start_id"]
                and identity[1]
            ):
                raise MaintenanceStoreError("repair_runner_child_still_alive")
            connection.execute(
                "UPDATE repair_case_run_start SET child_state = 'exited' "
                "WHERE invocation_id = ?",
                (invocation,),
            )

    def _fail_repair_case_run(
        self,
        *,
        invocation_id: str,
        failure_code: str,
        capability: object,
        failed_at: datetime | str,
    ) -> None:
        """Durably close a failed case run after proving its child is not live."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        invocation = _safe_id(invocation_id, "repair_run_invocation_invalid")
        code = _safe_code(failure_code, "repair_run_failure_code_invalid")
        when = _timestamp(failed_at)
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT state, owner_pid, owner_start_id, child_pid, child_start_id, "
                "child_state FROM repair_case_run_start WHERE invocation_id = ?",
                (invocation,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("repair_case_run_not_found")
            if row["state"] != "started":
                return
            owner = _host_process_identity(int(row["owner_pid"]))
            if (
                owner is None
                or not owner[1]
                or owner[0] != row["owner_start_id"]
                or int(row["owner_pid"]) != os.getpid()
            ):
                raise MaintenanceStoreError("repair_runner_owner_identity_mismatch")
            child_state = str(row["child_state"])
            if child_state == "started":
                child = _host_process_identity(int(row["child_pid"]))
                if child is not None and child[0] == row["child_start_id"] and child[1]:
                    raise MaintenanceStoreError("repair_runner_child_still_alive")
                connection.execute(
                    "UPDATE repair_case_run_start SET child_state = 'exited' "
                    "WHERE invocation_id = ?",
                    (invocation,),
                )
            elif child_state != "exited" and child_state != "not_started":
                raise MaintenanceStoreError("repair_runner_child_state_invalid")
            connection.execute(
                "UPDATE repair_case_run_start SET state = 'failed', failure_code = ? "
                "WHERE invocation_id = ? AND state = 'started'",
                (code, invocation),
            )

    def abandon_interrupted_repair_case_run(
        self,
        *,
        invocation_id: str,
        capability: object,
        abandoned_at: datetime | str,
    ) -> None:
        """Recover an expired case run only after owner and child are proven dead."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        invocation = _safe_id(invocation_id, "repair_run_invocation_invalid")
        when = _timestamp(abandoned_at)
        host_now = datetime.now(timezone.utc)
        if _parse_timestamp(when) > host_now:
            raise MaintenanceStoreError("repair_runner_abandon_time_invalid")
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT * FROM repair_case_run_start WHERE invocation_id = ?",
                (invocation,),
            ).fetchone()
            if row is None or row["state"] != "started":
                raise MaintenanceStoreError("repair_case_run_not_abandonable")
            if host_now < _parse_timestamp(str(row["lease_expires_at"])):
                raise MaintenanceStoreError("repair_runner_lease_not_expired")
            owner = _host_process_identity(int(row["owner_pid"]))
            if owner is not None and owner[0] == row["owner_start_id"] and owner[1]:
                raise MaintenanceStoreError("repair_runner_owner_still_alive")
            child_state = str(row["child_state"])
            if child_state == "started":
                child = _host_process_identity(int(row["child_pid"]))
                if child is not None and child[0] == row["child_start_id"] and child[1]:
                    raise MaintenanceStoreError("repair_runner_child_still_alive")
                connection.execute(
                    "UPDATE repair_case_run_start SET child_state = 'exited' "
                    "WHERE invocation_id = ?",
                    (invocation,),
                )
            elif child_state not in {"not_started", "exited"}:
                raise MaintenanceStoreError("repair_runner_child_state_invalid")
            connection.execute(
                "UPDATE repair_case_run_start SET state = 'failed', "
                "failure_code = 'runner_interrupted' WHERE invocation_id = ? "
                "AND state = 'started'",
                (invocation,),
            )

    def _record_repair_case_receipt(
        self,
        *,
        capability: object,
        invocation_id: str,
        nonce: str,
        outcome: str,
        exit_code: int,
        collected: int,
        assertion_failures: int,
        errors: int,
        skips: int,
        collection_errors: int,
        created_at: datetime | str,
    ) -> RepairRunReceiptRecord:
        """Persist one exact-node run emitted by the host-owned pytest runner."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        invocation = _safe_id(invocation_id, "repair_run_invocation_invalid")
        if outcome not in _TEST_STATUSES:
            raise MaintenanceStoreError("repair_run_outcome_invalid")
        counts = (
            exit_code,
            collected,
            assertion_failures,
            errors,
            skips,
            collection_errors,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise MaintenanceStoreError("repair_run_receipt_invalid")
        when = _timestamp(created_at)
        nonce_hash = hashlib.sha256(nonce.encode("ascii", errors="strict")).hexdigest()
        receipt_id = str(uuid.uuid4())
        with self._transaction(repair_runner_capability=capability) as connection:
            start = connection.execute(
                "SELECT * FROM repair_case_run_start WHERE invocation_id = ?",
                (invocation,),
            ).fetchone()
            if (
                start is None
                or start["state"] != "started"
                or start["nonce_hash"] != nonce_hash
                or start["invocation_parent_id"] is not None
                or start["child_state"] not in {"not_started", "exited"}
            ):
                raise MaintenanceStoreError("repair_run_receipt_mismatch")
            owner = _host_process_identity(int(start["owner_pid"]))
            if (
                int(start["owner_pid"]) != os.getpid()
                or owner is None
                or not owner[1]
                or owner[0] != start["owner_start_id"]
            ):
                raise MaintenanceStoreError("repair_runner_owner_identity_mismatch")
            results = {str(start["semantic_case_id"]): outcome}
            cases_json = str(start["cases_json"])
            results_json = json.dumps(
                results, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            )
            receipt_payload = {
                "receipt_id": receipt_id,
                "campaign_id": str(start["campaign_id"]),
                "attempt_id": str(start["attempt_id"]),
                "run_kind": "case",
                "semantic_case_id": str(start["semantic_case_id"]),
                "invocation_id": invocation,
                "token_hash": None,
                "nonce_hash": nonce_hash,
                "code_sha256": str(start["code_sha256"]),
                "cases": json.loads(cases_json),
                "results": results,
                "exit_code": exit_code,
                "collected": collected,
                "assertion_failures": assertion_failures,
                "errors": errors,
                "skips": skips,
                "collection_errors": collection_errors,
                "invocation_parent_id": None,
            }
            receipt_sha256 = hashlib.sha256(
                json.dumps(
                    receipt_payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ).hexdigest()
            connection.execute(
                "INSERT INTO repair_run_receipt "
                "(receipt_id, campaign_id, attempt_id, run_kind, semantic_case_id, "
                "invocation_id, token_hash, nonce_hash, code_sha256, cases_json, "
                "results_json, exit_code, collected, assertion_failures, errors, "
                "skips, collection_errors, invocation_parent_id, receipt_sha256, created_at) "
                "VALUES (?, ?, ?, 'case', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    receipt_id,
                    start["campaign_id"],
                    start["attempt_id"],
                    start["semantic_case_id"],
                    invocation,
                    nonce_hash,
                    start["code_sha256"],
                    cases_json,
                    results_json,
                    exit_code,
                    collected,
                    assertion_failures,
                    errors,
                    skips,
                    collection_errors,
                    receipt_sha256,
                    when,
                ),
            )
            return RepairRunReceiptRecord(
                receipt_id=receipt_id,
                campaign_id=str(start["campaign_id"]),
                attempt_id=str(start["attempt_id"]),
                run_kind="case",
                semantic_case_id=str(start["semantic_case_id"]),
                invocation_id=invocation,
                code_sha256=str(start["code_sha256"]),
                cases=((str(start["semantic_case_id"]), str(start["node"])),),
                results=MappingProxyType(results),
                exit_code=exit_code,
                collected=collected,
                assertion_failures=assertion_failures,
                errors=errors,
                skips=skips,
                collection_errors=collection_errors,
                invocation_parent_id=None,
                receipt_sha256=receipt_sha256,
                consumed=False,
            )

    def consume_repair_case_receipt(
        self,
        *,
        receipt_id: str,
        expected: Mapping[str, object],
        consumed_at: datetime | str,
    ) -> RepairRunReceiptRecord:
        receipt = _safe_id(receipt_id, "repair_run_receipt_id_invalid")
        when = _timestamp(consumed_at)
        if not isinstance(expected, Mapping):
            raise MaintenanceStoreError("repair_run_receipt_invalid")
        with self._transaction(
            repair_runner_capability=self._repair_runner_capability
        ) as connection:
            row = connection.execute(
                "SELECT * FROM repair_run_receipt WHERE receipt_id = ?",
                (receipt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("host_run_receipt_unavailable")
            document = self._repair_receipt_document(row)
            if row["consumed_at"] is not None:
                raise MaintenanceStoreError("host_run_receipt_consumed")
            if document["run_kind"] != "case":
                raise MaintenanceStoreError("host_run_receipt_mismatch")
            start = connection.execute(
                "SELECT * FROM repair_case_run_start WHERE invocation_id = ?",
                (document["invocation_id"],),
            ).fetchone()
            if (
                start is None
                or start["state"] != "recorded"
                or start["receipt_id"] != receipt
                or start["campaign_id"] != document["campaign_id"]
                or start["attempt_id"] != document["attempt_id"]
                or start["semantic_case_id"] != document["semantic_case_id"]
                or start["code_sha256"] != document["code_sha256"]
                or start["cases_json"]
                != json.dumps(
                    [
                        {
                            "semantic_case_id": case_id,
                            "node": node,
                        }
                        for case_id, node in document["cases"]
                    ],
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                or start["invocation_parent_id"] != document["invocation_parent_id"]
            ):
                raise MaintenanceStoreError("host_run_receipt_mismatch")
            comparisons = {
                "campaign_id": document["campaign_id"],
                "attempt_id": document["attempt_id"],
                "semantic_case_id": document["semantic_case_id"],
                "invocation_id": document["invocation_id"],
                "code_sha256": document["code_sha256"],
                "cases": document["cases"],
                "results": document["results"],
                "exit_code": document["exit_code"],
                "collected": document["collected"],
                "assertion_failures": document["assertion_failures"],
                "errors": document["errors"],
                "skips": document["skips"],
                "collection_errors": document["collection_errors"],
                "invocation_parent_id": document["invocation_parent_id"],
                "receipt_sha256": document["receipt_sha256"],
            }
            if any(expected.get(key) != value for key, value in comparisons.items()):
                raise MaintenanceStoreError("host_run_receipt_mismatch")
            connection.execute(
                "UPDATE repair_run_receipt SET consumed_at = ? WHERE receipt_id = ?",
                (when, receipt),
            )
            case_id = str(document["semantic_case_id"])
            connection.execute(
                "INSERT INTO repair_campaign_result "
                "(result_id, campaign_id, semantic_case_id, outcome, code_sha256, "
                "receipt_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    document["campaign_id"],
                    case_id,
                    document["results"][case_id],
                    document["code_sha256"],
                    receipt,
                    when,
                ),
            )
            if document["results"][case_id] == "PASS":
                connection.execute(
                    "UPDATE repair_campaign_case SET frozen = 1 "
                    "WHERE campaign_id = ? AND semantic_case_id = ? AND frozen = 0",
                    (document["campaign_id"], case_id),
                )
            row = connection.execute(
                "SELECT * FROM repair_run_receipt WHERE receipt_id = ?",
                (receipt,),
            ).fetchone()
            return RepairRunReceiptRecord(
                receipt_id=receipt,
                campaign_id=str(row["campaign_id"]),
                attempt_id=str(row["attempt_id"]),
                run_kind="case",
                semantic_case_id=str(row["semantic_case_id"]),
                invocation_id=str(row["invocation_id"]),
                code_sha256=str(row["code_sha256"]),
                cases=tuple(
                    (item["semantic_case_id"], item["node"])
                    for item in json.loads(row["cases_json"])
                ),
                results=MappingProxyType(json.loads(row["results_json"])),
                exit_code=int(row["exit_code"]),
                collected=int(row["collected"]),
                assertion_failures=int(row["assertion_failures"]),
                errors=int(row["errors"]),
                skips=int(row["skips"]),
                collection_errors=int(row["collection_errors"]),
                invocation_parent_id=row["invocation_parent_id"],
                receipt_sha256=str(row["receipt_sha256"]),
                consumed=True,
            )

    @staticmethod
    def _repair_receipt_document(row: sqlite3.Row) -> dict[str, object]:
        try:
            cases = json.loads(str(row["cases_json"]))
            results = json.loads(str(row["results_json"]))
        except json.JSONDecodeError as exc:
            raise MaintenanceStoreError("repair_run_receipt_corrupt") from exc
        if (
            not isinstance(cases, list)
            or any(
                not isinstance(item, dict)
                or set(item) != {"semantic_case_id", "node"}
                or not isinstance(item["semantic_case_id"], str)
                or not isinstance(item["node"], str)
                for item in cases
            )
            or not isinstance(results, dict)
            or any(
                not isinstance(case_id, str) or not isinstance(outcome, str)
                for case_id, outcome in results.items()
            )
        ):
            raise MaintenanceStoreError("repair_run_receipt_corrupt")
        receipt_payload = {
            "receipt_id": str(row["receipt_id"]),
            "campaign_id": str(row["campaign_id"]),
            "attempt_id": str(row["attempt_id"]),
            "run_kind": str(row["run_kind"]),
            "semantic_case_id": row["semantic_case_id"],
            "invocation_id": row["invocation_id"],
            "token_hash": row["token_hash"],
            "nonce_hash": str(row["nonce_hash"]),
            "code_sha256": str(row["code_sha256"]),
            "cases": cases,
            "results": results,
            "exit_code": int(row["exit_code"]),
            "collected": int(row["collected"]),
            "assertion_failures": int(row["assertion_failures"]),
            "errors": int(row["errors"]),
            "skips": int(row["skips"]),
            "collection_errors": int(row["collection_errors"]),
            "invocation_parent_id": row["invocation_parent_id"],
        }
        expected_receipt_hash = hashlib.sha256(
            json.dumps(
                receipt_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        if expected_receipt_hash != row["receipt_sha256"]:
            raise MaintenanceStoreError("repair_run_receipt_corrupt")
        return {
            "receipt_id": str(row["receipt_id"]),
            "campaign_id": str(row["campaign_id"]),
            "attempt_id": str(row["attempt_id"]),
            "run_kind": str(row["run_kind"]),
            "semantic_case_id": row["semantic_case_id"],
            "invocation_id": row["invocation_id"],
            "token_hash": row["token_hash"],
            "nonce_hash": str(row["nonce_hash"]),
            "code_sha256": str(row["code_sha256"]),
            "cases": tuple((item["semantic_case_id"], item["node"]) for item in cases),
            "results": results,
            "exit_code": int(row["exit_code"]),
            "collected": int(row["collected"]),
            "assertion_failures": int(row["assertion_failures"]),
            "errors": int(row["errors"]),
            "skips": int(row["skips"]),
            "collection_errors": int(row["collection_errors"]),
            "invocation_parent_id": row["invocation_parent_id"],
            "receipt_sha256": str(row["receipt_sha256"]),
            "consumed": row["consumed_at"] is not None,
        }

    def begin_repair_full_regression(
        self,
        *,
        campaign_id: str,
        code_sha256: str,
        invocation_parent_id: str | None,
        started_at: datetime | str,
    ) -> dict[str, object]:
        """Persist the exact case set and a one-use token before suite execution."""

        campaign = _safe_id(campaign_id, "campaign_id_invalid")
        if not isinstance(code_sha256, str) or not _SHA256_PATTERN.fullmatch(
            code_sha256
        ):
            raise MaintenanceStoreError("repair_campaign_code_hash_invalid")
        parent = (
            None
            if invocation_parent_id is None
            else _safe_id(invocation_parent_id, "repair_invocation_parent_invalid")
        )
        when = _timestamp(started_at)
        lease_expires_at = _timestamp(_parse_timestamp(when) + timedelta(seconds=600))
        owner_identity = _host_process_identity(os.getpid())
        owner_start_id = (
            owner_identity[0]
            if owner_identity is not None and owner_identity[1]
            else f"local-{os.getpid()}"
        )
        token = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        token_hash = token
        with self._transaction(
            repair_runner_capability=self._repair_runner_capability
        ) as connection:
            row = connection.execute(
                "SELECT * FROM repair_campaign WHERE campaign_id = ?",
                (campaign,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("repair_campaign_not_found")
            if row["state"] != "active":
                raise MaintenanceStoreError("full_regression_token_already_used")
            if row["invocation_parent_id"] is not None or parent is not None:
                raise MaintenanceStoreError("nested_full_regression_rejected")
            if row["code_sha256"] != code_sha256:
                raise MaintenanceStoreError("repair_campaign_code_hash_mismatch")
            case_rows = connection.execute(
                "SELECT c.semantic_case_id, c.node FROM repair_campaign_case c "
                "WHERE c.campaign_id = ? ORDER BY c.ordinal",
                (campaign,),
            ).fetchall()
            if not case_rows:
                raise MaintenanceStoreError("repair_campaign_cases_invalid")
            cases = [
                {
                    "semantic_case_id": str(case["semantic_case_id"]),
                    "node": str(case["node"]),
                }
                for case in case_rows
            ]
            snapshot = json.dumps(
                cases, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            )
            snapshot_hash = hashlib.sha256(snapshot.encode("ascii")).hexdigest()
            connection.execute(
                "UPDATE repair_campaign SET state = 'full_regression_started', "
                "full_regression_token_hash = ?, full_regression_nonce_hash = NULL, "
                "full_regression_lease_expires_at = ?, "
                "full_snapshot_json = ?, full_snapshot_sha256 = ?, "
                "full_regression_owner_pid = ?, full_regression_owner_start_id = ?, "
                "full_regression_child_state = 'not_started', updated_at = ? "
                "WHERE campaign_id = ? AND state = 'active'",
                (
                    token_hash,
                    lease_expires_at,
                    snapshot,
                    snapshot_hash,
                    os.getpid(),
                    owner_start_id,
                    when,
                    campaign,
                ),
            )
            return {
                "token": token,
                "nonce": None,
                "cases": tuple(
                    (item["semantic_case_id"], item["node"]) for item in cases
                ),
                "snapshot_sha256": snapshot_hash,
                "lease_expires_at": lease_expires_at,
            }

    def _record_repair_runner_child_start(
        self,
        *,
        campaign_id: str,
        child_pid: int,
        capability: object,
        started_at: datetime | str,
    ) -> str:
        """Record a sandbox child while it is suspended, before it can run."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        if child_pid == os.getpid():
            raise MaintenanceStoreError("repair_runner_child_identity_invalid")
        identity = _host_process_identity(child_pid)
        if identity is None or not identity[1]:
            raise MaintenanceStoreError("repair_runner_child_identity_unavailable")
        when = _timestamp(started_at)
        campaign = _safe_id(campaign_id, "repair_campaign_id_invalid")
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT state, full_regression_owner_pid, full_regression_owner_start_id, "
                "full_regression_child_state FROM repair_campaign WHERE campaign_id = ?",
                (campaign,),
            ).fetchone()
            owner = (
                _host_process_identity(int(row["full_regression_owner_pid"]))
                if row is not None
                else None
            )
            if (
                row is None
                or row["state"] != "full_regression_started"
                or row["full_regression_child_state"] != "not_started"
                or int(row["full_regression_owner_pid"]) != os.getpid()
                or owner is None
                or not owner[1]
                or owner[0] != row["full_regression_owner_start_id"]
            ):
                raise MaintenanceStoreError("repair_runner_child_start_invalid")
            connection.execute(
                "UPDATE repair_campaign SET full_regression_child_pid = ?, "
                "full_regression_child_start_id = ?, full_regression_child_state = 'started', "
                "updated_at = ? WHERE campaign_id = ?",
                (child_pid, identity[0], when, campaign),
            )
            return identity[0]

    def _record_repair_runner_child_exit(
        self,
        *,
        campaign_id: str,
        capability: object,
        exited_at: datetime | str,
    ) -> None:
        """Persist child completion after the host has waited for process exit."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        campaign = _safe_id(campaign_id, "repair_campaign_id_invalid")
        when = _timestamp(exited_at)
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT state, full_regression_child_pid, full_regression_child_start_id, "
                "full_regression_child_state FROM repair_campaign WHERE campaign_id = ?",
                (campaign,),
            ).fetchone()
            if (
                row is None
                or row["state"] != "full_regression_started"
                or row["full_regression_child_state"] != "started"
            ):
                raise MaintenanceStoreError("repair_runner_child_exit_invalid")
            identity = _host_process_identity(int(row["full_regression_child_pid"]))
            if (
                identity is not None
                and identity[0] == row["full_regression_child_start_id"]
                and identity[1]
            ):
                raise MaintenanceStoreError("repair_runner_child_still_alive")
            connection.execute(
                "UPDATE repair_campaign SET full_regression_child_state = 'exited', "
                "updated_at = ? WHERE campaign_id = ?",
                (when, campaign),
            )

    def abandon_interrupted_repair_full_regression(
        self,
        *,
        campaign_id: str,
        capability: object,
        abandoned_at: datetime | str,
    ) -> None:
        """Close a crashed run only after its owner and any child are proven dead."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        campaign = _safe_id(campaign_id, "repair_campaign_id_invalid")
        when = _timestamp(abandoned_at)
        host_now = datetime.now(timezone.utc)
        if _parse_timestamp(when) > host_now:
            raise MaintenanceStoreError("repair_runner_abandon_time_invalid")
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT * FROM repair_campaign WHERE campaign_id = ?",
                (campaign,),
            ).fetchone()
            if row is None or row["state"] != "full_regression_started":
                raise MaintenanceStoreError("full_regression_not_abandonable")
            if row[
                "full_regression_lease_expires_at"
            ] is None or host_now < _parse_timestamp(
                str(row["full_regression_lease_expires_at"])
            ):
                raise MaintenanceStoreError("repair_runner_lease_not_expired")
            owner = _host_process_identity(int(row["full_regression_owner_pid"]))
            if (
                owner is not None
                and owner[0] == row["full_regression_owner_start_id"]
                and owner[1]
            ):
                raise MaintenanceStoreError("repair_runner_owner_still_alive")
            child_state = str(row["full_regression_child_state"])
            if child_state == "started":
                child = _host_process_identity(int(row["full_regression_child_pid"]))
                if (
                    child is not None
                    and child[0] == row["full_regression_child_start_id"]
                    and child[1]
                ):
                    raise MaintenanceStoreError("repair_runner_child_still_alive")
                connection.execute(
                    "UPDATE repair_campaign SET full_regression_child_state = 'exited' "
                    "WHERE campaign_id = ?",
                    (campaign,),
                )
            elif child_state == "exited":
                child = _host_process_identity(int(row["full_regression_child_pid"]))
                if (
                    child is not None
                    and child[0] == row["full_regression_child_start_id"]
                    and child[1]
                ):
                    raise MaintenanceStoreError("repair_runner_child_still_alive")
            elif child_state != "not_started":
                raise MaintenanceStoreError("repair_runner_child_state_invalid")
            connection.execute(
                "UPDATE repair_campaign SET state = 'full_regression_failed', "
                "full_regression_failure_code = 'runner_interrupted', updated_at = ? "
                "WHERE campaign_id = ? AND state = 'full_regression_started'",
                (when, campaign),
            )

    def _claim_repair_runner_nonce(
        self,
        *,
        campaign_id: str,
        capability: object,
        token: str | None = None,
        nonce: str | None = None,
    ) -> tuple[str, str, tuple[tuple[str, str], ...], str, str]:
        """Private one-shot handoff of the persisted nonce to HostPytestRunner."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        campaign = _safe_id(campaign_id, "campaign_id_invalid")
        if token is not None and (
            not isinstance(token, str) or not _SHA256_PATTERN.fullmatch(token)
        ):
            raise MaintenanceStoreError("full_regression_token_invalid")
        if nonce is not None and (not isinstance(nonce, str) or not nonce):
            raise MaintenanceStoreError("repair_runner_nonce_invalid")
        when = _timestamp(datetime.now(timezone.utc))
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT c.*, s.started_at FROM repair_campaign c "
                "JOIN step_attempt s ON s.attempt_id = c.attempt_id "
                "WHERE c.campaign_id = ?",
                (campaign,),
            ).fetchone()
            if (
                row is None
                or row["state"] != "full_regression_started"
                or (token is not None and row["full_regression_token_hash"] != token)
                or row["full_regression_claimed_at"] is not None
                or row["full_regression_nonce_hash"] is not None
            ):
                raise MaintenanceStoreError("full_regression_token_invalid")
            token_value = str(row["full_regression_token_hash"])
            nonce_value = nonce or secrets.token_urlsafe(32)
            nonce_hash = hashlib.sha256(nonce_value.encode("ascii")).hexdigest()
            cases_data = json.loads(str(row["full_snapshot_json"]))
            cases = tuple(
                (str(item["semantic_case_id"]), str(item["node"]))
                for item in cases_data
            )
            connection.execute(
                "UPDATE repair_campaign SET full_regression_claimed_at = ?, "
                "full_regression_nonce_hash = ?, updated_at = ? "
                "WHERE campaign_id = ? AND state = 'full_regression_started' "
                "AND full_regression_claimed_at IS NULL",
                (when, nonce_hash, when, campaign),
            )
            return (
                str(row["attempt_id"]),
                str(row["code_sha256"]),
                cases,
                token_value,
                nonce_value,
            )

    def _fail_repair_full_regression(
        self,
        *,
        campaign_id: str,
        attempt_id: str,
        failure_code: str,
        capability: object,
        failed_at: datetime | str,
    ) -> None:
        """Durably close a claimed run that cannot produce a clean receipt."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        campaign = _safe_id(campaign_id, "campaign_id_invalid")
        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        code = _safe_code(failure_code, "repair_run_failure_code_invalid")
        when = _timestamp(failed_at)
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT state, attempt_id, full_regression_claimed_at, "
                "full_regression_nonce_hash, full_regression_owner_pid, "
                "full_regression_owner_start_id, full_regression_child_pid, "
                "full_regression_child_start_id, full_regression_child_state "
                "FROM repair_campaign WHERE campaign_id = ?",
                (campaign,),
            ).fetchone()
            if (
                row is None
                or row["attempt_id"] != attempt
                or row["state"] != "full_regression_started"
                or row["full_regression_claimed_at"] is None
                or row["full_regression_nonce_hash"] is None
            ):
                raise MaintenanceStoreError("full_regression_token_invalid")
            owner = _host_process_identity(int(row["full_regression_owner_pid"]))
            if (
                owner is None
                or not owner[1]
                or owner[0] != row["full_regression_owner_start_id"]
                or int(row["full_regression_owner_pid"]) != os.getpid()
            ):
                raise MaintenanceStoreError("repair_runner_owner_identity_mismatch")
            if row["full_regression_child_state"] == "started":
                child = _host_process_identity(int(row["full_regression_child_pid"]))
                if (
                    child is not None
                    and child[0] == row["full_regression_child_start_id"]
                    and child[1]
                ):
                    raise MaintenanceStoreError("repair_runner_child_still_alive")
                connection.execute(
                    "UPDATE repair_campaign SET full_regression_child_state = 'exited' "
                    "WHERE campaign_id = ?",
                    (campaign,),
                )
            connection.execute(
                "UPDATE repair_campaign SET state = 'full_regression_failed', "
                "full_regression_failure_code = ?, updated_at = ? "
                "WHERE campaign_id = ? AND state = 'full_regression_started'",
                (code, when, campaign),
            )

    def _record_repair_run_receipt(
        self,
        *,
        capability: object,
        campaign_id: str,
        attempt_id: str,
        token: str,
        nonce: str,
        code_sha256: str,
        cases: tuple[tuple[str, str], ...],
        results: Mapping[str, str],
        exit_code: int,
        collected: int,
        assertion_failures: int,
        errors: int,
        skips: int,
        collection_errors: int,
        invocation_parent_id: str | None,
        created_at: datetime | str,
    ) -> RepairRunReceiptRecord:
        """Only the built-in runner capability can persist a test-run receipt."""

        if capability is not self._repair_runner_capability:
            raise MaintenanceStoreError("repair_runner_capability_invalid")
        campaign = _safe_id(campaign_id, "campaign_id_invalid")
        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        if not isinstance(code_sha256, str) or not _SHA256_PATTERN.fullmatch(
            code_sha256
        ):
            raise MaintenanceStoreError("repair_run_code_hash_invalid")
        if invocation_parent_id is not None:
            raise MaintenanceStoreError("nested_full_regression_rejected")
        if not isinstance(cases, tuple) or not isinstance(results, Mapping):
            raise MaintenanceStoreError("repair_run_receipt_invalid")
        if any(
            type(value) is not int or value < 0
            for value in (
                collected,
                assertion_failures,
                errors,
                skips,
                collection_errors,
            )
        ):
            raise MaintenanceStoreError("repair_run_receipt_invalid")
        if type(exit_code) is not int or not 0 <= exit_code <= 255:
            raise MaintenanceStoreError("repair_run_receipt_invalid")
        normalized_cases = tuple(
            (_safe_id(case_id, "repair_semantic_case_id_invalid"), node)
            for case_id, node in cases
        )
        if len({case_id for case_id, _ in normalized_cases}) != len(normalized_cases):
            raise MaintenanceStoreError("repair_run_receipt_invalid")
        case_document = [
            {"semantic_case_id": case_id, "node": node}
            for case_id, node in normalized_cases
        ]
        results_document = dict(results)
        expected_ids = {case_id for case_id, _ in normalized_cases}
        if set(results_document) != expected_ids or any(
            value not in {"PASS", "FAIL", "ERROR", "SKIP", "NOT_RUN", "UNVERIFIED"}
            for value in results_document.values()
        ):
            raise MaintenanceStoreError("repair_run_receipt_invalid")
        if not isinstance(token, str) or not _SHA256_PATTERN.fullmatch(token):
            raise MaintenanceStoreError("full_regression_token_invalid")
        token_hash = token
        nonce_hash = hashlib.sha256(nonce.encode("ascii", errors="strict")).hexdigest()
        cases_json = json.dumps(
            case_document, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        results_json = json.dumps(
            results_document, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        when = _timestamp(created_at)
        receipt_id = str(uuid.uuid4())
        receipt_hash_payload = {
            "receipt_id": receipt_id,
            "campaign_id": campaign,
            "attempt_id": attempt,
            "run_kind": "full_regression",
            "semantic_case_id": None,
            "invocation_id": None,
            "token_hash": token_hash,
            "nonce_hash": nonce_hash,
            "code_sha256": code_sha256,
            "cases": case_document,
            "results": results_document,
            "exit_code": exit_code,
            "collected": collected,
            "assertion_failures": assertion_failures,
            "errors": errors,
            "skips": skips,
            "collection_errors": collection_errors,
            "invocation_parent_id": None,
        }
        receipt_sha256 = hashlib.sha256(
            json.dumps(
                receipt_hash_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        with self._transaction(repair_runner_capability=capability) as connection:
            row = connection.execute(
                "SELECT * FROM repair_campaign WHERE campaign_id = ?",
                (campaign,),
            ).fetchone()
            stored_cases = (
                json.loads(str(row["full_snapshot_json"])) if row is not None else None
            )
            child_state = (
                str(row["full_regression_child_state"]) if row is not None else ""
            )
            if (
                row is None
                or row["state"] != "full_regression_started"
                or row["attempt_id"] != attempt
                or row["code_sha256"] != code_sha256
                or row["full_regression_token_hash"] != token_hash
                or row["full_regression_nonce_hash"] != nonce_hash
                or row["full_regression_claimed_at"] is None
                or stored_cases != case_document
                or child_state not in {"not_started", "exited"}
            ):
                raise MaintenanceStoreError("repair_run_receipt_mismatch")
            connection.execute(
                "INSERT INTO repair_run_receipt "
                "(receipt_id, campaign_id, attempt_id, run_kind, semantic_case_id, "
                "invocation_id, token_hash, nonce_hash, code_sha256, cases_json, results_json, "
                "exit_code, collected, assertion_failures, errors, skips, collection_errors, "
                "invocation_parent_id, receipt_sha256, created_at) "
                "VALUES (?, ?, ?, 'full_regression', NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    receipt_id,
                    campaign,
                    attempt,
                    token_hash,
                    nonce_hash,
                    code_sha256,
                    cases_json,
                    results_json,
                    exit_code,
                    collected,
                    assertion_failures,
                    errors,
                    skips,
                    collection_errors,
                    receipt_sha256,
                    when,
                ),
            )
            return RepairRunReceiptRecord(
                receipt_id=receipt_id,
                campaign_id=campaign,
                attempt_id=attempt,
                run_kind="full_regression",
                semantic_case_id=None,
                invocation_id=None,
                code_sha256=code_sha256,
                cases=normalized_cases,
                results=MappingProxyType(results_document),
                exit_code=exit_code,
                collected=collected,
                assertion_failures=assertion_failures,
                errors=errors,
                skips=skips,
                collection_errors=collection_errors,
                invocation_parent_id=None,
                receipt_sha256=receipt_sha256,
                consumed=False,
            )

    def consume_repair_run_receipt(
        self,
        *,
        receipt_id: str,
        expected: Mapping[str, object],
        consumed_at: datetime | str,
    ) -> RepairRunReceiptRecord:
        """Consume a matching host-runner receipt once and freeze its PASS set."""

        receipt = _safe_id(receipt_id, "repair_run_receipt_id_invalid")
        when = _timestamp(consumed_at)
        if not isinstance(expected, Mapping):
            raise MaintenanceStoreError("repair_run_receipt_invalid")
        with self._transaction(
            repair_runner_capability=self._repair_runner_capability
        ) as connection:
            row = connection.execute(
                "SELECT * FROM repair_run_receipt WHERE receipt_id = ?",
                (receipt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("host_run_receipt_unavailable")
            document = self._repair_receipt_document(row)
            if row["consumed_at"] is not None:
                raise MaintenanceStoreError("host_run_receipt_consumed")
            expected_token = expected.get("token")
            if (
                not isinstance(expected_token, str)
                or expected_token != row["token_hash"]
                or expected.get("receipt_id") != receipt
                or expected.get("run_id") != receipt
            ):
                raise MaintenanceStoreError("host_run_receipt_mismatch")
            comparisons = {
                "campaign_id": document["campaign_id"],
                "attempt_id": document["attempt_id"],
                "code_sha256": document["code_sha256"],
                "cases": document["cases"],
                "results": document["results"],
                "exit_code": document["exit_code"],
                "collected": document["collected"],
                "assertion_failures": document["assertion_failures"],
                "errors": document["errors"],
                "skips": document["skips"],
                "collection_errors": document["collection_errors"],
                "invocation_parent_id": document["invocation_parent_id"],
                "receipt_sha256": document["receipt_sha256"],
            }
            if any(expected.get(key) != value for key, value in comparisons.items()):
                raise MaintenanceStoreError("host_run_receipt_mismatch")
            if document["run_kind"] != "full_regression":
                raise MaintenanceStoreError("host_run_receipt_mismatch")
            campaign = str(document["campaign_id"])
            campaign_row = connection.execute(
                "SELECT state, attempt_id, code_sha256, invocation_parent_id, "
                "full_regression_token_hash, full_regression_nonce_hash, "
                "full_snapshot_json, full_snapshot_sha256, full_run_receipt_id "
                "FROM repair_campaign WHERE campaign_id = ?",
                (campaign,),
            ).fetchone()
            if (
                campaign_row is None
                or campaign_row["state"] != "full_regression_started"
                or campaign_row["attempt_id"] != document["attempt_id"]
                or campaign_row["code_sha256"] != document["code_sha256"]
                or campaign_row["invocation_parent_id"]
                != document["invocation_parent_id"]
                or campaign_row["full_regression_token_hash"] != row["token_hash"]
                or campaign_row["full_regression_nonce_hash"] != row["nonce_hash"]
                or campaign_row["full_snapshot_json"]
                != json.dumps(
                    [
                        {
                            "semantic_case_id": case_id,
                            "node": node,
                        }
                        for case_id, node in document["cases"]
                    ],
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                or hashlib.sha256(
                    str(campaign_row["full_snapshot_json"]).encode("ascii")
                ).hexdigest()
                != campaign_row["full_snapshot_sha256"]
                or campaign_row["full_run_receipt_id"] is not None
            ):
                raise MaintenanceStoreError("host_run_receipt_mismatch")
            connection.execute(
                "UPDATE repair_run_receipt SET consumed_at = ? WHERE receipt_id = ?",
                (when, receipt),
            )
            connection.execute(
                "UPDATE repair_campaign SET state = 'full_regression_recorded', "
                "full_run_receipt_id = ?, updated_at = ? WHERE campaign_id = ?",
                (receipt, when, campaign),
            )
            for case_id, _node in document["cases"]:
                connection.execute(
                    "INSERT INTO repair_campaign_result "
                    "(result_id, campaign_id, semantic_case_id, outcome, "
                    "code_sha256, receipt_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        campaign,
                        case_id,
                        document["results"][case_id],
                        document["code_sha256"],
                        receipt,
                        when,
                    ),
                )
                if document["results"][case_id] == "PASS":
                    connection.execute(
                        "UPDATE repair_campaign_case SET frozen = 1 "
                        "WHERE campaign_id = ? AND semantic_case_id = ? AND frozen = 0",
                        (campaign, case_id),
                    )
            row = connection.execute(
                "SELECT * FROM repair_run_receipt WHERE receipt_id = ?",
                (receipt,),
            ).fetchone()
            return RepairRunReceiptRecord(
                receipt_id=receipt,
                campaign_id=str(row["campaign_id"]),
                attempt_id=str(row["attempt_id"]),
                run_kind=str(row["run_kind"]),
                semantic_case_id=None,
                invocation_id=None,
                code_sha256=str(row["code_sha256"]),
                cases=tuple(
                    (item["semantic_case_id"], item["node"])
                    for item in json.loads(row["cases_json"])
                ),
                results=MappingProxyType(json.loads(row["results_json"])),
                exit_code=int(row["exit_code"]),
                collected=int(row["collected"]),
                assertion_failures=int(row["assertion_failures"]),
                errors=int(row["errors"]),
                skips=int(row["skips"]),
                collection_errors=int(row["collection_errors"]),
                invocation_parent_id=row["invocation_parent_id"],
                receipt_sha256=str(row["receipt_sha256"]),
                consumed=True,
            )

    def get_job_base_commit(self, *, job_id: str) -> str:
        """Return the release base commit pinned to a maintenance job."""

        job = _safe_id(job_id, "job_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT r.base_commit FROM maintenance_job j "
                "JOIN release r ON r.release_id = j.release_id WHERE j.job_id = ?",
                (job,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("job_not_found")
            return str(row["base_commit"])
        finally:
            connection.close()

    def get_latest_deployed_commit(self) -> str:
        """Return the latest exact commit confirmed active by deployment."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT d.result_commit, d.active_commit, r.result_commit AS release_commit "
                "FROM deployment AS d JOIN release AS r ON r.release_id = d.release_id "
                "WHERE d.status = 'deployed' "
                "ORDER BY d.deployed_at DESC, d.deployment_id DESC LIMIT 1"
            ).fetchone()
            if (
                row is None
                or row["active_commit"] is None
                or row["active_commit"] != row["result_commit"]
                or row["result_commit"] != row["release_commit"]
            ):
                raise MaintenanceStoreError("active_deployed_commit_unavailable")
            return str(row["active_commit"])
        finally:
            connection.close()

    def read_attempt_artifact(
        self,
        *,
        attempts_root: str | Path,
        relative_path: str,
        expected_sha256: str,
    ) -> bytes:
        """Read a hash-bound regular file below the configured attempt root."""

        relative = _relative_output_path(relative_path)
        if not isinstance(expected_sha256, str) or not _SHA256_PATTERN.fullmatch(
            expected_sha256
        ):
            raise MaintenanceStoreError("attempt_output_digest_invalid")
        return _read_handoff_artifact(
            Path(attempts_root).expanduser(),
            relative,
            expected_sha256,
            "attempt_output",
        )

    def prior_step_review_execution_ids(
        self,
        *,
        job_id: str,
        step_key: str,
        sequence: int,
        current_attempt_id: str,
        attempts_root: str | Path,
    ) -> frozenset[str]:
        """Read persisted reviewer executions from earlier attempts of a step."""

        job = _safe_id(job_id, "job_id_invalid")
        step = _safe_id(step_key, "step_key_invalid")
        current_attempt = _safe_id(current_attempt_id, "attempt_id_invalid")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise MaintenanceStoreError("step_sequence_invalid")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT attempt_id, output_path, output_sha256 FROM step_attempt "
                "WHERE job_id = ? AND step_key = ? AND sequence = ? "
                "AND attempt_id <> ? AND status IN ('complete', 'handoff_accepted') "
                "ORDER BY attempt_no",
                (job, step, sequence, current_attempt),
            ).fetchall()
        finally:
            connection.close()

        identities: set[str] = set()
        root = Path(attempts_root).expanduser()
        for row in rows:
            attempt = str(row["attempt_id"])
            expected_path = f"{job}/{attempt}/result.json"
            if (
                row["output_path"] != expected_path
                or not isinstance(row["output_sha256"], str)
                or not _SHA256_PATTERN.fullmatch(row["output_sha256"])
            ):
                raise MaintenanceStoreError("prior_step_review_unavailable")
            content = _read_handoff_artifact(
                root,
                expected_path,
                str(row["output_sha256"]),
                "prior_step_review",
            )
            try:
                document = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MaintenanceStoreError("prior_step_review_invalid") from exc
            review = document.get("review") if isinstance(document, dict) else None
            execution_id = (
                review.get("review_execution_id") if isinstance(review, dict) else None
            )
            if not isinstance(execution_id, str) or not _ID_PATTERN.fullmatch(
                execution_id
            ):
                raise MaintenanceStoreError("prior_step_review_invalid")
            identities.add(execution_id)
        return frozenset(identities)

    def next_step_review_round(self, attempt_id: str) -> int:
        """Return the next review round from persisted history for this step."""

        attempt = _safe_id(attempt_id, "attempt_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT job_id, step_key, sequence, status FROM step_attempt "
                "WHERE attempt_id = ?",
                (attempt,),
            ).fetchone()
            if row is None:
                raise MaintenanceStoreError("step_attempt_not_found")
            if row["status"] != "running":
                raise MaintenanceStoreError("step_attempt_not_running")
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM review_result r "
                    "JOIN step_attempt s ON s.attempt_id = r.scope_id "
                    "WHERE r.scope_type = 'step_attempt' AND s.job_id = ? "
                    "AND s.step_key = ? AND s.sequence = ?",
                    (row["job_id"], row["step_key"], row["sequence"]),
                ).fetchone()[0]
            )
            return count + 1
        finally:
            connection.close()
