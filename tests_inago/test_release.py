"""Cross-flow migration, backup, and isolated restore rehearsal for STEP23."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from automation.inago.backup import create_daily_backup
from automation.inago.restore_check import check_monthly_restore
from tests_inago.test_step17 import NOW as RESTORE_NOW
from tests_inago.test_step18 import _create_database, _migrate, _write_sources


def test_release_migrated_history_backup_and_restore_requires_newer_reports(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "legacy-source"
    data_root = tmp_path / "new-data"
    backup_root = tmp_path / "backups"
    source_contents = _write_sources(source_root)
    connection = _create_database(data_root)
    try:
        migration = _migrate(connection, source_root, data_root, mode="apply")
        assert migration.created_publications == 2
        assert migration.selection_allowed is False
        assert migration.supplementation_required
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM publication_event WHERE valid=1"
            ).fetchone()[0]
            == 2
        )

        backup = create_daily_backup(
            connection,
            data_root=data_root,
            backup_root=backup_root,
            now=RESTORE_NOW + timedelta(seconds=1),
            fixture_mode=True,
        )

        # Simulate a valid human report arriving after the backup snapshot. The
        # isolated restore must require its recovery before selection can resume.
        connection.execute(
            """
            INSERT INTO human_action(
                action_id, action, offer_key, draft_id, draft_version,
                prior_revision, receipt_revision, reason, reported_at,
                old_event_ref, new_body, apply_status, applied_at,
                applied_run_id, apply_error, actual_posted_at
            ) VALUES ('Q-AFTER-BACKUP', 'posted', 'shop_test:item_D',
                      'D-NEW-D', 1, 0, 1, NULL, ?, NULL, NULL, 'pending',
                      NULL, NULL, NULL, ?)
            """,
            (
                (RESTORE_NOW + timedelta(hours=8)).isoformat(),
                (RESTORE_NOW + timedelta(hours=7)).isoformat(),
            ),
        )
        connection.commit()
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'"
            ).fetchone()[0]
            == 1
        )

        restored = check_monthly_restore(
            backup.directory,
            backup_root=backup_root,
            now=RESTORE_NOW + timedelta(days=1),
            fixture_mode=True,
        )

        assert restored.directory.resolve() != data_root.resolve()
        assert restored.integrity_check == "ok"
        assert restored.foreign_key_errors == 0
        assert restored.table_counts_match is True
        assert restored.selection_allowed is False
        assert restored.requires_supplementation is True
        assert restored.old_task_activation_attempts == 0
        assert restored.old_task_state_observed is False
        restored_connection = sqlite3.connect(restored.database_path)
        try:
            assert (
                restored_connection.execute(
                    "SELECT COUNT(*) FROM publication_event WHERE valid=1"
                ).fetchone()[0]
                == 2
            )
            assert (
                restored_connection.execute(
                    "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'"
                ).fetchone()[0]
                == 0
            )
            assert (
                restored_connection.execute(
                    "SELECT COUNT(*) FROM human_action WHERE action_id='Q-AFTER-BACKUP'"
                ).fetchone()[0]
                == 0
            )
            assert (
                restored_connection.execute(
                    "SELECT COUNT(*) FROM exclusion_key WHERE effective=1"
                ).fetchone()[0]
                == 2
            )
        finally:
            restored_connection.close()

        assert {path.name: path.read_bytes() for path in source_root.iterdir()} == (
            source_contents
        )
    finally:
        connection.close()
