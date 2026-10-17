"""Fail-closed process identity observations for watchdog reconciliation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    state: str
    start_time: str | None

    def __post_init__(self) -> None:
        if self.state not in {"running", "stopped", "unknown"}:
            raise ValueError("process_observation_state_invalid")
        if self.state != "running" and self.start_time is not None:
            raise ValueError("process_observation_start_time_invalid")


def _windows_process_observation(pid: int) -> ProcessObservation:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    class FileTime(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
        0x1000, False, pid
    )  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        error = ctypes.get_last_error()
        if error in {6, 87, 1168}:  # invalid handle/parameter or process not found
            return ProcessObservation("stopped", None)
        return ProcessObservation("unknown", None)
    try:
        creation = FileTime()
        exit_time = FileTime()
        kernel = FileTime()
        user = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return ProcessObservation("unknown", None)
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        started = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(
            microseconds=ticks // 10
        )
        return ProcessObservation("running", started.isoformat())
    finally:
        kernel32.CloseHandle(handle)


def observe_process(pid: int) -> ProcessObservation:
    """Observe a PID without terminating it or guessing about access failures."""

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return ProcessObservation("unknown", None)
    if os.name == "nt":
        try:
            return _windows_process_observation(pid)
        except (AttributeError, OSError, ValueError):
            return ProcessObservation("unknown", None)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return ProcessObservation("stopped", None)
    except PermissionError:
        return ProcessObservation("unknown", None)
    except OSError:
        return ProcessObservation("unknown", None)
    # POSIX kill(pid, 0) cannot prove process creation identity on all hosts.
    return ProcessObservation("unknown", None)


def current_process_start_time() -> str | None:
    """Return the current process creation time when the host can prove it."""

    observation = observe_process(os.getpid())
    return observation.start_time if observation.state == "running" else None
