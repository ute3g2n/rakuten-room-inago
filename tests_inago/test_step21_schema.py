"""Q1.4 search contracts and lossless SQLite forward migration (STEP21.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from automation.inago import repository
from automation.inago.contracts import InjectedFailure, PersistenceContractError

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "automation/inago/sql"
NOW = "2026-10-13T13:00:00+09:00"


@pytest.fixture
def legacy_v2(tmp_path: Path):
    connection = sqlite3.connect(tmp_path / "v2.sqlite", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript((SQL / "001_initial.sql").read_text(encoding="utf-8"))
    connection.executescript(
        (SQL / "002_human_action_actual_posted_at.sql").read_text(encoding="utf-8")
    )
    connection.executemany(
        "INSERT INTO schema_migration VALUES (?, ?, ?)",
        [(1, NOW, "fixture v1"), (2, NOW, "fixture v2")],
    )
    connection.execute("PRAGMA user_version=2")
    connection.execute(
        "INSERT INTO settings_version VALUES ('S1',NULL,'[]','[]','[\"P01\"]',NULL,?)",
        (NOW,),
    )
    connection.execute(
        "INSERT INTO run VALUES ('R1','2026-10-13','daily','running','S1',"
        "'SAVED-SEED',?,'2026-10-13T17:45:00+09:00',"
        "'2026-10-13T18:00:00+09:00',9,1,'SAVED-CHECKPOINT','pending',NULL)",
        (NOW,),
    )
    for number in range(1, 10):
        connection.execute(
            "INSERT INTO search_attempt VALUES (?, 'R1', ?, '[\"K1\"]',"
            "'parent', ?, ?, ?, 'observed', 'internal', 50, NULL, NULL)",
            (f"A{number}", f"query-{number}", number, NOW, NOW),
        )
    connection.execute(
        "INSERT INTO keyword_snapshot VALUES ('K1','テーマ','google_trends_public_ui',"
        "'JP','90d','web','Top','[]',?,'observed','[]')",
        (NOW,),
    )
    connection.execute(
        "INSERT INTO offer VALUES ('shop:item','shop','item','架空商品',"
        "'https://item.rakuten.co.jp/shop/item/','[]','T1','[]',?,?,'[]')",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO draft VALUES ('D1','shop:item','R1',1,'保存済み本文',?,?,"
        "'generator1','P01','committed',NULL,'P01','[]')",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO reservation VALUES ('RES1','shop:item','D1','active',?,NULL,NULL)",
        (NOW,),
    )
    connection.execute(
        "INSERT INTO human_action (action_id,action,offer_key,draft_id,draft_version,"
        "prior_revision,receipt_revision,reported_at,actual_posted_at,apply_status)"
        " VALUES ('H1','posted','shop:item','D1',1,0,1,"
        "'2026-10-13T21:30:00+09:00','2026-10-13T21:20:00+09:00','pending')"
    )
    try:
        yield connection
    finally:
        connection.close()


def saved_rows(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    tables = ("run", "offer", "draft", "reservation", "human_action")
    return {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
        for table in tables
    }


def test_forward_migration_preserves_bodies_reports_reservations_and_budget(legacy_v2):
    before = saved_rows(legacy_v2)
    assert repository.initialize_database(legacy_v2) == 7
    expected = dict(before)
    expected["human_action"] = [(*row, None) for row in before["human_action"]]
    assert saved_rows(legacy_v2) == expected
    assert legacy_v2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert legacy_v2.execute("PRAGMA foreign_key_check").fetchall() == []
    assert legacy_v2.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert legacy_v2.execute(
        "SELECT parent_word,range FROM keyword_snapshot WHERE snapshot_id='K1'"
    ).fetchone()[:] == ("テーマ", "90d")
    assert repository.initialize_database(legacy_v2) == 7
    assert legacy_v2.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0] == 7
    with pytest.raises(sqlite3.IntegrityError):
        legacy_v2.execute("DELETE FROM run WHERE run_id='R1'")


def test_tenth_search_and_large_counters_persist_without_changing_fifty(legacy_v2):
    repository.initialize_database(legacy_v2)
    legacy_v2.execute("UPDATE run SET attempt_used=10 WHERE run_id='R1'")
    legacy_v2.execute(
        "INSERT INTO search_attempt (attempt_id,run_id,query,parent_refs_json,level,"
        "query_kind,keyword_snapshot_ref,selected_word_ref,attempt_no,started_at,"
        "source_status,source_order,max_posts) VALUES ('A10','R1','実取得原語','[]',"
        "'related','related','K1','W1',10,?,'invalid','internal',50)",
        (NOW,),
    )
    legacy_v2.execute("UPDATE run SET attempt_used=10001 WHERE run_id='R1'")
    with pytest.raises(sqlite3.IntegrityError):
        legacy_v2.execute("UPDATE run SET attempt_used=-1 WHERE run_id='R1'")
    with pytest.raises(sqlite3.IntegrityError):
        legacy_v2.execute(
            "UPDATE search_attempt SET max_posts=51 WHERE attempt_id='A10'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        legacy_v2.execute(
            "UPDATE search_attempt SET attempt_no=0 WHERE attempt_id='A10'"
        )


@pytest.mark.parametrize("kind", ["related", "theme_fallback"])
def test_search_kind_and_refs_validate_in_entity_contract(legacy_v2, kind):
    repository.initialize_database(legacy_v2)
    value = {
        "attempt_id": "A10",
        "run_id": "R1",
        "query": "原語",
        "parent_refs": ["T1"],
        "level": kind,
        "query_kind": kind,
        "keyword_snapshot_ref": "K1" if kind == "related" else None,
        "selected_word_ref": "W1" if kind == "related" else None,
        "attempt_no": 10,
        "started_at": NOW,
        "ended_at": None,
        "source_status": "invalid",
        "source_order": "internal",
        "max_posts": 50,
        "retry_of": None,
        "error_code": "in_progress",
    }
    assert repository.validate_entity("search_attempt", value)
    missing_kind = {key: item for key, item in value.items() if key != "query_kind"}
    with pytest.raises(repository.RejectedContract):
        repository.validate_entity("search_attempt", missing_kind)
    with pytest.raises(repository.RejectedContract):
        repository.validate_entity(
            "search_attempt", {**value, "query_kind": "invented"}
        )
    if kind == "related":
        with pytest.raises(repository.RejectedContract):
            repository.validate_entity(
                "search_attempt", {**value, "selected_word_ref": None}
            )


def test_three_month_observation_requires_ui_period_and_keeps_legacy_records(legacy_v2):
    repository.initialize_database(legacy_v2)
    legacy_v2.execute(
        "INSERT INTO keyword_snapshot (snapshot_id,parent_word,source,geo,range,"
        "search_type,list_type,words_json,fetched_at,availability,filter_decisions_json,"
        "ui_period_label) VALUES ('KNEW','テーマ','google_trends_public_ui','JP','3m',"
        "'web','Top','[]',?,'observed','[]','過去3か月間')",
        (NOW,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        legacy_v2.execute(
            "UPDATE keyword_snapshot SET ui_period_label=NULL WHERE snapshot_id='KNEW'"
        )
    assert legacy_v2.execute("SELECT COUNT(*) FROM keyword_snapshot").fetchone()[0] == 2


def test_migration_failure_rolls_back_tables_and_restores_foreign_keys(
    legacy_v2, monkeypatch
):
    before = saved_rows(legacy_v2)
    original = repository._execute_sql_file

    def fail_after_rebuild(connection, path):
        original(connection, path)
        if path.name == "003_search_rules_14.sql":
            raise InjectedFailure("after rebuild, before commit")

    monkeypatch.setattr(repository, "_execute_sql_file", fail_after_rebuild)
    with pytest.raises(InjectedFailure):
        repository.initialize_database(legacy_v2)
    assert saved_rows(legacy_v2) == before
    assert legacy_v2.execute("PRAGMA user_version").fetchone()[0] == 2
    assert legacy_v2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert legacy_v2.execute("PRAGMA foreign_key_check").fetchall() == []
    assert legacy_v2.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0] == 2
    assert not legacy_v2.in_transaction


def test_initialization_does_not_commit_callers_transaction(legacy_v2):
    legacy_v2.execute("BEGIN IMMEDIATE")
    with pytest.raises(PersistenceContractError):
        repository.initialize_database(legacy_v2)
    assert legacy_v2.in_transaction
    assert legacy_v2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    legacy_v2.rollback()


def test_empty_database_initializes_v3_idempotently(tmp_path):
    with sqlite3.connect(tmp_path / "empty.sqlite", isolation_level=None) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert repository.initialize_database(connection) == 7
        assert repository.initialize_database(connection) == 7
        assert repository.schema_version(connection) == 7
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_custom_index_is_preserved_and_requires_explicit_schema_review(legacy_v2):
    legacy_v2.execute("CREATE INDEX user_run_lookup ON run(started_at)")
    before = saved_rows(legacy_v2)
    with pytest.raises(repository.UnsupportedSchemaVersion, match="custom"):
        repository.initialize_database(legacy_v2)
    assert saved_rows(legacy_v2) == before
    assert legacy_v2.execute("PRAGMA user_version").fetchone()[0] == 2
    assert legacy_v2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert (
        legacy_v2.execute(
            "SELECT 1 FROM sqlite_master WHERE name='user_run_lookup'"
        ).fetchone()
        is not None
    )


def test_broken_reference_prevents_migration_commit_and_keeps_original_state(legacy_v2):
    legacy_v2.execute("PRAGMA foreign_keys=OFF")
    legacy_v2.execute("DELETE FROM offer WHERE offer_key='shop:item'")
    legacy_v2.execute("PRAGMA foreign_keys=ON")
    before = saved_rows(legacy_v2)
    with pytest.raises(PersistenceContractError, match="foreign keys"):
        repository.initialize_database(legacy_v2)
    assert saved_rows(legacy_v2) == before
    assert legacy_v2.execute("PRAGMA user_version").fetchone()[0] == 2
    assert legacy_v2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert not legacy_v2.in_transaction


def test_busy_migration_keeps_v2_state_and_restores_foreign_keys(legacy_v2):
    database_path = legacy_v2.execute("PRAGMA database_list").fetchone()[2]
    blocker = sqlite3.connect(database_path, isolation_level=None)
    before = saved_rows(legacy_v2)
    legacy_v2.execute("PRAGMA busy_timeout=0")
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            repository.initialize_database(legacy_v2)
        assert legacy_v2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not legacy_v2.in_transaction
    finally:
        blocker.rollback()
        blocker.close()
    assert saved_rows(legacy_v2) == before
    assert legacy_v2.execute("PRAGMA user_version").fetchone()[0] == 2
