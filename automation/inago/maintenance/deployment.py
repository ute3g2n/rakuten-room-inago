"""Small runtime switch operation helpers for the maintenance deployment role."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from automation.inago.maintenance.paths import _has_reparse_point


class DeploymentError(RuntimeError):
    """Safe operation error code without raw host output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RuntimeSwitcher(Protocol):
    def current_commit(self) -> str | None: ...

    def activate(self, commit: str, checkout: Path | None = None) -> None: ...

    def healthy(self, expected_commit: str) -> bool: ...


class FileDeploymentExclusionGuard:
    """Local lock for a single runtime switch operation."""

    def __init__(self, lock_path: str | Path) -> None:
        self.lock_path = Path(lock_path).expanduser()
        self._handle = None
        self._state_lock = threading.Lock()

    def acquire(self) -> bool:
        if self._handle is not None or not self._state_lock.acquire(blocking=False):
            return False
        handle = None
        try:
            if _has_reparse_point(self.lock_path):
                raise DeploymentError("deployment_exclusion_path_invalid")
            handle = self.lock_path.open("a+b")
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            elif os.name == "posix":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                raise DeploymentError("deployment_exclusion_unavailable")
        except OSError:
            if handle is not None:
                handle.close()
            self._state_lock.release()
            return False
        except (ImportError, DeploymentError):
            if handle is not None:
                handle.close()
            self._state_lock.release()
            raise
        self._handle = handle
        return True

    def __enter__(self) -> FileDeploymentExclusionGuard:
        if self._handle is not None:
            raise DeploymentError("deployment_exclusion_reentrant")
        while not self.acquire():
            time.sleep(0.1)
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif os.name == "posix":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                handle.close()
            finally:
                self._state_lock.release()


@dataclass(frozen=True, slots=True)
class RuntimeSwitchResult:
    status: str
    reason_code: str | None
    prior_runtime: str | None
    requested_runtime: str
    active_runtime: str | None


def switch_runtime_with_health_rollback(
    switcher: RuntimeSwitcher, requested_runtime: str
) -> RuntimeSwitchResult:
    """Perform one switch; roll back only when this operation leaves runtime unhealthy."""
    prior = switcher.current_commit()
    try:
        switcher.activate(requested_runtime)
        healthy = switcher.healthy(requested_runtime)
    except Exception as exc:
        reason = getattr(exc, "code", None) or type(exc).__name__.lower()
        active = switcher.current_commit()
        if prior is not None:
            try:
                switcher.activate(prior)
                active = switcher.current_commit()
            except Exception:
                return RuntimeSwitchResult("failed", "runtime_switch_and_rollback_failed", prior, requested_runtime, switcher.current_commit())
        return RuntimeSwitchResult("failed", str(reason), prior, requested_runtime, active)
    if healthy:
        return RuntimeSwitchResult("switched", None, prior, requested_runtime, switcher.current_commit())
    if prior is None:
        return RuntimeSwitchResult("failed", "health_check_failed_rollback_unavailable", None, requested_runtime, switcher.current_commit())
    try:
        switcher.activate(prior)
    except Exception:
        return RuntimeSwitchResult("failed", "health_check_failed_rollback_failed", prior, requested_runtime, switcher.current_commit())
    return RuntimeSwitchResult("rolled_back", "health_check_failed", prior, requested_runtime, switcher.current_commit())
