"""Windows process-shared byte lock for one daily run at a time."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Self


class RunLockError(RuntimeError):
    """The daily operating-system lock could not be used safely."""


@dataclass(frozen=True, slots=True)
class LockOwner:
    """Stable identity for the process that currently owns the daily lock."""

    pid: int
    process_start_time: str
    run_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "process_start_time": self.process_start_time,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "LockOwner | None":
        if not isinstance(payload, dict) or set(payload) != {
            "pid",
            "process_start_time",
            "run_id",
        }:
            return None
        pid = payload.get("pid")
        process_start_time = payload.get("process_start_time")
        run_id = payload.get("run_id")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid < 1
            or not isinstance(process_start_time, str)
            or not isinstance(run_id, str)
            or not run_id.strip()
        ):
            return None
        try:
            parsed = datetime.fromisoformat(process_start_time.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return cls(pid, process_start_time, run_id)


@dataclass(frozen=True, slots=True)
class LockOwnerObservation:
    """Read-only observation of the atomically published lock-owner record."""

    status: Literal["present", "missing", "invalid", "unknown"]
    owner: LockOwner | None
    reason: str


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    """Process identity comparison that never treats access failure as death."""

    status: Literal["alive", "dead", "reused", "unknown"]
    pid: int
    expected_start_time: str
    observed_start_time: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class ProcessTreeObservation:
    """Read-only list of live descendants for one owner PID."""

    status: Literal["observed", "unknown"]
    parent_pid: int
    child_pids: tuple[int, ...]
    reason: str


def read_lock_owner(path: str | Path) -> LockOwnerObservation:
    """Read owner metadata without trying to acquire, release, or repair a lock."""

    owner_path = Path(path)
    try:
        raw_bytes = owner_path.read_bytes()
    except FileNotFoundError:
        return LockOwnerObservation("missing", None, "owner_file_missing")
    except OSError:
        return LockOwnerObservation("unknown", None, "owner_file_unreadable")
    if len(raw_bytes) > 4096:
        return LockOwnerObservation("invalid", None, "owner_file_too_large")
    try:
        raw = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeError:
        return LockOwnerObservation("invalid", None, "owner_encoding_invalid")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return LockOwnerObservation("invalid", None, "owner_json_invalid")
    owner = LockOwner.from_dict(payload)
    if owner is None:
        return LockOwnerObservation("invalid", None, "owner_fields_invalid")
    return LockOwnerObservation("present", owner, "owner_metadata_observed")


def _canonical_start_time(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def observe_process_owner(
    owner: LockOwner,
    *,
    start_time_reader: Callable[[int], str] | None = None,
) -> ProcessObservation:
    """Compare PID and creation time; permission/API errors remain unknown."""

    reader = start_time_reader or process_start_time
    expected = _canonical_start_time(owner.process_start_time)
    if expected is None:
        return ProcessObservation(
            "unknown",
            owner.pid,
            owner.process_start_time,
            None,
            "owner_start_time_invalid",
        )
    try:
        observed_raw = reader(owner.pid)
    except ProcessLookupError:
        return ProcessObservation(
            "dead", owner.pid, owner.process_start_time, None, "process_not_found"
        )
    except PermissionError:
        return ProcessObservation(
            "unknown",
            owner.pid,
            owner.process_start_time,
            None,
            "process_access_denied",
        )
    except (OSError, RunLockError, TypeError, ValueError):
        return ProcessObservation(
            "unknown",
            owner.pid,
            owner.process_start_time,
            None,
            "process_observation_failed",
        )
    observed = _canonical_start_time(observed_raw)
    if observed is None:
        return ProcessObservation(
            "unknown",
            owner.pid,
            owner.process_start_time,
            None,
            "process_start_time_invalid",
        )
    if observed != expected:
        return ProcessObservation(
            "reused", owner.pid, owner.process_start_time, observed, "pid_reused"
        )
    return ProcessObservation(
        "alive",
        owner.pid,
        owner.process_start_time,
        observed,
        "process_identity_matches",
    )


class RunLockBusy(RunLockError):
    """Another process owns the lock; ``owner`` may be unknown during publish."""

    def __init__(self, owner: LockOwner | None) -> None:
        self.owner = owner
        super().__init__("daily run lock is already held")


def process_start_time(pid: int) -> str:
    """Read a Windows process creation time so PID reuse is distinguishable."""

    if os.name != "nt":
        raise RunLockError("the daily process lock requires Windows msvcrt")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        raise ValueError("pid must be a positive integer")

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise RunLockError("the Windows process observer is unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        error = ctypes.get_last_error()
        if error in {87, 1168}:  # ERROR_INVALID_PARAMETER / ERROR_NOT_FOUND
            raise ProcessLookupError(error, "OpenProcess process not found", pid)
        if error == 5:  # ERROR_ACCESS_DENIED is not evidence that the process exited.
            raise PermissionError(error, "OpenProcess access denied", pid)
        raise OSError(error, "OpenProcess failed", pid)
    try:
        created = FileTime()
        exited = FileTime()
        kernel = FileTime()
        user = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            error = ctypes.get_last_error()
            if error in {87, 1168}:
                raise ProcessLookupError(
                    error, "GetProcessTimes process not found", pid
                )
            if error == 5:
                raise PermissionError(error, "GetProcessTimes access denied", pid)
            raise OSError(error, "GetProcessTimes failed", pid)
    finally:
        kernel32.CloseHandle(handle)

    ticks = (int(created.high) << 32) | int(created.low)
    unix_seconds = (ticks - 116_444_736_000_000_000) / 10_000_000
    return datetime.fromtimestamp(unix_seconds, timezone.utc).isoformat(
        timespec="microseconds"
    )


def _windows_process_tree(parent_pid: int) -> tuple[int, ...]:
    """Snapshot process ancestry with Toolhelp; return descendant PIDs only."""

    if os.name != "nt":
        raise RunLockError("the process tree observer requires Windows")

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_int32),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ProcessEntry32W),
    ]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ProcessEntry32W),
    ]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    invalid_handle = ctypes.c_void_p(-1).value
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snapshot or snapshot == invalid_handle:
        raise OSError(ctypes.get_last_error(), "process snapshot unavailable")
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            error = ctypes.get_last_error()
            if error == 18:  # ERROR_NO_MORE_FILES
                return ()
            raise OSError(error, "process snapshot could not be read")
        parents: dict[int, int] = {}
        while True:
            pid = int(entry.th32ProcessID)
            parents[pid] = int(entry.th32ParentProcessID)
            entry.dwSize = ctypes.sizeof(ProcessEntry32W)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != 18:  # ERROR_NO_MORE_FILES
                    raise OSError(error, "process snapshot ended unexpectedly")
                break
        descendants: set[int] = set()
        frontier = {parent_pid}
        while frontier:
            children = {
                pid
                for pid, parent in parents.items()
                if parent in frontier and pid != parent_pid
            }
            children.difference_update(descendants)
            if not children:
                break
            descendants.update(children)
            frontier = children
            if len(descendants) > 4096:
                raise RunLockError("process tree exceeds the observation limit")
        return tuple(sorted(descendants))
    finally:
        kernel32.CloseHandle(snapshot)


def observe_process_tree(
    parent_pid: int,
    *,
    process_tree_reader: Callable[[int], tuple[int, ...]] | None = None,
) -> ProcessTreeObservation:
    """Observe descendants without terminating or changing any process."""

    if (
        isinstance(parent_pid, bool)
        or not isinstance(parent_pid, int)
        or parent_pid < 1
    ):
        return ProcessTreeObservation("unknown", 0, (), "parent_pid_invalid")
    reader = process_tree_reader or _windows_process_tree
    try:
        raw_children = reader(parent_pid)
    except (
        AttributeError,
        OSError,
        RunLockError,
        TypeError,
        ValueError,
        OverflowError,
    ):
        return ProcessTreeObservation(
            "unknown", parent_pid, (), "process_tree_observation_failed"
        )
    if not isinstance(raw_children, (tuple, list)) or len(raw_children) > 4096:
        return ProcessTreeObservation("unknown", parent_pid, (), "process_tree_invalid")
    children: set[int] = set()
    for child_pid in raw_children:
        if (
            isinstance(child_pid, bool)
            or not isinstance(child_pid, int)
            or child_pid < 1
            or child_pid == parent_pid
        ):
            return ProcessTreeObservation(
                "unknown", parent_pid, (), "process_tree_invalid"
            )
        children.add(child_pid)
    return ProcessTreeObservation(
        "observed", parent_pid, tuple(sorted(children)), "process_tree_observed"
    )


def _read_owner(path: Path) -> LockOwner | None:
    try:
        return LockOwner.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _write_owner_atomically(path: Path, owner: LockOwner) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            json.dump(
                owner.to_dict(), temporary, ensure_ascii=False, separators=(",", ":")
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


class RunLock:
    """Keep one non-replaced lock file open for the lifetime of its owner."""

    def __init__(self, handle, lock_path: Path, owner_path: Path, owner: LockOwner):
        self._handle = handle
        self.lock_path = lock_path
        self.owner_path = owner_path
        self.owner = owner
        self._released = False

    @classmethod
    def acquire(
        cls,
        lock_path: str | Path,
        owner_path: str | Path,
        *,
        run_id: str,
        pid: int | None = None,
        started_at: str | None = None,
    ) -> Self:
        """Acquire byte zero without waiting and atomically publish its owner."""

        if os.name != "nt":
            raise RunLockError("the daily process lock requires Windows msvcrt")
        if (
            not isinstance(run_id, str)
            or not run_id.strip()
            or run_id != run_id.strip()
        ):
            raise ValueError("run_id must be a non-empty identifier")
        actual_pid = os.getpid() if pid is None else pid
        actual_start = started_at or process_start_time(actual_pid)
        owner = LockOwner(actual_pid, actual_start, run_id)
        if LockOwner.from_dict(owner.to_dict()) != owner:
            raise ValueError("lock owner metadata is invalid")

        lock = Path(lock_path).expanduser().resolve()
        owner_file = Path(owner_path).expanduser().resolve()
        lock.parent.mkdir(parents=True, exist_ok=True)
        owner_file.parent.mkdir(parents=True, exist_ok=True)
        pending_marker = tempfile.NamedTemporaryFile(
            mode="wb",
            dir=owner_file.parent,
            prefix=f"{owner_file.name}.pending.",
            delete=True,
        )
        lock_handle = None
        lock_acquired = False
        owner_publish_attempted = False
        acquisition_error: BaseException | None = None
        try:
            pending_marker.flush()
            os.fsync(pending_marker.fileno())
            lock_handle = lock.open("a+b")
            if lock.stat().st_size == 0:
                lock_handle.write(b"\0")
                lock_handle.flush()
                os.fsync(lock_handle.fileno())
            import msvcrt

            lock_handle.seek(0)
            try:
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RunLockBusy(_read_owner(owner_file)) from exc
            lock_acquired = True
            owner_publish_attempted = True
            _write_owner_atomically(owner_file, owner)
        except BaseException as exc:
            acquisition_error = exc

        marker_close_error: OSError | None = None
        try:
            pending_marker.close()
        except OSError as exc:
            marker_close_error = exc

        cleanup_errors: list[OSError] = []
        if marker_close_error is not None:
            try:
                Path(pending_marker.name).unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_errors.append(exc)

        should_release_lock = (
            marker_close_error is not None or acquisition_error is not None
        )
        if should_release_lock:
            if (
                marker_close_error is None
                and acquisition_error is not None
                and lock_acquired
                and owner_publish_attempted
                and _read_owner(owner_file) == owner
            ):
                try:
                    owner_file.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    cleanup_errors.append(exc)
            if lock_acquired and lock_handle is not None:
                try:
                    import msvcrt

                    lock_handle.seek(0)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError as exc:
                    cleanup_errors.append(exc)
            if lock_handle is not None:
                try:
                    lock_handle.close()
                except OSError as exc:
                    cleanup_errors.append(exc)

        if cleanup_errors:
            if marker_close_error is not None:
                raise RunLockError(
                    "failed to clean up the daily lock after closing its marker"
                ) from marker_close_error
            raise RunLockError(
                "failed to clean up a failed daily lock acquisition"
            ) from cleanup_errors[0]
        if marker_close_error is not None:
            raise RunLockError(
                "could not close the pending daily lock marker"
            ) from marker_close_error
        if acquisition_error is not None:
            raise acquisition_error.with_traceback(acquisition_error.__traceback__)
        assert lock_handle is not None
        return cls(lock_handle, lock, owner_file, owner)

    def release(self) -> None:
        if self._released:
            return
        import msvcrt

        try:
            stored_owner = _read_owner(self.owner_path)
            if stored_owner == self.owner:
                try:
                    self.owner_path.unlink()
                except FileNotFoundError:
                    pass
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._released = True
            self._handle.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()
