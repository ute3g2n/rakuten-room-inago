from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from automation.inago.metrics_live_port import metrics_result_from_documents
from automation.inago.production_runtime import (
    ProductionRuntimeError,
    _write_setup_state,
    load_production_config,
    run_production_daily,
)
from automation.inago.cli import EXIT_RUNTIME_ERROR, main


def _write_config(root: Path, *, owner_room_url: str) -> Path:
    runtime = root / "InagoRuntime"
    (runtime / "config").mkdir(parents=True)
    application_root = runtime / "app-20261014-120000"
    tools = application_root / "tools"
    tools.mkdir(parents=True)
    node = tools / "node.exe"
    node.write_text("fixture node", encoding="utf-8")
    codex = tools / "codex.exe"
    codex.write_text("fixture codex", encoding="utf-8")
    local_app_data = root / "LocalAppData"
    profile = local_app_data / "InagoPlaywrightProfile"
    profile.mkdir(parents=True)
    browser = local_app_data / "Google" / "Chrome" / "Application" / "chrome.exe"
    browser.parent.mkdir(parents=True)
    browser.write_text("fixture executable", encoding="utf-8")
    auth = runtime / "secrets" / "affiliate-auth-state.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}", encoding="utf-8")
    legacy = root / "legacy"
    legacy.mkdir()
    manifest = {
        "schema_version": 1,
        "application_root": str(application_root),
        "legacy_source_root": str(legacy),
    }
    (runtime / "config" / "install-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    document = {
        "schema_version": 1,
        "environment": "production",
        "runtime_root": str(runtime),
        "application_root": str(application_root),
        "backup_root": str(root / "InagoRuntimeBackups"),
        "owner_room_url": owner_room_url,
        "start_date": "2026-10-15",
        "adopted_settings": {
            "version": "confirmed-0.3-step46-all132",
            "adopted": True,
            "status": "confirmed",
        },
        "detailed_rules": {
            "version": "detailed-writing-rules-candidate-0.1-step68",
            "adopted": False,
            "status": "reviewed_candidate_not_new_owner_confirmation",
        },
        "browser": {
            "chrome_executable": str(browser),
            "user_data_dir": str(profile),
            "node_executable": str(node),
        },
        "codex": {"command": [str(codex)]},
        "metrics": {"affiliate_auth_state": str(auth)},
        "legacy": {
            "source_root": str(legacy),
            "posts_log_path": "posts-log.jsonl",
            "product_ledger_path": "products.json",
            "post_status_path": "post-status.json",
            "drafts_path": "drafts.json",
        },
        "human_api": {"bind_host": "127.0.0.1", "port": 8765},
    }
    path = runtime / "config" / "settings.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_production_config_requires_exact_runtime_and_public_owner_room_url(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )

    config = load_production_config(
        config_path,
        data_root=tmp_path / "InagoRuntime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )

    assert config.runtime_root == (tmp_path / "InagoRuntime").resolve()
    assert config.start_date == "2026-10-15"
    assert config.detailed_rules_enabled is False
    assert "room_0123456789" not in repr(config)


@pytest.mark.parametrize(
    "url",
    [
        "https://room.rakuten.co.jp.evil.example/room_0123456789/items",
        "http://room.rakuten.co.jp/room_0123456789/items",
        "https://user:pass@room.rakuten.co.jp/room_0123456789/items",
        "https://room.rakuten.co.jp/room_0123456789/other",
    ],
)
def test_production_config_rejects_non_owner_or_unsafe_room_urls(
    tmp_path: Path, url: str
) -> None:
    config_path = _write_config(tmp_path, owner_room_url=url)

    with pytest.raises(ProductionRuntimeError):
        load_production_config(
            config_path,
            data_root=tmp_path / "InagoRuntime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


@pytest.mark.parametrize("target", ["settings", "manifest"])
def test_production_config_rejects_boolean_schema_version(
    tmp_path: Path, target: str
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    if target == "settings":
        selected_path = config_path
    else:
        selected_path = config_path.parent / "install-manifest.json"
    document = json.loads(selected_path.read_text(encoding="utf-8"))
    document["schema_version"] = True
    selected_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ProductionRuntimeError):
        load_production_config(
            config_path,
            data_root=tmp_path / "InagoRuntime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


@pytest.mark.parametrize("field", ["runtime_root", "backup_root"])
def test_production_config_rejects_mismatched_private_roots(
    tmp_path: Path, field: str
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    document = json.loads(config_path.read_text(encoding="utf-8"))
    document[field] = str(tmp_path / "other-root")
    config_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ProductionRuntimeError):
        load_production_config(
            config_path,
            data_root=tmp_path / "InagoRuntime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


def test_production_config_rejects_chrome_outside_standard_installation(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    document = json.loads(config_path.read_text(encoding="utf-8"))
    alternate = tmp_path / "chrome.exe"
    alternate.write_text("unapproved fixture executable", encoding="utf-8")
    document["browser"]["chrome_executable"] = str(alternate)
    config_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ProductionRuntimeError, match="standard.*installation"):
        load_production_config(
            config_path,
            data_root=tmp_path / "InagoRuntime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


def test_production_config_rejects_legacy_source_not_pinned_in_install_manifest(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    document = json.loads(config_path.read_text(encoding="utf-8"))
    alternate = tmp_path / "other-legacy"
    alternate.mkdir()
    document["legacy"]["source_root"] = str(alternate)
    config_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ProductionRuntimeError, match="does not match the installed"):
        load_production_config(
            config_path,
            data_root=tmp_path / "InagoRuntime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


def _create_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable to this account")


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_production_config_rejects_runtime_junction_before_resolving_target(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    runtime_alias = tmp_path / "InagoRuntime"
    runtime_target = tmp_path / "redirected-runtime"
    runtime_alias.replace(runtime_target)
    _create_junction(runtime_alias, runtime_target)

    with pytest.raises(ProductionRuntimeError, match="reparse"):
        load_production_config(
            config_path,
            data_root=runtime_alias,
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_production_config_rejects_backup_junction_before_resolving_target(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    backup_alias = tmp_path / "InagoRuntimeBackups"
    backup_target = tmp_path / "redirected-backups"
    backup_target.mkdir()
    _create_junction(backup_alias, backup_target)

    with pytest.raises(ProductionRuntimeError, match="reparse"):
        load_production_config(
            config_path,
            data_root=tmp_path / "InagoRuntime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_production_config_rejects_chrome_junction_before_resolving_target(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    browser_path = Path(
        json.loads(config_path.read_text(encoding="utf-8"))["browser"][
            "chrome_executable"
        ]
    )
    application_dir = browser_path.parent
    redirect_target = tmp_path / "redirected-chrome-application"
    application_dir.replace(redirect_target)
    _create_junction(application_dir, redirect_target)

    with pytest.raises(ProductionRuntimeError, match="reparse"):
        load_production_config(
            config_path,
            data_root=tmp_path / "InagoRuntime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_production_config_rejects_legacy_junction_before_resolving_target(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    legacy_alias = tmp_path / "legacy"
    legacy_target = tmp_path / "redirected-legacy"
    legacy_alias.replace(legacy_target)
    _create_junction(legacy_alias, legacy_target)

    with pytest.raises(ProductionRuntimeError, match="reparse"):
        load_production_config(
            config_path,
            data_root=tmp_path / "InagoRuntime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


def test_production_daily_does_not_start_before_first_1300_boundary(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "InagoRuntime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )

    class BeforeStartClock:
        @staticmethod
        def now_jst() -> datetime:
            return datetime(2026, 10, 15, 12, 59, tzinfo=ZoneInfo("Asia/Tokyo"))

    result = run_production_daily(config, clock=BeforeStartClock())

    assert result == {
        "status": "not_due",
        "target_date": "2026-10-15",
        "committed_count": 0,
    }
    assert not (tmp_path / "InagoRuntime" / "state.sqlite").exists()


def test_production_daily_startwhenavailable_does_not_run_before_1300_next_day(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "InagoRuntime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )

    class BeforeNextStartClock:
        @staticmethod
        def now_jst() -> datetime:
            return datetime(2026, 10, 16, 12, 59, tzinfo=ZoneInfo("Asia/Tokyo"))

    result = run_production_daily(config, clock=BeforeNextStartClock())

    assert result == {
        "status": "not_due",
        "target_date": "2026-10-16",
        "committed_count": 0,
    }
    assert not (tmp_path / "InagoRuntime" / "state.sqlite").exists()


def test_production_daily_rejects_setup_state_parent_junction(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "InagoRuntime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    state_target = tmp_path / "redirected-state"
    state_target.mkdir()
    (state_target / "setup-status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ready",
                "migration_selection_allowed": True,
                "settings_version": "confirmed-0.3-step46-all132",
            }
        ),
        encoding="utf-8",
    )
    state_alias = config.runtime_root / "state"
    _create_junction(state_alias, state_target)

    class DueClock:
        @staticmethod
        def now_jst() -> datetime:
            return datetime(2026, 10, 15, 13, 1, tzinfo=ZoneInfo("Asia/Tokyo"))

    with pytest.raises(ProductionRuntimeError, match="setup state.*reparse"):
        run_production_daily(config, clock=DueClock())
    assert not (config.runtime_root / "state.sqlite").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_setup_state_writer_rejects_parent_junction(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "InagoRuntime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    state_target = tmp_path / "redirected-state"
    state_target.mkdir()
    _create_junction(config.runtime_root / "state", state_target)

    with pytest.raises(ProductionRuntimeError, match="setup state.*reparse"):
        _write_setup_state(
            config,
            {
                "schema_version": 1,
                "status": "ready",
                "migration_selection_allowed": True,
            },
        )
    assert not (state_target / "setup-status.json").exists()


@pytest.mark.parametrize("schema_version", [None, 2, True])
def test_production_daily_rejects_missing_or_unsupported_setup_state_schema(
    tmp_path: Path, schema_version: int | bool | None
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "InagoRuntime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    state_path = config.runtime_root / "state" / "setup-status.json"
    state_path.parent.mkdir()
    state = {
        "status": "ready",
        "migration_selection_allowed": True,
        "settings_version": "confirmed-0.3-step46-all132",
    }
    if schema_version is not None:
        state["schema_version"] = schema_version
    state_path.write_text(json.dumps(state), encoding="utf-8")

    class DueClock:
        @staticmethod
        def now_jst() -> datetime:
            return datetime(2026, 10, 15, 13, 1, tzinfo=ZoneInfo("Asia/Tokyo"))

    with pytest.raises(ProductionRuntimeError, match="schema is unsupported"):
        run_production_daily(config, clock=DueClock())
    assert not (config.runtime_root / "state.sqlite").exists()


def test_production_daily_reports_invalid_utf8_setup_state_as_unreadable(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "InagoRuntime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    state_path = config.runtime_root / "state" / "setup-status.json"
    state_path.parent.mkdir()
    state_path.write_bytes(b"\xff")

    class DueClock:
        @staticmethod
        def now_jst() -> datetime:
            return datetime(2026, 10, 15, 13, 1, tzinfo=ZoneInfo("Asia/Tokyo"))

    with pytest.raises(ProductionRuntimeError, match="setup state is unreadable"):
        run_production_daily(config, clock=DueClock())
    assert not (config.runtime_root / "state.sqlite").exists()


def test_metrics_adapter_preserves_zero_null_and_provisional_scopes() -> None:
    observed_at = "2026-10-15T13:20:00+09:00"
    affiliate = {
        "status": "partial",
        "observedAt": observed_at,
        "daily": {
            "status": "partial",
            "observedAt": observed_at,
            "rows": [
                {
                    "date": "2026-10-14",
                    "clicks": 0,
                    "salesCount": None,
                    "salesYen": 0,
                    "commissionUnconfirmedYen": None,
                }
            ],
        },
        "orders": [
            {
                "orderRef": None,
                "isTemporaryRef": True,
                "occurredAt": None,
                "shopName": "fixture-shop",
                "productName": "fixture-product",
                "itemCode": None,
                "productUrl": None,
                "salesYen": None,
                "commissionYen": 0,
                "orderStatus": None,
                "linkType": None,
                "window": "affiliate-orders:2026-10",
            }
        ],
        "orderWindows": [
            {
                "window": "affiliate-orders:2026-10",
                "count": 1,
                "observedAt": observed_at,
                "availability": "observed",
            }
        ],
    }
    room = {
        "status": "observed",
        "observedAt": observed_at,
        "windowStart": "2026-09-15T13:20:00+09:00",
        "posts": [
            {
                "postId": "12345",
                "roomPostUrl": "https://room.rakuten.co.jp/room_0123456789/12345",
                "itemUrl": "https://item.rakuten.co.jp/shop/item/",
                "likes": 0,
                "createdAt": "2026-10-14T12:00:00+09:00",
            }
        ],
    }

    result = metrics_result_from_documents(
        run_id="RUN-1", target_date="2026-10-15", affiliate=affiliate, room=room
    )

    clicks = next(item for item in result.daily_observations if item.metric == "clicks")
    sales_count = next(
        item for item in result.daily_observations if item.metric == "sales_count"
    )
    sales_yen = next(
        item for item in result.daily_observations if item.metric == "sales_yen"
    )
    assert clicks.value == 0 and clicks.availability == "observed"
    assert sales_count.value is None and sales_count.availability == "unknown"
    assert sales_yen.value == 0 and sales_yen.provisional is True
    assert result.orders[0].sales_yen is None
    assert result.orders[0].commission_yen == 0
    assert result.room_posts[0].likes == 0
    assert result.status == "partial"


def test_task_templates_bind_daily_and_human_api_to_safe_modes() -> None:
    task_root = (
        Path(__file__).resolve().parents[1] / "automation" / "inago" / "task-xml"
    )
    namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    daily = ET.parse(task_root / "RakutenRoomInagoDaily.xml").getroot()
    human = ET.parse(task_root / "RakutenRoomInagoHumanApi.xml").getroot()
    daily_boundary = daily.find(".//t:CalendarTrigger/t:StartBoundary", namespace)
    human_logon = human.find(".//t:LogonTrigger", namespace)
    daily_args = daily.findtext(".//t:Actions/t:Exec/t:Arguments", namespaces=namespace)
    human_args = human.findtext(".//t:Actions/t:Exec/t:Arguments", namespaces=namespace)

    assert daily_boundary is not None and daily_boundary.text == "{{START_BOUNDARY}}"
    assert human_logon is not None
    assert "daily --environment production" in (daily_args or "")
    assert "serve --environment production" in (human_args or "")
    assert "ROOM" not in (daily_args or "")
    assert "ROOM" not in (human_args or "")


def test_task_registration_uses_only_the_protected_application_python_runtime() -> None:
    project_root = Path(__file__).resolve().parents[1]
    script_path = project_root / "automation" / "inago" / "register_tasks.ps1"
    source = script_path.read_text(encoding="utf-8")

    assert "$projectRoot" not in source
    assert (
        "$pythonExe = Join-Path $applicationRoot '.venv-inago\\Scripts\\python.exe'"
        in source
    )
    assert source.count("$pythonExe =") == 1


def test_task_registration_validates_setup_state_schema_before_registration() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / "automation" / "inago" / "register_tasks.ps1").read_text(
        encoding="utf-8"
    )

    state_load = source.index("$setup = Get-Content")
    schema_gate = source.index("$setup.schema_version")
    state_gate = source.index("$setup.status")
    first_registration = source.index("Register-ScheduledTask -TaskName")

    assert state_load < schema_gate < state_gate < first_registration
    assert "$setup -isnot [PSCustomObject]" in source
    assert "$setup.schema_version -isnot [int]" in source
    assert "$setup.schema_version -isnot [long]" in source
    assert "$setup.schema_version -ne 1" in source


def test_production_installation_checks_source_before_copying_or_preparing_storage() -> (
    None
):
    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / "automation" / "inago" / "install_runtime.ps1").read_text(
        encoding="utf-8"
    )

    trusted_source = source.index("Assert-InagoTrustedSourceTree -Root $source")
    prepare_storage = source.index("& $prepareScript | Out-Null")
    first_copy = source.index("Copy-Item -LiteralPath $source")
    tool_resolver = source.index("function Resolve-TrustedApplication")
    python_launcher_validation = source.index(
        "$pythonLauncher = Resolve-TrustedApplication 'py.exe'"
    )
    node_validation = source.index("$nodePath = Resolve-TrustedApplication 'node.exe'")
    codex_validation = source.index(
        "$codexPath = Resolve-TrustedApplication 'codex.exe'"
    )
    first_external_execution = source.index("& $pythonLauncher")
    python_base_validation = source.index(
        "Assert-InagoTrustedSourceTree -Root (Split-Path -Parent $pythonBase)"
    )
    node_execution = source.index("& $nodePath --version")
    bootstrap_owner_check = source.index(
        "$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value"
    )
    first_module_load = source.index(". $securityModule")

    assert trusted_source < prepare_storage < first_copy
    assert tool_resolver < python_launcher_validation < first_external_execution
    assert python_base_validation < source.index("& $pythonBase -m venv")
    assert node_validation < node_execution
    assert codex_validation < source.index("Copy-Item -LiteralPath $codexPath")
    assert bootstrap_owner_check < first_module_load
    assert "Assert-InagoTrustedSourceTree -Root (Split-Path -Parent $source)" in source
    assert "Assert-InagoTrustedSourceTree -Root $stableNodeRoot" in source
    assert (
        "if ($Name -ieq 'py.exe' -and $systemLauncherPaths -contains $source)" in source
    )
    assert (
        "Assert-InagoTrustedSourceTree -Root $source -AllowedSids $allowedSids"
        in source
    )


def test_task_registration_validates_chrome_trust_before_registering_tasks() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / "automation" / "inago" / "register_tasks.ps1").read_text(
        encoding="utf-8"
    )

    standard_install_path_check = source.index(
        "$approvedChromePaths -notcontains $chromeExecutable"
    )
    trusted_acl_check = source.index(
        "Assert-InagoTrustedSourceTree -Root (Split-Path -Parent $chromeExecutable)"
    )
    first_registration = source.index("Register-ScheduledTask -TaskName")

    assert standard_install_path_check < trusted_acl_check < first_registration


def test_trusted_source_tree_requires_approved_owners_for_ancestors() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / "automation" / "inago" / "storage_security.ps1").read_text(
        encoding="utf-8"
    )

    ancestor_check = source.index("Assert-InagoStorageAcl -Path $cursor")
    ancestor_check_end = source.index("\n        }", ancestor_check)

    assert "-Mode 'trusted-source'" in source[ancestor_check:ancestor_check_end]
    assert (
        "-RequireAllowedOwner $false" not in source[ancestor_check:ancestor_check_end]
    )


def test_user_profile_trust_checks_require_an_approved_owner() -> None:
    project_root = Path(__file__).resolve().parents[1]
    for relative in (
        "automation/inago/prepare_runtime_storage.ps1",
        "automation/inago/register_tasks.ps1",
    ):
        source = (project_root / relative).read_text(encoding="utf-8")
        assert "-RequireAllowedOwner $false" not in source


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
@pytest.mark.parametrize(
    ("mode", "extra_right"),
    [("private", "Read"), ("trusted-source", "Modify")],
)
def test_storage_acl_rejects_unapproved_read_or_write_grants(
    tmp_path: Path, mode: str, extra_right: str
) -> None:
    powershell = __import__("shutil").which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    project_root = Path(__file__).resolve().parents[1]
    module = project_root / "automation" / "inago" / "storage_security.ps1"
    root = tmp_path / "acl-fixture"
    root.mkdir()
    script = tmp_path / "verify-storage-acl.ps1"
    module_literal = str(module).replace("'", "''")
    script.write_text(
        f"""param([string]$Root, [string]$Mode, [string]$ExtraRight)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
. '{module_literal}'
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
$allowed = @($owner.Value, 'S-1-5-18', 'S-1-5-32-544')
$acl = Get-Acl -LiteralPath $Root
$acl.SetAccessRuleProtection($true, $false)
$acl.SetOwner($owner)
foreach ($sidValue in $allowed) {{
    $sid = [Security.Principal.SecurityIdentifier]::new($sidValue)
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    $acl.AddAccessRule($rule)
}}
$everyone = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
$extra = [System.Security.AccessControl.FileSystemAccessRule]::new(
    $everyone,
    [System.Security.AccessControl.FileSystemRights]::$ExtraRight,
    [System.Security.AccessControl.AccessControlType]::Allow
)
$acl.AddAccessRule($extra)
Set-Acl -LiteralPath $Root -AclObject $acl
try {{
    Assert-InagoStorageAcl -Path $Root -AllowedSids $allowed -Mode $Mode
    Write-Output 'ACCEPTED'
    exit 0
}} catch {{
    Write-Output 'REJECTED'
    exit 42
}}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Root",
            str(root),
            "-Mode",
            mode,
            "-ExtraRight",
            extra_right,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 42, result.stderr
    assert result.stdout.strip() == "REJECTED"


def test_metrics_process_receives_the_configured_chrome_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from automation.inago.metrics_live_port import NodeMetricsPort

    runtime = tmp_path / "runtime"
    output_root = runtime / "artifacts" / "metrics"
    script = runtime / "metrics_live.mjs"
    script.parent.mkdir(parents=True)
    script.write_text("// fixture", encoding="utf-8")
    browser = tmp_path / "configured chrome.exe"
    browser.write_text("fixture", encoding="utf-8")
    port = NodeMetricsPort(
        node_executable="node.exe",
        browser_executable=browser,
        script_path=script,
        output_root=output_root,
        runtime_root=runtime,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
        affiliate_auth_state=tmp_path / "affiliate-auth-state.json",
        timeout_seconds=60,
        environment={"FIXTURE_ENV": "preserved"},
    )
    observed_at = "2026-10-15T04:00:00Z"
    documents = {
        "affiliate.json": {
            "status": "unavailable",
            "observedAt": observed_at,
            "daily": {"status": "unavailable", "observedAt": observed_at, "rows": []},
            "orders": [],
            "orderWindows": [],
        },
        "room.json": {
            "status": "unavailable",
            "observedAt": observed_at,
            "windowStart": "2026-09-15T04:00:00Z",
            "posts": [],
        },
    }
    captured: dict[str, str] = {}
    attempt = output_root / "attempt-fixture"

    class FakeProcess:
        returncode = 0

        @staticmethod
        def poll() -> int:
            return 0

        def communicate(self, timeout: int) -> tuple[bytes, bytes]:
            assert timeout > 0
            return json.dumps({"attemptDir": str(attempt)}).encode(), b""

    def fake_popen(command, *, env, **kwargs):
        captured.update(env)
        attempt.mkdir(parents=True)
        for name, value in documents.items():
            (attempt / name).write_text(json.dumps(value), encoding="utf-8")
        return FakeProcess()

    monkeypatch.setattr(
        "automation.inago.metrics_live_port.subprocess.Popen", fake_popen
    )

    class FakeConnection:
        @staticmethod
        def execute(query: str, params: tuple[str, ...]):
            assert "target_date" in query
            assert params == ("RUN-CHROME",)
            return SimpleNamespace(fetchone=lambda: ("2026-10-15",))

    result = port.collect(
        FakeConnection(),
        run_id="RUN-CHROME",
        deadline_at=(datetime.now().astimezone() + timedelta(minutes=5)).isoformat(),
    )

    assert result.run_id == "RUN-CHROME"
    assert captured["INAGO_BROWSER_EXECUTABLE"] == str(browser.resolve())
    assert captured["FIXTURE_ENV"] == "preserved"


def test_public_components_pass_configured_chrome_to_metrics_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import automation.inago.production_runtime as production_runtime
    import automation.inago.daily_ai as daily_ai
    import automation.inago.live_sources as live_sources
    import automation.inago.metrics_live_port as metrics_live_port

    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "InagoRuntime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    captured: dict[str, object] = {}
    fake_metrics = SimpleNamespace(cancel_run=lambda run_id: True)

    monkeypatch.setattr(
        production_runtime,
        "register_production_settings",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        production_runtime, "_make_copy_spec_factory", lambda *args: object()
    )
    monkeypatch.setattr(daily_ai, "DailyAIRootRunner", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        live_sources, "PublicBrowserSource", lambda source_config: object()
    )

    def fake_metrics_port(**kwargs):
        captured.update(kwargs)
        return fake_metrics

    monkeypatch.setattr(metrics_live_port, "NodeMetricsPort", fake_metrics_port)

    components = production_runtime.build_public_daily_components(
        config,
        object(),
        clock=SimpleNamespace(
            now_jst=lambda: datetime(2026, 10, 15, 13, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        ),
    )

    assert captured["browser_executable"] == config.chrome_executable
    assert components["metrics_port"] is fake_metrics


def test_production_cli_rejects_paths_outside_private_runtime_before_opening_db(
    tmp_path: Path,
) -> None:
    from io import StringIO

    stdout = StringIO()
    stderr = StringIO()
    result = main(
        [
            "daily",
            "--environment",
            "production",
            "--config",
            str(tmp_path / "settings.json"),
            "--data-root",
            str(tmp_path),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert result == EXIT_RUNTIME_ERROR
    assert json.loads(stderr.getvalue())["status"] == "runtime_error"
    assert not (tmp_path / "state.sqlite").exists()
