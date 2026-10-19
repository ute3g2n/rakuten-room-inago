"""Validated, maintenance-only runtime settings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from automation.inago.maintenance.paths import _has_reparse_point


class MaintenanceSettingsError(ValueError):
    """A safe error code for absent or invalid maintenance settings."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MaintenanceSettings:
    """Approved settings for maintenance jobs; these do not configure daily runs."""

    schema_version: int = 1
    settings_version: str = "1.0"
    timezone: str = "Asia/Tokyo"
    code_fix_concurrency: int = 1
    lease_heartbeat_seconds: int = 30
    watchdog_interval_seconds: int = 60
    ai_job_timeout_seconds: int = 1200
    cancellation_confirmation_timeout_seconds: int = 60
    request_retry_limit: int = 2
    review_round_limit: int = 3
    job_resume_limit: int = 3
    diagnostic_log_retention_days: int = 90
    diagnostic_log_max_file_bytes: int = 4 * 1024 * 1024


_DEFAULT_SETTINGS = MaintenanceSettings()
_SETTING_KEYS = frozenset(asdict(_DEFAULT_SETTINGS))
_LEGACY_LOG_FILE_COUNT_KEY = "diagnostic_log_max_files_per_day"
_LEGACY_LOG_FILE_COUNT_VALUE = 20
_LEGACY_SETTING_KEYS = _SETTING_KEYS | {_LEGACY_LOG_FILE_COUNT_KEY}
_MAX_SETTINGS_BYTES = 16 * 1024


def default_settings_document() -> dict[str, Any]:
    """Return a JSON-ready copy of the adopted maintenance settings."""

    return asdict(_DEFAULT_SETTINGS)


def validate_settings_document(value: object) -> MaintenanceSettings:
    """Accept only the exact implemented schema and approved initial values."""

    if not isinstance(value, dict):
        raise MaintenanceSettingsError("settings_schema_invalid")
    normalized = dict(value)
    if set(normalized) == _LEGACY_SETTING_KEYS:
        legacy_limit = normalized.pop(_LEGACY_LOG_FILE_COUNT_KEY)
        if (
            type(legacy_limit) is not int
            or legacy_limit != _LEGACY_LOG_FILE_COUNT_VALUE
        ):
            raise MaintenanceSettingsError("settings_value_unapproved")
    elif set(normalized) != _SETTING_KEYS:
        raise MaintenanceSettingsError("settings_schema_invalid")
    expected = default_settings_document()
    if any(type(normalized[key]) is not type(expected[key]) for key in _SETTING_KEYS):
        raise MaintenanceSettingsError("settings_value_type_invalid")
    if normalized != expected:
        raise MaintenanceSettingsError("settings_value_unapproved")
    return MaintenanceSettings(**normalized)


def load_settings(path: str | Path) -> MaintenanceSettings:
    """Read and validate the private settings file without exposing its contents."""

    settings_path = Path(path)
    if _has_reparse_point(settings_path):
        raise MaintenanceSettingsError("settings_path_reparse_point")
    try:
        with settings_path.open("rb") as stream:
            content = stream.read(_MAX_SETTINGS_BYTES + 1)
    except OSError as exc:
        raise MaintenanceSettingsError("settings_file_unavailable") from exc
    if len(content) > _MAX_SETTINGS_BYTES:
        raise MaintenanceSettingsError("settings_file_too_large")
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaintenanceSettingsError("settings_json_invalid") from exc
    return validate_settings_document(document)


def ensure_settings_file(path: str | Path) -> MaintenanceSettings:
    """Create the initial settings once or validate the existing file."""

    settings_path = Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        return load_settings(settings_path)
    payload = json.dumps(
        default_settings_document(), ensure_ascii=False, indent=2, sort_keys=True
    )
    try:
        with settings_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.write("\n")
    except FileExistsError:
        return load_settings(settings_path)
    except OSError as exc:
        raise MaintenanceSettingsError("settings_write_failed") from exc
    return _DEFAULT_SETTINGS
