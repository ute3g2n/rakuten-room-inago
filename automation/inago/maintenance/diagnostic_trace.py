"""Bounded MN04 diagnostics for maintenance workflows, separate from daily L02."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache, wraps
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading
import time
import uuid
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker

from automation.inago.maintenance.paths import _has_reparse_point
from automation.inago.maintenance.safe_file import (
    SafeFileError,
    open_file_beneath,
    unlink_file_beneath,
)
from automation.inago.retention import RetentionError, validate_data_root


JST = ZoneInfo("Asia/Tokyo")
DIAGNOSTIC_SCHEMA_VERSION = 1
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_FILE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_FILES_PER_DAY = 20
DEFAULT_MAX_EVENT_BYTES = 16 * 1024
ORPHAN_STAGE_MIN_AGE = timedelta(days=1)

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_CLASS = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_SAFE_SHA = re.compile(r"[a-fA-F0-9]{7,64}\Z")
_GITHUB_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,})(?![A-Za-z0-9_])"
)
_STAGES = {
    "cli",
    "database",
    "daily_check",
    "incident",
    "investigation",
    "planning",
    "repair",
    "test",
    "review",
    "report",
    "deployment",
    "watchdog",
    "worker",
    "model",
    "retention",
    "evidence",
}
_EVENTS = {
    "maintenance_started",
    "maintenance_finished",
    "stage_started",
    "stage_completed",
    "stage_failed",
    "stage_waiting",
    "stage_cancelled",
    "stage_resumed",
    "model_requested",
    "model_observed",
    "review_completed",
    "test_completed",
    "deployment_result",
    "retention_completed",
    "retention_failed",
}
_STATUSES = {
    "started",
    "complete",
    "failed",
    "blocked",
    "waiting",
    "cancelled",
    "resumed",
    "unverified",
    "approved",
    "changes_requested",
    "passed",
    "not_run",
    "ready",
    "not_ready",
    "busy",
    "unknown",
}
_MODELS = {
    "gpt-6.1-sol",
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
}
_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
_SERVICE_TIERS = {"normal", "default", "fast", "priority"}
_REVIEW_STATUSES = {"not_run", "approved", "changes_requested", "blocked", "unknown"}
_TEST_STATUSES = {"not_run", "passed", "failed", "blocked", "unknown"}
_IDENTITY_FIELDS = {
    "source_launch_id",
    "source_run_id",
    "incident_id",
    "job_id",
    "attempt_id",
    "step_key",
    "release_id",
    "session_key",
    "parent_key",
    "role_name",
    "artifact_ref",
}
_EVENT_VALUE_FIELDS = {
    "reason_code",
    "error_class",
    "model",
    "effort",
    "service_tier",
    "fast_mode",
    "review_status",
    "test_status",
    "base_commit",
    "result_version",
    "duration_ms",
    "count",
}
_ALLOWED_FIELDS = _IDENTITY_FIELDS | _EVENT_VALUE_FIELDS
_LOG_NAME = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}(?:\.part-[0-9]{4})?\.jsonl\Z"
)
_LOG_STAGE = re.compile(r"\.maintenance-log-stage-[a-fA-F0-9]{32}\.tmp\Z")
_ROTATION_SUFFIX = re.compile(r"\.part-[0-9]{4}\Z")
_DATE_DIR = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_EVIDENCE_STAGE = re.compile(r"\.maintenance-evidence-stage-[a-fA-F0-9]{32}\Z")
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


class MaintenanceDiagnosticError(ValueError):
    """A safe MN04 validation or persistence error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MaintenanceAuditReceiptError(MaintenanceDiagnosticError):
    """A receipt failed after an operation returned or raised."""

    def __init__(
        self,
        code: str,
        *,
        operation_returned: bool,
        operation_result: Any | None = None,
    ) -> None:
        self.operation_returned = operation_returned
        self.operation_result = operation_result
        self.state_reconciliation_required = True
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MaintenanceDiagnosticPolicy:
    retention_days: int = DEFAULT_RETENTION_DAYS
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_files_per_day: int = DEFAULT_MAX_FILES_PER_DAY
    max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES

    def __post_init__(self) -> None:
        values = (
            self.retention_days,
            self.max_file_bytes,
            self.max_files_per_day,
            self.max_event_bytes,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise MaintenanceDiagnosticError("diagnostic_policy_invalid")
        if self.max_file_bytes < 1024 or self.max_event_bytes > self.max_file_bytes:
            raise MaintenanceDiagnosticError("diagnostic_policy_invalid")


@dataclass(frozen=True, slots=True)
class DiagnosticWriteResult:
    status: str
    reason_code: str | None = None
    bytes_written: int = 0
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticRetentionResult:
    removed_files: int
    failures: int

    @property
    def status(self) -> str:
        return "degraded" if self.failures else "complete"


@dataclass(frozen=True, slots=True)
class OrphanRecoveryResult:
    removed_stages: int
    failures: int

    @property
    def status(self) -> str:
        return "degraded" if self.failures else "complete"


@dataclass(frozen=True, slots=True)
class MaintenanceRetentionOutcome:
    expired_log_files: int
    expired_evidence_files: int
    recovered_orphan_stages: int
    failures: int

    @property
    def status(self) -> str:
        return "degraded" if self.failures else "complete"


def _safe_id(value: object, field: str, *, optional: bool = True) -> str | None:
    if value is None and optional:
        return None
    if (
        not isinstance(value, str)
        or not _SAFE_ID.fullmatch(value)
        or _GITHUB_TOKEN.search(value)
    ):
        raise MaintenanceDiagnosticError("diagnostic_identity_invalid")
    return value


def _aware_jst(value: object, field: str) -> datetime:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise MaintenanceDiagnosticError("diagnostic_timestamp_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MaintenanceDiagnosticError("diagnostic_timestamp_invalid")
    return parsed.astimezone(JST)


def _normalize_event(
    *,
    maintenance_id: str,
    event: str,
    stage: str,
    status: str,
    at: datetime,
    retention_days: int,
    fields: Mapping[str, object],
) -> dict[str, Any]:
    if event not in _EVENTS or stage not in _STAGES or status not in _STATUSES:
        raise MaintenanceDiagnosticError("diagnostic_enum_invalid")
    if set(fields) - _ALLOWED_FIELDS:
        raise MaintenanceDiagnosticError("diagnostic_field_not_allowlisted")
    normalized: dict[str, Any] = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "maintenance_id": maintenance_id,
        "target_date": at.date().isoformat(),
        "at": at.isoformat(timespec="milliseconds"),
        "retention_expires_at": (at + timedelta(days=retention_days)).isoformat(
            timespec="milliseconds"
        ),
        "event": event,
        "stage": stage,
        "status": status,
        # The run may not exist yet. Keep that absence explicit in every event.
        "source_run_id": None,
    }
    for field, value in fields.items():
        if field in _IDENTITY_FIELDS:
            normalized[field] = _safe_id(value, field)
        elif field == "reason_code":
            if value is not None and (
                not isinstance(value, str) or not _SAFE_CODE.fullmatch(value)
            ):
                raise MaintenanceDiagnosticError("diagnostic_code_invalid")
            if value is not None:
                normalized[field] = value
        elif field == "error_class":
            if value is not None and (
                not isinstance(value, str) or not _SAFE_CLASS.fullmatch(value)
            ):
                raise MaintenanceDiagnosticError("diagnostic_class_invalid")
            if value is not None:
                normalized[field] = value
        elif field == "model":
            if value is not None and value not in _MODELS:
                raise MaintenanceDiagnosticError("diagnostic_model_invalid")
            if value is not None:
                normalized[field] = value
        elif field == "effort":
            if value is not None and value not in _EFFORTS:
                raise MaintenanceDiagnosticError("diagnostic_effort_invalid")
            if value is not None:
                normalized[field] = value
        elif field == "service_tier":
            if value is not None and value not in _SERVICE_TIERS:
                raise MaintenanceDiagnosticError("diagnostic_service_tier_invalid")
            if value is not None:
                normalized[field] = value
        elif field == "fast_mode":
            if value is not None and type(value) is not bool:
                raise MaintenanceDiagnosticError("diagnostic_boolean_invalid")
            if value is not None:
                normalized[field] = value
        elif field == "review_status":
            if value is not None and value not in _REVIEW_STATUSES:
                raise MaintenanceDiagnosticError("diagnostic_review_status_invalid")
            if value is not None:
                normalized[field] = value
        elif field == "test_status":
            if value is not None and value not in _TEST_STATUSES:
                raise MaintenanceDiagnosticError("diagnostic_test_status_invalid")
            if value is not None:
                normalized[field] = value
        elif field == "base_commit":
            if value is not None and (
                not isinstance(value, str) or not _SAFE_SHA.fullmatch(value)
            ):
                raise MaintenanceDiagnosticError("diagnostic_commit_invalid")
            if value is not None:
                normalized[field] = value.lower()
        elif field == "result_version":
            selected = _safe_id(value, field)
            if selected is not None:
                normalized[field] = selected
        elif field in {"duration_ms", "count"}:
            if value is not None and (
                type(value) is not int or not 0 <= value <= 2**53 - 1
            ):
                raise MaintenanceDiagnosticError("diagnostic_count_invalid")
            if value is not None:
                normalized[field] = value
    return normalized


def _prepare_data_root(data_root: str | Path) -> Path:
    root = Path(data_root).expanduser()
    try:
        validate_data_root(root, allow_missing=True)
        root.mkdir(parents=True, exist_ok=True)
        return validate_data_root(root)
    except (OSError, RetentionError) as exc:
        raise MaintenanceDiagnosticError("diagnostic_root_unavailable") from exc


def _ensure_directory(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if _has_reparse_point(current):
            raise MaintenanceDiagnosticError("diagnostic_path_reparse_point")
        try:
            current.mkdir(exist_ok=True)
        except OSError as exc:
            raise MaintenanceDiagnosticError(
                "diagnostic_directory_unavailable"
            ) from exc
        if not current.is_dir() or _has_reparse_point(current):
            raise MaintenanceDiagnosticError("diagnostic_directory_invalid")
    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MaintenanceDiagnosticError("diagnostic_path_outside_root") from exc
    return current


def _path_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.absolute()))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _safe_log_entries(day_dir: Path) -> list[os.DirEntry[str]]:
    entries: list[os.DirEntry[str]] = []
    if not day_dir.exists():
        return entries
    if _has_reparse_point(day_dir):
        raise MaintenanceDiagnosticError("diagnostic_path_reparse_point")
    try:
        with os.scandir(day_dir) as stream:
            for entry in stream:
                if not _LOG_NAME.fullmatch(entry.name):
                    continue
                path = Path(entry.path)
                if _has_reparse_point(path) or not entry.is_file(follow_symlinks=False):
                    raise MaintenanceDiagnosticError("diagnostic_log_entry_invalid")
                entries.append(entry)
    except OSError as exc:
        raise MaintenanceDiagnosticError("diagnostic_directory_unavailable") from exc
    return entries


def _read_log(path: Path, *, max_bytes: int) -> list[dict[str, Any]]:
    if (
        not _DATE_DIR.fullmatch(path.parent.name)
        or path.parent.parent.name != "logs"
        or path.parent.parent.parent.name != "maintenance"
    ):
        raise MaintenanceDiagnosticError("diagnostic_file_invalid")
    root = path.parents[3]
    relative = path.relative_to(root).as_posix()
    try:
        with open_file_beneath(root, relative, "rb") as stream:
            raw = stream.read(max_bytes + 1)
    except MaintenanceDiagnosticError:
        raise
    except (OSError, SafeFileError) as exc:
        raise MaintenanceDiagnosticError("diagnostic_file_unavailable") from exc
    if len(raw) > max_bytes:
        raise MaintenanceDiagnosticError("diagnostic_file_size_invalid")
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise MaintenanceDiagnosticError("diagnostic_file_incomplete")
    rows: list[dict[str, Any]] = []
    file_stem = path.name.removesuffix(".jsonl")
    rotation = re.fullmatch(r"(?P<base>.+)\.part-[0-9]{4}", file_stem)
    expected_id = rotation.group("base") if rotation else file_stem
    expected_date = path.parent.name
    for line in raw.splitlines():
        try:
            record = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MaintenanceDiagnosticError("diagnostic_file_invalid") from exc
        if (
            not isinstance(record, dict)
            or record.get("schema_version") != DIAGNOSTIC_SCHEMA_VERSION
            or record.get("maintenance_id") != expected_id
            or record.get("target_date") != expected_date
        ):
            raise MaintenanceDiagnosticError("diagnostic_file_invalid")
        if _diagnostic_event_validator().is_valid(record) is False:
            raise MaintenanceDiagnosticError("diagnostic_file_invalid")
        rows.append(record)
    return rows


@lru_cache(maxsize=1)
def _diagnostic_event_validator() -> Draft202012Validator:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "maintenance-diagnostic-event.schema.json"
    )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise MaintenanceDiagnosticError("diagnostic_schema_unavailable") from exc
    return Draft202012Validator(schema, format_checker=FormatChecker())


class MaintenanceDiagnosticTrace:
    """Append allowlisted maintenance workflow events to bounded MN04 JSONL."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        maintenance_id: str | None = None,
        policy: MaintenanceDiagnosticPolicy = MaintenanceDiagnosticPolicy(),
        clock: Any | None = None,
    ) -> None:
        selected_id = maintenance_id or uuid.uuid4().hex
        self.maintenance_id = _safe_id(selected_id, "maintenance_id", optional=False)
        assert self.maintenance_id is not None
        if _ROTATION_SUFFIX.search(self.maintenance_id):
            raise MaintenanceDiagnosticError("diagnostic_identity_invalid")
        self.data_root = Path(data_root).expanduser()
        self.policy = policy
        self.clock = clock
        self._failed_paths: set[str] = set()

    def write(
        self,
        *,
        event: str,
        stage: str,
        status: str,
        at: datetime | str | None = None,
        **fields: object,
    ) -> DiagnosticWriteResult:
        try:
            selected_time = _aware_jst(at, "at") if at is not None else self._now()
            normalized = _normalize_event(
                maintenance_id=self.maintenance_id,
                event=event,
                stage=stage,
                status=status,
                at=selected_time,
                retention_days=self.policy.retention_days,
                fields=fields,
            )
            encoded = (
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            if len(encoded) > self.policy.max_event_bytes:
                return DiagnosticWriteResult("unavailable", "event_size_limit")
            root = _prepare_data_root(self.data_root)
            day_dir = _ensure_directory(
                root,
                PurePosixPath("maintenance") / "logs" / normalized["target_date"],
            )
            with _path_lock(day_dir):
                entries = _safe_log_entries(day_dir)
                matching = sorted(
                    (
                        entry
                        for entry in entries
                        if entry.name == f"{self.maintenance_id}.jsonl"
                        or entry.name.startswith(f"{self.maintenance_id}.part-")
                    ),
                    key=lambda entry: entry.name,
                )
                for entry in matching:
                    _read_log(Path(entry.path), max_bytes=self.policy.max_file_bytes)
                target = self._select_target(day_dir, entries, matching, encoded)
                key = os.path.normcase(str(target.absolute()))
                if key in self._failed_paths:
                    return DiagnosticWriteResult(
                        "unavailable", "trace_io_unavailable", path=target
                    )
                if target.exists():
                    _read_log(target, max_bytes=self.policy.max_file_bytes)
                self._append(target, encoded)
                return DiagnosticWriteResult(
                    "written", bytes_written=len(encoded), path=target
                )
        except MaintenanceDiagnosticError as exc:
            return DiagnosticWriteResult("unavailable", exc.code)
        except (OSError, RetentionError, SafeFileError, ValueError, TypeError):
            return DiagnosticWriteResult("unavailable", "trace_write_unavailable")

    def require_written(self, **event: object) -> Path:
        result = self.write(**event)  # type: ignore[arg-type]
        if result.status != "written" or result.path is None:
            raise MaintenanceDiagnosticError(
                result.reason_code or "trace_write_unavailable"
            )
        return result.path

    def _now(self) -> datetime:
        if self.clock is not None:
            value = self.clock.now_jst()
            return _aware_jst(value, "at")
        return datetime.now(JST)

    def _select_target(
        self,
        day_dir: Path,
        entries: list[os.DirEntry[str]],
        matching: list[os.DirEntry[str]],
        encoded: bytes,
    ) -> Path:
        if matching:
            latest = matching[-1]
            latest_path = Path(latest.path)
            if latest.stat(follow_symlinks=False).st_size + len(encoded) <= (
                self.policy.max_file_bytes
            ):
                return latest_path
        if len(entries) >= self.policy.max_files_per_day:
            raise MaintenanceDiagnosticError("daily_file_quota")
        if not matching:
            return day_dir / f"{self.maintenance_id}.jsonl"
        segment = len(matching) + 1
        if segment > 9999:
            raise MaintenanceDiagnosticError("daily_file_quota")
        return day_dir / f"{self.maintenance_id}.part-{segment:04d}.jsonl"

    def _append(self, path: Path, encoded: bytes) -> None:
        if _has_reparse_point(path) or _has_reparse_point(path.parent):
            raise MaintenanceDiagnosticError("diagnostic_path_reparse_point")
        root = path.parents[3]
        relative_path = path.relative_to(root).as_posix()
        original_stat = None
        original_bytes = b""
        if path.exists():
            try:
                with open_file_beneath(root, relative_path, "rb") as stream:
                    original_stat = os.fstat(stream.fileno())
                    original_bytes = stream.read(self.policy.max_file_bytes + 1)
                    named_stat = path.lstat()
            except (OSError, SafeFileError) as exc:
                raise MaintenanceDiagnosticError("diagnostic_file_unavailable") from exc
            if not stat.S_ISREG(original_stat.st_mode) or not os.path.samestat(
                original_stat, named_stat
            ):
                raise MaintenanceDiagnosticError("diagnostic_log_target_invalid")
        payload = original_bytes + encoded
        if len(payload) > self.policy.max_file_bytes:
            raise MaintenanceDiagnosticError("diagnostic_file_size_limit")
        temporary = path.parent / f".maintenance-log-stage-{uuid.uuid4().hex}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary, flags, 0o600)
            opened = os.fstat(descriptor)
            named = temporary.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or _has_reparse_point(temporary)
                or not os.path.samestat(opened, named)
            ):
                raise MaintenanceDiagnosticError("diagnostic_log_stage_invalid")
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("staged write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            if _has_reparse_point(path) or _has_reparse_point(path.parent):
                raise MaintenanceDiagnosticError("diagnostic_path_reparse_point")
            if original_stat is None:
                if path.exists():
                    raise MaintenanceDiagnosticError("diagnostic_log_changed")
            else:
                current_stat = path.lstat()
                if not stat.S_ISREG(current_stat.st_mode) or not os.path.samestat(
                    original_stat, current_stat
                ):
                    raise MaintenanceDiagnosticError("diagnostic_log_changed")
            os.replace(temporary, path)
            if os.name != "nt":
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OSError:
            self._failed_paths.add(os.path.normcase(str(path.absolute())))
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary.exists() and not _has_reparse_point(temporary):
                try:
                    temporary.unlink()
                except OSError:
                    pass


def _result_status(value: object) -> str:
    statuses = {
        "started": "started",
        "running": "started",
        "in_progress": "started",
        "complete": "complete",
        "completed": "complete",
        "normal": "complete",
        "normal_shortfall": "complete",
        "checked_ok": "complete",
        "verified": "complete",
        "failed": "failed",
        "rolled_back": "failed",
        "blocked": "blocked",
        "abnormal": "blocked",
        "incomplete": "blocked",
        "investigation_required": "blocked",
        "waiting_external": "waiting",
        "waiting": "waiting",
        "configured_unverified": "unverified",
        "cancelled": "cancelled",
        "resumed": "resumed",
        "approved": "approved",
        "changes_requested": "changes_requested",
        "passed": "passed",
        "ready": "ready",
        "deployed": "ready",
        "not_ready": "not_ready",
        "unknown": "unknown",
        "busy": "busy",
    }
    selected = value if isinstance(value, str) else None
    return statuses.get(selected, "unknown")


def _trace_method_fields(
    function: Any, args: tuple[object, ...], kwargs: Mapping[str, object]
) -> dict[str, object]:
    try:
        bound = inspect.signature(function).bind_partial(*args, **kwargs).arguments
    except (TypeError, ValueError):
        bound = dict(kwargs)
    fields: dict[str, object] = {}
    for field in _IDENTITY_FIELDS | {"base_commit", "result_version"}:
        value = bound.get(field)
        if value is not None:
            fields[field] = value
    for key in ("context", "request", "identity"):
        nested = bound.get(key)
        if key == "request" and nested is not None:
            nested_context = getattr(nested, "context", None)
            if isinstance(nested_context, Mapping):
                nested = nested_context
        if isinstance(nested, Mapping):
            for field in _IDENTITY_FIELDS:
                if field not in fields and nested.get(field) is not None:
                    fields[field] = nested[field]
            if (
                "result_version" not in fields
                and nested.get("result_version") is not None
            ):
                fields["result_version"] = nested["result_version"]
            if "base_commit" not in fields and nested.get("base_commit") is not None:
                fields["base_commit"] = nested["base_commit"]
    for candidate in bound.values():
        for field in _IDENTITY_FIELDS | {"base_commit", "result_version"}:
            if field in fields:
                continue
            try:
                value = getattr(candidate, field, None)
            except Exception:
                continue
            if value is not None:
                fields[field] = value
    instance = bound.get("self")
    plan = getattr(instance, "plan", None)
    if plan is not None:
        for field in _IDENTITY_FIELDS | {
            "model",
            "effort",
            "service_tier",
            "fast_mode",
        }:
            if field in fields:
                continue
            value = getattr(plan, field, None)
            if field == "job_id" and value is None:
                value = getattr(getattr(plan, "manifest", None), "job_id", None)
            elif field == "source_run_id" and value is None:
                value = getattr(getattr(plan, "manifest", None), "source_run_id", None)
            elif field == "incident_id" and value is None:
                value = getattr(getattr(plan, "manifest", None), "incident_id", None)
            elif field == "source_launch_id" and value is None:
                value = getattr(
                    getattr(plan, "manifest", None), "source_launch_id", None
                )
            elif field == "model" and value is None:
                value = getattr(plan, "requested_model", None)
            elif field == "effort" and value is None:
                value = getattr(plan, "requested_effort", None)
            elif field == "service_tier" and value is None:
                value = getattr(plan, "requested_service_tier", None)
            elif field == "fast_mode" and value is None:
                value = getattr(plan, "requested_fast_mode", None)
            if value is not None:
                fields[field] = value
    for source_name, target_name in (
        ("requested_model", "model"),
        ("observed_model", "model"),
        ("requested_effort", "effort"),
        ("observed_effort", "effort"),
        ("requested_service_tier", "service_tier"),
        ("observed_service_tier", "service_tier"),
        ("requested_fast_mode", "fast_mode"),
    ):
        value = bound.get(source_name)
        if value is not None:
            fields[target_name] = value
    return fields


def audit_maintenance_stage(
    stage: str,
    *,
    start_event: str = "stage_started",
    finish_event: str = "stage_completed",
    include_review: bool = False,
    include_test: bool = False,
):
    """Record a controller boundary before and after its persistent operation."""

    if (
        stage not in _STAGES
        or start_event not in _EVENTS
        or finish_event not in _EVENTS
    ):
        raise MaintenanceDiagnosticError("diagnostic_enum_invalid")

    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            instance = args[0] if args else None
            trace = getattr(instance, "_diagnostic_trace", None)
            if trace is None:
                trace = kwargs.get("diagnostic_trace")
            if trace is None:
                return function(*args, **kwargs)
            fields = _trace_method_fields(function, args, kwargs)
            trace.require_written(
                event=start_event,
                stage=stage,
                status="resumed" if start_event == "stage_resumed" else "started",
                **fields,
            )
            started = time.monotonic()
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                failure_fields = dict(fields)
                code = getattr(exc, "code", None) or getattr(exc, "reason_code", None)
                if isinstance(code, str) and _SAFE_CODE.fullmatch(code):
                    failure_fields["reason_code"] = code
                class_name = type(exc).__name__
                if _SAFE_CLASS.fullmatch(class_name):
                    failure_fields["error_class"] = class_name
                failure_fields["duration_ms"] = max(
                    0, int((time.monotonic() - started) * 1000)
                )
                try:
                    trace.require_written(
                        event="stage_failed",
                        stage=stage,
                        status="failed",
                        **failure_fields,
                    )
                except MaintenanceDiagnosticError as trace_error:
                    raise MaintenanceAuditReceiptError(
                        trace_error.code, operation_returned=False
                    ) from exc
                raise

            def require_completion_receipt(**event: object) -> Path:
                try:
                    return trace.require_written(**event)
                except MaintenanceDiagnosticError as trace_error:
                    raise MaintenanceAuditReceiptError(
                        trace_error.code,
                        operation_returned=True,
                        operation_result=result,
                    ) from trace_error

            event_fields = dict(fields)
            event_fields["duration_ms"] = max(
                0, int((time.monotonic() - started) * 1000)
            )
            for field in ("reason_code", "review_status", "test_status", "base_commit"):
                value = getattr(result, field, None)
                if value is not None:
                    event_fields[field] = value
            result_state = getattr(result, "status", None)
            started_flag = getattr(result, "started", None)
            if result_state is None and type(started_flag) is bool:
                result_state = "started" if started_flag else "blocked"
            if result_state is None:
                result_state = getattr(result, "gate_status", None)
            if result_state is None:
                result_state = getattr(result, "next_action", None)
            if result_state is None:
                result_state = getattr(result, "investigation_status", None)
            if result_state is None:
                result_state = getattr(result, "plan_status", None)
            if result_state is None:
                result_state = getattr(result, "review_status", None)
            if result_state is None:
                result_state = getattr(result, "state", None)
            if result_state is None:
                result_state = getattr(getattr(result, "stage", None), "status", None)
            if result_state is None:
                result_state = getattr(
                    getattr(result, "plan", None), "observation_status", None
                )
            if result_state is None:
                result_state = getattr(result, "observation_status", None)
            selected_status = _result_status(result_state)
            require_completion_receipt(
                event=finish_event,
                stage=stage,
                status=selected_status,
                **event_fields,
            )
            if include_review:
                review_status = getattr(result, "review_status", None)
                if isinstance(review_status, str):
                    review_event_status = _result_status(review_status)
                    if review_event_status not in _REVIEW_STATUSES:
                        review_event_status = "unknown"
                    require_completion_receipt(
                        event="review_completed",
                        stage="review",
                        status=review_event_status,
                        review_status=review_event_status,
                        **fields,
                    )
            if include_test:
                test_status = getattr(result, "outcome", None)
                if not isinstance(test_status, str):
                    test_status = getattr(result, "status", None)
                test_event_status = _result_status(test_status)
                if test_event_status not in _TEST_STATUSES:
                    test_event_status = "unknown"
                require_completion_receipt(
                    event="test_completed",
                    stage="test",
                    status=test_event_status,
                    test_status=test_event_status,
                    **fields,
                )
            if selected_status == "waiting":
                require_completion_receipt(
                    event="stage_waiting",
                    stage=stage,
                    status="waiting",
                    **event_fields,
                )
            elif selected_status == "cancelled":
                require_completion_receipt(
                    event="stage_cancelled",
                    stage=stage,
                    status="cancelled",
                    **event_fields,
                )
            return result

        wrapped.__signature__ = signature
        return wrapped

    return decorate


def read_diagnostic_events(
    path: str | Path,
    *,
    policy: MaintenanceDiagnosticPolicy = MaintenanceDiagnosticPolicy(),
) -> list[dict[str, Any]]:
    """Read one bounded MN04 file and reject incomplete or mixed records."""

    selected = Path(path)
    if not _LOG_NAME.fullmatch(selected.name):
        raise MaintenanceDiagnosticError("diagnostic_file_invalid")
    return _read_log(selected, max_bytes=policy.max_file_bytes)


def prune_expired_diagnostic_logs(
    data_root: str | Path,
    *,
    now: datetime | str,
    policy: MaintenanceDiagnosticPolicy = MaintenanceDiagnosticPolicy(),
) -> DiagnosticRetentionResult:
    """Delete only MN04 direct-child JSONL files after their last event expires."""

    try:
        current = _aware_jst(now, "now")
        root = validate_data_root(data_root)
        log_root = root / "maintenance" / "logs"
        if not log_root.exists():
            return DiagnosticRetentionResult(0, 0)
        if _has_reparse_point(log_root):
            raise MaintenanceDiagnosticError("diagnostic_path_reparse_point")
        removed = failures = 0
        with os.scandir(log_root) as dates:
            date_entries = [entry for entry in dates if _DATE_DIR.fullmatch(entry.name)]
        for date_entry in date_entries:
            day_dir = Path(date_entry.path)
            try:
                if _has_reparse_point(day_dir) or not date_entry.is_dir(
                    follow_symlinks=False
                ):
                    failures += 1
                    continue
                with _path_lock(day_dir):
                    with os.scandir(day_dir) as raw_entries:
                        stages = [
                            entry
                            for entry in raw_entries
                            if _LOG_STAGE.fullmatch(entry.name)
                        ]
                    for entry in stages:
                        stage_path = Path(entry.path)
                        try:
                            if _has_reparse_point(stage_path) or not entry.is_file(
                                follow_symlinks=False
                            ):
                                failures += 1
                                continue
                            modified = datetime.fromtimestamp(
                                entry.stat(follow_symlinks=False).st_mtime, JST
                            )
                            if current - modified < ORPHAN_STAGE_MIN_AGE:
                                continue
                            relative = stage_path.relative_to(root).as_posix()
                            unlink_file_beneath(root, relative)
                            removed += 1
                        except (OSError, SafeFileError, MaintenanceDiagnosticError):
                            failures += 1
                    entries = _safe_log_entries(day_dir)
                    for entry in entries:
                        path = Path(entry.path)
                        try:
                            records = _read_log(path, max_bytes=policy.max_file_bytes)
                            if not records:
                                failures += 1
                                continue
                            expiries = [
                                _aware_jst(row.get("retention_expires_at"), "expiry")
                                for row in records
                            ]
                            if max(expiries) > current:
                                continue
                            relative = path.relative_to(root).as_posix()
                            unlink_file_beneath(root, relative)
                            removed += 1
                        except (OSError, SafeFileError, MaintenanceDiagnosticError):
                            failures += 1
            except (OSError, MaintenanceDiagnosticError):
                failures += 1
        return DiagnosticRetentionResult(removed, failures)
    except (OSError, RetentionError, MaintenanceDiagnosticError, ValueError):
        return DiagnosticRetentionResult(0, 1)


def _remove_owned_tree(path: Path, evidence_root: Path) -> None:
    if _has_reparse_point(path):
        raise MaintenanceDiagnosticError("orphan_path_reparse_point")
    with os.scandir(path) as entries:
        children = list(entries)
    for entry in children:
        child = Path(entry.path)
        if _has_reparse_point(child):
            raise MaintenanceDiagnosticError("orphan_path_reparse_point")
        if entry.is_dir(follow_symlinks=False):
            _remove_owned_tree(child, evidence_root)
        elif entry.is_file(follow_symlinks=False):
            child.unlink()
        else:
            raise MaintenanceDiagnosticError("orphan_entry_invalid")
    path.resolve(strict=True).relative_to(evidence_root)
    path.rmdir()


def recover_orphan_evidence_stages(
    data_root: str | Path,
    *,
    now: datetime | str,
    min_age: timedelta = ORPHAN_STAGE_MIN_AGE,
) -> OrphanRecoveryResult:
    """Remove only old, recognizable MN06 staging directories below evidence/."""

    try:
        current = _aware_jst(now, "now")
        root = validate_data_root(data_root)
        evidence_root = root / "maintenance" / "evidence"
        if not evidence_root.exists():
            return OrphanRecoveryResult(0, 0)
        if _has_reparse_point(evidence_root) or not evidence_root.is_dir():
            raise MaintenanceDiagnosticError("orphan_root_invalid")
        removed = failures = 0
        stack: list[tuple[Path, int]] = [(evidence_root, 0)]
        while stack:
            directory, depth = stack.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    child = Path(entry.path)
                    if _has_reparse_point(child):
                        if _EVIDENCE_STAGE.fullmatch(entry.name):
                            failures += 1
                        continue
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if _EVIDENCE_STAGE.fullmatch(entry.name):
                        try:
                            modified = datetime.fromtimestamp(
                                entry.stat(follow_symlinks=False).st_mtime, JST
                            )
                            if current - modified < min_age:
                                continue
                            if depth < 1 or depth > 4:
                                failures += 1
                                continue
                            _remove_owned_tree(child, evidence_root)
                            removed += 1
                        except (OSError, MaintenanceDiagnosticError, ValueError):
                            failures += 1
                    elif depth < 4 and (
                        _DATE_DIR.fullmatch(entry.name)
                        or _SAFE_ID.fullmatch(entry.name)
                    ):
                        stack.append((child, depth + 1))
        return OrphanRecoveryResult(removed, failures)
    except (OSError, RetentionError, MaintenanceDiagnosticError, ValueError):
        return OrphanRecoveryResult(0, 1)


def run_maintenance_retention(
    data_root: str | Path,
    *,
    now: datetime | str,
    policy: MaintenanceDiagnosticPolicy = MaintenanceDiagnosticPolicy(),
) -> MaintenanceRetentionOutcome:
    """Apply MN04/MN06 copy retention and stage recovery only."""

    root = Path(data_root).expanduser()
    if not root.exists():
        return MaintenanceRetentionOutcome(0, 0, 0, 0)
    logs = prune_expired_diagnostic_logs(root, now=now, policy=policy)
    orphans = recover_orphan_evidence_stages(root, now=now)
    try:
        from automation.inago.maintenance.evidence import expire_evidence_copies

        evidence_count, evidence_failures = expire_evidence_copies(root, now=now)
    except (OSError, RetentionError, ValueError):
        evidence_count, evidence_failures = 0, 1
    return MaintenanceRetentionOutcome(
        expired_log_files=logs.removed_files,
        expired_evidence_files=evidence_count,
        recovered_orphan_stages=orphans.removed_stages,
        failures=logs.failures + orphans.failures + evidence_failures,
    )
