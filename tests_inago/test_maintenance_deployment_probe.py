import sqlite3
from pathlib import Path

from automation.inago.maintenance.deployment import (
    FileDeploymentExclusionGuard,
    observe_daily_owner_active,
    observe_pending_action_snapshot,
)


def test_read_only_deployment_probes_and_shared_exclusion_lock(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    database_path = runtime_root / "state.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE human_action (action_id TEXT, action TEXT, offer_key TEXT, "
            "receipt_revision INTEGER, apply_status TEXT)"
        )

    assert observe_daily_owner_active(runtime_root) is False
    empty = observe_pending_action_snapshot(runtime_root)
    assert empty is not None and empty.count == 0

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO human_action VALUES (?, ?, ?, ?, 'pending')",
            ("action-1", "posted", "offer-1", 2),
        )
    pending = observe_pending_action_snapshot(runtime_root)
    assert pending is not None and pending.count == 1
    assert pending.revision != empty.revision

    first = FileDeploymentExclusionGuard(runtime_root / ".maintenance-deployment.lock")
    second = FileDeploymentExclusionGuard(runtime_root / ".maintenance-deployment.lock")
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire()
    second.release()
