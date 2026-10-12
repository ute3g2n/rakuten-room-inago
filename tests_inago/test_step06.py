"""Next-day application of pending human reports for STEP 06."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from automation.inago.clock import FakeClock
from automation.inago.repository import initialize_database, record_pending_human_action
from automation.inago.report_apply import apply_pending_reports


JST = ZoneInfo("Asia/Tokyo")
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
CURRENT_DATE = "2026-10-13"
PRIOR_DATE = "2026-10-12"


@pytest.fixture
def database(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    initialize_database(connection)
    _seed_runs(connection)
    _seed_fifteen_reserved_drafts(connection)
    try:
        yield connection
    finally:
        connection.close()


def _seed_runs(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO settings_version
            (version, confirmed_at, genres_json, themes_json, pattern_refs_json,
             change_reason, created_at)
        VALUES (?, NULL, '[]', '[]', '[\"P01\",\"P02\",\"P03\",\"P04\",\"P05\"]',
                NULL, '2026-10-12T13:00:00+09:00')
        """,
        (SETTINGS_VERSION,),
    )
    for run_id, target_date, status, committed_count in (
        ("R1", PRIOR_DATE, "complete", 15),
        ("R2", CURRENT_DATE, "running", 0),
    ):
        connection.execute(
            """
            INSERT INTO run
                (run_id, target_date, mode, status, settings_version, seed,
                 started_at, draft_deadline, end_deadline, attempt_used,
                 committed_count, checkpoint, metrics_status, finished_at)
            VALUES (?, ?, 'daily', ?, ?, ?, ?, ?, ?, 0, ?, NULL,
                    'not_started', NULL)
            """,
            (
                run_id,
                target_date,
                status,
                SETTINGS_VERSION,
                f"seed-{run_id}",
                f"{target_date}T13:00:00+09:00",
                f"{target_date}T17:45:00+09:00",
                f"{target_date}T18:00:00+09:00",
                committed_count,
            ),
        )


def _seed_fifteen_reserved_drafts(connection: sqlite3.Connection) -> None:
    for number in range(1, 16):
        suffix = f"A{number:02}"
        offer_key = f"shop_test:item_{suffix}"
        draft_id = f"D-{suffix}"
        created_at = f"{PRIOR_DATE}T13:10:00+09:00"
        connection.execute(
            """
            INSERT INTO offer
                (offer_key, shop_id, item_id, name, url, image_refs_json,
                 main_theme_id, aux_theme_ids_json, first_seen, last_seen,
                 facts_json)
            VALUES (?, 'shop_test', ?, ?, ?, '[]', 'G01-T01', '[]', ?, ?, '[]')
            """,
            (
                offer_key,
                f"item_{suffix}",
                f"匿名fixture商品{number}",
                f"https://example.invalid/items/{suffix}",
                created_at,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO draft
                (draft_id, offer_key, run_id, version, body, generated_at,
                 committed_at, generator_version, pattern_version, status,
                 artifact_ref, pattern_id, evidence_refs_json)
            VALUES (?, ?, 'R1', 1, ?, ?, ?, 'fixture-copy-1', ?,
                    'committed', NULL, 'P01', '[]')
            """,
            (
                draft_id,
                offer_key,
                f"匿名fixture本文{number}",
                created_at,
                created_at,
                SETTINGS_VERSION,
            ),
        )
        connection.execute(
            """
            INSERT INTO reservation
                (reservation_id, offer_key, draft_id, status, created_at,
                 released_at, release_reason)
            VALUES (?, ?, ?, 'active', ?, NULL, NULL)
            """,
            (f"RES-{suffix}-V1", offer_key, draft_id, created_at),
        )


def _offer(number: int) -> str:
    return f"shop_test:item_A{number:02}"


def _draft(number: int, version: int = 1) -> str:
    return f"D-A{number:02}" if version == 1 else f"D-A{number:02}-V{version}"


def _submit_report(
    connection: sqlite3.Connection,
    *,
    number: int,
    action_id: str,
    action: str = "posted",
    reported_at: str = f"{PRIOR_DATE}T21:30:00+09:00",
    draft_id: str | None = None,
    draft_version: int = 1,
    actual_posted_at: str | None = f"{PRIOR_DATE}T21:00:00+09:00",
    reason: str | None = None,
    correction_ref: str | None = None,
) -> dict[str, object]:
    offer_key = _offer(number)
    expected_revision = int(
        connection.execute(
            "SELECT COALESCE(MAX(receipt_revision), 0) FROM human_action "
            "WHERE offer_key = ?",
            (offer_key,),
        ).fetchone()[0]
    )
    payload = {
        "action_id": action_id,
        "offer_key": offer_key,
        "draft_id": draft_id or _draft(number, draft_version),
        "draft_version": draft_version,
        "expected_receipt_revision": expected_revision,
        "action": action,
        "reason": reason,
        "correction_ref": correction_ref,
        "actual_posted_at": actual_posted_at,
    }
    clock = FakeClock(datetime.fromisoformat(reported_at))
    return record_pending_human_action(connection, payload, clock=clock)


def _insert_publication(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    number: int,
    draft_id: str,
    draft_version: int,
    valid: bool = True,
    reported_at: str = f"{PRIOR_DATE}T21:30:00+09:00",
    actual_posted_at: str | None = f"{PRIOR_DATE}T21:00:00+09:00",
) -> None:
    connection.execute(
        """
        INSERT INTO publication_event
            (event_id, offer_key, draft_id, draft_version, reported_at,
             actual_posted_at, room_url, evidence_kind, valid, revision)
        VALUES (?, ?, ?, ?, ?, ?, NULL, 'human_check', ?, 0)
        """,
        (
            event_id,
            _offer(number),
            draft_id,
            draft_version,
            reported_at,
            actual_posted_at,
            int(valid),
        ),
    )
    if valid:
        connection.execute(
            """
            INSERT INTO exclusion_key
                (offer_key, reason, evidence_refs_json, effective, updated_at)
            VALUES (?, 'actual_posted', ?, 1, ?)
            ON CONFLICT(offer_key) DO UPDATE SET
                evidence_refs_json = excluded.evidence_refs_json,
                effective = 1,
                updated_at = excluded.updated_at
            """,
            (
                _offer(number),
                json.dumps(
                    [
                        row[0]
                        for row in connection.execute(
                            "SELECT event_id FROM publication_event "
                            "WHERE offer_key=? AND valid=1 ORDER BY event_id",
                            (_offer(number),),
                        )
                    ]
                ),
                f"{PRIOR_DATE}T22:00:00+09:00",
            ),
        )


def _apply_clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 13, 13, 0, tzinfo=JST))


def test_applies_eight_posted_reports_atomically_and_keeps_prior_run_count(
    database: sqlite3.Connection,
) -> None:
    for number in range(1, 9):
        _submit_report(database, number=number, action_id=f"ACT-POST-{number:02}")

    result = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert result.cohort_action_ids == tuple(f"ACT-POST-{n:02}" for n in range(1, 9))
    assert result.applied_action_ids == result.cohort_action_ids
    assert result.pending_action_ids == ()
    assert result.exploration_allowed is True
    assert result.error is None
    assert (
        database.execute(
            "SELECT COUNT(*) FROM publication_event WHERE valid=1"
        ).fetchone()[0]
        == 8
    )
    assert (
        database.execute(
            "SELECT COUNT(*) FROM exclusion_key WHERE effective=1"
        ).fetchone()[0]
        == 8
    )
    assert (
        database.execute(
            "SELECT COUNT(*) FROM reservation WHERE status='active'"
        ).fetchone()[0]
        == 7
    )
    assert (
        database.execute(
            "SELECT committed_count FROM run WHERE run_id='R1'"
        ).fetchone()[0]
        == 15
    )
    assert (
        database.execute(
            "SELECT committed_count FROM run WHERE run_id='R2'"
        ).fetchone()[0]
        == 0
    )
    assert (
        database.execute(
            "SELECT COUNT(*) FROM human_action WHERE apply_status='applied' "
            "AND applied_run_id='R2' AND applied_at='2026-10-13T13:00:00+09:00'"
        ).fetchone()[0]
        == 8
    )
    assert (
        database.execute("SELECT COUNT(*) FROM draft WHERE status='posted'").fetchone()[
            0
        ]
        == 8
    )
    checkpoint = json.loads(
        database.execute("SELECT checkpoint FROM run WHERE run_id='R2'").fetchone()[0]
    )
    assert checkpoint["report_apply"]["action_ids"] == list(result.cohort_action_ids)


def test_freezes_all_older_dates_and_late_post_is_only_offer_blocked(
    database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _submit_report(
        database,
        number=1,
        action_id="ACT-OLDER-DAY",
        reported_at="2026-10-11T21:00:00+09:00",
    )
    _submit_report(database, number=2, action_id="ACT-PRIOR-DAY")
    _submit_report(
        database,
        number=3,
        action_id="ACT-CURRENT-DAY",
        reported_at="2026-10-13T12:59:00+09:00",
    )

    import automation.inago.report_apply as report_apply_module

    original_pending = report_apply_module._current_pending_for_cohort
    inserted = False

    def add_late_report_after_cohort(connection, cohort):
        nonlocal inserted
        if not inserted:
            inserted = True
            _submit_report(
                connection,
                number=4,
                action_id="ACT-LATE-POST",
                reported_at="2026-10-13T13:01:00+09:00",
            )
        return original_pending(connection, cohort)

    monkeypatch.setattr(
        report_apply_module,
        "_current_pending_for_cohort",
        add_late_report_after_cohort,
    )
    result = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert result.cohort_action_ids == ("ACT-OLDER-DAY", "ACT-PRIOR-DAY")
    assert result.applied_action_ids == result.cohort_action_ids
    assert result.exploration_allowed is True
    assert result.pending_action_ids == ()
    assert "ACT-CURRENT-DAY" not in result.cohort_action_ids
    assert "ACT-LATE-POST" not in result.cohort_action_ids
    assert result.blocked_offer_keys == (_offer(3), _offer(4))
    assert (
        database.execute(
            "SELECT apply_status FROM human_action WHERE action_id='ACT-CURRENT-DAY'"
        ).fetchone()[0]
        == "pending"
    )


@pytest.mark.parametrize(
    ("checkpoint", "error_code"),
    [("not-json", "run_checkpoint_invalid_json"), ("[]", "run_checkpoint_not_object")],
)
def test_invalid_run_checkpoint_stops_before_changing_management_state(
    database: sqlite3.Connection, checkpoint: str, error_code: str
) -> None:
    _submit_report(database, number=1, action_id="ACT-CHECKPOINT-ERROR")
    database.execute("UPDATE run SET checkpoint=? WHERE run_id='R2'", (checkpoint,))

    result = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert result.exploration_allowed is False
    assert result.error == error_code
    assert result.cohort_action_ids == ()
    assert (
        database.execute(
            "SELECT apply_status FROM human_action WHERE action_id='ACT-CHECKPOINT-ERROR'"
        ).fetchone()[0]
        == "pending"
    )
    assert database.execute("SELECT COUNT(*) FROM publication_event").fetchone()[0] == 0
    assert (
        database.execute(
            "SELECT COUNT(*) FROM reservation WHERE status='active'"
        ).fetchone()[0]
        == 15
    )


@pytest.mark.parametrize(
    ("reported_at", "error_code"),
    [
        ("not-a-timestamp", "reported_at_invalid"),
        ("2026-10-12T21:30:00", "reported_at_timezone_missing"),
    ],
)
def test_invalid_report_time_stays_pending_and_blocks_exploration(
    database: sqlite3.Connection, reported_at: str, error_code: str
) -> None:
    _submit_report(database, number=1, action_id="ACT-BAD-TIME")
    database.execute(
        "UPDATE human_action SET reported_at=? WHERE action_id='ACT-BAD-TIME'",
        (reported_at,),
    )

    result = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert result.exploration_allowed is False
    assert result.error == error_code
    assert (
        database.execute(
            "SELECT apply_status FROM human_action WHERE action_id='ACT-BAD-TIME'"
        ).fetchone()[0]
        == "pending"
    )
    assert database.execute("SELECT COUNT(*) FROM publication_event").fetchone()[0] == 0


def test_empty_prior_day_cohort_is_persisted_and_allows_exploration(
    database: sqlite3.Connection,
) -> None:
    result = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert result.cohort_action_ids == ()
    assert result.pending_action_ids == ()
    assert result.exploration_allowed is True
    assert (
        json.loads(
            database.execute("SELECT checkpoint FROM run WHERE run_id='R2'").fetchone()[
                0
            ]
        )["report_apply"]["action_ids"]
        == []
    )


def test_open_caller_transaction_blocks_report_application(
    database: sqlite3.Connection,
) -> None:
    _submit_report(database, number=1, action_id="ACT-OPEN-TRANSACTION")
    database.execute("BEGIN")

    result = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    database.rollback()
    assert result.exploration_allowed is False
    assert result.error == "database_transaction_already_open"
    assert (
        database.execute(
            "SELECT apply_status FROM human_action WHERE action_id='ACT-OPEN-TRANSACTION'"
        ).fetchone()[0]
        == "pending"
    )


def test_skipped_report_does_not_remove_valid_posted_evidence(
    database: sqlite3.Connection,
) -> None:
    connection = database
    connection.execute(
        "UPDATE reservation SET status='released', released_at=?, "
        "release_reason='posted' WHERE reservation_id='RES-A01-V1'",
        ("2026-10-12T22:00:00+09:00",),
    )
    connection.execute("UPDATE draft SET status='posted' WHERE draft_id='D-A01'")
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES ('D-A01-V2', ?, 'R2', 2, '後続fixture本文',
                '2026-10-13T13:10:00+09:00', '2026-10-13T13:20:00+09:00',
                'fixture-copy-1', ?, 'committed', NULL, 'P01', '[]')
        """,
        (_offer(1), SETTINGS_VERSION),
    )
    connection.execute(
        """
        INSERT INTO reservation
            (reservation_id, offer_key, draft_id, status, created_at,
             released_at, release_reason)
        VALUES ('RES-A01-V2', ?, 'D-A01-V2', 'active',
                '2026-10-13T13:20:00+09:00', NULL, NULL)
        """,
        (_offer(1),),
    )
    _insert_publication(
        connection,
        event_id="PUB-VALID-A01-V1",
        number=1,
        draft_id="D-A01",
        draft_version=1,
    )
    _submit_report(
        connection,
        number=1,
        action_id="ACT-SKIP-A01-V2",
        action="skipped",
        draft_id="D-A01-V2",
        draft_version=2,
        actual_posted_at=None,
    )

    result = apply_pending_reports(connection, run_id="R2", clock=_apply_clock())

    assert result.exploration_allowed is True
    assert (
        connection.execute(
            "SELECT valid FROM publication_event WHERE event_id='PUB-VALID-A01-V1'"
        ).fetchone()[0]
        == 1
    )
    assert tuple(
        connection.execute(
            "SELECT effective, evidence_refs_json FROM exclusion_key WHERE offer_key=?",
            (_offer(1),),
        ).fetchone()
    ) == (1, '["PUB-VALID-A01-V1"]')
    assert tuple(
        connection.execute(
            "SELECT status, release_reason FROM reservation "
            "WHERE reservation_id='RES-A01-V2'"
        ).fetchone()
    ) == ("released", "skipped")
    assert (
        connection.execute(
            "SELECT status FROM draft WHERE draft_id='D-A01-V2'"
        ).fetchone()[0]
        == "do_not_post_already_posted"
    )


def test_skip_keeps_valid_evidence_and_releases_other_active_offer_reservation(
    database: sqlite3.Connection,
) -> None:
    connection = database
    connection.execute(
        "UPDATE reservation SET status='released', released_at=?, "
        "release_reason='posted' WHERE reservation_id='RES-A06-V1'",
        ("2026-10-12T22:00:00+09:00",),
    )
    connection.execute("UPDATE draft SET status='posted' WHERE draft_id='D-A06'")
    connection.execute(
        """INSERT INTO draft
               (draft_id, offer_key, run_id, version, body, generated_at,
                committed_at, generator_version, pattern_version, status,
                artifact_ref, pattern_id, evidence_refs_json)
           VALUES ('D-A06-V2', ?, 'R2', 2, 'fixture later draft',
                   '2026-10-13T13:10:00+09:00', '2026-10-13T13:20:00+09:00',
                   'fixture-copy-1', ?, 'committed', NULL, 'P02', '[]')""",
        (_offer(6), SETTINGS_VERSION),
    )
    connection.execute(
        """INSERT INTO reservation
               (reservation_id, offer_key, draft_id, status, created_at,
                released_at, release_reason)
           VALUES ('RES-A06-V2', ?, 'D-A06-V2', 'active',
                   '2026-10-13T13:20:00+09:00', NULL, NULL)""",
        (_offer(6),),
    )
    _insert_publication(
        connection,
        event_id="PUB-VALID-A06-V1",
        number=6,
        draft_id="D-A06",
        draft_version=1,
    )
    _submit_report(
        connection,
        number=6,
        action_id="ACT-SKIP-A06-V1",
        action="skipped",
        actual_posted_at=None,
    )

    result = apply_pending_reports(connection, run_id="R2", clock=_apply_clock())

    assert result.exploration_allowed is True
    assert (
        connection.execute(
            "SELECT valid FROM publication_event WHERE event_id='PUB-VALID-A06-V1'"
        ).fetchone()[0]
        == 1
    )
    assert tuple(
        connection.execute(
            "SELECT status, release_reason FROM reservation "
            "WHERE reservation_id='RES-A06-V2'"
        ).fetchone()
    ) == ("released", "valid_prior_post")


def test_correction_keeps_other_valid_evidence_and_recomputes_exclusion(
    database: sqlite3.Connection,
) -> None:
    _insert_publication(
        database,
        event_id="PUB-A01-FALSE",
        number=1,
        draft_id="D-A01",
        draft_version=1,
    )
    _insert_publication(
        database,
        event_id="PUB-A01-OTHER",
        number=1,
        draft_id="D-A01",
        draft_version=1,
        reported_at="2026-10-12T22:00:00+09:00",
    )
    _submit_report(
        database,
        number=1,
        action_id="ACT-CORRECT-A01",
        action="correction",
        reason="該当する投稿はしていません",
        correction_ref="PUB-A01-FALSE",
        actual_posted_at=None,
    )

    result = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert result.exploration_allowed is True
    assert (
        database.execute(
            "SELECT valid FROM publication_event WHERE event_id='PUB-A01-FALSE'"
        ).fetchone()[0]
        == 0
    )
    assert (
        database.execute(
            "SELECT valid FROM publication_event WHERE event_id='PUB-A01-OTHER'"
        ).fetchone()[0]
        == 1
    )
    row = database.execute(
        "SELECT effective, evidence_refs_json FROM exclusion_key WHERE offer_key=?",
        (_offer(1),),
    ).fetchone()
    assert tuple(row) == (1, '["PUB-A01-OTHER"]')
    assert (
        database.execute(
            "SELECT COUNT(*) FROM reservation WHERE offer_key=? AND status='active'",
            (_offer(1),),
        ).fetchone()[0]
        == 0
    )


def test_correction_for_another_draft_version_stays_pending_and_blocks_exploration(
    database: sqlite3.Connection,
) -> None:
    _insert_publication(
        database,
        event_id="PUB-A07-V1",
        number=7,
        draft_id="D-A07",
        draft_version=1,
    )
    database.execute("UPDATE draft SET status='posted' WHERE draft_id='D-A07'")
    database.execute(
        "UPDATE reservation SET status='released', released_at=?, "
        "release_reason='posted' WHERE reservation_id='RES-A07-V1'",
        ("2026-10-12T22:00:00+09:00",),
    )
    database.execute(
        """INSERT INTO draft
               (draft_id, offer_key, run_id, version, body, generated_at,
                committed_at, generator_version, pattern_version, status,
                artifact_ref, pattern_id, evidence_refs_json)
           VALUES ('D-A07-V2', ?, 'R2', 2, 'fixture later version',
                   '2026-10-13T13:10:00+09:00', '2026-10-13T13:20:00+09:00',
                   'fixture-copy-1', ?, 'committed', NULL, 'P02', '[]')""",
        (_offer(7), SETTINGS_VERSION),
    )
    database.execute(
        """INSERT INTO reservation
               (reservation_id, offer_key, draft_id, status, created_at,
                released_at, release_reason)
           VALUES ('RES-A07-V2', ?, 'D-A07-V2', 'active',
                   '2026-10-13T13:20:00+09:00', NULL, NULL)""",
        (_offer(7),),
    )
    _submit_report(
        database,
        number=7,
        action_id="ACT-CORRECTION-MISMATCH",
        action="correction",
        draft_id="D-A07-V2",
        draft_version=2,
        reason="別版の投稿報告を訂正しようとしたfixture",
        correction_ref="PUB-A07-V1",
        actual_posted_at=None,
    )

    result = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert result.exploration_allowed is False
    assert result.pending_action_ids == ("ACT-CORRECTION-MISMATCH",)
    assert result.error == "report_apply_failed"
    assert (
        database.execute(
            "SELECT apply_status FROM human_action WHERE action_id='ACT-CORRECTION-MISMATCH'"
        ).fetchone()[0]
        == "pending"
    )
    assert (
        database.execute(
            "SELECT apply_error FROM human_action WHERE action_id='ACT-CORRECTION-MISMATCH'"
        ).fetchone()[0]
        == "report_apply_failed"
    )
    assert (
        database.execute(
            "SELECT valid FROM publication_event WHERE event_id='PUB-A07-V1'"
        ).fetchone()[0]
        == 1
    )
    assert (
        database.execute(
            "SELECT status FROM reservation WHERE reservation_id='RES-A07-V2'"
        ).fetchone()[0]
        == "active"
    )


def test_false_post_correction_restores_original_reservation_without_competitor(
    database: sqlite3.Connection,
) -> None:
    _insert_publication(
        database,
        event_id="PUB-A02-FALSE",
        number=2,
        draft_id="D-A02",
        draft_version=1,
    )
    database.execute("UPDATE draft SET status='posted' WHERE draft_id='D-A02'")
    database.execute(
        "UPDATE reservation SET status='released', released_at=?, "
        "release_reason='posted' WHERE reservation_id='RES-A02-V1'",
        ("2026-10-12T22:00:00+09:00",),
    )
    _submit_report(
        database,
        number=2,
        action_id="ACT-CORRECT-A02",
        action="correction",
        reason="誤って投稿済みにしました",
        correction_ref="PUB-A02-FALSE",
        actual_posted_at=None,
    )

    result = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert result.exploration_allowed is True
    assert (
        database.execute(
            "SELECT COUNT(*) FROM exclusion_key WHERE offer_key=? AND effective=1",
            (_offer(2),),
        ).fetchone()[0]
        == 0
    )
    assert tuple(
        database.execute(
            "SELECT status, released_at, release_reason FROM reservation "
            "WHERE reservation_id='RES-A02-V1'"
        ).fetchone()
    ) == ("active", None, None)
    assert (
        database.execute("SELECT status FROM draft WHERE draft_id='D-A02'").fetchone()[
            0
        ]
        == "committed"
    )
    assert (
        database.execute(
            "SELECT valid FROM publication_event WHERE event_id='PUB-A02-FALSE'"
        ).fetchone()[0]
        == 0
    )


def test_false_post_correction_keeps_one_competing_later_reservation(
    database: sqlite3.Connection,
) -> None:
    _insert_publication(
        database,
        event_id="PUB-A05-FALSE",
        number=5,
        draft_id="D-A05",
        draft_version=1,
    )
    database.execute("UPDATE draft SET status='posted' WHERE draft_id='D-A05'")
    database.execute(
        "UPDATE reservation SET status='released', released_at=?, "
        "release_reason='posted' WHERE reservation_id='RES-A05-V1'",
        ("2026-10-12T22:00:00+09:00",),
    )
    database.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES ('D-A05-V2', ?, 'R2', 2, '後続fixture本文',
                '2026-10-13T13:10:00+09:00', '2026-10-13T13:20:00+09:00',
                'fixture-copy-1', ?, 'do_not_post_already_posted', NULL, 'P02', '[]')
        """,
        (_offer(5), SETTINGS_VERSION),
    )
    database.execute(
        """
        INSERT INTO reservation
            (reservation_id, offer_key, draft_id, status, created_at,
             released_at, release_reason)
        VALUES ('RES-A05-V2', ?, 'D-A05-V2', 'active',
                '2026-10-13T13:20:00+09:00', NULL, NULL)
        """,
        (_offer(5),),
    )
    _submit_report(
        database,
        number=5,
        action_id="ACT-CORRECT-A05",
        action="correction",
        reason="投稿済み報告を訂正します",
        correction_ref="PUB-A05-FALSE",
        actual_posted_at=None,
    )

    result = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert result.exploration_allowed is True
    assert (
        database.execute(
            "SELECT COUNT(*) FROM exclusion_key WHERE offer_key=? AND effective=1",
            (_offer(5),),
        ).fetchone()[0]
        == 0
    )
    active = database.execute(
        "SELECT draft_id FROM reservation WHERE offer_key=? AND status='active'",
        (_offer(5),),
    ).fetchall()
    assert [row[0] for row in active] == ["D-A05-V2"]
    assert (
        database.execute(
            "SELECT status FROM draft WHERE draft_id='D-A05-V2'"
        ).fetchone()[0]
        == "committed"
    )


def test_old_draft_post_releases_later_reservation_and_preserves_counts(
    database: sqlite3.Connection,
) -> None:
    connection = database
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('R0', '2026-10-11', 'daily', 'complete', ?, 'seed-R0',
                '2026-10-11T13:00:00+09:00', '2026-10-11T17:45:00+09:00',
                '2026-10-11T18:00:00+09:00', 0, 1, NULL, 'not_started',
                '2026-10-11T13:30:00+09:00')
        """,
        (SETTINGS_VERSION,),
    )
    connection.execute("UPDATE draft SET run_id='R0' WHERE draft_id='D-A03'")
    connection.execute(
        """
        UPDATE reservation SET status='released', released_at=?, release_reason='skipped'
        WHERE reservation_id='RES-A03-V1'
        """,
        ("2026-10-12T12:00:00+09:00",),
    )
    connection.execute("UPDATE draft SET status='skipped' WHERE draft_id='D-A03'")
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES ('D-A03-V2', ?, 'R1', 2, '後続fixture本文',
                '2026-10-12T13:10:00+09:00', '2026-10-12T13:20:00+09:00',
                'fixture-copy-1', ?, 'committed', NULL, 'P02', '[]')
        """,
        (_offer(3), SETTINGS_VERSION),
    )
    connection.execute(
        """
        INSERT INTO reservation
            (reservation_id, offer_key, draft_id, status, created_at,
             released_at, release_reason)
        VALUES ('RES-A03-V2', ?, 'D-A03-V2', 'active',
                '2026-10-12T13:20:00+09:00', NULL, NULL)
        """,
        (_offer(3),),
    )
    _submit_report(
        connection,
        number=3,
        action_id="ACT-OLD-V1-POSTED",
        draft_id="D-A03",
        draft_version=1,
    )

    result = apply_pending_reports(connection, run_id="R2", clock=_apply_clock())

    assert result.exploration_allowed is True
    publication = connection.execute(
        "SELECT draft_id, draft_version, actual_posted_at, valid "
        "FROM publication_event WHERE offer_key=?",
        (_offer(3),),
    ).fetchone()
    assert tuple(publication) == ("D-A03", 1, "2026-10-12T21:00:00+09:00", 1)
    assert tuple(
        connection.execute(
            "SELECT status, release_reason FROM reservation "
            "WHERE reservation_id='RES-A03-V2'"
        ).fetchone()
    ) == ("released", "valid_prior_post")
    assert (
        connection.execute(
            "SELECT status FROM draft WHERE draft_id='D-A03-V2'"
        ).fetchone()[0]
        == "do_not_post_already_posted"
    )
    assert (
        connection.execute(
            "SELECT committed_count FROM run WHERE run_id='R1'"
        ).fetchone()[0]
        == 15
    )
    assert (
        connection.execute(
            "SELECT committed_count FROM run WHERE run_id='R0'"
        ).fetchone()[0]
        == 1
    )


def test_partial_failure_rolls_back_one_report_and_resume_applies_only_pending(
    database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    for number in range(1, 4):
        _submit_report(database, number=number, action_id=f"ACT-RESUME-{number}")

    import automation.inago.report_apply as report_apply_module

    original = report_apply_module._apply_action_in_transaction

    def fail_after_mutation(connection, action, run_id, applied_at):
        original(connection, action, run_id, applied_at)
        if action["action_id"] == "ACT-RESUME-2":
            raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(
        report_apply_module, "_apply_action_in_transaction", fail_after_mutation
    )
    stopped = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert stopped.exploration_allowed is False
    assert stopped.pending_action_ids == ("ACT-RESUME-2", "ACT-RESUME-3")
    assert database.execute("SELECT COUNT(*) FROM publication_event").fetchone()[0] == 1
    assert (
        database.execute(
            "SELECT apply_status FROM human_action WHERE action_id='ACT-RESUME-2'"
        ).fetchone()[0]
        == "pending"
    )
    assert (
        database.execute(
            "SELECT apply_error FROM human_action WHERE action_id='ACT-RESUME-2'"
        ).fetchone()[0]
        == "report_apply_failed"
    )
    assert (
        database.execute(
            "SELECT COUNT(*) FROM reservation WHERE status='active'"
        ).fetchone()[0]
        == 14
    )

    monkeypatch.setattr(report_apply_module, "_apply_action_in_transaction", original)
    resumed = apply_pending_reports(database, run_id="R2", clock=_apply_clock())

    assert resumed.cohort_action_ids == stopped.cohort_action_ids
    assert resumed.exploration_allowed is True
    assert resumed.pending_action_ids == ()
    assert set(resumed.applied_action_ids) == {"ACT-RESUME-2", "ACT-RESUME-3"}
    assert (
        database.execute(
            "SELECT apply_error FROM human_action WHERE action_id='ACT-RESUME-2'"
        ).fetchone()[0]
        is None
    )
    assert database.execute("SELECT COUNT(*) FROM publication_event").fetchone()[0] == 3
    assert (
        database.execute(
            "SELECT COUNT(*) FROM human_action WHERE apply_status='applied'"
        ).fetchone()[0]
        == 3
    )
    assert (
        database.execute(
            "SELECT committed_count FROM run WHERE run_id='R1'"
        ).fetchone()[0]
        == 15
    )
    replayed = apply_pending_reports(database, run_id="R2", clock=_apply_clock())
    assert replayed.cohort_action_ids == resumed.cohort_action_ids
    assert replayed.applied_action_ids == ()
    assert replayed.exploration_allowed is True
    assert database.execute("SELECT COUNT(*) FROM publication_event").fetchone()[0] == 3


def test_large_frozen_cohort_is_rechecked_in_sqlite_safe_batches(
    database: sqlite3.Connection,
) -> None:
    from automation.inago.report_apply import _current_pending_for_cohort

    cohort = tuple(f"REPORT-{number:04}" for number in range(1001))

    assert _current_pending_for_cohort(database, cohort) == cohort
