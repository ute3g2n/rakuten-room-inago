"""Contracts for importing browser-held human reports before daily application."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import automation.inago.repository as repository
from automation.inago.clock import FakeClock
from automation.inago.repository import initialize_database
from automation.inago.report_import import (
    MAX_BATCH_BYTES,
    ReportImportError,
    import_offline_report_batches,
)
from tests_inago.test_step05 import _seed_fifteen_drafts


def _database(tmp_path: Path):
    database_path = tmp_path / "state.sqlite"

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(database_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        initialize_database(connection)
        return connection

    connection = connect()
    try:
        _seed_fifteen_drafts(connection)
    finally:
        connection.close()
    return connect


def _report(action_id: str, *, expected_revision: int, action: str = "posted") -> dict:
    return {
        "action_id": action_id,
        "offer_key": "shop_test:item_A01",
        "draft_id": "D-A01",
        "draft_version": 1,
        "expected_receipt_revision": expected_revision,
        "action": action,
        "reason": None,
        "correction_ref": None,
        "actual_posted_at": None,
    }


def _write_batch(inbox: Path, name: str, reports: list[dict]) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reports": [
                    {
                        "queued_at": "2026-10-15T21:10:00+09:00",
                        "report": report,
                    }
                    for report in reports
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_import_records_original_queue_time_and_retries_idempotently(tmp_path: Path) -> None:
    connect = _database(tmp_path)
    runtime_root = tmp_path / "runtime"
    inbox = runtime_root / "incoming-human-reports"
    source = _write_batch(
        inbox,
        "human-reports-2026-10-15T21-10-00.json",
        [_report("ACT-OFFLINE-POST-1", expected_revision=0)],
    )
    clock = FakeClock(datetime(2026, 10, 16, 13, 0, tzinfo=ZoneInfo("Asia/Tokyo")))

    connection = connect()
    try:
        result = import_offline_report_batches(
            connection, runtime_root=runtime_root, clock=clock
        )
        assert result.reports_received == 1
        row = connection.execute(
            "SELECT action, reported_at, apply_status FROM human_action"
        ).fetchone()
        assert tuple(row) == ("posted", "2026-10-15T21:10:00+09:00", "pending")
        assert not source.exists()
        assert (runtime_root / "processed-human-reports" / source.name).is_file()
    finally:
        connection.close()

    retry_source = _write_batch(
        inbox,
        "human-reports-retry.json",
        [_report("ACT-OFFLINE-POST-1", expected_revision=0)],
    )
    connection = connect()
    try:
        result = import_offline_report_batches(
            connection, runtime_root=runtime_root, clock=clock
        )
        assert result.reports_received == 1
        assert connection.execute("SELECT COUNT(*) FROM human_action").fetchone()[0] == 1
        assert not retry_source.exists()
    finally:
        connection.close()


def test_invalid_batch_revision_rolls_back_all_reports_and_keeps_file(tmp_path: Path) -> None:
    connect = _database(tmp_path)
    runtime_root = tmp_path / "runtime"
    source = _write_batch(
        runtime_root / "incoming-human-reports",
        "human-reports-conflict.json",
        [
            _report("ACT-OFFLINE-POST-2", expected_revision=0),
            _report("ACT-OFFLINE-SKIP-2", expected_revision=0, action="skipped"),
        ],
    )
    connection = connect()
    try:
        with pytest.raises(ReportImportError, match="revision"):
            import_offline_report_batches(
                connection,
                runtime_root=runtime_root,
                clock=FakeClock(
                    datetime(2026, 10, 16, 13, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
                ),
            )
        assert connection.execute("SELECT COUNT(*) FROM human_action").fetchone()[0] == 0
        assert source.is_file()
    finally:
        connection.close()


def test_import_rejects_duplicate_keys_and_oversized_batches(tmp_path: Path) -> None:
    connect = _database(tmp_path)
    runtime_root = tmp_path / "runtime"
    inbox = runtime_root / "incoming-human-reports"
    inbox.mkdir(parents=True)
    source = inbox / "human-reports-malformed.json"
    source.write_text('{"schema_version":1,"schema_version":1,"reports":[]}', encoding="utf-8")
    connection = connect()
    try:
        with pytest.raises(ReportImportError, match="JSON"):
            import_offline_report_batches(connection, runtime_root=runtime_root)
        assert connection.execute("SELECT COUNT(*) FROM human_action").fetchone()[0] == 0
        source.write_bytes(b" " * (MAX_BATCH_BYTES + 1))
        with pytest.raises(ReportImportError, match="size"):
            import_offline_report_batches(connection, runtime_root=runtime_root)
        assert connection.execute("SELECT COUNT(*) FROM human_action").fetchone()[0] == 0
    finally:
        connection.close()


def test_schema_v4_to_v5_migration_preserves_rows_and_adds_pending_correction_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "schema-v4.sqlite"
    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        monkeypatch.setattr(repository, "CURRENT_SCHEMA_VERSION", 4)
        assert initialize_database(connection) == 4
        _seed_fifteen_drafts(connection)
        before = connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0]
        assert before == 15
        assert "correction_action_ref" not in {
            row[1] for row in connection.execute("PRAGMA table_info(human_action)")
        }

        monkeypatch.setattr(repository, "CURRENT_SCHEMA_VERSION", 5)
        assert initialize_database(connection) == 5
        after = connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0]
        assert after == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert "correction_action_ref" in {
            row[1] for row in connection.execute("PRAGMA table_info(human_action)")
        }
        foreign_keys = connection.execute("PRAGMA foreign_key_list(human_action)").fetchall()
        assert any(
            row["from"] == "correction_action_ref"
            and row["table"] == "human_action"
            and row["to"] == "action_id"
            for row in foreign_keys
        )
    finally:
        connection.close()
