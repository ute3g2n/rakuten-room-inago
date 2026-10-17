"""Allowlisted evidence collection, redaction, and consistent SQLite snapshots.

The collector handles only explicitly supplied sources under a caller-provided
read root. It never reads browser stores, executes evidence content, or copies
unfiltered text into a shared bundle. SQLite snapshots are a separate private
artifact operation and use SQLite's backup API through a read-only source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from automation.inago.trace import redact_sensitive_text


JST = ZoneInfo("Asia/Tokyo")
EVIDENCE_SCHEMA_VERSION = 1
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_PRIOR_MANIFEST_BYTES = 1024 * 1024

EVIDENCE_CLASS_IDS = (
    "L01",
    "L02",
    "L03",
    "L04",
    "L05",
    "O01",
    "O02",
    "O03",
    "A01",
    "A02",
    "A03",
    "M01",
    "M02",
    "M03",
    "S01",
    "S02",
    "S03",
    "W01",
    "V01",
    "H01",
    "H02",
    "B01",
    "B02",
    "X01",
    "X02",
    "X03",
)

_ALLOWED_FORMATS = {
    "L01": {"jsonl"},
    "L02": {"jsonl"},
    "L03": {"json", "jsonl"},
    "L04": {"json", "jsonl"},
    "L05": {"json", "jsonl"},
    "O01": {"json", "jsonl"},
    "O02": {"json", "jsonl"},
    "O03": {"json", "jsonl"},
    "A01": {"json", "jsonl"},
    "A02": {"json", "jsonl", "response"},
    "A03": {"json", "jsonl"},
    "M01": {"jsonl"},
    "M02": {"json", "jsonl", "response"},
    "M03": {"json", "jsonl"},
    "S01": {"sqlite"},
    "S02": {"json", "jsonl"},
    "S03": {"json", "jsonl"},
    "W01": {"json", "jsonl"},
    "V01": {"html_snapshot"},
    "H01": {"binary"},
    "H02": {"json", "jsonl"},
    "B01": {"json", "sqlite"},
    "B02": {"json", "sqlite"},
    "X01": {"jsonl", "text"},
    "X02": {"jsonl"},
    "X03": {"json", "xml"},
}

_ARTIFACT_STATUSES = {
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
}
_CLASS_STATUSES = _ARTIFACT_STATUSES | {"not_listed", "partial"}
_MISSING_STATUSES = {"not_saved", "expired", "unavailable", "suppressed"}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_GITHUB_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,})(?![A-Za-z0-9_])"
)
_SAFE_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\Z"
)
_SAFE_RAW_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d{1,6})?)?(?:[Zz]|[+-]\d{2}:?\d{2})?)?\Z"
)
_SECRET_KEY = re.compile(
    r"(?:authorization|cookie|credential|password|private[_-]?key|secret|token|"
    r"apikey|api[_-]?key|email|address|phone|recipient|customer|sessionid|"
    r"product[_-]?(?:name|body|text)|draft[_-]?(?:body|text)|prompt|response[_-]?text)",
    re.IGNORECASE,
)
_TIMESTAMP_FIELDS = (
    "timestamp",
    "occurred_at",
    "event_at",
    "created_at",
    "observed_at",
    "recorded_at",
    "started_at",
    "completed_at",
    "scheduled_at",
    "at",
)
_IDENTITY_FIELDS = (
    "run_id",
    "source_run_id",
    "job_id",
    "attempt_id",
    "request_id",
    "action_id",
    "source_launch_id",
    "thread_id",
    "turn_id",
    "task_id",
    "artifact_ref",
    "source_ref",
)
_SAFE_FIELDS = {
    "attempt",
    "attempt_limit",
    "availability",
    "cause_code",
    "component",
    "count",
    "degraded",
    "duration_ms",
    "error_class",
    "error_code",
    "event",
    "event_id",
    "exit_code",
    "file_bytes",
    "item_count",
    "operation",
    "phase",
    "reason_code",
    "record_count",
    "retry_count",
    "retryable",
    "row_count",
    "scope",
    "stage",
    "status",
    "suppressed",
    "suppression_marker_count",
    "suppression_reason",
    "trace_status",
    "target_date",
    "execution_date",
    "response_bytes",
    "response_status",
    "scheduler_state",
    "integrity_status",
    "result_kind",
}
_SAFE_ENUMS = {
    "availability": {
        "observed",
        "partial",
        "unknown",
        "unavailable",
        "not_present",
        "unmatched",
        "expired",
        "missing",
    },
    "trace_status": {"healthy", "degraded", "unavailable", "not_written", "unknown"},
    "integrity_status": {"ok", "failed", "unknown"},
    "scheduler_state": {
        "ready",
        "running",
        "disabled",
        "unknown",
        "queued",
        "completed",
        "failed",
    },
    "event": {
        "job_finished",
        "jst_boundary_event",
        "public_source_failure",
        "public_source_stage",
        "public_source_trace_suppressed",
        "request_finished",
        "request_started",
        "stage",
        "stage_cancelled",
        "stage_failure",
        "synthetic_event",
        "trace_event",
        "trace_suppressed",
    },
    "component": {
        "ai_adapter",
        "cli",
        "controller",
        "daily_pipeline",
        "evidence_collector",
        "maintenance",
        "scheduler",
        "watchdog",
        "worker",
    },
    "operation": {
        "copy",
        "daily",
        "database_close",
        "database_open",
        "end_backup",
        "export",
        "maintenance_collect",
        "metrics",
        "monthly_restore",
        "operations_monitor",
        "preflight",
        "preservation",
        "prepare",
        "snapshot",
        "start_backup",
        "trace",
        "watchdog_reconcile",
    },
    "phase": {
        "begin",
        "cancel",
        "copy",
        "daily",
        "database_close",
        "end",
        "error",
        "export",
        "final_answer",
        "metrics",
        "monthly_restore",
        "operations_monitor",
        "preflight",
        "preservation",
        "prepare",
        "start_backup",
        "status",
        "watchdog",
    },
    "reason_code": {
        "allowlisted_fields_only",
        "artifact_write_failed",
        "authentication_required",
        "daily_not_due",
        "daily_result_failed",
        "daily_runtime_error",
        "data_root_not_directory",
        "fixture_transient_room_failure",
        "maintenance_operation_failed",
        "no_correlated_records",
        "shared_deadline_exhausted",
        "source_cancelled",
        "source_timeout",
        "startup_preflight_blocked",
        "trace_unavailable",
        "transport_error",
    },
    "cause_code": {
        "authentication_required",
        "daily_runtime_error",
        "source_cancelled",
        "source_timeout",
        "transport_error",
    },
    "error_code": {
        "artifact_write_failed",
        "authentication_required",
        "daily_runtime_error",
        "database_close_error",
        "fixture_transient_room_failure",
        "shared_deadline_exhausted",
        "source_cancelled",
        "source_timeout",
        "sqlite_integrity_check_failed",
        "trace_unavailable",
        "transport_error",
    },
    "error_class": {
        "appservererror",
        "cancellederror",
        "databaseerror",
        "filenotfounderror",
        "integrityerror",
        "jsondecodeerror",
        "operationalerror",
        "oserror",
        "sqliteerror",
        "timeouterror",
        "typeerror",
        "valueerror",
    },
    "scope": {"cleanup", "job", "overall", "query", "request", "row", "run"},
    "stage": {
        "daily",
        "database_close",
        "database_open",
        "end_backup",
        "export",
        "metrics",
        "monthly_restore",
        "operations_monitor",
        "preflight",
        "preservation",
        "prepare",
        "start_backup",
        "watchdog",
    },
    "status": {
        "acquired",
        "already_checked",
        "blocked",
        "complete",
        "completed",
        "disabled",
        "empty",
        "empty_response",
        "error",
        "expired",
        "failed",
        "healthy",
        "invalid",
        "invalid_input",
        "locked",
        "log_unavailable",
        "not_due",
        "not_implemented",
        "not_run",
        "not_written",
        "observed",
        "partial",
        "preservation_failed",
        "export_failed",
        "metrics_failed",
        "ready",
        "runtime_error",
        "shortfall",
        "started",
        "unavailable",
        "unknown",
        "verified",
        "write_failed",
    },
    "suppression_reason": {"byte_quota", "event_quota", "file_quota", "retention"},
    "response_status": {
        "complete",
        "empty",
        "empty_response",
        "failed",
        "invalid",
        "ok",
        "unavailable",
    },
    "result_kind": {"blocked", "complete", "failed", "not_run", "partial", "ready"},
}
EVIDENCE_VALUE_ENUMS = {
    field: frozenset(values) for field, values in _SAFE_ENUMS.items()
}


class EvidenceCollectionError(ValueError):
    """A safe error code for invalid source, correlation, or output contracts."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """One explicitly allowlisted source path relative to ``source_root``."""

    class_id: str
    source_ref: str
    relative_path: str
    format: str
    missing_status: str = "not_saved"
    source_timezone: str | None = None
    retention_status: str = "unknown"
    expires_at: str | None = None
    present_at_discovery: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceCollection:
    """A manifest and only its redacted JSON artifacts."""

    manifest: Mapping[str, Any]
    artifacts: Mapping[str, bytes]


@dataclass(frozen=True, slots=True)
class SQLiteSnapshot:
    """Safe metadata about a private SQLite backup-API snapshot."""

    integrity_check: str
    foreign_key_errors: int
    row_count: int
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _ParsedTime:
    raw: str | int | float | None
    utc: datetime | None
    format: str


def _validate_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise EvidenceCollectionError(f"{field}_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceCollectionError(f"{field}_invalid") from exc
    if parsed.isoformat() != value:
        raise EvidenceCollectionError(f"{field}_invalid")
    return parsed


def _validate_safe_id(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_ID.fullmatch(value)
        or _is_sensitive_text(value)
    ):
        raise EvidenceCollectionError(f"{field}_invalid")
    return value


def _absolute_path(path: str | Path) -> Path:
    try:
        return Path(path).expanduser().absolute()
    except (OSError, RuntimeError, ValueError) as exc:
        raise EvidenceCollectionError("path_invalid") from exc


def _reject_reparse_components(path: Path, *, allow_missing_leaf: bool = False) -> None:
    absolute = _absolute_path(path)
    components = list(reversed(absolute.parents)) + [absolute]
    for index, component in enumerate(components):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            if allow_missing_leaf:
                continue
            raise EvidenceCollectionError("path_missing")
        except OSError as exc:
            raise EvidenceCollectionError("path_uninspectable") from exc
        if stat.S_ISLNK(mode):
            raise EvidenceCollectionError("path_reparse_point")
        try:
            attributes = component.lstat().st_file_attributes
        except (AttributeError, OSError):
            attributes = 0
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise EvidenceCollectionError("path_reparse_point")


def _resolve_source_path(
    source_root: str | Path, relative_path: str
) -> tuple[Path, Path]:
    root_input = _absolute_path(source_root)
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise EvidenceCollectionError("source_relative_path_invalid")
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or ":" in relative_path
    ):
        raise EvidenceCollectionError("source_relative_path_invalid")
    candidate = root_input.joinpath(*relative.parts)
    try:
        candidate.relative_to(root_input)
    except ValueError as exc:
        raise EvidenceCollectionError("source_path_outside_root") from exc
    return root_input, candidate


def _parse_timestamp(value: object, source_timezone: str | None) -> _ParsedTime:
    if isinstance(value, bool):
        return _ParsedTime(None, None, "invalid")
    if isinstance(value, int):
        try:
            return _ParsedTime(
                value, datetime.fromtimestamp(value, timezone.utc), "unix_seconds"
            )
        except (OverflowError, OSError, ValueError):
            return _ParsedTime(value, None, "invalid")
    if isinstance(value, float):
        if not math.isfinite(value):
            return _ParsedTime(None, None, "invalid")
        try:
            return _ParsedTime(
                value, datetime.fromtimestamp(value, timezone.utc), "unix_seconds"
            )
        except (OverflowError, OSError, ValueError):
            return _ParsedTime(value, None, "invalid")
    if not isinstance(value, str) or not value:
        return _ParsedTime(None, None, "unavailable")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return _ParsedTime(value[:128], None, "invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if source_timezone == "UTC":
            parsed = parsed.replace(tzinfo=timezone.utc)
            time_format = "utc_naive"
        elif source_timezone == "Asia/Tokyo":
            parsed = parsed.replace(tzinfo=JST)
            time_format = "jst_naive"
        else:
            return _ParsedTime(value[:128], None, "timezone_missing")
    else:
        time_format = "iso8601"
    return _ParsedTime(value[:128], parsed.astimezone(timezone.utc), time_format)


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    result = value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if result.endswith(".000000+00:00"):
        result = result.replace(".000000+00:00", "+00:00")
    return result.replace("+00:00", "Z")


def _identity_values(record: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in _IDENTITY_FIELDS:
        value = record.get(field)
        if (
            isinstance(value, str)
            and _SAFE_ID.fullmatch(value)
            and not _is_sensitive_text(value)
        ):
            result[field] = value
    return result


def _normalize_record_keys(record: Mapping[str, Any]) -> dict[str, Any] | None:
    normalized: dict[str, Any] = {}
    for key, value in record.items():
        if not isinstance(key, str):
            return None
        normalized_key = key.casefold()
        if normalized_key in normalized and normalized[normalized_key] != value:
            return None
        normalized[normalized_key] = value
    return normalized


def _is_sensitive_text(value: str) -> bool:
    sanitized = redact_sensitive_text(value)
    if sanitized != value:
        return True
    return bool(
        re.search(
            r"(?i)\b(?:canary|bearer\s+|eyJ[A-Za-z0-9_-]+\."
            r"|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{16,}"
            r"|xox[baprs]-[A-Za-z0-9-]{16,})\b",
            value,
        )
        or _GITHUB_TOKEN.search(value)
    )


def _safe_timestamp_raw(parsed: _ParsedTime) -> str | int | float | None:
    if parsed.format == "invalid":
        return None
    if isinstance(parsed.raw, str):
        if not _SAFE_RAW_TIMESTAMP.fullmatch(parsed.raw) or _is_sensitive_text(
            parsed.raw
        ):
            return None
        return parsed.raw
    if isinstance(parsed.raw, (int, float)) and not isinstance(parsed.raw, bool):
        return parsed.raw if parsed.utc is not None else None
    return None


def _safe_field_value(field: str, value: object) -> tuple[object | None, bool]:
    if field in _IDENTITY_FIELDS:
        if (
            isinstance(value, str)
            and _SAFE_ID.fullmatch(value)
            and not _is_sensitive_text(value)
        ):
            return value, False
        return None, True
    if field == "event_id":
        return (
            (value, False)
            if isinstance(value, str) and _SAFE_UUID.fullmatch(value)
            else (None, True)
        )
    if field in {"target_date", "execution_date"}:
        try:
            return _validate_date(value, field).isoformat(), False
        except EvidenceCollectionError:
            return None, True
    if field in {"retryable", "suppressed", "degraded"}:
        return (value, False) if type(value) is bool else (None, True)
    if field in {
        "attempt",
        "attempt_limit",
        "count",
        "duration_ms",
        "exit_code",
        "file_bytes",
        "item_count",
        "record_count",
        "response_bytes",
        "retry_count",
        "row_count",
        "suppression_marker_count",
    }:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= 2**31 - 1
        ):
            return value, False
        return None, True
    if field in _SAFE_ENUMS:
        if isinstance(value, str) and value.casefold() in _SAFE_ENUMS[field]:
            return value.casefold(), False
        return None, True
    if field in _SAFE_FIELDS:
        return None, True
    return None, True


def _time_for_record(
    record: Mapping[str, Any], source_timezone: str | None
) -> _ParsedTime:
    for field in _TIMESTAMP_FIELDS:
        if field in record:
            return _parse_timestamp(record[field], source_timezone)
    return _ParsedTime(None, None, "unavailable")


def _matches_correlation(
    record_ids: Mapping[str, str],
    expected: Mapping[str, str],
    parsed_time: _ParsedTime,
    target: date,
    *,
    record_has_identity: bool,
) -> bool:
    mismatched = any(
        field in record_ids and record_ids[field] != value
        for field, value in expected.items()
    )
    if mismatched:
        return False
    matched_id = any(
        record_ids.get(field) == value for field, value in expected.items()
    )
    if matched_id:
        return True
    if record_has_identity:
        return False
    if parsed_time.utc is None:
        return False
    local_time = parsed_time.utc.astimezone(JST)
    return local_time.date() == target


def _safe_event(
    record: Mapping[str, Any],
    *,
    class_id: str,
    source_ref: str,
    record_ref: str,
    source_timezone: str | None,
) -> tuple[dict[str, Any], int]:
    parsed_time = _time_for_record(record, source_timezone)
    identities = _identity_values(record)
    fields: dict[str, Any] = {}
    dropped = 0
    timestamp_keys = set(_TIMESTAMP_FIELDS)
    for key, value in record.items():
        if not isinstance(key, str):
            dropped += 1
            continue
        normalized_key = key.casefold()
        if _SECRET_KEY.search(normalized_key) or normalized_key in timestamp_keys:
            if normalized_key not in timestamp_keys:
                dropped += 1
            continue
        if normalized_key in _IDENTITY_FIELDS:
            continue
        if normalized_key not in _SAFE_FIELDS:
            dropped += 1
            continue
        safe_value, rejected = _safe_field_value(normalized_key, value)
        if rejected:
            dropped += 1
        elif safe_value is not None:
            fields[normalized_key] = safe_value
    event = {
        "class_id": class_id,
        "source_ref": source_ref,
        "record_ref": record_ref,
        "occurred_at_raw": _safe_timestamp_raw(parsed_time),
        "occurred_at_utc": _utc_text(parsed_time.utc),
        "time_format": parsed_time.format,
        "correlation": identities,
        "fields": fields,
    }
    return event, dropped


def _read_source(
    path: Path,
    source: EvidenceSource,
    source_root: Path,
    source_root_identity: tuple[int, int],
) -> tuple[bytes | None, str | None, int | None]:
    descriptor: int | None = None
    try:
        descriptor = _open_source_descriptor(path, source_root, source_root_identity)
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            return None, "unavailable", opened_metadata.st_size
        source_size = opened_metadata.st_size
        if source_size > MAX_SOURCE_BYTES:
            return None, "omitted_size_limit", source_size
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        with stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
    except FileNotFoundError:
        status = "disappeared" if source.present_at_discovery else source.missing_status
        return None, status, None
    except EvidenceCollectionError as exc:
        if exc.code == "path_missing":
            status = (
                "disappeared" if source.present_at_discovery else source.missing_status
            )
            return None, status, None
        raise
    except OSError:
        return None, "unavailable", None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(raw) > MAX_SOURCE_BYTES:
        return None, "omitted_size_limit", max(source_size, len(raw))
    return raw, None, len(raw)


def _open_source_root_descriptor(source_root: Path) -> int:
    if os.name == "nt":
        return _open_windows_source_root_descriptor(source_root)

    directory_flag = getattr(os, "O_DIRECTORY", 0)
    no_follow_flag = getattr(os, "O_NOFOLLOW", 0)
    if not directory_flag or not no_follow_flag or not source_root.is_absolute():
        raise OSError("safe_source_root_open_unsupported")
    current_directory = os.open(
        source_root.anchor,
        os.O_RDONLY | directory_flag | no_follow_flag,
    )
    try:
        for part in source_root.parts[1:]:
            next_directory = os.open(
                part,
                os.O_RDONLY | directory_flag | no_follow_flag,
                dir_fd=current_directory,
            )
            os.close(current_directory)
            current_directory = next_directory
        descriptor = current_directory
        current_directory = -1
        return descriptor
    finally:
        if current_directory >= 0:
            os.close(current_directory)


def _open_source_descriptor(
    path: Path, source_root: Path, source_root_identity: tuple[int, int]
) -> int:
    relative_parts = path.relative_to(source_root).parts
    if not relative_parts:
        raise OSError("source_relative_path_invalid")
    root_descriptor = _open_source_root_descriptor(source_root)
    current_directory = root_descriptor
    try:
        root_metadata = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or (root_metadata.st_dev, root_metadata.st_ino) != source_root_identity
        ):
            raise OSError("source_root_identity_changed")
        if os.name == "nt":
            return _open_windows_source_descriptor(relative_parts, root_descriptor)

        directory_flag = getattr(os, "O_DIRECTORY", 0)
        no_follow_flag = getattr(os, "O_NOFOLLOW", 0)
        if not directory_flag or not no_follow_flag:
            raise OSError("safe_relative_open_unsupported")
        for part in relative_parts[:-1]:
            next_directory = os.open(
                part,
                os.O_RDONLY | directory_flag | no_follow_flag,
                dir_fd=current_directory,
            )
            if current_directory != root_descriptor:
                os.close(current_directory)
            current_directory = next_directory
        return os.open(
            relative_parts[-1],
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | no_follow_flag,
            dir_fd=current_directory,
        )
    finally:
        if current_directory != root_descriptor:
            os.close(current_directory)
        os.close(root_descriptor)


def _open_windows_source_root_descriptor(source_root: Path) -> int:
    import msvcrt

    handle = _windows_nt_create_file(
        _windows_native_absolute_path(source_root),
        root_handle=None,
        directory=True,
    )
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        _windows_close_handle(handle)
        raise


def _windows_native_absolute_path(path: Path) -> str:
    import ntpath

    absolute = ntpath.abspath(str(path))
    drive, tail = ntpath.splitdrive(absolute)
    if not drive or drive.startswith("\\\\") or not tail.startswith("\\"):
        raise OSError("safe_windows_source_path_unsupported")
    return f"\\??\\{absolute}"


def _windows_nt_create_file(
    name: str, *, root_handle: int | None, directory: bool
) -> int:
    import ctypes
    from ctypes import wintypes

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class _IoStatusUnion(ctypes.Union):
        _fields_ = [("Status", ctypes.c_long), ("Pointer", wintypes.LPVOID)]

    class _IoStatusBlock(ctypes.Structure):
        _anonymous_ = ("Value",)
        _fields_ = [("Value", _IoStatusUnion), ("Information", ctypes.c_size_t)]

    ntdll = ctypes.WinDLL("ntdll")
    nt_create_file = ntdll.NtCreateFile
    nt_create_file.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    nt_create_file.restype = ctypes.c_long
    buffer = ctypes.create_unicode_buffer(name)
    encoded_length = len(name.encode("utf-16-le"))
    unicode_name = _UnicodeString(
        encoded_length,
        encoded_length + 2,
        ctypes.cast(buffer, wintypes.LPWSTR),
    )
    object_attributes = _ObjectAttributes(
        ctypes.sizeof(_ObjectAttributes),
        root_handle,
        ctypes.pointer(unicode_name),
        0x00000040 | 0x00001000,  # OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE
        None,
        None,
    )
    io_status = _IoStatusBlock()
    file_handle = wintypes.HANDLE()
    desired_access = (
        0x00000080 | 0x00100000 if directory else 0x00000001 | 0x00000080 | 0x00100000
    )
    share_access = 0x00000003 if directory else 0x00000001
    create_options = (
        0x00000001 | 0x00000020 if directory else 0x00000040 | 0x00200000 | 0x00000020
    )
    status = int(
        nt_create_file(
            ctypes.byref(file_handle),
            desired_access,
            ctypes.byref(object_attributes),
            ctypes.byref(io_status),
            None,
            0,
            share_access,
            1,  # FILE_OPEN
            create_options,
            None,
            0,
        )
    )
    if status < 0:
        status_code = status & 0xFFFFFFFF
        if status_code == 0xC000050B:  # STATUS_REPARSE_POINT_ENCOUNTERED
            raise OSError("source_reparse_point")
        rtl_status_to_error = ntdll.RtlNtStatusToDosError
        rtl_status_to_error.argtypes = [ctypes.c_long]
        rtl_status_to_error.restype = wintypes.ULONG
        raise ctypes.WinError(int(rtl_status_to_error(status)))
    return file_handle.value


def _windows_final_path(handle: int) -> str:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_final_path(handle, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    final_path = buffer.value
    if final_path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + final_path[8:]
    if final_path.startswith("\\\\?\\"):
        return final_path[4:]
    return final_path


def _windows_close_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL("kernel32").CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _open_windows_source_descriptor(
    relative_parts: tuple[str, ...],
    root_descriptor: int,
) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("reparse_tag", wintypes.DWORD),
        ]

    get_file_information = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).GetFileInformationByHandleEx
    get_file_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    get_file_information.restype = wintypes.BOOL

    if not relative_parts:
        raise OSError("source_relative_path_invalid")
    root_handle = msvcrt.get_osfhandle(root_descriptor)
    relative_name = "\\".join(relative_parts)
    source_handle = _windows_nt_create_file(
        relative_name,
        root_handle=root_handle,
        directory=False,
    )
    try:
        tag_info = _FileAttributeTagInfo()
        if not get_file_information(
            source_handle, 9, ctypes.byref(tag_info), ctypes.sizeof(tag_info)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if tag_info.file_attributes & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
            raise OSError("source_reparse_point")
        final_text = os.path.normcase(
            os.path.abspath(_windows_final_path(source_handle))
        )
        root_text = os.path.normcase(os.path.abspath(_windows_final_path(root_handle)))
        try:
            if os.path.commonpath((root_text, final_text)) != root_text:
                raise OSError("source_path_outside_root")
        except ValueError as exc:
            raise OSError("source_path_outside_root") from exc
        return msvcrt.open_osfhandle(source_handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        _windows_close_handle(source_handle)
        raise


def _source_metadata(
    path: Path,
    source: EvidenceSource,
    source_root: Path,
    source_root_identity: tuple[int, int],
) -> tuple[str | None, int | None]:
    """Inspect existence and size without reading an omitted-content source."""

    descriptor: int | None = None
    try:
        descriptor = _open_source_descriptor(path, source_root, source_root_identity)
        metadata = os.fstat(descriptor)
    except FileNotFoundError:
        status = "disappeared" if source.present_at_discovery else source.missing_status
        return status, None
    except EvidenceCollectionError as exc:
        if exc.code == "path_missing":
            status = (
                "disappeared" if source.present_at_discovery else source.missing_status
            )
            return status, None
        raise
    except OSError:
        return "unavailable", None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if not stat.S_ISREG(metadata.st_mode):
        return "unavailable", metadata.st_size
    return None, metadata.st_size


def _parse_records(
    raw: bytes, source: EvidenceSource
) -> tuple[list[Mapping[str, Any]], str | None]:
    if not raw:
        return [], "empty_response" if source.format == "response" else "empty"
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [], "invalid_response" if source.format == "response" else "invalid"
    if source.format == "jsonl":
        records: list[Mapping[str, Any]] = []
        try:
            for line in decoded.splitlines():
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    return [], "invalid"
                records.append(value)
        except json.JSONDecodeError:
            return [], "invalid"
        return records, None
    try:
        value = json.loads(decoded)
    except json.JSONDecodeError:
        return [], "invalid_response" if source.format == "response" else "invalid"
    if isinstance(value, dict):
        return [value], None
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value, None
    return [], "invalid_response" if source.format == "response" else "invalid"


def _captured_at(value: str) -> str:
    parsed = _parse_timestamp(value, None)
    if parsed.utc is None:
        raise EvidenceCollectionError("captured_at_invalid")
    return parsed.utc.astimezone(JST).isoformat(timespec="seconds")


def _class_status(artifacts: Sequence[Mapping[str, Any]]) -> tuple[str, int | None]:
    if not artifacts:
        return "not_listed", None
    statuses = {str(item["status"]) for item in artifacts}
    counts = [
        item["record_count"]
        for item in artifacts
        if isinstance(item["record_count"], int)
    ]
    if statuses == {"collected"}:
        return "collected", sum(counts)
    if len(statuses) == 1:
        only = next(iter(statuses))
        return only, sum(counts) if counts and only == "no_correlated_records" else None
    return "partial", sum(counts) if counts else None


def collect_evidence(
    *,
    source_root: str | Path,
    sources: Sequence[EvidenceSource],
    target_date: str,
    captured_at: str,
    correlation: Mapping[str, str | None],
) -> EvidenceCollection:
    """Collect supplied JSON/JSONL sources into a 26-class redacted manifest."""

    target = _validate_date(target_date, "target_date")
    captured = _captured_at(captured_at)
    if not isinstance(correlation, Mapping) or set(correlation) - set(_IDENTITY_FIELDS):
        raise EvidenceCollectionError("correlation_schema_invalid")
    safe_correlation: dict[str, str] = {}
    for field, value in correlation.items():
        if value is not None:
            safe_correlation[field] = _validate_safe_id(value, field)

    source_root_path = _absolute_path(source_root)
    root_descriptor: int | None = None
    try:
        root_descriptor = _open_source_root_descriptor(source_root_path)
        root_metadata = os.fstat(root_descriptor)
    except OSError as exc:
        raise EvidenceCollectionError("source_root_invalid") from exc
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise EvidenceCollectionError("source_root_not_directory")
    source_root_identity = (root_metadata.st_dev, root_metadata.st_ino)
    resolved_root = source_root_path

    seen_refs: set[tuple[str, str]] = set()
    grouped: dict[str, list[dict[str, Any]]] = {
        class_id: [] for class_id in EVIDENCE_CLASS_IDS
    }
    output_artifacts: dict[str, bytes] = {}
    events_with_order: list[tuple[datetime | None, int, dict[str, Any]]] = []
    dropped_fields = 0
    source_sequence = 0

    for source in sources:
        if not isinstance(source, EvidenceSource):
            raise EvidenceCollectionError("source_contract_invalid")
        if source.class_id not in _ALLOWED_FORMATS:
            raise EvidenceCollectionError("evidence_class_not_allowlisted")
        if source.format not in _ALLOWED_FORMATS[source.class_id]:
            raise EvidenceCollectionError("evidence_format_not_allowlisted")
        if source.class_id == "X02" and not safe_correlation.get("thread_id"):
            raise EvidenceCollectionError("x02_thread_id_required")
        if source.source_timezone not in {None, "UTC", "Asia/Tokyo"}:
            raise EvidenceCollectionError("source_timezone_invalid")
        if source.retention_status not in {"unknown", "no_expiry", "known"}:
            raise EvidenceCollectionError("source_retention_status_invalid")
        if source.expires_at is None:
            if source.retention_status == "known":
                raise EvidenceCollectionError("source_expiry_missing")
            expiry_raw = None
            expiry_utc = None
        else:
            if source.retention_status != "known":
                raise EvidenceCollectionError("source_expiry_status_mismatch")
            parsed_expiry = _parse_timestamp(source.expires_at, source.source_timezone)
            if parsed_expiry.utc is None:
                raise EvidenceCollectionError("source_expiry_invalid")
            expiry_raw = _safe_timestamp_raw(parsed_expiry)
            expiry_utc = _utc_text(parsed_expiry.utc)
        _validate_safe_id(source.source_ref, "source_ref")
        if source.missing_status not in _MISSING_STATUSES:
            raise EvidenceCollectionError("missing_status_invalid")
        if not isinstance(source.present_at_discovery, bool):
            raise EvidenceCollectionError("source_presence_invalid")
        key = (source.class_id, source.source_ref)
        if key in seen_refs:
            raise EvidenceCollectionError("duplicate_source_ref")
        seen_refs.add(key)
        _root, source_path = _resolve_source_path(resolved_root, source.relative_path)
        artifact: dict[str, Any] = {
            "class_id": source.class_id,
            "source_ref": source.source_ref,
            "source_format": source.format,
            "retention_status": source.retention_status,
            "source_expires_at_raw": expiry_raw,
            "source_expires_at_utc": expiry_utc,
            "status": "unavailable",
            "reason_code": "source_unavailable",
            "record_count": None,
            "source_bytes": None,
            "artifact_ref": None,
            "redacted_bytes": None,
            "redacted_sha256": None,
        }
        if source.format in {"sqlite", "binary", "html_snapshot", "text", "xml"}:
            read_status, source_size = _source_metadata(
                source_path,
                source,
                resolved_root,
                source_root_identity,
            )
            if read_status is not None:
                artifact["status"] = read_status
                artifact["reason_code"] = {
                    "not_saved": "source_not_saved",
                    "expired": "source_expired",
                    "unavailable": "source_unavailable",
                    "suppressed": "source_suppressed",
                    "disappeared": "source_disappeared",
                }[read_status]
                artifact["source_bytes"] = source_size
                grouped[source.class_id].append(artifact)
                continue
            artifact["source_bytes"] = source_size
            artifact["status"] = "content_omitted"
            artifact["reason_code"] = {
                "sqlite": "sqlite_snapshot_api_required",
                "binary": "binary_content_not_allowlisted",
                "html_snapshot": "html_content_not_allowlisted",
                "text": "unstructured_text_not_allowlisted",
                "xml": "structured_adapter_required",
            }[source.format]
            grouped[source.class_id].append(artifact)
            continue
        raw, read_status, source_size = _read_source(
            source_path, source, resolved_root, source_root_identity
        )
        if read_status is not None:
            artifact["status"] = read_status
            artifact["reason_code"] = {
                "not_saved": "source_not_saved",
                "expired": "source_expired",
                "unavailable": "source_unavailable",
                "suppressed": "source_suppressed",
                "disappeared": "source_disappeared",
                "omitted_size_limit": "source_size_limit",
            }[read_status]
            artifact["source_bytes"] = source_size
            grouped[source.class_id].append(artifact)
            continue
        assert raw is not None
        artifact["source_bytes"] = source_size
        records, parse_status = _parse_records(raw, source)
        if parse_status is not None:
            artifact["status"] = parse_status
            artifact["reason_code"] = {
                "empty": "source_empty",
                "empty_response": "response_empty",
                "invalid": "source_invalid",
                "invalid_response": "response_invalid",
            }[parse_status]
            grouped[source.class_id].append(artifact)
            continue

        source_events: list[dict[str, Any]] = []
        for index, record in enumerate(records, start=1):
            normalized_record = _normalize_record_keys(record)
            if normalized_record is None:
                dropped_fields += len(record)
                continue
            record = normalized_record
            record_ids = _identity_values(record)
            parsed_time = _time_for_record(record, source.source_timezone)
            record_has_identity = any(field in record for field in _IDENTITY_FIELDS)
            if source.class_id == "X02" and record_ids.get(
                "thread_id"
            ) != safe_correlation.get("thread_id"):
                continue
            if not _matches_correlation(
                record_ids,
                safe_correlation,
                parsed_time,
                target,
                record_has_identity=record_has_identity,
            ):
                continue
            event, dropped = _safe_event(
                record,
                class_id=source.class_id,
                source_ref=source.source_ref,
                record_ref=f"record-{index}",
                source_timezone=source.source_timezone,
            )
            dropped_fields += dropped
            source_events.append(event)
            events_with_order.append((parsed_time.utc, source_sequence, event))
            source_sequence += 1

        artifact["record_count"] = len(source_events)
        if not source_events:
            artifact["status"] = "no_correlated_records"
            artifact["reason_code"] = "no_correlated_records"
            grouped[source.class_id].append(artifact)
            continue
        artifact_path = (
            f"artifacts/{source.class_id}/source-"
            f"{len(grouped[source.class_id]) + 1:04d}.jsonl"
        )
        encoded = "".join(
            json.dumps(event, ensure_ascii=False, allow_nan=False, sort_keys=True)
            + "\n"
            for event in source_events
        ).encode("utf-8")
        artifact["status"] = "collected"
        artifact["reason_code"] = "allowlisted_fields_only"
        artifact["artifact_ref"] = artifact_path
        artifact["redacted_bytes"] = len(encoded)
        artifact["redacted_sha256"] = hashlib.sha256(encoded).hexdigest()
        grouped[source.class_id].append(artifact)
        output_artifacts[artifact_path] = encoded

    events_with_order.sort(
        key=lambda item: (
            item[0] is None,
            item[0] or datetime.max.replace(tzinfo=timezone.utc),
            item[1],
        )
    )
    class_summaries: list[dict[str, Any]] = []
    artifact_summaries: list[dict[str, Any]] = []
    for class_id in EVIDENCE_CLASS_IDS:
        artifacts = grouped[class_id]
        status, record_count = _class_status(artifacts)
        class_summaries.append(
            {
                "class_id": class_id,
                "status": status,
                "artifact_count": len(artifacts),
                "record_count": record_count,
            }
        )
        artifact_summaries.extend(artifacts)

    manifest: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "target_date": target.isoformat(),
        "captured_at": captured,
        "correlation": safe_correlation,
        "classes": class_summaries,
        "artifacts": artifact_summaries,
        "events": [item[2] for item in events_with_order],
        "redaction": {
            "allowlist_version": 1,
            "free_text_included": False,
            "dropped_fields_count": dropped_fields,
        },
    }
    return EvidenceCollection(manifest=manifest, artifacts=output_artifacts)


def _safe_output_relative(path: str) -> PurePosixPath:
    if not isinstance(path, str):
        raise EvidenceCollectionError("output_artifact_path_invalid")
    relative = PurePosixPath(path)
    match = re.fullmatch(r"artifacts/([A-Z][0-9]{2})/source-([0-9]{4,})\.jsonl", path)
    if (
        relative.is_absolute()
        or match is None
        or match.group(1) not in EVIDENCE_CLASS_IDS
    ):
        raise EvidenceCollectionError("output_artifact_path_invalid")
    return relative


def write_evidence_bundle(
    output_path: str | Path, collection: EvidenceCollection
) -> None:
    """Atomically publish a new bundle; an existing output is never replaced."""

    if not isinstance(collection, EvidenceCollection):
        raise EvidenceCollectionError("collection_invalid")
    if not isinstance(collection.artifacts, Mapping):
        raise EvidenceCollectionError("collection_artifacts_invalid")
    target = _absolute_path(output_path)
    if target.exists():
        raise EvidenceCollectionError("evidence_output_exists")
    parent = target.parent
    _reject_reparse_components(parent)
    if not parent.is_dir():
        raise EvidenceCollectionError("evidence_output_parent_invalid")
    seen_artifact_paths: set[str] = set()
    for relative_path, content in collection.artifacts.items():
        _safe_output_relative(relative_path)
        folded_path = relative_path.casefold()
        if folded_path in seen_artifact_paths:
            raise EvidenceCollectionError("output_artifact_path_duplicate")
        seen_artifact_paths.add(folded_path)
        if not isinstance(content, bytes):
            raise EvidenceCollectionError("evidence_artifact_bytes_invalid")

    temp_name: str | None = None
    try:
        temp_name = tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent)
        stage = Path(temp_name)
        for relative_path, content in collection.artifacts.items():
            relative = _safe_output_relative(relative_path)
            artifact_path = stage.joinpath(*relative.parts)
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            with artifact_path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        manifest_bytes = (
            json.dumps(
                collection.manifest,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        manifest_path = stage / "manifest.json"
        with manifest_path.open("xb") as stream:
            stream.write(manifest_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(stage, target)
        temp_name = None
    finally:
        if temp_name is not None:
            shutil.rmtree(temp_name, ignore_errors=True)


def snapshot_sqlite(
    source_path: str | Path, destination_path: str | Path
) -> SQLiteSnapshot:
    """Create a consistent private SQLite snapshot using the backup API."""

    source = _absolute_path(source_path)
    destination = _absolute_path(destination_path)
    _reject_reparse_components(source)
    _reject_reparse_components(destination.parent)
    if not source.is_file() or not destination.parent.is_dir():
        raise EvidenceCollectionError("sqlite_snapshot_path_invalid")
    try:
        resolved_source = source.resolve(strict=True)
        resolved_destination = destination.resolve(strict=False)
        if resolved_destination == resolved_source:
            raise EvidenceCollectionError("sqlite_snapshot_alias_rejected")
    except (OSError, RuntimeError, ValueError) as exc:
        raise EvidenceCollectionError("sqlite_snapshot_path_invalid") from exc
    if destination.exists():
        raise EvidenceCollectionError("sqlite_snapshot_exists")

    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    created_destination = False
    completed = False
    try:
        source_uri = resolved_source.as_uri() + "?mode=ro"
        source_connection = sqlite3.connect(source_uri, uri=True, timeout=5.0)
        source_connection.execute("PRAGMA query_only=ON")
        with destination.open("xb"):
            created_destination = True
        destination_connection = sqlite3.connect(destination, timeout=5.0)
        source_connection.backup(destination_connection, pages=128, sleep=0.01)
        integrity = str(
            destination_connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        foreign_key_errors = sum(
            1 for _ in destination_connection.execute("PRAGMA foreign_key_check")
        )
        table_names = [
            str(row[0])
            for row in destination_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        row_count = 0
        for table_name in table_names:
            quoted = '"' + table_name.replace('"', '""') + '"'
            row_count += int(
                destination_connection.execute(
                    f"SELECT count(*) FROM {quoted}"
                ).fetchone()[0]
            )
        destination_connection.close()
        destination_connection = None
        source_connection.close()
        source_connection = None
        if integrity != "ok" or foreign_key_errors:
            raise EvidenceCollectionError("sqlite_snapshot_integrity_failed")
        digest_state = hashlib.sha256()
        with destination.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest_state.update(chunk)
        digest = digest_state.hexdigest()
        completed = True
        return SQLiteSnapshot(
            integrity_check=integrity,
            foreign_key_errors=foreign_key_errors,
            row_count=row_count,
            byte_count=destination.stat().st_size,
            sha256=digest,
        )
    except EvidenceCollectionError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise EvidenceCollectionError("sqlite_snapshot_failed") from exc
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        if created_destination and not completed and destination.exists():
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(destination) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            destination.unlink(missing_ok=True)


def _prior_class(source: str, path: str) -> str | None:
    source_lower = source.casefold().replace("\\", "/")
    path_lower = path.casefold().replace("\\", "/")
    if "production-cli.jsonl" in source_lower:
        return "L01"
    if "/traces/" in source_lower or "trace-" in path_lower:
        return "L02"
    if "task scheduler" in source_lower:
        return "X03"
    if "/backups/daily-final-20261017-" in source_lower:
        return "B02"
    if "/backups/daily-20261017/" in source_lower:
        return "B01"
    return None


def inspect_prior_manifest(path: str | Path) -> dict[str, Any]:
    """Validate an E0 manifest and return only non-sensitive index metadata."""

    manifest_path = _absolute_path(path)
    _reject_reparse_components(manifest_path)
    if (
        not manifest_path.is_file()
        or manifest_path.stat().st_size > MAX_PRIOR_MANIFEST_BYTES
    ):
        raise EvidenceCollectionError("prior_manifest_unavailable")
    try:
        raw = manifest_path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceCollectionError("prior_manifest_invalid") from exc
    required = {
        "manifest_version",
        "captured_at_jst",
        "target_date",
        "incident_id",
        "collection_mode",
        "redaction",
        "evidence",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise EvidenceCollectionError("prior_manifest_schema_invalid")
    if value["manifest_version"] != "1.0":
        raise EvidenceCollectionError("prior_manifest_version_unsupported")
    _validate_date(value["target_date"], "prior_target_date")
    if (
        not isinstance(value["captured_at_jst"], str)
        or _parse_timestamp(value["captured_at_jst"], None).utc is None
        or not isinstance(value["incident_id"], str)
        or not _SAFE_ID.fullmatch(value["incident_id"])
        or not isinstance(value["collection_mode"], str)
        or len(value["collection_mode"]) > 512
        or not isinstance(value["redaction"], str)
        or len(value["redaction"]) > 2048
    ):
        raise EvidenceCollectionError("prior_manifest_metadata_invalid")
    entries = value["evidence"]
    if not isinstance(entries, list):
        raise EvidenceCollectionError("prior_manifest_evidence_invalid")
    manifested: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise EvidenceCollectionError("prior_manifest_entry_invalid")
        common_keys = {"path", "source", "capture"}
        records_entry = set(entry) == common_keys | {"records"}
        backup_entry = set(entry) == common_keys | {"bytes", "last_write_time"}
        if not (records_entry or backup_entry):
            raise EvidenceCollectionError("prior_manifest_entry_invalid")
        if (
            not isinstance(entry["path"], str)
            or not entry["path"]
            or len(entry["path"]) > 2048
            or not isinstance(entry["source"], str)
            or not entry["source"]
            or len(entry["source"]) > 2048
            or not isinstance(entry["capture"], str)
            or len(entry["capture"]) > 2048
        ):
            raise EvidenceCollectionError("prior_manifest_entry_invalid")
        if (
            records_entry
            and entry["records"] is not None
            and (
                isinstance(entry["records"], bool)
                or not isinstance(entry["records"], int)
                or entry["records"] < 0
            )
        ):
            raise EvidenceCollectionError("prior_manifest_entry_invalid")
        if backup_entry:
            last_write = entry["last_write_time"]
            if (
                isinstance(entry["bytes"], bool)
                or not isinstance(entry["bytes"], int)
                or entry["bytes"] < 0
                or not isinstance(last_write, str)
                or _parse_timestamp(last_write, "Asia/Tokyo").utc is None
            ):
                raise EvidenceCollectionError("prior_manifest_entry_invalid")
        class_id = _prior_class(entry["source"], entry["path"])
        if class_id is not None:
            manifested.add(class_id)
    return {
        "manifest_version": "1.0",
        "target_date": value["target_date"],
        "evidence_count": len(entries),
        "manifested_class_ids": sorted(manifested),
    }
