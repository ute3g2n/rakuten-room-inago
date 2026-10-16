"""Fixture-only bridge for isolated daily-run probes.

This module does not load production settings or open production roots. It
accepts a dedicated ``isolated_probe`` document, derives every writable path
under the private test area, and delegates to the existing daily composition
and trigger functions.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from typing import Any, Callable

from jsonschema import Draft202012Validator, ValidationError

from automation.inago.daily_runtime import compose_public_daily_ports
from automation.inago.trigger import run_scheduled_daily


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROBE_TEST_ROOT = PROJECT_ROOT / "非公開作業データ" / "イナゴ実装試験"
PROBE_SCHEMA_PATH = Path(__file__).parent / "schemas" / "daily-probe.schema.json"
PRODUCTION_RUNTIME_ROOT = PROJECT_ROOT / "rakuten-room-inago" / "runtime"
PRODUCTION_BACKUP_ROOT = PROJECT_ROOT / "rakuten-room-inago" / "backups"
_PROBE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REPARSE_POINT_ATTRIBUTE = 0x400


class DailyProbeConfigError(ValueError):
    """Raised when a probe setting or write boundary is unsafe."""


@dataclass(frozen=True, slots=True)
class DailyProbeConfig:
    """Validated probe settings with write paths derived from one private root."""

    config_path: Path
    probe_root: Path
    probe_id: str
    schema_version: int = 1
    environment: str = "isolated_probe"
    network_allowed: bool = False
    test_only: bool = True

    @property
    def data_root(self) -> Path:
        return self.probe_root / "data"

    @property
    def database_path(self) -> Path:
        return self.data_root / "state.sqlite"

    @property
    def backup_root(self) -> Path:
        return self.probe_root / "backups"

    @property
    def artifacts_root(self) -> Path:
        return self.probe_root / "artifacts"

    @property
    def observations_root(self) -> Path:
        return self.data_root / "observations"

    @property
    def logs_root(self) -> Path:
        return self.data_root / "logs"

    @property
    def reports_root(self) -> Path:
        return self.probe_root / "reports"

    @property
    def restore_root(self) -> Path:
        return self.probe_root / "restore-check"

    @property
    def write_roots(self) -> tuple[Path, ...]:
        return (
            self.data_root,
            self.backup_root,
            self.artifacts_root,
            self.observations_root,
            self.logs_root,
            self.reports_root,
            self.restore_root,
        )


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
        os.path.normpath(str(right))
    )


def _is_strict_child(path: Path, parent: Path) -> bool:
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return False
    return bool(relative.parts) and all(part not in {".", ".."} for part in relative.parts)


def _assert_no_reparse_points(path: Path, parent: Path) -> None:
    """Reject links/junctions from the candidate back to its trusted parent."""

    cursor = path.absolute()
    floor = parent.absolute()
    if not _is_strict_child(cursor, floor):
        raise DailyProbeConfigError("probe path must stay below its private parent")
    while True:
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            info = None
        except OSError as exc:
            raise DailyProbeConfigError("probe path cannot be inspected") from exc
        if info is not None:
            attributes = getattr(info, "st_file_attributes", 0)
            if cursor.is_symlink() or attributes & _REPARSE_POINT_ATTRIBUTE:
                raise DailyProbeConfigError("probe path cannot contain a link or junction")
        if _same_path(cursor, floor):
            return
        if not _is_strict_child(cursor, floor):
            raise DailyProbeConfigError("probe path escaped its private parent")
        cursor = cursor.parent


def _resolved_strict_child(
    value: str | Path,
    parent: Path,
    *,
    name: str,
    require_file: bool = False,
) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute() or ".." in raw.parts:
        raise DailyProbeConfigError(f"{name} must be an absolute contained path")

    parent_absolute = parent.absolute()
    candidate_absolute = raw.absolute()
    if not _is_strict_child(candidate_absolute, parent_absolute):
        raise DailyProbeConfigError(f"{name} must stay under the isolated test area")
    _assert_no_reparse_points(candidate_absolute, parent_absolute)

    try:
        resolved_parent = parent_absolute.resolve(strict=False)
        resolved = candidate_absolute.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DailyProbeConfigError(f"{name} cannot be resolved") from exc
    if not _is_strict_child(resolved, resolved_parent):
        raise DailyProbeConfigError(f"{name} resolves outside the isolated test area")
    _assert_no_reparse_points(resolved, resolved_parent)
    if require_file and not resolved.is_file():
        raise DailyProbeConfigError(f"{name} must name an existing file")
    return resolved


def _assert_probe_test_root() -> Path:
    project_root = PROJECT_ROOT.resolve(strict=False)
    private_root = PROBE_TEST_ROOT.absolute()
    try:
        resolved_private_root = private_root.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DailyProbeConfigError("private test area cannot be resolved") from exc
    if not _same_path(private_root, resolved_private_root):
        raise DailyProbeConfigError("private test area cannot be redirected")
    _assert_no_reparse_points(private_root, project_root)
    return resolved_private_root


def _load_schema() -> dict[str, Any]:
    try:
        schema = json.loads(PROBE_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise DailyProbeConfigError("isolated probe schema is unavailable") from exc
    return schema


def _validate_config(config: DailyProbeConfig) -> DailyProbeConfig:
    private_root = _assert_probe_test_root()
    if type(config.schema_version) is not int or config.schema_version != 1:
        raise DailyProbeConfigError("unsupported isolated probe config version")
    if config.environment != "isolated_probe" or config.network_allowed is not False:
        raise DailyProbeConfigError("probe environment must be offline isolated_probe")
    if config.test_only is not True or not _PROBE_ID_PATTERN.fullmatch(config.probe_id):
        raise DailyProbeConfigError("probe config must be explicitly test-only")

    config_path = _resolved_strict_child(
        config.config_path,
        private_root,
        name="config_path",
        require_file=True,
    )
    probe_root = _resolved_strict_child(
        config.probe_root,
        private_root,
        name="probe_root",
    )
    for name, path in (
        ("runtime_root", PRODUCTION_RUNTIME_ROOT.resolve(strict=False)),
        ("backup_root", PRODUCTION_BACKUP_ROOT.resolve(strict=False)),
    ):
        if (
            _same_path(probe_root, path)
            or _is_strict_child(probe_root, path)
            or _is_strict_child(path, probe_root)
        ):
            raise DailyProbeConfigError(f"probe root overlaps production {name}")

    normalized = DailyProbeConfig(
        config_path=config_path,
        probe_root=probe_root,
        probe_id=config.probe_id,
        schema_version=config.schema_version,
        environment=config.environment,
        network_allowed=config.network_allowed,
        test_only=config.test_only,
    )
    for write_root in normalized.write_roots:
        _resolved_strict_child(
            write_root,
            normalized.probe_root,
            name="write_root",
        )
    return normalized


def load_daily_probe_config(config_path: str | Path) -> DailyProbeConfig:
    """Read and validate an isolated probe config without creating any paths."""

    private_root = _assert_probe_test_root()
    path = _resolved_strict_child(
        config_path,
        private_root,
        name="config_path",
        require_file=True,
    )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyProbeConfigError("isolated probe config is unreadable") from exc
    try:
        Draft202012Validator(_load_schema()).validate(document)
    except ValidationError as exc:
        raise DailyProbeConfigError("isolated probe config is invalid") from exc

    config = DailyProbeConfig(
        config_path=path,
        probe_root=Path(document["probe_root"]),
        probe_id=document["probe_id"],
        schema_version=document["schema_version"],
        environment=document["environment"],
        network_allowed=document["network_allowed"],
        test_only=document["test_only"],
    )
    return _validate_config(config)


def validate_daily_probe_config(config: DailyProbeConfig) -> DailyProbeConfig:
    """Recheck a loaded config and all derived writable paths before use."""

    if not isinstance(config, DailyProbeConfig):
        raise DailyProbeConfigError("a loaded DailyProbeConfig is required")
    return _validate_config(config)


def open_probe_database(config: DailyProbeConfig) -> sqlite3.Connection:
    """Open a new or existing schema-v5 database only under the validated probe."""

    selected = validate_daily_probe_config(config)
    from automation.inago.repository import open_database

    connection: sqlite3.Connection | None = None
    try:
        connection = open_database(SimpleNamespace(data_root=selected.data_root))
        assert_probe_database(connection, selected)
        return connection
    except Exception:
        if connection is not None:
            connection.close()
        raise


def assert_probe_database(
    connection: sqlite3.Connection, config: DailyProbeConfig
) -> Path:
    """Require the opened DB, foreign keys, WAL, and schema version to be probe-local."""

    selected = validate_daily_probe_config(config)
    try:
        rows = connection.execute("PRAGMA database_list").fetchall()
        main_path = next(
            (str(row[2]) for row in rows if str(row[1]) == "main"),
            "",
        )
    except sqlite3.Error as exc:
        raise DailyProbeConfigError("probe database cannot be inspected") from exc
    if not main_path:
        raise DailyProbeConfigError("probe database must be a file under the probe root")

    database_path = _resolved_strict_child(
        main_path,
        selected.data_root,
        name="database_path",
        require_file=True,
    )
    if not _same_path(database_path, selected.database_path.resolve(strict=False)):
        raise DailyProbeConfigError("probe connection must use its isolated database")
    try:
        foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        from automation.inago.repository import CURRENT_SCHEMA_VERSION, schema_version

        current_version = schema_version(connection)
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    except Exception as exc:
        raise DailyProbeConfigError("probe database contract cannot be verified") from exc
    if (
        foreign_keys != 1
        or journal_mode != "wal"
        or current_version != CURRENT_SCHEMA_VERSION
        or foreign_key_errors
    ):
        raise DailyProbeConfigError("probe database must satisfy the schema-v5 SQLite contract")
    return database_path


def _require_fixture_dependency(name: str, value: Any) -> None:
    if getattr(value, "fixture_only", False) is not True:
        raise DailyProbeConfigError(f"{name} must be explicitly fixture-only")


def compose_daily_probe_ports(
    *,
    config: DailyProbeConfig,
    source: Any,
    ai_root: Any,
    copy_spec_factory: Callable[..., Any],
    metrics_port: Any,
    cancel_metrics_job: Callable[[str], bool],
):
    """Compose the production daily ports after requiring fixture-only adapters."""

    selected = validate_daily_probe_config(config)
    for name, value in (
        ("public source", source),
        ("AI root", ai_root),
        ("copy spec factory", copy_spec_factory),
        ("metrics port", metrics_port),
        ("metrics cancellation", cancel_metrics_job),
    ):
        _require_fixture_dependency(name, value)
    try:
        ports = compose_public_daily_ports(
            data_root=selected.data_root,
            source=source,
            ai_root=ai_root,
            copy_spec_factory=copy_spec_factory,
            metrics_port=metrics_port,
            cancel_metrics_job=cancel_metrics_job,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise DailyProbeConfigError("fixture ports do not satisfy daily composition") from exc
    if ports.fixture_only is not True:
        raise DailyProbeConfigError("daily composition did not remain fixture-only")
    return ports


def run_daily_probe(
    connection: sqlite3.Connection,
    *,
    config: DailyProbeConfig,
    source: Any,
    ai_root: Any,
    copy_spec_factory: Callable[..., Any],
    metrics_port: Any,
    cancel_metrics_job: Callable[[str], bool],
    clock: Any | None = None,
    run_id_factory: Callable[..., str] | None = None,
    seed_factory: Callable[[], str] | None = None,
    on_draft_commit: Callable[[sqlite3.Connection], None] | None = None,
):
    """Run the shared scheduler trigger with isolated DB and fixture-only ports."""

    selected = validate_daily_probe_config(config)
    assert_probe_database(connection, selected)
    if on_draft_commit is not None:
        _require_fixture_dependency("draft commit callback", on_draft_commit)
    ports = compose_daily_probe_ports(
        config=selected,
        source=source,
        ai_root=ai_root,
        copy_spec_factory=copy_spec_factory,
        metrics_port=metrics_port,
        cancel_metrics_job=cancel_metrics_job,
    )
    return run_scheduled_daily(
        connection,
        data_root=selected.data_root,
        ports=ports,
        clock=clock,
        run_id_factory=run_id_factory,
        seed_factory=seed_factory,
        on_draft_commit=on_draft_commit,
    )
