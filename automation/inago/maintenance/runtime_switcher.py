"""Retired Task Scheduler code-root switch API.

The current maintenance flow applies the reviewed local changes at the fixed
repository root. This compatibility type cannot change Scheduler state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from automation.inago.maintenance.deployment import DeploymentError


class TaskSchedulerRuntimeSwitcher:
    """Compatibility tombstone for the retired Scheduler-switch workflow.

    The prior implementation rejected a dirty fixed root; that gate and the
    broader Task Scheduler mutation path are retired. The local seven-role
    flow permits the operator's current root changes and never calls this API.
    """

    def __init__(self, **_: Any) -> None:
        pass

    def current_commit(self) -> str | None:
        return None

    def activate(self, commit: str, checkout: Path | None = None) -> None:
        _ = commit, checkout
        raise DeploymentError("legacy_runtime_switch_retired")

    def healthy(self, expected_commit: str) -> bool:
        _ = expected_commit
        return False
