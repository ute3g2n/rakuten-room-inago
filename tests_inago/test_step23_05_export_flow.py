"""Production daily and standalone re-export contracts for STEP23.05."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from automation.inago.clock import FakeClock, JST
from automation.inago.repository import initialize_database


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_VERSION = "fixture-export-flow"
NOW = "2026-10-15T13:00:00+09:00"


def _new_database(path: Path) -> None:
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        initialize_database(connection)
        connection.execute(
            """
            INSERT INTO settings_version
                (version, confirmed_at, genres_json, themes_json, pattern_refs_json,
                 change_reason, created_at)
            VALUES (?, NULL, '[]', '[]', '["P01","P02","P03","P04","P05"]',
                    NULL, ?)
            """,
            (SETTINGS_VERSION, NOW),
        )
    finally:
        connection.close()


def _open_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _insert_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    status: str = "running",
    committed_count: int = 0,
) -> None:
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, '2026-10-15', 'daily', ?, ?, ?, ?, ?, ?, 0, ?, NULL,
                'not_started', NULL)
        """,
        (
            run_id,
            status,
            SETTINGS_VERSION,
            f"seed-{run_id}",
            NOW,
            "2026-10-15T17:45:00+09:00",
            "2026-10-15T18:00:00+09:00",
            committed_count,
        ),
    )


def _insert_offer(connection: sqlite3.Connection, index: int) -> str:
    offer_key = f"shop_export:item_{index}"
    connection.execute(
        """
        INSERT INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES (?, 'shop_export', ?, ?, ?, '[]', 'G01-T01', '[]', ?, ?, '[]')
        """,
        (
            offer_key,
            f"item_{index}",
            f"fixture商品{index}",
            f"https://item.rakuten.co.jp/shop_export/item_{index}/",
            NOW,
            NOW,
        ),
    )
    return offer_key


def _insert_draft(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    offer_key: str,
    index: int,
) -> str:
    draft_id = f"D-EXPORT-{index}"
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES (?, ?, ?, 1, ?, ?, ?, 'fixture-copy-1', 'confirmed-0.3',
                'committed', NULL, 'P01', '[]')
        """,
        (draft_id, offer_key, run_id, f"fixture本文{index}🦗", NOW, NOW),
    )
    return draft_id


def _payload_from_html(path: Path) -> dict[str, Any]:
    html = path.read_text(encoding="utf-8")
    match = re.search(
        r'<script id="dashboard-history-data" type="application/json">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def _runtime_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from automation.inago import backup, production_runtime, restore_check

    runtime_root = tmp_path / "runtime"
    backup_root = tmp_path / "backups"
    runtime_root.mkdir()
    backup_root.mkdir()
    database_path = runtime_root / "state.sqlite"
    _new_database(database_path)
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
    monkeypatch.setattr(production_runtime, "_assert_production_roots", lambda _config: None)
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
    monkeypatch.setattr(backup, "list_daily_backups", lambda _root: set())
    monkeypatch.setattr(
        backup,
        "create_daily_backup",
        lambda *_args, **_kwargs: SimpleNamespace(
            directory=backup_root / "daily-20261015",
            operation_id="fixture-backup-operation",
            artifact_count=0,
        ),
    )
    monkeypatch.setattr(
        restore_check,
        "check_monthly_restore",
        lambda backup_directory, **_kwargs: SimpleNamespace(
            directory=backup_directory,
            already_checked=True,
            selection_allowed=False,
            requires_supplementation=False,
        ),
    )
    return config, database_path


def _fake_daily_outcome(status: str, count: int):
    from automation.inago.orchestrator import DailyRunOutcome

    return DailyRunOutcome(
        status=status,
        run_id="RUN-EXPORT-20261015",
        target_date="2026-10-15",
        committed_count=count,
        attempt_used=1,
        metrics_status="unavailable",
    )


def test_committed_draft_callback_refreshes_html_after_the_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import orchestrator
    from automation.inago.dashboard_history import write_dashboard_history_html

    database_path = tmp_path / "state.sqlite"
    _new_database(database_path)
    connection = _open_database(database_path)
    try:
        _insert_run(connection, run_id="RUN-EXPORT-HOOK")
        offer_key = _insert_offer(connection, 1)
        connection.execute(
            """
            INSERT INTO search_attempt
                (attempt_id, run_id, query, parent_refs_json, level, attempt_no,
                 started_at, ended_at, source_status, source_order, max_posts)
            VALUES ('A-EXPORT-HOOK', 'RUN-EXPORT-HOOK', 'fixture', '[]',
                    'combined', 1, ?, ?, 'observed', 'internal', 50)
            """,
            (NOW, NOW),
        )
        spec = SimpleNamespace(
            run_id="RUN-EXPORT-HOOK",
            offer_key=offer_key,
            target_date="2026-10-15",
            settings_version=SETTINGS_VERSION,
            deadline_at="2026-10-15T17:45:00+09:00",
        )
        ports = SimpleNamespace(
            offer_intake=None,
            copy_spec_factory=lambda *_args, **_kwargs: spec,
            copy_runner=object(),
        )
        state = {
            "processed_attempts": [],
            "handled_offer_keys": [],
            "theme_targets": {"G01-T01": 1},
            "offer_theme_ids": {},
            "error_count": 0,
            "source_failures": 0,
        }
        monkeypatch.setattr(orchestrator, "_source_posts_for_attempt", lambda *_: [])
        monkeypatch.setattr(
            orchestrator,
            "load_offer_history",
            lambda *_: {},
        )
        monkeypatch.setattr(
            orchestrator,
            "evaluate_sources",
            lambda *_args, **_kwargs: SimpleNamespace(
                eligible_representatives=[SimpleNamespace(offer_key=offer_key)]
            ),
        )
        monkeypatch.setattr(
            orchestrator, "_persist_candidate_decisions", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(orchestrator, "_save_state", lambda *_args, **_kwargs: None)

        def commit_stub(db, *_args, **_kwargs):
            _insert_draft(db, run_id="RUN-EXPORT-HOOK", offer_key=offer_key, index=1)
            db.execute(
                "UPDATE run SET committed_count=1 WHERE run_id='RUN-EXPORT-HOOK'"
            )
            db.commit()
            return SimpleNamespace(status="committed")

        monkeypatch.setattr(orchestrator, "generate_and_commit_copy", commit_stub)
        snapshots: list[str] = []
        output_path = tmp_path / "posting-history.html"

        def refresh_after_commit(db):
            write_dashboard_history_html(
                db,
                output_path=output_path,
                runtime_root=tmp_path,
            )
            snapshots.append(
                _payload_from_html(output_path)["days"][0]["items"][0]["draft_id"]
            )

        orchestrator._process_search_result(
            connection,
            run_id="RUN-EXPORT-HOOK",
            target_date="2026-10-15",
            settings_version=SETTINGS_VERSION,
            theme_id="G01-T01",
            seed="fixture-seed",
            attempt_id="A-EXPORT-HOOK",
            identities={},
            state=state,
            ports=ports,
            clock=FakeClock(datetime(2026, 10, 15, 13, 15, tzinfo=JST)),
            data_root=tmp_path,
            on_draft_commit=refresh_after_commit,
        )
        assert snapshots == ["D-EXPORT-1"]
    finally:
        connection.close()


def test_scheduled_trigger_forwards_commit_callback_to_the_daily_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import daily_runtime, trigger

    callback = lambda _connection: None
    connection = sqlite3.connect(":memory:")
    ports = object()
    downstream: list[dict[str, Any]] = []
    run_scheduled_daily = trigger.run_scheduled_daily
    monkeypatch.setattr(
        daily_runtime,
        "compose_public_daily_ports",
        lambda **_kwargs: ports,
    )

    def capture_scheduled(_connection, **kwargs):
        downstream.append(kwargs)
        return "scheduled-result"

    monkeypatch.setattr(trigger, "run_scheduled_daily", capture_scheduled)
    result = trigger.run_public_scheduled_daily(
        connection,
        data_root=tmp_path,
        source=object(),
        ai_root=object(),
        copy_spec_factory=lambda *_args, **_kwargs: None,
        metrics_port=object(),
        cancel_metrics_job=lambda _run_id: True,
        on_draft_commit=callback,
    )

    assert result == "scheduled-result"
    assert downstream[0]["ports"] is ports
    assert downstream[0]["on_draft_commit"] is callback

    daily_calls: list[dict[str, Any]] = []

    def capture_daily(_connection, **kwargs):
        daily_calls.append(kwargs)
        return "daily-result"

    monkeypatch.setattr(trigger, "run_daily", capture_daily)
    result = run_scheduled_daily(
        connection,
        data_root=tmp_path,
        ports=ports,
        on_draft_commit=callback,
    )
    assert result == "daily-result"
    assert daily_calls[0]["on_draft_commit"] is callback
    connection.close()


def test_production_daily_refreshes_after_each_commit_and_at_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import production_runtime, trigger

    config, database_path = _runtime_fixture(monkeypatch, tmp_path)
    refresh_counts: list[int] = []

    def fake_daily(connection, **kwargs):
        _insert_run(connection, run_id="RUN-EXPORT-20261015")
        for index in (1, 2):
            offer_key = _insert_offer(connection, index)
            _insert_draft(
                connection,
                run_id="RUN-EXPORT-20261015",
                offer_key=offer_key,
                index=index,
            )
            connection.execute(
                "UPDATE run SET committed_count=? WHERE run_id='RUN-EXPORT-20261015'",
                (index,),
            )
            connection.commit()
            kwargs["on_draft_commit"](connection)
            refresh_counts.append(
                _payload_from_html(config.runtime_root / "posting-history.html")[
                    "days"
                ][0]["item_count"]
            )
        connection.execute(
            "UPDATE run SET status='complete', finished_at=? "
            "WHERE run_id='RUN-EXPORT-20261015'",
            (NOW,),
        )
        connection.commit()
        return _fake_daily_outcome("complete", 2)

    monkeypatch.setattr(trigger, "run_public_scheduled_daily", fake_daily)
    result = production_runtime.run_production_daily(
        config,
        clock=FakeClock(datetime(2026, 10, 15, 13, 15, tzinfo=JST)),
    )

    assert refresh_counts == [1, 2]
    assert result["status"] == "complete"
    assert result["daily_status"] == "complete"
    assert result["export"]["status"] == "ready"
    assert result["export"]["draft_count"] == 2
    assert result["export"]["run_count"] == 1
    assert Path(result["export"]["path"]).name == "posting-history.html"
    assert len(_payload_from_html(config.runtime_root / "posting-history.html")["days"][0]["items"]) == 2
    assert database_path.is_file()


def test_production_daily_exports_saved_drafts_after_runtime_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import production_runtime, trigger

    config, _ = _runtime_fixture(monkeypatch, tmp_path)

    def failing_daily(connection, **kwargs):
        _insert_run(connection, run_id="RUN-EXPORT-20261015")
        offer_key = _insert_offer(connection, 1)
        _insert_draft(
            connection,
            run_id="RUN-EXPORT-20261015",
            offer_key=offer_key,
            index=1,
        )
        connection.execute(
            "UPDATE run SET committed_count=1 WHERE run_id='RUN-EXPORT-20261015'"
        )
        connection.commit()
        kwargs["on_draft_commit"](connection)
        raise RuntimeError("fixture interruption")

    monkeypatch.setattr(trigger, "run_public_scheduled_daily", failing_daily)
    result = production_runtime.run_production_daily(
        config,
        clock=FakeClock(datetime(2026, 10, 15, 13, 15, tzinfo=JST)),
    )

    assert result["status"] == "failed"
    assert result["daily_status"] == "failed"
    assert result["export"]["status"] == "ready"
    assert result["export"]["draft_count"] == 1
    items = _payload_from_html(config.runtime_root / "posting-history.html")["days"][0]["items"]
    assert items[0]["draft_id"] == "D-EXPORT-1"


def test_production_daily_exports_readable_history_after_readiness_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import production_runtime

    config, database_path = _runtime_fixture(monkeypatch, tmp_path)
    connection = _open_database(database_path)
    try:
        _insert_run(
            connection,
            run_id="RUN-EXPORT-READINESS",
            status="partial",
            committed_count=1,
        )
        offer_key = _insert_offer(connection, 1)
        _insert_draft(
            connection,
            run_id="RUN-EXPORT-READINESS",
            offer_key=offer_key,
            index=1,
        )
        connection.commit()
    finally:
        connection.close()

    def readiness_failure(_config):
        raise RuntimeError("fixture readiness failure")

    monkeypatch.setattr(
        production_runtime, "require_production_ready", readiness_failure
    )
    result = production_runtime.run_production_daily(
        config,
        clock=FakeClock(datetime(2026, 10, 15, 13, 15, tzinfo=JST)),
    )

    assert result["status"] == "failed"
    assert result["daily_status"] == "failed"
    assert result["export"]["status"] == "ready"
    assert result["export"]["draft_count"] == 1
    assert _payload_from_html(config.runtime_root / "posting-history.html")["days"][0]["items"][0]["draft_id"] == "D-EXPORT-1"


def test_export_failure_is_separate_from_a_completed_daily_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import dashboard_history, production_runtime, trigger

    config, _ = _runtime_fixture(monkeypatch, tmp_path)
    exporter_calls = 0

    def failed_export(*_args, **_kwargs):
        nonlocal exporter_calls
        exporter_calls += 1
        raise OSError("fixture output failure")

    monkeypatch.setattr(dashboard_history, "export_dashboard_history_html", failed_export)
    monkeypatch.setattr(
        trigger,
        "run_public_scheduled_daily",
        lambda *_args, **_kwargs: _fake_daily_outcome("complete", 15),
    )
    result = production_runtime.run_production_daily(
        config,
        clock=FakeClock(datetime(2026, 10, 15, 13, 15, tzinfo=JST)),
    )

    assert result["status"] == "export_failed"
    assert result["daily_status"] == "complete"
    assert result["export"]["status"] == "failed"
    assert result["export"]["error_code"] == "OSError"
    assert exporter_calls == 1


def test_production_export_writes_from_read_only_database_without_daily_adapters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime"
    backup_root = tmp_path / "backups"
    runtime_root.mkdir()
    backup_root.mkdir()
    database_path = runtime_root / "state.sqlite"
    _new_database(database_path)
    connection = _open_database(database_path)
    try:
        _insert_run(connection, run_id="RUN-EXPORT-ONLY", status="complete")
        offer_key = _insert_offer(connection, 1)
        _insert_draft(
            connection,
            run_id="RUN-EXPORT-ONLY",
            offer_key=offer_key,
            index=1,
        )
        connection.execute(
            "UPDATE run SET committed_count=1 WHERE run_id='RUN-EXPORT-ONLY'"
        )
        connection.commit()
    finally:
        connection.close()
    database_bytes_before = database_path.read_bytes()
    config = SimpleNamespace(
        application_root=PROJECT_ROOT,
        runtime_root=runtime_root,
        backup_root=backup_root,
        human_api_port=18741,
    )
    monkeypatch.setattr(production_runtime, "_assert_production_roots", lambda _config: None)
    monkeypatch.setattr(
        production_runtime,
        "build_public_daily_components",
        lambda *_args, **_kwargs: pytest.fail("daily adapters must not start for export"),
    )

    result = production_runtime.run_production_dashboard_export(config)

    assert result["status"] == "ready"
    assert result["draft_count"] == 1
    assert result["run_count"] == 1
    assert database_path.read_bytes() == database_bytes_before
    assert Path(result["path"]).is_file()
    assert _payload_from_html(Path(result["path"]))["days"][0]["items"][0]["draft_id"] == "D-EXPORT-1"


def test_cli_export_mode_dispatches_without_startup_preflight_or_daily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from io import StringIO

    from automation.inago import cli, production_runtime

    calls: list[str] = []
    monkeypatch.setattr(
        production_runtime,
        "load_human_api_config",
        lambda *_args, **_kwargs: SimpleNamespace(runtime_root=Path("fixture-root")),
    )
    monkeypatch.setattr(
        production_runtime,
        "run_production_dashboard_export",
        lambda _config: (calls.append("export") or {"status": "ready", "draft_count": 2}),
    )
    monkeypatch.setattr(
        production_runtime,
        "run_production_daily",
        lambda *_args, **_kwargs: pytest.fail("daily flow must not run for export"),
    )
    monkeypatch.setattr(cli, "_append_production_log", lambda *_args, **_kwargs: None)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = cli.main(
        [
            "export",
            "--config",
            "fixture-config.json",
            "--data-root",
            "fixture-root",
            "--environment",
            "production",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["draft_count"] == 2
    assert calls == ["export"]
    assert stderr.getvalue() == ""
