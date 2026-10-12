"""JST wall time and monotonic deadline clocks for deterministic runs."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


class Clock(Protocol):
    """Clock operations shared by production and deterministic test clocks."""

    def now_jst(self) -> datetime:
        """Return the current aware datetime in Japan Standard Time."""

    def monotonic(self) -> float:
        """Return a process-monotonic counter for durations and deadlines."""

    def deadline_remaining(self, deadline: float) -> float:
        """Return non-negative seconds until a monotonic deadline."""

    def wait(self, seconds: float) -> None:
        """Wait for seconds; test clocks advance without sleeping."""


def _validate_seconds(seconds: float) -> float:
    value = float(seconds)
    if not math.isfinite(value) or value < 0:
        raise ValueError("seconds must be a finite non-negative number")
    return value


class SystemClock:
    """System clock using aware JST and a monotonic duration source."""

    def now_jst(self) -> datetime:
        return datetime.now(timezone.utc).astimezone(JST)

    def monotonic(self) -> float:
        return time.monotonic()

    def deadline_remaining(self, deadline: float) -> float:
        return max(0.0, float(deadline) - self.monotonic())

    def wait(self, seconds: float) -> None:
        duration = _validate_seconds(seconds)
        if duration:
            time.sleep(duration)


@dataclass(slots=True)
class FakeClock:
    """Deterministic clock whose waits advance simulated time immediately."""

    current: datetime
    monotonic_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.current.tzinfo is None or self.current.utcoffset() is None:
            raise ValueError("FakeClock requires a timezone-aware datetime")
        self.current = self.current.astimezone(JST)
        self.monotonic_seconds = _validate_seconds(self.monotonic_seconds)

    def now_jst(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.monotonic_seconds

    def deadline_remaining(self, deadline: float) -> float:
        return max(0.0, float(deadline) - self.monotonic())

    def advance(self, seconds: float) -> None:
        duration = _validate_seconds(seconds)
        self.current += timedelta(seconds=duration)
        self.monotonic_seconds += duration

    def wait(self, seconds: float) -> None:
        self.advance(seconds)
