"""Validate repository-local Windows runtime configuration for Inago production."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable, Mapping
from urllib.parse import urlsplit

from automation.inago.clock import Clock
from automation.inago.paths import PROJECT_ROOT
from automation.inago.settings import (
    BASE_SETTINGS_VERSION,
    DETAIL_CANDIDATE_VERSION,
    SettingsRuntime,
)

if TYPE_CHECKING:
    from automation.inago.copy_service import CopyGenerationSpec


class ProductionRuntimeError(ValueError):
    """A production path or setting is missing, unsafe, or inconsistent."""


@dataclass(frozen=True, slots=True)
class ProductionRuntimeConfig:
    user_profile_root: Path = field(repr=False)
    application_root: Path
    runtime_root: Path
    backup_root: Path
    owner_room_url: str = field(repr=False)
    start_date: str
    adopted_settings: Mapping[str, Any] = field(repr=False)
    detailed_rules: Mapping[str, Any] = field(repr=False)
    chrome_executable: Path
    user_data_dir: Path
    node_executable: str
    codex_command: tuple[str, ...]
    affiliate_auth_state: Path = field(repr=False)
    legacy_source_root: Path = field(repr=False)
    legacy_paths: Mapping[str, str] = field(repr=False)
    human_api_port: int

    @property
    def detailed_rules_enabled(self) -> bool:
        return False

    @property
    def metrics_output_root(self) -> Path:
        return self.runtime_root / "artifacts" / "metrics"

    @property
    def codex_home(self) -> Path:
        return self.user_profile_root / ".codex"


def _has_reparse_point(path: Path) -> bool:
    cursor = path
    while True:
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ProductionRuntimeError("runtime path cannot be inspected") from exc
        else:
            attributes = getattr(info, "st_file_attributes", 0)
            if cursor.is_symlink() or attributes & 0x400:
                return True
        if cursor.parent == cursor:
            return False
        cursor = cursor.parent


def _absolute_path(value: object, name: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ProductionRuntimeError(f"{name} is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ProductionRuntimeError(f"{name} must be absolute")
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProductionRuntimeError(f"{name} cannot be resolved") from exc


def _lexical_absolute_path(value: object, name: str) -> Path:
    """Normalize an absolute path without following a symlink or junction."""

    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ProductionRuntimeError(f"{name} is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ProductionRuntimeError(f"{name} must be absolute")
    try:
        return Path(os.path.abspath(os.path.normpath(str(path))))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProductionRuntimeError(f"{name} cannot be normalized") from exc


def _same_path(left: Path, right: Path) -> bool:
    left_text = os.path.normcase(os.path.abspath(os.path.normpath(str(left))))
    right_text = os.path.normcase(os.path.abspath(os.path.normpath(str(right))))
    return left_text == right_text


def _checked_private_path(value: object, name: str) -> Path:
    """Reject redirection in the lexical path before returning a resolved path."""

    lexical = _lexical_absolute_path(value, name)
    if _has_reparse_point(lexical):
        raise ProductionRuntimeError(f"{name} cannot contain reparse points")
    try:
        return lexical.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProductionRuntimeError(f"{name} cannot be resolved") from exc


def _approved_chrome_executable(value: object, local_root: Path) -> Path:
    """Accept only the standard per-user or machine Chrome installation paths."""

    lexical = _lexical_absolute_path(value, "chrome_executable")
    approved_roots = [local_root]
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        configured = os.environ.get(variable)
        if configured:
            candidate = Path(configured).expanduser()
            if candidate.is_absolute():
                approved_roots.append(candidate)
    approved_paths = [
        root / "Google" / "Chrome" / "Application" / "chrome.exe"
        for root in approved_roots
    ]
    if not any(_same_path(lexical, candidate) for candidate in approved_paths):
        raise ProductionRuntimeError(
            "Chrome must use a standard per-user or machine installation path"
        )
    if _has_reparse_point(lexical):
        raise ProductionRuntimeError("Chrome paths cannot contain reparse points")
    try:
        resolved = lexical.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProductionRuntimeError("chrome_executable cannot be resolved") from exc
    if not resolved.is_file():
        raise ProductionRuntimeError("Chrome executable is unavailable")
    return resolved


def _owner_url(value: object) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ProductionRuntimeError("owner ROOM URL is required")
    parts = urlsplit(value)
    components = [part for part in parts.path.split("/") if part]
    if (
        parts.scheme != "https"
        or parts.hostname != "room.rakuten.co.jp"
        or parts.username
        or parts.password
        or parts.port not in {None, 443}
        or parts.query
        or parts.fragment
        or len(components) != 2
        or not re.fullmatch(r"room_[a-f0-9]{8,64}", components[0])
        or components[1] != "items"
    ):
        raise ProductionRuntimeError(
            "owner ROOM URL is outside the public profile contract"
        )
    return value


def _document(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionRuntimeError("production configuration is unreadable") from exc
    if not isinstance(value, dict):
        raise ProductionRuntimeError("production configuration must be an object")
    return value


def load_production_config(
    config_path: str | Path,
    *,
    data_root: str | Path,
    user_profile: str | Path | None = None,
    local_app_data: str | Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> ProductionRuntimeConfig:
    """Load repository-local configuration without deployment copies or ACL gates."""

    local_root = Path(local_app_data or os.environ.get("LOCALAPPDATA", "")).expanduser()
    if not local_root.is_absolute():
        raise ProductionRuntimeError("LOCALAPPDATA is unavailable")
    profile_root = Path(user_profile or os.environ.get("USERPROFILE", "")).expanduser()
    if not profile_root.is_absolute():
        raise ProductionRuntimeError("USERPROFILE is unavailable")
    expected_root = _lexical_absolute_path(
        PROJECT_ROOT / "rakuten-room-inago" / "runtime", "expected runtime root"
    )
    runtime_root = _checked_private_path(data_root, "data_root")
    config_file = _checked_private_path(config_path, "config_path")
    expected_config = expected_root / "config" / "settings.json"
    if not _same_path(runtime_root, expected_root) or not _same_path(
        config_file, expected_config
    ):
        raise ProductionRuntimeError(
            "production paths must use the repository runtime root"
        )
    if not config_file.is_file():
        raise ProductionRuntimeError("production configuration file is missing")
    value = _document(config_file)
    previous_database = profile_root / "InagoRuntime" / "state.sqlite"
    if previous_database.is_file():
        transferred = value.get("previous_runtime_transfer")
        destination = runtime_root / "state.sqlite"
        if (
            not destination.is_file()
            or not isinstance(transferred, dict)
            or transferred.get("status") != "content_verified"
            or transferred.get("history_and_pending_preserved") is not True
            or not _same_path(
                _absolute_path(transferred.get("source_database"), "previous source"),
                previous_database,
            )
            or not _same_path(
                _absolute_path(
                    transferred.get("destination_database"), "previous destination"
                ),
                destination,
            )
        ):
            raise ProductionRuntimeError(
                "existing external Inago database must be transferred and content verified before repository setup"
            )
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("environment") != "production"
    ):
        raise ProductionRuntimeError("production configuration version is unsupported")
    configured_root = _checked_private_path(value.get("runtime_root"), "runtime_root")
    if not _same_path(configured_root, expected_root):
        raise ProductionRuntimeError(
            "configured runtime root is not the repository runtime root"
        )

    application_root = _checked_private_path(
        value.get("application_root"), "application_root"
    )
    if not _same_path(application_root, PROJECT_ROOT) or not application_root.is_dir():
        raise ProductionRuntimeError("application root must be the current repository")

    owner_url = _owner_url(value.get("owner_room_url"))
    start_date = value.get("start_date")
    try:
        parsed_start = date.fromisoformat(start_date)
    except (TypeError, ValueError) as exc:
        raise ProductionRuntimeError("start_date must be an ISO calendar date") from exc
    if parsed_start.isoformat() != start_date:
        raise ProductionRuntimeError("start_date must be YYYY-MM-DD")

    basic = value.get("adopted_settings")
    detail = value.get("detailed_rules")
    if (
        not isinstance(basic, dict)
        or basic.get("version") != BASE_SETTINGS_VERSION
        or basic.get("adopted") is not True
        or basic.get("status") != "confirmed"
    ):
        raise ProductionRuntimeError("adopted basic settings version is not confirmed")
    if (
        not isinstance(detail, dict)
        or detail.get("version") != DETAIL_CANDIDATE_VERSION
        or detail.get("adopted") is not False
        or detail.get("status") != "reviewed_candidate_not_new_owner_confirmation"
    ):
        raise ProductionRuntimeError("detailed rules must remain an inactive candidate")

    expected_backup = _lexical_absolute_path(
        PROJECT_ROOT / "rakuten-room-inago" / "backups", "expected backup root"
    )
    backup_root = _checked_private_path(value.get("backup_root"), "backup_root")
    if not _same_path(backup_root, expected_backup):
        raise ProductionRuntimeError(
            "backup root must use the repository backup directory"
        )

    browser = value.get("browser")
    codex = value.get("codex")
    metrics = value.get("metrics")
    legacy = value.get("legacy")
    human_api = value.get("human_api")
    if not all(
        isinstance(item, dict) for item in (browser, codex, metrics, legacy, human_api)
    ):
        raise ProductionRuntimeError("production runtime sections are incomplete")

    chrome = _approved_chrome_executable(browser.get("chrome_executable"), local_root)
    user_data = _checked_private_path(browser.get("user_data_dir"), "user_data_dir")
    expected_user_data = local_root / "InagoPlaywrightProfile"
    if not _same_path(user_data, expected_user_data):
        raise ProductionRuntimeError(
            "ROOM browser must use the dedicated Inago profile"
        )
    if not user_data.is_dir():
        raise ProductionRuntimeError(
            "Chrome or the dedicated browser profile is unavailable"
        )
    if _has_reparse_point(user_data):
        raise ProductionRuntimeError("Browser profile cannot contain reparse points")

    node = browser.get("node_executable")
    command = codex.get("command")
    if (
        not isinstance(node, str)
        or not node.strip()
        or not Path(node).is_absolute()
        or not Path(node).is_file()
        or not which(node)
    ):
        raise ProductionRuntimeError("Node executable is unavailable")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part.strip() for part in command)
        or not Path(command[0]).is_absolute()
        or not Path(command[0]).is_file()
        or not which(command[0])
    ):
        raise ProductionRuntimeError("Codex CLI executable is unavailable")

    auth_state = _checked_private_path(
        metrics.get("affiliate_auth_state"), "affiliate_auth_state"
    )
    if not auth_state.is_file() or _has_reparse_point(auth_state):
        raise ProductionRuntimeError(
            "read-only Affiliate auth reference is unavailable"
        )

    legacy_lexical = _lexical_absolute_path(
        legacy.get("source_root"), "legacy.source_root"
    )
    if not _same_path(legacy_lexical, PROJECT_ROOT / "rakuten-room-kai"):
        raise ProductionRuntimeError(
            "legacy source root must use the repository legacy directory"
        )
    if _has_reparse_point(legacy_lexical):
        raise ProductionRuntimeError("legacy source root cannot contain reparse points")
    try:
        legacy_root = legacy_lexical.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProductionRuntimeError("legacy source root cannot be resolved") from exc
    if not legacy_root.is_dir():
        raise ProductionRuntimeError("legacy source root is unavailable or unsafe")
    legacy_paths = {
        key: legacy.get(key)
        for key in (
            "posts_log_path",
            "product_ledger_path",
            "post_status_path",
            "drafts_path",
        )
    }
    for key, relative in legacy_paths.items():
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ProductionRuntimeError(f"legacy.{key} must be a relative child")
    if human_api.get("bind_host") != "127.0.0.1":
        raise ProductionRuntimeError("Human API must bind to loopback only")
    port = human_api.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ProductionRuntimeError("Human API port is invalid")

    return ProductionRuntimeConfig(
        user_profile_root=profile_root.resolve(strict=False),
        application_root=application_root,
        runtime_root=runtime_root,
        backup_root=backup_root,
        owner_room_url=owner_url,
        start_date=start_date,
        adopted_settings=basic,
        detailed_rules=detail,
        chrome_executable=chrome,
        user_data_dir=user_data,
        node_executable=node,
        codex_command=tuple(command),
        affiliate_auth_state=auth_state,
        legacy_source_root=legacy_root,
        legacy_paths=legacy_paths,
        human_api_port=port,
    )


def register_production_settings(
    connection: sqlite3.Connection,
    config: ProductionRuntimeConfig,
    *,
    now: str,
) -> SettingsRuntime:
    """Register only the adopted basic settings and keep the candidate disabled."""

    from automation.inago.settings import register_external_settings

    runtime = register_external_settings(
        connection,
        config.adopted_settings,
        config.detailed_rules,
        created_at=now,
    )
    if (
        runtime.active_basic_version != BASE_SETTINGS_VERSION
        or runtime.detailed_rules_enabled
    ):
        raise ProductionRuntimeError(
            "registered settings do not match the approved runtime versions"
        )
    return runtime


def _make_copy_spec_factory(
    config: ProductionRuntimeConfig, settings_runtime: SettingsRuntime
) -> Callable[..., CopyGenerationSpec | None]:
    from automation.inago.copy_service import CopyGenerationSpec

    themes = {theme["theme_id"]: theme for theme in settings_runtime.themes}

    def make_copy_spec(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        offer_key: str,
        theme_id: str,
        target_date: str,
        settings_version: str,
        deadline_at: str,
        deadline_monotonic: float,
    ) -> CopyGenerationSpec | None:
        if settings_version != settings_runtime.active_basic_version:
            raise ProductionRuntimeError(
                "copy settings version differs from active runtime settings"
            )
        row = connection.execute(
            "SELECT * FROM offer WHERE offer_key=?", (offer_key,)
        ).fetchone()
        if row is None:
            return None
        theme = themes.get(theme_id)
        if theme is None:
            raise ProductionRuntimeError(
                "selected theme is absent from adopted settings"
            )
        latest = connection.execute(
            "SELECT version FROM draft WHERE offer_key=? ORDER BY version DESC LIMIT 1",
            (offer_key,),
        ).fetchone()
        draft_version = int(latest[0]) + 1 if latest is not None else 1
        existing = connection.execute(
            "SELECT version FROM draft WHERE run_id=? AND offer_key=?",
            (run_id, offer_key),
        ).fetchone()
        if existing is not None:
            draft_version = int(existing[0])
        try:
            facts = json.loads(row["facts_json"])
            images = json.loads(row["image_refs_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProductionRuntimeError("saved offer facts are malformed") from exc
        if not isinstance(facts, list) or not isinstance(images, list):
            raise ProductionRuntimeError(
                "saved offer facts do not match the database contract"
            )
        evidence_refs = tuple(
            dict.fromkeys(
                str(fact["source_ref"])
                for fact in facts
                if isinstance(fact, Mapping)
                and isinstance(fact.get("source_ref"), str)
                and fact["source_ref"]
            )
        )
        payload = {
            "offer": {
                "offer_key": offer_key,
                "draft_version": draft_version,
                "name": row["name"],
                "url": row["url"],
                "image_refs": images,
            },
            "purpose": f"ROOMで「{theme['name']}」をテーマに商品を紹介する",
            "facts": facts,
            "author_experience": {"availability": "unknown", "value": None},
        }
        return CopyGenerationSpec(
            run_id=run_id,
            offer_key=offer_key,
            draft_version=draft_version,
            target_date=target_date,
            settings_version=settings_version,
            input_refs=(f"offer:{offer_key}",),
            evidence_refs=evidence_refs,
            input_payload=payload,
            deadline_at=deadline_at,
            deadline_monotonic=deadline_monotonic,
            generator_version="inago-daily-copy-v1",
            pattern_version=settings_version,
        )

    return make_copy_spec


def build_public_daily_components(
    config: ProductionRuntimeConfig,
    connection: sqlite3.Connection,
    *,
    clock: Clock,
) -> dict[str, Any]:
    """Compose the audited public browser, AI-O, copy factory, and metrics ports."""

    from automation.inago.daily_ai import DailyAIRootRunner
    from automation.inago.live_sources import PublicBrowserSource, PublicSourceConfig
    from automation.inago.metrics_live_port import NodeMetricsPort

    settings_runtime = register_production_settings(
        connection,
        config,
        now=clock.now_jst().isoformat(),
    )
    source = PublicBrowserSource(
        PublicSourceConfig(
            chrome_executable=config.chrome_executable,
            evidence_root=config.runtime_root / "observations",
            environment="public_ui",
            timeout_seconds=17100,
            node_executable=config.node_executable,
            headless=False,
            user_data_dir=config.user_data_dir,
        )
    )
    ai_root = DailyAIRootRunner(
        data_root=config.runtime_root,
        skill_root=PROJECT_ROOT / ".agents" / "skills",
        agent_config_root=PROJECT_ROOT / ".codex" / "agents",
        cli_command=config.codex_command,
        environment={"CODEX_HOME": str(config.codex_home)},
        clock=clock,
        max_timeout_seconds=480,
    )
    metrics_port = NodeMetricsPort(
        node_executable=config.node_executable,
        browser_executable=config.chrome_executable,
        script_path=PROJECT_ROOT
        / "automation"
        / "inago"
        / "adapters"
        / "metrics_live.mjs",
        output_root=config.metrics_output_root,
        runtime_root=config.runtime_root,
        owner_room_url=config.owner_room_url,
        affiliate_auth_state=config.affiliate_auth_state,
        timeout_seconds=1800,
    )
    return {
        "source": source,
        "ai_root": ai_root,
        "copy_spec_factory": _make_copy_spec_factory(config, settings_runtime),
        "metrics_port": metrics_port,
        "cancel_metrics_job": metrics_port.cancel_run,
    }


def _open_production_database(config: ProductionRuntimeConfig) -> sqlite3.Connection:
    from automation.inago.repository import open_database

    return open_database(SimpleNamespace(data_root=config.runtime_root))


def _setup_state_path(config: ProductionRuntimeConfig) -> Path:
    return config.runtime_root / "state" / "setup-status.json"


def _checked_setup_state_path(config: ProductionRuntimeConfig) -> Path:
    """Check the entire setup-state path before each read or write."""

    _assert_production_roots(config)
    path = _setup_state_path(config)
    if _has_reparse_point(path):
        raise ProductionRuntimeError(
            "production setup state cannot contain reparse points"
        )
    return path


def _read_private_setup_state(
    config: ProductionRuntimeConfig, *, required: bool
) -> dict[str, Any] | None:
    path = _checked_setup_state_path(config)
    if not path.is_file():
        if required:
            raise ProductionRuntimeError("production setup has not completed")
        return None
    path = _checked_setup_state_path(config)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionRuntimeError(
            "existing runtime setup state is unreadable"
        ) from exc
    if not isinstance(existing, dict):
        raise ProductionRuntimeError("existing runtime setup state is invalid")
    if (
        type(existing.get("schema_version")) is not int
        or existing["schema_version"] != 1
    ):
        raise ProductionRuntimeError(
            "existing runtime setup state schema is unsupported"
        )
    return existing


def _write_setup_state(
    config: ProductionRuntimeConfig, value: Mapping[str, Any]
) -> None:
    from automation.atomic_io import atomic_write_json

    path = _checked_setup_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _checked_setup_state_path(config)
    atomic_write_json(path, dict(value))


def _write_private_state(path: Path, value: Mapping[str, Any]) -> None:
    from automation.atomic_io import atomic_write_json

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, dict(value))


def _assert_production_roots(config: ProductionRuntimeConfig) -> None:
    """Recheck repository data paths immediately before production writes."""

    expected_runtime = _lexical_absolute_path(
        PROJECT_ROOT / "rakuten-room-inago" / "runtime", "expected runtime root"
    )
    expected_backup = _lexical_absolute_path(
        PROJECT_ROOT / "rakuten-room-inago" / "backups", "expected backup root"
    )
    for actual, expected, label in (
        (config.runtime_root, expected_runtime, "runtime_root"),
        (config.backup_root, expected_backup, "backup_root"),
    ):
        lexical = _lexical_absolute_path(actual, label)
        if not _same_path(lexical, expected) or _has_reparse_point(lexical):
            raise ProductionRuntimeError(f"{label} changed or contains a reparse point")


def initialize_production_runtime(
    config: ProductionRuntimeConfig,
    *,
    now: str,
) -> dict[str, Any]:
    """Register approved settings, safely migrate history, and create a baseline backup."""

    from automation.inago.backup import create_daily_backup, list_daily_backups
    from automation.inago.migrate import migrate_legacy_history

    _assert_production_roots(config)
    _ensure_human_api_token(config)
    _ensure_codex_home(config)
    existing = _read_private_setup_state(config, required=False)
    connection = _open_production_database(config)
    try:
        settings = register_production_settings(connection, config, now=now)
        existing_status = None
        if existing is not None:
            existing_status = existing.get("status")
            if existing_status == "ready":
                if (
                    existing.get("settings_version") != settings.active_basic_version
                    or existing.get("migration_selection_allowed") is not True
                ):
                    raise ProductionRuntimeError(
                        "existing runtime setup is not ready for selection"
                    )
                migration_counts = existing.get("migration_counts", {})
            elif existing_status != "blocked_supplementation":
                raise ProductionRuntimeError(
                    "existing runtime setup has an unsupported state"
                )
        if existing_status != "ready":
            migration_args = {
                "source_root": config.legacy_source_root,
                "data_root": config.runtime_root,
                "posts_log_path": config.legacy_paths["posts_log_path"],
                "product_ledger_path": config.legacy_paths["product_ledger_path"],
                "post_status_path": config.legacy_paths["post_status_path"],
                "drafts_path": config.legacy_paths["drafts_path"],
                "now": now,
                "fixture_mode": False,
            }
            dry_run = migrate_legacy_history(
                connection, mode="dry_run", **migration_args
            )
            if dry_run.supplementation_required:
                reasons: dict[str, int] = {}
                for item in dry_run.supplementation_required:
                    reason = str(item.get("reason", "unknown"))
                    reasons[reason] = reasons.get(reason, 0) + 1
                _write_setup_state(
                    config,
                    {
                        "schema_version": 1,
                        "status": "blocked_supplementation",
                        "settings_version": settings.active_basic_version,
                        "migration_selection_allowed": False,
                        "supplementation_count": len(dry_run.supplementation_required),
                        "supplementation_reasons": reasons,
                        "updated_at": now,
                    },
                )
                raise ProductionRuntimeError(
                    f"legacy history needs {len(dry_run.supplementation_required)} owner confirmations before selection"
                )
            applied = migrate_legacy_history(connection, mode="apply", **migration_args)
            if not applied.selection_allowed:
                raise ProductionRuntimeError(
                    "legacy history migration did not enable selection"
                )
            migration_counts = {
                "source_records": applied.source_record_count,
                "legacy_evidence": applied.evidence_count,
                "human_statements": applied.statement_count,
                "unknown": applied.unknown_count,
                "created_publications": applied.created_publications,
                "created_exclusions": applied.created_exclusions,
            }
            _write_setup_state(
                config,
                {
                    "schema_version": 1,
                    "status": "ready",
                    "settings_version": settings.active_basic_version,
                    "detailed_rules_enabled": False,
                    "migration_selection_allowed": True,
                    "migration_counts": migration_counts,
                    "updated_at": now,
                },
            )

        _assert_production_roots(config)
        backup_dir = config.backup_root / f"daily-{now[:10].replace('-', '')}"
        valid_backups = set(list_daily_backups(config.backup_root))
        if backup_dir.resolve(strict=False) not in valid_backups:
            if backup_dir.exists():
                raise ProductionRuntimeError(
                    "today's backup exists but is not a valid Inago generation"
                )
            backup = create_daily_backup(
                connection,
                data_root=config.runtime_root,
                backup_root=config.backup_root,
                now=now,
                fixture_mode=False,
            )
            backup_dir = backup.directory
        counts = {
            "settings_version": settings.active_basic_version,
            "schema_version": int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            ),
            "publication_events": int(
                connection.execute("SELECT COUNT(*) FROM publication_event").fetchone()[
                    0
                ]
            ),
            "effective_exclusions": int(
                connection.execute(
                    "SELECT COUNT(*) FROM exclusion_key WHERE effective=1"
                ).fetchone()[0]
            ),
            "backup_generation": backup_dir.name,
            "migration": migration_counts,
        }
        return counts
    finally:
        connection.close()


def run_production_daily(
    config: ProductionRuntimeConfig,
    *,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Run one scheduled daily job from the repository with local runtime data."""

    from dataclasses import asdict
    from datetime import date

    from automation.inago.backup import create_daily_backup, list_daily_backups
    from automation.inago.clock import SystemClock
    from automation.inago.trigger import run_public_scheduled_daily

    selected_clock = clock or SystemClock()
    now = selected_clock.now_jst()
    first_day = date.fromisoformat(config.start_date)
    if now.date() < first_day or now.timetz().replace(tzinfo=None) < time(13, 0):
        return {
            "status": "not_due",
            "target_date": now.date().isoformat(),
            "committed_count": 0,
        }
    setup_state = require_production_ready(config)

    _assert_production_roots(config)
    connection = _open_production_database(config)
    try:
        settings = register_production_settings(
            connection,
            config,
            now=selected_clock.now_jst().isoformat(),
        )
        if settings.active_basic_version != setup_state["settings_version"]:
            raise ProductionRuntimeError("runtime settings changed after setup")
        _assert_production_roots(config)
        today_backup = config.backup_root / f"daily-{now.date().strftime('%Y%m%d')}"
        if today_backup.resolve(strict=False) not in set(
            list_daily_backups(config.backup_root)
        ):
            if today_backup.exists():
                raise ProductionRuntimeError("today's backup exists but is invalid")
            create_daily_backup(
                connection,
                data_root=config.runtime_root,
                backup_root=config.backup_root,
                now=now,
                fixture_mode=False,
            )
        components = build_public_daily_components(
            config,
            connection,
            clock=selected_clock,
        )
        result = run_public_scheduled_daily(
            connection,
            data_root=config.runtime_root,
            source=components["source"],
            ai_root=components["ai_root"],
            copy_spec_factory=components["copy_spec_factory"],
            metrics_port=components["metrics_port"],
            cancel_metrics_job=components["cancel_metrics_job"],
            clock=selected_clock,
        )
        return asdict(result)
    finally:
        connection.close()


def require_production_ready(config: ProductionRuntimeConfig) -> dict[str, Any]:
    """Check daily prerequisites explicitly, including under optimized Python."""

    state = _read_private_setup_state(config, required=True)
    if (
        state is None
        or state.get("status") != "ready"
        or state.get("migration_selection_allowed") is not True
        or state.get("settings_version") != BASE_SETTINGS_VERSION
    ):
        raise ProductionRuntimeError(
            "migration/setting preconditions do not permit daily selection"
        )
    return state


def serve_human_api(config: ProductionRuntimeConfig) -> None:
    """Serve the owner dashboard and pending-report API on authenticated loopback."""

    from automation.inago.human_api import create_server

    def connection_factory():
        return _open_production_database(config)

    token_path = _human_api_token_path(config)
    if not token_path.is_file() or _has_reparse_point(token_path):
        raise ProductionRuntimeError("private Human API token is unavailable")
    try:
        token_document = json.loads(token_path.read_text(encoding="utf-8"))
        token = (
            token_document.get("token") if isinstance(token_document, dict) else None
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionRuntimeError("private Human API token is unreadable") from exc
    if not isinstance(token, str) or len(token) < 32:
        raise ProductionRuntimeError("private Human API token is invalid")
    server = create_server(
        connection_factory=connection_factory,
        bind_host="127.0.0.1",
        port=config.human_api_port,
        auth_token=token,
    )
    server.serve_forever(poll_interval=0.5)


def _human_api_token_path(config: ProductionRuntimeConfig) -> Path:
    return config.runtime_root / "secrets" / "human-api-token.json"


def _ensure_human_api_token(config: ProductionRuntimeConfig) -> None:
    path = _human_api_token_path(config)
    if _has_reparse_point(path):
        raise ProductionRuntimeError(
            "Human API token path cannot contain reparse points"
        )
    if path.exists():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProductionRuntimeError(
                "existing Human API token is unreadable"
            ) from exc
        token = document.get("token") if isinstance(document, dict) else None
        if not isinstance(token, str) or len(token) < 32:
            raise ProductionRuntimeError("existing Human API token is invalid")
        return
    _assert_production_roots(config)
    _write_private_state(
        path, {"schema_version": 1, "token": secrets.token_urlsafe(32)}
    )


def _ensure_codex_home(config: ProductionRuntimeConfig) -> None:
    """Use the existing signed-in Codex home without copying credentials."""

    source = config.codex_home / "auth.json"
    if not source.is_file():
        raise ProductionRuntimeError("Codex ChatGPT authentication is unavailable")
