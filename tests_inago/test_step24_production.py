from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from types import SimpleNamespace
from typing import TypedDict, cast
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from automation.inago.metrics_live_port import metrics_result_from_documents
from automation.inago.production_runtime import (
    ProductionRuntimeError,
    _write_setup_state,
    load_human_api_config,
    load_production_config,
    initialize_production_runtime,
    require_production_ready,
    run_production_daily,
)
from automation.inago.cli import EXIT_RUNTIME_ERROR, main


@pytest.fixture(autouse=True)
def repository_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("automation.inago.production_runtime.PROJECT_ROOT", tmp_path)
    from automation.inago import production_runtime

    monkeypatch.setattr(
        production_runtime,
        "_windows_known_folder",
        lambda name: tmp_path if name == "profile" else tmp_path / "LocalAppData",
        raising=False,
    )


def _write_config(root: Path, *, owner_room_url: str) -> Path:
    runtime = root / "rakuten-room-inago" / "runtime"
    (runtime / "config").mkdir(parents=True)
    application_root = root
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
    auth = root / "AffiliatePrivate" / "auth-state.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}", encoding="utf-8")
    legacy = (
        root
        / "archive"
        / "legacy-bakemono-20261015"
        / "local-preserved"
        / "rakuten-room-kai"
    )
    legacy.mkdir(parents=True)
    document = {
        "schema_version": 1,
        "environment": "production",
        "runtime_root": str(runtime),
        "application_root": str(application_root),
        "backup_root": str(root / "rakuten-room-inago" / "backups"),
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
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )

    assert (
        config.runtime_root == (tmp_path / "rakuten-room-inago" / "runtime").resolve()
    )
    assert config.start_date == "2026-10-15"
    assert config.detailed_rules_enabled is False
    assert "room_0123456789" not in repr(config)


def test_ready_runtime_does_not_need_local_legacy_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    legacy_root = (
        tmp_path
        / "archive"
        / "legacy-bakemono-20261015"
        / "local-preserved"
        / "rakuten-room-kai"
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    from automation.inago import backup, production_runtime

    existing_connection = production_runtime._open_production_database(config)
    existing_connection.close()
    legacy_root.rmdir()
    setup_state = config.runtime_root / "state" / "setup-status.json"
    setup_state.parent.mkdir(parents=True)
    setup_state.write_text(
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

    assert require_production_ready(config)["status"] == "ready"
    assert config.legacy_source_root == legacy_root
    assert not legacy_root.exists()

    monkeypatch.setattr(
        production_runtime, "_ensure_human_api_token", lambda _config: None
    )
    monkeypatch.setattr(production_runtime, "_ensure_codex_home", lambda _config: None)
    monkeypatch.setattr(
        production_runtime,
        "register_production_settings",
        lambda *_args, **_kwargs: SimpleNamespace(
            active_basic_version="confirmed-0.3-step46-all132"
        ),
    )
    backup_directory = config.backup_root / "daily-20261015"
    monkeypatch.setattr(backup, "list_daily_backups", lambda _root: ())
    monkeypatch.setattr(
        backup,
        "create_daily_backup",
        lambda *_args, **_kwargs: SimpleNamespace(directory=backup_directory),
    )

    result = initialize_production_runtime(config, now="2026-10-15T12:00:00+09:00")

    assert result["settings_version"] == "confirmed-0.3-step46-all132"
    assert result["backup_generation"] == "daily-20261015"
    assert result["migration"] == {}
    assert not legacy_root.exists()


def test_ready_runtime_rejects_missing_database_without_creating_one(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    legacy_root = (
        tmp_path
        / "archive"
        / "legacy-bakemono-20261015"
        / "local-preserved"
        / "rakuten-room-kai"
    )
    legacy_root.rmdir()
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    setup_state = config.runtime_root / "state" / "setup-status.json"
    setup_state.parent.mkdir(parents=True)
    setup_state.write_text(
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

    with pytest.raises(sqlite3.OperationalError, match="database is unavailable"):
        initialize_production_runtime(config, now="2026-10-15T12:00:00+09:00")

    assert not (config.runtime_root / "state.sqlite").exists()
    assert not (config.runtime_root / "secrets" / "human-api-token.json").exists()
    assert json.loads(setup_state.read_text(encoding="utf-8"))["status"] == "ready"


def test_setup_migration_requires_archived_legacy_source(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    legacy_root = (
        tmp_path
        / "archive"
        / "legacy-bakemono-20261015"
        / "local-preserved"
        / "rakuten-room-kai"
    )
    legacy_root.rmdir()
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    with pytest.raises(
        ProductionRuntimeError, match="archived legacy source is required"
    ):
        initialize_production_runtime(config, now="2026-10-15T12:00:00+09:00")

    assert not (config.runtime_root / "state.sqlite").exists()
    assert not (config.runtime_root / "secrets" / "human-api-token.json").exists()
    assert not (config.runtime_root / "state" / "setup-status.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_setup_migration_rejects_legacy_junction_added_after_config_load(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    legacy_root = (
        tmp_path
        / "archive"
        / "legacy-bakemono-20261015"
        / "local-preserved"
        / "rakuten-room-kai"
    )
    legacy_target = tmp_path / "redirected-legacy"
    legacy_root.replace(legacy_target)
    _create_junction(legacy_root, legacy_target)

    with pytest.raises(ProductionRuntimeError, match="reparse points"):
        initialize_production_runtime(config, now="2026-10-15T12:00:00+09:00")

    assert not (config.runtime_root / "state.sqlite").exists()
    assert not (config.runtime_root / "secrets" / "human-api-token.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_migration_roots_reject_a_junction(
    tmp_path: Path,
) -> None:
    from automation.inago.migrate import MigrationInputError, _roots

    source_target = tmp_path / "source-target"
    source_target.mkdir()
    source_alias = tmp_path / "source-alias"
    _create_junction(source_alias, source_target)
    data_root = tmp_path / "data"
    data_root.mkdir()

    with pytest.raises(MigrationInputError, match="reparse points"):
        _roots(source_alias, data_root, fixture_mode=False)


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_migration_source_file_rejects_nested_junction(tmp_path: Path) -> None:
    from automation.inago.migrate import MigrationInputError, _source_file

    source_root = tmp_path / "source"
    source_root.mkdir()
    external_directory = tmp_path / "external"
    external_directory.mkdir()
    (external_directory / "legacy.json").write_text("{}", encoding="utf-8")
    _create_junction(source_root / "linked", external_directory)

    with pytest.raises(MigrationInputError, match="reparse point"):
        _source_file(source_root, "linked/legacy.json")


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_setup_migration_rechecks_source_before_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    legacy_root = config.legacy_source_root
    legacy_target = tmp_path / "redirected-legacy-after-dry-run"
    from automation.inago import migrate, production_runtime

    monkeypatch.setattr(
        production_runtime, "_ensure_human_api_token", lambda _config: None
    )
    monkeypatch.setattr(production_runtime, "_ensure_codex_home", lambda _config: None)
    monkeypatch.setattr(
        production_runtime,
        "register_production_settings",
        lambda *_args, **_kwargs: SimpleNamespace(
            active_basic_version="confirmed-0.3-step46-all132"
        ),
    )
    modes: list[str] = []

    def fake_migrate(_connection, *, mode: str, **_kwargs):
        modes.append(mode)
        if mode == "dry_run":
            legacy_root.replace(legacy_target)
            _create_junction(legacy_root, legacy_target)
            return SimpleNamespace(supplementation_required=())
        pytest.fail("apply must not run after the archive source changes")

    monkeypatch.setattr(migrate, "migrate_legacy_history", fake_migrate)

    with pytest.raises(ProductionRuntimeError, match="reparse points"):
        initialize_production_runtime(config, now="2026-10-15T12:00:00+09:00")

    assert modes == ["dry_run"]
    assert not (config.runtime_root / "state" / "setup-status.json").exists()


@pytest.mark.parametrize("status", [[], {"unexpected": "shape"}])
def test_setup_migration_rejects_non_string_existing_status(
    tmp_path: Path, status: object
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    setup_state = config.runtime_root / "state" / "setup-status.json"
    setup_state.parent.mkdir(parents=True)
    setup_state.write_text(
        json.dumps({"schema_version": 1, "status": status}),
        encoding="utf-8",
    )

    with pytest.raises(ProductionRuntimeError, match="unsupported state"):
        initialize_production_runtime(config, now="2026-10-15T12:00:00+09:00")

    assert not (config.runtime_root / "state.sqlite").exists()
    assert not (config.runtime_root / "secrets" / "human-api-token.json").exists()


def test_human_api_config_does_not_require_browser_or_ai_dependencies(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    value = json.loads(config_path.read_text(encoding="utf-8"))
    value["browser"] = {"user_data_dir": str(tmp_path / "missing-profile")}
    value["codex"] = {}
    value["metrics"] = {}
    value["legacy"] = {}
    config_path.write_text(json.dumps(value), encoding="utf-8")

    config = load_human_api_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
    )

    assert config.runtime_root == tmp_path / "rakuten-room-inago" / "runtime"
    assert config.human_api_port == 8765


def test_human_api_config_rejects_unverified_previous_runtime_database(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    previous_database = tmp_path / "InagoRuntime" / "state.sqlite"
    previous_database.parent.mkdir()
    previous_database.write_bytes(b"legacy database sentinel")

    with pytest.raises(
        ProductionRuntimeError,
        match="external Inago database must be transferred",
    ):
        load_human_api_config(
            config_path,
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
        )

    assert previous_database.read_bytes() == b"legacy database sentinel"
    assert not (config_path.parents[1] / "state.sqlite").exists()


def test_human_api_database_open_does_not_create_missing_database(
    tmp_path: Path,
) -> None:
    import sqlite3
    from automation.inago.production_runtime import _open_existing_production_database

    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_human_api_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
    )

    with pytest.raises(sqlite3.OperationalError):
        _open_existing_production_database(config)

    assert not (config.runtime_root / "state.sqlite").exists()


def test_human_api_database_open_preserves_durable_write_settings(
    tmp_path: Path,
) -> None:
    from automation.inago.production_runtime import _open_existing_production_database
    from automation.inago.repository import SQLITE_BUSY_TIMEOUT_MS, open_database

    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_human_api_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
    )
    initialized = open_database(SimpleNamespace(data_root=config.runtime_root))
    initialized.close()

    connection = _open_existing_production_database(config)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert (
            connection.execute("PRAGMA busy_timeout").fetchone()[0]
            == SQLITE_BUSY_TIMEOUT_MS
        )
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        connection.close()


def test_human_api_database_open_does_not_initialize_unrecognized_database(
    tmp_path: Path,
) -> None:
    import sqlite3
    from automation.inago.contracts import UnsupportedSchemaVersion
    from automation.inago.production_runtime import _open_existing_production_database

    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_human_api_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
    )
    database_path = config.runtime_root / "state.sqlite"
    database_path.touch()

    with pytest.raises(UnsupportedSchemaVersion):
        _open_existing_production_database(config)

    connection = sqlite3.connect(database_path)
    try:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        connection.close()
    assert tables == []


def test_serve_cli_uses_minimal_human_api_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import automation.inago.production_runtime as production

    captured: dict[str, object] = {}

    def load_human(config_path, *, data_root):
        captured["config_path"] = config_path
        captured["data_root"] = data_root
        return SimpleNamespace(runtime_root=tmp_path)

    def reject_full_config(*args, **kwargs):
        raise AssertionError("serve must not validate browser or AI dependencies")

    monkeypatch.setattr(production, "load_human_api_config", load_human)
    monkeypatch.setattr(production, "load_production_config", reject_full_config)
    monkeypatch.setattr(production, "require_production_ready", lambda config: {})
    monkeypatch.setattr(
        production,
        "serve_human_api",
        lambda config: (_ for _ in ()).throw(RuntimeError("server fixture stopped")),
    )
    monkeypatch.setattr(
        "automation.inago.cli._append_production_log", lambda *a, **k: None
    )

    stdout, stderr = io.StringIO(), io.StringIO()
    result = main(
        [
            "serve",
            "--environment",
            "production",
            "--config",
            str(tmp_path / "settings.json"),
            "--data-root",
            str(tmp_path / "runtime"),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert result == EXIT_RUNTIME_ERROR
    assert captured == {
        "config_path": str(tmp_path / "settings.json"),
        "data_root": str(tmp_path / "runtime"),
    }


def test_production_config_rejects_profile_outside_current_windows_user(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )

    with pytest.raises(ProductionRuntimeError, match="current Windows user's profile"):
        load_production_config(
            config_path,
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
            user_profile=tmp_path / "another-user-profile",
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


def test_production_config_rejects_local_app_data_outside_current_windows_user(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )

    with pytest.raises(
        ProductionRuntimeError, match="current Windows user.*LocalAppData directory"
    ):
        load_production_config(
            config_path,
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "another-local-app-data",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


def test_production_config_resolves_expected_dedicated_browser_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from automation.inago import production_runtime

    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    resolved_profile = tmp_path / "PackagedLocalCache" / "InagoPlaywrightProfile"
    resolved_profile.mkdir(parents=True)
    expected_profile = tmp_path / "LocalAppData" / "InagoPlaywrightProfile"
    original_check = production_runtime._checked_private_path

    def resolve_packaged_profile(value: object, name: str) -> Path:
        result = original_check(value, name)
        if name in {"user_data_dir", "expected dedicated browser profile"} and (
            Path(value) == expected_profile
        ):
            return resolved_profile
        return result

    monkeypatch.setattr(
        production_runtime, "_checked_private_path", resolve_packaged_profile
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )

    assert config.user_data_dir == resolved_profile

    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["browser"]["user_data_dir"] = str(tmp_path / "OtherProfile")
    config_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ProductionRuntimeError):
        load_production_config(
            config_path,
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


def test_production_config_reports_missing_dedicated_browser_profile(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    profile = tmp_path / "LocalAppData" / "InagoPlaywrightProfile"
    profile.rmdir()

    with pytest.raises(
        ProductionRuntimeError,
        match="dedicated ROOM browser profile directory is missing",
    ):
        load_production_config(
            config_path,
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


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
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


def test_production_config_rejects_boolean_schema_version(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["schema_version"] = True
    config_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ProductionRuntimeError):
        load_production_config(
            config_path,
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
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
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
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
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )


def test_production_config_rejects_legacy_source_outside_repository(
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

    with pytest.raises(ProductionRuntimeError, match="archived local-preserved"):
        load_production_config(
            config_path,
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
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
    runtime_alias = tmp_path / "rakuten-room-inago" / "runtime"
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
    backup_alias = tmp_path / "rakuten-room-inago" / "backups"
    backup_target = tmp_path / "redirected-backups"
    backup_target.mkdir()
    _create_junction(backup_alias, backup_target)

    with pytest.raises(ProductionRuntimeError, match="reparse"):
        load_production_config(
            config_path,
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
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
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
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
    legacy_alias = (
        tmp_path
        / "archive"
        / "legacy-bakemono-20261015"
        / "local-preserved"
        / "rakuten-room-kai"
    )
    legacy_target = tmp_path / "redirected-legacy"
    legacy_alias.replace(legacy_target)
    _create_junction(legacy_alias, legacy_target)

    with pytest.raises(ProductionRuntimeError, match="reparse"):
        load_production_config(
            config_path,
            data_root=tmp_path / "rakuten-room-inago" / "runtime",
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
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
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
    assert not (tmp_path / "rakuten-room-inago" / "runtime" / "state.sqlite").exists()


def test_production_daily_startwhenavailable_does_not_run_before_1300_next_day(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
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
    assert not (tmp_path / "rakuten-room-inago" / "runtime" / "state.sqlite").exists()


def test_production_daily_rejects_setup_state_parent_junction(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
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

    result = run_production_daily(config, clock=DueClock())

    assert result["status"] == "failed"
    assert result["reason"] == "ProductionRuntimeError"
    assert not (config.runtime_root / "state.sqlite").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_setup_state_writer_rejects_parent_junction(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
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
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
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

    result = run_production_daily(config, clock=DueClock())

    assert result["status"] == "failed"
    assert result["reason"] == "ProductionRuntimeError"
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
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
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

    result = run_production_daily(config, clock=DueClock())

    assert result["status"] == "failed"
    assert result["reason"] == "ProductionRuntimeError"
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
    daily_wake_to_run = daily.findtext(
        ".//t:Settings/t:WakeToRun", namespaces=namespace
    )
    human_wake_to_run = human.findtext(
        ".//t:Settings/t:WakeToRun", namespaces=namespace
    )
    daily_args = daily.findtext(".//t:Actions/t:Exec/t:Arguments", namespaces=namespace)
    human_args = human.findtext(".//t:Actions/t:Exec/t:Arguments", namespaces=namespace)

    assert daily_boundary is not None and daily_boundary.text == "{{START_BOUNDARY}}"
    assert human_logon is not None
    assert daily_wake_to_run == "true"
    assert human_wake_to_run == "false"
    assert "daily --environment production" in (daily_args or "")
    assert "serve --environment production" in (human_args or "")
    assert "ROOM" not in (daily_args or "")
    assert "ROOM" not in (human_args or "")


def test_task_registration_uses_repository_python_without_acl_or_installer() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / "automation" / "inago" / "register_tasks.ps1").read_text(
        encoding="utf-8"
    )
    assert (
        "$pythonExe = Join-Path $applicationRoot '.venv-inago\\Scripts\\python.exe'"
        in source
    )
    assert "$applicationRoot = $projectRoot" in source
    assert "Get-Acl" not in source
    assert "Assert-Inago" not in source
    assert "install-manifest" not in source
    assert not (project_root / "automation/inago/install_runtime.ps1").exists()


def test_human_api_task_liveness_does_not_send_or_read_bearer_token() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / "automation" / "inago" / "register_tasks.ps1").read_text(
        encoding="utf-8"
    )
    probe = source.split("function Test-HumanApiLiveness", 1)[1].split("$dailyXml", 1)[
        0
    ]

    assert "/health" in probe
    assert "Authorization" not in probe
    assert "TokenPath" not in probe
    assert "human-api-token.json" not in probe


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
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
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


def test_production_cli_rejects_paths_outside_repository_runtime_before_opening_db(
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
    report = json.loads(stdout.getvalue())
    assert report["status"] == "blocked"
    assert report["failed_checks"]
    assert not stderr.getvalue()
    assert not (tmp_path / "state.sqlite").exists()


def test_production_cli_passes_explicit_profile_roots_to_runtime_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import automation.inago.production_runtime as production

    captured: dict[str, object] = {}

    def fake_load(config_path, *, data_root, user_profile=None, local_app_data=None):
        captured.update(
            config_path=config_path,
            data_root=data_root,
            user_profile=user_profile,
            local_app_data=local_app_data,
        )
        return SimpleNamespace(runtime_root=tmp_path)

    monkeypatch.setattr(production, "load_production_config", fake_load)
    monkeypatch.setattr(
        production,
        "initialize_production_runtime",
        lambda *_args, **_kwargs: {"settings_version": "fixture-settings"},
    )
    config_path = tmp_path / "settings.json"
    data_root = tmp_path / "runtime"
    user_profile = tmp_path / "UserProfile"
    local_app_data = tmp_path / "LocalAppData"

    result = main(
        [
            "setup",
            "--environment",
            "production",
            "--config",
            str(config_path),
            "--data-root",
            str(data_root),
            "--user-profile",
            str(user_profile),
            "--local-app-data",
            str(local_app_data),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert result == 0
    assert captured == {
        "config_path": str(config_path),
        "data_root": str(data_root),
        "user_profile": str(user_profile),
        "local_app_data": str(local_app_data),
    }


def test_production_cli_logs_sanitized_preflight_failure_to_repository_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import automation.inago.production_runtime as production
    import automation.inago.cli as cli

    runtime = tmp_path / "rakuten-room-inago" / "runtime"
    runtime.mkdir(parents=True)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path, raising=False)

    def fail_config(*args, **kwargs):
        raise ProductionRuntimeError("production configuration is missing")

    monkeypatch.setattr(production, "load_production_config", fail_config)
    stdout, stderr = io.StringIO(), io.StringIO()
    result = main(
        [
            "daily",
            "--environment",
            "production",
            "--config",
            str(runtime / "config" / "settings.json"),
            "--data-root",
            str(runtime),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert result == EXIT_RUNTIME_ERROR
    report = json.loads(stdout.getvalue())
    assert report["status"] == "blocked"
    assert "configuration_invalid" in report["failed_checks"]
    assert not stderr.getvalue()
    event = json.loads(
        (runtime / "logs" / "production-cli.jsonl").read_text(encoding="utf-8")
    )
    assert event["mode"] == "daily"
    assert event["status"] == "blocked"
    assert event["phase"] == "preflight"
    assert event["reason"] == "startup_preflight_blocked"
    assert event["diagnostics"] == {"failed_checks": ["configuration_invalid"]}


def test_production_cli_logs_profile_visibility_as_booleans_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import automation.inago.production_runtime as production
    import automation.inago.cli as cli

    config_path = _write_config(
        tmp_path,
        owner_room_url="https://room.rakuten.co.jp/room_0123456789/items",
    )
    runtime = config_path.parents[1]
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(
        production,
        "_windows_known_folder",
        lambda name: (
            tmp_path / "OtherLocalAppData" if name == "local_app_data" else tmp_path
        ),
    )

    def fail_profile(*args, **kwargs):
        raise ProductionRuntimeError(
            "dedicated ROOM browser profile directory is missing"
        )

    monkeypatch.setattr(production, "load_production_config", fail_profile)
    stdout, stderr = io.StringIO(), io.StringIO()
    result = main(
        [
            "daily",
            "--environment",
            "production",
            "--config",
            str(config_path),
            "--data-root",
            str(runtime),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert result == EXIT_RUNTIME_ERROR
    report = json.loads(stdout.getvalue())
    assert report["status"] == "blocked"
    assert "browser_profile_missing" in report["failed_checks"]
    assert not stderr.getvalue()
    event = json.loads(
        (runtime / "logs" / "production-cli.jsonl").read_text(encoding="utf-8")
    )
    diagnostics = event["diagnostics"]
    assert event["status"] == "blocked"
    assert event["phase"] == "preflight"
    assert "browser_profile_missing" in diagnostics["failed_checks"]
    assert diagnostics["configured_profile_exists"] is True
    assert diagnostics["configured_profile_is_directory"] is True
    assert diagnostics["current_local_app_data_profile_exists"] is False
    assert diagnostics["configured_profile_matches_current_local_app_data"] is False
    assert str(tmp_path) not in json.dumps(diagnostics)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction protection")
def test_production_cli_does_not_log_preflight_failure_through_runtime_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import automation.inago.production_runtime as production
    import automation.inago.cli as cli

    runtime = tmp_path / "rakuten-room-inago" / "runtime"
    runtime.parent.mkdir(parents=True)
    outside = tmp_path / "outside-runtime"
    outside.mkdir()
    _create_junction(runtime, outside)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path, raising=False)

    def fail_config(*args, **kwargs):
        raise ProductionRuntimeError("production configuration is missing")

    monkeypatch.setattr(
        production,
        "load_production_config",
        fail_config,
    )

    result = main(
        [
            "serve",
            "--environment",
            "production",
            "--config",
            str(runtime / "config" / "settings.json"),
            "--data-root",
            str(runtime),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert result == EXIT_RUNTIME_ERROR
    assert list(outside.iterdir()) == []


def test_repository_config_needs_no_install_manifest_or_copied_tools(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    assert config.application_root == tmp_path.resolve()
    assert config.codex_home == tmp_path / ".codex"
    assert not (config.runtime_root / "config/install-manifest.json").exists()
    assert not (config.runtime_root / "tools").exists()


def test_repository_config_accepts_installed_tools_without_copying(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    value = json.loads(config_path.read_text(encoding="utf-8"))
    tools = tmp_path / "InstalledTools"
    tools.mkdir()
    for name in ("node.exe", "codex.exe"):
        (tools / name).write_text("fixture", encoding="utf-8")
    value["browser"]["node_executable"] = str(tools / "node.exe")
    value["codex"]["command"] = [str(tools / "codex.exe")]
    config_path.write_text(json.dumps(value), encoding="utf-8")
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    assert config.node_executable == str(tools / "node.exe")
    assert config.codex_command == (str(tools / "codex.exe"),)


def test_codex_auth_is_referenced_without_creating_runtime_copy(tmp_path: Path) -> None:
    from automation.inago.production_runtime import _ensure_codex_home

    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago" / "runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    config.codex_home.mkdir()
    auth = config.codex_home / "auth.json"
    auth.write_text("fixture auth", encoding="utf-8")
    _ensure_codex_home(config)
    assert auth.read_text(encoding="utf-8") == "fixture auth"
    assert not (config.runtime_root / "codex-home").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows task XML generation")
def test_task_render_uses_repository_and_leaves_scheduler_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    from datetime import date

    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("PowerShell unavailable")
    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    document = json.loads(config_path.read_text(encoding="utf-8"))
    first_day = (date.today() + timedelta(days=1)).isoformat()
    document["start_date"] = first_day
    config_path.write_text(json.dumps(document), encoding="utf-8")
    setup = config_path.parents[1] / "state/setup-status.json"
    setup.parent.mkdir()
    setup.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ready",
                "migration_selection_allowed": True,
            }
        ),
        encoding="utf-8",
    )
    python = tmp_path / ".venv-inago/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("must not execute", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "automation/inago/register_tasks.ps1"
    user_profile = tmp_path / "UserProfile"
    local_app_data = tmp_path / "LocalAppData"
    environment = dict(
        os.environ,
        USERPROFILE=str(user_profile),
        LOCALAPPDATA=str(local_app_data),
    )
    process = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-ProjectRoot",
            str(tmp_path),
            "-StartBoundary",
            first_day + "T13:00:00+09:00",
            "-RenderOnly",
        ],
        capture_output=True,
        env=environment,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    rendered = json.loads(process.stdout.decode("utf-8-sig"))
    assert rendered["Registered"] is False
    namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    for name in ("DailyXml", "HumanApiXml"):
        assert not rendered[name].lstrip().startswith("<?xml")
        xml = ET.fromstring(rendered[name])
        working_directory = xml.find(".//t:WorkingDirectory", namespace)
        command = xml.find(".//t:Command", namespace)
        arguments = xml.findtext(".//t:Arguments", namespaces=namespace) or ""
        assert working_directory is not None
        assert Path(working_directory.text) == tmp_path
        assert command is not None and Path(command.text) == python
        assert "--user-profile" in arguments
        assert "--local-app-data" in arguments
        assert f'--user-profile "{user_profile}"' in arguments
        assert f'--local-app-data "{local_app_data}"' in arguments
        wake_to_run = xml.findtext(".//t:Settings/t:WakeToRun", namespaces=namespace)
        assert wake_to_run == ("false" if name == "HumanApiXml" else "true")
        if name == "HumanApiXml":
            principal_user = xml.findtext(
                ".//t:Principal/t:UserId", namespaces=namespace
            )
            trigger_user = xml.findtext(
                ".//t:LogonTrigger/t:UserId", namespaces=namespace
            )
            assert trigger_user == principal_user
    assert not (tmp_path / "InagoRuntime").exists()
    assert not (config_path.parent / "install-manifest.json").exists()


def test_existing_affiliate_auth_is_read_only_reference_without_copy(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    config = load_production_config(
        config_path,
        data_root=tmp_path / "rakuten-room-inago/runtime",
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    assert config.affiliate_auth_state == tmp_path / "AffiliatePrivate/auth-state.json"
    assert config.affiliate_auth_state.read_text(encoding="utf-8") == "{}"
    assert not (config.runtime_root / "secrets/affiliate-auth-state.json").exists()


def test_external_state_is_not_silently_abandoned_on_repository_setup(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    old_db = tmp_path / "InagoRuntime/state.sqlite"
    old_db.parent.mkdir()
    old_db.write_bytes(b"existing database sentinel")
    with pytest.raises(
        ProductionRuntimeError, match="external Inago database must be transferred"
    ):
        load_production_config(
            config_path,
            data_root=tmp_path / "rakuten-room-inago/runtime",
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )
    assert old_db.read_bytes() == b"existing database sentinel"
    assert not (config_path.parents[1] / "state.sqlite").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse point")
def test_lifecycle_log_rejects_junction_to_outside_repository(tmp_path: Path) -> None:
    from automation.inago.cli import _append_production_log

    runtime = tmp_path / "rakuten-room-inago/runtime"
    runtime.mkdir(parents=True)
    outside = tmp_path / "outside-log"
    outside.mkdir()
    _create_junction(runtime / "logs", outside)
    with pytest.raises(ProductionRuntimeError, match="reparse"):
        _append_production_log(runtime, mode="daily", status="starting")
    assert not list(outside.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows mocked scheduler")
@pytest.mark.parametrize(
    "state,kind,expected",
    [
        ("Ready", "known", "replaced"),
        ("Ready", "current_wake_disabled", "replaced"),
        ("Running", "known", "blocked"),
        ("Queued", "known", "blocked"),
        ("Unknown", "known", "blocked"),
        ("Disabled", "known", "replaced"),
        ("Ready", "custom", "blocked"),
    ],
)
def test_task_switch_only_replaces_known_idle_installed_definition(
    tmp_path: Path, state: str, kind: str, expected: str
) -> None:
    import shutil
    from datetime import date

    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("PowerShell unavailable")
    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["start_date"] = (date.today() + timedelta(days=1)).isoformat()
    config_path.write_text(json.dumps(document), encoding="utf-8")
    setup = config_path.parents[1] / "state/setup-status.json"
    setup.parent.mkdir()
    setup.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ready",
                "migration_selection_allowed": True,
            }
        ),
        encoding="utf-8",
    )
    python = tmp_path / ".venv-inago/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("not executable", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "automation/inago/register_tasks.ps1"
    harness = tmp_path / "task-switch-fixture.ps1"
    harness.write_text(
        r"""param([string]$RegistrationScript,[string]$Root,[string]$Boundary,[string]$State,[string]$Kind)
. $RegistrationScript -ProjectRoot $Root -StartBoundary $Boundary -RenderOnly | Out-Null
$previousRoot = Join-Path $env:USERPROFILE 'InagoRuntime'
$previousApp = Join-Path $previousRoot 'app-20261014-120000'
$previousPython = Join-Path $previousApp '.venv-inago\Scripts\python.exe'
$previousConfig = Join-Path $previousRoot 'config\settings.json'
$previousArgs = "-B -m automation.inago.cli daily --environment production --config `"$previousConfig`" --data-root `"$previousRoot`""
$taskExecute = $previousPython
$taskArgs = $previousArgs
$taskWorkingDirectory = $previousApp
$triggerBoundary = '2026-10-01T13:00:00+09:00'
if ($Kind -eq 'custom') { $taskArgs = 'custom task' }
if ($Kind -eq 'current_wake_disabled') {
    $taskExecute = $pythonExe
    $taskArgs = "-B -m automation.inago.cli daily --environment production --config `"$configPath`" --data-root `"$expectedRoot`" --user-profile `"$userProfile`" --local-app-data `"$localAppData`""
    $taskWorkingDirectory = $applicationRoot
    $triggerBoundary = $Boundary
}
$script:existing = [pscustomobject]@{
    Actions = @([pscustomobject]@{Execute=$taskExecute; Arguments=$taskArgs; WorkingDirectory=$taskWorkingDirectory})
    Principal = [pscustomobject]@{UserId=$identity.Name; LogonType='Interactive'; RunLevel='Limited'}
    Settings = [pscustomobject]@{MultipleInstances='IgnoreNew'; Hidden=$true; WakeToRun=$false}
    Triggers = @([pscustomobject]@{Enabled=$true; DaysInterval=1; StartBoundary=$triggerBoundary; CimClass=[pscustomobject]@{CimClassName='MSFT_TaskDailyTrigger'}})
    State = $State
}
$script:registered = @()
function Get-ScheduledTask { param($TaskName,$TaskPath,$ErrorAction); return $script:existing }
function Register-ScheduledTask { [CmdletBinding()] param($TaskName,$TaskPath,$Xml,[switch]$Force); $script:registered += [pscustomobject]@{Name=$TaskName; Xml=$Xml; Force=[bool]$Force} }
try { Register-ExactTask 'RakutenRoomInagoDaily' $dailyXml 'daily'; $status='replaced' } catch { $status='blocked' }
[pscustomobject]@{Status=$status; Registrations=@($script:registered)} | ConvertTo-Json -Depth 5 -Compress
""",
        encoding="utf-8",
    )
    process = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(harness),
            "-RegistrationScript",
            str(script),
            "-Root",
            str(tmp_path),
            "-Boundary",
            document["start_date"] + "T13:00:00+09:00",
            "-State",
            state,
            "-Kind",
            kind,
        ],
        capture_output=True,
        env=dict(
            os.environ,
            LOCALAPPDATA=str(tmp_path / "LocalAppData"),
            USERPROFILE=str(tmp_path / "UserProfile"),
        ),
        check=False,
    )
    assert process.returncode == 0, process.stderr
    observed = json.loads(process.stdout.decode("utf-8-sig"))
    assert observed["Status"] == expected
    if expected == "replaced":
        assert len(observed["Registrations"]) == 1
        registration = observed["Registrations"][0]
        assert registration["Force"] is True
        xml = ET.fromstring(registration["Xml"])
        assert (
            Path(
                xml.find(
                    ".//{http://schemas.microsoft.com/windows/2004/02/mit/task}WorkingDirectory"
                ).text
            )
            == tmp_path
        )
    else:
        assert observed["Registrations"] == []


class _DailyTaskRegistration(TypedDict):
    Name: str
    Xml: str
    Force: bool


class _DailyTaskRegistrationObservation(TypedDict):
    Status: str
    Registrations: list[_DailyTaskRegistration]
    User: str
    Error: str | None


def _prepare_daily_task_limit_drift_fixture(
    tmp_path: Path,
) -> tuple[str, Path, Path]:
    from datetime import date

    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    document = json.loads(config_path.read_text(encoding="utf-8"))
    start_date = (date.today() + timedelta(days=1)).isoformat()
    document["start_date"] = start_date
    config_path.write_text(json.dumps(document), encoding="utf-8")
    setup = config_path.parents[1] / "state/setup-status.json"
    setup.parent.mkdir()
    setup.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ready",
                "migration_selection_allowed": True,
            }
        ),
        encoding="utf-8",
    )
    python = tmp_path / ".venv-inago/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("not executable", encoding="utf-8")
    return f"{start_date}T13:00:00+09:00", config_path, python


def _write_daily_task_limit_drift_harness(tmp_path: Path) -> Path:
    harness = tmp_path / "daily-task-limit-drift-fixture.ps1"
    harness.write_text(
        r"""param([string]$RegistrationScript,[string]$Root,[string]$Boundary,[string]$ExecutionLimit,[string]$State)
. $RegistrationScript -ProjectRoot $Root -StartBoundary $Boundary -RenderOnly | Out-Null
$expectedArgs = "-B -m automation.inago.cli daily --environment production --config `"$configPath`" --data-root `"$expectedRoot`" --user-profile `"$userProfile`" --local-app-data `"$localAppData`""
$script:existing = [pscustomobject]@{
    Actions = @([pscustomobject]@{Execute=$pythonExe; Arguments=$expectedArgs; WorkingDirectory=$applicationRoot})
    Principal = [pscustomobject]@{UserId=$userId; LogonType='Interactive'; RunLevel='Limited'}
    Settings = [pscustomobject]@{MultipleInstances='IgnoreNew'; Hidden=$true; WakeToRun=$true; ExecutionTimeLimit=$ExecutionLimit}
    Triggers = @([pscustomobject]@{Enabled=$true; DaysInterval=1; StartBoundary=$Boundary; CimClass=[pscustomobject]@{CimClassName='MSFT_TaskDailyTrigger'}})
    State = $State
}
$script:registered = @()
function Get-ScheduledTask { [CmdletBinding()] param($TaskName,$TaskPath); return $script:existing }
function Register-ScheduledTask { [CmdletBinding()] param($TaskName,$TaskPath,$Xml,[switch]$Force); $script:registered += [pscustomobject]@{Name=$TaskName; Xml=$Xml; Force=[bool]$Force} }
try {
    Register-ExactTask 'RakutenRoomInagoDaily' $dailyXml 'daily'
    $status = if ($script:registered.Count -gt 0) { 'replaced' } else { 'accepted' }
} catch { $status='blocked'; $errorMessage = $_.Exception.Message }
[pscustomobject]@{Status=$status; Registrations=@($script:registered); User=$userId; Error=$errorMessage} | ConvertTo-Json -Depth 5 -Compress
""",
        encoding="utf-8",
    )
    return harness


def _run_daily_task_limit_drift_harness(
    tmp_path: Path,
    harness: Path,
    script: Path,
    boundary: str,
    execution_limit: str,
    state: str,
    powershell: str,
) -> _DailyTaskRegistrationObservation:
    process = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(harness),
            "-RegistrationScript",
            str(script),
            "-Root",
            str(tmp_path),
            "-Boundary",
            boundary,
            "-ExecutionLimit",
            execution_limit,
            "-State",
            state,
        ],
        capture_output=True,
        env=dict(
            os.environ,
            LOCALAPPDATA=str(tmp_path / "LocalAppData"),
            USERPROFILE=str(tmp_path / "UserProfile"),
        ),
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout.decode("utf-8-sig"))
    assert isinstance(result, dict)
    assert isinstance(result.get("Status"), str)
    assert isinstance(result.get("Registrations"), list)
    assert isinstance(result.get("User"), str)
    assert result.get("Error") is None or isinstance(result["Error"], str)
    for registration in result["Registrations"]:
        assert isinstance(registration, dict)
        assert isinstance(registration.get("Name"), str)
        assert isinstance(registration.get("Xml"), str)
        assert isinstance(registration.get("Force"), bool)
    return cast(_DailyTaskRegistrationObservation, result)


def _assert_daily_task_action_xml(
    xml: ET.Element,
    namespace: dict[str, str],
    tmp_path: Path,
    config_path: Path,
    python: Path,
) -> None:
    runtime_root = config_path.parents[1]
    assert (
        Path(xml.findtext(".//t:Actions/t:Exec/t:Command", namespaces=namespace))
        == python
    )
    assert xml.findtext(
        ".//t:Actions/t:Exec/t:WorkingDirectory", namespaces=namespace
    ) == str(tmp_path)
    expected_args = (
        "-B -m automation.inago.cli daily --environment production --config "
        f'"{runtime_root / "config/settings.json"}" --data-root "{runtime_root}" '
        f'--user-profile "{tmp_path / "UserProfile"}" '
        f'--local-app-data "{tmp_path / "LocalAppData"}"'
    )
    assert (
        xml.findtext(".//t:Actions/t:Exec/t:Arguments", namespaces=namespace)
        == expected_args
    )


def _assert_daily_task_trigger_xml(
    xml: ET.Element, namespace: dict[str, str], boundary: str
) -> None:
    assert (
        xml.findtext(
            ".//t:Triggers/t:CalendarTrigger/t:ScheduleByDay/t:DaysInterval",
            namespaces=namespace,
        )
        == "1"
    )
    assert (
        xml.findtext(
            ".//t:Triggers/t:CalendarTrigger/t:StartBoundary", namespaces=namespace
        )
        == boundary
    )


def _assert_daily_task_replacement_xml(
    observed: _DailyTaskRegistrationObservation,
    tmp_path: Path,
    config_path: Path,
    python: Path,
    boundary: str,
) -> None:
    registration = observed["Registrations"][0]
    assert registration["Name"] == "RakutenRoomInagoDaily"
    assert registration["Force"] is True
    xml = ET.fromstring(registration["Xml"])
    namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert (
        xml.findtext(".//t:Settings/t:ExecutionTimeLimit", namespaces=namespace)
        == "PT0S"
    )
    assert xml.findtext(".//t:Settings/t:WakeToRun", namespaces=namespace) == "true"
    assert (
        xml.findtext(".//t:Principal/t:UserId", namespaces=namespace)
        == observed["User"]
    )
    assert (
        xml.findtext(".//t:Principal/t:RunLevel", namespaces=namespace)
        == "LeastPrivilege"
    )
    _assert_daily_task_action_xml(xml, namespace, tmp_path, config_path, python)
    _assert_daily_task_trigger_xml(xml, namespace, boundary)


@pytest.mark.skipif(os.name != "nt", reason="Windows mocked scheduler")
@pytest.mark.parametrize(
    "execution_limit,state,expected,registration_count",
    [
        ("PT6H", "Ready", "replaced", 1),
        ("PT6H", "Disabled", "replaced", 1),
        ("PT6H", "Running", "blocked", 0),
        ("PT0S", "Ready", "accepted", 0),
        ("00:00:00", "Ready", "accepted", 0),
    ],
)
def test_daily_task_execution_time_limit_drift_is_repaired_only_when_idle(
    tmp_path: Path,
    execution_limit: str,
    state: str,
    expected: str,
    registration_count: int,
) -> None:
    import shutil

    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("PowerShell unavailable")
    boundary, config_path, python = _prepare_daily_task_limit_drift_fixture(tmp_path)
    script = Path(__file__).resolve().parents[1] / "automation/inago/register_tasks.ps1"
    harness = _write_daily_task_limit_drift_harness(tmp_path)
    observed = _run_daily_task_limit_drift_harness(
        tmp_path, harness, script, boundary, execution_limit, state, powershell
    )

    assert observed["Status"] == expected, observed.get("Error")
    assert len(observed["Registrations"]) == registration_count
    if registration_count:
        _assert_daily_task_replacement_xml(
            observed, tmp_path, config_path, python, boundary
        )
    else:
        assert observed["Registrations"] == []


@pytest.mark.skipif(os.name != "nt", reason="Windows mocked scheduler")
@pytest.mark.parametrize(
    "definition,expected,registration_count",
    [
        ("previous", "accepted", 1),
        ("current", "accepted", 0),
        ("current_sid", "accepted", 0),
        ("current_mismatch", "blocked", 0),
        ("principal_mismatch", "blocked", 0),
    ],
)
def test_previous_repository_human_task_without_trigger_user_is_updated(
    tmp_path: Path, definition: str, expected: str, registration_count: int
) -> None:
    import shutil
    from datetime import date

    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("PowerShell unavailable")
    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["start_date"] = (date.today() + timedelta(days=1)).isoformat()
    config_path.write_text(json.dumps(document), encoding="utf-8")
    setup = config_path.parents[1] / "state/setup-status.json"
    setup.parent.mkdir()
    setup.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ready",
                "migration_selection_allowed": True,
            }
        ),
        encoding="utf-8",
    )
    python = tmp_path / ".venv-inago/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("not executable", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "automation/inago/register_tasks.ps1"
    harness = tmp_path / "human-api-task-switch-fixture.ps1"
    harness.write_text(
        r"""param([string]$RegistrationScript,[string]$Root,[string]$Boundary,[string]$Definition)
. $RegistrationScript -ProjectRoot $Root -StartBoundary $Boundary -RenderOnly | Out-Null
$previousRepositoryArgs = "-B -m automation.inago.cli serve --environment production --config `"$configPath`" --data-root `"$expectedRoot`""
$triggerUser = $null
if ($Definition -eq 'current' -or $Definition -eq 'current_sid' -or $Definition -eq 'current_mismatch' -or $Definition -eq 'principal_mismatch') {
    $previousRepositoryArgs = "-B -m automation.inago.cli serve --environment production --config `"$configPath`" --data-root `"$expectedRoot`" --user-profile `"$userProfile`" --local-app-data `"$localAppData`""
    $triggerUser = $userId
}
if ($Definition -eq 'current_mismatch') { $triggerUser = 'S-1-5-18' }
$principalUser = $identity.Name
if ($Definition -eq 'current_sid') { $principalUser = $userId }
if ($Definition -eq 'principal_mismatch') { $principalUser = 'S-1-5-18' }
$script:existing = [pscustomobject]@{
    Actions = @([pscustomobject]@{Execute=$pythonExe; Arguments=$previousRepositoryArgs; WorkingDirectory=$applicationRoot})
    Principal = [pscustomobject]@{UserId=$principalUser; LogonType='Interactive'; RunLevel='Limited'}
    Settings = [pscustomobject]@{MultipleInstances='IgnoreNew'; Hidden=$true; WakeToRun=$false; ExecutionTimeLimit='PT0S'}
    Triggers = @([pscustomobject]@{Enabled=$true; UserId=$triggerUser; CimClass=[pscustomobject]@{CimClassName='MSFT_TaskLogonTrigger'}})
    State = 'Ready'
}
$script:registered = @()
function Get-ScheduledTask { [CmdletBinding()] param($TaskName,$TaskPath); return $script:existing }
function Register-ScheduledTask { [CmdletBinding()] param($TaskName,$TaskPath,$Xml,[switch]$Force); $script:registered += [pscustomobject]@{Name=$TaskName; Xml=$Xml; Force=[bool]$Force} }
try { Register-ExactTask 'RakutenRoomInagoHumanApi' $humanXml 'serve'; $status='accepted' } catch { $status='blocked' }
[pscustomobject]@{Status=$status; Registrations=@($script:registered); User=$userId} | ConvertTo-Json -Depth 5 -Compress
""",
        encoding="utf-8",
    )
    process = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(harness),
            "-RegistrationScript",
            str(script),
            "-Root",
            str(tmp_path),
            "-Boundary",
            document["start_date"] + "T13:00:00+09:00",
            "-Definition",
            definition,
        ],
        capture_output=True,
        env=dict(
            os.environ,
            LOCALAPPDATA=str(tmp_path / "LocalAppData"),
            USERPROFILE=str(tmp_path / "UserProfile"),
        ),
        check=False,
    )

    assert process.returncode == 0, process.stderr
    observed = json.loads(process.stdout.decode("utf-8-sig"))
    assert observed["Status"] == expected
    assert len(observed["Registrations"]) == registration_count
    if registration_count:
        registration = observed["Registrations"][0]
        assert registration["Force"] is True
        xml = ET.fromstring(registration["Xml"])
        namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        assert (
            xml.findtext(".//t:LogonTrigger/t:UserId", namespaces=namespace)
            == observed["User"]
        )


def test_new_database_file_alone_does_not_prove_external_history_transfer(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    old_db = tmp_path / "InagoRuntime/state.sqlite"
    old_db.parent.mkdir()
    old_db.write_bytes(b"old state")
    new_db = config_path.parents[1] / "state.sqlite"
    new_db.touch()
    with pytest.raises(ProductionRuntimeError, match="content verified"):
        load_production_config(
            config_path,
            data_root=config_path.parents[1],
            user_profile=tmp_path,
            local_app_data=tmp_path / "LocalAppData",
            which=lambda command: str(command) if Path(command).is_file() else None,
        )
    assert old_db.read_bytes() == b"old state"
    assert new_db.stat().st_size == 0


@pytest.mark.parametrize("mode,expected", [("setup", "ready"), ("daily", "completed")])
def test_successful_production_result_survives_lifecycle_log_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, expected: str
) -> None:
    import io
    import automation.inago.production_runtime as production

    config = SimpleNamespace(runtime_root=tmp_path)
    monkeypatch.setattr(
        production, "load_production_config", lambda *args, **kwargs: config
    )
    monkeypatch.setattr(
        production,
        "initialize_production_runtime",
        lambda *args, **kwargs: {"settings_version": "fixture"},
    )
    monkeypatch.setattr(
        production,
        "run_production_daily",
        lambda *args, **kwargs: {"status": "completed", "committed_count": 15},
    )
    monkeypatch.setattr(
        production,
        "run_production_startup_preflight",
        lambda *args, **kwargs: {"status": "ready", "failed_checks": []},
    )

    def fail_log(*args, **kwargs):
        raise OSError("fixture unavailable log")

    monkeypatch.setattr("automation.inago.cli._append_production_log", fail_log)
    stdout, stderr = io.StringIO(), io.StringIO()
    result = main(
        [
            mode,
            "--environment",
            "production",
            "--config",
            str(tmp_path / "config"),
            "--data-root",
            str(tmp_path),
        ],
        stdout=stdout,
        stderr=stderr,
    )
    assert result == 0
    assert json.loads(stdout.getvalue())["status"] == expected
    assert json.loads(stderr.getvalue())["status"] == "log_unavailable"
    assert not (tmp_path / "state.sqlite").exists()


def test_production_readiness_rejects_blocked_state_under_python_optimization(
    tmp_path: Path,
) -> None:
    import sys

    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    state = config_path.parents[1] / "state/setup-status.json"
    state.parent.mkdir()
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "blocked_supplementation",
                "migration_selection_allowed": False,
                "settings_version": "confirmed-0.3-step46-all132",
            }
        ),
        encoding="utf-8",
    )
    script = tmp_path / "optimized-ready-check.py"
    script.write_text(
        """import sys
from pathlib import Path
import automation.inago.production_runtime as p
root=Path(sys.argv[1])
p.PROJECT_ROOT=root
p._windows_known_folder=lambda name: root if name=='profile' else root/'LocalAppData'
config=p.load_production_config(root/'rakuten-room-inago/runtime/config/settings.json',data_root=root/'rakuten-room-inago/runtime',user_profile=root,local_app_data=root/'LocalAppData',which=lambda command:str(command) if Path(command).is_file() else None)
p.require_production_ready(config)
""",
        encoding="utf-8",
    )
    environment = dict(
        os.environ,
        PYTHONOPTIMIZE="1",
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        PYTHONIOENCODING="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-B", "-O", str(script), str(tmp_path)],
        env=environment,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert b"preconditions do not permit" in result.stderr
    assert not (config_path.parents[1] / "state.sqlite").exists()


def test_content_verified_external_database_transfer_is_accepted(
    tmp_path: Path,
) -> None:
    import sqlite3
    from automation.inago.repository import open_database

    config_path = _write_config(
        tmp_path, owner_room_url="https://room.rakuten.co.jp/room_0123456789/items"
    )
    old_root = tmp_path / "InagoRuntime"
    source = open_database(SimpleNamespace(data_root=old_root))
    destination_path = config_path.parents[1] / "state.sqlite"
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        assert (
            source.execute("PRAGMA user_version").fetchone()[0]
            == destination.execute("PRAGMA user_version").fetchone()[0]
        )
    finally:
        source.close()
        destination.close()
    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["previous_runtime_transfer"] = {
        "status": "content_verified",
        "source_database": str(old_root / "state.sqlite"),
        "destination_database": str(destination_path),
        "history_and_pending_preserved": True,
    }
    config_path.write_text(json.dumps(document), encoding="utf-8")
    config = load_production_config(
        config_path,
        data_root=config_path.parents[1],
        user_profile=tmp_path,
        local_app_data=tmp_path / "LocalAppData",
        which=lambda command: str(command) if Path(command).is_file() else None,
    )
    human_config = load_human_api_config(
        config_path,
        data_root=config.runtime_root,
    )
    assert config.runtime_root / "state.sqlite" == destination_path
    assert human_config.runtime_root == config.runtime_root
    assert (old_root / "state.sqlite").is_file()
