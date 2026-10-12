"""Windows process-shared byte lock for one daily run at a time."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Self


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

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
        raise OSError(ctypes.get_last_error(), "OpenProcess failed", pid)
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
            raise OSError(ctypes.get_last_error(), "GetProcessTimes failed", pid)
    finally:
        kernel32.CloseHandle(handle)

    ticks = (int(created.high) << 32) | int(created.low)
    unix_seconds = (ticks - 116_444_736_000_000_000) / 10_000_000
    return datetime.fromtimestamp(unix_seconds, timezone.utc).isoformat(
        timespec="microseconds"
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
        lock_handle = lock.open("a+b")
        try:
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
            try:
                _write_owner_atomically(owner_file, owner)
            except BaseException:
                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                raise
        except BaseException:
            lock_handle.close()
            raise
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
