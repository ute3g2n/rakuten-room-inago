"""Compatibility entry point for the current seven-role maintenance runner.

The former multi-stage provider and its repair/report hash contracts are retired.
Historical rows remain in storage; new work enters through the skill-chain runner.
"""

from __future__ import annotations

from pathlib import Path

from automation.inago.maintenance.store import MaintenanceStore


def build_production_workflow_provider(
    store: MaintenanceStore,
    *,
    runtime_switcher: object | None = None,
    deployment_coordinator: object | None = None,
    diagnostic_trace: object | None = None,
) -> object:
    """Delegate old imports to the current seven-role runner."""

    _ = runtime_switcher, deployment_coordinator, diagnostic_trace
    from automation.inago.maintenance.skill_chain import (
        build_skill_chain_workflow_provider,
    )

    return build_skill_chain_workflow_provider(
        store, repository_root=Path(__file__).resolve().parents[3]
    )
