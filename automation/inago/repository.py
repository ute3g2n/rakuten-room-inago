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

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from automation.inago.clock import Clock, SystemClock
from automation.jsonschema_formats import rfc3339_format_checker
from automation.inago.contracts import (
    AIJobResult,
    AISubjobResult,
    ArtifactRecord,
    DraftCommitRequest,
    DraftCommitResult,
    IdempotencyConflict,
    InjectedFailure,
    PersistenceContractError,
    RejectedContract,
    UnsupportedSchemaVersion,
)

CURRENT_SCHEMA_VERSION = 4
READABLE_SCHEMA_VERSIONS = frozenset({4})
LEGACY_UNASSIGNED_THEME_ID = "legacy-unassigned"
SQLITE_BUSY_TIMEOUT_MS = 5_000
JST = ZoneInfo("Asia/Tokyo")
_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "entities.schema.json"
_INITIAL_SQL_PATH = Path(__file__).resolve().parent / "sql" / "001_initial.sql"
_MIGRATION_SQL_PATHS = {
    2: Path(__file__).resolve().parent
    / "sql"
    / "002_human_action_actual_posted_at.sql",
    3: Path(__file__).resolve().parent / "sql" / "003_search_rules_14.sql",
    4: Path(__file__).resolve().parent / "sql" / "004_trends_retry_state.sql",
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
_SCHEMA_V4_TABLES = frozenset({"trends_acquisition", "trends_acquisition_attempt"})


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
            format_checker=rfc3339_format_checker(),
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

    if connection.in_transaction:
        raise PersistenceContractError(
            "schema initialization requires an exclusively owned idle connection"
        )
    existing_version = _stored_schema_version(connection)
    existing_tables = _user_tables(connection)
    if existing_version is not None:
        required_tables = _ENTITY_TABLES | (
            _SCHEMA_V4_TABLES
            if existing_version == CURRENT_SCHEMA_VERSION
            else frozenset()
        )
        missing = required_tables - existing_tables
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

    if existing_version == 2:
        _verify_rebuild_objects(connection)
    foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    # SQLite's supported table-rebuild procedure requires this change outside
    # BEGIN. It affects this owned connection only; checks run before each commit.
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        return _advance_database_migrations(connection)
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.execute(f"PRAGMA foreign_keys = {foreign_keys}")


def offer_needs_name_observation(main_theme_id: object, facts_json: object) -> bool:
    """Return whether an identity-only or name-unknown offer needs a later refresh."""

    if main_theme_id == LEGACY_UNASSIGNED_THEME_ID:
        return True
    if not isinstance(facts_json, str):
        raise PersistenceContractError("offer facts JSON is not text")
    try:
        facts = json.loads(facts_json)
    except json.JSONDecodeError as exc:
        raise PersistenceContractError("offer facts JSON is invalid") from exc
    if not isinstance(facts, list):
        raise PersistenceContractError("offer facts JSON is not an array")
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("field") != "name":
            continue
        if (
            fact.get("availability") != "observed"
            or not isinstance(fact.get("value"), str)
            or not fact["value"].strip()
        ):
            return True
    return False


def _advance_database_migrations(connection: sqlite3.Connection) -> int:
    """Advance one atomic migration at a time on the caller's owned connection."""

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

            required_tables = _ENTITY_TABLES | (
                _SCHEMA_V4_TABLES if version >= CURRENT_SCHEMA_VERSION else frozenset()
            )
            missing = required_tables - tables
            if missing:
                raise UnsupportedSchemaVersion(
                    f"schema v{version} is missing required tables: "
                    + ", ".join(sorted(missing))
                )

            if version >= CURRENT_SCHEMA_VERSION:
                _verify_foreign_keys(connection)
                connection.commit()
                return version

            next_version = version + 1
            if next_version == 3:
                _verify_rebuild_objects(connection)
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
                    {
                        2: "preserve actual posted time on pending human reports",
                        3: "Q1.4 search counts, query kinds and three-month observations",
                        4: "durable per-theme Google Trends retry state",
                    }[next_version],
                ),
            )
            connection.execute(f"PRAGMA user_version = {next_version}")
            _verify_foreign_keys(connection)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise


def _verify_foreign_keys(connection: sqlite3.Connection) -> None:
    """Reject migration commit if rebuilt tables lose a referenced record."""

    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise PersistenceContractError("migration would leave broken foreign keys")


def _verify_rebuild_objects(connection: sqlite3.Connection) -> None:
    """Keep unexpected user schema objects intact instead of dropping them."""

    objects = connection.execute(
        "SELECT type,name FROM sqlite_master WHERE sql IS NOT NULL "
        "AND type IN ('index','trigger','view') "
        "AND (tbl_name IN ('run','search_attempt','keyword_snapshot') OR type='view')"
    ).fetchall()
    if any(tuple(row) != ("index", "one_daily_run_per_target_date") for row in objects):
        raise UnsupportedSchemaVersion(
            "Q1.4 migration requires review of custom indexes, triggers or views"
        )


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


def pending_posted_offer_keys(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Return products held by a pending posted report, including late reports."""

    rows = connection.execute(
        "SELECT DISTINCT offer_key FROM human_action "
        "WHERE action='posted' AND apply_status='pending' ORDER BY offer_key"
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def apply_human_action_in_transaction(
    connection: sqlite3.Connection,
    action: Mapping[str, Any],
    run_id: str,
    applied_at: str,
) -> None:
    """Apply one human report and its applied marker within the caller's transaction."""

    if not connection.in_transaction:
        raise PersistenceContractError(
            "human report application requires a transaction"
        )
    action_id = str(action["action_id"])
    action_kind = str(action["action"])
    offer_key = str(action["offer_key"])
    draft_id = str(action["draft_id"])
    draft_version = int(action["draft_version"])
    draft = connection.execute(
        "SELECT draft_id, offer_key, version, status FROM draft "
        "WHERE offer_key=? AND draft_id=? AND version=?",
        (offer_key, draft_id, draft_version),
    ).fetchone()
    if draft is None:
        raise RejectedContract("reported draft version is missing")

    def refresh_exclusion() -> list[sqlite3.Row]:
        events = connection.execute(
            "SELECT event_id, draft_id, draft_version FROM publication_event "
            "WHERE offer_key=? AND valid=1 ORDER BY event_id",
            (offer_key,),
        ).fetchall()
        if not events:
            connection.execute(
                "DELETE FROM exclusion_key WHERE offer_key=?", (offer_key,)
            )
            return []
        event_refs = json.dumps(
            [str(row["event_id"]) for row in events],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """INSERT INTO exclusion_key
                   (offer_key, reason, evidence_refs_json, effective, updated_at)
               VALUES (?, 'actual_posted', ?, 1, ?)
               ON CONFLICT(offer_key) DO UPDATE SET
                   reason='actual_posted', evidence_refs_json=excluded.evidence_refs_json,
                   effective=1, updated_at=excluded.updated_at""",
            (offer_key, event_refs, applied_at),
        )
        return events

    def release_active(posted_draft_id: str, posted_reason: str = "posted") -> None:
        active = connection.execute(
            "SELECT reservation_id, draft_id FROM reservation "
            "WHERE offer_key=? AND status='active' ORDER BY reservation_id",
            (offer_key,),
        ).fetchall()
        for reservation in active:
            reason = (
                posted_reason
                if reservation["draft_id"] == posted_draft_id
                else "valid_prior_post"
            )
            connection.execute(
                "UPDATE reservation SET status='released', released_at=?, "
                "release_reason=? WHERE reservation_id=? AND status='active'",
                (applied_at, reason, reservation["reservation_id"]),
            )

    if action_kind == "posted":
        connection.execute(
            """INSERT INTO publication_event
                   (event_id, offer_key, draft_id, draft_version, reported_at,
                    actual_posted_at, room_url, evidence_kind, valid, revision)
               VALUES (?, ?, ?, ?, ?, ?, NULL, 'human_check', 1, 0)""",
            (
                f"PUB-{action_id}",
                offer_key,
                draft_id,
                draft_version,
                action["reported_at"],
                action["actual_posted_at"],
            ),
        )
        connection.execute(
            "UPDATE draft SET status='posted' WHERE draft_id=? AND version=?",
            (draft_id, draft_version),
        )
        later = connection.execute(
            "SELECT draft_id, version FROM draft WHERE offer_key=? AND version>? "
            "ORDER BY version, draft_id",
            (offer_key, draft_version),
        ).fetchall()
        for newer in later:
            valid_newer = connection.execute(
                "SELECT 1 FROM publication_event WHERE offer_key=? AND draft_id=? "
                "AND draft_version=? AND valid=1 LIMIT 1",
                (offer_key, newer["draft_id"], newer["version"]),
            ).fetchone()
            if valid_newer is None:
                connection.execute(
                    "UPDATE draft SET status='do_not_post_already_posted' "
                    "WHERE draft_id=? AND version=?",
                    (newer["draft_id"], newer["version"]),
                )
        release_active(draft_id)
        refresh_exclusion()

    elif action_kind == "skipped":
        valid_event = connection.execute(
            "SELECT 1 FROM publication_event WHERE offer_key=? AND valid=1 LIMIT 1",
            (offer_key,),
        ).fetchone()
        if valid_event is None:
            connection.execute(
                "UPDATE draft SET status='skipped' WHERE draft_id=? AND version=? "
                "AND status NOT IN ('posted','do_not_post_already_posted')",
                (draft_id, draft_version),
            )
            connection.execute(
                "UPDATE reservation SET status='released', released_at=?, "
                "release_reason='skipped' WHERE offer_key=? AND draft_id=? "
                "AND status='active'",
                (applied_at, offer_key, draft_id),
            )
        elif draft["status"] not in {"posted", "do_not_post_already_posted"}:
            connection.execute(
                "UPDATE draft SET status='do_not_post_already_posted' "
                "WHERE draft_id=? AND version=?",
                (draft_id, draft_version),
            )
        if valid_event is not None:
            release_active(draft_id, posted_reason="skipped")
        refresh_exclusion()

    elif action_kind == "correction":
        event_ref = action["old_event_ref"]
        reason = action["reason"]
        if not isinstance(reason, str) or not reason.strip() or not event_ref:
            raise RejectedContract("correction reason or reference is missing")
        event = connection.execute(
            "SELECT * FROM publication_event WHERE event_id=?",
            (event_ref,),
        ).fetchone()
        if (
            event is None
            or event["offer_key"] != offer_key
            or event["draft_id"] != draft_id
            or int(event["draft_version"] or 0) != draft_version
        ):
            raise RejectedContract("correction reference does not match reported draft")
        if int(event["valid"]) == 1:
            connection.execute(
                "UPDATE publication_event SET valid=0, revision=revision+1 "
                "WHERE event_id=? AND valid=1",
                (event_ref,),
            )
        valid_events = refresh_exclusion()
        current_valid_version = any(
            row["draft_id"] == draft_id
            and int(row["draft_version"] or 0) == draft_version
            for row in valid_events
        )
        if current_valid_version:
            corrected_status = "posted"
        elif valid_events:
            corrected_status = "do_not_post_already_posted"
        else:
            corrected_status = "committed"
        connection.execute(
            "UPDATE draft SET status=? WHERE draft_id=? AND version=?",
            (corrected_status, draft_id, draft_version),
        )
        if valid_events:
            posted_event_draft = next(
                (str(row["draft_id"]) for row in valid_events if row["draft_id"]), ""
            )
            release_active(posted_event_draft)
        else:
            connection.execute(
                "UPDATE draft SET status='committed' WHERE offer_key=? "
                "AND status='do_not_post_already_posted'",
                (offer_key,),
            )
            active = connection.execute(
                "SELECT draft_id FROM reservation WHERE offer_key=? AND status='active'",
                (offer_key,),
            ).fetchone()
            if active is None:
                original_reservation = connection.execute(
                    "SELECT reservation_id FROM reservation WHERE offer_key=? AND draft_id=? "
                    "ORDER BY created_at DESC, reservation_id DESC LIMIT 1",
                    (offer_key, draft_id),
                ).fetchone()
                if original_reservation is None:
                    raise RejectedContract(
                        "original reservation is missing for correction"
                    )
                connection.execute(
                    "UPDATE reservation SET status='active', released_at=NULL, "
                    "release_reason=NULL WHERE reservation_id=?",
                    (original_reservation["reservation_id"],),
                )
            else:
                active_draft = connection.execute(
                    "SELECT status FROM draft WHERE offer_key=? AND draft_id=?",
                    (offer_key, active["draft_id"]),
                ).fetchone()
                if (
                    active_draft is not None
                    and active_draft["status"] == "do_not_post_already_posted"
                ):
                    connection.execute(
                        "UPDATE draft SET status='committed' WHERE offer_key=? AND draft_id=?",
                        (offer_key, active["draft_id"]),
                    )
    else:
        raise RejectedContract("unsupported human report action")

    updated = connection.execute(
        "UPDATE human_action SET apply_status='applied', applied_at=?, "
        "applied_run_id=?, apply_error=NULL WHERE action_id=? AND apply_status='pending'",
        (applied_at, run_id, action_id),
    )
    if updated.rowcount != 1:
        raise RejectedContract("human report pending state changed during application")


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


def load_copy_generation_state(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    offer_key: str,
) -> dict[str, Any]:
    """Read the current offer, draft, and retry checkpoint for copy generation."""

    run = connection.execute("SELECT * FROM run WHERE run_id = ?", (run_id,)).fetchone()
    offer = connection.execute(
        "SELECT * FROM offer WHERE offer_key = ?", (offer_key,)
    ).fetchone()
    if run is None or offer is None:
        raise RejectedContract("copy run or offer does not exist")
    draft = connection.execute(
        "SELECT * FROM draft WHERE run_id = ? AND offer_key = ?",
        (run_id, offer_key),
    ).fetchone()
    attempt = connection.execute(
        """
        SELECT * FROM generation_attempt
         WHERE run_id = ? AND offer_key = ?
         ORDER BY attempt_no DESC LIMIT 1
        """,
        (run_id, offer_key),
    ).fetchone()
    copy_job = None
    artifact = None
    if attempt is not None and attempt["outcome"] == "success":
        copy_job = connection.execute(
            """
            SELECT * FROM ai_job
             WHERE run_id = ? AND agent_id = 'AG-COPY' AND artifact_ref = ?
             ORDER BY started_at DESC LIMIT 1
            """,
            (run_id, attempt["artifact_ref"]),
        ).fetchone()
        artifact = connection.execute(
            "SELECT * FROM artifact WHERE artifact_ref = ?",
            (attempt["artifact_ref"],),
        ).fetchone()
    return {
        "run": dict(run),
        "offer": dict(offer),
        "draft": dict(draft) if draft is not None else None,
        "attempt": dict(attempt) if attempt is not None else None,
        "copy_job": dict(copy_job) if copy_job is not None else None,
        "artifact": dict(artifact) if artifact is not None else None,
    }


def read_existing_draft_result(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    offer_key: str,
) -> DraftCommitResult | None:
    """Return a saved draft without regenerating it, after checking its record."""

    draft = connection.execute(
        "SELECT * FROM draft WHERE run_id = ? AND offer_key = ?",
        (run_id, offer_key),
    ).fetchone()
    if draft is None:
        return None
    run = connection.execute(
        "SELECT committed_count, settings_version FROM run WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if run is None or run["settings_version"] != draft["pattern_version"]:
        raise RejectedContract("saved draft no longer matches its run version")
    if draft["status"] == "committed":
        stored_artifact = connection.execute(
            "SELECT * FROM artifact WHERE artifact_ref = ?",
            (draft["artifact_ref"],),
        ).fetchone()
        if stored_artifact is None:
            raise PersistenceContractError("saved draft artifact record is missing")
        artifact = ArtifactRecord(
            artifact_ref=stored_artifact["artifact_ref"],
            path=Path(stored_artifact["path"]),
            content_hash=stored_artifact["hash"],
            source_kind=stored_artifact["source_kind"],
            created_at=stored_artifact["created_at"],
            retention_expires_at=stored_artifact["retention_expires_at"],
            status=stored_artifact["status"],
        )
        payload = _artifact_payload(connection, artifact)
        if (
            payload["offer_key"] != draft["offer_key"]
            or payload["draft_version"] != draft["version"]
            or payload["body"] != draft["body"]
            or payload["pattern_id"] != draft["pattern_id"]
            or json.loads(draft["evidence_refs_json"]) != payload["evidence_refs"]
        ):
            raise PersistenceContractError(
                "saved draft differs from its response artifact"
            )
    return DraftCommitResult(
        draft_id=draft["draft_id"],
        offer_key=draft["offer_key"],
        draft_version=int(draft["version"]),
        committed_count=int(run["committed_count"]),
        already_committed=True,
    )


def reserve_generation_attempt(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    offer_key: str,
    settings_version: str,
    generation_attempt_id: str,
    attempt_no: int,
    started_at: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Durably reserve one of the three copy calls before invoking an AI runner."""

    _timestamp(started_at, "started_at")
    validate_entity(
        "generation_attempt",
        {
            "generation_attempt_id": generation_attempt_id,
            "run_id": run_id,
            "offer_key": offer_key,
            "draft_id": None,
            "attempt_no": attempt_no,
            "outcome": "in_progress",
            "artifact_ref": None,
            "error_code": None,
            "started_at": started_at,
            "ended_at": None,
        },
    )
    system_clock = clock or SystemClock()
    now = system_clock.now_jst()
    if now.tzinfo is None or now.utcoffset() is None:
        raise RejectedContract("generation clock must return an offset-aware time")
    connection.execute("BEGIN IMMEDIATE")
    try:
        run = connection.execute(
            "SELECT * FROM run WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run is None or run["mode"] != "daily" or run["status"] != "running":
            raise RejectedContract("copy generation requires a running daily run")
        if run["settings_version"] != settings_version:
            raise RejectedContract("copy settings version differs from the started run")
        if now.astimezone(JST) >= _timestamp(run["draft_deadline"], "draft_deadline"):
            raise RejectedContract("copy generation deadline has passed")
        if int(run["committed_count"]) >= 15:
            raise RejectedContract("daily committed_count limit 15 has been reached")
        if (
            connection.execute(
                "SELECT 1 FROM draft WHERE run_id = ? AND offer_key = ?",
                (run_id, offer_key),
            ).fetchone()
            is not None
        ):
            raise RejectedContract("offer already has a saved draft for this run")
        if (
            connection.execute(
                "SELECT 1 FROM exclusion_key WHERE offer_key = ? AND effective = 1",
                (offer_key,),
            ).fetchone()
            is not None
        ):
            raise RejectedContract("offer already has an effective posted exclusion")
        if (
            connection.execute(
                "SELECT 1 FROM reservation WHERE offer_key = ? AND status = 'active'",
                (offer_key,),
            ).fetchone()
            is not None
        ):
            raise RejectedContract("offer already has an active reservation")
        if (
            connection.execute(
                """
            SELECT 1 FROM human_action
             WHERE offer_key = ? AND action = 'posted' AND apply_status = 'pending'
            """,
                (offer_key,),
            ).fetchone()
            is not None
        ):
            raise RejectedContract("offer has a pending posted report")

        latest = connection.execute(
            """
            SELECT attempt_no, outcome FROM generation_attempt
             WHERE run_id = ? AND offer_key = ? ORDER BY attempt_no DESC LIMIT 1
            """,
            (run_id, offer_key),
        ).fetchone()
        expected_no = 1 if latest is None else int(latest["attempt_no"]) + 1
        if latest is not None and latest["outcome"] == "in_progress":
            raise RejectedContract("copy generation already has an in-progress attempt")
        if attempt_no != expected_no or attempt_no > 3:
            raise RejectedContract(
                "copy generation attempt is not the next allowed call"
            )
        connection.execute(
            """
            INSERT INTO generation_attempt
                (generation_attempt_id, run_id, offer_key, draft_id, attempt_no,
                 outcome, artifact_ref, error_code, started_at, ended_at)
            VALUES (?, ?, ?, NULL, ?, 'in_progress', NULL, NULL, ?, NULL)
            """,
            (generation_attempt_id, run_id, offer_key, attempt_no, started_at),
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    return {
        "generation_attempt_id": generation_attempt_id,
        "attempt_no": attempt_no,
        "outcome": "in_progress",
        "started_at": started_at,
    }


def finish_generation_attempt(
    connection: sqlite3.Connection,
    *,
    generation_attempt_id: str,
    run_id: str,
    offer_key: str,
    attempt_no: int,
    outcome: str,
    artifact_ref: str | None,
    error_code: str | None,
    ended_at: str,
    artifacts: tuple[ArtifactRecord, ...] = (),
    ai_jobs: tuple[Mapping[str, Any], ...] = (),
) -> None:
    """Atomically persist one call result and its observable AI-job records."""

    if outcome not in {"success", "empty", "error"}:
        raise RejectedContract("finished generation outcome is invalid")
    _timestamp(ended_at, "ended_at")
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = connection.execute(
            """
            SELECT * FROM generation_attempt
             WHERE generation_attempt_id = ?
            """,
            (generation_attempt_id,),
        ).fetchone()
        if (
            current is None
            or current["run_id"] != run_id
            or current["offer_key"] != offer_key
            or current["attempt_no"] != attempt_no
        ):
            raise RejectedContract("generation attempt reservation is missing")
        validate_entity(
            "generation_attempt",
            {
                "generation_attempt_id": generation_attempt_id,
                "run_id": run_id,
                "offer_key": offer_key,
                "draft_id": current["draft_id"],
                "attempt_no": attempt_no,
                "outcome": outcome,
                "artifact_ref": artifact_ref,
                "error_code": error_code,
                "started_at": current["started_at"],
                "ended_at": ended_at,
            },
        )
        if current["outcome"] not in {"in_progress", outcome}:
            raise RejectedContract(
                "generation attempt was already completed differently"
            )
        if current["outcome"] == outcome and (
            current["artifact_ref"] != artifact_ref
            or current["error_code"] != error_code
            or current["ended_at"] != ended_at
        ):
            raise RejectedContract("generation attempt result changed during replay")

        for artifact in artifacts:
            _insert_artifact(connection, artifact)
        for record in ai_jobs:
            _insert_ai_job_record(connection, record)
        if current["outcome"] == "in_progress":
            updated = connection.execute(
                """
                UPDATE generation_attempt
                   SET outcome = ?, artifact_ref = ?, error_code = ?, ended_at = ?
                 WHERE generation_attempt_id = ? AND outcome = 'in_progress'
                """,
                (outcome, artifact_ref, error_code, ended_at, generation_attempt_id),
            )
            if updated.rowcount != 1:
                raise RejectedContract("generation attempt changed before completion")
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _insert_ai_job_record(
    connection: sqlite3.Connection, record: Mapping[str, Any]
) -> None:
    required = {
        "job_id",
        "run_id",
        "protocol_version",
        "schema_version",
        "task_id",
        "agent_id",
        "skill_name",
        "attempt",
        "target_date",
        "settings_version",
        "input_refs",
        "evidence_refs",
        "input_hash",
        "deadline_at",
        "started_at",
        "finished_at",
        "resolved_model",
        "reasoning_setting",
        "auth_mode",
        "result_status",
        "payload",
        "error",
        "artifact_ref",
        "accepted_at",
        "adoption_status",
    }
    if set(record) != required:
        raise RejectedContract(
            "AI job record fields do not match the persistence contract"
        )
    payload = {
        **dict(record),
        "input_refs": list(record["input_refs"]),
        "evidence_refs": list(record["evidence_refs"]),
    }
    validate_entity("ai_job", payload)
    try:
        payload_json = (
            json.dumps(
                record["payload"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if record["payload"] is not None
            else None
        )
        error_json = (
            json.dumps(
                record["error"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if record["error"] is not None
            else None
        )
    except (TypeError, ValueError) as exc:
        raise RejectedContract("AI job payload and error must be strict JSON") from exc
    values = (
        record["run_id"],
        record["protocol_version"],
        record["schema_version"],
        record["task_id"],
        record["agent_id"],
        record["skill_name"],
        record["attempt"],
        record["target_date"],
        record["settings_version"],
        json.dumps(payload["input_refs"], ensure_ascii=False),
        json.dumps(payload["evidence_refs"], ensure_ascii=False),
        record["input_hash"],
        record["deadline_at"],
        record["started_at"],
        record["finished_at"],
        record["resolved_model"],
        record["reasoning_setting"],
        record["auth_mode"],
        record["result_status"],
        payload_json,
        error_json,
        record["artifact_ref"],
        record["accepted_at"],
        record["adoption_status"],
    )
    existing = connection.execute(
        "SELECT * FROM ai_job WHERE job_id = ?", (record["job_id"],)
    ).fetchone()
    columns = (
        "run_id",
        "protocol_version",
        "schema_version",
        "task_id",
        "agent_id",
        "skill_name",
        "attempt",
        "target_date",
        "settings_version",
        "input_refs_json",
        "evidence_refs_json",
        "input_hash",
        "deadline_at",
        "started_at",
        "finished_at",
        "resolved_model",
        "reasoning_setting",
        "auth_mode",
        "result_status",
        "payload_json",
        "error_json",
        "artifact_ref",
        "accepted_at",
        "adoption_status",
    )
    if existing is not None:
        if tuple(existing[column] for column in columns) != values:
            raise RejectedContract(
                "AI job ID already identifies different execution data"
            )
        return
    connection.execute(
        """
        INSERT INTO ai_job
            (job_id, run_id, protocol_version, schema_version, task_id, agent_id,
             skill_name, attempt, target_date, settings_version, input_refs_json,
             evidence_refs_json, input_hash, deadline_at, started_at, finished_at,
             resolved_model, reasoning_setting, auth_mode, result_status, payload_json,
             error_json, artifact_ref, accepted_at, adoption_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (record["job_id"], *values),
    )


def _key_job_records(
    connection: sqlite3.Connection,
    result: AIJobResult,
    *,
    adoption_status: str,
    accepted_at: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        result.result_status != "success"
        or result.adoption_status != "pending"
        or result.task_id != "T02"
        or result.agent_id != "AI-O"
        or result.skill_name != "rakuten-room-inago"
        or not isinstance(result.payload, Mapping)
        or result.error is not None
        or result.artifact is None
        or result.artifact.source_kind != "daily"
        or result.artifact.retention_expires_at is None
        or result.artifact.artifact_ref != f"{result.job_id}-a{result.attempt}-response"
        or len(result.input_refs) != 1
        or len(result.subjobs) != 1
    ):
        raise RejectedContract("successful KEY job result is incomplete or invalid")
    child = result.subjobs[0]
    if (
        child.job_id != f"{result.job_id}-a{result.attempt}-child-01"
        or child.parent_job_id != result.job_id
        or child.run_id != result.run_id
        or child.attempt != result.attempt
        or child.task_id != "T02"
        or child.agent_id != "AG-KEY"
        or child.skill_name != "rr-inago-keywords"
        or child.result_status != "success"
        or child.settings_version != result.settings_version
        or child.target_date != result.target_date
        or child.input_refs != result.input_refs
    ):
        raise RejectedContract("successful KEY specialist result is mismatched")
    run = connection.execute(
        "SELECT target_date,settings_version,status FROM run WHERE run_id=?",
        (result.run_id,),
    ).fetchone()
    if (
        run is None
        or run[0] != result.target_date
        or run[1] != result.settings_version
        or run[2] not in {"running", "partial"}
    ):
        raise RejectedContract("successful KEY job no longer matches its daily run")
    if result.started_at is None or result.finished_at is None:
        raise RejectedContract("successful KEY job timestamps are missing")
    _timestamp(result.started_at, "started_at")
    _timestamp(result.finished_at, "finished_at")
    if accepted_at is not None:
        _timestamp(accepted_at, "accepted_at")

    common = {
        "protocol_version": "1",
        "schema_version": "1",
        "attempt": result.attempt,
        "target_date": result.target_date,
        "settings_version": result.settings_version,
        "deadline_at": result.deadline_at,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "artifact_ref": result.artifact.artifact_ref,
        "accepted_at": accepted_at,
        "adoption_status": adoption_status,
        "error": None,
    }
    root_record = {
        **common,
        "job_id": result.job_id,
        "run_id": result.run_id,
        "task_id": result.task_id,
        "agent_id": result.agent_id,
        "skill_name": result.skill_name,
        "input_refs": result.input_refs,
        "evidence_refs": result.evidence_refs,
        "input_hash": result.input_hash,
        "resolved_model": result.resolved_model,
        "reasoning_setting": result.reasoning_setting,
        "auth_mode": result.auth_mode,
        "result_status": result.result_status,
        "payload": dict(result.payload),
    }
    child_record = {
        **common,
        "job_id": child.job_id,
        "run_id": child.run_id,
        "task_id": child.task_id,
        "agent_id": child.agent_id,
        "skill_name": child.skill_name,
        "input_refs": child.input_refs,
        "evidence_refs": child.evidence_refs,
        "input_hash": child.input_hash,
        "resolved_model": child.resolved_model,
        "reasoning_setting": child.reasoning_setting,
        "auth_mode": child.auth_mode,
        "result_status": child.result_status,
        "payload": dict(child.payload),
    }
    return root_record, child_record


def _job_transaction(connection: sqlite3.Connection, savepoint: str) -> bool:
    owns_transaction = not connection.in_transaction
    connection.execute(
        "BEGIN IMMEDIATE" if owns_transaction else f"SAVEPOINT {savepoint}"
    )
    return owns_transaction


def persist_pending_key_job_result(
    connection: sqlite3.Connection, result: AIJobResult
) -> None:
    """Save a successful KEY response as pending so interruption cannot lose it."""

    root_record, child_record = _key_job_records(
        connection, result, adoption_status="pending", accepted_at=None
    )
    savepoint = "inago_key_pending"
    owns_transaction = _job_transaction(connection, savepoint)
    try:
        _insert_artifact(connection, result.artifact)
        _insert_ai_job_record(connection, root_record)
        _insert_ai_job_record(connection, child_record)
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE {savepoint}")
    except BaseException:
        if owns_transaction:
            connection.rollback()
        else:
            connection.execute(f"ROLLBACK TO {savepoint}")
            connection.execute(f"RELEASE {savepoint}")
        raise


def accept_pending_key_job_result(
    connection: sqlite3.Connection,
    result: AIJobResult,
    *,
    accepted_at: str,
) -> None:
    """Accept pending AI-O and AG-KEY rows inside snapshot-adoption transaction."""

    if not connection.in_transaction:
        raise PersistenceContractError(
            "AI job adoption requires the caller's transaction"
        )
    root_record, child_record = _key_job_records(
        connection, result, adoption_status="pending", accepted_at=None
    )
    _timestamp(accepted_at, "accepted_at")
    _insert_artifact(connection, result.artifact)
    for record in (root_record, child_record):
        _insert_ai_job_record(connection, record)
        updated = connection.execute(
            "UPDATE ai_job SET adoption_status='accepted',accepted_at=? "
            "WHERE job_id=? AND run_id=? AND attempt=? AND result_status='success' "
            "AND adoption_status='pending' AND accepted_at IS NULL "
            "AND artifact_ref=? AND input_hash=?",
            (
                accepted_at,
                record["job_id"],
                record["run_id"],
                record["attempt"],
                record["artifact_ref"],
                record["input_hash"],
            ),
        )
        if updated.rowcount != 1:
            raise RejectedContract("pending KEY job changed before adoption")


def load_pending_key_job_result(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    target_date: str,
    settings_version: str,
    snapshot_id: str,
    data_root: str | Path,
) -> AIJobResult | None:
    """Rebuild the one pending KEY result for a saved snapshot without AI rerun."""

    expected_refs = json.dumps([snapshot_id], ensure_ascii=False, separators=(",", ":"))

    def fetch_dicts(sql: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
        cursor = connection.execute(sql, parameters)
        names = [str(column[0]) for column in cursor.description or ()]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    roots = fetch_dicts(
        "SELECT * FROM ai_job WHERE run_id=? AND target_date=? "
        "AND settings_version=? AND task_id='T02' AND agent_id='AI-O' "
        "AND skill_name='rakuten-room-inago' AND result_status='success' "
        "AND adoption_status='pending' AND accepted_at IS NULL AND input_refs_json=?",
        (run_id, target_date, settings_version, expected_refs),
    )
    if not roots:
        return None
    if len(roots) != 1:
        raise RejectedContract("multiple pending KEY jobs match one snapshot")
    root = roots[0]
    try:
        root_input_refs = json.loads(root["input_refs_json"])
        root_evidence_refs = json.loads(root["evidence_refs_json"])
        root_payload = json.loads(root["payload_json"])
        root_error = json.loads(root["error_json"]) if root["error_json"] else None
    except (TypeError, json.JSONDecodeError) as exc:
        raise RejectedContract("pending KEY root record is malformed") from exc
    if (
        root_input_refs != [snapshot_id]
        or not isinstance(root_evidence_refs, list)
        or not isinstance(root_payload, dict)
        or root_error is not None
        or root["artifact_ref"] is None
    ):
        raise RejectedContract("pending KEY root record cannot be resumed")
    child_prefix = f"{root['job_id']}-a{root['attempt']}-child-%"
    children = fetch_dicts(
        "SELECT * FROM ai_job WHERE run_id=? AND target_date=? "
        "AND settings_version=? AND task_id='T02' AND agent_id='AG-KEY' "
        "AND skill_name='rr-inago-keywords' AND result_status='success' "
        "AND adoption_status='pending' AND accepted_at IS NULL "
        "AND artifact_ref=? AND job_id LIKE ?",
        (
            run_id,
            target_date,
            settings_version,
            root["artifact_ref"],
            child_prefix,
        ),
    )
    if len(children) != 1:
        raise RejectedContract("pending KEY specialist record is missing or ambiguous")
    child = children[0]
    try:
        child_input_refs = json.loads(child["input_refs_json"])
        child_evidence_refs = json.loads(child["evidence_refs_json"])
        child_payload = json.loads(child["payload_json"])
        child_error = json.loads(child["error_json"]) if child["error_json"] else None
    except (TypeError, json.JSONDecodeError) as exc:
        raise RejectedContract("pending KEY specialist record is malformed") from exc
    if (
        child_input_refs != root_input_refs
        or not isinstance(child_evidence_refs, list)
        or not isinstance(child_payload, dict)
        or child_error is not None
    ):
        raise RejectedContract("pending KEY specialist record cannot be resumed")
    artifacts = fetch_dicts(
        "SELECT * FROM artifact WHERE artifact_ref=?",
        (root["artifact_ref"],),
    )
    if len(artifacts) != 1:
        raise RejectedContract("pending KEY response artifact is missing")
    artifact_row = artifacts[0]
    if (
        artifact_row["status"] != "available"
        or artifact_row["source_kind"] != "daily"
        or artifact_row["retention_expires_at"] is None
    ):
        raise RejectedContract("pending KEY response artifact is unavailable")
    from automation.inago.retention import validate_data_root

    root_path = validate_data_root(data_root)
    path = Path(artifact_row["path"])
    if path.is_symlink():
        raise RejectedContract("pending KEY response artifact path is unsafe")
    try:
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(root_path)
        content = resolved_path.read_bytes()
    except (OSError, RuntimeError, ValueError) as exc:
        raise RejectedContract("pending KEY response artifact is unavailable") from exc
    if hashlib.sha256(content).hexdigest() != artifact_row["hash"]:
        raise RejectedContract("pending KEY response artifact content changed")

    artifact = ArtifactRecord(
        artifact_ref=str(artifact_row["artifact_ref"]),
        path=resolved_path,
        content_hash=str(artifact_row["hash"]),
        source_kind=str(artifact_row["source_kind"]),
        created_at=str(artifact_row["created_at"]),
        retention_expires_at=str(artifact_row["retention_expires_at"]),
        status=str(artifact_row["status"]),
    )
    subjob = AISubjobResult(
        job_id=str(child["job_id"]),
        parent_job_id=str(root["job_id"]),
        run_id=str(child["run_id"]),
        attempt=int(child["attempt"]),
        task_id=str(child["task_id"]),
        agent_id=str(child["agent_id"]),
        skill_name=str(child["skill_name"]),
        target_date=str(child["target_date"]),
        settings_version=str(child["settings_version"]),
        input_refs=tuple(child_input_refs),
        evidence_refs=tuple(child_evidence_refs),
        input_hash=str(child["input_hash"]),
        deadline_at=str(child["deadline_at"]),
        result_status="success",
        payload=child_payload,
        resolved_model=str(child["resolved_model"]),
        reasoning_setting=str(child["reasoning_setting"]),
        auth_mode=str(child["auth_mode"]),
    )
    return AIJobResult(
        job_id=str(root["job_id"]),
        run_id=str(root["run_id"]),
        attempt=int(root["attempt"]),
        task_id=str(root["task_id"]),
        agent_id=str(root["agent_id"]),
        skill_name=str(root["skill_name"]),
        target_date=str(root["target_date"]),
        settings_version=str(root["settings_version"]),
        input_refs=tuple(root_input_refs),
        evidence_refs=tuple(root_evidence_refs),
        input_hash=str(root["input_hash"]),
        deadline_at=str(root["deadline_at"]),
        result_status="success",
        adoption_status="pending",
        payload=root_payload,
        error=None,
        artifact=artifact,
        subjobs=(subjob,),
        resolved_model=str(root["resolved_model"]),
        reasoning_setting=str(root["reasoning_setting"]),
        auth_mode=str(root["auth_mode"]),
        started_at=str(root["started_at"]),
        finished_at=str(root["finished_at"]),
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
