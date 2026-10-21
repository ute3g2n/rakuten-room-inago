"""Best-effort snapshots of the Codex session files used by daily and maintenance flows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo


DEFAULT_POLL_INTERVAL_SECONDS = 30
_JST = ZoneInfo("Asia/Tokyo")
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_HEADER_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ConversationLogSyncResult:
    status: str
    reason_code: str | None
    copied_count: int
    unchanged_count: int
    directory: Path


@dataclass(frozen=True, slots=True)
class _SessionFile:
    path: Path
    thread_id: str
    parent_thread_id: str | None
    is_subagent: bool


def session_day_directories(
    sessions_root: str | Path, first_day: date, last_day: date
) -> tuple[Path, ...]:
    """Return the Codex YYYY/MM/DD folders for an inclusive date range."""

    if last_day < first_day:
        first_day, last_day = last_day, first_day
    root = Path(sessions_root)
    result: list[Path] = []
    day = first_day
    while day <= last_day:
        result.append(root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}")
        day += timedelta(days=1)
    return tuple(result)


def iter_rollout_paths(
    sessions_root: str | Path, first_day: date, last_day: date
) -> tuple[Path, ...]:
    """List regular rollout files under Codex's dated session folders."""

    paths: list[Path] = []
    for directory in session_day_directories(sessions_root, first_day, last_day):
        try:
            if directory.is_symlink() or not directory.is_dir():
                continue
            paths.extend(
                path
                for path in directory.glob("rollout-*.jsonl")
                if not path.is_symlink() and path.is_file()
            )
        except OSError:
            continue
    return tuple(sorted(paths, key=lambda path: str(path).casefold()))


def read_rollout_header(path: str | Path) -> Mapping[str, Any] | None:
    """Read only a bounded session_meta header, never conversation turns."""

    try:
        with Path(path).open("rb") as stream:
            line = stream.readline(_MAX_HEADER_BYTES + 1)
        if len(line) > _MAX_HEADER_BYTES:
            return None
        event = json.loads(line.decode("utf-8"))
        if not isinstance(event, Mapping) or event.get("type") != "session_meta":
            return None
        payload = event.get("payload")
        return payload if isinstance(payload, Mapping) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _jst_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value
    elif isinstance(value, str):
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return date.fromisoformat(value)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("conversation_log_start_date_invalid") from exc
    else:
        raise ValueError("conversation_log_start_date_invalid")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_JST).date()


def _component(value: str) -> str:
    safe = _SAFE_COMPONENT.sub("_", value).strip(" .")
    if not safe or safe in {".", ".."}:
        safe = "id"
    if safe != value:
        suffix = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:10]
        safe = f"{safe[:110]}-{suffix}"
    return safe


def _single_line(value: object) -> str:
    text = str(value)
    return " ".join("".join(ch if ch >= " " and ch != "\x7f" else " " for ch in text).split())


def conversation_log_directory(
    runtime_root: str | Path,
    *,
    flow: str,
    started_on: date | datetime | str,
    flow_id: str,
    role: str,
) -> Path:
    """Return the stable, ignored runtime folder for one flow role."""

    if flow not in {"daily", "maintenance"}:
        raise ValueError("conversation_log_flow_invalid")
    if not isinstance(flow_id, str) or not flow_id.strip():
        raise ValueError("conversation_log_flow_id_invalid")
    if not isinstance(role, str) or not role.strip():
        raise ValueError("conversation_log_role_invalid")
    day = _jst_date(started_on).isoformat()
    root = Path(runtime_root).expanduser().resolve(strict=False)
    return (
        root
        / "conversation-logs"
        / flow
        / f"{day}__{_component(flow_id)}"
        / _component(role)
    )


def _prepare_destination(path: Path, runtime_root: Path) -> None:
    root_input = Path(runtime_root).expanduser()
    root = root_input.resolve(strict=False)
    if root_input.is_symlink():
        raise OSError("runtime_root_is_symlink")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise OSError("runtime_root_not_directory")
    try:
        relative = path.relative_to(root / "conversation-logs")
    except ValueError as exc:
        raise OSError("conversation_log_path_outside_runtime") from exc
    base = root / "conversation-logs"
    if base.exists() or base.is_symlink():
        if base.is_symlink() or not base.is_dir():
            raise OSError("conversation_log_path_not_directory")
    else:
        base.mkdir()
    try:
        base.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise OSError("conversation_log_path_outside_runtime") from exc
    current = base
    for part in relative.parts:
        candidate = current / part
        if candidate.exists() or candidate.is_symlink():
            if candidate.is_symlink() or not candidate.is_dir():
                raise OSError("conversation_log_path_not_directory")
        else:
            candidate.mkdir()
        try:
            candidate.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise OSError("conversation_log_path_outside_runtime") from exc
        current = candidate
    if current != path:
        raise OSError("conversation_log_path_invalid")


def _sessions_root(codex_home: str | Path | None) -> Path:
    if codex_home is None:
        home = os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
        codex_home_path = Path(home).expanduser()
    else:
        codex_home_path = Path(codex_home).expanduser()
    return codex_home_path / "sessions"


def _session_files(
    sessions_root: Path, first_day: date, last_day: date
) -> tuple[_SessionFile, ...]:
    result: list[_SessionFile] = []
    for path in iter_rollout_paths(sessions_root, first_day, last_day):
        header = read_rollout_header(path)
        if header is None:
            continue
        thread_id = header.get("id")
        parent_thread_id = header.get("parent_thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        result.append(
            _SessionFile(
                path=path,
                thread_id=thread_id,
                parent_thread_id=(
                    parent_thread_id if isinstance(parent_thread_id, str) else None
                ),
                is_subagent=header.get("thread_source") == "subagent",
            )
        )
    return tuple(result)


def _file_name(kind: str, thread_id: str) -> str:
    return f"{kind}__{_component(thread_id)}.jsonl"


def _atomic_copy(source: Path, destination: Path) -> str:
    if destination.is_symlink():
        return "destination_is_symlink"
    before = source.stat()
    if destination.is_file():
        try:
            existing = destination.stat()
            if (
                existing.st_size == before.st_size
                and existing.st_mtime_ns == before.st_mtime_ns
            ):
                return "unchanged"
        except OSError:
            pass
    temporary: Path | None = None
    try:
        with source.open("rb") as source_stream:
            opened_before = os.fstat(source_stream.fileno())
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as target_stream:
                temporary = Path(target_stream.name)
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            opened_after = os.fstat(source_stream.fileno())
        after = source.stat()
        if (
            opened_before.st_size != opened_after.st_size
            or opened_before.st_mtime_ns != opened_after.st_mtime_ns
            or opened_after.st_size != after.st_size
            or opened_after.st_mtime_ns != after.st_mtime_ns
        ):
            return "source_changed_during_copy"
        os.utime(temporary, ns=(after.st_atime_ns, after.st_mtime_ns))
        os.replace(temporary, destination)
        temporary = None
        return "copied"
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _read_saved_header(path: Path) -> Mapping[str, Any] | None:
    return read_rollout_header(path)


def _write_status(
    directory: Path,
    *,
    flow: str,
    started_on: date,
    flow_id: str,
    role: str,
    root_thread_id: str | None,
    status: str,
    reason_code: str | None,
    thread_status: Mapping[str, tuple[str, str | None]],
) -> None:
    lines = [
        f"updated_at_utc={datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')}",
        f"flow={_single_line(flow)}",
        f"started_on_jst={started_on.isoformat()}",
        f"flow_id={_single_line(flow_id)}",
        f"role={_single_line(role)}",
        f"root_thread_id={_single_line(root_thread_id) if root_thread_id else 'unavailable'}",
        f"sync_status={status}",
        f"reason_code={reason_code or 'none'}",
        "threads:",
    ]
    saved_thread_ids: set[str] = set()
    for path in sorted(directory.glob("*.jsonl"), key=lambda item: item.name.casefold()):
        if path.is_symlink() or not path.is_file():
            continue
        header = _read_saved_header(path)
        thread_id = header.get("id") if header else None
        if not isinstance(thread_id, str):
            continue
        parent = header.get("parent_thread_id") if header else None
        parent_id = parent if isinstance(parent, str) else "none"
        kind = "main" if path.name.startswith("main__") else "sub"
        entry_status, entry_reason = thread_status.get(
            thread_id, ("preserved", None)
        )
        saved_thread_ids.add(thread_id)
        lines.append(
            " ".join(
                (
                    f"thread_id={_single_line(thread_id)}",
                    f"parent_thread_id={_single_line(parent_id)}",
                    f"kind={kind}",
                    f"file={path.name}",
                    f"status={entry_status}",
                    f"reason_code={entry_reason or 'none'}",
                )
            )
        )
    for thread_id, (entry_status, entry_reason) in thread_status.items():
        if thread_id in saved_thread_ids:
            continue
        lines.append(
            " ".join(
                (
                    f"thread_id={_single_line(thread_id)}",
                    "parent_thread_id=unknown",
                    "kind=unknown",
                    "file=none",
                    f"status={entry_status}",
                    f"reason_code={entry_reason or 'none'}",
                )
            )
        )
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".status.",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "status.txt")
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def sync_conversation_logs(
    runtime_root: str | Path,
    *,
    flow: str,
    started_on: date | datetime | str,
    flow_id: str,
    role: str,
    root_thread_id: str | None,
    codex_home: str | Path | None = None,
) -> ConversationLogSyncResult:
    """Atomically refresh all available original rollout files for one tree."""

    try:
        directory = conversation_log_directory(
            runtime_root,
            flow=flow,
            started_on=started_on,
            flow_id=flow_id,
            role=role,
        )
        started_day = _jst_date(started_on)
    except (OSError, RuntimeError, TypeError, ValueError):
        fallback = Path(runtime_root) / "conversation-logs"
        return ConversationLogSyncResult("failed", "identity_invalid", 0, 0, fallback)

    copied = 0
    unchanged = 0
    reason_code: str | None = None
    thread_status: dict[str, tuple[str, str | None]] = {}
    try:
        _prepare_destination(directory, Path(runtime_root))
        if not isinstance(root_thread_id, str) or not root_thread_id.strip():
            status, reason_code = "waiting", "root_thread_id_unavailable"
        else:
            sessions_root = _sessions_root(codex_home)
            today = datetime.now(_JST).date()
            last_day = max(today, started_day) + timedelta(days=1)
            first_day = started_day - timedelta(days=1)
            files = _session_files(sessions_root, first_day, last_day)
            roots = [item for item in files if item.thread_id == root_thread_id]
            if not roots:
                status, reason_code = "partial", "root_thread_not_found"
                thread_status[root_thread_id] = ("missing", reason_code)
            else:
                roots.sort(
                    key=lambda item: (
                        item.path.stat().st_mtime_ns,
                        str(item.path).casefold(),
                    ),
                    reverse=True,
                )
                selected: dict[str, tuple[_SessionFile, str]] = {
                    root_thread_id: (roots[0], "main")
                }
                pending = [root_thread_id]
                while pending:
                    parent_id = pending.pop()
                    children = [
                        item
                        for item in files
                        if item.is_subagent and item.parent_thread_id == parent_id
                    ]
                    for child in children:
                        if child.thread_id in selected:
                            continue
                        selected[child.thread_id] = (child, "sub")
                        pending.append(child.thread_id)

                # If duplicate rollout headers reuse an id, choose the newest copy.
                for thread_id, (item, kind) in tuple(selected.items()):
                    duplicates = [
                        candidate
                        for candidate in files
                        if candidate.thread_id == thread_id
                        and (kind == "main" or candidate.is_subagent)
                    ]
                    if len(duplicates) > 1:
                        duplicates.sort(
                            key=lambda candidate: candidate.path.stat().st_mtime_ns,
                            reverse=True,
                        )
                        selected[thread_id] = (duplicates[0], kind)

                status = "complete"
                for thread_id, (item, kind) in selected.items():
                    destination = directory / _file_name(kind, thread_id)
                    try:
                        copy_status = _atomic_copy(item.path, destination)
                    except OSError:
                        copy_status = "copy_failed"
                    if copy_status == "copied":
                        copied += 1
                        thread_status[thread_id] = ("copied", None)
                    elif copy_status == "unchanged":
                        unchanged += 1
                        thread_status[thread_id] = ("unchanged", None)
                    else:
                        status = "partial"
                        reason_code = reason_code or copy_status
                        thread_status[thread_id] = ("failed", copy_status)
                if status == "complete" and reason_code is None:
                    reason_code = None

        _write_status(
            directory,
            flow=flow,
            started_on=started_day,
            flow_id=flow_id,
            role=role,
            root_thread_id=root_thread_id,
            status=status,
            reason_code=reason_code,
            thread_status=thread_status,
        )
        return ConversationLogSyncResult(
            status,
            reason_code,
            copied,
            unchanged,
            directory,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return ConversationLogSyncResult(
            "failed",
            "conversation_log_save_failed",
            copied,
            unchanged,
            directory,
        )


class ConversationLogWatcher:
    """Periodically snapshot a root rollout while an app-server turn is active."""

    def __init__(
        self,
        runtime_root: str | Path,
        *,
        flow: str,
        started_on: date | datetime | str,
        flow_id: str,
        role: str,
        root_thread_id: str | None | Callable[[], str | None],
        codex_home: str | Path | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        on_result: Callable[[ConversationLogSyncResult], None] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("conversation_log_poll_interval_invalid")
        self.runtime_root = runtime_root
        self.flow = flow
        self.started_on = started_on
        self.flow_id = flow_id
        self.role = role
        self.root_thread_id_source = root_thread_id
        self.codex_home = codex_home
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.on_result = on_result
        self.directory = conversation_log_directory(
            runtime_root,
            flow=flow,
            started_on=started_on,
            flow_id=flow_id,
            role=role,
        )
        self.last_result: ConversationLogSyncResult | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _root_thread_id(self) -> str | None:
        source = self.root_thread_id_source
        try:
            value = source() if callable(source) else source
        except Exception:
            return None
        return value if isinstance(value, str) and value.strip() else None

    def sync_now(self) -> ConversationLogSyncResult:
        with self._lock:
            result = sync_conversation_logs(
                self.runtime_root,
                flow=self.flow,
                started_on=self.started_on,
                flow_id=self.flow_id,
                role=self.role,
                root_thread_id=self._root_thread_id(),
                codex_home=self.codex_home,
            )
            self.last_result = result
        if self.on_result is not None:
            try:
                self.on_result(result)
            except Exception:
                pass
        return result

    def _run(self) -> None:
        while not self._stop.wait(self.poll_interval_seconds):
            self.sync_now()

    def start(self) -> "ConversationLogWatcher":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self.sync_now()
        self._thread = threading.Thread(
            target=self._run,
            name="inago-conversation-log-watcher",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> ConversationLogSyncResult | None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join()
        self._thread = None
        return self.sync_now()
