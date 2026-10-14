"""Retention, daily backup, and isolated restore-check contracts."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from automation.inago.backup import BackupError, create_daily_backup, list_daily_backups
from automation.inago.repository import initialize_database
from automation.inago.restore_check import RestoreCheckError, check_monthly_restore
from automation.inago.retention import (
    ArtifactExpiredError,
    RetentionError,
    apply_retention,
    retention_expiry,
)


JST = "+09:00"
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
NOW = datetime.fromisoformat(f"2026-10-12T13:00:00{JST}")


def _connect_database(data_root: Path) -> sqlite3.Connection:
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
        ) VALUES (
            'RUN-2026-10-12', '2026-10-12', 'daily', 'complete', ?, 'seed-17',
            ?, ?, ?, 9, 15, 'complete', 'not_started', ?
        )
        """,
        (
            SETTINGS_VERSION,
            NOW.isoformat(),
            (NOW + timedelta(minutes=285)).isoformat(),
            (NOW + timedelta(hours=5)).isoformat(),
            NOW.isoformat(),
        ),
    )
    return connection


def _insert_offer_and_draft(
    connection: sqlite3.Connection,
    *,
    index: int,
    status: str,
    artifact_ref: str | None = None,
) -> tuple[str, str]:
    offer_key = f"fixture-shop:item-{index:02d}"
    draft_id = f"D-{index:02d}"
    connection.execute(
        """
        INSERT INTO offer(
            offer_key, shop_id, item_id, name, url, image_refs_json,
            main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json
        ) VALUES (?, 'fixture-shop', ?, ?, ?, '[]', 'theme-01', '[]', ?, ?, '[]')
        """,
        (
            offer_key,
            f"item-{index:02d}",
            f"Fixture product {index}",
            f"https://example.invalid/item-{index:02d}",
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.execute(
        """
        INSERT INTO draft(
            draft_id, offer_key, run_id, version, body, generated_at,
            committed_at, generator_version, pattern_version, status,
            artifact_ref, pattern_id, evidence_refs_json
        ) VALUES (?, ?, 'RUN-2026-10-12', 1, ?, ?, ?, 'fixture-generator',
                  'confirmed-0.3-step46-all132', ?, ?, 'P01', '[]')
        """,
        (
            draft_id,
            offer_key,
            f"fixture body {index}",
            NOW.isoformat(),
            NOW.isoformat(),
            status,
            artifact_ref,
        ),
    )
    return offer_key, draft_id


def _insert_artifact(
    connection: sqlite3.Connection,
    data_root: Path,
    *,
    artifact_ref: str,
    expires_at: datetime | None,
    created_at: datetime | None = None,
    body: bytes | None = None,
    source_kind: str = "daily",
) -> Path:
    artifact_dir = data_root / "artifacts" / "fixture"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{artifact_ref}.json"
    artifact_path.write_bytes(body or json.dumps({"fixture": artifact_ref}).encode())
    connection.execute(
        """
        INSERT INTO artifact(
            artifact_ref, path, hash, created_at, source_kind,
            retention_expires_at, status
        ) VALUES (?, ?, 'fixture-runtime-record', ?, ?, ?, 'available')
        """,
        (
            artifact_ref,
            str(artifact_path.resolve()),
            (created_at or NOW).isoformat(),
            source_kind,
            expires_at.isoformat() if expires_at else None,
        ),
    )
    return artifact_path


def _seed_retention_database(data_root: Path) -> sqlite3.Connection:
    connection = _connect_database(data_root)
    for index in range(1, 18):
        state = "posted" if index <= 10 else "committed"
        offer_key, draft_id = _insert_offer_and_draft(
            connection, index=index, status=state
        )
        if index <= 10:
            event_id = f"PUB-{index:02d}"
            connection.execute(
                """
                INSERT INTO publication_event(
                    event_id, offer_key, draft_id, draft_version, reported_at,
                    actual_posted_at, room_url, evidence_kind, valid, revision
                ) VALUES (?, ?, ?, 1, ?, ?, NULL, 'human_check', 1, 0)
                """,
                (
                    event_id,
                    offer_key,
                    draft_id,
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO exclusion_key(
                    offer_key, reason, evidence_refs_json, effective, updated_at
                ) VALUES (?, 'actual_posted', ?, 1, ?)
                """,
                (offer_key, json.dumps([event_id]), NOW.isoformat()),
            )
        else:
            connection.execute(
                """
                INSERT INTO reservation(
                    reservation_id, offer_key, draft_id, status, created_at,
                    released_at, release_reason
                ) VALUES (?, ?, ?, 'active', ?, NULL, NULL)
                """,
                (f"RES-{index:02d}", offer_key, draft_id, NOW.isoformat()),
            )

    for index in range(11, 14):
        connection.execute(
            """
            INSERT INTO human_action(
                action_id, action, offer_key, draft_id, draft_version,
                prior_revision, receipt_revision, reason, reported_at,
                old_event_ref, new_body, apply_status, applied_at,
                applied_run_id, apply_error, actual_posted_at
            ) VALUES (?, 'posted', ?, ?, 1, 0, 1, NULL, ?, NULL, NULL,
                      'pending', NULL, NULL, NULL, ?)
            """,
            (
                f"Q-{index - 10:02d}",
                f"fixture-shop:item-{index:02d}",
                f"D-{index:02d}",
                (NOW + timedelta(hours=8)).isoformat(),
                (NOW + timedelta(hours=7)).isoformat(),
            ),
        )
    connection.execute(
        """
        INSERT INTO human_action(
            action_id, action, offer_key, draft_id, draft_version,
            prior_revision, receipt_revision, reason, reported_at,
            old_event_ref, new_body, apply_status, applied_at,
            applied_run_id, apply_error, actual_posted_at
        ) VALUES ('CORR-01', 'correction', 'fixture-shop:item-01', 'D-01', 1,
                  0, 2, 'fixture correction', ?, 'PUB-01', 'corrected fixture body',
                  'applied', ?, 'RUN-2026-10-12', NULL, ?)
        """,
        (
            NOW.isoformat(),
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )

    source_expiry = NOW - timedelta(days=90)
    connection.execute(
        """
        INSERT INTO search_attempt(
            attempt_id, run_id, query, parent_refs_json, level, attempt_no,
            started_at, ended_at, source_status, source_order, max_posts,
            retry_of, error_code
        ) VALUES ('SEARCH-RETENTION', 'RUN-2026-10-12', 'fixture-query', '[]',
                  'combined', 1, ?, ?, 'observed', 'internal', 50, NULL, NULL)
        """,
        (source_expiry.isoformat(), source_expiry.isoformat()),
    )
    connection.execute(
        """
        INSERT INTO source_post(
            source_key, stable_room_id, room_url, attempt_id, order_index,
            likes, comments, item_url, image_ref, actor_ref, published_at,
            evidence_ref, observed_at, availability
        ) VALUES ('SOURCE-RETENTION', 'stable-fixture-id', 'https://example.invalid/room',
                  'SEARCH-RETENTION', 1, 300, 5, 'https://example.invalid/item',
                  'fixture-image', 'fixture-actor', ?, 'raw-before', ?, 'observed')
        """,
        ((source_expiry - timedelta(days=1)).isoformat(), source_expiry.isoformat()),
    )
    connection.execute(
        """
        INSERT INTO keyword_snapshot(
            snapshot_id, parent_word, source, geo, range, search_type,
            list_type, words_json, fetched_at, availability, filter_decisions_json
        ) VALUES ('KEYWORD-RETENTION', 'fixture-parent', 'fixture-source', 'JP',
                  '90d', 'web', 'Top', '["fixture-term"]', ?, 'observed', '[]')
        """,
        (source_expiry.isoformat(),),
    )

    raw_expiries = {
        "raw-before": datetime.fromisoformat(f"2026-10-12T13:00:01{JST}"),
        "raw-equal": datetime.fromisoformat(f"2026-10-12T13:00:00{JST}"),
        "raw-after": datetime.fromisoformat(f"2026-10-12T12:59:59{JST}"),
    }
    raw_created = {
        "raw-before": datetime.fromisoformat(f"2026-07-14T13:00:01{JST}"),
        "raw-equal": datetime.fromisoformat(f"2026-07-14T13:00:00{JST}"),
        "raw-after": datetime.fromisoformat(f"2026-07-14T12:59:59{JST}"),
    }
    for artifact_ref, expires_at in raw_expiries.items():
        _insert_artifact(
            connection,
            data_root,
            artifact_ref=artifact_ref,
            expires_at=expires_at,
            created_at=raw_created[artifact_ref],
        )
    connection.execute(
        """
        INSERT INTO revenue_order(
            order_ref, is_temporary_ref, source_ref, match_kind
        ) VALUES ('ORDER-EQUAL', 0, 'raw-equal', 'unmatched')
        """
    )
    connection.execute(
        """
        INSERT INTO revenue_order(
            order_ref, is_temporary_ref, source_ref, match_kind
        ) VALUES ('ORDER-BEFORE', 0, 'raw-before', 'unmatched')
        """
    )

    aggregate_times = [
        datetime.fromisoformat(f"2025-10-12T13:00:01{JST}"),
        datetime.fromisoformat(f"2025-10-12T13:00:00{JST}"),
        datetime.fromisoformat(f"2025-10-12T12:59:59{JST}"),
    ]
    for index, observed_at in enumerate(aggregate_times, start=1):
        connection.execute(
            """
            INSERT INTO metric_observation(
                observation_id, scope, ref, metric, value_json, unit,
                observed_at, window, availability, source_ref, provisional
            ) VALUES (?, 'day_aggregate', ?, 'sales_yen', '100', 'JPY',
                      ?, 'day', 'observed', ?, 0)
            """,
            (
                f"AGG-{index:02d}",
                f"day-{index:02d}",
                observed_at.isoformat(),
                "raw-equal" if index == 1 else None,
            ),
        )
    connection.commit()
    return connection


def _scalar(connection: sqlite3.Connection, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


def test_retention_expiry_uses_fixed_days_and_calendar_year_boundaries() -> None:
    assert retention_expiry(
        "raw", datetime.fromisoformat(f"2026-07-14T13:00:00{JST}")
    ) == datetime.fromisoformat(f"2026-10-12T13:00:00{JST}")
    assert retention_expiry(
        "aggregate", datetime.fromisoformat(f"2024-02-29T13:00:00{JST}")
    ) == datetime.fromisoformat(f"2025-02-28T13:00:00{JST}")
    assert retention_expiry(
        "aggregate", datetime.fromisoformat(f"2023-03-01T13:00:00{JST}")
    ) == datetime.fromisoformat(f"2024-03-01T13:00:00{JST}")


def test_retention_expires_at_equal_and_after_but_preserves_required_state(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    connection = _seed_retention_database(data_root)
    connection.row_factory = None
    try:
        result = apply_retention(connection, data_root=data_root, now=NOW)

        assert result.expired_artifacts == 2
        assert result.expired_aggregates == 2
        assert result.expired_source_posts == 1
        assert result.expired_keyword_snapshots == 1
        assert result.deleted_revenue_orders == 1
        assert (
            _scalar(
                connection, "SELECT COUNT(*) FROM artifact WHERE status='available'"
            )
            == 1
        )
        assert (
            _scalar(connection, "SELECT COUNT(*) FROM artifact WHERE status='expired'")
            == 2
        )
        assert (
            _scalar(
                connection,
                "SELECT COUNT(*) FROM metric_observation WHERE availability='observed'",
            )
            == 1
        )
        assert (
            _scalar(
                connection,
                "SELECT COUNT(*) FROM metric_observation WHERE availability='expired'",
            )
            == 2
        )
        assert _scalar(connection, "SELECT COUNT(*) FROM revenue_order") == 1
        assert tuple(
            connection.execute(
                "SELECT availability, value_json FROM metric_observation WHERE observation_id='AGG-01'"
            ).fetchone()
        ) == ("observed", "100")
        assert (
            connection.execute(
                "SELECT status FROM artifact WHERE artifact_ref='raw-before'"
            ).fetchone()[0]
            == "available"
        )
        assert (
            connection.execute(
                "SELECT status FROM artifact WHERE artifact_ref='raw-equal'"
            ).fetchone()[0]
            == "expired"
        )
        assert not (data_root / "artifacts" / "fixture" / "raw-equal.json").exists()
        assert result.open_artifact(connection, "raw-before", now=NOW).is_file()
        expired_source = connection.execute(
            """
            SELECT availability, stable_room_id, room_url, item_url, image_ref,
                   actor_ref, published_at, likes, comments
            FROM source_post WHERE source_key='SOURCE-RETENTION'
            """
        ).fetchone()
        assert tuple(expired_source) == (
            "expired",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        expired_keywords = connection.execute(
            "SELECT availability, words_json FROM keyword_snapshot WHERE snapshot_id='KEYWORD-RETENTION'"
        ).fetchone()
        assert tuple(expired_keywords) == ("expired", "[]")
        assert (
            _scalar(connection, "SELECT COUNT(*) FROM exclusion_key WHERE effective=1")
            == 10
        )
        assert (
            _scalar(
                connection, "SELECT COUNT(*) FROM reservation WHERE status='active'"
            )
            == 7
        )
        assert (
            _scalar(
                connection,
                "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'",
            )
            == 3
        )
        assert (
            _scalar(
                connection,
                "SELECT COUNT(*) FROM human_action WHERE action='correction'",
            )
            == 1
        )
        assert (
            _scalar(
                connection,
                "SELECT committed_count FROM run WHERE run_id='RUN-2026-10-12'",
            )
            == 15
        )
        with pytest.raises(ArtifactExpiredError):
            result.open_artifact(connection, "raw-equal", now=NOW)

        later = apply_retention(
            connection, data_root=data_root, now=NOW + timedelta(seconds=1)
        )
        assert later.expired_artifacts == 1
        assert later.expired_aggregates == 1
        assert later.deleted_revenue_orders == 1
        assert (
            _scalar(
                connection, "SELECT COUNT(*) FROM artifact WHERE status='available'"
            )
            == 0
        )
        assert (
            _scalar(
                connection,
                "SELECT COUNT(*) FROM metric_observation WHERE availability='observed'",
            )
            == 0
        )
        assert _scalar(connection, "SELECT COUNT(*) FROM revenue_order") == 0
        assert (
            _scalar(connection, "SELECT COUNT(*) FROM exclusion_key WHERE effective=1")
            == 10
        )
        assert (
            _scalar(
                connection, "SELECT COUNT(*) FROM reservation WHERE status='active'"
            )
            == 7
        )
        assert (
            _scalar(
                connection,
                "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'",
            )
            == 3
        )
        assert (
            _scalar(
                connection,
                "SELECT COUNT(*) FROM human_action WHERE action='correction'",
            )
            == 1
        )
    finally:
        connection.close()


def test_retention_keeps_required_generation_artifact_but_expires_raw_reference(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    connection = _seed_retention_database(data_root)
    try:
        artifact_path = _insert_artifact(
            connection,
            data_root,
            artifact_ref="draft-required",
            expires_at=NOW - timedelta(seconds=1),
            source_kind="generation",
        )
        raw_path = _insert_artifact(
            connection,
            data_root,
            artifact_ref="raw-draft-ref",
            expires_at=NOW - timedelta(seconds=1),
            source_kind="daily",
        )
        connection.execute(
            "UPDATE draft SET artifact_ref='draft-required' WHERE draft_id='D-01'"
        )
        connection.execute(
            "UPDATE draft SET artifact_ref='raw-draft-ref' WHERE draft_id='D-02'"
        )
        connection.commit()

        result = apply_retention(connection, data_root=data_root, now=NOW)

        assert result.expired_artifacts == 3
        assert (
            connection.execute(
                "SELECT status FROM artifact WHERE artifact_ref='draft-required'"
            ).fetchone()[0]
            == "available"
        )
        assert artifact_path.is_file()
        assert (
            connection.execute(
                "SELECT status FROM artifact WHERE artifact_ref='raw-draft-ref'"
            ).fetchone()[0]
            == "expired"
        )
        assert not raw_path.exists()
    finally:
        connection.close()


def test_retention_raw_detail_boundaries_cover_89_90_and_91_days(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    connection = _seed_retention_database(data_root)
    try:
        for age_days in (89, 90, 91):
            connection.execute(
                """
                INSERT INTO metric_observation(
                    observation_id, scope, ref, metric, value_json, unit,
                    observed_at, window, availability, source_ref, provisional
                ) VALUES (?, 'room_post', ?, 'likes', '1', 'count',
                          ?, 'fixture', 'observed', NULL, 0)
                """,
                (
                    f"DETAIL-{age_days}",
                    f"post-{age_days}",
                    (NOW - timedelta(days=age_days)).isoformat(),
                ),
            )
        connection.commit()

        result = apply_retention(connection, data_root=data_root, now=NOW)

        assert result.expired_detail_observations == 2
        states = {
            str(row[0]): (row[1], row[2])
            for row in connection.execute(
                """
                SELECT observation_id, availability, value_json
                FROM metric_observation WHERE observation_id LIKE 'DETAIL-%'
                """
            )
        }
        assert states["DETAIL-89"] == ("observed", "1")
        assert states["DETAIL-90"] == ("expired", None)
        assert states["DETAIL-91"] == ("expired", None)
    finally:
        connection.close()


def test_retention_scrubs_expired_trends_terms_and_classification_details(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    connection = _seed_retention_database(data_root)
    try:
        for age_days in (89, 90, 91):
            observed_at = NOW - timedelta(days=age_days)
            connection.execute(
                """
                INSERT INTO keyword_snapshot(
                    snapshot_id,parent_word,source,geo,range,search_type,list_type,
                    words_json,fetched_at,availability,filter_decisions_json,
                    ui_period_label
                ) VALUES (?,?, 'google_trends_public_ui','JP','3m','web','Top',
                          ?,?,'observed',?,'過去3か月間')
                """,
                (
                    f"TREND-RETENTION-{age_days}",
                    f"theme-{age_days}",
                    json.dumps(
                        [{"word_id": f"W-{age_days}", "raw": "raw term"}],
                        ensure_ascii=False,
                    ),
                    observed_at.isoformat(),
                    json.dumps(
                        [{"kind": "word", "raw": "raw term", "reason": "detail"}],
                        ensure_ascii=False,
                    ),
                ),
            )
        connection.commit()

        result = apply_retention(connection, data_root=data_root, now=NOW)

        # The shared retention fixture also seeds one expired keyword snapshot.
        assert result.expired_keyword_snapshots == 3
        states = {
            row[0]: (row[1], json.loads(row[2]), json.loads(row[3]))
            for row in connection.execute(
                "SELECT snapshot_id,availability,words_json,filter_decisions_json "
                "FROM keyword_snapshot WHERE snapshot_id LIKE 'TREND-RETENTION-%'"
            )
        }
        assert states["TREND-RETENTION-89"] == (
            "observed",
            [{"word_id": "W-89", "raw": "raw term"}],
            [{"kind": "word", "raw": "raw term", "reason": "detail"}],
        )
        assert states["TREND-RETENTION-90"] == ("expired", [], [])
        assert states["TREND-RETENTION-91"] == ("expired", [], [])
    finally:
        connection.close()


def test_retention_deletes_expired_public_source_observations(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    connection = _connect_database(data_root)
    observation_root = data_root / "observations"
    observation_root.mkdir()
    paths = {}
    for age_days in (89, 90, 91):
        observed_at = NOW - timedelta(days=age_days)
        expiry = retention_expiry("raw", observed_at).isoformat()
        path = observation_root / f"trends-request-{age_days}-observation-01.json"
        path.write_text(
            json.dumps(
                {
                    "kind": "trends",
                    "recorded_at": observed_at.isoformat(),
                    "retention_expires_at": expiry,
                    "response": {"words": ["fixture raw word"]},
                }
            ),
            encoding="utf-8",
        )
        paths[age_days] = path
    legacy_path = observation_root / "observation-legacy-room.json"
    legacy_path.write_text(
        json.dumps(
            {
                "kind": "room",
                "recorded_at": (NOW - timedelta(days=91)).isoformat(),
                "product_facts": [],
            }
        ),
        encoding="utf-8",
    )
    control_path = observation_root / "access-stop-RUN-2026-10-12.json"
    control_path.write_text('{"run_id":"RUN-2026-10-12"}', encoding="utf-8")
    connection.commit()

    try:
        result = apply_retention(connection, data_root=data_root, now=NOW)

        assert result.expired_source_observation_files == 3
        assert result.source_observation_deletion_failures == 0
        assert paths[89].is_file()
        assert not paths[90].exists() and not paths[91].exists()
        assert not legacy_path.exists()
        assert control_path.is_file()
    finally:
        connection.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
def test_retention_refuses_expired_observation_directory_junction(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    observation_root = data_root / "observations"
    data_root.mkdir(parents=True)
    external_observations = tmp_path / "external-observations"
    external_observations.mkdir()
    external_expired = external_observations / "room-old-observation-1.json"
    external_expired.write_text(
        json.dumps(
            {
                "kind": "room",
                "retention_expires_at": "2026-10-12T12:59:59+09:00",
            }
        ),
        encoding="utf-8",
    )
    junction = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(observation_root),
            str(external_observations),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if junction.returncode != 0:
        pytest.skip(f"could not create a fixture junction: {junction.stderr}")

    connection = _connect_database(data_root)
    connection.commit()
    try:
        result = apply_retention(connection, data_root=data_root, now=NOW)

        assert result.expired_source_observation_files == 0
        assert result.source_observation_deletion_failures == 1
        assert external_expired.is_file()
    finally:
        connection.close()
        observation_root.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
def test_retention_refuses_configured_data_root_junction_before_cleanup(
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "external-data"
    observation_root = target_root / "observations"
    observation_root.mkdir(parents=True)
    external_expired = observation_root / "room-old-observation-1.json"
    external_expired.write_text(
        json.dumps(
            {
                "kind": "room",
                "retention_expires_at": "2026-10-12T12:59:59+09:00",
            }
        ),
        encoding="utf-8",
    )
    connection = _connect_database(target_root)
    connection.commit()
    configured_root = tmp_path / "configured-data"
    junction = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(configured_root),
            str(target_root),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if junction.returncode != 0:
        connection.close()
        pytest.skip(f"could not create a fixture junction: {junction.stderr}")

    try:
        with pytest.raises(RetentionError, match="data root contains"):
            apply_retention(connection, data_root=configured_root, now=NOW)
        assert not connection.in_transaction
        assert external_expired.is_file()
    finally:
        connection.close()
        configured_root.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
def test_retention_detects_dangling_observation_directory_junction(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    observation_root = data_root / "observations"
    target = tmp_path / "junction-target"
    target.mkdir()
    junction = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(observation_root),
            str(target),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if junction.returncode != 0:
        pytest.skip(f"could not create a fixture junction: {junction.stderr}")
    try:
        target.rmdir()
    except OSError as exc:
        observation_root.rmdir()
        pytest.skip(f"could not make a dangling fixture junction: {exc}")

    connection = _connect_database(data_root)
    connection.commit()
    try:
        result = apply_retention(connection, data_root=data_root, now=NOW)

        assert result.expired_source_observation_files == 0
        assert result.source_observation_deletion_failures == 1
        assert getattr(observation_root.lstat(), "st_file_attributes", 0) & 0x400
    finally:
        connection.close()
        observation_root.rmdir()


def test_retention_never_deletes_an_artifact_outside_the_explicit_data_root(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    connection = _seed_retention_database(data_root)
    try:
        outside_path = tmp_path / "outside-fixture.json"
        outside_path.write_text("fixture-only", encoding="utf-8")
        _insert_artifact(
            connection,
            data_root,
            artifact_ref="escaped-artifact",
            expires_at=NOW,
        )
        connection.execute(
            "UPDATE artifact SET path=? WHERE artifact_ref='escaped-artifact'",
            (str(outside_path.resolve()),),
        )
        connection.commit()

        result = apply_retention(connection, data_root=data_root, now=NOW)

        assert "escaped-artifact" in result.expired_artifact_refs
        assert "escaped-artifact" in result.file_deletion_failures
        assert outside_path.read_text(encoding="utf-8") == "fixture-only"
        assert (
            connection.execute(
                "SELECT status FROM artifact WHERE artifact_ref='escaped-artifact'"
            ).fetchone()[0]
            == "expired"
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("observed_at", "expiry_at"),
    [
        (
            f"2024-02-29T13:00:00{JST}",
            f"2025-02-28T13:00:00{JST}",
        ),
        (
            f"2023-03-01T13:00:00{JST}",
            f"2024-03-01T13:00:00{JST}",
        ),
    ],
)
@pytest.mark.parametrize(
    "offset_seconds,expected", [(-1, "observed"), (0, "expired"), (1, "expired")]
)
def test_aggregate_retention_boundary_before_equal_after(
    tmp_path: Path,
    observed_at: str,
    expiry_at: str,
    offset_seconds: int,
    expected: str,
) -> None:
    data_root = tmp_path / "data"
    connection = _seed_retention_database(data_root)
    try:
        connection.execute(
            "UPDATE metric_observation SET observed_at=? WHERE observation_id='AGG-01'",
            (observed_at,),
        )
        connection.commit()
        expiry = datetime.fromisoformat(expiry_at)

        apply_retention(
            connection,
            data_root=data_root,
            now=expiry + timedelta(seconds=offset_seconds),
        )

        row = connection.execute(
            "SELECT availability, value_json FROM metric_observation WHERE observation_id='AGG-01'"
        ).fetchone()
        assert row[0] == expected
        assert row[1] == ("100" if expected == "observed" else None)
    finally:
        connection.close()


def test_daily_backup_is_consistent_keeps_seven_generations_and_bundles_artifacts(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    backup_root = tmp_path / "backups"
    connection = _seed_retention_database(data_root)
    try:
        created = []
        for day in range(1, 9):
            now = datetime.fromisoformat(f"2026-10-{day:02d}T13:00:00{JST}")
            created.append(
                create_daily_backup(
                    connection,
                    data_root=data_root,
                    backup_root=backup_root,
                    now=now,
                    fixture_mode=True,
                )
            )

        generations = list_daily_backups(backup_root)
        assert len(generations) == 7
        assert created[0].directory not in generations
        assert (
            _scalar(connection, "SELECT COUNT(*) FROM backup WHERE status='complete'")
            == 8
        )
        latest = generations[-1]
        manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == 4
        assert manifest["table_counts"]["run"] == 1
        assert manifest["table_counts"]["draft"] == 17
        assert manifest["table_counts"]["backup"] == 7
        assert manifest["validation"] == {
            "db_integrity": True,
            "counts_match": True,
            "details_expired_filtered": True,
            "errors": [],
        }
        assert {item["artifact_ref"] for item in manifest["artifacts"]} == {
            "raw-before",
            "raw-equal",
            "raw-after",
        }
        snapshot = sqlite3.connect(latest / "state.sqlite")
        try:
            assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert snapshot.execute("PRAGMA foreign_key_check").fetchall() == []
            assert (
                snapshot.execute(
                    "SELECT COUNT(*) FROM reservation WHERE status='active'"
                ).fetchone()[0]
                == 7
            )
        finally:
            snapshot.close()
        for item in manifest["artifacts"]:
            assert (latest / item["bundle_path"]).is_file()
    finally:
        connection.close()


def test_failed_backup_keeps_source_and_prior_generation_unchanged(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    backup_root = tmp_path / "backups"
    connection = _seed_retention_database(data_root)
    try:
        first = create_daily_backup(
            connection,
            data_root=data_root,
            backup_root=backup_root,
            now=NOW,
            fixture_mode=True,
        )
        before = connection.execute(
            "SELECT committed_count FROM run WHERE run_id='RUN-2026-10-12'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE artifact SET path=?, retention_expires_at=NULL WHERE artifact_ref='raw-before'",
            (str((tmp_path / "missing.json").resolve()),),
        )
        connection.commit()
        with pytest.raises(BackupError):
            create_daily_backup(
                connection,
                data_root=data_root,
                backup_root=backup_root,
                now=NOW + timedelta(days=1),
                fixture_mode=True,
            )
        assert first.directory.is_dir()
        assert (
            connection.execute(
                "SELECT committed_count FROM run WHERE run_id='RUN-2026-10-12'"
            ).fetchone()[0]
            == before
        )
        assert (
            connection.execute("SELECT status FROM backup ORDER BY rowid").fetchall()[
                0
            ][0]
            == "complete"
        )
        assert (
            connection.execute(
                "SELECT status FROM backup ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
            == "failed"
        )
        assert len(list_daily_backups(backup_root)) == 1
    finally:
        connection.close()


def test_backup_rejects_duplicate_daily_generation_and_non_fixture_destination(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    connection = _seed_retention_database(data_root)
    try:
        create_daily_backup(
            connection,
            data_root=data_root,
            backup_root=tmp_path / "backups",
            now=NOW,
            fixture_mode=True,
        )
        with pytest.raises(BackupError, match="already exists"):
            create_daily_backup(
                connection,
                data_root=data_root,
                backup_root=tmp_path / "backups",
                now=NOW,
                fixture_mode=True,
            )
        with pytest.raises(BackupError, match="fixture"):
            create_daily_backup(
                connection,
                data_root=data_root,
                backup_root=Path("C:/project/rakuten-room-inago-backups"),
                now=NOW + timedelta(days=1),
                fixture_mode=True,
            )
    finally:
        connection.close()


def test_restore_check_isolated_reexpires_data_and_never_allows_selection(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    backup_root = tmp_path / "backups"
    connection = _seed_retention_database(data_root)
    try:
        generation_path = _insert_artifact(
            connection,
            data_root,
            artifact_ref="generation-live",
            expires_at=NOW + timedelta(days=90),
            source_kind="generation",
        )
        connection.execute(
            "UPDATE draft SET artifact_ref='generation-live' WHERE draft_id='D-01'"
        )
        missing_path = _insert_artifact(
            connection,
            data_root,
            artifact_ref="raw-missing",
            expires_at=None,
        )
        missing_path.unlink()
        connection.execute(
            "UPDATE artifact SET status='missing' WHERE artifact_ref='raw-missing'"
        )
        connection.commit()
        before = {
            "run": connection.execute("SELECT COUNT(*) FROM run").fetchone()[0],
            "draft": connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0],
            "artifacts": connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[
                0
            ],
            "reservations": connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0],
            "actions": connection.execute(
                "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'"
            ).fetchone()[0],
        }
        backup = create_daily_backup(
            connection,
            data_root=data_root,
            backup_root=backup_root,
            now=NOW - timedelta(seconds=2),
            fixture_mode=True,
        )
        result = check_monthly_restore(
            backup.directory,
            backup_root=backup_root,
            now=NOW + timedelta(seconds=1),
            fixture_mode=True,
        )

        assert result.selection_allowed is False
        assert result.requires_supplementation is True
        assert result.old_tasks_enabled is None
        assert result.old_task_activation_attempts == 0
        assert result.old_task_state_observed is False
        assert result.table_counts_match is True
        assert result.integrity_check == "ok"
        assert result.foreign_key_errors == 0
        assert result.expired_artifacts == 3
        assert result.expired_aggregates == 3
        assert result.posted_count == 10
        assert result.active_reservations == 7
        assert result.pending_reports == 3
        assert result.correction_count == 1
        assert result.run_count == before["run"] == 1
        assert result.directory.is_dir()
        assert result.directory.resolve() != data_root.resolve()
        restored = sqlite3.connect(result.database_path)
        try:
            available = restored.execute(
                "SELECT path FROM artifact WHERE artifact_ref='generation-live' AND status='available'"
            ).fetchone()
            assert available is not None
            restored_artifact = Path(available[0])
            assert restored_artifact.is_file()
            assert (
                restored_artifact.parent.parent.parent
                == result.directory / "restored-data"
            )
            assert restored_artifact != generation_path
            assert (
                restored.execute(
                    "SELECT COUNT(*) FROM artifact WHERE status='available'"
                ).fetchone()[0]
                == 1
            )
            assert (
                restored.execute(
                    "SELECT COUNT(*) FROM artifact WHERE status='expired'"
                ).fetchone()[0]
                == 3
            )
            assert (
                restored.execute(
                    "SELECT status FROM artifact WHERE artifact_ref='raw-missing'"
                ).fetchone()[0]
                == "missing"
            )
            assert (
                restored.execute("SELECT COUNT(*) FROM revenue_order").fetchone()[0]
                == 0
            )
            assert (
                restored.execute(
                    "SELECT COUNT(*) FROM metric_observation WHERE availability='observed'"
                ).fetchone()[0]
                == 0
            )
            assert (
                restored.execute(
                    "SELECT COUNT(*) FROM metric_observation WHERE availability='expired'"
                ).fetchone()[0]
                == 3
            )
            assert restored.execute(
                "SELECT availability, likes FROM source_post WHERE source_key='SOURCE-RETENTION'"
            ).fetchone() == ("expired", None)
            assert restored.execute(
                "SELECT availability, words_json FROM keyword_snapshot WHERE snapshot_id='KEYWORD-RETENTION'"
            ).fetchone() == ("expired", "[]")
            assert (
                restored.execute("SELECT version FROM settings_version").fetchone()[0]
                == SETTINGS_VERSION
            )
        finally:
            restored.close()
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM artifact WHERE status='available'"
            ).fetchone()[0]
            == 4
        )
        assert (
            connection.execute(
                "SELECT status FROM artifact WHERE artifact_ref='raw-missing'"
            ).fetchone()[0]
            == "missing"
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM revenue_order").fetchone()[0] == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM metric_observation WHERE availability='observed'"
            ).fetchone()[0]
            == 3
        )
        assert tuple(
            connection.execute(
                "SELECT availability, likes FROM source_post WHERE source_key='SOURCE-RETENTION'"
            ).fetchone()
        ) == ("observed", 300)
        assert tuple(
            connection.execute(
                "SELECT availability, words_json FROM keyword_snapshot WHERE snapshot_id='KEYWORD-RETENTION'"
            ).fetchone()
        ) == ("observed", '["fixture-term"]')
        assert connection.execute("SELECT committed_count FROM run").fetchone()[0] == 15

        repeated = check_monthly_restore(
            backup.directory,
            backup_root=backup_root,
            now=NOW + timedelta(days=4),
            fixture_mode=True,
        )
        assert repeated.already_checked is True
        assert repeated.directory == result.directory
    finally:
        connection.close()


def test_restore_check_rejects_path_traversal_without_live_writes(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    backup_root = tmp_path / "backups"
    connection = _seed_retention_database(data_root)
    try:
        backup = create_daily_backup(
            connection,
            data_root=data_root,
            backup_root=backup_root,
            now=NOW - timedelta(seconds=1),
            fixture_mode=True,
        )
        manifest_path = backup.directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["bundle_path"] = "../../outside.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with pytest.raises(RestoreCheckError, match="path"):
            check_monthly_restore(
                backup.directory,
                backup_root=backup_root,
                now=NOW + timedelta(seconds=1),
                fixture_mode=True,
            )
        assert connection.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM artifact WHERE status='available'"
            ).fetchone()[0]
            == 3
        )
        assert not (backup_root / "restore-check" / "2026-10").exists()
    finally:
        connection.close()


def test_restore_check_rejects_corrupt_sqlite_copy_without_touching_source(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    backup_root = tmp_path / "backups"
    connection = _seed_retention_database(data_root)
    try:
        backup = create_daily_backup(
            connection,
            data_root=data_root,
            backup_root=backup_root,
            now=NOW - timedelta(seconds=2),
            fixture_mode=True,
        )
        (backup.directory / "state.sqlite").write_bytes(b"not a SQLite database")

        with pytest.raises(RestoreCheckError):
            check_monthly_restore(
                backup.directory,
                backup_root=backup_root,
                now=NOW + timedelta(seconds=1),
                fixture_mode=True,
            )
        assert connection.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM artifact WHERE status='available'"
            ).fetchone()[0]
            == 3
        )
        assert not (backup_root / "restore-check" / "2026-10").exists()
    finally:
        connection.close()
