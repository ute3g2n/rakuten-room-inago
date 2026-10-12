"""Explicit path resolution and fixture write-boundary checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAFE_FIXTURE_BASE = PROJECT_ROOT / "非公開作業データ" / "イナゴ実装試験"
FIXTURE_CONFIG_BASE = PROJECT_ROOT / "tests_inago" / "config"


class PathContractError(ValueError):
    """Raised when a required runtime path is absent or unusable."""


class UnsafePathError(PathContractError):
    """Raised when fixture mode points outside its isolated write area."""


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Resolved inputs passed to runtime components without creating files."""

    workspace_root: Path
    data_root: Path
    config_path: Path


def _required_path(value: str | Path | None, name: str) -> Path:
    if value is None or not str(value).strip():
        raise PathContractError(f"{name} is required")

    try:
        return Path(value).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathContractError(f"{name} cannot be resolved") from exc


def _is_strict_child(path: Path, parent: Path) -> bool:
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return False
    return bool(relative.parts)


def resolve_runtime_paths(
    *,
    data_root: str | Path | None,
    config_path: str | Path | None,
    fixture_mode: bool,
) -> RuntimePaths:
    """Resolve required paths and constrain fixtures to repository test paths.

    The function is intentionally read-only. Callers must create a data root
    only after validation and only through the storage component that owns it.
    """

    workspace_root = PROJECT_ROOT.resolve()
    resolved_data_root = _required_path(data_root, "data_root")
    resolved_config_path = _required_path(config_path, "config_path")

    if fixture_mode:
        if not _is_strict_child(resolved_data_root, SAFE_FIXTURE_BASE.resolve()):
            raise UnsafePathError(
                "fixture data_root must stay under the private test area"
            )
        if not _is_strict_child(resolved_config_path, FIXTURE_CONFIG_BASE.resolve()):
            raise UnsafePathError(
                "fixture config_path must stay under tests_inago/config"
            )
        if not resolved_config_path.is_file():
            raise PathContractError("fixture config_path must name an existing file")

    return RuntimePaths(
        workspace_root=workspace_root,
        data_root=resolved_data_root,
        config_path=resolved_config_path,
    )
