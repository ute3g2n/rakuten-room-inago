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
    _has_reparse_point,
    resolve_maintenance_database_path,
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


_PROVIDER_ERROR_SENSITIVE_MARKERS = (
    "secret",
    "password",
    "credential",
    "cookie",
    "token",
    "apikey",
    "api_key",
    "privatekey",
    "private_key",
)


def _safe_provider_error_identifier(value: object) -> str | None:
    safe = _safe_id(value, 128)
    if safe is None:
        return None
    folded = safe.casefold()
    if any(marker in folded for marker in _PROVIDER_ERROR_SENSITIVE_MARKERS):
        return None
    return safe


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


def _secondary_journal_path(data_root: str | Path) -> Path:
    """Resolve the secondary spool directly from the validated DB root."""

    database_path = resolve_maintenance_database_path(data_root)
    maintenance_root = database_path.parent
    # Keep the emergency spool in a sibling directory so a damaged or
    # unavailable primary directory cannot take both sinks down together.
    fallback = maintenance_root / "failure-journal-secondary" / FAILURE_JOURNAL_FILENAME
    if any(_has_reparse_point(candidate) for candidate in (fallback.parent, fallback)):
        raise ValueError("failure_journal_secondary_reparse_point")
    try:
        resolved = fallback.resolve(strict=False)
        resolved.relative_to(maintenance_root.resolve(strict=False))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("failure_journal_secondary_path_invalid") from exc
    return resolved


def _append_encoded(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _has_reparse_point(path.parent) or _has_reparse_point(path):
        raise ValueError("failure_journal_reparse_point")
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


def _encoded_event(event: Mapping[str, object]) -> bytes:
    return (
        json.dumps(event, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _read_event_sources(
    data_root: str | Path,
) -> tuple[list[tuple[dict[str, object], str]], list[dict[str, object]]]:
    """Read both append-only sources, retaining valid rows if one source fails."""

    rows: list[tuple[dict[str, object], str]] = []
    errors: list[dict[str, object]] = []
    sources: list[tuple[str, Path]] = []
    try:
        sources.append(("fallback", _secondary_journal_path(data_root)))
    except Exception as exc:
        errors.append(
            {
                "source": "fallback_path_resolution",
                "exception_class": type(exc).__name__,
                "exception_errno": _exception_errno(exc),
            }
        )
    try:
        sources.append(
            (
                "primary",
                resolve_maintenance_failure_journal_path(
                    data_root, filename=FAILURE_JOURNAL_FILENAME
                ),
            )
        )
    except Exception as exc:
        errors.append(
            {
                "source": "primary_path_resolution",
                "exception_class": type(exc).__name__,
                "exception_errno": _exception_errno(exc),
            }
        )
    # Read order is secondary first so recovery stays useful even when
    # validation of the original journal path fails.
    for source, path in sources:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        except Exception as exc:
            errors.append(
                {
                    "source": source,
                    "exception_class": type(exc).__name__,
                    "exception_errno": _exception_errno(exc),
                }
            )
            continue
        for line_number, line in enumerate(lines, start=1):
            if not line:
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError) as exc:
                errors.append(
                    {
                        "source": source,
                        "line": line_number,
                        "exception_class": type(exc).__name__,
                        "exception_errno": _exception_errno(exc),
                    }
                )
                continue
            if isinstance(row, dict):
                rows.append((row, source))
            else:
                errors.append(
                    {
                        "source": source,
                        "line": line_number,
                        "exception_class": "JournalRowNotObject",
                        "exception_errno": None,
                    }
                )
    return rows, errors


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
    provider_error_type: object = None,
    provider_error_code: object = None,
    provider_error_param: object = None,
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
        "provider_error_type": _safe_provider_error_identifier(provider_error_type),
        "provider_error_code": _safe_provider_error_identifier(provider_error_code),
        "provider_error_param": _safe_provider_error_identifier(provider_error_param),
        "exception_chain": classes,
        "exception_errno": _exception_errno(error),
        "stack_frames": frames,
        "capture_location": _safe_id(capture_location, 192),
        "input_refs": _safe_references(input_refs),
        "artifact_refs": _safe_references(artifact_refs),
        "primary_sink": _safe_id(primary_sink, 32),
        "process_id": os.getpid(),
    }
    try:
        primary = resolve_maintenance_failure_journal_path(
            data_root, filename=FAILURE_JOURNAL_FILENAME
        )
        _append_encoded(primary, _encoded_event(event))
        return {
            "status": "written",
            "path": primary.as_posix(),
            "event_id": event["event_id"],
            "primary_status": "written",
            "fallback_status": "not_needed",
        }
    except Exception as primary_exc:
        classes, frames = _exception_details(primary_exc)
        event["primary_sink_failure"] = {
            "exception_chain": classes,
            "errno": _exception_errno(primary_exc),
            "capture_location": "failure_journal.append_failure_event.primary_write",
            "stack_frames": frames,
        }
        event["fallback_status"] = "written_to_secondary_spool"
        try:
            fallback = _secondary_journal_path(data_root)
            _append_encoded(fallback, _encoded_event(event))
            return {
                "status": "written",
                "path": fallback.as_posix(),
                "event_id": event["event_id"],
                "primary_status": "unavailable",
                "fallback_status": "written",
                "primary_exception_class": type(primary_exc).__name__,
                "primary_exception_errno": _exception_errno(primary_exc),
            }
        except Exception as fallback_exc:
            fallback_classes, fallback_frames = _exception_details(fallback_exc)
            primary_classes, primary_frames = _exception_details(primary_exc)
            try:
                os.write(2, b"maintenance_failure_journal_both_sinks_unavailable\n")
            except Exception:
                pass
            return {
                "status": "unavailable",
                "primary_status": "unavailable",
                "fallback_status": "unavailable",
                "primary_exception_class": type(primary_exc).__name__,
                "primary_exception_errno": _exception_errno(primary_exc),
                "primary_exception_chain": primary_classes,
                "primary_stack_frames": primary_frames,
                "fallback_exception_class": type(fallback_exc).__name__,
                "fallback_exception_errno": _exception_errno(fallback_exc),
                "fallback_exception_chain": fallback_classes,
                "fallback_stack_frames": fallback_frames,
                "capture_location": "failure_journal.append_failure_event.secondary_write",
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

    rows, errors = _read_event_sources(data_root)
    latest: dict[str, tuple[int, dict[str, object]]] = {}
    ordered = sorted(
        enumerate(rows),
        key=lambda item: (str(item[1][0].get("observed_at") or ""), item[0]),
    )
    for sequence, (row, _source) in ordered:
        if isinstance(row, dict) and isinstance(row.get("correlation_id"), str):
            latest[row["correlation_id"]] = (sequence, row)
    open_rows = [item[1] for item in latest.values() if item[1].get("phase") == "begin"]
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
    return {
        "status": "complete" if not errors else "partial",
        "reconciled": len(open_rows),
        "unknown": len(errors),
        "source_errors": errors,
    }


def replay_failure_journal(data_root: str | Path, store: object) -> Mapping[str, object]:
    """Copy fallback events into the primary append-only ledger after recovery."""

    rows, errors = _read_event_sources(data_root)
    replayed = 0
    failures = len(errors)
    record = getattr(store, "record_operation_event", None)
    if not callable(record):
        return {"status": "unknown", "replayed": 0, "unknown": 1}
    seen_event_ids: set[str] = set()
    for row, _source in rows:
        if not isinstance(row, dict) or not isinstance(row.get("event_id"), str):
            failures += 1
            continue
        event_id = row["event_id"]
        if event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        event = dict(row)
        event.setdefault("exception_chain", [])
        event.setdefault("stack_frames", [])
        event.setdefault("input_refs", [])
        event.setdefault("artifact_refs", [])
        # Both JSONL files are fallback sources to the maintenance DB ledger.
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
        "source_errors": errors,
    }
