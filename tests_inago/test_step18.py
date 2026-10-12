"""Read-only legacy-history import and new/old storage separation contracts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from automation.inago.migrate import MigrationInputError, migrate_legacy_history
from automation.inago.repository import initialize_database
from automation.inago.selection import load_offer_history


JST = "+09:00"
NOW = datetime.fromisoformat(f"2026-10-12T13:00:00{JST}")
SETTINGS_VERSION = "confirmed-0.3-step46-all132"


def _write_sources(
    source_root: Path, *, duplicate_evidence: bool = False
) -> dict[str, bytes]:
    source_root.mkdir(parents=True, exist_ok=True)
    products = [
        {
            "id": "P01",
            "name": "Anonymous item A",
            "url": "https://item.rakuten.co.jp/shop_test/item_A/",
            "normalized_url": "https://item.rakuten.co.jp/shop_test/item_A/",
            "genre": "家事ラク用品",
            "status": "posted",
            "item_code": "item_A",
            "model_candidate": None,
            "checked_at": "2026-10-01",
            "posted_at": "2026-10-09",
        },
        {
            "id": "P02",
            "name": "Anonymous item B",
            "url": "https://item.rakuten.co.jp/shop_test/item_B/?scid=fixture",
            "normalized_url": "https://item.rakuten.co.jp/shop_test/item_B/",
            "genre": "家事ラク用品",
            "status": "posted",
            "item_code": "item_B",
            "model_candidate": None,
            "checked_at": "2026-10-01",
            "posted_at": "2026-10-10",
        },
        {
            "id": "P03",
            "name": "Anonymous planned item",
            "url": "https://item.rakuten.co.jp/shop_test/item_C/",
            "normalized_url": "https://item.rakuten.co.jp/shop_test/item_C/",
            "genre": "家事ラク用品",
            "status": "planned",
            "item_code": "item_C",
            "model_candidate": None,
            "checked_at": None,
            "posted_at": None,
        },
        {
            "id": "P04",
            "name": "Anonymous draft item",
            "url": "https://item.rakuten.co.jp/shop_test/item_D/",
            "normalized_url": "https://item.rakuten.co.jp/shop_test/item_D/",
            "genre": "家事ラク用品",
            "status": "eligible",
            "item_code": "item_D",
            "model_candidate": None,
            "checked_at": None,
            "posted_at": None,
        },
    ]
    ledger = json.dumps(
        {"version": 1, "products": products, "next_id": 5},
        ensure_ascii=False,
    )
    posts = [
        {
            "date": "2026-10-09",
            "product_id": "P01",
            "status": "posted",
            "source": "legacy-user-confirmation",
            "posted_at": f"2026-10-09T20:05:00{JST}",
            "reaction": "fixture-only legacy reaction",
            "rating": "A",
        },
        {
            "date": "2026-10-10",
            "product_id": "P03",
            "status": "planned",
            "source": "legacy-plan",
            "posted_at": None,
            "reaction": None,
            "rating": "A",
        },
        {
            "date": "2026-10-11",
            "product_id": "P05",
            "status": "unknown",
            "source": "legacy-unknown",
            "posted_at": None,
            "reaction": None,
            "rating": None,
        },
    ]
    if duplicate_evidence:
        posts.append(
            {
                "date": "2026-10-11",
                "product_id": "P01",
                "status": "posted",
                "source": "legacy-user-confirmation",
                "posted_at": f"2026-10-11T20:05:00{JST}",
                "reaction": None,
                "rating": "C",
            }
        )
    status = {
        "version": 1,
        "items": {
            "P02": {
                "product_id": "P02",
                "scheduled_date": None,
                "posted": True,
                "ever_marked_posted": True,
                "posted_at": f"2026-10-10T21:10:00{JST}",
                "updated_at": f"2026-10-10T21:15:00{JST}",
                "correction_history": [],
            },
            "P03": {
                "product_id": "P03",
                "scheduled_date": "2026-10-10",
                "posted": False,
                "ever_marked_posted": False,
                "posted_at": None,
                "updated_at": f"2026-10-10T21:20:00{JST}",
                "correction_history": [],
            },
        },
    }
    drafts = {
        "version": 1,
        "drafts": [
            {
                "product_id": "P04",
                "url": "https://item.rakuten.co.jp/shop_test/item_D/",
                "body": "PRIVATE FIXTURE BODY MUST NOT BE COPIED INTO REPORTS",
                "status": "accepted",
                "checked_at": None,
            }
        ],
    }
    files = {
        "product-ledger.json": ledger.encode("utf-8"),
        "posts-log.jsonl": "".join(json.dumps(row) + "\n" for row in posts).encode(
            "utf-8"
        ),
        "post-status.json": json.dumps(status, ensure_ascii=False).encode("utf-8"),
        "drafts.json": json.dumps(drafts, ensure_ascii=False).encode("utf-8"),
        "old-task-state.txt": b"fixture:disabled; read only\n",
        "legacy-auth-placeholder.txt": b"fixture placeholder; no secret\n",
    }
    for name, contents in files.items():
        (source_root / name).write_bytes(contents)
    return files


def _insert_offer(connection: sqlite3.Connection, item_id: str) -> None:
    offer_key = f"shop_test:{item_id}"
    connection.execute(
        """
        INSERT INTO offer(
            offer_key, shop_id, item_id, name, url, image_refs_json,
            main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json
        ) VALUES (?, 'shop_test', ?, ?, ?, '[]', 'theme-01', '[]', ?, ?, '[]')
        """,
        (
            offer_key,
            item_id,
            f"Anonymous {item_id}",
            f"https://item.rakuten.co.jp/shop_test/{item_id}/",
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )


def _create_database(data_root: Path) -> sqlite3.Connection:
    data_root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(data_root / "state.sqlite")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    initialize_database(connection)
    connection.execute(
        """
        INSERT INTO settings_version(
            version, confirmed_at, genres_json, themes_json,
            pattern_refs_json, change_reason, created_at
        ) VALUES (?, ?, '[]', '[]', '[]', NULL, ?)
        """,
        (SETTINGS_VERSION, NOW.isoformat(), NOW.isoformat()),
    )
    connection.execute(
        """
        INSERT INTO run(
            run_id, target_date, mode, status, settings_version, seed,
            started_at, draft_deadline, end_deadline, attempt_used,
            committed_count, checkpoint, metrics_status, finished_at
        ) VALUES ('RUN-MIGRATION-FIXTURE', '2026-10-12', 'daily', 'running', ?,
                  'fixed-seed', ?, ?, ?, 0, 0, 'candidate_selection', 'not_started', NULL)
        """,
        (
            SETTINGS_VERSION,
            NOW.isoformat(),
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    for item_id in ("item_A", "item_B", "item_C", "item_D"):
        _insert_offer(connection, item_id)
    connection.execute(
        """
        INSERT INTO draft(
            draft_id, offer_key, run_id, version, body, generated_at,
            committed_at, generator_version, pattern_version, status,
            artifact_ref, pattern_id, evidence_refs_json
        ) VALUES ('D-NEW-D', 'shop_test:item_D', 'RUN-MIGRATION-FIXTURE', 1,
                  'new-side fixture draft', ?, ?, 'fixture', ?, 'committed',
                  NULL, 'P01', '[]')
        """,
        (NOW.isoformat(), NOW.isoformat(), SETTINGS_VERSION),
    )
    connection.execute(
        """
        INSERT INTO reservation(
            reservation_id, offer_key, draft_id, status, created_at,
            released_at, release_reason
        ) VALUES ('RES-NEW-D', 'shop_test:item_D', 'D-NEW-D', 'active', ?, NULL, NULL)
        """,
        (NOW.isoformat(),),
    )
    connection.commit()
    return connection


def _snapshot(connection: sqlite3.Connection) -> dict[str, object]:
    return {
        "run": tuple(connection.execute("SELECT * FROM run").fetchone()),
        "offers": connection.execute("SELECT COUNT(*) FROM offer").fetchone()[0],
        "drafts": [tuple(row) for row in connection.execute("SELECT * FROM draft")],
        "reservations": [
            tuple(row) for row in connection.execute("SELECT * FROM reservation")
        ],
        "settings": [
            tuple(row) for row in connection.execute("SELECT * FROM settings_version")
        ],
        "human_actions": connection.execute(
            "SELECT COUNT(*) FROM human_action"
        ).fetchone()[0],
        "ai_jobs": connection.execute("SELECT COUNT(*) FROM ai_job").fetchone()[0],
    }


def _migrate(
    connection: sqlite3.Connection,
    source_root: Path,
    data_root: Path,
    *,
    mode: str,
):
    return migrate_legacy_history(
        connection,
        source_root=source_root,
        data_root=data_root,
        posts_log_path="posts-log.jsonl",
        product_ledger_path="product-ledger.json",
        post_status_path="post-status.json",
        drafts_path="drafts.json",
        now=NOW,
        mode=mode,
        fixture_mode=True,
    )


def test_dry_run_classifies_legacy_records_without_writing_any_state(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "legacy-source"
    data_root = tmp_path / "new-data"
    source_bytes = _write_sources(source_root)
    connection = _create_database(data_root)
    try:
        before = _snapshot(connection)
        result = _migrate(connection, source_root, data_root, mode="dry_run")

        assert result.mode == "dry_run"
        assert result.source_record_count == 6
        assert result.evidence_count == 1
        assert result.statement_count == 1
        assert result.planned_count >= 1
        assert result.draft_count == 1
        assert result.unknown_count >= 1
        assert result.publication_count_before == result.publication_count_after == 0
        assert result.exclusion_count_before == result.exclusion_count_after == 0
        assert result.selection_allowed is False
        assert len(result.supplementation_required) >= 1
        unknown_missing_key = next(
            item
            for item in result.supplementation_required
            if item["legacy_product_ref"] == "P05"
        )
        assert unknown_missing_key["reason"] == "legacy_product_url_unreadable"
        assert _snapshot(connection) == before
        assert {p.name: p.read_bytes() for p in source_root.iterdir()} == source_bytes
        assert (
            not any((data_root / "migration").glob("*"))
            if (data_root / "migration").exists()
            else True
        )
    finally:
        connection.close()


def test_apply_and_reapply_import_only_evidence_and_preserve_old_and_new_sources(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "legacy-source"
    data_root = tmp_path / "new-data"
    source_bytes = _write_sources(source_root)
    connection = _create_database(data_root)
    try:
        before = _snapshot(connection)
        first = _migrate(connection, source_root, data_root, mode="apply")
        assert first.created_publications == 2
        assert first.created_exclusions == 2
        assert first.publication_count_after == 2
        assert first.selection_allowed is False
        assert len(first.supplementation_required) == 1
        assert [
            row[0]
            for row in connection.execute(
                "SELECT offer_key FROM exclusion_key ORDER BY offer_key"
            ).fetchall()
        ] == ["shop_test:item_A", "shop_test:item_B"]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM publication_event WHERE valid=1"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM publication_event WHERE draft_id IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT status FROM draft WHERE draft_id='D-NEW-D'"
            ).fetchone()[0]
            == "committed"
        )
        public_report = json.dumps(first.to_document(), ensure_ascii=False)
        assert (
            "PRIVATE FIXTURE BODY MUST NOT BE COPIED INTO REPORTS" not in public_report
        )
        assert "fixture-only legacy reaction" not in public_report
        assert '"rating"' not in public_report
        assert first.to_document()["old_file_write_attempts"] == 0
        assert first.to_document()["old_task_api_calls"] == 0
        assert _snapshot(connection)["run"] == before["run"]
        assert _snapshot(connection)["offers"] == before["offers"]
        assert _snapshot(connection)["drafts"] == before["drafts"]
        assert _snapshot(connection)["reservations"] == before["reservations"]
        assert _snapshot(connection)["settings"] == before["settings"]
        assert _snapshot(connection)["human_actions"] == before["human_actions"]
        assert _snapshot(connection)["ai_jobs"] == before["ai_jobs"]
        history = load_offer_history(connection, "RUN-MIGRATION-FIXTURE")
        assert history.permanently_posted == frozenset(
            {"shop_test:item_A", "shop_test:item_B"}
        )

        second = _migrate(connection, source_root, data_root, mode="apply")
        assert second.created_publications == 0
        assert second.created_exclusions == 0
        assert second.already_imported_count == 2
        assert second.publication_count_after == 2
        assert (
            connection.execute("SELECT COUNT(*) FROM exclusion_key").fetchone()[0] == 2
        )
        assert (
            connection.execute(
                "SELECT status FROM draft WHERE draft_id='D-NEW-D'"
            ).fetchone()[0]
            == "committed"
        )
        assert {p.name: p.read_bytes() for p in source_root.iterdir()} == source_bytes
    finally:
        connection.close()


def test_existing_event_content_conflict_is_rejected_without_partial_writes(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "legacy-source"
    data_root = tmp_path / "new-data"
    _write_sources(source_root)
    connection = _create_database(data_root)
    connection.execute(
        """
        CREATE TRIGGER fail_second_legacy_event
        BEFORE INSERT ON publication_event
        WHEN NEW.offer_key = 'shop_test:item_B'
        BEGIN
            SELECT RAISE(ABORT, 'fixture injected migration failure');
        END
        """
    )
    before = _snapshot(connection)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="fixture injected"):
            _migrate(connection, source_root, data_root, mode="apply")

        assert (
            connection.execute(
                "SELECT COUNT(*) FROM publication_event WHERE valid=1"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM exclusion_key").fetchone()[0] == 0
        )
        assert _snapshot(connection) == before
        assert not connection.in_transaction
    finally:
        connection.close()


def test_two_distinct_evidence_refs_for_one_key_create_two_events_and_one_exclusion(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "legacy-source"
    data_root = tmp_path / "new-data"
    _write_sources(source_root, duplicate_evidence=True)
    connection = _create_database(data_root)
    try:
        result = _migrate(connection, source_root, data_root, mode="apply")
        assert result.created_publications == 3
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM publication_event WHERE offer_key='shop_test:item_A' AND valid=1"
            ).fetchone()[0]
            == 2
        )
        refs = json.loads(
            connection.execute(
                "SELECT evidence_refs_json FROM exclusion_key WHERE offer_key='shop_test:item_A'"
            ).fetchone()[0]
        )
        assert len(refs) == 2
        assert len(set(refs)) == 2
    finally:
        connection.close()


def test_old_database_path_is_never_accepted_as_migration_destination(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "legacy-source"
    data_root = tmp_path / "new-data"
    _write_sources(source_root)
    data_root.mkdir(parents=True)
    legacy_db_path = source_root / "state.sqlite"
    legacy_db_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_connection = sqlite3.connect(legacy_db_path)
    legacy_connection.execute("CREATE TABLE old_state(value TEXT)")
    legacy_connection.execute("INSERT INTO old_state VALUES ('preserve')")
    legacy_connection.commit()
    before = legacy_db_path.read_bytes()
    try:
        with pytest.raises(
            MigrationInputError, match="destination.*data_root|database"
        ):
            migrate_legacy_history(
                legacy_connection,
                source_root=source_root,
                data_root=data_root,
                posts_log_path="posts-log.jsonl",
                product_ledger_path="product-ledger.json",
                post_status_path="post-status.json",
                drafts_path="drafts.json",
                now=NOW,
                mode="apply",
                fixture_mode=True,
            )
        assert legacy_db_path.read_bytes() == before
        assert legacy_connection.execute("SELECT value FROM old_state").fetchone() == (
            "preserve",
        )
    finally:
        legacy_connection.close()


def test_posted_quality_conflict_does_not_turn_a_planned_log_into_a_post(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "legacy-source"
    data_root = tmp_path / "new-data"
    _write_sources(source_root)
    ledger = json.loads((source_root / "product-ledger.json").read_text("utf-8"))
    next(product for product in ledger["products"] if product["id"] == "P03")[
        "status"
    ] = "posted"
    (source_root / "product-ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False), encoding="utf-8"
    )
    before_migration = {p.name: p.read_bytes() for p in source_root.iterdir()}
    connection = _create_database(data_root)
    try:
        result = _migrate(connection, source_root, data_root, mode="apply")
        assert result.created_publications == 2
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM publication_event WHERE offer_key='shop_test:item_C'"
            ).fetchone()[0]
            == 0
        )
        assert any(
            item["reason"] == "legacy_status_conflict"
            for item in result.supplementation_required
        )
        assert {
            p.name: p.read_bytes() for p in source_root.iterdir()
        } == before_migration
    finally:
        connection.close()


def test_scheduled_and_marked_post_are_not_migrated_as_human_confirmation(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "legacy-source"
    data_root = tmp_path / "new-data"
    _write_sources(source_root)
    status_path = source_root / "post-status.json"
    status = json.loads(status_path.read_text("utf-8"))
    status["items"]["P02"]["scheduled_date"] = "2026-10-10"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    connection = _create_database(data_root)
    try:
        result = _migrate(connection, source_root, data_root, mode="apply")
        assert result.created_publications == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM publication_event WHERE offer_key='shop_test:item_B'"
            ).fetchone()[0]
            == 0
        )
        assert any(
            item["reason"] == "human_status_inconsistent"
            for item in result.supplementation_required
        )
    finally:
        connection.close()


def test_post_status_map_key_mismatch_fails_closed_before_new_writes(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "legacy-source"
    data_root = tmp_path / "new-data"
    source_bytes = _write_sources(source_root)
    status_path = source_root / "post-status.json"
    status = json.loads(status_path.read_text("utf-8"))
    status["items"]["P02"]["product_id"] = "P03"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    source_bytes["post-status.json"] = status_path.read_bytes()
    connection = _create_database(data_root)
    before = _snapshot(connection)
    try:
        with pytest.raises(MigrationInputError, match="identifier"):
            _migrate(connection, source_root, data_root, mode="apply")
        assert _snapshot(connection) == before
        assert {
            path.name: path.read_bytes() for path in source_root.iterdir()
        } == source_bytes
    finally:
        connection.close()
