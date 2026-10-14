"""Night report receipt, revision, and local HTTP API contracts for STEP 05."""

from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

import pytest

from automation.inago.clock import FakeClock
from automation.inago.human_api import create_server
from automation.inago.repository import initialize_database, validate_entity

JST = ZoneInfo("Asia/Tokyo")
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
MAX_BODY_BYTES = 64 * 1024


@dataclass(frozen=True)
class DatabaseFixture:
    path: Path
    connect: Callable[[], sqlite3.Connection]


@pytest.fixture
def seeded_database(tmp_path: Path) -> DatabaseFixture:
    path = tmp_path / "state.sqlite"

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(
            path,
            timeout=5,
            isolation_level=None,
            check_same_thread=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        initialize_database(connection)
        return connection

    connection = connect()
    try:
        _seed_fifteen_drafts(connection)
    finally:
        connection.close()
    return DatabaseFixture(path=path, connect=connect)


def _seed_fifteen_drafts(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO settings_version
            (version, confirmed_at, genres_json, themes_json, pattern_refs_json,
             change_reason, created_at)
        VALUES (?, NULL, '[]', '[]', '["P01","P02","P03","P04","P05"]',
                NULL, '2026-10-12T13:00:00+09:00')
        """,
        (SETTINGS_VERSION,),
    )
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('R1', '2026-10-12', 'daily', 'complete', ?, 'seed-r1',
                '2026-10-12T13:00:00+09:00', '2026-10-12T17:45:00+09:00',
                '2026-10-12T18:00:00+09:00', 9, 15, NULL, 'not_started',
                '2026-10-12T13:20:00+09:00')
        """,
        (SETTINGS_VERSION,),
    )
    for number in range(1, 16):
        suffix = f"A{number:02}"
        offer_key = f"shop_test:item_{suffix}"
        draft_id = f"D-{suffix}"
        timestamp = "2026-10-12T13:10:00+09:00"
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
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO draft
                (draft_id, offer_key, run_id, version, body, generated_at,
                 committed_at, generator_version, pattern_version, status,
                 artifact_ref, pattern_id, evidence_refs_json)
            VALUES (?, ?, 'R1', 1, ?, ?, ?, 'fixture-copy-1', 'confirmed-0.3',
                    'committed', NULL, 'P01', '[]')
            """,
            (
                draft_id,
                offer_key,
                f"匿名fixture本文{number}",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO reservation
                (reservation_id, offer_key, draft_id, status, created_at,
                 released_at, release_reason)
            VALUES (?, ?, ?, 'active', ?, NULL, NULL)
            """,
            (f"RES-{suffix}", offer_key, draft_id, timestamp),
        )


@contextmanager
def _running_server(
    connection_factory: Callable[[], sqlite3.Connection],
    clock: FakeClock,
    *,
    auth_token: str | None = None,
) -> Iterator[Any]:
    server = create_server(
        connection_factory=connection_factory,
        clock=clock,
        auth_token=auth_token,
    )
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        assert not thread.is_alive()


def _request(
    server: Any,
    method: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    path: str = "/api/state",
) -> tuple[int, dict[str, Any] | None, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        if response.getheader("Content-Type", "").startswith("application/json"):
            return response.status, json.loads(raw.decode("utf-8")), raw
        return response.status, None, raw
    finally:
        connection.close()


def _json_request(
    server: Any,
    method: str,
    *,
    payload: dict[str, Any] | None = None,
    csrf_token: str | None = None,
    headers: dict[str, str] | None = None,
    raw_body: bytes | None = None,
    path: str = "/api/reports",
) -> tuple[int, dict[str, Any] | None, bytes]:
    request_headers = {
        "Origin": server.origin,
        "Accept": "application/json",
    }
    if method == "POST":
        request_headers["Content-Type"] = "application/json; charset=utf-8"
        if csrf_token is not None:
            request_headers["X-CSRF-Token"] = csrf_token
    if headers:
        request_headers.update(headers)
    body = raw_body
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    return _request(server, method, body=body, headers=request_headers, path=path)


def _get_state(server: Any) -> tuple[dict[str, Any], str]:
    status, payload, _ = _json_request(server, "GET", path="/api/state")
    assert status == 200
    assert payload is not None
    return payload, str(payload["csrf_token"])


def test_production_style_human_api_requires_private_bearer_token(
    seeded_database: DatabaseFixture,
) -> None:
    token = "private-test-token-" + ("x" * 40)
    clock = FakeClock(datetime(2026, 10, 15, 21, 0, tzinfo=JST))
    with _running_server(seeded_database.connect, clock, auth_token=token) as server:
        health_status, health, _ = _json_request(server, "GET", path="/health")
        assert health_status == 200
        assert health == {"status": "ready"}

        unauthorized, body, _ = _json_request(server, "GET", path="/api/state")
        assert unauthorized == 401
        assert body == {"error": "authentication_required"}

        wrong, _, _ = _json_request(
            server,
            "GET",
            path="/api/state",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert wrong == 401

        accepted, state, _ = _json_request(
            server,
            "GET",
            path="/api/state",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert accepted == 200
        assert state is not None
        assert "csrf_token" in state
        assert token not in json.dumps(state)


def test_human_api_health_probe_reports_database_unavailable_without_details() -> None:
    clock = FakeClock(datetime(2026, 10, 15, 21, 0, tzinfo=JST))

    def unavailable_database():
        raise sqlite3.OperationalError("private database path")

    with _running_server(unavailable_database, clock, auth_token="x" * 40) as server:
        status, payload, _ = _json_request(server, "GET", path="/health")

    assert status == 503
    assert payload == {"status": "unavailable"}


def test_human_api_health_probe_rejects_database_without_dashboard_schema() -> None:
    clock = FakeClock(datetime(2026, 10, 15, 21, 0, tzinfo=JST))

    def uninitialized_database() -> sqlite3.Connection:
        return sqlite3.connect(":memory:", isolation_level=None)

    with _running_server(uninitialized_database, clock, auth_token="x" * 40) as server:
        status, payload, _ = _json_request(server, "GET", path="/health")

    assert status == 503
    assert payload == {"status": "unavailable"}


def _report(
    *,
    number: int = 1,
    action_id: str | None = None,
    action: str = "posted",
    expected_revision: int = 0,
    actual_posted_at: str | None = "2026-10-12T21:00:01+09:00",
    reason: str | None = None,
    correction_ref: str | None = None,
    draft_id: str | None = None,
    draft_version: int = 1,
) -> dict[str, Any]:
    suffix = f"A{number:02}"
    return {
        "action_id": action_id or f"ACT-{suffix}-{action.upper()}",
        "offer_key": f"shop_test:item_{suffix}",
        "draft_id": draft_id or f"D-{suffix}",
        "draft_version": draft_version,
        "expected_receipt_revision": expected_revision,
        "action": action,
        "reason": reason,
        "correction_ref": correction_ref,
        "actual_posted_at": actual_posted_at,
    }


def _count(connection: sqlite3.Connection, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


def test_v1_migration_preserves_pending_report_and_adds_nullable_actual_time(
    tmp_path: Path,
) -> None:
    """A v1 pending row survives the additive migration without invented time."""

    database_path = tmp_path / "legacy-v1.sqlite"
    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    sql_path = (
        Path(__file__).resolve().parents[1]
        / "automation"
        / "inago"
        / "sql"
        / "001_initial.sql"
    )
    connection.executescript(sql_path.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO schema_migration(version, applied_at, description) VALUES (1, ?, ?)",
        ("2026-10-12T12:00:00+09:00", "initial Q1.3 entity contract"),
    )
    connection.execute("PRAGMA user_version = 1")
    connection.execute(
        """
        INSERT INTO settings_version
            (version, confirmed_at, genres_json, themes_json, pattern_refs_json,
             change_reason, created_at)
        VALUES (?, NULL, '[]', '[]', '["P01"]', NULL, ?)
        """,
        (SETTINGS_VERSION, "2026-10-12T12:00:00+09:00"),
    )
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('R-OLD', '2026-10-12', 'daily', 'complete', ?, 'seed-old',
                '2026-10-12T13:00:00+09:00', '2026-10-12T17:45:00+09:00',
                '2026-10-12T18:00:00+09:00', 0, 1, NULL, 'not_started',
                '2026-10-12T13:10:00+09:00')
        """,
        (SETTINGS_VERSION,),
    )
    connection.execute(
        """
        INSERT INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES ('shop_test:item_OLD', 'shop_test', 'item_OLD', 'fixture',
                'https://example.invalid/old', '[]', 'G01-T01', '[]', ?, ?, '[]')
        """,
        ("2026-10-12T13:00:00+09:00", "2026-10-12T13:00:00+09:00"),
    )
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES ('D-OLD', 'shop_test:item_OLD', 'R-OLD', 1, 'fixture body',
                '2026-10-12T13:05:00+09:00', '2026-10-12T13:10:00+09:00',
                'fixture-copy-1', 'confirmed-0.3', 'committed', NULL, 'P01', '[]')
        """
    )
    connection.execute(
        """
        INSERT INTO human_action
            (action_id, action, offer_key, draft_id, draft_version,
             prior_revision, receipt_revision, reason, reported_at,
             old_event_ref, new_body, apply_status, applied_at,
             applied_run_id, apply_error)
        VALUES ('ACT-OLD', 'posted', 'shop_test:item_OLD', 'D-OLD', 1,
                0, 1, NULL, '2026-10-12T21:30:00+09:00', NULL, NULL,
                'pending', NULL, NULL, NULL)
        """
    )

    try:
        assert initialize_database(connection) == 4
        assert connection.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert connection.execute("PRAGMA user_version").fetchone() == (4,)
        action = connection.execute(
            "SELECT action_id, reported_at, actual_posted_at, apply_status "
            "FROM human_action WHERE action_id = 'ACT-OLD'"
        ).fetchone()
        assert tuple(action) == (
            "ACT-OLD",
            "2026-10-12T21:30:00+09:00",
            None,
            "pending",
        )
        assert initialize_database(connection) == 4
        assert _count(connection, "SELECT COUNT(*) FROM schema_migration") == 4
    finally:
        connection.close()


def test_human_action_entity_contract_requires_nullable_actual_posted_at() -> None:
    action = {
        "action_id": "ACT-ENTITY",
        "action": "posted",
        "offer_key": "shop_test:item_A01",
        "draft_id": "D-A01",
        "draft_version": 1,
        "prior_revision": 0,
        "receipt_revision": 1,
        "reason": None,
        "reported_at": "2026-10-12T21:30:00+09:00",
        "actual_posted_at": None,
        "old_event_ref": None,
        "new_body": None,
        "apply_status": "pending",
        "applied_at": None,
        "applied_run_id": None,
        "apply_error": None,
    }
    assert validate_entity("human_action", action)
    action["actual_posted_at"] = "2026-10-12T21:00:00"
    with pytest.raises(ValueError):
        validate_entity("human_action", action)


def test_get_state_is_read_only_and_returns_receipt_revision(
    seeded_database: DatabaseFixture,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        first, csrf_token = _get_state(server)
        second, _ = _get_state(server)
        assert first["csrf_token"] == csrf_token
        assert len(first["drafts"]) == 15
        assert first["receipt_revisions"] == {
            f"shop_test:item_A{i:02}": 0 for i in range(1, 16)
        }
        assert first["pending_reports"] == []
        assert second["pending_reports"] == []

    connection = seeded_database.connect()
    try:
        assert _count(connection, "SELECT COUNT(*) FROM human_action") == 0
        assert (
            _count(
                connection, "SELECT COUNT(*) FROM reservation WHERE status = 'active'"
            )
            == 15
        )
        assert _count(connection, "SELECT COUNT(*) FROM publication_event") == 0
        assert _count(connection, "SELECT COUNT(*) FROM exclusion_key") == 0
        assert (
            connection.execute(
                "SELECT committed_count FROM run WHERE run_id='R1'"
            ).fetchone()[0]
            == 15
        )
    finally:
        connection.close()


def test_server_cannot_bind_to_another_host(seeded_database: DatabaseFixture) -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_server(
            connection_factory=seeded_database.connect,
            bind_host="0.0.0.0",
        )


def test_eight_night_posted_reports_are_pending_only_and_keep_real_timestamps(
    seeded_database: DatabaseFixture,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        for number in range(1, 9):
            payload = _report(
                number=number,
                action_id=f"ACT-A{number:02}-POSTED",
                actual_posted_at=f"2026-10-12T21:00:{number:02}+09:00",
            )
            status, response, _ = _json_request(
                server,
                "POST",
                payload=payload,
                csrf_token=csrf_token,
            )
            assert status == 201
            assert response == {
                "action_id": payload["action_id"],
                "receipt_revision": 1,
                "apply_status": "pending",
                "saved": True,
            }
            clock.advance(1)

        state, _ = _get_state(server)
        assert len(state["pending_reports"]) == 8
        assert all(
            state["receipt_revisions"][f"shop_test:item_A{i:02}"] == 1
            for i in range(1, 9)
        )

    connection = seeded_database.connect()
    try:
        assert (
            _count(
                connection,
                "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'",
            )
            == 8
        )
        assert (
            _count(
                connection,
                "SELECT COUNT(*) FROM human_action WHERE actual_posted_at IS NOT NULL",
            )
            == 8
        )
        assert _count(connection, "SELECT COUNT(*) FROM publication_event") == 0
        assert _count(connection, "SELECT COUNT(*) FROM exclusion_key") == 0
        assert (
            _count(connection, "SELECT COUNT(*) FROM reservation WHERE status='active'")
            == 15
        )
        assert (
            connection.execute(
                "SELECT committed_count FROM run WHERE run_id='R1'"
            ).fetchone()[0]
            == 15
        )
        timestamps = [
            row[0]
            for row in connection.execute(
                "SELECT actual_posted_at FROM human_action ORDER BY action_id"
            )
        ]
        assert timestamps == [f"2026-10-12T21:00:{n:02}+09:00" for n in range(1, 9)]
        reported = [
            row[0]
            for row in connection.execute(
                "SELECT reported_at FROM human_action ORDER BY action_id"
            )
        ]
        assert reported[0] == "2026-10-12T21:30:00+09:00"
        assert reported[-1] == "2026-10-12T21:30:07+09:00"
    finally:
        connection.close()


@pytest.mark.parametrize(
    "receipt_time",
    [
        datetime(2026, 10, 12, 18, 0, 1, tzinfo=JST),
        datetime(2026, 10, 12, 23, 59, 0, tzinfo=JST),
    ],
    ids=["after-18", "23-59"],
)
def test_night_receipts_remain_available_after_18_and_at_2359(
    seeded_database: DatabaseFixture, receipt_time: datetime
) -> None:
    clock = FakeClock(receipt_time)
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        status, payload, _ = _json_request(
            server,
            "POST",
            payload=_report(number=1),
            csrf_token=csrf_token,
        )
        assert status == 201
        assert payload is not None and payload["saved"] is True
    connection = seeded_database.connect()
    try:
        row = connection.execute(
            "SELECT reported_at, apply_status FROM human_action"
        ).fetchone()
        assert tuple(row) == (receipt_time.isoformat(), "pending")
    finally:
        connection.close()


def test_action_id_retry_is_idempotent_and_changed_payload_conflicts(
    seeded_database: DatabaseFixture,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        original = _report(number=1, action_id="ACT-IDEMPOTENT")
        first_status, first_body, first_raw = _json_request(
            server, "POST", payload=original, csrf_token=csrf_token
        )
        clock.advance(30)
        retry_status, retry_body, retry_raw = _json_request(
            server, "POST", payload=original, csrf_token=csrf_token
        )
        assert retry_status == first_status == 201
        assert retry_body == first_body
        assert retry_raw == first_raw

        changed = {**original, "actual_posted_at": "2026-10-12T21:15:00+09:00"}
        conflict_status, conflict, _ = _json_request(
            server, "POST", payload=changed, csrf_token=csrf_token
        )
        assert conflict_status == 409
        assert conflict == {"saved": False, "error": "action_id_conflict"}

    connection = seeded_database.connect()
    try:
        assert _count(connection, "SELECT COUNT(*) FROM human_action") == 1
        assert (
            connection.execute(
                "SELECT actual_posted_at FROM human_action WHERE action_id='ACT-IDEMPOTENT'"
            ).fetchone()[0]
            == original["actual_posted_at"]
        )
    finally:
        connection.close()


def test_old_receipt_revision_returns_409_and_latest_revision(
    seeded_database: DatabaseFixture,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        first = _report(number=1, action_id="ACT-FIRST")
        assert (
            _json_request(server, "POST", payload=first, csrf_token=csrf_token)[0]
            == 201
        )
        stale = _report(number=1, action_id="ACT-STALE", action="skipped")
        status, conflict, _ = _json_request(
            server, "POST", payload=stale, csrf_token=csrf_token
        )
        assert status == 409
        assert conflict == {
            "saved": False,
            "error": "stale_revision",
            "receipt_revision": 1,
            "reload_required": True,
        }

    connection = seeded_database.connect()
    try:
        assert _count(connection, "SELECT COUNT(*) FROM human_action") == 1
        assert _count(connection, "SELECT COUNT(*) FROM publication_event") == 0
        assert _count(connection, "SELECT COUNT(*) FROM exclusion_key") == 0
        assert (
            _count(connection, "SELECT COUNT(*) FROM reservation WHERE status='active'")
            == 15
        )
    finally:
        connection.close()


def test_correction_requires_reason_and_known_same_offer_event(
    seeded_database: DatabaseFixture,
) -> None:
    connection = seeded_database.connect()
    try:
        connection.execute(
            """
            INSERT INTO publication_event
                (event_id, offer_key, draft_id, draft_version, reported_at,
                 actual_posted_at, room_url, evidence_kind, valid, revision)
            VALUES ('EV-A01', 'shop_test:item_A01', 'D-A01', 1,
                    '2026-10-12T20:00:00+09:00',
                    '2026-10-12T19:59:00+09:00', NULL, 'human_statement', 1, 1)
            """
        )
    finally:
        connection.close()

    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        no_reason = _report(
            number=1,
            action_id="ACT-CORR-NO-REASON",
            action="correction",
            correction_ref="EV-A01",
            reason="   ",
            actual_posted_at=None,
        )
        status, rejected, _ = _json_request(
            server, "POST", payload=no_reason, csrf_token=csrf_token
        )
        assert status == 400
        assert rejected == {"saved": False, "error": "invalid_report"}

        wrong_event = _report(
            number=2,
            action_id="ACT-CORR-WRONG-EVENT",
            action="correction",
            correction_ref="EV-A01",
            reason="正しい元イベントを指定します",
            actual_posted_at=None,
        )
        wrong_status, _, _ = _json_request(
            server, "POST", payload=wrong_event, csrf_token=csrf_token
        )
        assert wrong_status == 400

        correction = _report(
            number=1,
            action_id="ACT-CORR-VALID",
            action="correction",
            correction_ref="EV-A01",
            reason="対象イベントを訂正します",
            actual_posted_at="2026-10-12T19:58:00+09:00",
        )
        accepted_status, accepted, _ = _json_request(
            server, "POST", payload=correction, csrf_token=csrf_token
        )
        assert accepted_status == 201
        assert accepted is not None and accepted["apply_status"] == "pending"

    connection = seeded_database.connect()
    try:
        row = connection.execute(
            "SELECT action, old_event_ref, reason, actual_posted_at, apply_status "
            "FROM human_action"
        ).fetchone()
        assert tuple(row) == (
            "correction",
            "EV-A01",
            "対象イベントを訂正します",
            "2026-10-12T19:58:00+09:00",
            "pending",
        )
        assert _count(connection, "SELECT COUNT(*) FROM publication_event") == 1
    finally:
        connection.close()


def test_old_draft_report_preserves_later_active_reservation(
    seeded_database: DatabaseFixture,
) -> None:
    connection = seeded_database.connect()
    try:
        connection.execute(
            """
            UPDATE reservation SET status='released', released_at=?, release_reason='skipped'
            WHERE reservation_id='RES-A01'
            """,
            ("2026-10-12T20:00:00+09:00",),
        )
        connection.execute(
            """
            INSERT INTO run
                (run_id, target_date, mode, status, settings_version, seed,
                 started_at, draft_deadline, end_deadline, attempt_used,
                 committed_count, checkpoint, metrics_status, finished_at)
            VALUES ('R2', '2026-10-13', 'daily', 'complete', ?, 'seed-r2',
                    '2026-10-13T13:00:00+09:00', '2026-10-13T17:45:00+09:00',
                    '2026-10-13T18:00:00+09:00', 9, 1, NULL, 'not_started',
                    '2026-10-13T13:20:00+09:00')
            """,
            (SETTINGS_VERSION,),
        )
        connection.execute(
            """
            INSERT INTO draft
                (draft_id, offer_key, run_id, version, body, generated_at,
                 committed_at, generator_version, pattern_version, status,
                 artifact_ref, pattern_id, evidence_refs_json)
            VALUES ('D-A01-V2', 'shop_test:item_A01', 'R2', 2,
                    '後続fixture本文', '2026-10-13T13:10:00+09:00',
                    '2026-10-13T13:20:00+09:00', 'fixture-copy-1',
                    'confirmed-0.3', 'committed', NULL, 'P01', '[]')
            """
        )
        connection.execute(
            """
            INSERT INTO reservation
                (reservation_id, offer_key, draft_id, status, created_at,
                 released_at, release_reason)
            VALUES ('RES-A01-V2', 'shop_test:item_A01', 'D-A01-V2', 'active',
                    '2026-10-13T13:20:00+09:00', NULL, NULL)
            """
        )
    finally:
        connection.close()

    clock = FakeClock(datetime(2026, 10, 13, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        old_version_report = _report(
            number=1,
            action_id="ACT-OLD-V1",
            draft_id="D-A01",
            draft_version=1,
            actual_posted_at="2026-10-12T22:00:00+09:00",
        )
        status, response, _ = _json_request(
            server,
            "POST",
            payload=old_version_report,
            csrf_token=csrf_token,
        )
        assert status == 201 and response is not None

    connection = seeded_database.connect()
    try:
        assert (
            _count(
                connection,
                "SELECT COUNT(*) FROM human_action WHERE draft_id='D-A01' AND draft_version=1",
            )
            == 1
        )
        assert (
            _count(
                connection,
                "SELECT COUNT(*) FROM reservation WHERE offer_key='shop_test:item_A01' AND status='active'",
            )
            == 1
        )
        assert (
            connection.execute(
                "SELECT status FROM draft WHERE draft_id='D-A01-V2'"
            ).fetchone()[0]
            == "committed"
        )
        assert _count(connection, "SELECT COUNT(*) FROM publication_event") == 0
        assert _count(connection, "SELECT COUNT(*) FROM exclusion_key") == 0
    finally:
        connection.close()


def test_report_rejects_nonexistent_or_mismatched_draft_version(
    seeded_database: DatabaseFixture,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        wrong_draft = _report(
            number=1,
            action_id="ACT-MISMATCH-OFFER",
            draft_id="D-A02",
            draft_version=1,
        )
        wrong_version = _report(
            number=1,
            action_id="ACT-MISMATCH-VERSION",
            draft_id="D-A01",
            draft_version=2,
        )
        padded_id = _report(number=1, action_id=" ACT-PADDED-ID ")
        for payload in (wrong_draft, wrong_version, padded_id):
            status, response, _ = _json_request(
                server, "POST", payload=payload, csrf_token=csrf_token
            )
            assert status == 400
            assert response == {"saved": False, "error": "invalid_report"}

    connection = seeded_database.connect()
    try:
        assert _count(connection, "SELECT COUNT(*) FROM human_action") == 0
    finally:
        connection.close()


def test_invalid_actual_time_edit_action_and_unknown_fields_are_rejected(
    seeded_database: DatabaseFixture,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        base = _report(number=1)
        bad_time = {**base, "actual_posted_at": "2026-10-12T21:00:00"}
        edit = {**base, "action_id": "ACT-EDIT", "action": "edit"}
        unknown = {**base, "unexpected": True}
        missing_nullable_field = dict(base)
        del missing_nullable_field["actual_posted_at"]

        for payload in (bad_time, edit, unknown, missing_nullable_field):
            status, body, _ = _json_request(
                server, "POST", payload=payload, csrf_token=csrf_token
            )
            assert status == 400
            assert body == {"saved": False, "error": "invalid_report"}

    connection = seeded_database.connect()
    try:
        assert _count(connection, "SELECT COUNT(*) FROM human_action") == 0
    finally:
        connection.close()


def test_origin_csrf_content_type_and_size_are_enforced(
    seeded_database: DatabaseFixture,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        payload = _report(number=1)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        bad_origin, origin_error, _ = _json_request(
            server,
            "POST",
            payload=payload,
            csrf_token=csrf_token,
            headers={"Origin": "http://localhost:9000"},
        )
        assert bad_origin == 403
        assert origin_error == {"saved": False, "error": "origin_rejected"}

        bad_csrf, csrf_error, _ = _json_request(
            server,
            "POST",
            payload=payload,
            csrf_token="wrong-token",
        )
        assert bad_csrf == 403
        assert csrf_error == {"saved": False, "error": "csrf_rejected"}

        long_csrf, long_csrf_error, _ = _json_request(
            server,
            "POST",
            payload=payload,
            csrf_token="x" * 257,
        )
        assert long_csrf == 403
        assert long_csrf_error == {"saved": False, "error": "csrf_rejected"}

        bad_host, host_error, _ = _json_request(
            server,
            "POST",
            payload=payload,
            csrf_token=csrf_token,
            headers={"Host": "localhost"},
        )
        assert bad_host == 403
        assert host_error == {"saved": False, "error": "origin_rejected"}

        wrong_type, type_error, _ = _json_request(
            server,
            "POST",
            payload=payload,
            csrf_token=csrf_token,
            headers={"Content-Type": "text/plain"},
        )
        assert wrong_type == 415
        assert type_error == {"saved": False, "error": "content_type_rejected"}

        chunked_status, chunked_error, _ = _json_request(
            server,
            "POST",
            payload=payload,
            csrf_token=csrf_token,
            headers={"Transfer-Encoding": "chunked"},
        )
        assert chunked_status == 400
        assert chunked_error == {
            "saved": False,
            "error": "transfer_encoding_rejected",
        }

        bad_length, bad_length_error, _ = _json_request(
            server,
            "POST",
            # The server closes after rejecting the invalid length; send no body
            # so the client cannot race that close with an undeclared payload.
            raw_body=b"",
            csrf_token=csrf_token,
            headers={"Content-Length": "invalid"},
        )
        assert bad_length == 411
        assert bad_length_error == {
            "saved": False,
            "error": "content_length_required",
        }

        oversized = body + b" " * (MAX_BODY_BYTES + 1 - len(body))
        large_status, large_error, _ = _json_request(
            server,
            "POST",
            csrf_token=csrf_token,
            raw_body=oversized,
        )
        assert large_status == 413
        assert large_error == {"saved": False, "error": "body_too_large"}

    connection = seeded_database.connect()
    try:
        assert _count(connection, "SELECT COUNT(*) FROM human_action") == 0
    finally:
        connection.close()


def test_65536_byte_valid_report_is_accepted(seeded_database: DatabaseFixture) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        payload = _report(number=1, action="skipped", actual_posted_at=None)
        payload["reason"] = "x"
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        payload["reason"] = "x" * (MAX_BODY_BYTES - len(encoded) + 1)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        assert len(body) == MAX_BODY_BYTES
        status, response, _ = _json_request(
            server, "POST", csrf_token=csrf_token, raw_body=body
        )
        assert status == 201 and response is not None and response["saved"] is True


def test_malformed_json_and_non_object_body_are_rejected_before_database_access(
    seeded_database: DatabaseFixture,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        bodies = (
            b"{not-json",
            b"[]",
            b'{"action_id":"FIRST","action_id":"SECOND"}',
            b'{"value":NaN}',
            b"\xff",
        )
        for body in bodies:
            status, response, _ = _json_request(
                server,
                "POST",
                csrf_token=csrf_token,
                raw_body=body,
            )
            assert status == 400
            assert response == {"saved": False, "error": "invalid_report"}

        wrong_route, wrong_route_response, _ = _json_request(
            server,
            "POST",
            payload=_report(number=1),
            csrf_token=csrf_token,
            path="/api/other",
        )
        assert wrong_route == 404
        assert wrong_route_response == {"saved": False, "error": "not_found"}

    connection = seeded_database.connect()
    try:
        assert _count(connection, "SELECT COUNT(*) FROM human_action") == 0
    finally:
        connection.close()


def test_get_requires_exact_origin_and_reports_storage_failure(
    seeded_database: DatabaseFixture,
) -> None:
    fail = False

    def connection_factory() -> sqlite3.Connection:
        if fail:
            raise sqlite3.OperationalError("fixture database unavailable")
        return seeded_database.connect()

    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(connection_factory, clock) as server:
        wrong_origin, origin_error, _ = _json_request(
            server,
            "GET",
            headers={"Origin": "http://127.0.0.1.evil.invalid"},
            path="/api/state",
        )
        assert wrong_origin == 403
        assert origin_error == {"error": "origin_rejected"}

        missing_path, missing_path_body, _ = _json_request(
            server,
            "GET",
            path="/api/unknown",
        )
        assert missing_path == 404
        assert missing_path_body == {"error": "not_found"}

        fail = True
        unavailable, unavailable_body, _ = _json_request(
            server,
            "GET",
            path="/api/state",
        )
        assert unavailable == 503
        assert unavailable_body == {"error": "storage_unavailable"}


def test_database_failure_returns_5xx_saved_false_without_success_receipt(
    seeded_database: DatabaseFixture,
) -> None:
    fail = False

    def failing_connection_factory() -> sqlite3.Connection:
        if fail:
            raise sqlite3.OperationalError("fixture database unavailable")
        return seeded_database.connect()

    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(failing_connection_factory, clock) as server:
        _, csrf_token = _get_state(server)
        fail = True
        status, response, _ = _json_request(
            server,
            "POST",
            payload=_report(number=1),
            csrf_token=csrf_token,
        )
        assert status == 503
        assert response == {"saved": False, "error": "storage_unavailable"}

    connection = seeded_database.connect()
    try:
        assert _count(connection, "SELECT COUNT(*) FROM human_action") == 0
    finally:
        connection.close()


def test_failed_insert_rolls_back_and_does_not_claim_success(
    seeded_database: DatabaseFixture,
) -> None:
    connection = seeded_database.connect()
    try:
        connection.execute(
            """
            CREATE TRIGGER reject_report BEFORE INSERT ON human_action
            BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END
            """
        )
    finally:
        connection.close()

    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        status, response, _ = _json_request(
            server,
            "POST",
            payload=_report(number=1),
            csrf_token=csrf_token,
        )
        assert status == 503
        assert response == {"saved": False, "error": "storage_unavailable"}

    connection = seeded_database.connect()
    try:
        assert _count(connection, "SELECT COUNT(*) FROM human_action") == 0
        assert _count(connection, "SELECT COUNT(*) FROM publication_event") == 0
        assert _count(connection, "SELECT COUNT(*) FROM exclusion_key") == 0
        assert (
            _count(connection, "SELECT COUNT(*) FROM reservation WHERE status='active'")
            == 15
        )
    finally:
        connection.close()


def test_two_concurrent_reports_with_same_revision_have_one_winner(
    seeded_database: DatabaseFixture,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        barrier = threading.Barrier(2)

        def submit(payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
            barrier.wait(timeout=5)
            status, response, _ = _json_request(
                server, "POST", payload=payload, csrf_token=csrf_token
            )
            return status, response

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    submit,
                    _report(number=1, action_id="ACT-CONCURRENT-POSTED"),
                ),
                pool.submit(
                    submit,
                    _report(
                        number=1, action_id="ACT-CONCURRENT-SKIPPED", action="skipped"
                    ),
                ),
            ]
            results = [future.result(timeout=10) for future in futures]

        assert sorted(status for status, _ in results) == [201, 409]
        conflict = next(body for status, body in results if status == 409)
        assert conflict == {
            "saved": False,
            "error": "stale_revision",
            "receipt_revision": 1,
            "reload_required": True,
        }

    connection = seeded_database.connect()
    try:
        assert _count(connection, "SELECT COUNT(*) FROM human_action") == 1
        assert _count(connection, "SELECT COUNT(*) FROM publication_event") == 0
        assert _count(connection, "SELECT COUNT(*) FROM exclusion_key") == 0
        assert (
            _count(connection, "SELECT COUNT(*) FROM reservation WHERE status='active'")
            == 15
        )
        assert (
            connection.execute(
                "SELECT committed_count FROM run WHERE run_id='R1'"
            ).fetchone()[0]
            == 15
        )
    finally:
        connection.close()


def test_untrusted_reason_is_saved_as_plain_data(
    seeded_database: DatabaseFixture,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    text = "<script>alert('not executed')</script>"
    with _running_server(seeded_database.connect, clock) as server:
        _, csrf_token = _get_state(server)
        payload = _report(
            number=1, action="skipped", actual_posted_at=None, reason=text
        )
        status, response, _ = _json_request(
            server, "POST", payload=payload, csrf_token=csrf_token
        )
        assert status == 201 and response is not None

    connection = seeded_database.connect()
    try:
        assert (
            connection.execute(
                "SELECT reason FROM human_action WHERE action_id=?",
                (payload["action_id"],),
            ).fetchone()[0]
            == text
        )
        assert _count(connection, "SELECT COUNT(*) FROM publication_event") == 0
    finally:
        connection.close()
