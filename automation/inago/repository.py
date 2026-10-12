"""SQLite schema lifecycle and atomic draft persistence for Inago."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from automation.inago.clock import Clock, SystemClock
from automation.inago.contracts import (
    ArtifactRecord,
    DraftCommitRequest,
    DraftCommitResult,
    IdempotencyConflict,
    InjectedFailure,
    PersistenceContractError,
    RejectedContract,
    UnsupportedSchemaVersion,
)


CURRENT_SCHEMA_VERSION = 2
READABLE_SCHEMA_VERSIONS = frozenset({2})
SQLITE_BUSY_TIMEOUT_MS = 5_000
JST = ZoneInfo("Asia/Tokyo")
_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "entities.schema.json"
_INITIAL_SQL_PATH = Path(__file__).resolve().parent / "sql" / "001_initial.sql"
_MIGRATION_SQL_PATHS = {
    2: Path(__file__).resolve().parent
    / "sql"
    / "002_human_action_actual_posted_at.sql",
}

_ENTITY_TABLES = frozenset(
    {
        "settings_version",
        "run",
        "search_attempt",
        "source_post",
        "offer",
        "candidate_decision",
        "keyword_snapshot",
        "theme_selection",
        "draft",
        "generation_attempt",
        "reservation",
        "publication_event",
        "human_action",
        "exclusion_key",
        "metric_observation",
        "revenue_order",
        "artifact",
        "recovery",
        "backup",
        "restore_check",
        "ai_job",
    }
)


@lru_cache(maxsize=1)
def _schema_document() -> dict[str, Any]:
    try:
        document = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(document)
        return document
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise PersistenceContractError("entity schema cannot be loaded") from exc


def validate_entity(entity: str, value: Mapping[str, Any]) -> bool:
    """Validate one JSON-shaped entity against the versioned Q-derived schema."""

    definitions = _schema_document().get("$defs", {})
    if entity not in definitions:
        raise PersistenceContractError(f"unknown schema entity: {entity}")
    root_schema = {
        "$schema": _schema_document()["$schema"],
        "$defs": definitions,
        "$ref": f"#/$defs/{entity}",
    }
    try:
        Draft202012Validator(
            root_schema,
            format_checker=FormatChecker(),
        ).validate(dict(value))
    except ValidationError as exc:
        field_path = ".".join(str(part) for part in exc.absolute_path) or entity
        raise RejectedContract(f"{entity} schema violation at {field_path}") from exc
    return True


def _execute_sql_file(connection: sqlite3.Connection, path: Path) -> None:
    script = path.read_text(encoding="utf-8")
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            pending = ""
            if statement:
                connection.execute(statement)
    if pending.strip():
        raise PersistenceContractError("initial SQL contains an incomplete statement")


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _stored_schema_version(connection: sqlite3.Connection) -> int | None:
    if "schema_migration" not in _user_tables(connection):
        return None
    versions = [
        int(row[0])
        for row in connection.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        )
    ]
    if not versions:
        raise UnsupportedSchemaVersion("schema_migration has no applied version")
    if versions != list(range(1, versions[-1] + 1)):
        raise UnsupportedSchemaVersion(
            f"unsupported applied schema versions: {versions}"
        )
    if versions[-1] > CURRENT_SCHEMA_VERSION:
        raise UnsupportedSchemaVersion(
            f"database schema {versions[-1]} is newer than this reader"
        )
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if user_version != versions[-1]:
        raise UnsupportedSchemaVersion(
            "PRAGMA user_version disagrees with schema_migration"
        )
    return versions[-1]


def initialize_database(connection: sqlite3.Connection) -> int:
    """Initialize empty storage and atomically advance supported migrations."""

    existing_version = _stored_schema_version(connection)
    existing_tables = _user_tables(connection)
    if existing_version is not None:
        missing = _ENTITY_TABLES - existing_tables
        if missing:
            raise UnsupportedSchemaVersion(
                f"schema v{existing_version} is missing required tables: "
                + ", ".join(sorted(missing))
            )
        if existing_version == CURRENT_SCHEMA_VERSION:
            return existing_version
    elif existing_tables:
        raise UnsupportedSchemaVersion(
            "database has tables but no recognized schema version"
        )

    while True:
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = _stored_schema_version(connection)
            tables = _user_tables(connection)
            if version is None:
                if tables:
                    raise UnsupportedSchemaVersion(
                        "database has tables but no recognized schema version"
                    )
                applied_at = SystemClock().now_jst().isoformat()
                _execute_sql_file(connection, _INITIAL_SQL_PATH)
                connection.execute(
                    """
                    INSERT INTO schema_migration(version, applied_at, description)
                    VALUES (?, ?, ?)
                    """,
                    (1, applied_at, "initial Q1.3 entity contract"),
                )
                connection.execute("PRAGMA user_version = 1")
                version = 1
                tables = _user_tables(connection)

            missing = _ENTITY_TABLES - tables
            if missing:
                raise UnsupportedSchemaVersion(
                    f"schema v{version} is missing required tables: "
                    + ", ".join(sorted(missing))
                )

            if version >= CURRENT_SCHEMA_VERSION:
                connection.commit()
                return version

            next_version = version + 1
            migration_path = _MIGRATION_SQL_PATHS.get(next_version)
            if migration_path is None or not migration_path.is_file():
                raise UnsupportedSchemaVersion(
                    f"no migration is available from schema v{version}"
                )
            _execute_sql_file(connection, migration_path)
            applied_at = SystemClock().now_jst().isoformat()
            connection.execute(
                """
                INSERT INTO schema_migration(version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                (
                    next_version,
                    applied_at,
                    "preserve actual posted time on pending human reports",
                ),
            )
            connection.execute(f"PRAGMA user_version = {next_version}")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise


def open_database(paths: Any) -> sqlite3.Connection:
    """Open the explicitly selected data root and apply safe SQLite settings."""

    try:
        root = Path(paths.data_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        database_path = (root / "state.sqlite").resolve()
        if not database_path.is_relative_to(root):
            raise PersistenceContractError(
                "database path escaped the explicit data root"
            )
        connection = sqlite3.connect(
            str(database_path),
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, PersistenceContractError):
            raise
        raise PersistenceContractError("database path cannot be opened") from exc

    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        initialize_database(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def schema_version(connection: sqlite3.Connection) -> int:
    """Return the readable schema version, rejecting unknown layouts."""

    version = _stored_schema_version(connection)
    if version is None or version not in READABLE_SCHEMA_VERSIONS:
        raise UnsupportedSchemaVersion("database has no readable schema version")
    return version


def _timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise RejectedContract(f"{field} must be an offset-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RejectedContract(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RejectedContract(f"{field} must include a timezone offset")
    return parsed


class ReceiptRevisionConflict(RejectedContract):
    """A report was based on a stale per-offer receipt revision."""

    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__("report receipt revision is stale")


def _report_payload_for_comparison(request: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        request["action_id"],
        request["action"],
        request["offer_key"],
        request["draft_id"],
        request["draft_version"],
        request["expected_receipt_revision"],
        request["reason"],
        request["correction_ref"],
        request["actual_posted_at"],
    )


def record_pending_human_action(
    connection: sqlite3.Connection,
    request: Mapping[str, Any],
    *,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Atomically save one night report without changing managed posting state."""

    try:
        payload = dict(request)
    except (TypeError, ValueError) as exc:
        raise RejectedContract("human report must be an object") from exc

    expected_fields = {
        "action_id",
        "offer_key",
        "draft_id",
        "draft_version",
        "expected_receipt_revision",
        "action",
        "reason",
        "correction_ref",
        "actual_posted_at",
    }
    if set(payload) != expected_fields:
        raise RejectedContract("human report fields do not match the request contract")

    action_id = payload.get("action_id")
    action = payload.get("action")
    offer_key = payload.get("offer_key")
    draft_id = payload.get("draft_id")
    draft_version = payload.get("draft_version")
    expected_revision = payload.get("expected_receipt_revision")
    reason = payload.get("reason")
    correction_ref = payload.get("correction_ref")
    actual_posted_at = payload.get("actual_posted_at")

    for field, value in (
        ("action_id", action_id),
        ("offer_key", offer_key),
        ("draft_id", draft_id),
    ):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise RejectedContract(f"{field} is required")
    if not isinstance(action, str) or action not in {
        "posted",
        "skipped",
        "correction",
    }:
        raise RejectedContract("unsupported human report action")
    if (
        isinstance(draft_version, bool)
        or not isinstance(draft_version, int)
        or draft_version < 1
    ):
        raise RejectedContract("draft_version must be a positive integer")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise RejectedContract("expected_receipt_revision must be non-negative")
    if reason is not None and not isinstance(reason, str):
        raise RejectedContract("reason must be text or null")
    if correction_ref is not None and (
        not isinstance(correction_ref, str)
        or not correction_ref.strip()
        or correction_ref != correction_ref.strip()
    ):
        raise RejectedContract("correction_ref must be a non-empty ID or null")
    if action == "correction":
        if reason is None or not reason.strip():
            raise RejectedContract("correction reason is required")
        if correction_ref is None:
            raise RejectedContract("correction_ref is required for a correction")
    elif correction_ref is not None:
        raise RejectedContract("correction_ref is only valid for corrections")
    if actual_posted_at is not None:
        _timestamp(actual_posted_at, "actual_posted_at")

    request_key = _report_payload_for_comparison(payload)
    system_clock = clock or SystemClock()
    now = system_clock.now_jst()
    if now.tzinfo is None or now.utcoffset() is None:
        raise RejectedContract("report clock must return an offset-aware time")
    reported_at = now.astimezone(JST).isoformat()

    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT * FROM human_action WHERE action_id = ?", (action_id,)
        ).fetchone()
        if existing is not None:
            existing_key = (
                existing["action_id"],
                existing["action"],
                existing["offer_key"],
                existing["draft_id"],
                existing["draft_version"],
                existing["prior_revision"],
                existing["reason"],
                existing["old_event_ref"],
                existing["actual_posted_at"],
            )
            if existing_key != request_key:
                raise IdempotencyConflict(
                    "action_id was reused with different report content"
                )
            connection.commit()
            return {
                "action_id": existing["action_id"],
                "receipt_revision": int(existing["receipt_revision"]),
                "apply_status": "pending",
                "saved": True,
            }

        draft = connection.execute(
            """
            SELECT 1 FROM draft
             WHERE offer_key = ? AND draft_id = ? AND version = ?
            """,
            (offer_key, draft_id, draft_version),
        ).fetchone()
        if draft is None:
            raise RejectedContract("report does not identify an existing draft version")

        if correction_ref is not None:
            event = connection.execute(
                """
                SELECT 1 FROM publication_event
                 WHERE event_id = ? AND offer_key = ?
                """,
                (correction_ref, offer_key),
            ).fetchone()
            if event is None:
                raise RejectedContract(
                    "correction_ref does not identify an event for this offer"
                )

        current_revision = int(
            connection.execute(
                "SELECT COALESCE(MAX(receipt_revision), 0) "
                "FROM human_action WHERE offer_key = ?",
                (offer_key,),
            ).fetchone()[0]
        )
        if current_revision != expected_revision:
            raise ReceiptRevisionConflict(current_revision)

        next_revision = current_revision + 1
        entity = {
            "action_id": action_id,
            "action": action,
            "offer_key": offer_key,
            "draft_id": draft_id,
            "draft_version": draft_version,
            "prior_revision": current_revision,
            "receipt_revision": next_revision,
            "reason": reason,
            "reported_at": reported_at,
            "actual_posted_at": actual_posted_at,
            "old_event_ref": correction_ref,
            "new_body": None,
            "apply_status": "pending",
            "applied_at": None,
            "applied_run_id": None,
            "apply_error": None,
        }
        validate_entity("human_action", entity)
        connection.execute(
            """
            INSERT INTO human_action
                (action_id, action, offer_key, draft_id, draft_version,
                 prior_revision, receipt_revision, reason, reported_at,
                 actual_posted_at, old_event_ref, new_body, apply_status,
                 applied_at, applied_run_id, apply_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                action,
                offer_key,
                draft_id,
                draft_version,
                current_revision,
                next_revision,
                reason,
                reported_at,
                actual_posted_at,
                correction_ref,
                None,
                "pending",
                None,
                None,
                None,
            ),
        )
        connection.commit()
        return {
            "action_id": action_id,
            "receipt_revision": next_revision,
            "apply_status": "pending",
            "saved": True,
        }
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def read_human_action_state(connection: sqlite3.Connection) -> dict[str, Any]:
    """Read drafts and report state from one consistent, read-only snapshot."""

    connection.execute("BEGIN")
    try:
        drafts = [
            {
                "offer_key": row["offer_key"],
                "draft_id": row["draft_id"],
                "draft_version": int(row["version"]),
                "status": row["status"],
            }
            for row in connection.execute(
                """
                SELECT offer_key, draft_id, version, status
                  FROM draft
                 ORDER BY offer_key, version DESC, draft_id
                """
            )
        ]
        receipt_revisions = {
            row["offer_key"]: int(row["receipt_revision"])
            for row in connection.execute(
                """
                SELECT o.offer_key, COALESCE(MAX(h.receipt_revision), 0) AS receipt_revision
                  FROM offer AS o
                  LEFT JOIN human_action AS h ON h.offer_key = o.offer_key
                 GROUP BY o.offer_key
                 ORDER BY o.offer_key
                """
            )
        }
        pending_reports = [
            {
                "action_id": row["action_id"],
                "action": row["action"],
                "offer_key": row["offer_key"],
                "draft_id": row["draft_id"],
                "draft_version": int(row["draft_version"]),
                "receipt_revision": int(row["receipt_revision"]),
                "reason": row["reason"],
                "reported_at": row["reported_at"],
                "actual_posted_at": row["actual_posted_at"],
                "correction_ref": row["old_event_ref"],
                "apply_status": row["apply_status"],
            }
            for row in connection.execute(
                """
                SELECT action_id, action, offer_key, draft_id, draft_version,
                       receipt_revision, reason, reported_at, actual_posted_at,
                       old_event_ref, apply_status
                  FROM human_action
                 WHERE apply_status = 'pending'
                 ORDER BY reported_at, receipt_revision, action_id
                """
            )
        ]
        connection.commit()
        return {
            "drafts": drafts,
            "receipt_revisions": receipt_revisions,
            "pending_reports": pending_reports,
        }
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _database_root(connection: sqlite3.Connection) -> Path:
    row = connection.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        raise PersistenceContractError(
            "runtime database must be a file under data_root"
        )
    return Path(str(row[2])).resolve().parent


def _artifact_payload(
    connection: sqlite3.Connection,
    artifact: ArtifactRecord,
) -> dict[str, Any]:
    root = _database_root(connection)
    path = artifact.path.expanduser().resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise RejectedContract("response artifact is missing or outside data_root")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RejectedContract("response artifact cannot be read") from exc
    actual_content_hash = hashlib.sha256(raw).hexdigest()
    if actual_content_hash != artifact.content_hash:
        raise RejectedContract(
            "response artifact content does not match its runtime record"
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RejectedContract("response artifact is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise RejectedContract("response artifact must contain a JSON object")
    validate_entity("copy_response", payload)
    return payload


def _request_payload(request: DraftCommitRequest) -> dict[str, Any]:
    try:
        serialized = json.dumps(
            dict(request.response),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        payload = json.loads(serialized)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RejectedContract("copy response must be a strict JSON object") from exc
    if not isinstance(payload, dict):
        raise RejectedContract("copy response must be a JSON object")
    validate_entity("copy_response", payload)
    return payload


def _validate_artifact_record(artifact: ArtifactRecord) -> None:
    validate_entity(
        "artifact",
        {
            "artifact_ref": artifact.artifact_ref,
            "path": str(artifact.path),
            "hash": artifact.content_hash,
            "created_at": artifact.created_at,
            "source_kind": artifact.source_kind,
            "retention_expires_at": artifact.retention_expires_at,
            "status": artifact.status,
        },
    )
    if artifact.source_kind != "generation" or artifact.status != "available":
        raise RejectedContract(
            "draft response artifact must be an available generation artifact"
        )


def _validate_request(request: DraftCommitRequest) -> dict[str, Any]:
    for field in (
        "run_id",
        "job_id",
        "settings_version",
        "draft_id",
        "generator_version",
        "pattern_version",
        "input_hash",
    ):
        value = getattr(request, field)
        if not isinstance(value, str) or not value.strip():
            raise RejectedContract(f"{field} is required")
    if request.attempt_no < 1 or request.attempt_no > 3:
        raise RejectedContract(
            "attempt_no must stay within the three-call generation limit"
        )
    if not request.input_refs or any(not item.strip() for item in request.input_refs):
        raise RejectedContract("input_refs must identify the actual job inputs")
    _timestamp(request.generated_at, "generated_at")
    _timestamp(request.committed_at, "committed_at")
    _validate_artifact_record(request.artifact)
    return _request_payload(request)


def _existing_commit(
    connection: sqlite3.Connection,
    request: DraftCommitRequest,
    payload: Mapping[str, Any],
) -> DraftCommitResult | None:
    existing = connection.execute(
        """
        SELECT * FROM draft
         WHERE draft_id = ? OR (run_id = ? AND offer_key = ?)
        """,
        (request.draft_id, request.run_id, payload["offer_key"]),
    ).fetchone()
    if existing is None:
        return None

    expected = (
        request.draft_id,
        payload["offer_key"],
        request.run_id,
        payload["draft_version"],
        payload["body"],
        request.generated_at,
        request.committed_at,
        request.generator_version,
        request.pattern_version,
        request.artifact.artifact_ref,
        payload["pattern_id"],
        json.dumps(payload["evidence_refs"], ensure_ascii=False, separators=(",", ":")),
    )
    actual = (
        existing["draft_id"],
        existing["offer_key"],
        existing["run_id"],
        existing["version"],
        existing["body"],
        existing["generated_at"],
        existing["committed_at"],
        existing["generator_version"],
        existing["pattern_version"],
        existing["artifact_ref"],
        existing["pattern_id"],
        existing["evidence_refs_json"],
    )
    if actual != expected:
        raise IdempotencyConflict(
            "stable draft identity was reused with different content"
        )

    run = connection.execute(
        "SELECT settings_version, target_date FROM run WHERE run_id = ?",
        (request.run_id,),
    ).fetchone()
    if run is None or run["settings_version"] != request.settings_version:
        raise IdempotencyConflict(
            "stable draft identity was reused with a different settings version"
        )

    job = connection.execute(
        "SELECT * FROM ai_job WHERE job_id = ?", (request.job_id,)
    ).fetchone()
    expected_refs_json = json.dumps(list(request.input_refs), ensure_ascii=False)
    expected_payload = json.loads(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    if (
        job is None
        or job["run_id"] != request.run_id
        or job["target_date"] != run["target_date"]
        or job["settings_version"] != request.settings_version
        or job["input_refs_json"] != expected_refs_json
        or job["input_hash"] != request.input_hash
        or job["attempt"] != request.attempt_no
        or job["artifact_ref"] != request.artifact.artifact_ref
        or job["result_status"] != "success"
        or job["adoption_status"] != "accepted"
        or job["accepted_at"] is None
        or job["task_id"] != "T02"
        or job["agent_id"] != "AG-COPY"
        or job["skill_name"] != "rr-inago-copywriter"
        or json.loads(job["input_refs_json"]) != list(request.input_refs)
        or json.loads(job["payload_json"]) != expected_payload
    ):
        raise IdempotencyConflict(
            "stable draft identity was reused with a different AI job or input"
        )
    if not set(payload["evidence_refs"]).issubset(set(request.input_refs)):
        raise IdempotencyConflict(
            "stable draft response references an input the job did not receive"
        )

    attempt = connection.execute(
        """
        SELECT * FROM generation_attempt
         WHERE run_id = ? AND offer_key = ? AND attempt_no = ?
        """,
        (request.run_id, payload["offer_key"], request.attempt_no),
    ).fetchone()
    if (
        attempt is None
        or attempt["outcome"] != "success"
        or attempt["artifact_ref"] != request.artifact.artifact_ref
        or attempt["draft_id"] != request.draft_id
    ):
        raise PersistenceContractError(
            "committed draft is detached from its successful generation attempt"
        )

    reservation = connection.execute(
        "SELECT COUNT(*) FROM reservation WHERE offer_key = ? AND draft_id = ?",
        (payload["offer_key"], request.draft_id),
    ).fetchone()[0]
    run_count = connection.execute(
        "SELECT committed_count FROM run WHERE run_id = ?", (request.run_id,)
    ).fetchone()
    if reservation < 1 or run_count is None or int(run_count[0]) < 1:
        raise PersistenceContractError(
            "committed draft has no matching reservation or daily count"
        )
    return DraftCommitResult(
        draft_id=request.draft_id,
        offer_key=str(payload["offer_key"]),
        draft_version=int(payload["draft_version"]),
        committed_count=int(run_count[0]),
        already_committed=True,
    )


def _check_job_contract(
    connection: sqlite3.Connection,
    request: DraftCommitRequest,
    payload: Mapping[str, Any],
    target_date: str,
) -> sqlite3.Row:
    job = connection.execute(
        "SELECT * FROM ai_job WHERE job_id = ?", (request.job_id,)
    ).fetchone()
    if job is None:
        raise RejectedContract("AI job envelope is missing")
    expected_refs = json.loads(job["input_refs_json"])
    expected_payload = json.loads(job["payload_json"]) if job["payload_json"] else None
    if (
        job["run_id"] != request.run_id
        or job["target_date"] != target_date
        or job["settings_version"] != request.settings_version
        or job["input_refs_json"]
        != json.dumps(list(request.input_refs), ensure_ascii=False)
        or job["input_hash"] != request.input_hash
        or job["attempt"] != request.attempt_no
        or job["artifact_ref"] != request.artifact.artifact_ref
        or job["result_status"] != "success"
        or job["adoption_status"] != "pending"
        or job["task_id"] != "T02"
        or job["agent_id"] != "AG-COPY"
        or job["skill_name"] != "rr-inago-copywriter"
        or expected_refs != list(request.input_refs)
        or expected_payload != dict(payload)
    ):
        raise RejectedContract("AI job, response, or input reference does not match")
    if not set(payload["evidence_refs"]).issubset(set(expected_refs)):
        raise RejectedContract(
            "response evidence_refs include an input the job did not receive"
        )
    return job


def _insert_artifact(connection: sqlite3.Connection, artifact: ArtifactRecord) -> None:
    stored = connection.execute(
        "SELECT * FROM artifact WHERE artifact_ref = ?", (artifact.artifact_ref,)
    ).fetchone()
    values = (
        str(artifact.path.resolve()),
        artifact.content_hash,
        artifact.created_at,
        artifact.source_kind,
        artifact.retention_expires_at,
        artifact.status,
    )
    if stored is not None:
        actual = tuple(
            stored[key]
            for key in (
                "path",
                "hash",
                "created_at",
                "source_kind",
                "retention_expires_at",
                "status",
            )
        )
        if actual != values:
            raise IdempotencyConflict(
                "artifact_ref already identifies different runtime content"
            )
        return
    connection.execute(
        """
        INSERT INTO artifact
            (artifact_ref, path, hash, created_at, source_kind,
             retention_expires_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (artifact.artifact_ref, *values),
    )


def commit_draft(
    connection: sqlite3.Connection,
    request: DraftCommitRequest,
    *,
    clock: Clock | None = None,
    failpoint: str | None = None,
) -> DraftCommitResult:
    """Commit one accepted response, active reservation, and run count atomically."""

    if failpoint not in (None, "before_commit", "after_commit"):
        raise ValueError("unsupported draft commit failpoint")
    payload = _validate_request(request)
    artifact_payload = _artifact_payload(connection, request.artifact)
    if artifact_payload != payload:
        raise RejectedContract(
            "saved response artifact differs from the offered response"
        )
    system_clock = clock or SystemClock()
    now = system_clock.now_jst()
    if now.tzinfo is None or now.utcoffset() is None:
        raise RejectedContract("commit clock must return an offset-aware time")
    accepted_at = now.astimezone(JST).isoformat()

    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = _existing_commit(connection, request, payload)
        if existing is not None:
            connection.rollback()
            return existing

        run = connection.execute(
            "SELECT * FROM run WHERE run_id = ?", (request.run_id,)
        ).fetchone()
        if run is None:
            raise RejectedContract("daily run does not exist")
        if run["mode"] != "daily":
            raise RejectedContract("draft commits require a daily run")
        if run["status"] != "running":
            raise RejectedContract("daily run is not accepting draft commits")
        if run["settings_version"] != request.settings_version:
            raise RejectedContract(
                "draft settings version differs from the started run"
            )
        if now.astimezone(JST) >= _timestamp(run["draft_deadline"], "draft_deadline"):
            raise RejectedContract("draft deadline has passed")
        if int(run["committed_count"]) >= 15:
            raise RejectedContract("daily committed_count limit 15 has been reached")

        offer_key = str(payload["offer_key"])
        version = int(payload["draft_version"])
        offer = connection.execute(
            "SELECT * FROM offer WHERE offer_key = ?", (offer_key,)
        ).fetchone()
        if offer is None:
            raise RejectedContract(
                "response offer_key does not identify a stored offer"
            )
        job = _check_job_contract(
            connection, request, payload, target_date=str(run["target_date"])
        )
        attempt = connection.execute(
            """
            SELECT * FROM generation_attempt
             WHERE run_id = ? AND offer_key = ? AND attempt_no = ?
            """,
            (request.run_id, offer_key, request.attempt_no),
        ).fetchone()
        if (
            attempt is None
            or attempt["outcome"] != "success"
            or attempt["artifact_ref"] != request.artifact.artifact_ref
            or attempt["draft_id"] is not None
        ):
            raise RejectedContract(
                "successful generation attempt does not match the response"
            )

        exclusion = connection.execute(
            "SELECT 1 FROM exclusion_key WHERE offer_key = ? AND effective = 1",
            (offer_key,),
        ).fetchone()
        active_reservation = connection.execute(
            "SELECT 1 FROM reservation WHERE offer_key = ? AND status = 'active'",
            (offer_key,),
        ).fetchone()
        pending_post = connection.execute(
            """
            SELECT 1 FROM human_action
             WHERE offer_key = ? AND action = 'posted' AND apply_status = 'pending'
            """,
            (offer_key,),
        ).fetchone()
        if exclusion is not None:
            raise RejectedContract("offer already has an effective posted exclusion")
        if active_reservation is not None:
            raise RejectedContract("offer already has an active reservation")
        if pending_post is not None:
            raise RejectedContract("offer has a pending posted report")

        _insert_artifact(connection, request.artifact)
        connection.execute(
            """
            INSERT INTO draft
                (draft_id, offer_key, run_id, version, body, generated_at,
                 committed_at, generator_version, pattern_version, status,
                 artifact_ref, pattern_id, evidence_refs_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?, ?)
            """,
            (
                request.draft_id,
                offer_key,
                request.run_id,
                version,
                payload["body"],
                request.generated_at,
                request.committed_at,
                request.generator_version,
                request.pattern_version,
                request.artifact.artifact_ref,
                payload["pattern_id"],
                json.dumps(
                    payload["evidence_refs"], ensure_ascii=False, separators=(",", ":")
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO reservation
                (reservation_id, offer_key, draft_id, status, created_at,
                 released_at, release_reason)
            VALUES (?, ?, ?, 'active', ?, NULL, NULL)
            """,
            (
                f"RES-{request.draft_id}",
                offer_key,
                request.draft_id,
                accepted_at,
            ),
        )
        updated_attempt = connection.execute(
            """
            UPDATE generation_attempt SET draft_id = ?
             WHERE generation_attempt_id = ? AND draft_id IS NULL AND outcome = 'success'
            """,
            (request.draft_id, attempt["generation_attempt_id"]),
        )
        if updated_attempt.rowcount != 1:
            raise RejectedContract(
                "generation attempt was already attached to another draft"
            )
        accepted_job = connection.execute(
            """
            UPDATE ai_job SET adoption_status = 'accepted', accepted_at = ?
             WHERE job_id = ? AND adoption_status = 'pending' AND result_status = 'success'
            """,
            (accepted_at, job["job_id"]),
        )
        if accepted_job.rowcount != 1:
            raise RejectedContract("AI job was no longer pending at commit time")
        count_update = connection.execute(
            """
            UPDATE run SET committed_count = committed_count + 1
             WHERE run_id = ? AND committed_count < 15 AND status = 'running'
            """,
            (request.run_id,),
        )
        if count_update.rowcount != 1:
            raise RejectedContract("daily committed_count could not be incremented")

        if failpoint == "before_commit":
            raise InjectedFailure("before_commit")
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

    if failpoint == "after_commit":
        raise InjectedFailure("after_commit")

    committed_count = int(
        connection.execute(
            "SELECT committed_count FROM run WHERE run_id = ?", (request.run_id,)
        ).fetchone()[0]
    )
    return DraftCommitResult(
        draft_id=request.draft_id,
        offer_key=str(payload["offer_key"]),
        draft_version=int(payload["draft_version"]),
        committed_count=committed_count,
        already_committed=False,
    )
