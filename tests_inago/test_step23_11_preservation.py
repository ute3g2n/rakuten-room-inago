"""Daily final backup and monthly restore integration for STEP23.11."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from automation.inago.clock import FakeClock, JST
from automation.inago.repository import initialize_database


NOW = "2026-10-15T13:15:00+09:00"
TARGET_DATE = "2026-10-15"
SETTINGS_VERSION = "step23-11-fixture-settings"
RUN_ID = "RUN-STEP23-11-20261015"


def _create_database(database_path: Path) -> None:
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        initialize_database(connection)
        connection.execute(
            """
            INSERT INTO settings_version
                (version, confirmed_at, genres_json, themes_json,
                 pattern_refs_json, change_reason, created_at)
            VALUES (?, NULL, '[]', '[]', '[]', NULL, ?)
            """,
            (SETTINGS_VERSION, NOW),
        )
    finally:
        connection.close()


def _open_database(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _production_daily_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime"
    backup_root = tmp_path / "backups"
    runtime_root.mkdir()
    backup_root.mkdir()
    database_path = runtime_root / "state.sqlite"
    _create_database(database_path)
    config = SimpleNamespace(
        start_date="2026-10-01",
        runtime_root=runtime_root,
        backup_root=backup_root,
        adopted_settings={"version": SETTINGS_VERSION},
    )
    monkeypatch.setattr(
        production_runtime,
        "require_production_ready",
        lambda _config: {"settings_version": SETTINGS_VERSION},
    )
    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    monkeypatch.setattr(
        production_runtime,
        "_open_existing_production_database",
        lambda _config: _open_database(database_path),
    )
    monkeypatch.setattr(
        production_runtime,
        "register_production_settings",
        lambda *_args, **_kwargs: SimpleNamespace(
            active_basic_version=SETTINGS_VERSION
        ),
    )
    monkeypatch.setattr(
        production_runtime,
        "build_public_daily_components",
        lambda *_args, **_kwargs: {
            "source": object(),
            "ai_root": object(),
            "copy_spec_factory": object(),
            "metrics_port": object(),
            "cancel_metrics_job": object(),
        },
    )
    return config, database_path


def _insert_completed_daily_state(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, ?, 'daily', 'complete', ?, 'fixture-seed', ?, ?, ?, 1, 1,
                '{}', 'observed', ?)
        """,
        (
            RUN_ID,
            TARGET_DATE,
            SETTINGS_VERSION,
            NOW,
            "2026-10-15T17:45:00+09:00",
            "2026-10-15T18:00:00+09:00",
            NOW,
        ),
    )
    offer_key = "fixture-shop:item-01"
    draft_id = "D-STEP23-11-01"
    connection.execute(
        """
        INSERT INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES (?, 'fixture-shop', 'item-01', 'fixture item',
                'https://item.rakuten.co.jp/fixture-shop/item-01/',
                '[]', 'G01-T01', '[]', ?, ?, '[]')
        """,
        (offer_key, NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES (?, ?, ?, 1, 'fixture committed body', ?, ?,
                'fixture-copy-1', 'confirmed-0.3', 'committed', NULL,
                'P01', '[]')
        """,
        (draft_id, offer_key, RUN_ID, NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO reservation(
            reservation_id, offer_key, draft_id, status, created_at,
            released_at, release_reason
        ) VALUES ('RES-STEP23-11-01', ?, ?, 'active', ?, NULL, NULL)
        """,
        (offer_key, draft_id, NOW),
    )
    connection.execute(
        """
        INSERT INTO human_action(
            action_id, action, offer_key, draft_id, draft_version,
            prior_revision, receipt_revision, reason, reported_at,
            old_event_ref, new_body, apply_status, applied_at,
            applied_run_id, apply_error, actual_posted_at
        ) VALUES ('ACT-STEP23-11-PENDING', 'posted', ?, ?, 1, 0, 1,
                  NULL, ?, NULL, NULL, 'pending', NULL, NULL, NULL, ?)
        """,
        (offer_key, draft_id, NOW, NOW),
    )
    connection.commit()


def test_final_backup_uses_unique_stage_and_retains_same_day_generation(
    tmp_path: Path,
) -> None:
    from automation.inago.backup import create_daily_backup, list_daily_backups

    runtime_root = tmp_path / "runtime"
    backup_root = tmp_path / "backups"
    runtime_root.mkdir()
    database_path = runtime_root / "state.sqlite"
    _create_database(database_path)
    connection = _open_database(database_path)
    try:
        start = create_daily_backup(
            connection,
            data_root=runtime_root,
            backup_root=backup_root,
            now=NOW,
            fixture_mode=True,
        )
        first_final = create_daily_backup(
            connection,
            data_root=runtime_root,
            backup_root=backup_root,
            now=NOW,
            fixture_mode=True,
            generation_stage="final",
        )
        second_final = create_daily_backup(
            connection,
            data_root=runtime_root,
            backup_root=backup_root,
            now=NOW,
            fixture_mode=True,
            generation_stage="final",
        )

        assert start.directory.name == "daily-20261015"
        assert first_final.directory != second_final.directory
        assert first_final.directory.is_dir()
        assert second_final.directory.is_dir()
        assert len(list_daily_backups(backup_root)) == 1
        assert len(list_daily_backups(backup_root, generation_stage="final")) == 2
    finally:
        connection.close()


def test_final_backup_retains_only_seven_generations_without_pruning_start_backup(
    tmp_path: Path,
) -> None:
    from automation.inago.backup import create_daily_backup, list_daily_backups

    runtime_root = tmp_path / "runtime"
    backup_root = tmp_path / "backups"
    runtime_root.mkdir()
    database_path = runtime_root / "state.sqlite"
    _create_database(database_path)
    connection = _open_database(database_path)
    try:
        start = create_daily_backup(
            connection,
            data_root=runtime_root,
            backup_root=backup_root,
            now=NOW,
            fixture_mode=True,
        )
        final_generations = [
            create_daily_backup(
                connection,
                data_root=runtime_root,
                backup_root=backup_root,
                now=f"2026-10-15T13:{minute:02d}:00+09:00",
                fixture_mode=True,
                generation_stage="final",
            )
            for minute in range(8)
        ]

        retained = list_daily_backups(backup_root, generation_stage="final")
        assert len(retained) == 7
        assert final_generations[0].directory not in retained
        assert all(item.directory in retained for item in final_generations[1:])
        assert list_daily_backups(backup_root) == (start.directory,)
    finally:
        connection.close()


def test_production_daily_creates_final_backup_and_monthly_restore_reuses_month(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import production_runtime, trigger
    from automation.inago.backup import list_daily_backups
    from automation.inago.orchestrator import DailyRunOutcome

    config, database_path = _production_daily_fixture(monkeypatch, tmp_path)
    calls: list[str] = []

    def fake_daily(connection, **kwargs):
        calls.append(RUN_ID)
        if len(calls) == 1:
            _insert_completed_daily_state(connection)
            kwargs["on_draft_commit"](connection)
        return DailyRunOutcome(
            "complete", RUN_ID, TARGET_DATE, 1, 1, "observed", resumed=len(calls) > 1
        )

    monkeypatch.setattr(trigger, "run_public_scheduled_daily", fake_daily)
    monkeypatch.setattr(
        production_runtime,
        "run_production_recovery",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "decision": {"action": "already_finished"},
        },
    )
    clock = FakeClock(datetime.fromisoformat(NOW).astimezone(JST))

    first = production_runtime.run_production_daily(config, clock=clock)
    second = production_runtime.run_production_daily(config, clock=clock)

    assert first["status"] == second["status"] == "complete"
    assert first["daily_status"] == second["daily_status"] == "complete"
    assert first["preservation"]["status"] == "ready"
    assert second["preservation"]["status"] == "ready"
    assert first["preservation"]["monthly_restore"]["already_checked"] is False
    assert second["preservation"]["monthly_restore"]["already_checked"] is True
    assert (
        first["preservation"]["end_backup"]["path"]
        != second["preservation"]["end_backup"]["path"]
    )
    assert len(list_daily_backups(config.backup_root, generation_stage="start")) == 1
    assert len(list_daily_backups(config.backup_root, generation_stage="final")) == 2
    assert (config.backup_root / "restore-check" / "2026-10" / "report.json").is_file()

    start_backup = config.backup_root / "daily-20261015"
    start_connection = sqlite3.connect(start_backup / "state.sqlite")
    try:
        assert start_connection.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 0
    finally:
        start_connection.close()

    end_backup_path = Path(first["preservation"]["end_backup"]["path"])
    end_connection = sqlite3.connect(end_backup_path / "state.sqlite")
    try:
        assert (
            end_connection.execute(
                "SELECT body FROM draft WHERE draft_id=?", ("D-STEP23-11-01",)
            ).fetchone()[0]
            == "fixture committed body"
        )
        assert (
            end_connection.execute(
                "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'"
            ).fetchone()[0]
            == 1
        )
        assert (
            end_connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0]
            == 1
        )
    finally:
        end_connection.close()

    monthly_database = (
        config.backup_root
        / "restore-check"
        / "2026-10"
        / "restored-data"
        / "state.sqlite"
    )
    restored = sqlite3.connect(monthly_database)
    try:
        assert (
            restored.execute(
                "SELECT body FROM draft WHERE draft_id=?", ("D-STEP23-11-01",)
            ).fetchone()[0]
            == "fixture committed body"
        )
        assert (
            restored.execute(
                "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'"
            ).fetchone()[0]
            == 1
        )
        assert (
            restored.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0]
            == 1
        )
    finally:
        restored.close()


def test_end_backup_failure_keeps_daily_commit_and_reports_preservation_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import backup, production_runtime, trigger
    from automation.inago.orchestrator import DailyRunOutcome

    config, database_path = _production_daily_fixture(monkeypatch, tmp_path)
    original_create = backup.create_daily_backup

    def fail_final_backup(*args, **kwargs):
        if kwargs.get("generation_stage") == "final":
            raise OSError("fixture end backup failed")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(backup, "create_daily_backup", fail_final_backup)

    def fake_daily(connection, **kwargs):
        _insert_completed_daily_state(connection)
        kwargs["on_draft_commit"](connection)
        return DailyRunOutcome("complete", RUN_ID, TARGET_DATE, 1, 1, "observed")

    monkeypatch.setattr(trigger, "run_public_scheduled_daily", fake_daily)
    result = production_runtime.run_production_daily(
        config,
        clock=FakeClock(datetime.fromisoformat(NOW).astimezone(JST)),
    )

    assert result["daily_status"] == "complete"
    assert result["status"] == "preservation_failed"
    assert result["preservation"]["status"] == "failed"
    assert result["preservation"]["end_backup"]["error_code"] == "OSError"
    connection = sqlite3.connect(database_path)
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM draft WHERE draft_id='D-STEP23-11-01'"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_monthly_restore_check_runs_once_again_in_the_next_month(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import production_runtime
    from automation.inago.backup import create_daily_backup

    config, database_path = _production_daily_fixture(monkeypatch, tmp_path)
    connection = _open_database(database_path)
    try:
        october_backup = create_daily_backup(
            connection,
            data_root=config.runtime_root,
            backup_root=config.backup_root,
            now="2026-10-15T18:00:00+09:00",
            fixture_mode=False,
            generation_stage="final",
        )
        october_first = production_runtime.run_production_monthly_restore_check(
            config,
            now=datetime.fromisoformat("2026-10-15T18:10:00+09:00"),
        )
        november_backup = create_daily_backup(
            connection,
            data_root=config.runtime_root,
            backup_root=config.backup_root,
            now="2026-11-01T18:00:00+09:00",
            fixture_mode=False,
            generation_stage="final",
        )
        november_first = production_runtime.run_production_monthly_restore_check(
            config,
            now=datetime.fromisoformat("2026-11-01T18:10:00+09:00"),
        )
        november_repeat = production_runtime.run_production_monthly_restore_check(
            config,
            now=datetime.fromisoformat("2026-11-02T18:10:00+09:00"),
        )
    finally:
        connection.close()

    assert october_first["status"] == "ready"
    assert october_first["already_checked"] is False
    assert october_backup.directory.name.startswith("daily-final-20261015-")
    assert november_first["status"] == "ready"
    assert november_first["already_checked"] is False
    assert november_backup.directory.name.startswith("daily-final-20261101-")
    assert november_repeat["status"] == "already_checked"
    assert november_repeat["already_checked"] is True
    assert (config.backup_root / "restore-check" / "2026-10" / "report.json").is_file()
    assert (config.backup_root / "restore-check" / "2026-11" / "report.json").is_file()


def test_production_cli_backup_mode_dispatches_without_starting_daily(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import cli, production_runtime

    config = SimpleNamespace(runtime_root=tmp_path)
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(
        production_runtime, "load_production_config", lambda *_args, **_kwargs: config
    )
    monkeypatch.setattr(
        production_runtime,
        "run_production_backup",
        lambda _config: {
            "mode": "backup",
            "status": "ready",
            "backup": {
                "status": "ready",
                "path": str(
                    tmp_path / "daily-final-20261015-0123456789abcdef0123456789abcdef"
                ),
                "error_code": None,
            },
        },
    )
    monkeypatch.setattr(
        production_runtime,
        "run_production_daily",
        lambda *_args, **_kwargs: pytest.fail("backup mode must not start daily"),
    )
    monkeypatch.setattr(
        cli,
        "_append_production_log",
        lambda _root, **event: logged.append(event),
    )
    output = io.StringIO()
    errors = io.StringIO()

    code = cli.main(
        [
            "backup",
            "--environment",
            "production",
            "--config",
            str(tmp_path / "settings.json"),
            "--data-root",
            str(tmp_path / "runtime"),
        ],
        stdout=output,
        stderr=errors,
    )

    assert code == 0
    assert json.loads(output.getvalue())["mode"] == "backup"
    assert json.loads(output.getvalue())["status"] == "ready"
    assert logged[0]["mode"] == "backup"
    assert logged[0]["diagnostics"]["file_name"].startswith("daily-final-")


def test_production_cli_restore_check_mode_dispatches_monthly_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import cli, production_runtime

    config = SimpleNamespace(runtime_root=tmp_path)
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(
        production_runtime, "load_production_config", lambda *_args, **_kwargs: config
    )
    monkeypatch.setattr(
        production_runtime,
        "run_production_monthly_restore_check",
        lambda _config: {
            "mode": "restore-check",
            "status": "already_checked",
            "already_checked": True,
            "reason_code": None,
            "path": str(tmp_path / "restore-check" / "2026-10"),
        },
    )
    monkeypatch.setattr(
        production_runtime,
        "run_production_daily",
        lambda *_args, **_kwargs: pytest.fail(
            "restore-check mode must not start daily"
        ),
    )
    monkeypatch.setattr(
        cli,
        "_append_production_log",
        lambda _root, **event: logged.append(event),
    )
    output = io.StringIO()
    errors = io.StringIO()

    code = cli.main(
        [
            "restore-check",
            "--environment",
            "production",
            "--config",
            str(tmp_path / "settings.json"),
            "--data-root",
            str(tmp_path / "runtime"),
        ],
        stdout=output,
        stderr=errors,
    )

    assert code == 0
    assert json.loads(output.getvalue())["status"] == "already_checked"
    assert logged[0]["mode"] == "restore-check"
    assert logged[0]["status"] == "ready"
    assert logged[0]["diagnostics"]["already_checked"] is True


def test_production_backup_mode_surfaces_monthly_restore_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import cli, production_runtime

    config = SimpleNamespace(runtime_root=tmp_path)
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(
        production_runtime, "load_production_config", lambda *_args, **_kwargs: config
    )
    monkeypatch.setattr(
        production_runtime,
        "run_production_end_of_day_preservation",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "end_backup": {
                "status": "ready",
                "path": str(
                    tmp_path / "daily-final-20261015-0123456789abcdef0123456789abcdef"
                ),
                "error_code": None,
            },
            "monthly_restore": {
                "status": "failed",
                "path": None,
                "error_code": "RestoreCheckError",
            },
        },
    )
    monkeypatch.setattr(
        production_runtime,
        "run_production_daily",
        lambda *_args, **_kwargs: pytest.fail("backup mode must not start daily"),
    )
    monkeypatch.setattr(
        cli,
        "_append_production_log",
        lambda _root, **event: logged.append(event),
    )
    output = io.StringIO()
    errors = io.StringIO()

    code = cli.main(
        [
            "backup",
            "--environment",
            "production",
            "--config",
            str(tmp_path / "settings.json"),
            "--data-root",
            str(tmp_path / "runtime"),
        ],
        stdout=output,
        stderr=errors,
    )

    result = json.loads(output.getvalue())
    assert code != 0
    assert result["status"] == "failed"
    assert result["backup"]["status"] == "ready"
    assert result["monthly_restore"]["status"] == "failed"
    assert logged[0]["status"] == "failed"
    assert logged[0]["reason"] == "RestoreCheckError"
    assert logged[0]["diagnostics"]["monthly_restore_status"] == "failed"


def test_monthly_restore_check_reuses_result_if_peer_finishes_before_lock_acquisition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago.backup import create_daily_backup
    from automation.inago.restore_check import check_monthly_restore

    runtime_root = tmp_path / "runtime"
    backup_root = tmp_path / "backups"
    runtime_root.mkdir()
    database_path = runtime_root / "state.sqlite"
    _create_database(database_path)
    connection = _open_database(database_path)
    try:
        backup = create_daily_backup(
            connection,
            data_root=runtime_root,
            backup_root=backup_root,
            now=NOW,
            fixture_mode=True,
            generation_stage="final",
        )
    finally:
        connection.close()

    original_mkdir = Path.mkdir
    peer_finished = False
    peer_results = []

    def finish_peer_before_lock(self: Path, *args: Any, **kwargs: Any) -> None:
        nonlocal peer_finished
        if self.name == ".2026-10.lock" and not peer_finished:
            peer_finished = True
            peer_results.append(
                check_monthly_restore(
                    backup.directory,
                    backup_root=backup_root,
                    now=NOW,
                    fixture_mode=True,
                    reuse_existing=True,
                )
            )
        original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", finish_peer_before_lock)
    result = check_monthly_restore(
        backup.directory,
        backup_root=backup_root,
        now=NOW,
        fixture_mode=True,
        reuse_existing=True,
    )

    assert peer_finished is True
    assert peer_results[0].already_checked is False
    assert result.already_checked is True
    assert result.directory == peer_results[0].directory
