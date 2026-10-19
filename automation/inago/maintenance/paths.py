"""Resolve the maintenance database without opening the daily database."""

from __future__ import annotations

import stat
from pathlib import Path


class UnsafeMaintenancePathError(ValueError):
    """The maintenance state path is missing, aliased, or escapes its root."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _has_reparse_point(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise UnsafeMaintenancePathError("maintenance_path_uninspectable") from exc
    if stat.S_ISLNK(mode):
        return True
    try:
        file_attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(file_attributes & reparse_flag)


def resolve_maintenance_database_path(
    data_root: str | Path | None,
    *,
    daily_database_path: str | Path | None = None,
) -> Path:
    """Return ``<data_root>/maintenance/state.sqlite`` after isolation checks.

    The function never creates directories or opens either database. Callers
    must validate all runtime inputs before ``MaintenanceStore.initialize``.
    """

    if data_root is None or not str(data_root).strip():
        raise UnsafeMaintenancePathError("data_root_required")
    root_input = Path(data_root).expanduser()
    if _has_reparse_point(root_input):
        raise UnsafeMaintenancePathError("data_root_reparse_point")
    try:
        root = root_input.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafeMaintenancePathError("data_root_invalid") from exc
    if root.exists() and not root.is_dir():
        raise UnsafeMaintenancePathError("data_root_not_directory")

    maintenance_dir = root / "maintenance"
    database_path = maintenance_dir / "state.sqlite"
    if _has_reparse_point(maintenance_dir) or _has_reparse_point(database_path):
        raise UnsafeMaintenancePathError("maintenance_path_reparse_point")
    try:
        resolved_database = database_path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafeMaintenancePathError("maintenance_path_invalid") from exc
    try:
        resolved_database.relative_to(root)
    except ValueError as exc:
        raise UnsafeMaintenancePathError("maintenance_path_outside_data_root") from exc
    if resolved_database == root:
        raise UnsafeMaintenancePathError("maintenance_path_equals_data_root")

    if daily_database_path is not None:
        daily_input = Path(daily_database_path).expanduser()
        if _has_reparse_point(daily_input):
            raise UnsafeMaintenancePathError("daily_database_reparse_point")
        try:
            daily_resolved = daily_input.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise UnsafeMaintenancePathError("daily_database_path_invalid") from exc
        if resolved_database == daily_resolved:
            raise UnsafeMaintenancePathError("maintenance_database_aliases_daily")
        if resolved_database.exists() and daily_resolved.exists():
            try:
                if resolved_database.samefile(daily_resolved):
                    raise UnsafeMaintenancePathError(
                        "maintenance_database_aliases_daily"
                    )
            except OSError as exc:
                raise UnsafeMaintenancePathError(
                    "database_identity_unverified"
                ) from exc

    return resolved_database


def resolve_maintenance_settings_path(
    data_root: str | Path | None,
    *,
    daily_database_path: str | Path | None = None,
) -> Path:
    """Return the isolated runtime settings path beside the management DB."""

    database_path = resolve_maintenance_database_path(
        data_root,
        daily_database_path=daily_database_path,
    )
    return database_path.with_name("settings.json")


def resolve_maintenance_failure_journal_path(
    data_root: str | Path | None, *, filename: str = "events.jsonl"
) -> Path:
    """Resolve the independent fallback journal below the maintenance root."""

    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in filename)
    ):
        raise UnsafeMaintenancePathError("failure_journal_filename_invalid")
    database_path = resolve_maintenance_database_path(data_root)
    journal = database_path.parent / "failure-journal" / filename
    for candidate in (journal.parent, journal):
        if _has_reparse_point(candidate):
            raise UnsafeMaintenancePathError("failure_journal_reparse_point")
    try:
        resolved = journal.resolve(strict=False)
        resolved.relative_to(database_path.parent.resolve(strict=False))
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafeMaintenancePathError("failure_journal_path_invalid") from exc
    return resolved
