from pathlib import Path

from automation.inago.maintenance.deployment import (
    FileDeploymentExclusionGuard,
)


def test_deployment_exclusion_lock_serializes_runtime_switch(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()

    first = FileDeploymentExclusionGuard(runtime_root / ".maintenance-deployment.lock")
    second = FileDeploymentExclusionGuard(runtime_root / ".maintenance-deployment.lock")
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire()
    second.release()
