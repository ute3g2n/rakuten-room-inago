"""Independent, privacy-safe fallback journal for maintenance operations.

The journal deliberately avoids application loggers and database connections so
it remains usable when either primary sink is the source of a failure.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import traceback
import uuid
from typing import Iterator, Mapping

from automation.inago.maintenance.paths import (
    resolve_maintenance_failure_journal_path,
)
from automation.inago.maintenance.code_revision import (
    current_code_revision as _current_invocation_code_revision,
    current_code_revision_reason,
)
from automation.inago.maintenance.settings import FAILURE_JOURNAL_FILENAME


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_SAFE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)
_CURRENT_CORRELATION: ContextVar[str | None] = ContextVar(
    "maintenance_operation_correlation", default=None
)
_CURRENT_DATA_ROOT: ContextVar[str | Path | None] = ContextVar(
    "maintenance_operation_data_root", default=None
)


@contextmanager
def bind_operation_correlation(
    correlation_id: str, data_root: str | Path | None = None
) -> Iterator[None]:
    token: Token[str | None] = _CURRENT_CORRELATION.set(correlation_id)
    root_token: Token[str | Path | None] = _CURRENT_DATA_ROOT.set(data_root)
    try:
        yield
    finally:
        _CURRENT_DATA_ROOT.reset(root_token)
        _CURRENT_CORRELATION.reset(token)


def record_current_operation_failure(operation: str, error: BaseException) -> None:
    root = _CURRENT_DATA_ROOT.get()
    if root is None:
        return
    append_failure_event(
        root,
        phase="failed",
        operation=operation,
        correlation_id=_CURRENT_CORRELATION.get(),
        reason_code="safe_file_operation_failed",
        error=error,
        capture_location=f"{operation}",
        primary_sink="safe_file",
    )


def current_code_revision() -> str:
    """Return the immutable invocation fingerprint, never a helper-file hash."""

    return _current_invocation_code_revision()


def _safe_id(value: object, limit: int = 128) -> str | None:
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    if any(character not in _SAFE_ID_CHARS for character in value):
        return None
    return value


_SENSITIVE_REFERENCE_MARKERS = (
    "secret",
    "password",
    "credential",
    "cookie",
    "token",
    "api-key",
    "api_key",
    "apikey",
    "access-key",
    "access_key",
    "accesskey",
    "private-key",
    "private_key",
    "privatekey",
    "id_rsa",
    "id_ed25519",
    "authorized_keys",
    ".env",
)


def _safe_reference(value: object, limit: int = 192) -> str | None:
    """Preserve IDs or safe private-root-relative artifact refs, never raw paths."""

    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    folded = value.casefold()
    if any(marker in folded for marker in _SENSITIVE_REFERENCE_MARKERS):
        return None
    safe_id = _safe_id(value, limit)
    if safe_id is not None:
        return safe_id
    # References are display-only. Accept a conservative POSIX relative form;
    # do not normalize an unsafe path into an apparently safe one.
    if value.startswith(("/", "\\")) or "\\" in value or ":" in value:
        return None
    components = value.split("/")
    if len(components) > 16 or any(part in ("", ".", "..") for part in components):
        return None
    if any(
        not all(character.isalnum() or character in "._-" for character in part)
        for part in components
    ):
        return None
    return value


def _safe_references(values: tuple[str, ...], limit: int = 16) -> list[str]:
    refs: list[str] = []
    for item in values[:limit]:
        safe = _safe_reference(item)
        if safe is not None:
            refs.append(safe)
    return refs


def _exception_details(error: BaseException | None) -> tuple[list[str], list[dict[str, object]]]:
    classes: list[str] = []
    seen: set[int] = set()
    current = error
    while current is not None and id(current) not in seen and len(classes) < 8:
        seen.add(id(current))
        classes.append(type(current).__name__[:64])
        current = current.__cause__ or current.__context__
    frames: list[dict[str, object]] = []
    if error is not None and error.__traceback__ is not None:
        for frame in traceback.extract_tb(error.__traceback__)[-24:]:
            frames.append(
                {
                    "file": Path(frame.filename).name[:128],
                    "line": frame.lineno,
                    "function": frame.name[:128],
                }
            )
    return classes, frames


def _exception_errno(error: BaseException | None) -> int | None:
    """Return the first safe numeric errno in the exception/cause chain."""

    seen: set[int] = set()
    current = error
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        value = getattr(current, "errno", None)
        if type(value) is int and 0 <= value <= 65535:
            return value
        current = current.__cause__ or current.__context__
    return None


def _lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.absolute()))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def append_failure_event(
    data_root: str | Path,
    *,
    phase: str,
    operation: str,
    correlation_id: str | None = None,
    parent_operation: str | None = None,
    job_id: str | None = None,
    source_launch_id: str | None = None,
    source_run_id: str | None = None,
    incident_id: str | None = None,
    invocation_id: str | None = None,
    stage: str | None = None,
    attempt_id: str | None = None,
    reason_code: str | None = None,
    error: BaseException | None = None,
    capture_location: str | None = None,
    input_refs: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    primary_sink: str | None = None,
) -> Mapping[str, object]:
    """Append one event; only identifiers, exception classes and code frames persist."""

    classes, frames = _exception_details(error)
    event: dict[str, object] = {
        "schema_version": 1,
        "event_id": uuid.uuid4().hex,
        "correlation_id": (
            _safe_id(correlation_id)
            or _safe_id(_CURRENT_CORRELATION.get())
            or uuid.uuid4().hex
        ),
        "parent_operation": _safe_id(parent_operation),
        "job_id": _safe_id(job_id),
        "source_launch_id": _safe_id(source_launch_id),
        "source_run_id": _safe_id(source_run_id),
        "incident_id": _safe_id(incident_id),
        "invocation_id": _safe_id(invocation_id),
        "stage": _safe_id(stage, 64),
        "attempt_id": _safe_id(attempt_id),
        "operation": _safe_id(operation, 128) or "unknown_operation",
        "phase": _safe_id(phase, 32) or "unknown",
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "code_revision": current_code_revision(),
        "code_revision_reason": current_code_revision_reason() or None,
        "reason_code": _safe_id(reason_code, 128),
        "exception_chain": classes,
        "exception_errno": _exception_errno(error),
        "stack_frames": frames,
        "capture_location": _safe_id(capture_location, 192),
        "input_refs": _safe_references(input_refs),
        "artifact_refs": _safe_references(artifact_refs),
        "primary_sink": _safe_id(primary_sink, 32),
        "process_id": os.getpid(),
    }
    encoded = (json.dumps(event, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    try:
        path = resolve_maintenance_failure_journal_path(
            data_root, filename=FAILURE_JOURNAL_FILENAME
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path = resolve_maintenance_failure_journal_path(
            data_root, filename=FAILURE_JOURNAL_FILENAME
        )
        with _lock_for(path):
            flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_BINARY", 0)
            descriptor = os.open(path, flags, 0o600)
            try:
                view = memoryview(encoded)
                while view:
                    count = os.write(descriptor, view)
                    if count <= 0:
                        raise OSError("journal append made no progress")
                    view = view[count:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return {"status": "written", "path": path.as_posix(), "event_id": event["event_id"]}
    except (OSError, RuntimeError, ValueError) as exc:
        # Never recurse through a logger. A short stderr marker is the last
        # available fallback; it intentionally contains no user data.
        try:
            os.write(2, b"maintenance_failure_journal_unavailable\n")
        except OSError:
            pass
        return {
            "status": "unavailable",
            "exception_class": type(exc).__name__,
            "capture_location": "failure_journal.append_failure_event",
        }


@contextmanager
def tracked_operation(data_root: str | Path, **identity: object) -> Iterator[str]:
    """Write correlated begin/end records without making either write a gate."""

    operation = str(identity.get("operation") or "maintenance_operation")
    correlation_id = str(identity.get("correlation_id") or uuid.uuid4().hex)
    common = dict(identity)
    common.pop("operation", None)
    common["correlation_id"] = correlation_id
    append_failure_event(data_root, phase="begin", operation=operation, **common)
    try:
        yield correlation_id
    except BaseException as exc:
        append_failure_event(
            data_root,
            phase="failed",
            operation=operation,
            error=exc,
            capture_location="failure_journal.tracked_operation",
            **common,
        )
        raise
    else:
        append_failure_event(data_root, phase="end", operation=operation, **common)


def reconcile_open_operations(data_root: str | Path) -> Mapping[str, object]:
    """Append interruption observations for begin events without a terminal event."""

    try:
        path = resolve_maintenance_failure_journal_path(
            data_root, filename=FAILURE_JOURNAL_FILENAME
        )
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except FileNotFoundError:
        return {"status": "complete", "reconciled": 0, "unknown": 0}
    except (OSError, RuntimeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "unknown", "reconciled": 0, "unknown": 1, "exception_class": type(exc).__name__}
    latest: dict[str, dict[str, object]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("correlation_id"), str):
            latest[row["correlation_id"]] = row
    open_rows = [row for row in latest.values() if row.get("phase") == "begin"]
    for row in open_rows:
        append_failure_event(
            data_root,
            phase="interruption_observed",
            operation=str(row.get("operation") or "unknown_operation"),
            correlation_id=str(row.get("correlation_id")),
            parent_operation=row.get("parent_operation"),
            job_id=row.get("job_id"),
            source_launch_id=row.get("source_launch_id"),
            source_run_id=row.get("source_run_id"),
            incident_id=row.get("incident_id"),
            invocation_id=row.get("invocation_id"),
            stage=row.get("stage"),
            attempt_id=row.get("attempt_id"),
            reason_code="process_interruption_observed",
            capture_location="failure_journal.reconcile_open_operations",
            primary_sink="fallback_spool",
        )
    return {"status": "complete", "reconciled": len(open_rows), "unknown": 0}


def replay_failure_journal(data_root: str | Path, store: object) -> Mapping[str, object]:
    """Copy fallback events into the primary append-only ledger after recovery."""

    try:
        path = resolve_maintenance_failure_journal_path(
            data_root, filename=FAILURE_JOURNAL_FILENAME
        )
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except FileNotFoundError:
        return {"status": "complete", "replayed": 0, "unknown": 0}
    except (OSError, RuntimeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "unknown", "replayed": 0, "unknown": 1, "exception_class": type(exc).__name__}
    replayed = 0
    failures = 0
    record = getattr(store, "record_operation_event", None)
    if not callable(record):
        return {"status": "unknown", "replayed": 0, "unknown": 1}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("event_id"), str):
            failures += 1
            continue
        event = dict(row)
        event.setdefault("exception_chain", [])
        event.setdefault("stack_frames", [])
        event.setdefault("input_refs", [])
        event.setdefault("artifact_refs", [])
        event["fallback_status"] = "replayed_from_fallback"
        try:
            record(event)
        except Exception as exc:
            failures += 1
            append_failure_event(
                data_root,
                phase="failed",
                operation="failure_journal.replay_store_event",
                correlation_id=_safe_id(row.get("correlation_id")),
                parent_operation=_safe_id(row.get("operation")),
                job_id=_safe_id(row.get("job_id")),
                source_launch_id=_safe_id(row.get("source_launch_id")),
                source_run_id=_safe_id(row.get("source_run_id")),
                incident_id=_safe_id(row.get("incident_id")),
                invocation_id=_safe_id(row.get("invocation_id")),
                stage=_safe_id(row.get("stage"), 64),
                attempt_id=_safe_id(row.get("attempt_id")),
                reason_code="failure_journal_replay_failed",
                error=exc,
                capture_location="failure_journal.replay_failure_journal.store_record_operation_event",
                input_refs=(f"event:{row['event_id']}",),
                primary_sink="maintenance_operation_event",
            )
        else:
            replayed += 1
    return {
        "status": "complete" if failures == 0 else "partial",
        "replayed": replayed,
        "unknown": failures,
    }
