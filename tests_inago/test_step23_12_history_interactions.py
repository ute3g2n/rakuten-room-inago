"""Targeted contracts for interactive daily history exports."""

from __future__ import annotations

import json
import http.client
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any
from zoneinfo import ZoneInfo

from automation.inago.clock import FakeClock
from automation.inago.dashboard_history import (
    export_dashboard_history_html,
    render_dashboard_history_html,
)
from automation.inago.human_api import create_server
from automation.inago.repository import (
    apply_human_action_in_transaction,
    initialize_database,
)
from tests_inago.test_step05 import _seed_fifteen_drafts


_SETTINGS_VERSION = "fixture-step23-12-url-mapping"
_TIMESTAMP = "2026-10-15T13:00:00+09:00"
_API_TOKEN = "isolated-file-origin-token-" + ("x" * 40)
_CLOCK = FakeClock(datetime(2026, 10, 15, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo")))


@contextmanager
def _running_file_origin_api(connection_factory):
    server = create_server(
        connection_factory=connection_factory,
        clock=_CLOCK,
        bind_host="127.0.0.1",
        port=0,
        auth_token=_API_TOKEN,
        allow_file_origin=True,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _http_request(
    server,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    request_headers = {"Origin": "null", **(headers or {})}
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(
            method,
            path,
            body=(json.dumps(payload).encode("utf-8") if payload is not None else None),
            headers=request_headers,
        )
        response = connection.getresponse()
        raw = response.read()
        decoded = json.loads(raw.decode("utf-8")) if raw else {}
        return (
            response.status,
            {key.lower(): value for key, value in response.getheaders()},
            decoded,
        )
    finally:
        connection.close()


def _preflight(server, path: str, method: str, requested_headers: str):
    return _http_request(
        server,
        "OPTIONS",
        path,
        headers={
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": requested_headers,
        },
    )


def _report_payload(
    *,
    action_id: str,
    action: str,
    expected_revision: int,
    offer_key: str = "shop_test:item_A01",
    draft_id: str = "D-A01",
    correction_ref=None,
    reason=None,
) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "offer_key": offer_key,
        "draft_id": draft_id,
        "draft_version": 1,
        "expected_receipt_revision": expected_revision,
        "action": action,
        "reason": reason,
        "correction_ref": correction_ref,
        "actual_posted_at": None,
    }


def _insert_run(
    connection: sqlite3.Connection, target_date: str, committed_count: int
) -> str:
    run_id = f"RUN-{target_date}"
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, ?, 'daily', 'complete', ?, ?, ?, ?, ?, 0, ?, NULL,
                'not_started', ?)
        """,
        (
            run_id,
            target_date,
            _SETTINGS_VERSION,
            f"seed-{target_date}",
            f"{target_date}T13:00:00+09:00",
            f"{target_date}T17:45:00+09:00",
            f"{target_date}T18:00:00+09:00",
            committed_count,
            f"{target_date}T18:00:00+09:00",
        ),
    )
    return run_id


def _insert_offer_and_draft(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    target_date: str,
    offer_key: str,
    url: str,
    draft_id: str,
) -> None:
    shop_id, item_id = offer_key.split(":", maxsplit=1)
    connection.execute(
        """
        INSERT INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES (?, ?, ?, ?, ?, '[]', 'G01-T01', '[]', ?, ?, '[]')
        """,
        (
            offer_key,
            shop_id,
            item_id,
            f"fixture {offer_key}",
            url,
            _TIMESTAMP,
            _TIMESTAMP,
        ),
    )
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES (?, ?, ?, 1, ?, ?, ?, 'fixture-copy', 'fixture-pattern',
                'committed', NULL, 'P01', '[]')
        """,
        (
            draft_id,
            offer_key,
            run_id,
            f"body for {draft_id}",
            _TIMESTAMP,
            _TIMESTAMP,
        ),
    )


def _payload_from_html(path: Path) -> dict[str, Any]:
    html = path.read_text(encoding="utf-8")
    match = re.search(
        r'<script id="dashboard-history-data" type="application/json">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def _api_database(path: Path, *, seed_correction: bool = False):
    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        initialize_database(connection)
        return connection

    connection = connect()
    try:
        _seed_fifteen_drafts(connection)
        if seed_correction:
            connection.execute(
                """
                INSERT INTO publication_event
                    (event_id, offer_key, draft_id, draft_version, reported_at,
                     actual_posted_at, room_url, evidence_kind, valid, revision)
                VALUES ('EVT-A03', 'shop_test:item_A03', 'D-A03', 1, ?, ?, NULL,
                        'human_check', 1, 1)
                """,
                (
                    "2026-10-12T21:00:00+09:00",
                    "2026-10-12T20:50:00+09:00",
                ),
            )
    finally:
        connection.close()
    return connect


def test_history_link_uses_matching_offer_url(tmp_path: Path) -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    initialize_database(connection)
    connection.execute(
        """
        INSERT INTO settings_version
            (version, confirmed_at, genres_json, themes_json, pattern_refs_json,
             change_reason, created_at)
        VALUES (?, NULL, '[]', '[]', '[\"P01\",\"P02\",\"P03\",\"P04\",\"P05\"]',
                NULL, ?)
        """,
        (_SETTINGS_VERSION, _TIMESTAMP),
    )

    first_date = "2026-10-15"
    second_date = "2026-10-14"
    first_run = _insert_run(connection, first_date, committed_count=4)
    second_run = _insert_run(connection, second_date, committed_count=2)
    expected_urls = {
        "D-FIXTURE-05": "https://item.rakuten.co.jp/step23shop01/item05",
        "D-OTHER-02": "https://item.rakuten.co.jp/step23shop02/item02/",
        "D-HTTP": "http://item.rakuten.co.jp/step23shop03/item03",
        "D-PRIVATE": "https://127.0.0.1/item04",
        "D-CREDENTIALS": "https://user:secret@item.rakuten.co.jp/shop/item06",
        "D-BAD-SCHEME": "javascript:alert(1)",
    }
    for index, (draft_id, url) in enumerate(expected_urls.items()):
        run_id = first_run if index < 4 else second_run
        target_date = first_date if index < 4 else second_date
        offer_key = f"shop{index:02d}:item{index:02d}"
        _insert_offer_and_draft(
            connection,
            run_id=run_id,
            target_date=target_date,
            offer_key=offer_key,
            url=url,
            draft_id=draft_id,
        )

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    output_path = runtime_root / "posting-history.html"
    try:
        export_dashboard_history_html(
            connection, output_path=output_path, runtime_root=runtime_root
        )
        payload = _payload_from_html(output_path)
    finally:
        connection.close()

    by_draft_id = {
        item["draft_id"]: item for day in payload["days"] for item in day["items"]
    }
    assert set(by_draft_id) == set(expected_urls)
    assert by_draft_id["D-FIXTURE-05"]["product_url"] == expected_urls["D-FIXTURE-05"]
    assert by_draft_id["D-OTHER-02"]["product_url"] == expected_urls["D-OTHER-02"]
    assert by_draft_id["D-FIXTURE-05"]["target_date"] == first_date
    assert by_draft_id["D-OTHER-02"]["target_date"] == first_date
    for draft_id in ("D-HTTP", "D-PRIVATE", "D-CREDENTIALS", "D-BAD-SCHEME"):
        assert by_draft_id[draft_id]["product_url"] is None

    missing_url_html = render_dashboard_history_html(
        {
            "days": [
                {
                    "target_date": first_date,
                    "run_status": "complete",
                    "committed_count": 1,
                    "items": [
                        {
                            "draft_id": "D-MISSING-URL",
                            "draft_version": 1,
                            "product_name": "fixture without URL",
                            "product_url": None,
                            "body": "fixture body",
                            "status": "committed",
                            "target_date": first_date,
                        }
                    ],
                }
            ]
        }
    )
    missing_url_match = re.search(
        r'<script id="dashboard-history-data" type="application/json">(.*?)</script>',
        missing_url_html,
        flags=re.DOTALL,
    )
    assert missing_url_match is not None
    missing_url_payload = json.loads(missing_url_match.group(1))
    assert missing_url_payload["days"][0]["items"][0]["product_url"] is None


def test_file_html_points_to_live_api_and_has_offline_outbox() -> None:
    html = render_dashboard_history_html(
        {"days": []}, api_origin="http://127.0.0.1:8765"
    )
    assert 'data-api-origin="http://127.0.0.1:8765"' in html
    assert "data-report-posted-draft-id" in html
    assert "/api/history-state" in html
    assert "/api/reports" in html
    assert "indexedDB" in html
    assert "browser-outbox" in html
    assert "showSaveFilePicker" in html
    assert 'credentials: HTTP_HISTORY ? "same-origin" : "omit"' in html
    assert "localStorage" not in html


def test_file_html_aggregates_posted_checks_and_replays_applied_outbox() -> None:
    html = render_dashboard_history_html(
        {"days": []}, api_origin="http://127.0.0.1:8765"
    )
    assert 'id="posted-count"' in html
    assert 'querySelectorAll(".posted-checkbox:checked")' in html
    assert "updatePostedCount();" in html
    assert '["pending", "applied"].includes(receipt.apply_status)' in html


def test_history_html_displays_separate_day_scoped_report_and_outbox_counts() -> None:
    html = render_dashboard_history_html(
        {
            "days": [
                {
                    "target_date": "2026-10-12",
                    "run_status": "complete",
                    "committed_count": 1,
                    "items": [],
                }
            ],
            "report_counts_by_date": [
                {
                    "target_date": "2026-10-12",
                    "action_counts": {
                        "posted": {"pending": 1, "applied": 3, "rejected": 0},
                        "skipped": {"pending": 1, "applied": 0, "rejected": 0},
                        "correction": {"pending": 0, "applied": 0, "rejected": 1},
                        "edit": {"pending": 0, "applied": 0, "rejected": 0},
                    },
                    "pending_report_count": 2,
                    "applied_report_count": 3,
                    "rejected_report_count": 1,
                }
            ],
        },
        api_origin="http://127.0.0.1:8765",
    )
    assert 'id="sqlite-pending-count"' in html
    assert 'id="sqlite-applied-count"' in html
    assert 'id="sqlite-rejected-count"' in html
    assert 'id="browser-outbox-day-count"' in html
    assert 'id="report-action-counts"' in html
    assert 'id="report-count-posted-pending"' in html
    assert 'id="report-count-correction-rejected"' in html
    assert "function updateDayReportCounts(day)" in html
    assert "entry.report.offer_key === item.offer_key" in html
    assert "reportCounts.action_counts?.[action]?.[status]" in html


def test_history_report_counts_are_day_action_status_scoped_and_not_recent_limited(
    tmp_path: Path,
) -> None:
    connect = _api_database(tmp_path / "state.sqlite")
    connection = connect()
    try:
        next_run = "RUN-2026-10-13"
        settings_version = connection.execute(
            "SELECT settings_version FROM run WHERE run_id='R1'"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO run
                (run_id, target_date, mode, status, settings_version, seed,
                 started_at, draft_deadline, end_deadline, attempt_used,
                 committed_count, checkpoint, metrics_status, finished_at)
            VALUES (?, '2026-10-13', 'daily', 'complete', ?,
                    'seed-2026-10-13', '2026-10-13T13:00:00+09:00',
                    '2026-10-13T17:45:00+09:00', '2026-10-13T18:00:00+09:00',
                    0, 0, NULL, 'not_started', '2026-10-13T18:00:00+09:00')
            """,
            (next_run, settings_version),
        )
        _insert_offer_and_draft(
            connection,
            run_id=next_run,
            target_date="2026-10-13",
            offer_key="shop_test:aggregate_B01",
            url="https://example.invalid/aggregate_B01",
            draft_id="D-AGG-B01",
        )
        for revision in range(1, 41):
            connection.execute(
                """
                INSERT INTO human_action
                    (action_id, action, offer_key, draft_id, draft_version,
                     prior_revision, receipt_revision, reason, reported_at,
                     actual_posted_at, old_event_ref, correction_action_ref,
                     new_body, apply_status, applied_at, applied_run_id, apply_error)
                VALUES (?, 'posted', 'shop_test:item_A01', 'D-A01', 1,
                        ?, ?, NULL, '2026-10-12T21:00:00+09:00', NULL,
                        NULL, NULL, NULL, 'applied',
                        '2026-10-13T13:00:00+09:00', ?, NULL)
                """,
                (
                    f"ACT-COUNT-APPLIED-{revision:02d}",
                    revision - 1,
                    revision,
                    next_run,
                ),
            )
        connection.execute(
            """
            INSERT INTO human_action
                (action_id, action, offer_key, draft_id, draft_version,
                 prior_revision, receipt_revision, reason, reported_at,
                 actual_posted_at, old_event_ref, correction_action_ref,
                 new_body, apply_status, applied_at, applied_run_id, apply_error)
            VALUES ('ACT-COUNT-PENDING-SKIP', 'skipped', 'shop_test:item_A01',
                    'D-A01', 1, 40, 41, NULL, '2026-10-12T21:01:00+09:00',
                    NULL, NULL, NULL, NULL, 'pending', NULL, NULL, NULL)
            """
        )
        connection.execute(
            """
            INSERT INTO human_action
                (action_id, action, offer_key, draft_id, draft_version,
                 prior_revision, receipt_revision, reason, reported_at,
                 actual_posted_at, old_event_ref, correction_action_ref,
                 new_body, apply_status, applied_at, applied_run_id, apply_error)
            VALUES ('ACT-COUNT-REJECTED-CORRECTION', 'correction',
                    'shop_test:item_A01', 'D-A01', 1, 41, 42,
                    'fixture correction', '2026-10-12T21:02:00+09:00',
                    NULL, NULL, NULL, NULL, 'rejected', NULL, NULL,
                    'fixture rejection')
            """
        )
        connection.execute(
            """
            INSERT INTO human_action
                (action_id, action, offer_key, draft_id, draft_version,
                 prior_revision, receipt_revision, reason, reported_at,
                 actual_posted_at, old_event_ref, correction_action_ref,
                 new_body, apply_status, applied_at, applied_run_id, apply_error)
            VALUES ('ACT-COUNT-NEXT-DAY', 'posted', 'shop_test:aggregate_B01',
                    'D-AGG-B01', 1, 0, 1, NULL, '2026-10-13T21:00:00+09:00',
                    NULL, NULL, NULL, NULL, 'pending', NULL, NULL, NULL)
            """
        )
        connection.commit()
    finally:
        connection.close()

    with _running_file_origin_api(connect) as server:
        status, _, state = _http_request(
            server,
            "GET",
            "/api/history-state",
            headers={"Authorization": f"Bearer {_API_TOKEN}"},
        )
    assert status == 200
    counts = {row["target_date"]: row for row in state["report_counts_by_date"]}
    assert counts["2026-10-12"]["pending_report_count"] == 1
    assert counts["2026-10-12"]["applied_report_count"] == 40
    assert counts["2026-10-12"]["rejected_report_count"] == 1
    assert counts["2026-10-12"]["action_counts"] == {
        "posted": {"pending": 0, "applied": 40, "rejected": 0},
        "skipped": {"pending": 1, "applied": 0, "rejected": 0},
        "correction": {"pending": 0, "applied": 0, "rejected": 1},
        "edit": {"pending": 0, "applied": 0, "rejected": 0},
    }
    assert counts["2026-10-13"]["pending_report_count"] == 1
    assert counts["2026-10-13"]["action_counts"]["posted"]["pending"] == 1
    assert len(state["recent_reports"]) <= 40


def test_file_origin_preflight_is_narrow_and_requires_bearer(tmp_path: Path) -> None:
    connect = _api_database(tmp_path / "state.sqlite")
    with _running_file_origin_api(connect) as server:
        status, headers, _ = _preflight(
            server, "/api/history-state", "GET", "authorization, accept"
        )
        assert status == 204
        assert headers["access-control-allow-origin"] == "null"
        assert "GET" in headers["access-control-allow-methods"]
        assert "*" not in headers["access-control-allow-headers"]

        status, headers, _ = _preflight(
            server,
            "/api/reports",
            "POST",
            "authorization, content-type, x-csrf-token, accept",
        )
        assert status == 204
        assert headers["access-control-allow-origin"] == "null"
        assert "POST" in headers["access-control-allow-methods"]

        status, headers, _ = _preflight(server, "/api/state", "GET", "authorization")
        assert status == 403
        assert "access-control-allow-origin" not in headers

        status, headers, body = _http_request(server, "GET", "/api/history-state")
        assert status == 401
        assert headers["access-control-allow-origin"] == "null"
        assert body["error"] == "authentication_required"


def test_file_origin_history_state_returns_csrf_and_db_receipts(tmp_path: Path) -> None:
    connect = _api_database(tmp_path / "state.sqlite")
    with _running_file_origin_api(connect) as server:
        status, _, state = _http_request(
            server,
            "GET",
            "/api/history-state",
            headers={"Authorization": f"Bearer {_API_TOKEN}"},
        )
        assert status == 200
        assert isinstance(state["csrf_token"], str) and state["csrf_token"]
        assert state["receipt_revisions"]["shop_test:item_A01"] == 0
        item = next(
            item
            for day in state["days"]
            for item in day["items"]
            if item["draft_id"] == "D-A01"
        )
        assert item["offer_key"] == "shop_test:item_A01"
        assert item["events"] == []
        assert _API_TOKEN not in json.dumps(state)


def test_history_posted_checkbox_submits_and_waits_for_receipt(tmp_path: Path) -> None:
    connect = _api_database(tmp_path / "state.sqlite")
    with _running_file_origin_api(connect) as server:
        status, _, state = _http_request(
            server,
            "GET",
            "/api/history-state",
            headers={"Authorization": f"Bearer {_API_TOKEN}"},
        )
        assert status == 200
        status, headers, receipt = _http_request(
            server,
            "POST",
            "/api/reports",
            headers={
                "Authorization": f"Bearer {_API_TOKEN}",
                "X-CSRF-Token": state["csrf_token"],
            },
            payload=_report_payload(
                action_id="ACT-FILE-POSTED",
                action="posted",
                expected_revision=0,
            ),
        )
        assert status == 201
        assert headers["access-control-allow-origin"] == "null"
        assert receipt["saved"] is True
        assert receipt["apply_status"] == "pending"
        connection = connect()
        try:
            reports = connection.execute(
                "SELECT action, apply_status FROM human_action "
                "WHERE offer_key='shop_test:item_A01'"
            ).fetchall()
            assert [(row["action"], row["apply_status"]) for row in reports] == [
                ("posted", "pending")
            ]
            assert (
                connection.execute("SELECT COUNT(*) FROM publication_event").fetchone()[
                    0
                ]
                == 0
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM reservation WHERE status='active'"
                ).fetchone()[0]
                == 15
            )
        finally:
            connection.close()


def test_disposable_history_fixture_posts_and_corrects_through_sqlite(
    tmp_path: Path,
) -> None:
    """Exercise the 15-draft HTML and immediate report path in a disposable DB."""

    database_path = tmp_path / "state.sqlite"
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    html_path = runtime_root / "posting-history.html"
    connect = _api_database(database_path)

    try:
        with _running_file_origin_api(connect) as server:
            connection = connect()
            try:
                result = export_dashboard_history_html(
                    connection,
                    output_path=html_path,
                    runtime_root=runtime_root,
                    api_origin=server.origin,
                )
            finally:
                connection.close()

            assert result.run_count == 1
            assert result.draft_count == 15
            html = html_path.read_text(encoding="utf-8")
            payload = _payload_from_html(html_path)
            assert [day["target_date"] for day in payload["days"]] == ["2026-10-12"]
            assert len(payload["days"][0]["items"]) == 15
            assert f'data-api-origin="{server.origin}"' in html
            assert 'id="date-navigation"' in html
            assert 'className = "posted-checkbox"' in html
            assert 'posted.addEventListener("change"' in html
            assert 'apiFetch("/api/reports"' in html
            assert "チェックを外すと訂正を送信します（理由入力不要）" in html

            status, _, state = _http_request(
                server,
                "GET",
                "/api/history-state",
                headers={"Authorization": f"Bearer {_API_TOKEN}"},
            )
            assert status == 200
            assert state["receipt_revisions"]["shop_test:item_A01"] == 0

            headers = {
                "Authorization": f"Bearer {_API_TOKEN}",
                "X-CSRF-Token": state["csrf_token"],
            }
            status, _, posted = _http_request(
                server,
                "POST",
                "/api/reports",
                headers=headers,
                payload=_report_payload(
                    action_id="ACT-DISPOSABLE-POSTED",
                    action="posted",
                    expected_revision=0,
                ),
            )
            assert status == 201
            assert posted["saved"] is True
            assert posted["apply_status"] == "pending"

            status, _, state = _http_request(
                server,
                "GET",
                "/api/history-state",
                headers={"Authorization": f"Bearer {_API_TOKEN}"},
            )
            assert status == 200
            day_counts = next(
                row
                for row in state["report_counts_by_date"]
                if row["target_date"] == "2026-10-12"
            )
            assert day_counts["pending_report_count"] == 1
            assert day_counts["action_counts"]["posted"]["pending"] == 1

            status, _, correction = _http_request(
                server,
                "POST",
                "/api/reports",
                headers=headers,
                payload=_report_payload(
                    action_id="ACT-DISPOSABLE-CORRECTION",
                    action="correction",
                    expected_revision=1,
                    correction_ref=posted["action_id"],
                    reason="誤って投稿済みにチェックしたため訂正",
                ),
            )
            assert status == 201
            assert correction["saved"] is True
            assert correction["apply_status"] == "pending"

            connection = connect()
            try:
                rows = connection.execute(
                    "SELECT action, reason, correction_action_ref, apply_status "
                    "FROM human_action WHERE offer_key='shop_test:item_A01' "
                    "ORDER BY receipt_revision"
                ).fetchall()
                assert [row["action"] for row in rows] == [
                    "posted",
                    "correction",
                ]
                assert rows[1]["reason"] == "誤って投稿済みにチェックしたため訂正"
                assert rows[1]["correction_action_ref"] == "ACT-DISPOSABLE-POSTED"
                assert [row["apply_status"] for row in rows] == [
                    "pending",
                    "pending",
                ]
            finally:
                connection.close()

            status, _, state = _http_request(
                server,
                "GET",
                "/api/history-state",
                headers={"Authorization": f"Bearer {_API_TOKEN}"},
            )
            assert status == 200
            day_counts = next(
                row
                for row in state["report_counts_by_date"]
                if row["target_date"] == "2026-10-12"
            )
            assert day_counts["pending_report_count"] == 2
            assert day_counts["action_counts"]["posted"]["pending"] == 1
            assert day_counts["action_counts"]["correction"]["pending"] == 1
    finally:
        html_path.unlink(missing_ok=True)
        database_path.unlink(missing_ok=True)


def test_history_api_accepts_system_reason_for_checkbox_off_correction(
    tmp_path: Path,
) -> None:
    connect = _api_database(tmp_path / "state.sqlite")
    reason = "投稿済みチェック解除（本人操作）"
    with _running_file_origin_api(connect) as server:
        status, _, state = _http_request(
            server,
            "GET",
            "/api/history-state",
            headers={"Authorization": f"Bearer {_API_TOKEN}"},
        )
        assert status == 200
        headers = {
            "Authorization": f"Bearer {_API_TOKEN}",
            "X-CSRF-Token": state["csrf_token"],
        }
        status, _, posted = _http_request(
            server,
            "POST",
            "/api/reports",
            headers=headers,
            payload=_report_payload(
                action_id="ACT-AUTO-REASON-POSTED",
                action="posted",
                expected_revision=0,
            ),
        )
        assert status == 201
        status, _, correction = _http_request(
            server,
            "POST",
            "/api/reports",
            headers=headers,
            payload=_report_payload(
                action_id="ACT-AUTO-REASON-CORRECTION",
                action="correction",
                expected_revision=1,
                correction_ref=posted["action_id"],
                reason=reason,
            ),
        )
        assert status == 201
        assert correction["saved"] is True
        assert correction["apply_status"] == "pending"

    connection = connect()
    try:
        row = connection.execute(
            "SELECT reason, correction_action_ref FROM human_action "
            "WHERE action_id='ACT-AUTO-REASON-CORRECTION'"
        ).fetchone()
        assert row["reason"] == reason
        assert row["correction_action_ref"] == "ACT-AUTO-REASON-POSTED"
    finally:
        connection.close()


def test_history_skip_and_correction_are_distinct_actions(tmp_path: Path) -> None:
    connect = _api_database(tmp_path / "state.sqlite", seed_correction=True)
    with _running_file_origin_api(connect) as server:
        status, _, state = _http_request(
            server,
            "GET",
            "/api/history-state",
            headers={"Authorization": f"Bearer {_API_TOKEN}"},
        )
        assert status == 200
        for payload in (
            _report_payload(
                action_id="ACT-FILE-SKIPPED",
                action="skipped",
                expected_revision=0,
                offer_key="shop_test:item_A02",
                draft_id="D-A02",
            ),
            _report_payload(
                action_id="ACT-FILE-CORRECTION",
                action="correction",
                expected_revision=0,
                offer_key="shop_test:item_A03",
                draft_id="D-A03",
                correction_ref="EVT-A03",
                reason="fixture correction reason",
            ),
        ):
            status, _, receipt = _http_request(
                server,
                "POST",
                "/api/reports",
                headers={
                    "Authorization": f"Bearer {_API_TOKEN}",
                    "X-CSRF-Token": state["csrf_token"],
                },
                payload=payload,
            )
            assert status == 201
            assert receipt["apply_status"] == "pending"
        connection = connect()
        try:
            rows = connection.execute(
                """
                SELECT action, offer_key, reason, old_event_ref, apply_status
                  FROM human_action ORDER BY offer_key
                """
            ).fetchall()
            assert [
                (row["action"], row["offer_key"], row["apply_status"]) for row in rows
            ] == [
                ("skipped", "shop_test:item_A02", "pending"),
                ("correction", "shop_test:item_A03", "pending"),
            ]
            correction = rows[1]
            assert correction["reason"] == "fixture correction reason"
            assert correction["old_event_ref"] == "EVT-A03"
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM publication_event "
                    "WHERE event_id='EVT-A03' AND valid=1"
                ).fetchone()[0]
                == 1
            )
        finally:
            connection.close()


def test_posted_report_can_be_corrected_before_next_day_apply(tmp_path: Path) -> None:
    connect = _api_database(tmp_path / "state.sqlite")
    with _running_file_origin_api(connect) as server:
        status, _, state = _http_request(
            server,
            "GET",
            "/api/history-state",
            headers={"Authorization": f"Bearer {_API_TOKEN}"},
        )
        assert status == 200
        common_headers = {
            "Authorization": f"Bearer {_API_TOKEN}",
            "X-CSRF-Token": state["csrf_token"],
        }
        status, _, posted = _http_request(
            server,
            "POST",
            "/api/reports",
            headers=common_headers,
            payload=_report_payload(
                action_id="ACT-FILE-PENDING-POSTED",
                action="posted",
                expected_revision=0,
            ),
        )
        assert status == 201
        correction = _report_payload(
            action_id="ACT-FILE-PENDING-CORRECTION",
            action="correction",
            expected_revision=1,
            correction_ref=posted["action_id"],
            reason="誤って投稿済みにチェックしたため訂正",
        )
        status, _, receipt = _http_request(
            server,
            "POST",
            "/api/reports",
            headers=common_headers,
            payload=correction,
        )
        assert status == 201
        assert receipt["apply_status"] == "pending"
        connection = connect()
        try:
            rows = connection.execute(
                "SELECT action, old_event_ref, correction_action_ref, apply_status "
                "FROM human_action "
                "WHERE offer_key='shop_test:item_A01' ORDER BY receipt_revision"
            ).fetchall()
            assert [row["action"] for row in rows] == ["posted", "correction"]
            assert rows[1]["old_event_ref"] is None
            assert rows[1]["correction_action_ref"] == "ACT-FILE-PENDING-POSTED"
            assert [row["apply_status"] for row in rows] == ["pending", "pending"]
            assert (
                connection.execute("SELECT COUNT(*) FROM publication_event").fetchone()[
                    0
                ]
                == 0
            )
            next_run = "RUN-NEXT-DAY"
            settings_version = connection.execute(
                "SELECT settings_version FROM run WHERE run_id='R1'"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO run
                    (run_id, target_date, mode, status, settings_version, seed,
                     started_at, draft_deadline, end_deadline, attempt_used,
                     committed_count, checkpoint, metrics_status, finished_at)
                VALUES (?, '2026-10-16', 'daily', 'complete', ?, 'seed-next-day',
                        '2026-10-16T13:00:00+09:00', '2026-10-16T17:45:00+09:00',
                        '2026-10-16T18:00:00+09:00', 0, 0, NULL, 'not_started',
                        '2026-10-16T18:00:00+09:00')
                """,
                (next_run, settings_version),
            )
            for action_id in ("ACT-FILE-PENDING-POSTED", "ACT-FILE-PENDING-CORRECTION"):
                action_row = connection.execute(
                    "SELECT * FROM human_action WHERE action_id=?", (action_id,)
                ).fetchone()
                connection.execute("BEGIN IMMEDIATE")
                apply_human_action_in_transaction(
                    connection,
                    action_row,
                    next_run,
                    "2026-10-16T13:00:00+09:00",
                )
                connection.commit()
            publication = connection.execute(
                "SELECT event_id, valid FROM publication_event "
                "WHERE event_id='PUB-ACT-FILE-PENDING-POSTED'"
            ).fetchone()
            assert tuple(publication) == (
                "PUB-ACT-FILE-PENDING-POSTED",
                0,
            )
            assert (
                connection.execute(
                    "SELECT status FROM draft WHERE draft_id='D-A01'"
                ).fetchone()[0]
                == "committed"
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM reservation WHERE status='active'"
                ).fetchone()[0]
                == 15
            )
        finally:
            connection.close()
