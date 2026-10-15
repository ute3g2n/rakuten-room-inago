"""Import reports exported from the standalone history HTML outbox."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from automation.inago.clock import Clock
from automation.inago.contracts import IdempotencyConflict, RejectedContract
from automation.inago.human_api import _parse_report
from automation.inago.repository import (
    ReceiptRevisionConflict,
    record_pending_human_action,
)

MAX_BATCH_BYTES = 2 * 1024 * 1024
MAX_REPORTS_PER_IMPORT = 500
JST = ZoneInfo("Asia/Tokyo")


class ReportImportError(RuntimeError):
    """An offline report batch is unsafe or could not be imported atomically."""


@dataclass(frozen=True, slots=True)
class ReportImportResult:
    files_processed: int
    reports_received: int


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_attribute = getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(metadata.st_file_attributes & reparse_attribute)


def _safe_child(root: Path, child: Path) -> Path:
    try:
        resolved_root = root.resolve(strict=True)
        resolved_child = child.resolve(strict=True)
        resolved_child.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReportImportError("offline report directory escaped its runtime root") from exc
    return resolved_child


def _read_batch(path: Path) -> list[tuple[str, dict[str, Any]]]:
    try:
        if _is_reparse_point(path) or not path.is_file():
            raise ReportImportError("offline report file is not a regular file")
        if path.stat().st_size > MAX_BATCH_BYTES:
            raise ReportImportError("offline report file exceeds the size limit")
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_BATCH_BYTES:
            raise ReportImportError("offline report file size is invalid")
        document = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_without_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except ReportImportError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReportImportError("offline report JSON is invalid") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "reports"}:
        raise ReportImportError("offline report envelope is invalid")
    version = document["schema_version"]
    if isinstance(version, bool) or version != 1:
        raise ReportImportError("offline report schema version is unsupported")
    entries = document["reports"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_REPORTS_PER_IMPORT:
        raise ReportImportError("offline report count is invalid")
    parsed: list[tuple[str, dict[str, Any]]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"queued_at", "report"}:
            raise ReportImportError("offline report entry is invalid")
        queued_at = entry["queued_at"]
        if not isinstance(queued_at, str):
            raise ReportImportError("offline report timestamp is invalid")
        try:
            timestamp = datetime.fromisoformat(queued_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReportImportError("offline report timestamp is invalid") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ReportImportError("offline report timestamp must include an offset")
        report_value = entry["report"]
        if not isinstance(report_value, Mapping):
            raise ReportImportError("offline report payload is invalid")
        try:
            canonical = json.dumps(
                dict(report_value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            report = _parse_report(canonical)
        except (TypeError, ValueError, RejectedContract) as exc:
            raise ReportImportError("offline report payload violates its schema") from exc
        parsed.append((timestamp.astimezone(JST).isoformat(), report))
    return parsed


def _archive_file(source: Path, archive_root: Path) -> None:
    destination = archive_root / source.name
    offset = 1
    while destination.exists():
        destination = archive_root / f"{source.stem}-{offset}{source.suffix}"
        offset += 1
    try:
        source.replace(destination)
    except OSError as exc:
        raise ReportImportError("offline report file could not be archived") from exc


def import_offline_report_batches(
    connection: sqlite3.Connection,
    *,
    runtime_root: str | Path,
    clock: Clock | None = None,
) -> ReportImportResult:
    """Validate, atomically receipt, then archive all browser-exported batches."""

    if connection.in_transaction:
        raise ReportImportError("offline report import requires an idle connection")
    root = Path(runtime_root)
    if not root.exists():
        return ReportImportResult(files_processed=0, reports_received=0)
    if _is_reparse_point(root) or not root.is_dir():
        raise ReportImportError("runtime root is not a regular directory")
    inbox = root / "incoming-human-reports"
    if not inbox.exists():
        return ReportImportResult(files_processed=0, reports_received=0)
    if _is_reparse_point(inbox) or not inbox.is_dir():
        raise ReportImportError("offline report inbox is not a regular directory")
    _safe_child(root, inbox)
    sources = sorted(inbox.glob("human-reports-*.json"), key=lambda item: item.name)
    if not sources:
        return ReportImportResult(files_processed=0, reports_received=0)

    parsed_files: list[tuple[Path, list[tuple[str, dict[str, Any]]]]] = []
    seen_action_ids: set[str] = set()
    total_reports = 0
    for source in sources:
        _safe_child(inbox, source)
        reports = _read_batch(source)
        for _, report in reports:
            action_id = report["action_id"]
            if action_id in seen_action_ids:
                raise ReportImportError("offline report batch repeats an action ID")
            seen_action_ids.add(action_id)
        total_reports += len(reports)
        if total_reports > MAX_REPORTS_PER_IMPORT:
            raise ReportImportError("offline report import count exceeds the limit")
        parsed_files.append((source, reports))

    archive_root = root / "processed-human-reports"
    if archive_root.exists():
        if _is_reparse_point(archive_root) or not archive_root.is_dir():
            raise ReportImportError("offline report archive is not a regular directory")
        _safe_child(root, archive_root)
    else:
        try:
            archive_root.mkdir()
        except OSError as exc:
            raise ReportImportError("offline report archive is unavailable") from exc

    received = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        for _source, reports in parsed_files:
            for reported_at, report in reports:
                record_pending_human_action(
                    connection,
                    report,
                    clock=clock,
                    reported_at=reported_at,
                    transaction_managed=True,
                )
                received += 1
        connection.commit()
    except ReceiptRevisionConflict as exc:
        if connection.in_transaction:
            connection.rollback()
        raise ReportImportError("offline report receipt revision conflict") from exc
    except (RejectedContract, IdempotencyConflict) as exc:
        if connection.in_transaction:
            connection.rollback()
        raise ReportImportError("offline report batch contract conflict") from exc
    except Exception as exc:
        if connection.in_transaction:
            connection.rollback()
        if isinstance(exc, ReportImportError):
            raise
        raise ReportImportError("offline report batch could not be committed") from exc

    for source, _reports in parsed_files:
        _archive_file(source, archive_root)
    return ReportImportResult(
        files_processed=len(parsed_files),
        reports_received=received,
    )
