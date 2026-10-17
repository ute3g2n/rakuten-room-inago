"""Transactional maintenance state, lease, handoff, and release-gate store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Mapping

from automation.inago.maintenance.safe_file import open_file_beneath
from automation.inago.maintenance.paths import (
    UnsafeMaintenancePathError,
    _has_reparse_point,
    resolve_maintenance_database_path,
)


SCHEMA_VERSION = 4
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
}
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
}


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
        job_resume_limit: int = 3,
        review_round_limit: int = 3,
    ) -> None:
        if (
            isinstance(job_resume_limit, bool)
            or not isinstance(job_resume_limit, int)
            or not 0 <= job_resume_limit <= 10
        ):
            raise MaintenanceStoreError("job_resume_limit_invalid")
        if (
            isinstance(review_round_limit, bool)
            or not isinstance(review_round_limit, int)
            or not 1 <= review_round_limit <= 10
        ):
            raise MaintenanceStoreError("review_round_limit_invalid")
        if daily_database_path is None and data_root is not None:
            daily_database_path = Path(data_root).expanduser() / "state.sqlite"
        self.job_resume_limit = job_resume_limit
        self.review_round_limit = review_round_limit
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
                        migration_version,
                        _timestamp(applied_at or datetime.now(timezone.utc)),
                        migration_description,
                    ),
                )
                connection.execute(f"PRAGMA user_version = {migration_version}")
                connection.commit()
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
            if check_status == "normal":
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
                if (
                    existing["status"] != launch_status
                    or existing["source_run_id"] != run_id
                    or existing["exit_code"] != exit_code
                    or existing["completed_at"] != completed
                    or existing["result_json"] != encoded_summary
                    or check is None
                    or check["source_run_id"] != run_id
                    or check["target_date"] != target
                    or check["check_status"] != check_status
                    or check["reason_code"] != reason_code
                    or check["observed_at"] != completed
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
                    or existing["check_status"] != check_status
                    or existing["reason_code"] != reason_code
                    or existing["observed_at"] != observed
                ):
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
                elif incident_values is not None:
                    raise IdempotencyConflictError("launch_incident_conflict")
                return DailyCheckResult(str(existing["check_id"]), False, incident_id)

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
            return DailyCheckResult(check_id, True, incident_id)

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
        if not cases or len(set(cases)) != len(cases):
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
            review_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM review_result "
                    "WHERE scope_type = 'release' AND scope_id = ?",
                    (release,),
                ).fetchone()[0]
            )
            if review_count >= self.review_round_limit:
                raise MaintenanceStoreError("review_round_limit_exceeded")
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

    def record_deployment(
        self,
        *,
        release_id: str,
        result_commit: str,
        config_version: str,
        status: str,
        deployed_at: datetime | str,
    ) -> str:
        release = _safe_id(release_id, "release_id_invalid")
        commit = _safe_id(result_commit, "result_commit_invalid")
        config = _safe_id(config_version, "config_version_invalid")
        if status not in _DEPLOYMENT_STATUSES:
            raise MaintenanceStoreError("deployment_status_invalid")
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
                "(deployment_id, release_id, attempt, result_commit, config_version, status, deployed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    deployment_id,
                    release,
                    attempt,
                    commit,
                    config,
                    status,
                    _timestamp(deployed_at),
                ),
            )
        return deployment_id

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
            if gate.status != "ready":
                return JobStartResult(False, None, gate.status, gate.reason_codes)
            incident_row = connection.execute(
                "SELECT status FROM incident WHERE incident_id = ?", (incident,)
            ).fetchone()
            if incident_row is None:
                raise MaintenanceStoreError("incident_not_found")
            if incident_row["status"] not in {"open", "in_progress", "blocked"}:
                raise MaintenanceStoreError("incident_not_active")
            existing = connection.execute(
                "SELECT job_id, status FROM maintenance_job "
                "WHERE incident_id = ? AND release_id = ?",
                (incident, release),
            ).fetchone()
            if existing is not None:
                return JobStartResult(
                    False,
                    str(existing["job_id"]),
                    gate.status,
                    ("job_already_exists",),
                )
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
            active_job = connection.execute(
                "SELECT job_id FROM maintenance_job "
                "WHERE status IN ('queued', 'running') LIMIT 1"
            ).fetchone()
            if active_job is not None:
                return JobStartResult(
                    False,
                    str(active_job["job_id"]),
                    gate.status,
                    ("maintenance_job_busy",),
                )
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
                reclaimable = current["state"] == "released"
                if current["state"] == "held":
                    expired = _parse_timestamp(when) >= _parse_timestamp(
                        current["expires_at"]
                    )
                    reclaimable = expired and prior_owner_observation in {
                        "dead",
                        "reused",
                    }
                    if not reclaimable:
                        raise LeaseBusyError("lease_busy")
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
        row = connection.execute(
            "SELECT owner_id, generation, state, expires_at FROM lease WHERE lease_key = ?",
            (lease.lease_key,),
        ).fetchone()
        if (
            row is None
            or row["owner_id"] != lease.owner_id
            or int(row["generation"]) != lease.generation
            or row["state"] != "held"
            or _parse_timestamp(now) >= _parse_timestamp(row["expires_at"])
        ):
            raise LeaseOwnershipError("lease_token_invalid_or_expired")

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
    ) -> StepAttemptResult:
        job = _safe_id(job_id, "job_id_invalid")
        step = _safe_code(step_key, "step_key_invalid")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise MaintenanceStoreError("step_sequence_invalid")
        when = _timestamp(started_at)
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
            if (
                job_row["lease_key"] is not None
                and job_row["lease_key"] != lease.lease_key
            ):
                raise MaintenanceStoreError("job_lease_key_conflict")
            if job_row["lease_generation"] is not None:
                current_generation = int(job_row["lease_generation"])
                if lease.generation < current_generation:
                    raise MaintenanceStoreError("lease_generation_not_advanced")
                if lease.generation > current_generation:
                    if int(job_row["resume_count"]) >= self.job_resume_limit:
                        raise MaintenanceStoreError("job_resume_limit_exceeded")
                    connection.execute(
                        "UPDATE maintenance_job SET lease_generation = ?, resume_count = ?, "
                        "updated_at = ? WHERE job_id = ?",
                        (
                            lease.generation,
                            int(job_row["resume_count"]) + 1,
                            when,
                            job,
                        ),
                    )
            else:
                connection.execute(
                    "UPDATE maintenance_job SET lease_key = ?, lease_generation = ?, "
                    "updated_at = ? WHERE job_id = ?",
                    (lease.lease_key, lease.generation, when, job),
                )
            prior_rows = connection.execute(
                "SELECT DISTINCT sequence FROM step_attempt "
                "WHERE job_id = ? AND sequence < ? ORDER BY sequence",
                (job, sequence),
            ).fetchall()
            prior_sequences = {int(row["sequence"]) for row in prior_rows}
            if prior_sequences != set(range(1, sequence)):
                raise MaintenanceStoreError("step_predecessor_incomplete")
            for prior_sequence in range(1, sequence):
                prior = connection.execute(
                    "SELECT s.attempt_id, s.job_id, s.step_key, s.status, "
                    "s.result_version, s.output_path, r.author_id "
                    "FROM step_attempt s "
                    "JOIN maintenance_job j ON j.job_id = s.job_id "
                    "JOIN release r ON r.release_id = j.release_id "
                    "WHERE s.job_id = ? AND s.sequence = ? "
                    "ORDER BY s.attempt_no DESC LIMIT 1",
                    (job, prior_sequence),
                ).fetchone()
                if prior is None or prior["status"] not in _STEP_TERMINAL_STATUSES:
                    raise MaintenanceStoreError("step_predecessor_incomplete")
                review = connection.execute(
                    "SELECT reviewer_id, reviewed_version, status FROM review_result "
                    "WHERE scope_type = 'step_attempt' AND scope_id = ? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (prior["attempt_id"],),
                ).fetchone()
                if (
                    review is None
                    or review["reviewer_id"] == prior["author_id"]
                    or review["reviewed_version"] != prior["result_version"]
                    or review["status"] != "approved"
                ):
                    raise MaintenanceStoreError("step_predecessor_not_approved")
                if (
                    prior["step_key"] == "maintenance_investigation"
                    and prior["status"] != "handoff_accepted"
                ):
                    raise MaintenanceStoreError("step_predecessor_handoff_not_accepted")
            latest = connection.execute(
                "SELECT * FROM step_attempt WHERE job_id = ? AND step_key = ? "
                "ORDER BY attempt_no DESC LIMIT 1",
                (job, step),
            ).fetchone()
            if latest is not None and int(latest["sequence"]) != sequence:
                raise MaintenanceStoreError("step_sequence_conflict")
            if latest is not None and latest["status"] in _STEP_TERMINAL_STATUSES:
                review = connection.execute(
                    "SELECT reviewed_version, status FROM review_result "
                    "WHERE scope_type = 'step_attempt' AND scope_id = ? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (latest["attempt_id"],),
                ).fetchone()
                if latest["status"] == "handoff_accepted":
                    if (
                        review is None
                        or review["reviewed_version"] != latest["result_version"]
                        or review["status"] != "approved"
                    ):
                        raise MaintenanceStoreError(
                            "handoff_review_not_approved_for_version"
                        )
                    return StepAttemptResult(
                        str(latest["attempt_id"]),
                        int(latest["attempt_no"]),
                        str(latest["status"]),
                        True,
                    )
                if (
                    review is not None
                    and review["reviewed_version"] == latest["result_version"]
                    and review["status"] == "approved"
                ):
                    return StepAttemptResult(
                        str(latest["attempt_id"]),
                        int(latest["attempt_no"]),
                        str(latest["status"]),
                        True,
                    )
                if review is None or review["status"] != "changes_requested":
                    raise MaintenanceStoreError("step_attempt_review_not_actionable")
                review_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_result r "
                        "JOIN step_attempt s ON s.attempt_id = r.scope_id "
                        "WHERE r.scope_type = 'step_attempt' AND s.job_id = ? "
                        "AND s.step_key = ? AND s.sequence = ?",
                        (job, step, sequence),
                    ).fetchone()[0]
                )
                if review_count >= self.review_round_limit:
                    raise MaintenanceStoreError("review_round_limit_exceeded")
            if latest is not None and latest["status"] == "running":
                if (
                    latest["lease_key"] == lease.lease_key
                    and int(latest["lease_generation"]) == lease.generation
                ):
                    raise MaintenanceStoreError("step_attempt_already_running")
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
                "lease_key, lease_generation, status, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
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
                ),
            )
            connection.execute(
                "UPDATE maintenance_job SET status = 'running', updated_at = ? WHERE job_id = ?",
                (when, job),
            )
            return StepAttemptResult(attempt_id, attempt_no, "running", False)

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
                review_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_result r "
                        "JOIN step_attempt s ON s.attempt_id = r.scope_id "
                        "WHERE r.scope_type = 'step_attempt' AND s.job_id = ? "
                        "AND s.step_key = ? AND s.sequence = ?",
                        (row["job_id"], row["step_key"], row["sequence"]),
                    ).fetchone()[0]
                )
                if review_count >= self.review_round_limit:
                    raise MaintenanceStoreError("review_round_limit_exceeded")

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
        with self._transaction() as connection:
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
            if review_count >= self.review_round_limit:
                raise MaintenanceStoreError("review_round_limit_exceeded")
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
            review_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM review_result r "
                    "JOIN step_attempt s ON s.attempt_id = r.scope_id "
                    "WHERE r.scope_type = 'step_attempt' AND s.job_id = ? "
                    "AND s.step_key = ? AND s.sequence = ?",
                    (row["job_id"], row["step_key"], row["sequence"]),
                ).fetchone()[0]
            )
            if review_count >= self.review_round_limit:
                raise MaintenanceStoreError("review_round_limit_exceeded")
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
                "j.resume_count AS job_resume_count, r.author_id "
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
                if (
                    lease.generation > int(row["job_lease_generation"])
                    and int(row["job_resume_count"]) >= self.job_resume_limit
                ):
                    raise HandoffContractError("handoff_resume_limit_exceeded")
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
            if (
                review is None
                or review["reviewer_id"] == row["author_id"]
                or review["reviewed_version"] != reviewed_version
                or review["status"] != "approved"
            ):
                raise HandoffContractError("handoff_review_not_approved_for_version")

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
                    or review_document.get("gate_status") != "approved"
                    or review_document.get("status") != "approved"
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
    ) -> JobFinalizeResult:
        """Finish an active job after checking lease and, for success, approvals."""

        job = _safe_id(job_id, "job_id_invalid")
        if final_status not in _JOB_TERMINAL_STATUSES:
            raise MaintenanceStoreError("job_final_status_invalid")
        if reason_code is not None:
            reason_code = _safe_code(reason_code, "reason_code_invalid")
        if final_status != "complete" and reason_code is None:
            raise MaintenanceStoreError("job_terminal_reason_required")
        if reason_code == "job_resume_limit_exceeded" and final_status != "blocked":
            raise MaintenanceStoreError("job_final_status_invalid")
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
                    and row["lease_key"] == lease.lease_key
                    and row["lease_generation"] is not None
                    and int(row["lease_generation"]) == lease.generation
                ):
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
            if row["lease_key"] is not None:
                if row["lease_key"] != lease.lease_key:
                    raise MaintenanceStoreError("job_lease_key_conflict")
                if row["lease_generation"] is None:
                    raise MaintenanceStoreError("job_lease_mismatch")
                current_generation = int(row["lease_generation"])
                if lease.generation < current_generation:
                    raise MaintenanceStoreError("lease_generation_not_advanced")
                if lease.generation > current_generation:
                    if final_resume_count >= self.job_resume_limit:
                        if (
                            final_status != "blocked"
                            or reason_code != "job_resume_limit_exceeded"
                        ):
                            raise MaintenanceStoreError("job_resume_limit_exceeded")
                    else:
                        final_resume_count += 1

            if final_status == "complete":
                gate = self._evaluate_release_gate(
                    connection, str(row["release_id"]), when
                )
                if gate.status != "ready":
                    raise MaintenanceStoreError("job_release_gate_not_ready")
                attempts = connection.execute(
                    "SELECT s.attempt_id, s.sequence, s.attempt_no, s.status, "
                    "s.result_version, r.author_id FROM step_attempt s "
                    "JOIN maintenance_job j ON j.job_id = s.job_id "
                    "JOIN release r ON r.release_id = j.release_id "
                    "WHERE s.job_id = ? ORDER BY s.sequence, s.attempt_no DESC",
                    (job,),
                ).fetchall()
                latest_by_sequence: dict[int, sqlite3.Row] = {}
                for attempt in attempts:
                    latest_by_sequence.setdefault(int(attempt["sequence"]), attempt)
                if not latest_by_sequence or set(latest_by_sequence) != set(
                    range(1, max(latest_by_sequence) + 1)
                ):
                    raise MaintenanceStoreError("job_steps_not_approved")
                for attempt in latest_by_sequence.values():
                    if (
                        attempt["status"] not in _STEP_TERMINAL_STATUSES
                        or attempt["result_version"] is None
                    ):
                        raise MaintenanceStoreError("job_steps_not_approved")
                    review = connection.execute(
                        "SELECT reviewer_id, reviewed_version, status "
                        "FROM review_result WHERE scope_type = 'step_attempt' "
                        "AND scope_id = ? ORDER BY rowid DESC LIMIT 1",
                        (attempt["attempt_id"],),
                    ).fetchone()
                    if (
                        review is None
                        or review["reviewer_id"] == attempt["author_id"]
                        or review["reviewed_version"] != attempt["result_version"]
                        or review["status"] != "approved"
                    ):
                        raise MaintenanceStoreError("job_steps_not_approved")

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
            if count >= self.review_round_limit:
                raise MaintenanceStoreError("review_round_limit_exceeded")
            return count + 1
        finally:
            connection.close()
