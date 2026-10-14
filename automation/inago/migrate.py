"""Import only positively evidenced legacy posts into the new SQLite history."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from automation.inago.contracts import PersistenceContractError
from automation.inago.paths import PROJECT_ROOT, SAFE_FIXTURE_BASE
from automation.inago.repository import LEGACY_UNASSIGNED_THEME_ID, validate_entity
from automation.jsonschema_formats import rfc3339_format_checker
from automation.inago.selection import (
    identify_offer,
    identify_offer_from_saved_url,
    normalize_offer_url,
)


JST = ZoneInfo("Asia/Tokyo")
MigrationMode = Literal["dry_run", "apply"]
MigrationClass = Literal[
    "legacy_evidence", "human_statement", "planned", "draft", "unknown"
]
_LEGACY_SCHEMA_ROOT = PROJECT_ROOT / "rakuten-room-kai" / "orchestrator" / "schemas"
_RESULT_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "migration.schema.json"
)
_LEGACY_SCHEMAS = {
    "posts": _LEGACY_SCHEMA_ROOT / "posts-log-record.schema.json",
    "products": _LEGACY_SCHEMA_ROOT / "product-ledger.schema.json",
    "status": _LEGACY_SCHEMA_ROOT / "post-status.schema.json",
    "drafts": _LEGACY_SCHEMA_ROOT / "drafts.schema.json",
}
_SOURCE_VERSIONS = {
    "posts": "posts-log-record/v1",
    "products": "product-ledger/v1",
    "status": "post-status/v1",
    "drafts": "drafts/v1",
}


class MigrationInputError(PersistenceContractError):
    """Legacy input or destination paths cannot be handled safely."""


class MigrationConflictError(MigrationInputError):
    """An original reference conflicts with already stored migration state."""


@dataclass(frozen=True, slots=True)
class MigrationResult:
    mode: MigrationMode
    observed_at: datetime
    source_files: tuple[dict[str, str], ...]
    items: tuple[dict[str, Any], ...]
    supplementation_required: tuple[dict[str, Any], ...]
    summary: dict[str, int]
    selection_allowed: bool

    @property
    def source_record_count(self) -> int:
        return self.summary["source_records"]

    @property
    def evidence_count(self) -> int:
        return self.summary["legacy_evidence"]

    @property
    def statement_count(self) -> int:
        return self.summary["human_statements"]

    @property
    def planned_count(self) -> int:
        return self.summary["planned"]

    @property
    def draft_count(self) -> int:
        return self.summary["drafts"]

    @property
    def unknown_count(self) -> int:
        return self.summary["unknown"]

    @property
    def created_publications(self) -> int:
        return self.summary["created_publications"]

    @property
    def already_imported_count(self) -> int:
        return self.summary["already_imported"]

    @property
    def created_exclusions(self) -> int:
        return self.summary["created_exclusions"]

    @property
    def publication_count_before(self) -> int:
        return self.summary["publication_count_before"]

    @property
    def publication_count_after(self) -> int:
        return self.summary["publication_count_after"]

    @property
    def exclusion_count_before(self) -> int:
        return self.summary["exclusion_count_before"]

    @property
    def exclusion_count_after(self) -> int:
        return self.summary["exclusion_count_after"]

    def to_document(self) -> dict[str, Any]:
        document = {
            "report_version": 1,
            "mode": self.mode,
            "observed_at": self.observed_at.isoformat(),
            "source_files": [dict(item) for item in self.source_files],
            "items": [dict(item) for item in self.items],
            "supplementation_required": [
                dict(item) for item in self.supplementation_required
            ],
            "summary": dict(self.summary),
            "selection_allowed": self.selection_allowed,
            "old_file_write_attempts": 0,
            "old_task_api_calls": 0,
        }
        _validate_result_document(document)
        return document


@dataclass(frozen=True, slots=True)
class _SourceRecord:
    source_ref: str
    source_path: str
    source_version: str
    classification: MigrationClass
    legacy_product_ref: str | None
    product_url: str | None
    reported_at: str | None
    actual_posted_at: str | None
    evidence_kind: str | None
    reason: str | None = None


def _parse_jst(value: str | datetime, *, name: str) -> datetime:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise MigrationInputError(f"{name} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MigrationInputError(f"{name} must include a timezone offset")
    return parsed.astimezone(JST)


def _strict_child(path: Path, parent: Path) -> bool:
    try:
        return bool(path.relative_to(parent).parts)
    except ValueError:
        return False


def _roots(
    source_root: str | Path,
    data_root: str | Path,
    *,
    fixture_mode: bool,
) -> tuple[Path, Path]:
    raw_source = Path(source_root).expanduser()
    raw_destination = Path(data_root).expanduser()
    if raw_source.is_symlink() or raw_destination.is_symlink():
        raise MigrationInputError("source_root and data_root cannot be symbolic links")
    try:
        source = raw_source.resolve(strict=True)
        destination = raw_destination.resolve(strict=True)
        safe_root = SAFE_FIXTURE_BASE.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise MigrationInputError(
            "source_root and data_root must already exist"
        ) from exc
    if not source.is_dir() or not destination.is_dir():
        raise MigrationInputError("source_root and data_root must be directories")
    if (
        source == destination
        or source.is_relative_to(destination)
        or destination.is_relative_to(source)
    ):
        raise MigrationInputError("legacy source and new data roots must be separate")
    if fixture_mode and (
        not _strict_child(source, safe_root)
        or not _strict_child(destination, safe_root)
    ):
        raise MigrationInputError(
            "fixture migration roots must stay under the private test area"
        )
    return source, destination


def _database_is_new_destination(
    connection: sqlite3.Connection,
    *,
    data_root: Path,
    source_root: Path,
) -> None:
    databases = connection.execute("PRAGMA database_list").fetchall()
    main_rows = [row for row in databases if str(row[1]) == "main"]
    if len(main_rows) != 1 or not str(main_rows[0][2]):
        raise MigrationInputError("destination database must be a file under data_root")
    main_path = Path(str(main_rows[0][2])).expanduser().resolve()
    expected = (data_root / "state.sqlite").resolve()
    if main_path != expected:
        raise MigrationInputError(
            "destination database must be data_root/state.sqlite, never the legacy database"
        )
    if main_path.is_relative_to(source_root):
        raise MigrationInputError("legacy source files cannot be a write destination")
    if connection.in_transaction:
        raise MigrationInputError("migration requires an idle destination connection")


def _source_file(source_root: Path, relative_path: str | Path) -> tuple[Path, str]:
    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise MigrationInputError("legacy source paths must be relative children")
    candidate = source_root / relative
    cursor = source_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise MigrationInputError("legacy source path contains a symbolic link")
    try:
        path = candidate.resolve(strict=True)
        path.relative_to(source_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MigrationInputError(
            "legacy source file is missing or escaped source_root"
        ) from exc
    if not path.is_file():
        raise MigrationInputError("legacy source path must name a regular file")
    return path, relative.as_posix()


def _schema_for(kind: str) -> dict[str, Any]:
    path = _LEGACY_SCHEMAS[kind]
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(document)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise MigrationInputError(
            f"read-only legacy {kind} schema is unavailable"
        ) from exc
    return document


def _validate_legacy(kind: str, value: Any, *, location: str) -> None:
    try:
        Draft202012Validator(
            _schema_for(kind), format_checker=rfc3339_format_checker()
        ).validate(value)
    except ValidationError as exc:
        raise MigrationInputError(
            f"legacy {kind} input is invalid at {location}: {exc.message}"
        ) from exc


def _read_json(path: Path, *, name: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationInputError(f"legacy {name} is not readable UTF-8 JSON") from exc


def _ledger_index(
    ledger: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    products = ledger.get("products")
    if not isinstance(products, list):
        raise MigrationInputError("legacy product ledger has no products array")
    by_id: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for product in products:
        product_id = str(product["id"])
        if product_id in by_id:
            duplicates.add(product_id)
        else:
            by_id[product_id] = product
    for product_id in duplicates:
        by_id.pop(product_id, None)
    return by_id, duplicates


def _source_record(
    *,
    source_ref: str,
    source_path: str,
    source_version: str,
    classification: MigrationClass,
    legacy_product_ref: str | None,
    product_url: str | None,
    reported_at: str | None,
    actual_posted_at: str | None,
    evidence_kind: str | None,
    reason: str | None = None,
) -> _SourceRecord:
    if not source_ref or len(source_ref) > 512:
        raise MigrationInputError("legacy source reference must be unique and bounded")
    return _SourceRecord(
        source_ref=source_ref,
        source_path=source_path,
        source_version=source_version,
        classification=classification,
        legacy_product_ref=legacy_product_ref,
        product_url=product_url,
        reported_at=reported_at,
        actual_posted_at=actual_posted_at,
        evidence_kind=evidence_kind,
        reason=reason,
    )


def _load_source_records(
    source_root: Path,
    *,
    posts_log_path: str | Path,
    product_ledger_path: str | Path,
    post_status_path: str | Path | None,
    drafts_path: str | Path | None,
    now: datetime,
) -> tuple[list[_SourceRecord], list[dict[str, str]], list[dict[str, Any]]]:
    ledger_file, ledger_relative = _source_file(source_root, product_ledger_path)
    ledger_document = _read_json(ledger_file, name="product ledger")
    _validate_legacy("products", ledger_document, location=ledger_relative)
    products_by_id, duplicate_ids = _ledger_index(ledger_document)
    source_files = [{"path": ledger_relative, "version": _SOURCE_VERSIONS["products"]}]

    def product_url_for(product_ref: str) -> str | None:
        product = products_by_id.get(product_ref)
        if product is None:
            return None
        value = product.get("url")
        return value if isinstance(value, str) and value.strip() else None

    records: list[_SourceRecord] = []
    post_file, post_relative = _source_file(source_root, posts_log_path)
    try:
        post_lines = post_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise MigrationInputError(
            "legacy posts log is not readable UTF-8 text"
        ) from exc
    source_files.append({"path": post_relative, "version": _SOURCE_VERSIONS["posts"]})
    log_status_by_product: dict[str, set[str]] = defaultdict(set)
    for line_number, line in enumerate(post_lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MigrationInputError(
                f"legacy posts log has invalid JSON at line {line_number}"
            ) from exc
        _validate_legacy("posts", row, location=f"{post_relative}#L{line_number}")
        product_ref = str(row["product_id"])
        status = str(row["status"])
        log_status_by_product[product_ref].add(status)
        classification: MigrationClass = {
            "posted": "legacy_evidence",
            "planned": "planned",
            "unknown": "unknown",
        }[status]
        records.append(
            _source_record(
                source_ref=f"{post_relative}@{_SOURCE_VERSIONS['posts']}#L{line_number}",
                source_path=post_relative,
                source_version=_SOURCE_VERSIONS["posts"],
                classification=classification,
                legacy_product_ref=product_ref,
                product_url=product_url_for(product_ref),
                reported_at=now.isoformat(),
                actual_posted_at=row.get("posted_at"),
                evidence_kind="legacy_evidence" if status == "posted" else None,
                reason=(
                    "duplicate_legacy_product_id"
                    if product_ref in duplicate_ids
                    else None
                ),
            )
        )

    statement_status_by_product: dict[str, set[str]] = defaultdict(set)
    status_file: Path | None = None
    status_relative: str | None = None
    if post_status_path is not None:
        status_file, status_relative = _source_file(source_root, post_status_path)
        status_document = _read_json(status_file, name="post status")
        _validate_legacy("status", status_document, location=status_relative)
        source_files.append(
            {"path": status_relative, "version": _SOURCE_VERSIONS["status"]}
        )
        items = status_document["items"]
        for product_ref, row in sorted(items.items()):
            if row["product_id"] != product_ref:
                raise MigrationConflictError(
                    "legacy post status key does not match its product identifier"
                )
            posted = row["posted"] is True
            ever_posted = row["ever_marked_posted"] is True
            scheduled = row["scheduled_date"] is not None
            corrections = row["correction_history"]
            if posted and ever_posted and not corrections and not scheduled:
                classification = "human_statement"
                reason = None
                event_kind = "human_statement"
                statement_status_by_product[product_ref].add("posted")
            elif posted != ever_posted or corrections or (posted and scheduled):
                classification = "unknown"
                reason = "human_status_inconsistent"
                event_kind = None
                statement_status_by_product[product_ref].add("unknown")
            elif scheduled:
                classification = "planned"
                reason = None
                event_kind = None
                statement_status_by_product[product_ref].add("planned")
            else:
                classification = "unknown"
                reason = "legacy_posting_state_unknown"
                event_kind = None
                statement_status_by_product[product_ref].add("unknown")
            records.append(
                _source_record(
                    source_ref=(
                        f"{status_relative}@{_SOURCE_VERSIONS['status']}#items/{product_ref}"
                    ),
                    source_path=status_relative,
                    source_version=_SOURCE_VERSIONS["status"],
                    classification=classification,  # type: ignore[arg-type]
                    legacy_product_ref=product_ref,
                    product_url=product_url_for(product_ref),
                    reported_at=row["updated_at"],
                    actual_posted_at=row["posted_at"],
                    evidence_kind=event_kind,
                    reason=reason,
                )
            )

    if drafts_path is not None:
        drafts_file, drafts_relative = _source_file(source_root, drafts_path)
        drafts_document = _read_json(drafts_file, name="drafts")
        _validate_legacy("drafts", drafts_document, location=drafts_relative)
        source_files.append(
            {"path": drafts_relative, "version": _SOURCE_VERSIONS["drafts"]}
        )
        for index, row in enumerate(drafts_document["drafts"]):
            product_ref = str(row["product_id"])
            records.append(
                _source_record(
                    source_ref=f"{drafts_relative}@{_SOURCE_VERSIONS['drafts']}#drafts/{index}",
                    source_path=drafts_relative,
                    source_version=_SOURCE_VERSIONS["drafts"],
                    classification="draft",
                    legacy_product_ref=product_ref,
                    product_url=product_url_for(product_ref) or row.get("url"),
                    reported_at=None,
                    actual_posted_at=None,
                    evidence_kind=None,
                )
            )

    duplicate_refs = [
        ref
        for ref, count in Counter(record.source_ref for record in records).items()
        if count > 1
    ]
    if duplicate_refs:
        raise MigrationConflictError("legacy source references are not unique")

    supplementary: list[dict[str, Any]] = []
    all_statuses: dict[str, set[str]] = defaultdict(set)
    for product_ref, statuses in log_status_by_product.items():
        all_statuses[product_ref].update(statuses)
    for product_ref, statuses in statement_status_by_product.items():
        all_statuses[product_ref].update(statuses)
    for product_ref, product in products_by_id.items():
        legacy_status = product.get("status")
        if legacy_status == "posted":
            all_statuses[product_ref].add("posted")
        elif legacy_status == "planned":
            all_statuses[product_ref].add("planned")
        elif legacy_status in {"needs_review", "eligible", "rejected"}:
            all_statuses[product_ref].add("draft")
    for product_ref, statuses in sorted(all_statuses.items()):
        if "posted" in statuses and ("planned" in statuses or "draft" in statuses):
            product = products_by_id.get(product_ref)
            product_url = product.get("url") if product else None
            supplementary.append(
                {
                    "source_ref": f"{ledger_relative}@{_SOURCE_VERSIONS['products']}#{product_ref}",
                    "source_path": ledger_relative,
                    "source_version": _SOURCE_VERSIONS["products"],
                    "legacy_product_ref": product_ref,
                    "product_url": str(product_url) if product_url else None,
                    "reason": "legacy_status_conflict",
                }
            )

    # Keep supplemental state explicit so concurrent calls cannot share it.
    # Raw names, body text, reactions, and legacy ratings are never copied.
    return records, source_files, supplementary


def _matching_offer(
    connection: sqlite3.Connection,
    product_url: str | None,
) -> tuple[str | None, str | None, str | None]:
    if not product_url:
        return None, None, "legacy_product_url_unreadable"
    try:
        normalized = normalize_offer_url(product_url)
    except (TypeError, ValueError):
        return None, None, "legacy_product_url_unreadable"
    url_identity = identify_offer_from_saved_url(normalized)
    if url_identity.status != "identified" or not url_identity.offer_key:
        return None, normalized, "legacy_product_url_unreadable"
    row = connection.execute(
        "SELECT offer_key,shop_id,item_id FROM offer WHERE offer_key=?",
        (url_identity.offer_key,),
    ).fetchone()
    if row is None:
        # A legacy post may predate the new offer ledger. Its canonical Rakuten
        # item URL still provides the shop/item identity required by the event FK.
        return url_identity.offer_key, normalized, None
    identity = identify_offer(
        normalized,
        observed_shop_id=str(row["shop_id"]),
        observed_item_id=str(row["item_id"]),
    )
    if identity.status != "identified" or identity.offer_key != row["offer_key"]:
        return None, normalized, "ambiguous_current_offer"
    return str(row["offer_key"]), normalized, None


def _validate_result_document(document: dict[str, Any]) -> None:
    try:
        schema = json.loads(_RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=rfc3339_format_checker()).validate(
            document
        )
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise MigrationInputError(
            "migration result does not satisfy its schema"
        ) from exc


def _event_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM publication_event WHERE valid=1"
        ).fetchone()[0]
    )


def _exclusion_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM exclusion_key WHERE effective=1"
        ).fetchone()[0]
    )


def _event_id(source_ref: str) -> str:
    return f"LEGACY:{source_ref}"


def _same_event(existing: sqlite3.Row, expected: dict[str, Any]) -> bool:
    return all(
        existing[key] == expected[key]
        for key in (
            "event_id",
            "offer_key",
            "draft_id",
            "draft_version",
            "reported_at",
            "actual_posted_at",
            "room_url",
            "evidence_kind",
            "valid",
            "revision",
        )
    )


def _prepare_records(
    connection: sqlite3.Connection,
    records: list[_SourceRecord],
    *,
    source_supplementation: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    supplementation: list[dict[str, Any]] = list(source_supplementation)
    for record in records:
        event_id: str | None = None
        offer_key: str | None = None
        reason = record.reason
        outcome = "not_posted"
        normalized_url: str | None = None
        if record.classification in {"legacy_evidence", "human_statement"}:
            event_id = _event_id(record.source_ref)
            offer_key, normalized_url, resolve_reason = _matching_offer(
                connection, record.product_url
            )
            reason = reason or resolve_reason
            if (
                record.legacy_product_ref is None
                or record.reason == "duplicate_legacy_product_id"
            ):
                offer_key = None
                reason = record.reason or "legacy_product_unmapped"
            if offer_key is None:
                reason = reason or "legacy_product_unmapped"
                outcome = "supplementation_required"
                supplementation.append(
                    {
                        "source_ref": record.source_ref,
                        "source_path": record.source_path,
                        "source_version": record.source_version,
                        "legacy_product_ref": record.legacy_product_ref,
                        "product_url": normalized_url,
                        "reason": reason,
                    }
                )
            else:
                outcome = "would_import"
        elif record.classification == "unknown":
            outcome = "supplementation_required"
            if reason is None:
                reason = "legacy_posting_state_unknown"
            if record.legacy_product_ref is None:
                normalized_url = None
                supplementary_reason = "legacy_product_unmapped"
            elif record.product_url is None:
                normalized_url = None
                supplementary_reason = "legacy_product_url_unreadable"
            else:
                matched_key, normalized_url, match_reason = _matching_offer(
                    connection, record.product_url
                )
                supplementary_reason = (
                    match_reason
                    if matched_key is None
                    else (
                        "human_status_inconsistent"
                        if reason == "human_status_inconsistent"
                        else "legacy_posting_state_unknown"
                    )
                )
            supplementation.append(
                {
                    "source_ref": record.source_ref,
                    "source_path": record.source_path,
                    "source_version": record.source_version,
                    "legacy_product_ref": record.legacy_product_ref,
                    "product_url": normalized_url,
                    "reason": supplementary_reason,
                }
            )
        items.append(
            {
                "source_ref": record.source_ref,
                "source_path": record.source_path,
                "source_version": record.source_version,
                "classification": record.classification,
                "legacy_product_ref": record.legacy_product_ref,
                "offer_key": offer_key,
                "outcome": outcome,
                "reason": reason,
                "event_id": event_id,
                "_normalized_url": normalized_url,
            }
        )

    for item in items:
        if item["outcome"] == "would_import" and item["offer_key"]:
            record = next(r for r in records if r.source_ref == item["source_ref"])
            if record.reported_at is None or record.evidence_kind is None:
                item["outcome"] = "supplementation_required"
                item["reason"] = "human_status_inconsistent"
                supplementation.append(
                    {
                        "source_ref": item["source_ref"],
                        "source_path": item["source_path"],
                        "source_version": item["source_version"],
                        "legacy_product_ref": item["legacy_product_ref"],
                        "product_url": record.product_url,
                        "reason": "human_status_inconsistent",
                    }
                )
                continue
            event = {
                "event_id": item["event_id"],
                "offer_key": item["offer_key"],
                "draft_id": None,
                "draft_version": None,
                "reported_at": record.reported_at,
                "actual_posted_at": record.actual_posted_at,
                "room_url": None,
                "evidence_kind": record.evidence_kind,
                "valid": True,
                "revision": 0,
            }
            validate_entity("publication_event", event)
            item["_event"] = event

    existing_refs = {
        str(row[0])
        for row in connection.execute("SELECT event_id FROM publication_event")
    }
    for item in items:
        event = item.get("_event")
        if event is None:
            continue
        if event["event_id"] in existing_refs:
            existing = connection.execute(
                "SELECT * FROM publication_event WHERE event_id=?", (event["event_id"],)
            ).fetchone()
            if existing is None or not _same_event(existing, event):
                raise MigrationConflictError(
                    "an original legacy reference conflicts with an existing publication event"
                )
            item["outcome"] = "already_imported"
        else:
            item["outcome"] = "would_import"
    return items, supplementation


def _apply_events_and_exclusions(
    connection: sqlite3.Connection,
    items: list[dict[str, Any]],
    *,
    now: datetime,
) -> tuple[int, int, int]:
    created_publications = 0
    created_exclusions = 0
    updated_exclusions = 0
    ready = [
        item
        for item in items
        if item["outcome"] in {"would_import", "already_imported"} and item["offer_key"]
    ]
    try:
        connection.execute("BEGIN IMMEDIATE")
        changed_offers: set[str] = set()
        for item in ready:
            offer_key = str(item["offer_key"])
            normalized_url = item.get("_normalized_url")
            identity = identify_offer_from_saved_url(
                normalized_url if isinstance(normalized_url, str) else None
            )
            if identity.status != "identified" or identity.offer_key != offer_key:
                raise MigrationConflictError(
                    "legacy post identity no longer agrees with its product URL"
                )
            existing_offer = connection.execute(
                "SELECT shop_id,item_id FROM offer WHERE offer_key=?",
                (offer_key,),
            ).fetchone()
            if existing_offer is None:
                placeholder = {
                    "offer_key": offer_key,
                    "shop_id": identity.shop_id,
                    "item_id": identity.item_id,
                    "name": "商品名未取得",
                    "url": identity.normalized_url,
                    "image_refs": [],
                    "main_theme_id": LEGACY_UNASSIGNED_THEME_ID,
                    "aux_theme_ids": [],
                    "first_seen": now.isoformat(),
                    "last_seen": now.isoformat(),
                    "facts": [],
                }
                validate_entity("offer", placeholder)
                connection.execute(
                    """INSERT INTO offer(
                           offer_key,shop_id,item_id,name,url,image_refs_json,
                           main_theme_id,aux_theme_ids_json,first_seen,last_seen,facts_json
                       ) VALUES (?,?,?,?,?,?,?,'[]',?,?,?)""",
                    (
                        offer_key,
                        identity.shop_id,
                        identity.item_id,
                        placeholder["name"],
                        identity.normalized_url,
                        "[]",
                        LEGACY_UNASSIGNED_THEME_ID,
                        now.isoformat(),
                        now.isoformat(),
                        "[]",
                    ),
                )
            elif (
                str(existing_offer["shop_id"]) != identity.shop_id
                or str(existing_offer["item_id"]) != identity.item_id
            ):
                raise MigrationConflictError(
                    "legacy post identity conflicts with the current offer ledger"
                )
            event_id = str(item["event_id"])
            event = item["_event"]
            existing = connection.execute(
                "SELECT * FROM publication_event WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing is not None:
                if not _same_event(existing, event):
                    raise MigrationConflictError(
                        "an original legacy reference conflicts with an existing publication event"
                    )
                item["outcome"] = "already_imported"
                changed_offers.add(str(item["offer_key"]))
                continue
            connection.execute(
                """INSERT INTO publication_event(
                       event_id, offer_key, draft_id, draft_version, reported_at,
                       actual_posted_at, room_url, evidence_kind, valid, revision
                   ) VALUES (?, ?, NULL, NULL, ?, ?, NULL, ?, 1, 0)""",
                (
                    event["event_id"],
                    event["offer_key"],
                    event["reported_at"],
                    event["actual_posted_at"],
                    event["evidence_kind"],
                ),
            )
            item["outcome"] = "imported"
            created_publications += 1
            changed_offers.add(str(item["offer_key"]))

        for offer_key in sorted(changed_offers):
            refs = [
                str(row[0])
                for row in connection.execute(
                    "SELECT event_id FROM publication_event WHERE offer_key=? AND valid=1 ORDER BY event_id",
                    (offer_key,),
                )
            ]
            exclusion = {
                "offer_key": offer_key,
                "reason": "actual_posted",
                "evidence_refs": refs,
                "effective": True,
                "updated_at": now.isoformat(),
            }
            validate_entity("exclusion_key", exclusion)
            existing = connection.execute(
                "SELECT reason, evidence_refs_json, effective FROM exclusion_key WHERE offer_key=?",
                (offer_key,),
            ).fetchone()
            expected_refs = json.dumps(refs, ensure_ascii=False, separators=(",", ":"))
            if existing is None:
                connection.execute(
                    """INSERT INTO exclusion_key(
                           offer_key, reason, evidence_refs_json, effective, updated_at
                       ) VALUES (?, 'actual_posted', ?, 1, ?)""",
                    (offer_key, expected_refs, now.isoformat()),
                )
                created_exclusions += 1
            elif (
                existing[0] != "actual_posted"
                or existing[1] != expected_refs
                or int(existing[2]) != 1
            ):
                connection.execute(
                    """UPDATE exclusion_key SET reason='actual_posted',
                           evidence_refs_json=?, effective=1, updated_at=?
                       WHERE offer_key=?""",
                    (expected_refs, now.isoformat(), offer_key),
                )
                updated_exclusions += 1
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    return created_publications, created_exclusions, updated_exclusions


def migrate_legacy_history(
    connection: sqlite3.Connection,
    *,
    source_root: str | Path,
    data_root: str | Path,
    posts_log_path: str | Path,
    product_ledger_path: str | Path,
    post_status_path: str | Path | None,
    drafts_path: str | Path | None,
    now: str | datetime,
    mode: MigrationMode,
    fixture_mode: bool = True,
) -> MigrationResult:
    """Read legacy sources, plan classifications, and optionally import evidenced posts.

    The old JSON/JSONL files are opened read-only. Writes are limited to valid
    ``publication_event`` rows and their ``exclusion_key`` rows in the explicit
    new ``data_root/state.sqlite`` database. Schedules, drafts, quality ratings,
    credentials, task state, and old databases are never migration targets.
    """

    if mode not in {"dry_run", "apply"}:
        raise MigrationInputError("mode must be dry_run or apply")
    current = _parse_jst(now, name="now")
    source, destination = _roots(source_root, data_root, fixture_mode=fixture_mode)
    _database_is_new_destination(connection, data_root=destination, source_root=source)
    records, source_files, source_supplementation = _load_source_records(
        source,
        posts_log_path=posts_log_path,
        product_ledger_path=product_ledger_path,
        post_status_path=post_status_path,
        drafts_path=drafts_path,
        now=current,
    )
    before_publications = _event_count(connection)
    before_exclusions = _exclusion_count(connection)
    items, supplementation = _prepare_records(
        connection, records, source_supplementation=source_supplementation
    )
    counts = Counter(record.classification for record in records)
    created_publications = 0
    created_exclusions = 0
    updated_exclusions = 0
    already_imported = sum(1 for item in items if item["outcome"] == "already_imported")
    if mode == "apply":
        (
            created_publications,
            created_exclusions,
            updated_exclusions,
        ) = _apply_events_and_exclusions(connection, items, now=current)
        already_imported = sum(
            1 for item in items if item["outcome"] == "already_imported"
        )
    after_publications = _event_count(connection)
    after_exclusions = _exclusion_count(connection)
    summary = {
        "source_records": len(records),
        "legacy_evidence": counts["legacy_evidence"],
        "human_statements": counts["human_statement"],
        "planned": counts["planned"],
        "drafts": counts["draft"],
        "unknown": counts["unknown"],
        "created_publications": created_publications,
        "already_imported": already_imported,
        "created_exclusions": created_exclusions,
        "updated_exclusions": updated_exclusions,
        "publication_count_before": before_publications,
        "publication_count_after": after_publications,
        "exclusion_count_before": before_exclusions,
        "exclusion_count_after": after_exclusions,
    }
    # Private event details are only transaction inputs, never report fields.
    for item in items:
        item.pop("_event", None)
        item.pop("_normalized_url", None)
    selection_allowed = mode == "apply" and not supplementation
    result = MigrationResult(
        mode=mode,
        observed_at=current,
        source_files=tuple(source_files),
        items=tuple(items),
        supplementation_required=tuple(supplementation),
        summary=summary,
        selection_allowed=selection_allowed,
    )
    result.to_document()
    return result
