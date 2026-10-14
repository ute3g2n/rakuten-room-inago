"""Windows process lock with separately inspectable heartbeat metadata."""

import ctypes
import json
import msvcrt
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.atomic_io import atomic_write_json


JST = timezone(timedelta(hours=9))


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


class LockBusy(RuntimeError):
    code = "LOCKED"
    def __init__(self, stale_candidate: bool):
        super().__init__("run lock is held")
        self.stale_candidate = stale_candidate


class RunLock:
    def __init__(self, path: Path, run_id: str):
        self.path = Path(path)
        self.metadata_path = self.path.with_suffix(self.path.suffix + ".meta.json")
        self.run_id = run_id
        self._stream = None

    @staticmethod
    def inspect_stale(path: Path, stale_minutes: int = 15) -> bool:
        path = Path(path)
        metadata = path.with_suffix(path.suffix + ".meta.json")
        source = metadata if metadata.exists() else path
        try:
            info = json.loads(source.read_text(encoding="utf-8"))
            heartbeat = datetime.fromisoformat(info["heartbeat_at"])
            if heartbeat.tzinfo is None:
                return False
            old = datetime.now(JST) - heartbeat > timedelta(minutes=stale_minutes)
            return old and not _pid_exists(int(info["pid"]))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                self._stream.write(b"\0")
                self._stream.flush()
            self._stream.seek(0)
            msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            self._stream.close()
            self._stream = None
            raise LockBusy(self.inspect_stale(self.path)) from error
        self.heartbeat()

    def heartbeat(self) -> None:
        if self._stream is None:
            raise RuntimeError("lock is not held")
        atomic_write_json(self.metadata_path, {"run_id": self.run_id, "pid": os.getpid(), "heartbeat_at": datetime.now(JST).isoformat(timespec="seconds")})

    def release(self) -> None:
        if self._stream is not None:
            self._stream.seek(0)
            msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            self._stream.close()
            self._stream = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, _type, _value, _traceback):
        self.release()
