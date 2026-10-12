"""Retention, daily backup, and isolated restore-check contracts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from automation.inago.backup import BackupError, create_daily_backup, list_daily_backups
from automation.inago.repository import initialize_database
from automation.inago.restore_check import RestoreCheckError, check_monthly_restore
from automation.inago.retention import (
    ArtifactExpiredError,
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
) -> Path:
    artifact_dir = data_root / "artifacts" / "fixture"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{artifact_ref}.json"
    artifact_path.write_bytes(body or json.dumps({"fixture": artifact_ref}).encode())
    connection.execute(
        """
        INSERT INTO artifact(
            artifact_ref, path, hash, settings_version, input_refs_json,
            evidence_refs_json, created_at, source_kind, retention_expires_at, status
        ) VALUES (?, ?, 'fixture-runtime-record', ?, '[]', '[]', ?, 'daily', ?, 'available')
        """,
        (
            artifact_ref,
            str(artifact_path.resolve()),
            SETTINGS_VERSION,
            (created_at or NOW).isoformat(),
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
                      ?, 'day', 'observed', NULL, 0)
            """,
            (f"AGG-{index:02d}", f"day-{index:02d}", observed_at.isoformat()),
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
    try:
        result = apply_retention(connection, data_root=data_root, now=NOW)

        assert result.expired_artifacts == 2
        assert result.expired_aggregates == 2
        assert _scalar(connection, "SELECT COUNT(*) FROM artifact WHERE status='available'") == 1
        assert _scalar(connection, "SELECT COUNT(*) FROM artifact WHERE status='expired'") == 2
        assert _scalar(connection, "SELECT COUNT(*) FROM metric_observation WHERE availability='observed'") == 1
        assert _scalar(connection, "SELECT COUNT(*) FROM metric_observation WHERE availability='expired'") == 2
        assert connection.execute(
            "SELECT status FROM artifact WHERE artifact_ref='raw-before'"
        ).fetchone()[0] == "available"
        assert connection.execute(
            "SELECT status FROM artifact WHERE artifact_ref='raw-equal'"
        ).fetchone()[0] == "expired"
        assert not (data_root / "artifacts" / "fixture" / "raw-equal.json").exists()
        assert _scalar(connection, "SELECT COUNT(*) FROM exclusion_key WHERE effective=1") == 10
        assert _scalar(connection, "SELECT COUNT(*) FROM reservation WHERE status='active'") == 7
        assert _scalar(connection, "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'") == 3
        assert _scalar(connection, "SELECT COUNT(*) FROM human_action WHERE action='correction'") == 1
        assert _scalar(connection, "SELECT committed_count FROM run WHERE run_id='RUN-2026-10-12'") == 15
        with pytest.raises(ArtifactExpiredError):
            result.open_artifact(connection, "raw-equal")

        later = apply_retention(
            connection, data_root=data_root, now=NOW + timedelta(seconds=1)
        )
        assert later.expired_artifacts == 1
        assert later.expired_aggregates == 1
        assert _scalar(connection, "SELECT COUNT(*) FROM artifact WHERE status='available'") == 0
        assert _scalar(connection, "SELECT COUNT(*) FROM metric_observation WHERE availability='observed'") == 0
        assert _scalar(connection, "SELECT COUNT(*) FROM exclusion_key WHERE effective=1") == 10
        assert _scalar(connection, "SELECT COUNT(*) FROM reservation WHERE status='active'") == 7
        assert _scalar(connection, "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'") == 3
        assert _scalar(connection, "SELECT COUNT(*) FROM human_action WHERE action='correction'") == 1
    finally:
        connection.close()


def test_retention_keeps_expired_artifact_when_a_saved_draft_still_references_it(
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
        )
        connection.execute(
            "UPDATE draft SET artifact_ref='draft-required' WHERE draft_id='D-01'"
        )
        connection.commit()

        result = apply_retention(connection, data_root=data_root, now=NOW)

        assert result.expired_artifacts == 2
        assert connection.execute(
            "SELECT status FROM artifact WHERE artifact_ref='draft-required'"
        ).fetchone()[0] == "available"
        assert artifact_path.is_file()
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
        latest = generations[-1]
        manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == 2
        assert manifest["table_counts"]["run"] == 1
        assert manifest["table_counts"]["draft"] == 17
        assert {item["artifact_ref"] for item in manifest["artifacts"]} == {
            "raw-before",
            "raw-equal",
            "raw-after",
        }
        snapshot = sqlite3.connect(latest / "state.sqlite")
        try:
            assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert snapshot.execute("PRAGMA foreign_key_check").fetchall() == []
            assert snapshot.execute("SELECT COUNT(*) FROM reservation WHERE status='active'").fetchone()[0] == 7
        finally:
            snapshot.close()
        for item in manifest["artifacts"]:
            assert (latest / item["bundle_path"]).is_file()
    finally:
        connection.close()


def test_failed_backup_keeps_source_and_prior_generation_unchanged(tmp_path: Path) -> None:
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
            "UPDATE artifact SET path=? WHERE artifact_ref='raw-before'",
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
        assert connection.execute(
            "SELECT committed_count FROM run WHERE run_id='RUN-2026-10-12'"
        ).fetchone()[0] == before
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
            fixture_mode=False,
        )
        with pytest.raises(BackupError, match="already exists"):
            create_daily_backup(
                connection,
                data_root=data_root,
                backup_root=tmp_path / "backups",
                now=NOW,
                fixture_mode=False,
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
        before = {
            "run": connection.execute("SELECT COUNT(*) FROM run").fetchone()[0],
            "draft": connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0],
            "artifacts": connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0],
            "reservations": connection.execute("SELECT COUNT(*) FROM reservation WHERE status='active'").fetchone()[0],
            "actions": connection.execute("SELECT COUNT(*) FROM human_action WHERE apply_status='pending'").fetchone()[0],
        }
        backup = create_daily_backup(
            connection,
            data_root=data_root,
            backup_root=backup_root,
            now=NOW - timedelta(seconds=1),
            fixture_mode=False,
        )
        result = check_monthly_restore(
            backup.directory,
            backup_root=backup_root,
            now=NOW + timedelta(seconds=1),
            fixture_mode=False,
        )

        assert result.selection_allowed is False
        assert result.requires_supplementation is True
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
            assert restored.execute("SELECT COUNT(*) FROM artifact WHERE status='available'").fetchone()[0] == 0
            assert restored.execute("SELECT COUNT(*) FROM artifact WHERE status='expired'").fetchone()[0] == 3
            assert restored.execute("SELECT COUNT(*) FROM metric_observation WHERE availability='observed'").fetchone()[0] == 0
            assert restored.execute("SELECT COUNT(*) FROM metric_observation WHERE availability='expired'").fetchone()[0] == 3
        finally:
            restored.close()
        assert connection.execute("SELECT COUNT(*) FROM artifact WHERE status='available'").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM metric_observation WHERE availability='observed'").fetchone()[0] == 3
        assert connection.execute("SELECT committed_count FROM run").fetchone()[0] == 15

        repeated = check_monthly_restore(
            backup.directory,
            backup_root=backup_root,
            now=NOW + timedelta(days=4),
            fixture_mode=False,
        )
        assert repeated.already_checked is True
        assert repeated.directory == result.directory
    finally:
        connection.close()


def test_restore_check_rejects_path_traversal_and_corrupt_snapshot_without_live_writes(
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
            fixture_mode=False,
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
                fixture_mode=False,
            )
        assert connection.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM artifact WHERE status='available'").fetchone()[0] == 3
        assert not (backup_root / "restore-check" / "2026-10").exists()
    finally:
        connection.close()

