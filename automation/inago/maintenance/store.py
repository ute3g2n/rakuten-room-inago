"""Transactional maintenance state and daily-operation observations."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Mapping
from zoneinfo import ZoneInfo

from automation.inago.maintenance.paths import (
    UnsafeMaintenancePathError,
    _has_reparse_point,
    resolve_maintenance_database_path,
)


SCHEMA_VERSION = 19
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
    16: (
        "016_operation_failure_details.sql",
        "retain safe operation context and nested reason codes in failure traces",
    ),
    17: (
        "017_optional_release_anchor.sql",
        "allow launch-linked maintenance jobs without an unrelated release anchor",
    ),
    18: (
        "018_optional_ai_role_config_hash.sql",
        "stop requiring role-configuration hashes while retaining old session rows",
    ),
    19: (
        "019_remove_legacy_maintenance_attestation_gates.sql",
        "remove legacy maintenance hash and receipt gates while retaining historical rows",
    ),
}
# Migration 12 rebuilds maintenance_job, a parent table referenced by several
# existing tables. SQLite keeps deferred-FK counters from DROP TABLE even after
# the replacement table is restored, so this migration checks the final schema
# with foreign_key_check while enforcement is temporarily disabled.
_MIGRATIONS_REQUIRING_FOREIGN_KEY_CHECK = frozenset({12, 17, 18, 19})
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ERROR_CLASS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
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
_JOB_TERMINAL_STATUSES = {"complete", "failed", "blocked", "cancelled"}
_SCHEMA_TABLES = {
    "schema_migration",
    "daily_check",
    "incident",
    "incident_run",
    "maintenance_job",
    "lease",
    "job_input_manifest",
    "maintenance_ai_session",
    "maintenance_ai_observation",
    "daily_launch",
    "watchdog_queue",
    "watchdog_cursor",
    "worker_queue",
    "worker_queue_event",
    "daily_result_observation",
    "maintenance_job_resume_event",
    "maintenance_workflow_stage_observation",
    "maintenance_operation_event",
}
# Older maintenance schema versions left these tables in place when their
# write paths were retired. Their rows remain historical data; their presence
# must not make a current database unopenable.
_HISTORICAL_SCHEMA_TABLES = {
    "deployment",
    "deployment_verification",
    "handoff_artifact",
    "release",
    "release_gate",
    "release_required_test",
    "repair_campaign",
    "repair_campaign_case",
    "repair_campaign_result",
    "repair_case_run_start",
    "repair_host_review_execution",
    "repair_review_receipt",
    "repair_run_receipt",
    "review_result",
    "step_attempt",
    "test_ledger",
}


def _split_table_definitions(sql: str) -> tuple[str, list[str], str]:
    """Split a CREATE TABLE statement without losing nested SQL expressions."""
    open_at = sql.find("(")
    if open_at < 0:
        raise UnknownMaintenanceSchemaError("migration_table_sql_invalid")
    depth = 0
    quote: str | None = None
    close_at = -1
    parts: list[str] = []
    start = open_at + 1
    for index in range(open_at, len(sql)):
        char = sql[index]
        if quote:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    continue
                quote = None
            continue
        if char in "'\"`":
            quote = char
        elif char == "[":
            quote = "]"
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                parts.append(sql[start:index].strip())
                close_at = index
                break
        elif char == "," and depth == 1:
            parts.append(sql[start:index].strip())
            start = index + 1
    if close_at < 0:
        raise UnknownMaintenanceSchemaError("migration_table_sql_invalid")
    return sql[:open_at], parts, sql[close_at + 1:]


def _remove_hash_checks(definition: str) -> str | None:
    """Drop schema constraints used by retired attestations and lease ownership."""
    terms = r"(?:sha256|hash|receipt|commit|signature|fingerprint|digest|discriminator|lease|claim_owner_id|claim_generation|claimed_at|owner_id|generation)"
    if re.match(r"(?is)^\s*(?:CONSTRAINT\b|PRIMARY\s+KEY\b|UNIQUE\b|FOREIGN\s+KEY\b|CHECK\b)", definition):
        return None if re.search(terms, definition, re.IGNORECASE) else definition
    match = re.match(r"(?is)^\s*(?:\"([^\"]+)\"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))", definition)
    if not match:
        return definition
    column = next(item for item in match.groups() if item is not None)
    target_column = re.search(terms, column, re.IGNORECASE) is not None
    result = definition
    if target_column:
        result = re.sub(r"\s+NOT\s+NULL\b", "", result, flags=re.IGNORECASE)
    # Remove CHECK clauses that constrain the legacy attestation column, preserving
    # neighboring defaults, collations, references, and unrelated checks.
    if re.search(terms, result, re.IGNORECASE):
        cursor = 0
        output = ""
        while cursor < len(result):
            check = re.search(r"\bCHECK\s*\(", result[cursor:], re.IGNORECASE)
            if check is None:
                output += result[cursor:]
                break
            begin = cursor + check.start()
            paren = cursor + check.end() - 1
            depth = 0
            quote: str | None = None
            end = -1
            for index in range(paren, len(result)):
                char = result[index]
                if quote:
                    if char == quote:
                        quote = None
                    continue
                if char in "'\"`":
                    quote = char
                elif char == "[":
                    quote = "]"
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            if end < 0:
                raise UnknownMaintenanceSchemaError("migration_check_sql_invalid")
            clause = result[begin:end]
            if re.search(terms, clause, re.IGNORECASE):
                output += result[cursor:begin]
            else:
                output += result[cursor:end]
            cursor = end
        result = output
    return result


def _migrate_remove_legacy_maintenance_attestation_gates(
    connection: sqlite3.Connection,
) -> None:
    """Retain history while removing retired hash, receipt, and lease gates."""
    protected_business_table = "daily_result_observation"
    triggers = connection.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    retained_triggers: list[str] = []
    for name, table, sql in triggers:
        is_legacy_gate = table != protected_business_table and re.search(
            r"sha256|\bhash\b|receipt|commit|signature|fingerprint|digest|discriminator|lease|claim_owner_id|claim_generation|claimed_at|owner_id|generation|release_gate|repair_",
            sql or "",
            re.IGNORECASE,
        )
        if not is_legacy_gate and sql:
            retained_triggers.append(str(sql))
        connection.execute(f'DROP TRIGGER IF EXISTS "{name}"')

    tables = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    rebuild: list[tuple[str, str, list[str]]] = []
    for table, create_sql in tables:
        if table == protected_business_table or not create_sql:
            continue
        prefix, definitions, suffix = _split_table_definitions(create_sql)
        changed: list[str] = []
        has_gate = False
        for definition in definitions:
            rewritten = _remove_hash_checks(definition)
            if rewritten is None:
                has_gate = True
                continue
            if rewritten != definition:
                has_gate = True
            changed.append(rewritten)
        indexes = [
            row[0] for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            ).fetchall()
            if not re.search(r"sha256|\bhash\b|receipt|commit|signature|fingerprint|digest|discriminator|lease|claim_owner_id|claim_generation|claimed_at|owner_id|generation", row[0], re.IGNORECASE)
        ]
        if has_gate:
            rebuild.append((table, prefix + "(" + ",\n".join(changed) + ")" + suffix, indexes))

    for table, create_sql, indexes in rebuild:
        temp = f"{table}__attestation_v19"
        connection.execute(f"DROP TABLE IF EXISTS \"{temp}\"")
        rewritten_sql = re.sub(
            r"(?is)(CREATE\s+TABLE\s+)(?:IF\s+NOT\s+EXISTS\s+)?(?:\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)",
            lambda match: match.group(1) + f'"{temp}"',
            create_sql,
            count=1,
        )
        connection.execute(rewritten_sql)
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info(\"{table}\")")]
        names = ", ".join(f'"{column}"' for column in columns)
        connection.execute(f'INSERT INTO "{temp}" ({names}) SELECT {names} FROM "{table}"')
        connection.execute(f'DROP TABLE "{table}"')
        connection.execute(f'ALTER TABLE "{temp}" RENAME TO "{table}"')
        for index_sql in indexes:
            connection.execute(index_sql)

    for trigger_sql in retained_triggers:
        connection.execute(trigger_sql)


class MaintenanceStoreError(RuntimeError):
    """A safe, non-content-bearing maintenance storage error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class UnknownMaintenanceSchemaError(MaintenanceStoreError):
    """The target database is not this maintenance schema or is too new."""


class IdempotencyConflictError(MaintenanceStoreError):
    """A repeated identity arrived with incompatible source facts."""


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
    owner_id: str | None
    generation: int | None
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
class MaintenanceJobRecord:
    job_id: str
    incident_id: str
    release_id: str | None
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
class JobInputManifest:
    job_id: str
    incident_id: str
    source_launch_id: str
    source_run_id: str | None
    target_date: str
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
class JobFinalizeResult:
    job_id: str
    status: str
    finalized_at: str
    reason_code: str | None


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
        owner_id=str(row["owner_id"]) if row["owner_id"] is not None else None,
        generation=int(row["generation"]) if row["generation"] is not None else None,
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
                    if migration_version == 19:
                        _migrate_remove_legacy_maintenance_attestation_gates(
                            connection
                        )
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
            or not _SCHEMA_TABLES.issubset(tables)
            or not tables.issubset(_SCHEMA_TABLES | _HISTORICAL_SCHEMA_TABLES)
        ):
            raise UnknownMaintenanceSchemaError("database_schema_unrecognized")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
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
                                                                            "lease",
                "job_input_manifest",
                "maintenance_ai_session",
                "maintenance_ai_observation",
                "daily_launch",
                "watchdog_queue",
                "watchdog_cursor",
                "worker_queue",
                "worker_queue_event",
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
        with self._transaction() as connection:
            self._validate_manifest_binding(connection, **identity)
            existing = connection.execute(
                "SELECT incident_id, source_launch_id, source_run_id, target_date, "
                "created_at FROM job_input_manifest WHERE job_id = ?",
                (identity["job_id"],),
            ).fetchone()
            if existing is not None:
                existing_identity = {
                    key: existing[key] for key in identity if key != "job_id"
                }
                if (
                    existing_identity
                    != {key: identity[key] for key in identity if key != "job_id"}
                ):
                    raise IdempotencyConflictError("job_input_manifest_conflict")
                return JobInputManifest(
                    job_id=str(identity["job_id"]),
                    incident_id=str(existing["incident_id"]),
                    source_launch_id=str(existing["source_launch_id"]),
                    source_run_id=existing["source_run_id"],
                    target_date=str(existing["target_date"]),
                    created_at=str(existing["created_at"]),
                )
            connection.execute(
                "INSERT INTO job_input_manifest "
                "(job_id, incident_id, source_launch_id, source_run_id, target_date, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    identity["job_id"],
                    identity["incident_id"],
                    identity["source_launch_id"],
                    identity["source_run_id"],
                    identity["target_date"],
                    when,
                ),
            )
        return JobInputManifest(
            job_id=str(identity["job_id"]),
            incident_id=str(identity["incident_id"]),
            source_launch_id=str(identity["source_launch_id"]),
            source_run_id=identity["source_run_id"],
            target_date=str(identity["target_date"]),
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
                "target_date, created_at "
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
                created_at=str(row["created_at"]),
            )
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
                        release_id=(
                            str(row["release_id"])
                            if row["release_id"] is not None
                            else None
                        ),
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
                f"WHERE {' AND '.join(filters)} AND j.release_id IS NOT NULL "
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
        resumed_at: datetime | str,
    ) -> JobResumeRecord:
        """Requeue an unfinished job without lease or review eligibility gates."""

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

    def get_incident_for_check(self, check_id: str) -> dict[str, str] | None:
        """Read the incident already attached to one exact daily check."""
        check = _safe_id(check_id, "check_id_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT i.incident_id, i.dedupe_key, i.component, i.event_code "
                "FROM incident_run ir JOIN incident i ON i.incident_id = ir.incident_id "
                "WHERE ir.check_id = ? ORDER BY ir.created_at, i.incident_id LIMIT 1",
                (check,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return {key: str(row[key]) for key in ("incident_id", "dedupe_key", "component", "event_code")}

    def ensure_skill_chain_job_for_launch(
        self,
        *,
        source_launch_id: str,
        source_run_id: str | None,
        target_date: str,
        queue_id: str,
        observed_at: datetime | str,
    ) -> MaintenanceJobRecord:
        """Register or reuse a maintenance job through the launch/check records.

        The existing launch and queue remain the source of truth. No worker queue
        identifier is reused as a maintenance job identifier.
        """
        launch_id = _safe_id(source_launch_id, "source_launch_id_invalid")
        run_id = None if source_run_id is None else _safe_id(source_run_id, "source_run_id_invalid")
        target = _target_date(target_date)
        queue_key = _safe_id(queue_id, "worker_queue_id_invalid")
        launch = self.get_daily_launch(launch_id)
        queue = self.get_worker_queue(launch_id)
        if (
            launch is None or launch.target_date != target
            or launch.source_run_id != run_id
            or queue is None or queue.queue_id != queue_key
            or queue.target_date != target
        ):
            raise MaintenanceStoreError("maintenance_launch_queue_identity_unavailable")
        from automation.inago.maintenance.worker import WORKER_DISPATCH_EXCLUDED_DATES

        if target in WORKER_DISPATCH_EXCLUDED_DATES:
            raise MaintenanceStoreError("worker_dispatch_date_excluded")

        check = self.get_daily_check_by_launch(launch_id)
        incident = self.get_incident_for_check(check.check_id) if check is not None else None
        if incident is None:
            incident_input = {
                "dedupe_key": f"maintenance-launch-{launch_id}",
                "component": "daily_pipeline",
                "event_code": "daily_failure_observed",
            }
            if check is None:
                check_result = self.record_daily_check(
                    source_launch_id=launch_id,
                    source_run_id=run_id,
                    target_date=target,
                    check_status="abnormal",
                    observed_at=observed_at,
                    reason_code="daily_failure_observed",
                    incident=incident_input,
                )
            else:
                check_result = self.record_daily_check(
                    source_launch_id=launch_id,
                    source_run_id=run_id,
                    target_date=target,
                    check_status=check.check_status,
                    observed_at=check.observed_at,
                    reason_code=check.reason_code,
                    incident=incident_input,
                )
            if check_result.incident_id is None:
                raise MaintenanceStoreError("maintenance_incident_registration_unavailable")
            incident_id = check_result.incident_id
        else:
            incident_id = incident["incident_id"]
        jobs = self.list_workflow_jobs_for_launch(
            source_launch_id=launch_id,
            source_run_id=run_id,
            target_date=target,
        )
        job = next((item for item in jobs if item.incident_id == incident_id), None)
        if job is None:
            job_id = f"job-maintenance-{uuid.uuid4().hex}"
            created_at = _timestamp(observed_at)
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO maintenance_job "
                    "(job_id, incident_id, release_id, source_run_id, result_version, "
                    "status, created_at, updated_at) "
                    "VALUES (?, ?, NULL, ?, 'local-maintenance-v1', 'queued', ?, ?)",
                    (job_id, incident_id, run_id, created_at, created_at),
                )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()
            jobs = self.list_workflow_jobs_for_launch(
                source_launch_id=launch_id,
                source_run_id=run_id,
                target_date=target,
            )
            job = next((item for item in jobs if item.job_id == job_id), None)
        if job is None:
            raise MaintenanceStoreError("maintenance_job_registration_unavailable")
        return job

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
        completed_at: datetime | str,
    ) -> WorkerQueueRecord:
        queue = _safe_id(queue_id, "worker_queue_id_invalid")
        check = _safe_id(check_id, "check_id_invalid")
        when = _timestamp(completed_at)
        with self._transaction() as connection:
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
                "completed_at = ?, updated_at = ? WHERE queue_id = ? AND status = 'queued'",
                (
                    check,
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
        blocked_at: datetime | str,
    ) -> WorkerQueueRecord:
        """Persist a fail-closed stop without marking the queue complete."""

        queue = _safe_id(queue_id, "worker_queue_id_invalid")
        check = None if check_id is None else _safe_id(check_id, "check_id_invalid")
        reason = _safe_code(reason_code, "worker_queue_reason_invalid")
        when = _timestamp(blocked_at)
        with self._transaction() as connection:
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
                "UPDATE worker_queue SET status = 'blocked', reason_code = ?, "
                "updated_at = ? WHERE queue_id = ? AND status = 'queued'",
                (reason, when, queue),
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
                    None,
                    None,
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
        resumed_at: datetime | str,
    ) -> WorkerQueueRecord:
        """Requeue a blocked item without checking its reason code."""

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
                elif not upgrade_to_abnormal and existing["check_status"] != "abnormal":
                    # A failed launch can have an incomplete daily check when its
                    # result could not be written. Formal maintenance intake may
                    # attach the incident to that exact check without rewriting
                    # the historical check status or manufacturing a new check.
                    failed_launch_incomplete_intake = False
                    if (
                        existing["check_status"] == "incomplete"
                        and check_status == "incomplete"
                    ):
                        launch = connection.execute(
                            "SELECT status FROM daily_launch WHERE source_launch_id = ?",
                            (launch_id,),
                        ).fetchone()
                        failed_launch_incomplete_intake = (
                            launch is not None and launch["status"] == "failed"
                        )
                    if not failed_launch_incomplete_intake:
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

    def finalize_job(
        self,
        *,
        job_id: str,
        final_status: str,
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

            connection.execute(
                "UPDATE maintenance_job SET status = ?, resume_count = ?, finished_at = ?, "
                "terminal_reason_code = ?, updated_at = ? WHERE job_id = ?",
                (
                    final_status,
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
        exception_reason_codes = event.get("exception_reason_codes", [])
        operation_context = event.get("operation_context", {})
        if (
            not isinstance(exception_reason_codes, list)
            or any(
                not isinstance(value, str)
                or _CODE_PATTERN.fullmatch(value) is None
                for value in exception_reason_codes
            )
            or not isinstance(operation_context, Mapping)
        ):
            raise MaintenanceStoreError("operation_event_invalid")
        try:
            exception_chain = json.dumps(event.get("exception_chain", []), separators=(",", ":"))
            exception_reason_codes_json = json.dumps(
                exception_reason_codes, separators=(",", ":")
            )
            operation_context_json = json.dumps(
                operation_context,
                separators=(",", ":"),
                ensure_ascii=True,
                sort_keys=True,
            )
            stack_frames = json.dumps(event.get("stack_frames", []), separators=(",", ":"))
            input_refs = json.dumps(event.get("input_refs", []), separators=(",", ":"))
            artifact_refs = json.dumps(event.get("artifact_refs", []), separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise MaintenanceStoreError("operation_event_invalid") from exc
        if len(operation_context_json) > 8192:
            raise MaintenanceStoreError("operation_context_too_large")
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO maintenance_operation_event "
                "(event_id, correlation_id, parent_operation, job_id, source_launch_id, "
                "source_run_id, incident_id, invocation_id, stage, attempt_id, operation, "
                "phase, observed_at, code_revision, reason_code, exception_chain_json, exception_errno, "
                "exception_reason_codes_json, operation_context_json, stack_frames_json, capture_location, input_refs_json, artifact_refs_json, "
                "primary_sink, fallback_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    exception_reason_codes_json,
                    operation_context_json,
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

    def list_operation_events(
        self,
        *,
        job_id: str,
        source_launch_id: str,
        source_run_id: str | None,
        invocation_id: str,
    ) -> tuple[dict[str, object], ...]:
        """Read trace rows for one exact workflow invocation and daily identity."""

        job = _safe_id(job_id, "job_id_invalid")
        launch = _safe_id(source_launch_id, "source_launch_id_invalid")
        invocation = _safe_id(invocation_id, "invocation_id_invalid")
        run = (
            _safe_id(source_run_id, "source_run_id_invalid")
            if source_run_id is not None
            else None
        )
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT event_id, correlation_id, parent_operation, job_id, "
                "source_launch_id, source_run_id, incident_id, invocation_id, "
                "stage, attempt_id, operation, phase, observed_at, code_revision, "
                "reason_code, exception_chain_json, exception_errno, "
                "exception_reason_codes_json, stack_frames_json, capture_location, "
                "input_refs_json, artifact_refs_json, primary_sink, fallback_status, "
                "recovered_at FROM maintenance_operation_event "
                "WHERE job_id = ? AND source_launch_id = ? AND source_run_id IS ? "
                "AND invocation_id = ? ORDER BY observed_at, rowid",
                (job, launch, run, invocation),
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
