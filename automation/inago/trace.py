"""Bounded, allowlisted daily trace events with explicit retention state."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
from typing import Any, Iterator, Mapping
import uuid
from zoneinfo import ZoneInfo

from automation.inago.retention import (
    RetentionError,
    retention_expiry,
    validate_data_root,
)


JST = ZoneInfo("Asia/Tokyo")
TRACE_SCHEMA_VERSION = 1
TRACE_MAX_EVENTS_PER_RUN = 2048
TRACE_MAX_BYTES_PER_RUN = 4 * 1024 * 1024
TRACE_MAX_FILES_PER_DATA_ROOT = 256
TRACE_MAX_EVENT_BYTES = 16 * 1024
TRACE_RETENTION_DAYS = 90
TRACE_SUPPRESSION_RESERVE_BYTES = 1024

_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_ERROR_CLASS = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_REPARSE_POINT = 0x400
_GLOBAL_LOCK = threading.RLock()
_PATH_LOCKS: dict[str, threading.RLock] = {}

_EVENT_FIELDS = {
    "request_id",
    "job_id",
    "root_job_id",
    "task_id",
    "attempt",
    "attempt_limit",
    "deadline_at",
    "remaining_ms",
    "elapsed_ms",
    "active_stage",
    "last_completed_stage",
    "primary_error_code",
    "error_class",
    "cause_code",
    "cause_class",
    "failed_operation",
    "retryable",
    "scope",
    "cleanup_error_code",
    "cleanup_error_class",
    "error_kind",
    "network_code",
    "correlation_status",
    "event_kind",
    "suppression_reason",
    "suppression_marker_count",
    "status",
    "host_class",
    "resource_type",
    "row_index",
    "process_returncode",
    "widget_scope",
    "widget_selector_version",
    "widget_kind",
    "widget_dom_count",
    "widget_visible_count",
    "widget_match_count",
    "widget_match_visible_count",
    "widget_wrong_visible_count",
    "related_widget_generation",
    "generation_before_period",
    "widget_empty_evidence",
    "widget_period_refresh_required",
    "widget_outcome",
    "room_condition_close_control_count",
    "room_condition_search_control_count",
    "room_condition_close_method",
    "room_condition_popup_state",
    "room_conditions_likes_verified",
    "room_conditions_comments_verified",
    "element_tag",
    "element_role",
    "element_visible",
    "element_enabled",
    "element_obscured",
    "handler_kind",
    "handler_tag",
    "handler_scope",
    "has_href",
    "href_kind",
    "link_target",
    "link_scope",
    "destination_kind",
    "timeout_ms",
    "wait_elapsed_ms",
}
_SCOPES = {"row", "query", "request", "job", "run", "cleanup"}
_CORRELATION_STATES = {"complete", "partial", "unresolved"}
_ERROR_KINDS = {
    "network_error",
    "timeout",
    "target_closed",
    "page_closed",
    "navigation_interrupted",
    "protocol_error",
    "browser_launch_error",
    "other_error",
}
_WIDGET_SCOPE = {"explore_related_queries"}
_WIDGET_SELECTOR_VERSIONS = {
    "related-widget-v2",
    "related-widget-v3",
    "related-widget-v4",
}
_WIDGET_KINDS = {"related_keywords", "related_topics", "unknown", "multiple"}
_WIDGET_EMPTY_EVIDENCE = {"none", "explicit_no_data"}
_WIDGET_OUTCOMES = {"ready", "deadline", "ambiguous", "contract_mismatch"}
_WIDGET_COUNT_FIELDS = {
    "widget_dom_count",
    "widget_visible_count",
    "widget_match_count",
    "widget_match_visible_count",
    "widget_wrong_visible_count",
}
_WIDGET_GENERATION_FIELDS = {"related_widget_generation", "generation_before_period"}
_ROOM_CONDITION_CLOSE_METHODS = {
    "close_control",
    "search_submit",
    "escape",
    "already_closed",
    "none",
}
_ROOM_CONDITION_POPUP_STATES = {"visible", "hidden", "unknown"}
_HEALTH_ORDER = {"healthy": 0, "degraded": 1, "unavailable": 2}
_COMPLETION_STAGE_ALIASES = {
    "trends_period_reflected": "trends_period_reflection_wait",
    "trends_top_rows_updated": "trends_top_rows_wait",
    "trends_related_widget_wait_ready": "trends_related_widget_wait",
    "room_search_restored": "room_search_restore",
}


class TraceContractError(ValueError):
    """A trace event or configured quota is outside the safe contract."""


@dataclass(frozen=True, slots=True)
class TracePolicy:
    """Per-run and per-root upper bounds; production defaults are fixed here."""

    max_events_per_run: int = TRACE_MAX_EVENTS_PER_RUN
    max_bytes_per_run: int = TRACE_MAX_BYTES_PER_RUN
    max_files_per_root: int = TRACE_MAX_FILES_PER_DATA_ROOT
    max_event_bytes: int = TRACE_MAX_EVENT_BYTES
    retention_days: int = TRACE_RETENTION_DAYS
    suppression_reserve_bytes: int = TRACE_SUPPRESSION_RESERVE_BYTES

    def __post_init__(self) -> None:
        positive = (
            self.max_events_per_run,
            self.max_bytes_per_run,
            self.max_files_per_root,
            self.max_event_bytes,
            self.retention_days,
            self.suppression_reserve_bytes,
        )
        if any(type(value) is not int or value <= 0 for value in positive):
            raise TraceContractError("trace policy values must be positive integers")
        if self.max_events_per_run < 2:
            raise TraceContractError(
                "trace event quota must reserve a suppression event"
            )
        if self.max_bytes_per_run < 1024:
            raise TraceContractError(
                "trace byte quota is too small for a suppression event"
            )
        if self.suppression_reserve_bytes < TRACE_SUPPRESSION_RESERVE_BYTES:
            raise TraceContractError(
                "trace suppression reserve is below its fixed minimum"
            )
        if self.suppression_reserve_bytes >= self.max_bytes_per_run:
            raise TraceContractError(
                "trace suppression reserve must be below the byte quota"
            )
        if self.max_event_bytes > self.max_bytes_per_run:
            raise TraceContractError("maximum event size exceeds the run byte quota")


DEFAULT_TRACE_POLICY = TracePolicy()


@dataclass(frozen=True, slots=True)
class TraceWriteResult:
    status: str
    reason: str | None = None
    bytes_written: int = 0
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class TraceRetentionResult:
    expired_events: int
    removed_files: int
    rewritten_files: int
    failures: int

    @property
    def health(self) -> str:
        return "degraded" if self.failures else "healthy"


def redact_sensitive_text(value: str) -> str:
    """Redact URL/query and credential-shaped text without preserving values."""

    if not isinstance(value, str):
        raise TypeError("diagnostic text must be a string")
    text = re.sub(
        r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key|"
        r"access[_-]?token|refresh[_-]?token|api[_-]?key|password|secret|session)"
        r"(\s*[:=]\s*)(?:bearer\s+|basic\s+)?[^\s,;]+",
        r"\1\2<redacted>",
        value,
    )
    text = re.sub(r"https?://[^\s\]\[()<>\"']+", "<url:redacted>", text)
    text = re.sub(r"([?&][A-Za-z0-9_.-]+)=([^&\s]+)", r"\1=<redacted>", text)
    return text[:512]


def _safe_id(value: object, *, name: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _SAFE_REF.fullmatch(value):
        raise TraceContractError(f"{name} is not a safe identifier")
    return value


def _safe_code(value: object, *, name: str, optional: bool = True) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _SAFE_REF.fullmatch(value):
        raise TraceContractError(f"{name} is not a safe code")
    return value


def _safe_class(value: object, *, name: str, optional: bool = True) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _SAFE_ERROR_CLASS.fullmatch(value):
        raise TraceContractError(f"{name} is not a safe error class")
    return value


def _safe_exception_class(error: BaseException | None) -> str | None:
    if error is None:
        return None
    name = type(error).__name__
    return name if _SAFE_ERROR_CLASS.fullmatch(name) else "Exception"


def _aware_iso(value: object, *, name: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, (str, datetime)):
        raise TraceContractError(f"{name} must be a timezone-aware ISO timestamp")
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise TraceContractError(
            f"{name} must be a timezone-aware ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TraceContractError(f"{name} must include a timezone offset")
    return parsed.astimezone(JST).isoformat(timespec="milliseconds")


def _safe_target_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise TraceContractError("target_date must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise TraceContractError("target_date must be canonical ISO date")
    return value


def _stable_span_id(kind: str, run_id: str, identity: str) -> str:
    value = uuid.uuid5(
        uuid.NAMESPACE_URL, f"rakuten-room-inago:{kind}:{run_id}:{identity}"
    )
    return str(value)


def make_trace_event(
    *,
    run_id: str,
    target_date: str,
    component: str,
    operation: str,
    phase: str,
    clock: Any | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Create a strict event envelope; arbitrary text and unknown fields are rejected."""

    if not isinstance(component, str) or not _SAFE_LABEL.fullmatch(component):
        raise TraceContractError("component is not a safe label")
    if not isinstance(operation, str) or not _SAFE_LABEL.fullmatch(operation):
        raise TraceContractError("operation is not a safe label")
    if phase not in {"begin", "end", "error", "cancel", "status"}:
        raise TraceContractError("phase is outside the trace contract")
    _safe_id(run_id, name="run_id")
    target_date = _safe_target_date(target_date)
    if set(fields) - _EVENT_FIELDS:
        raise TraceContractError("event contains fields outside the allowlist")
    now = clock.now_jst() if clock is not None else datetime.now(JST)
    at = _aware_iso(now, name="at")
    event: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "span_id": (
            _safe_id(span_id, name="span_id")
            if span_id
            else _stable_span_id("run", run_id, run_id)
        ),
        "component": component,
        "operation": operation,
        "phase": phase,
        "at": at,
        "elapsed_ms": 0,
        "run_id": run_id,
        "target_date": target_date,
        "correlation_status": "unresolved",
        "retention_expires_at": retention_expiry("raw", at).isoformat(
            timespec="milliseconds"
        ),
    }
    selected_parent = _safe_id(parent_span_id, name="parent_span_id", optional=True)
    if selected_parent is not None:
        event["parent_span_id"] = selected_parent
    for key, value in fields.items():
        if key in {"request_id", "job_id", "root_job_id", "task_id"}:
            selected = _safe_id(value, name=key, optional=True)
            if selected is not None:
                event[key] = selected
        elif key in {"primary_error_code", "cause_code", "cleanup_error_code"}:
            selected = _safe_code(value, name=key)
            if selected is not None:
                event[key] = selected
        elif key in {"error_class", "cause_class", "cleanup_error_class"}:
            selected = _safe_class(value, name=key)
            if selected is not None:
                event[key] = selected
        elif key == "network_code":
            if value is not None and (
                not isinstance(value, str)
                or not re.fullmatch(r"ERR_[A-Z0-9_]{1,64}", value)
            ):
                raise TraceContractError("network_code is outside the safe enum")
            if value is not None:
                event[key] = value
        elif key == "error_kind":
            if value is not None and value not in _ERROR_KINDS:
                raise TraceContractError("error_kind is outside the safe enum")
            if value is not None:
                event[key] = value
        elif key == "deadline_at":
            selected = _aware_iso(value, name=key, optional=True)
            if selected is not None:
                event[key] = selected
        elif key in {
            "attempt",
            "attempt_limit",
            "remaining_ms",
            "elapsed_ms",
            "suppression_marker_count",
            "process_returncode",
            "row_index",
        }:
            if value is None:
                continue
            if type(value) is not int or value < 0:
                raise TraceContractError(f"{key} must be a non-negative integer")
            if key == "row_index" and not 1 <= value <= 50:
                raise TraceContractError("row_index must be in the accepted first 50")
            event[key] = value
        elif key in _WIDGET_COUNT_FIELDS:
            if type(value) is not int or not 0 <= value <= 100:
                raise TraceContractError(f"{key} must be an integer from 0 to 100")
            event[key] = value
        elif key in _WIDGET_GENERATION_FIELDS:
            if type(value) is not int or not 0 <= value <= 10000000:
                raise TraceContractError(
                    f"{key} is outside the widget generation range"
                )
            event[key] = value
        elif key in {
            "room_condition_close_control_count",
            "room_condition_search_control_count",
        }:
            if type(value) is not int or not 0 <= value <= 100:
                raise TraceContractError(f"{key} must be from 0 to 100")
            event[key] = value
        elif key in {"timeout_ms", "wait_elapsed_ms"}:
            if (
                type(value) is not int
                or not 0 <= value <= 17_100_000
                or key == "timeout_ms"
                and value > 60_000
            ):
                raise TraceContractError(f"{key} is outside the bounded wait range")
            event[key] = value
        elif key in {
            "element_visible",
            "element_enabled",
            "element_obscured",
            "has_href",
        }:
            if type(value) is not bool:
                raise TraceContractError(f"{key} must be a boolean")
            event[key] = value
        elif key == "element_tag":
            if value not in {"a", "button", "div", "span", "other"}:
                raise TraceContractError("element_tag is outside the safe enum")
            event[key] = value
        elif key == "element_role":
            if value not in {"link", "button", "other"}:
                raise TraceContractError("element_role is outside the safe enum")
            event[key] = value
        elif key == "handler_kind":
            if value not in {"link", "button", "role", "onclick", "none"}:
                raise TraceContractError("handler_kind is outside the safe enum")
            event[key] = value
        elif key == "handler_tag":
            if value not in {"a", "button", "div", "span", "other"}:
                raise TraceContractError("handler_tag is outside the safe enum")
            event[key] = value
        elif key == "handler_scope":
            if value not in {"self", "ancestor", "none"}:
                raise TraceContractError("handler_scope is outside the safe enum")
            event[key] = value
        elif key == "href_kind":
            if value not in {
                "none",
                "empty",
                "blank",
                "rakuten_item",
                "rakuten_other",
                "other_https",
                "other_scheme",
                "invalid",
            }:
                raise TraceContractError("href_kind is outside the safe enum")
            event[key] = value
        elif key == "link_target":
            if value not in {"none", "_blank", "_self", "other"}:
                raise TraceContractError("link_target is outside the safe enum")
            event[key] = value
        elif key == "link_scope":
            if value not in {"none", "self", "ancestor", "descendant", "other"}:
                raise TraceContractError("link_scope is outside the safe enum")
            event[key] = value
        elif key == "destination_kind":
            if value not in {"popup", "same_tab"}:
                raise TraceContractError("destination_kind is outside the safe enum")
            event[key] = value
        elif key in {
            "active_stage",
            "last_completed_stage",
            "failed_operation",
            "event_kind",
            "suppression_reason",
            "status",
            "host_class",
            "resource_type",
        }:
            selected = _safe_code(value, name=key)
            if selected is not None:
                event[key] = selected
        elif key == "widget_selector_version":
            if value is not None and value not in _WIDGET_SELECTOR_VERSIONS:
                raise TraceContractError(
                    "widget_selector_version is outside the trace contract"
                )
            if value is not None:
                event[key] = value
        elif key == "widget_scope":
            if value is not None and value not in _WIDGET_SCOPE:
                raise TraceContractError("widget_scope is outside the trace contract")
            if value is not None:
                event[key] = value
        elif key == "widget_kind":
            if value is not None and value not in _WIDGET_KINDS:
                raise TraceContractError("widget_kind is outside the trace contract")
            if value is not None:
                event[key] = value
        elif key == "widget_empty_evidence":
            if value is not None and value not in _WIDGET_EMPTY_EVIDENCE:
                raise TraceContractError(
                    "widget_empty_evidence is outside the trace contract"
                )
            if value is not None:
                event[key] = value
        elif key == "widget_outcome":
            if value is not None and value not in _WIDGET_OUTCOMES:
                raise TraceContractError("widget_outcome is outside the trace contract")
            if value is not None:
                event[key] = value
        elif key == "room_condition_close_method":
            if value is not None and value not in _ROOM_CONDITION_CLOSE_METHODS:
                raise TraceContractError("room condition close method is invalid")
            if value is not None:
                event[key] = value
        elif key == "room_condition_popup_state":
            if value is not None and value not in _ROOM_CONDITION_POPUP_STATES:
                raise TraceContractError("room condition popup state is invalid")
            if value is not None:
                event[key] = value
        elif key in {
            "room_conditions_likes_verified",
            "room_conditions_comments_verified",
        }:
            if value is not None and not isinstance(value, bool):
                raise TraceContractError(f"{key} must be boolean")
            if value is not None:
                event[key] = value
        elif key == "widget_period_refresh_required":
            if value is not None and not isinstance(value, bool):
                raise TraceContractError(
                    "widget_period_refresh_required must be boolean"
                )
            if value is not None:
                event[key] = value
        elif key == "retryable":
            if value is not None and not isinstance(value, bool):
                raise TraceContractError("retryable must be boolean")
            if value is not None:
                event[key] = value
        elif key == "scope":
            if value is not None and value not in _SCOPES:
                raise TraceContractError("scope is outside the trace contract")
            if value is not None:
                event[key] = value
        elif key == "correlation_status":
            if value not in _CORRELATION_STATES:
                raise TraceContractError(
                    "correlation_status is outside the trace contract"
                )
            event[key] = value

    if "elapsed_ms" in fields:
        value = fields["elapsed_ms"]
        if type(value) is not int or value < 0:
            raise TraceContractError("elapsed_ms must be a non-negative integer")
        event["elapsed_ms"] = value
    identifiers = ("request_id", "job_id", "task_id")
    present = sum(key in event for key in identifiers)
    expected_correlation = (
        "unresolved"
        if present == 0
        else "complete" if present == len(identifiers) else "partial"
    )
    if event["correlation_status"] != expected_correlation:
        raise TraceContractError("correlation_status does not match the available IDs")
    event["correlation_status"] = expected_correlation
    return event


def _reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    junction = getattr(path, "is_junction", None)
    return (
        path.is_symlink()
        or bool(attributes & _REPARSE_POINT)
        or bool(callable(junction) and junction())
    )


def _read_bounded(path: Path, limit: int) -> bytes:
    """Read a regular trace file with an enforced allocation bound."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        named = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or _reparse(path)
            or not os.path.samestat(opened, named)
        ):
            raise TraceContractError("trace target changed during safe read")
        if opened.st_size > limit:
            raise TraceContractError("trace file exceeds the configured byte limit")
        chunks: list[bytes] = []
        total = 0
        while total <= limit:
            chunk = os.read(descriptor, min(64 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise TraceContractError("trace file exceeds the configured byte limit")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _trace_json_lines(raw: bytes, max_event_bytes: int) -> Iterator[bytes]:
    """Yield complete newline-terminated JSON records only."""

    if raw and not raw.endswith(b"\n"):
        raise TraceContractError("trace file ends with an incomplete event")
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            raise TraceContractError("trace file ends with an incomplete event")
        if len(line) > max_event_bytes:
            raise TraceContractError("trace event exceeds the byte limit")
        payload = line[:-1]
        yield payload[:-1] if payload.endswith(b"\r") else payload


def _assert_safe_descendant(
    root: Path, candidate: Path, *, allow_missing: bool
) -> None:
    resolved_root = validate_data_root(root, allow_missing=allow_missing)
    absolute = Path(os.path.abspath(candidate))
    try:
        relative = absolute.relative_to(Path(os.path.abspath(resolved_root)))
    except ValueError as exc:
        raise TraceContractError("trace path escaped the configured data root") from exc
    if not relative.parts:
        raise TraceContractError("trace path must be below the data root")
    cursor = Path(os.path.abspath(resolved_root))
    for part in relative.parts:
        cursor = cursor / part
        if _reparse(cursor):
            raise TraceContractError(
                "trace path contains a symbolic link or reparse point"
            )


def _thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(path))
    with _GLOBAL_LOCK:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _process_root_lock(data_root: Path) -> Iterator[None]:
    """Serialize trace-root quota and retention changes across processes."""

    trace_root = data_root / "logs" / "traces"
    trace_root.mkdir(parents=True, exist_ok=True)
    _assert_safe_descendant(data_root, trace_root, allow_missing=False)
    lock_path = trace_root / ".quota.lock"
    _assert_safe_descendant(data_root, lock_path, allow_missing=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        named = lock_path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or _reparse(lock_path)
            or not os.path.samestat(opened, named)
        ):
            raise TraceContractError("trace quota lock is not a safe regular file")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        if opened.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


class TraceWriter:
    """Append run-scoped JSONL events with count, byte, file and path limits."""

    def __init__(
        self,
        data_root: str | Path,
        run_id: str,
        target_date: str,
        *,
        policy: TracePolicy = DEFAULT_TRACE_POLICY,
        clock: Any | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.run_id = _safe_id(run_id, name="run_id") or ""
        self.target_date = _safe_target_date(target_date)
        self.policy = policy
        self.clock = clock
        self.path = (
            self.data_root
            / "logs"
            / "traces"
            / self.target_date
            / f"{self.run_id}.jsonl"
        )
        self._loaded = False
        self._event_count = 0
        self._bytes = 0
        self._suppressed = False
        self._suppressed_count = 0
        self._health = "healthy"
        self._append_failed = False
        self._lock = _thread_lock(self.path)
        self._trace_root = self.data_root / "logs" / "traces"
        self._root_lock = _thread_lock(self._trace_root)

    @property
    def health(self) -> str:
        return self._health

    @property
    def suppressed_count(self) -> int:
        return self._suppressed_count

    def mark_unavailable(self) -> None:
        self._set_health("unavailable")

    def mark_degraded(self) -> None:
        self._set_health("degraded")

    def write(self, event: Mapping[str, Any]) -> TraceWriteResult:
        try:
            normalized = _validate_trace_event(event, self.run_id, self.target_date)
            encoded = (
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            if len(encoded) > self.policy.max_event_bytes:
                return self._suppress("event_size_limit", normalized)
            _assert_safe_descendant(
                self.data_root, self.path.parent, allow_missing=True
            )
            with self._root_lock, _process_root_lock(self.data_root), self._lock:
                if self._append_failed:
                    self._set_health("unavailable")
                    return TraceWriteResult(
                        "unavailable", "trace_io_unavailable", path=self.path
                    )
                self._load_state()
                if self._suppressed:
                    self._suppressed_count += 1
                    return TraceWriteResult(
                        "suppressed", "quota_already_reached", path=self.path
                    )
                self._ensure_file_capacity()
                if self._event_count >= self.policy.max_events_per_run - 1:
                    return self._suppress_locked("event_limit", normalized)
                byte_ceiling = (
                    self.policy.max_bytes_per_run
                    - self.policy.suppression_reserve_bytes
                )
                if self._bytes + len(encoded) > byte_ceiling:
                    return self._suppress_locked("byte_limit", normalized)
                self._append_payload_safely(self.path, encoded)
                self._event_count += 1
                self._bytes += len(encoded)
                return TraceWriteResult(
                    "written", bytes_written=len(encoded), path=self.path
                )
        except (
            OSError,
            TraceContractError,
            TypeError,
            ValueError,
            UnicodeError,
        ) as exc:
            self._set_health("unavailable")
            return TraceWriteResult(
                "unavailable", _safe_failure_reason(exc), path=self.path
            )

    def _set_health(self, health: str) -> None:
        if _HEALTH_ORDER[health] > _HEALTH_ORDER[self._health]:
            self._health = health

    def _load_state(self) -> None:
        if self._loaded:
            return
        _assert_safe_descendant(self.data_root, self.path, allow_missing=True)
        if self.path.exists():
            if not self.path.is_file():
                raise TraceContractError("existing trace is not a regular file")
            raw = _read_bounded(self.path, self.policy.max_bytes_per_run)
            for line in _trace_json_lines(raw, self.policy.max_event_bytes):
                record = json.loads(line.decode("utf-8", errors="strict"))
                _validate_trace_event(record, self.run_id, self.target_date)
                self._event_count += 1
                if self._event_count > self.policy.max_events_per_run:
                    raise TraceContractError("existing trace exceeds the event quota")
                if record.get("event_kind") == "trace_suppressed":
                    self._suppressed = True
                    self._set_health("degraded")
            self._bytes = len(raw)
        self._loaded = True

    def _ensure_file_capacity(self) -> None:
        _assert_safe_descendant(self.data_root, self.path.parent, allow_missing=True)
        trace_root = self.data_root / "logs" / "traces"
        paths = _trace_paths(trace_root, limit=self.policy.max_files_per_root)
        exists = self.path.exists()
        if not exists and len(paths) >= self.policy.max_files_per_root:
            raise TraceContractError("trace file quota reached")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _assert_safe_descendant(self.data_root, self.path.parent, allow_missing=False)

    def _append_payload(self, path: Path, encoded: bytes) -> None:
        _assert_safe_descendant(self.data_root, path.parent, allow_missing=False)
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            opened = os.fstat(descriptor)
            named = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or _reparse(path)
                or not os.path.samestat(opened, named)
            ):
                raise TraceContractError("trace target changed during safe open")
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("trace append made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _append_payload_safely(self, path: Path, encoded: bytes) -> None:
        try:
            self._append_payload(path, encoded)
        except OSError:
            # A short append may leave an incomplete JSONL record. Do not append
            # again to a file whose last record cannot be trusted.
            self._append_failed = True
            raise

    def _suppress(self, reason: str, event: Mapping[str, Any]) -> TraceWriteResult:
        with self._root_lock, _process_root_lock(self.data_root), self._lock:
            try:
                if self._append_failed:
                    self._set_health("unavailable")
                    return TraceWriteResult(
                        "unavailable", "trace_io_unavailable", path=self.path
                    )
                self._load_state()
                return self._suppress_locked(reason, event)
            except (
                OSError,
                TraceContractError,
                TypeError,
                ValueError,
                UnicodeError,
            ) as exc:
                self._set_health("unavailable")
                return TraceWriteResult(
                    "unavailable", _safe_failure_reason(exc), path=self.path
                )

    def _suppress_locked(
        self, reason: str, event: Mapping[str, Any]
    ) -> TraceWriteResult:
        if self._append_failed:
            self._set_health("unavailable")
            return TraceWriteResult(
                "unavailable", "trace_io_unavailable", path=self.path
            )
        self._suppressed_count += 1
        self._set_health("degraded")
        if self._suppressed:
            return TraceWriteResult("suppressed", reason, path=self.path)
        marker = _suppression_event(event, reason, self.clock)
        encoded = (
            json.dumps(
                marker, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        ).encode("utf-8")
        if (
            len(encoded) > self.policy.max_event_bytes
            or len(encoded) > self.policy.suppression_reserve_bytes
            or self._event_count >= self.policy.max_events_per_run
            or self._bytes + len(encoded) > self.policy.max_bytes_per_run
        ):
            self._set_health("unavailable")
            return TraceWriteResult(
                "unavailable", "suppression_marker_does_not_fit", path=self.path
            )
        self._ensure_file_capacity()
        self._append_payload_safely(self.path, encoded)
        self._event_count += 1
        self._bytes += len(encoded)
        self._suppressed = True
        return TraceWriteResult(
            "suppressed", reason, bytes_written=len(encoded), path=self.path
        )


class TraceSession:
    """Track active and completed stages within one run/request correlation."""

    def __init__(
        self,
        writer: TraceWriter,
        *,
        component: str,
        request_id: str | None = None,
        job_id: str | None = None,
        root_job_id: str | None = None,
        task_id: str | None = None,
        attempt: int | None = None,
        attempt_limit: int | None = None,
        deadline_at: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        clock: Any | None = None,
    ) -> None:
        if not isinstance(writer, TraceWriter):
            raise TraceContractError("TraceSession requires a TraceWriter")
        if not isinstance(component, str) or not _SAFE_LABEL.fullmatch(component):
            raise TraceContractError("component is not a safe label")
        self.writer = writer
        self.component = component
        self.run_id = writer.run_id
        self.target_date = writer.target_date
        self.request_id = _safe_id(request_id, name="request_id", optional=True)
        self.job_id = _safe_id(job_id, name="job_id", optional=True)
        self.root_job_id = _safe_id(root_job_id, name="root_job_id", optional=True)
        self.task_id = _safe_id(task_id, name="task_id", optional=True)
        self.attempt = attempt
        self.attempt_limit = attempt_limit
        self.deadline_at = _aware_iso(deadline_at, name="deadline_at", optional=True)
        self.clock = clock or writer.clock
        run_span_id = _stable_span_id("run", self.run_id, self.run_id)
        root_span = component == "orchestrator" and parent_span_id is None
        self.parent_span_id = (
            None
            if root_span
            else _safe_id(parent_span_id, name="parent_span_id", optional=True)
            or run_span_id
        )
        identity = (
            self.request_id or self.job_id or self.root_job_id or str(uuid.uuid4())
        )
        self.span_id = (
            _safe_id(span_id, name="span_id")
            if span_id is not None
            else (
                run_span_id
                if root_span
                else _stable_span_id(component, self.run_id, identity)
            )
        )
        self.active_stage: str | None = None
        self.last_completed_stage: str | None = None
        self._started_at: datetime | None = None
        self._started_monotonic: float | None = None
        self._stage_started: dict[str, float] = {}
        self._health = "healthy"

    @property
    def health(self) -> str:
        return (
            self._health
            if _HEALTH_ORDER[self._health] >= _HEALTH_ORDER[self.writer.health]
            else self.writer.health
        )

    def start(self, *, operation: str) -> TraceWriteResult:
        if self._started_at is not None:
            raise TraceContractError("trace session has already started")
        now = self._now()
        self._started_at = now
        self._started_monotonic = self._monotonic()
        self.active_stage = operation
        self._stage_started[operation] = self._started_monotonic
        return self._emit(operation, "begin", event_kind="stage")

    def record_stage(
        self,
        operation: str,
        *,
        phase: str,
        elapsed_ms: int | None = None,
        at: str | None = None,
        **fields: Any,
    ) -> TraceWriteResult:
        if phase == "begin":
            self.active_stage = operation
            self._stage_started[operation] = self._monotonic()
        elif phase == "end":
            matches_active = self.active_stage in {None, operation}
            if (
                not matches_active
                and self.active_stage is not None
                and self.active_stage.endswith("_begin")
            ):
                active_base = self.active_stage[: -len("_begin")]
                completed_base = _COMPLETION_STAGE_ALIASES.get(
                    operation,
                    (
                        operation.rsplit("_", 1)[0]
                        if operation.endswith(("_complete", "_completed"))
                        else None
                    ),
                )
                matches_active = active_base == completed_base
            if not matches_active:
                raise TraceContractError(
                    "trace stage end does not match the active stage"
                )
            self.active_stage = None
            self.last_completed_stage = operation
        elif phase == "status":
            pass
        elif phase in {"error", "cancel"}:
            self.active_stage = operation
        else:
            raise TraceContractError("trace stage phase is invalid")
        result = self._emit(operation, phase, elapsed_ms=elapsed_ms, at=at, **fields)
        if result.status == "suppressed":
            self._set_health("degraded")
        elif result.status == "unavailable":
            self._set_health("unavailable")
        return result

    def fail(
        self,
        error: BaseException,
        *,
        error_code: str,
        failed_operation: str | None = None,
        cleanup_error: BaseException | None = None,
        cleanup_error_code: str | None = None,
        retryable: bool | None = None,
        scope: str | None = None,
    ) -> TraceWriteResult:
        if not isinstance(error, BaseException):
            raise TraceContractError("primary failure must be an exception")
        operation = failed_operation or self.active_stage or "unknown_operation"
        if self.active_stage is None:
            self.active_stage = operation
        cause = error.__cause__ or error.__context__
        cause_code = _optional_safe_code(getattr(cause, "code", None))
        cleanup_code = _optional_safe_code(cleanup_error_code)
        return self.record_stage(
            operation,
            phase="error",
            event_kind="stage_failure",
            primary_error_code=error_code,
            error_class=_safe_exception_class(error),
            cause_code=cause_code,
            cause_class=_safe_exception_class(cause),
            failed_operation=operation,
            cleanup_error_code=cleanup_code,
            cleanup_error_class=_safe_exception_class(cleanup_error),
            retryable=retryable,
            scope=scope,
        )

    def cancel(
        self,
        *,
        cancel_code: str = "cancelled",
        failed_operation: str | None = None,
        scope: str = "request",
    ) -> TraceWriteResult:
        operation = failed_operation or self.active_stage or "unknown_operation"
        if self.active_stage is None:
            self.active_stage = operation
        return self.record_stage(
            operation,
            phase="cancel",
            event_kind="stage_cancelled",
            primary_error_code=cancel_code,
            failed_operation=operation,
            scope=scope,
        )

    def finish(self, *, operation: str | None = None) -> TraceWriteResult:
        selected = operation or self.active_stage or "request_complete"
        if self.active_stage == selected:
            self.active_stage = None
            self.last_completed_stage = selected
        elif self.active_stage is not None:
            raise TraceContractError(
                "trace session ended with an unrelated active stage"
            )
        return self._emit(selected, "end", event_kind="stage")

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "job_id": self.job_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "active_stage": self.active_stage,
            "last_completed_stage": self.last_completed_stage,
            "remaining_ms": self._remaining_ms(),
            "health": self.health,
        }

    def _emit(
        self,
        operation: str,
        phase: str,
        *,
        event_kind: str = "stage",
        elapsed_ms: int | None = None,
        at: str | None = None,
        **fields: Any,
    ) -> TraceWriteResult:
        now = self._now()
        if at is not None:
            at_value = _aware_iso(at, name="at")
        else:
            at_value = _aware_iso(now, name="at")
        if elapsed_ms is None:
            start = self._stage_started.get(operation, self._started_monotonic)
            elapsed_ms = (
                max(0, round((self._monotonic() - start) * 1000))
                if start is not None
                else 0
            )
        context = {
            "request_id": self.request_id,
            "job_id": self.job_id,
            "root_job_id": self.root_job_id,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "attempt_limit": self.attempt_limit,
            "deadline_at": self.deadline_at,
            "remaining_ms": self._remaining_ms(at=at_value),
            "elapsed_ms": elapsed_ms,
            "active_stage": self.active_stage,
            "last_completed_stage": self.last_completed_stage,
            "correlation_status": self._correlation_status(),
            "event_kind": event_kind,
            **fields,
        }
        event = make_trace_event(
            run_id=self.run_id,
            target_date=self.target_date,
            component=self.component,
            operation=operation,
            phase=phase,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            clock=_FixedClock(now),
            **context,
        )
        event["at"] = at_value
        event["retention_expires_at"] = retention_expiry("raw", at_value).isoformat(
            timespec="milliseconds"
        )
        result = self.writer.write(event)
        if result.status == "suppressed":
            self._set_health("degraded")
        elif result.status == "unavailable":
            self._set_health("unavailable")
        return result

    def _remaining_ms(self, *, at: str | None = None) -> int | None:
        if self.deadline_at is None:
            return None
        try:
            now = datetime.fromisoformat(at) if at is not None else self._now()
            deadline = datetime.fromisoformat(self.deadline_at)
            return max(0, round((deadline - now).total_seconds() * 1000))
        except (TypeError, ValueError):
            return None

    def _now(self) -> datetime:
        return self.clock.now_jst() if self.clock is not None else datetime.now(JST)

    def _monotonic(self) -> float:
        return (
            float(self.clock.monotonic())
            if self.clock is not None
            else time.monotonic()
        )

    def _correlation_status(self) -> str:
        present = sum(
            value is not None for value in (self.request_id, self.job_id, self.task_id)
        )
        if not present:
            return "unresolved"
        return "complete" if present == 3 else "partial"

    def _set_health(self, value: str) -> None:
        if _HEALTH_ORDER[value] > _HEALTH_ORDER[self._health]:
            self._health = value

    def mark_unavailable(self) -> None:
        self._set_health("unavailable")
        self.writer.mark_unavailable()

    def mark_degraded(self) -> None:
        self._set_health("degraded")
        self.writer.mark_degraded()


@dataclass(frozen=True, slots=True)
class _FixedClock:
    value: datetime

    def now_jst(self) -> datetime:
        return self.value


def _validate_trace_event(
    event: Mapping[str, Any], run_id: str, target_date: str
) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise TraceContractError("trace event must be an object")
    required = {
        "schema_version",
        "event_id",
        "span_id",
        "component",
        "operation",
        "phase",
        "at",
        "elapsed_ms",
        "run_id",
        "target_date",
        "correlation_status",
        "retention_expires_at",
    }
    if not required.issubset(event) or set(event) - (
        required | _EVENT_FIELDS | {"parent_span_id"}
    ):
        raise TraceContractError("trace event fields do not match the common schema")
    if (
        type(event["schema_version"]) is not int
        or event["schema_version"] != TRACE_SCHEMA_VERSION
    ):
        raise TraceContractError("trace schema version is unsupported")
    _safe_id(event["run_id"], name="run_id")
    if event["run_id"] != run_id or event["target_date"] != target_date:
        raise TraceContractError("trace event identity differs from the writer")
    event_id = event["event_id"]
    try:
        uuid.UUID(str(event_id))
        uuid.UUID(str(event["span_id"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TraceContractError("trace event IDs must be UUIDs") from exc
    if "parent_span_id" in event:
        try:
            uuid.UUID(str(event["parent_span_id"]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise TraceContractError("parent span ID must be a UUID") from exc
    at = _aware_iso(event["at"], name="at")
    expires = _aware_iso(event["retention_expires_at"], name="retention_expires_at")
    expected_expiry = retention_expiry("raw", at)
    if datetime.fromisoformat(expires) != expected_expiry:
        raise TraceContractError("trace expiry does not match the fixed raw retention")
    optional = {key: value for key, value in event.items() if key in _EVENT_FIELDS}
    normalized = make_trace_event(
        run_id=run_id,
        target_date=target_date,
        component=event["component"],
        operation=event["operation"],
        phase=event["phase"],
        clock=_FixedClock(datetime.fromisoformat(at)),
        span_id=str(event["span_id"]),
        parent_span_id=event.get("parent_span_id"),
        **optional,
    )
    normalized["event_id"] = str(event_id)
    normalized["at"] = at
    normalized["retention_expires_at"] = expires
    return normalized


def _suppression_event(
    event: Mapping[str, Any], reason: str, clock: Any | None
) -> dict[str, Any]:
    return make_trace_event(
        run_id=str(event["run_id"]),
        target_date=str(event["target_date"]),
        component="trace_writer",
        operation="trace_suppressed",
        phase="status",
        clock=clock,
        span_id=str(event["span_id"]),
        event_kind="trace_suppressed",
        suppression_reason=reason,
        suppression_marker_count=1,
    )


def _optional_safe_code(value: object) -> str | None:
    if not isinstance(value, str) or not _SAFE_REF.fullmatch(value):
        return None
    return value


def _safe_failure_reason(exc: BaseException) -> str:
    if isinstance(exc, OSError):
        return "trace_io_unavailable"
    if isinstance(exc, TraceContractError):
        return "trace_contract_rejected"
    return "trace_write_unavailable"


def _trace_paths(trace_root: Path, *, limit: int) -> list[Path]:
    if not trace_root.exists():
        return []
    result: list[Path] = []
    stack = [trace_root]
    while stack:
        directory = stack.pop()
        if _reparse(directory):
            raise TraceContractError("trace directory contains a reparse point")
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if _reparse(path):
                    raise TraceContractError("trace tree contains a reparse point")
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False) and path.suffix == ".jsonl":
                    result.append(path)
                    if len(result) > limit:
                        raise TraceContractError(
                            "trace file tree exceeds its file quota"
                        )
    return result


def read_trace_events(
    path: str | Path,
    *,
    policy: TracePolicy = DEFAULT_TRACE_POLICY,
) -> list[dict[str, Any]]:
    """Read a private bounded trace file without exposing it to general logs."""

    selected = Path(path)
    raw = _read_bounded(selected, policy.max_bytes_per_run)
    result = []
    run_id: str | None = None
    target_date: str | None = None
    for line in _trace_json_lines(raw, policy.max_event_bytes):
        value = json.loads(line.decode("utf-8", errors="strict"))
        if not isinstance(value, dict):
            raise TraceContractError("trace event is not an object")
        current_run = value.get("run_id")
        current_date = value.get("target_date")
        if run_id is None:
            run_id, target_date = current_run, current_date
        if current_run != run_id or current_date != target_date:
            raise TraceContractError("trace file contains mixed run identities")
        result.append(_validate_trace_event(value, run_id, target_date))
        if len(result) > policy.max_events_per_run:
            raise TraceContractError("trace file exceeds the event limit")
    return result


def expire_trace_events(
    data_root: str | Path,
    *,
    now: str | datetime,
    policy: TracePolicy = DEFAULT_TRACE_POLICY,
) -> TraceRetentionResult:
    """Remove expired raw trace events while preserving current and compact state."""

    current = (
        datetime.fromisoformat(now.replace("Z", "+00:00"))
        if isinstance(now, str)
        else now
    )
    if current.tzinfo is None or current.utcoffset() is None:
        raise TraceContractError("retention time must include a timezone")
    current = current.astimezone(JST)
    root = validate_data_root(data_root)
    trace_root = root / "logs" / "traces"
    try:
        _assert_safe_descendant(root, trace_root, allow_missing=True)
    except (OSError, TraceContractError, RetentionError):
        return TraceRetentionResult(0, 0, 0, 1)
    if not trace_root.exists():
        return TraceRetentionResult(0, 0, 0, 0)
    try:
        with _thread_lock(trace_root), _process_root_lock(root):
            return _expire_trace_events_locked(trace_root, root, current, policy)
    except (OSError, TraceContractError, RetentionError, ValueError):
        return TraceRetentionResult(0, 0, 0, 1)


def _expire_trace_events_locked(
    trace_root: Path,
    root: Path,
    current: datetime,
    policy: TracePolicy,
) -> TraceRetentionResult:
    try:
        _assert_safe_descendant(root, trace_root, allow_missing=False)
        paths = _trace_paths(trace_root, limit=policy.max_files_per_root)
    except (OSError, TraceContractError, RetentionError):
        return TraceRetentionResult(0, 0, 0, 1)
    expired_events = removed_files = rewritten_files = failures = 0
    for path in paths:
        try:
            _assert_safe_descendant(root, path, allow_missing=False)
            try:
                raw = _read_bounded(path, policy.max_bytes_per_run)
            except TraceContractError as exc:
                if "byte limit" in str(exc):
                    failures += 1
                    continue
                raise
            if len(raw) > policy.max_bytes_per_run:
                failures += 1
                continue
            kept: list[bytes] = []
            removed = 0
            file_run_id: str | None = None
            file_target_date: str | None = None
            for payload in _trace_json_lines(raw, policy.max_event_bytes):
                line = payload + b"\n"
                record = json.loads(payload.decode("utf-8", errors="strict"))
                if not isinstance(record, dict):
                    raise TraceContractError("trace event is not an object")
                record_run_id = record.get("run_id")
                record_target_date = record.get("target_date")
                if file_run_id is None:
                    file_run_id = record_run_id
                    file_target_date = record_target_date
                if (
                    record_run_id != file_run_id
                    or record_target_date != file_target_date
                ):
                    raise TraceContractError("trace file contains mixed run identities")
                _validate_trace_event(record, file_run_id, file_target_date)
                expiry = _aware_iso(
                    record.get("retention_expires_at"), name="retention_expires_at"
                )
                expiry_time = datetime.fromisoformat(expiry)
                if expiry_time <= current:
                    removed += 1
                else:
                    kept.append(line)
                if removed + len(kept) > policy.max_events_per_run:
                    raise TraceContractError("trace file exceeds the event limit")
            if not removed:
                continue
            if not kept:
                path.unlink()
                expired_events += removed
                removed_files += 1
                continue
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            _assert_safe_descendant(root, temporary, allow_missing=True)
            descriptor = -1
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                descriptor = os.open(temporary, flags, 0o600)
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    descriptor = -1
                    stream.writelines(kept)
                    stream.flush()
                    os.fsync(stream.fileno())
                _assert_safe_descendant(root, path, allow_missing=False)
                os.replace(temporary, path)
                expired_events += removed
                rewritten_files += 1
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    failures += 1
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TraceContractError,
            ValueError,
        ):
            failures += 1
    return TraceRetentionResult(
        expired_events, removed_files, rewritten_files, failures
    )
