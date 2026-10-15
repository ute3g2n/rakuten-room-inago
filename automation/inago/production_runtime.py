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
import stat
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


@dataclass(frozen=True, slots=True)
class HumanApiRuntimeConfig:
    """Minimal repository-local settings needed by the independent report API."""

    application_root: Path
    runtime_root: Path
    backup_root: Path
    human_api_port: int


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


def _validated_legacy_source_root(
    config: ProductionRuntimeConfig,
) -> Path:
    """Revalidate the archived migration source immediately before use."""

    expected = _lexical_absolute_path(
        PROJECT_ROOT
        / "archive"
        / "legacy-bakemono-20261015"
        / "local-preserved"
        / "rakuten-room-kai",
        "expected archived legacy source root",
    )
    lexical = _lexical_absolute_path(config.legacy_source_root, "legacy.source_root")
    if not _same_path(lexical, expected):
        raise ProductionRuntimeError(
            "legacy source root changed from the archived local-preserved directory"
        )
    if _has_reparse_point(lexical):
        raise ProductionRuntimeError("legacy source root cannot contain reparse points")
    try:
        resolved = lexical.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ProductionRuntimeError(
            "archived legacy source is required for setup migration"
        ) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProductionRuntimeError("legacy source root cannot be resolved") from exc
    if (
        not _same_path(resolved, expected)
        or _has_reparse_point(lexical)
        or not resolved.is_dir()
    ):
        raise ProductionRuntimeError(
            "legacy source root changed or is unavailable for setup migration"
        )
    return resolved


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


def _windows_known_folder(name: str) -> Path:
    """Resolve a Windows known folder for the current process identity."""

    folder_ids = {
        "profile": "5E6C858F-0E22-4760-9AFE-EA3317B67173",
        "local_app_data": "F1B32785-6FBA-4FCF-9D55-7B8E7F157091",
    }
    folder_id = folder_ids.get(name)
    if folder_id is None:
        raise ProductionRuntimeError("Windows known-folder name is unsupported")
    if os.name != "nt":
        raise ProductionRuntimeError("current Windows user folders are unavailable")

    import ctypes
    from ctypes import wintypes
    from uuid import UUID

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    guid = GUID.from_buffer_copy(UUID(folder_id).bytes_le)
    result = wintypes.LPWSTR()
    status = shell32.SHGetKnownFolderPath(
        ctypes.byref(guid), 0, None, ctypes.byref(result)
    )
    if status != 0 or not result.value:
        raise ProductionRuntimeError("current Windows user folders are unavailable")
    try:
        return Path(result.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(result, ctypes.c_void_p))


def _current_user_root(value: str | Path | None, *, name: str, folder: str) -> Path:
    expected = _absolute_path(_windows_known_folder(folder), f"current {name}")
    selected = expected if value is None else _absolute_path(value, name)
    if not _same_path(selected, expected):
        label = "profile" if folder == "profile" else "LocalAppData directory"
        raise ProductionRuntimeError(
            f"{name} must match current Windows user's {label}"
        )
    return selected


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


def _validate_previous_runtime_transfer(
    value: Mapping[str, Any], *, profile_root: Path, runtime_root: Path
) -> None:
    """Refuse to serve or initialize an empty repository DB over old history."""

    previous_database = profile_root / "InagoRuntime" / "state.sqlite"
    if not previous_database.is_file():
        return
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
            "existing external Inago database must be transferred and content verified before repository runtime use"
        )


def load_production_config(
    config_path: str | Path,
    *,
    data_root: str | Path,
    user_profile: str | Path | None = None,
    local_app_data: str | Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> ProductionRuntimeConfig:
    """Load repository-local configuration without deployment copies or ACL gates."""

    local_root = _current_user_root(
        local_app_data, name="local_app_data", folder="local_app_data"
    )
    profile_root = _current_user_root(
        user_profile, name="user_profile", folder="profile"
    )
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
    _validate_previous_runtime_transfer(
        value, profile_root=profile_root, runtime_root=runtime_root
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
    expected_user_data = _checked_private_path(
        local_root / "InagoPlaywrightProfile",
        "expected dedicated browser profile",
    )
    if not _same_path(user_data, expected_user_data):
        raise ProductionRuntimeError(
            "ROOM browser must use the dedicated Inago profile"
        )
    try:
        user_data_mode = user_data.stat().st_mode
    except FileNotFoundError as exc:
        raise ProductionRuntimeError(
            "dedicated ROOM browser profile directory is missing"
        ) from exc
    except OSError as exc:
        raise ProductionRuntimeError(
            "dedicated ROOM browser profile directory cannot be inspected"
        ) from exc
    if not stat.S_ISDIR(user_data_mode):
        raise ProductionRuntimeError(
            "dedicated ROOM browser profile path is not a directory"
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
    expected_legacy_root = _lexical_absolute_path(
        PROJECT_ROOT
        / "archive"
        / "legacy-bakemono-20261015"
        / "local-preserved"
        / "rakuten-room-kai",
        "expected archived legacy source root",
    )
    if not _same_path(legacy_lexical, expected_legacy_root):
        raise ProductionRuntimeError(
            "legacy source root must use the archived local-preserved directory"
        )
    if _has_reparse_point(legacy_lexical):
        raise ProductionRuntimeError("legacy source root cannot contain reparse points")
    try:
        legacy_root = legacy_lexical.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProductionRuntimeError("legacy source root cannot be resolved") from exc
    if legacy_root.exists() and not legacy_root.is_dir():
        raise ProductionRuntimeError(
            "legacy source root must be a directory when present"
        )
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


def load_human_api_config(
    config_path: str | Path,
    *,
    data_root: str | Path,
) -> HumanApiRuntimeConfig:
    """Load only repository and loopback API settings; no browser or AI tools."""

    expected_root = _lexical_absolute_path(
        PROJECT_ROOT / "rakuten-room-inago" / "runtime", "expected runtime root"
    )
    expected_config = expected_root / "config" / "settings.json"
    runtime_root = _checked_private_path(data_root, "data_root")
    config_file = _checked_private_path(config_path, "config_path")
    if not _same_path(runtime_root, expected_root) or not _same_path(
        config_file, expected_config
    ):
        raise ProductionRuntimeError(
            "Human API paths must use the repository runtime root"
        )
    if not config_file.is_file():
        raise ProductionRuntimeError("production configuration file is missing")
    value = _document(config_file)
    profile_root = _current_user_root(None, name="user_profile", folder="profile")
    _validate_previous_runtime_transfer(
        value, profile_root=profile_root, runtime_root=runtime_root
    )
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("environment") != "production"
    ):
        raise ProductionRuntimeError("production configuration version is unsupported")

    application_root = _checked_private_path(
        value.get("application_root"), "application_root"
    )
    if not _same_path(application_root, PROJECT_ROOT) or not application_root.is_dir():
        raise ProductionRuntimeError("application root must be the current repository")
    configured_root = _checked_private_path(value.get("runtime_root"), "runtime_root")
    if not _same_path(configured_root, expected_root):
        raise ProductionRuntimeError(
            "configured runtime root is not the repository runtime root"
        )
    expected_backup = _lexical_absolute_path(
        PROJECT_ROOT / "rakuten-room-inago" / "backups", "expected backup root"
    )
    backup_root = _checked_private_path(value.get("backup_root"), "backup_root")
    if not _same_path(backup_root, expected_backup):
        raise ProductionRuntimeError(
            "backup root must use the repository backup directory"
        )
    human_api = value.get("human_api")
    if not isinstance(human_api, dict) or human_api.get("bind_host") != "127.0.0.1":
        raise ProductionRuntimeError("Human API must bind to loopback only")
    port = human_api.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ProductionRuntimeError("Human API port is invalid")
    return HumanApiRuntimeConfig(
        application_root=application_root,
        runtime_root=runtime_root,
        backup_root=backup_root,
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


def _open_production_database(
    config: ProductionRuntimeConfig | HumanApiRuntimeConfig,
) -> sqlite3.Connection:
    from automation.inago.repository import open_database

    return open_database(SimpleNamespace(data_root=config.runtime_root))


def _open_existing_production_database(
    config: ProductionRuntimeConfig | HumanApiRuntimeConfig,
) -> sqlite3.Connection:
    """Open an already migrated runtime DB without creating or upgrading it."""

    from automation.inago.repository import SQLITE_BUSY_TIMEOUT_MS, schema_version

    _assert_production_roots(config)
    database_path = config.runtime_root / "state.sqlite"
    if _has_reparse_point(database_path) or not database_path.is_file():
        raise sqlite3.OperationalError("production database is unavailable")
    try:
        database_uri = f"{database_path.resolve(strict=True).as_uri()}?mode=rw"
        connection = sqlite3.connect(
            database_uri,
            uri=True,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise sqlite3.OperationalError("production database is unavailable") from exc

    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA synchronous = FULL")
        schema_version(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def _setup_state_path(
    config: ProductionRuntimeConfig | HumanApiRuntimeConfig,
) -> Path:
    return config.runtime_root / "state" / "setup-status.json"


def _checked_setup_state_path(
    config: ProductionRuntimeConfig | HumanApiRuntimeConfig,
) -> Path:
    """Check the entire setup-state path before each read or write."""

    _assert_production_roots(config)
    path = _setup_state_path(config)
    if _has_reparse_point(path):
        raise ProductionRuntimeError(
            "production setup state cannot contain reparse points"
        )
    return path


def _read_private_setup_state(
    config: ProductionRuntimeConfig | HumanApiRuntimeConfig, *, required: bool
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


def _assert_production_roots(
    config: ProductionRuntimeConfig | HumanApiRuntimeConfig,
) -> None:
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
    existing = _read_private_setup_state(config, required=False)
    existing_status = existing.get("status") if existing is not None else None
    if existing is not None and (
        not isinstance(existing_status, str)
        or existing_status not in {"ready", "blocked_supplementation"}
    ):
        raise ProductionRuntimeError("existing runtime setup has an unsupported state")
    if existing_status != "ready":
        _validated_legacy_source_root(config)
        _ensure_human_api_token(config)
        _ensure_codex_home(config)
        connection = _open_production_database(config)
    else:
        connection = _open_existing_production_database(config)
    try:
        if existing_status == "ready":
            _ensure_human_api_token(config)
            _ensure_codex_home(config)
        settings = register_production_settings(connection, config, now=now)
        if existing is not None:
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
                "source_root": _validated_legacy_source_root(config),
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
            migration_args["source_root"] = _validated_legacy_source_root(config)
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
    connection = _open_existing_production_database(config)
    try:
        active_settings = connection.execute(
            "SELECT version FROM settings_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        expected_settings_version = str(config.adopted_settings.get("version", ""))
        if (
            active_settings is None
            or str(active_settings["version"]) != expected_settings_version
            or expected_settings_version != str(setup_state["settings_version"])
        ):
            raise ProductionRuntimeError(
                "existing database does not contain the ready adopted settings"
            )
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


def require_production_ready(
    config: ProductionRuntimeConfig | HumanApiRuntimeConfig,
) -> dict[str, Any]:
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


def _startup_preflight_side_effects() -> dict[str, bool]:
    return {
        "database_created": False,
        "database_written": False,
        "browser_started": False,
        "network_requested": False,
        "auth_contents_read": False,
        "daily_started": False,
    }


def _startup_preflight_config_failure(exc: ProductionRuntimeError) -> tuple[str, str]:
    message = str(exc)
    mappings = (
        (
            "dedicated ROOM browser profile directory is missing",
            "browser_profile",
            "browser_profile_missing",
        ),
        (
            "dedicated ROOM browser profile directory cannot be inspected",
            "browser_profile",
            "browser_profile_unavailable",
        ),
        (
            "dedicated ROOM browser profile path is not a directory",
            "browser_profile",
            "browser_profile_invalid",
        ),
        (
            "ROOM browser must use the dedicated Inago profile",
            "browser_profile",
            "browser_profile_mismatch",
        ),
        ("Chrome executable is unavailable", "chrome", "chrome_unavailable"),
        ("Node executable is unavailable", "node", "node_unavailable"),
        ("Codex CLI executable is unavailable", "codex", "codex_unavailable"),
        (
            "read-only Affiliate auth reference is unavailable",
            "affiliate_auth_reference",
            "affiliate_auth_reference_unavailable",
        ),
        ("current Windows user's", "process_identity", "process_identity_mismatch"),
        (
            "production configuration file is missing",
            "configuration",
            "configuration_missing",
        ),
        (
            "production configuration is unreadable",
            "configuration",
            "configuration_unreadable",
        ),
        (
            "adopted basic settings version is not confirmed",
            "adopted_settings",
            "adopted_settings_invalid",
        ),
        (
            "detailed rules must remain an inactive candidate",
            "adopted_settings",
            "adopted_settings_invalid",
        ),
    )
    for fragment, check_name, failure_code in mappings:
        if fragment in message:
            return check_name, failure_code
    return "configuration", "configuration_invalid"


def _startup_preflight_setup_failure(exc: ProductionRuntimeError) -> str:
    message = str(exc)
    if "setup has not completed" in message:
        return "setup_state_missing"
    if "setup state" in message or "setup state schema" in message:
        return "setup_state_invalid"
    if "migration/setting preconditions" in message:
        return "setup_not_ready"
    return "setup_state_unavailable"


def _startup_preflight_database(
    config: ProductionRuntimeConfig,
) -> tuple[str | None, str | None]:
    """Inspect SQLite without recovery, journal creation, or schema mutation."""

    from automation.inago.repository import schema_version

    connection: sqlite3.Connection | None = None
    database_path = config.runtime_root / "state.sqlite"
    try:
        _assert_production_roots(config)
        if _has_reparse_point(database_path):
            return "database_path_invalid", None
        if not database_path.is_file():
            return "database_missing", None
        for suffix in ("-wal", "-journal"):
            sidecar = Path(f"{database_path}{suffix}")
            if sidecar.is_file() and sidecar.stat().st_size > 0:
                return "database_recovery_pending", None
        database_uri = (
            f"{database_path.resolve(strict=True).as_uri()}?mode=ro&immutable=1"
        )
        connection = sqlite3.connect(
            database_uri,
            uri=True,
            timeout=5,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()
        if quick_check is None or str(quick_check[0]).lower() != "ok":
            return "database_schema_invalid", None
        schema_version(connection)
        active_settings = connection.execute(
            "SELECT version FROM settings_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if active_settings is None:
            return None, "adopted_settings_missing"
        expected_version = str(config.adopted_settings.get("version", ""))
        if str(active_settings["version"]) != expected_version:
            return None, "adopted_settings_mismatch"
        return None, None
    except (sqlite3.Error, OSError, RuntimeError, ValueError):
        return "database_schema_invalid", None
    finally:
        if connection is not None:
            connection.close()


def run_production_startup_preflight(
    config_path: str | Path,
    *,
    data_root: str | Path,
    user_profile: str | Path | None = None,
    local_app_data: str | Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, object]:
    """Report scheduler-identity readiness without launching or changing runtime state."""

    checks: dict[str, str] = {}
    failed_checks: list[str] = []
    side_effects = _startup_preflight_side_effects()
    try:
        config = load_production_config(
            config_path,
            data_root=data_root,
            user_profile=user_profile,
            local_app_data=local_app_data,
            which=which,
        )
    except Exception as exc:
        if isinstance(exc, ProductionRuntimeError):
            check_name, failure_code = _startup_preflight_config_failure(exc)
        else:
            check_name, failure_code = "configuration", "configuration_invalid"
        checks["configuration"] = "failed"
        checks[check_name] = "failed"
        failed_checks.append(failure_code)
        return {
            "status": "blocked",
            "checks": checks,
            "failed_checks": failed_checks,
            "side_effects": side_effects,
        }

    checks.update(
        {
            "configuration": "ready",
            "process_identity": "ready",
            "browser_profile": "ready",
            "chrome": "ready",
            "node": "ready",
            "codex": "ready",
            "affiliate_auth_reference": "ready",
            "adopted_settings": "ready",
        }
    )
    try:
        require_production_ready(config)
        checks["setup_state"] = "ready"
    except ProductionRuntimeError as exc:
        checks["setup_state"] = "failed"
        failed_checks.append(_startup_preflight_setup_failure(exc))

    database_failure, settings_failure = _startup_preflight_database(config)
    if database_failure is None:
        checks["database_schema"] = "ready"
    else:
        checks["database_schema"] = "failed"
        failed_checks.append(database_failure)
    if settings_failure is not None:
        checks["adopted_settings"] = "failed"
        failed_checks.append(settings_failure)
    checks["database_adopted_settings"] = (
        "ready" if database_failure is None and settings_failure is None else "failed"
    )

    return {
        "status": "ready" if not failed_checks else "blocked",
        "checks": checks,
        "failed_checks": failed_checks,
        "side_effects": side_effects,
    }


def serve_human_api(config: ProductionRuntimeConfig | HumanApiRuntimeConfig) -> None:
    """Serve the owner dashboard and pending-report API on authenticated loopback."""

    from automation.inago.human_api import create_server

    def connection_factory():
        return _open_existing_production_database(config)

    initial_connection = connection_factory()
    try:
        from automation.inago.export import build_dashboard_state

        build_dashboard_state(initial_connection)
    finally:
        initial_connection.close()

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


def _human_api_token_path(
    config: ProductionRuntimeConfig | HumanApiRuntimeConfig,
) -> Path:
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
