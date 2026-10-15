"""Isolated tests for process-identity startup preflight."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.inago import production_runtime


def _write_environment(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path, Path]:
    root.mkdir()
    runtime = root / "rakuten-room-inago" / "runtime"
    config_dir = runtime / "config"
    config_dir.mkdir(parents=True)
    profile_root = root / "WindowsUser"
    local_app_data = profile_root / "AppData" / "Local"
    local_app_data.mkdir(parents=True)
    dedicated_profile = local_app_data / "InagoPlaywrightProfile"
    dedicated_profile.mkdir()
    tools = root / "tools"
    tools.mkdir()
    node = tools / "node.exe"
    node.write_text("fixture node", encoding="utf-8")
    codex = tools / "codex.exe"
    codex.write_text("fixture codex", encoding="utf-8")
    chrome = local_app_data / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("fixture chrome", encoding="utf-8")
    auth_reference = root / "private-auth" / "auth-state.json"
    auth_reference.parent.mkdir()
    auth_reference.write_text("AUTH_SENTINEL_MUST_NOT_BE_READ", encoding="utf-8")
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
        "application_root": str(root),
        "backup_root": str(root / "rakuten-room-inago" / "backups"),
        "owner_room_url": "https://room.rakuten.co.jp/room_0123456789/items",
        "start_date": "2026-10-15",
        "adopted_settings": {
            "version": production_runtime.BASE_SETTINGS_VERSION,
            "adopted": True,
            "status": "confirmed",
        },
        "detailed_rules": {
            "version": production_runtime.DETAIL_CANDIDATE_VERSION,
            "adopted": False,
            "status": "reviewed_candidate_not_new_owner_confirmation",
        },
        "browser": {
            "chrome_executable": str(chrome),
            "user_data_dir": str(dedicated_profile),
            "node_executable": str(node),
        },
        "codex": {"command": [str(codex)]},
        "metrics": {"affiliate_auth_state": str(auth_reference)},
        "legacy": {
            "source_root": str(legacy),
            "posts_log_path": "posts-log.jsonl",
            "product_ledger_path": "products.json",
            "post_status_path": "post-status.json",
            "drafts_path": "drafts.json",
        },
        "human_api": {"bind_host": "127.0.0.1", "port": 8765},
    }
    config_path = config_dir / "settings.json"
    config_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(production_runtime, "PROJECT_ROOT", root)
    monkeypatch.setattr(
        production_runtime,
        "_windows_known_folder",
        lambda name: profile_root if name == "profile" else local_app_data,
    )
    return config_path, runtime, local_app_data, dedicated_profile


def _write_ready_setup_state(runtime: Path) -> None:
    setup_path = runtime / "state" / "setup-status.json"
    setup_path.parent.mkdir(parents=True, exist_ok=True)
    setup_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ready",
                "migration_selection_allowed": True,
                "settings_version": production_runtime.BASE_SETTINGS_VERSION,
            }
        ),
        encoding="utf-8",
    )


def _preflight(
    config_path: Path,
    runtime: Path,
    local_app_data: Path,
) -> dict[str, object]:
    check = getattr(production_runtime, "run_production_startup_preflight", None)
    assert callable(check), "production startup preflight is not implemented"
    return check(
        config_path,
        data_root=runtime,
        local_app_data=local_app_data,
        user_profile=local_app_data.parent.parent,
        which=lambda command: str(command) if Path(command).is_file() else None,
    )


def test_preflight_reports_missing_profile_without_database_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, runtime, local_app_data, profile = _write_environment(
        tmp_path / "repo", monkeypatch
    )
    profile.rmdir()

    report = _preflight(config_path, runtime, local_app_data)

    assert report["status"] == "blocked"
    assert "browser_profile_missing" in report["failed_checks"]
    assert report["side_effects"]["database_created"] is False
    assert not (runtime / "state.sqlite").exists()
    assert "AUTH_SENTINEL_MUST_NOT_BE_READ" not in repr(report)
    assert "room_0123456789" not in repr(report)


def test_preflight_does_not_create_a_missing_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, runtime, local_app_data, _ = _write_environment(
        tmp_path / "repo", monkeypatch
    )
    _write_ready_setup_state(runtime)

    report = _preflight(config_path, runtime, local_app_data)

    assert report["status"] == "blocked"
    assert "database_missing" in report["failed_checks"]
    assert not (runtime / "state.sqlite").exists()


def test_preflight_preserves_a_corrupt_database_byte_for_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, runtime, local_app_data, _ = _write_environment(
        tmp_path / "repo", monkeypatch
    )
    _write_ready_setup_state(runtime)
    database_path = runtime / "state.sqlite"
    original_bytes = b"not-a-sqlite-database\x00fixture"
    database_path.write_bytes(original_bytes)

    report = _preflight(config_path, runtime, local_app_data)

    assert report["status"] == "blocked"
    assert "database_schema_invalid" in report["failed_checks"]
    assert database_path.read_bytes() == original_bytes


def test_preflight_accepts_a_ready_database_without_writing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automation.inago.repository import open_database

    config_path, runtime, local_app_data, _ = _write_environment(
        tmp_path / "repo", monkeypatch
    )
    _write_ready_setup_state(runtime)
    database_path = runtime / "state.sqlite"
    connection = open_database(SimpleNamespace(data_root=runtime))
    connection.execute(
        """
        INSERT INTO settings_version (
            version, confirmed_at, genres_json, themes_json,
            pattern_refs_json, change_reason, created_at
        ) VALUES (?, ?, '[]', '[]', '[]', NULL, ?)
        """,
        (
            production_runtime.BASE_SETTINGS_VERSION,
            "2026-10-15T00:00:00+09:00",
            "2026-10-15T00:00:00+09:00",
        ),
    )
    connection.commit()
    connection.close()
    database_before = database_path.read_bytes()

    report = _preflight(config_path, runtime, local_app_data)

    assert report["status"] == "ready"
    assert report["checks"]["database_schema"] == "ready"
    assert report["checks"]["adopted_settings"] == "ready"
    assert report["side_effects"]["database_written"] is False
    assert database_path.read_bytes() == database_before
    assert "AUTH_SENTINEL_MUST_NOT_BE_READ" not in repr(report)
    assert "room_0123456789" not in repr(report)


def test_preflight_mode_is_registered_and_does_not_dispatch_daily() -> None:
    from automation.inago.cli import SUPPORTED_MODES

    script = (
        Path(__file__).resolve().parents[1]
        / "automation"
        / "inago"
        / "run.ps1"
    ).read_text(encoding="utf-8")
    registration = (
        Path(__file__).resolve().parents[1]
        / "automation"
        / "inago"
        / "register_tasks.ps1"
    ).read_text(encoding="utf-8")

    assert "preflight" in SUPPORTED_MODES
    assert "preflight" in script
    assert "run_production_startup_preflight" in registration


def test_preflight_cli_mode_only_runs_the_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automation.inago import cli

    monkeypatch.setattr(
        production_runtime,
        "run_production_startup_preflight",
        lambda *args, **kwargs: {
            "status": "ready",
            "checks": {"database": "ready"},
            "failed_checks": [],
            "side_effects": {"daily_started": False},
        },
        raising=False,
    )
    monkeypatch.setattr(
        production_runtime,
        "run_production_daily",
        lambda *args, **kwargs: pytest.fail("preflight dispatched daily"),
        raising=False,
    )
    output = io.StringIO()
    error = io.StringIO()

    result = cli.main(
        [
            "preflight",
            "--environment",
            "production",
            "--config",
            str(tmp_path / "unused-settings.json"),
            "--data-root",
            str(tmp_path / "unused-runtime"),
        ],
        stdout=output,
        stderr=error,
    )

    assert result == 0
    assert json.loads(output.getvalue())["status"] == "ready"
    assert not error.getvalue()
