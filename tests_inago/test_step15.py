"""Fixture-only dashboard export, local HTTP, and event contract tests."""

from __future__ import annotations

import http.client
import json
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from automation.inago.clock import FakeClock
from automation.inago.export import build_dashboard_state, safe_product_url
from automation.inago.human_api import create_server
from automation.inago.report_apply import apply_pending_reports
from automation.inago.repository import initialize_database
from tests_inago.test_step05 import (
    _json_request,
    _report,
    _running_server,
    _seed_fifteen_drafts,
)


ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
STATIC_HTML = ROOT / "automation" / "inago" / "templates" / "posting-dashboard.html"
APP_JS = ROOT / "rakuten-room-inago" / "dashboard-assets" / "app.js"


@dataclass(frozen=True)
class DashboardDatabase:
    path: Path
    connect: Any


@pytest.fixture
def dashboard_database(tmp_path: Path) -> DashboardDatabase:
    path = tmp_path / "dashboard-state.sqlite"

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
    return DashboardDatabase(path=path, connect=connect)


def _add_next_day_run(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('R2', '2026-10-13', 'daily', 'running', ?, 'seed-r2',
                '2026-10-13T13:00:00+09:00', '2026-10-13T17:45:00+09:00',
                '2026-10-13T18:00:00+09:00', 0, 0, NULL, 'not_started', NULL)
        """,
        (SETTINGS_VERSION,),
    )


def _request_without_origin(
    server: Any, path: str
) -> tuple[int, str, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        headers = {name.lower(): value for name, value in response.getheaders()}
        return (
            response.status,
            response.getheader("Content-Type", ""),
            headers,
            response.read(),
        )
    finally:
        connection.close()


class StaticDOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def test_dashboard_export_keeps_current_supply_separate_from_generation_history(
    dashboard_database: DashboardDatabase,
) -> None:
    connection = dashboard_database.connect()
    try:
        state = build_dashboard_state(connection)
    finally:
        connection.close()

    assert state["summary"]["available_count"] == 15
    assert state["summary"]["generated_count_total"] == 15
    assert state["summary"]["reported_posted_count"] == 0
    assert len(state["items"]) == 15
    assert state["items"][0]["body"] == "匿名fixture本文1"
    assert state["items"][0]["draft_version"] == 1
    assert state["items"][0]["pattern_version"] == "confirmed-0.3"
    assert state["items"][0]["settings_version"] == SETTINGS_VERSION
    assert state["recent_reports"] == []


def test_eight_night_reports_stay_pending_then_apply_without_rewriting_generation(
    dashboard_database: DashboardDatabase,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    with _running_server(dashboard_database.connect, clock) as server:
        _, before, _ = _json_request(server, "GET", path="/api/state")
        assert before is not None
        csrf_token = str(before["csrf_token"])
        assert before["summary"]["available_count"] == 15
        assert before["summary"]["generated_count_total"] == 15

        for number in range(1, 9):
            status, receipt, _ = _json_request(
                server,
                "POST",
                payload=_report(number=number),
                csrf_token=csrf_token,
                path="/api/reports",
            )
            assert status == 201
            assert receipt is not None and receipt["saved"] is True
            assert receipt["apply_status"] == "pending"

        _, night, _ = _json_request(server, "GET", path="/api/state")
        assert night is not None
        assert night["summary"]["available_count"] == 15
        assert night["summary"]["reported_posted_count"] == 0
        assert night["summary"]["pending_report_count"] == 8
        assert night["summary"]["generated_count_total"] == 15
        assert {row["apply_status"] for row in night["pending_reports"]} == {"pending"}

        connection = dashboard_database.connect()
        try:
            _add_next_day_run(connection)
            clock.advance(15 * 60 * 60)
            applied = apply_pending_reports(connection, run_id="R2", clock=clock)
            assert len(applied.applied_action_ids) == 8
            assert applied.pending_action_ids == ()
            assert applied.exploration_allowed is True
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM publication_event WHERE valid=1"
                ).fetchone()[0]
                == 8
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM reservation WHERE status='active'"
                ).fetchone()[0]
                == 7
            )
            assert (
                connection.execute(
                    "SELECT committed_count FROM run WHERE run_id='R1'"
                ).fetchone()[0]
                == 15
            )
        finally:
            connection.close()

        _, next_day, _ = _json_request(server, "GET", path="/api/state")
        assert next_day is not None
        assert next_day["summary"]["available_count"] == 7
        assert next_day["summary"]["reported_posted_count"] == 8
        assert next_day["summary"]["pending_report_count"] == 0
        assert next_day["summary"]["generated_count_total"] == 15
        assert {row["apply_status"] for row in next_day["recent_reports"]} == {
            "applied"
        }


def test_prior_version_report_and_later_do_not_post_are_separate(
    dashboard_database: DashboardDatabase,
) -> None:
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    connection = dashboard_database.connect()
    try:
        _add_next_day_run(connection)
        connection.execute(
            "UPDATE reservation SET status='released', released_at=?, release_reason='posted' "
            "WHERE offer_key='shop_test:item_A09'",
            ("2026-10-12T21:00:00+09:00",),
        )
        connection.execute(
            """
            INSERT INTO publication_event
                (event_id, offer_key, draft_id, draft_version, reported_at,
                 actual_posted_at, room_url, evidence_kind, valid, revision)
            VALUES ('PUB-A09', 'shop_test:item_A09', 'D-A09', 1, ?, ?, ?,
                    'human_statement', 1, 0)
            """,
            (
                "2026-10-12T21:00:00+09:00",
                "2026-10-12T20:55:00+09:00",
                "https://room.rakuten.co.jp/fixture/items/PUB-A09",
            ),
        )
        connection.execute(
            """
            INSERT INTO draft
                (draft_id, offer_key, run_id, version, body, generated_at,
                 committed_at, generator_version, pattern_version, status,
                 artifact_ref, pattern_id, evidence_refs_json)
            VALUES ('D-A09-V2', 'shop_test:item_A09', 'R2', 2,
                    '後続版のfixture本文', '2026-10-13T13:10:00+09:00',
                    '2026-10-13T13:10:00+09:00', 'fixture-copy-1',
                    'confirmed-0.3', 'do_not_post_already_posted', NULL,
                    'P01', '[]')
            """
        )
    finally:
        connection.close()

    with _running_server(dashboard_database.connect, clock) as server:
        _, initial_state, _ = _json_request(server, "GET", path="/api/state")
        assert initial_state is not None
        csrf_token = str(initial_state["csrf_token"])
        status, _, _ = _json_request(
            server,
            "POST",
            payload=_report(
                number=9,
                draft_id="D-A09",
                draft_version=1,
                actual_posted_at="2026-10-12T20:55:00+09:00",
            ),
            csrf_token=csrf_token,
            path="/api/reports",
        )
        assert status == 201
        _, state, _ = _json_request(server, "GET", path="/api/state")

    assert state is not None
    assert state["summary"]["available_count"] == 14
    assert state["summary"]["generated_count_total"] == 15
    assert all(item["draft_id"] != "D-A09-V2" for item in state["items"])
    excluded = next(
        row for row in state["do_not_post_drafts"] if row["draft_id"] == "D-A09-V2"
    )
    assert excluded["draft_status"] == "do_not_post_already_posted"
    assert excluded["settings_version"] == SETTINGS_VERSION
    report = next(row for row in state["pending_reports"] if row["draft_id"] == "D-A09")
    assert report["draft_version"] == 1
    assert report["pattern_version"] == "confirmed-0.3"
    assert report["settings_version"] == SETTINGS_VERSION
    assert report["body"] == "匿名fixture本文9"
    assert report["apply_status"] == "pending"


def test_dashboard_exports_every_pending_report_without_a_recent_history_cap(
    dashboard_database: DashboardDatabase,
) -> None:
    connection = dashboard_database.connect()
    try:
        for number in range(1, 102):
            connection.execute(
                """
                INSERT INTO human_action
                    (action_id, action, offer_key, draft_id, draft_version,
                     prior_revision, receipt_revision, reason, reported_at,
                     actual_posted_at, old_event_ref, new_body, apply_status,
                     applied_at, applied_run_id, apply_error)
                VALUES (?, 'skipped', 'shop_test:item_A01', 'D-A01', 1,
                        ?, ?, NULL, '2026-10-12T21:30:00+09:00', NULL,
                        NULL, NULL, 'pending', NULL, NULL, NULL)
                """,
                (f"ACT-PENDING-{number:03}", number - 1, number),
            )
        state = build_dashboard_state(connection)
    finally:
        connection.close()
    assert state["summary"]["pending_report_count"] == 101
    assert len(state["pending_reports"]) == 101
    assert state["recent_reports"] == []


def test_safe_product_urls_and_dashboard_static_dom_contract(
    dashboard_database: DashboardDatabase,
) -> None:
    assert safe_product_url("https://item.rakuten.co.jp/shop/item/") == (
        "https://item.rakuten.co.jp/shop/item/"
    )
    for unsafe in (
        "javascript:alert(1)",
        "data:text/html,hello",
        "file:///C:/Windows/win.ini",
        "https://user:pass@example.com/item",
        "https://127.0.0.1/private",
        "https://127.1/private",
        "https://0x7f000001/private",
    ):
        assert safe_product_url(unsafe) is None

    parser = StaticDOMParser()
    parser.feed(STATIC_HTML.read_text(encoding="utf-8"))
    by_id = {
        attrs.get("id"): tag
        for tag, attrs in parser.tags
        if attrs.get("id") is not None
    }
    assert by_id["connection-status"] == "p"
    assert by_id["product-list"] == "div"
    assert by_id["pending-reports"] == "ol"
    assert by_id["recent-reports"] == "ol"
    assert by_id["do-not-post-list"] == "ol"
    script_tags = [attrs for tag, attrs in parser.tags if tag == "script"]
    assert script_tags == [{"type": "module", "src": "dashboard-assets/app.js"}]
    assert all(not (tag == "img") for tag, _ in parser.tags)

    connection = dashboard_database.connect()
    try:
        connection.execute(
            "UPDATE offer SET url='javascript:alert(1)', name=? "
            "WHERE offer_key='shop_test:item_A01'",
            ("<img src=x onerror=alert(1)>",),
        )
        connection.execute(
            "UPDATE draft SET body=? WHERE draft_id='D-A01'",
            ("本文<script>alert(1)</script>",),
        )
        connection.commit()
        state = build_dashboard_state(connection)
    finally:
        connection.close()

    item = next(
        row for row in state["items"] if row["offer_key"] == "shop_test:item_A01"
    )
    assert item["product_url"] is None
    assert item["product_name"] == "<img src=x onerror=alert(1)>"
    assert item["body"] == "本文<script>alert(1)</script>"
    app_source = APP_JS.read_text(encoding="utf-8")
    html_source = STATIC_HTML.read_text(encoding="utf-8")
    assert "textContent" in app_source
    assert "innerHTML" not in app_source
    assert "localStorage" not in app_source
    assert "D: 過去の生成実数" in html_source
    assert "P: 管理反映済みの投稿" in html_source


def test_local_dashboard_routes_and_missing_origin_read_only_get(
    dashboard_database: DashboardDatabase,
) -> None:
    with _running_server(
        dashboard_database.connect,
        FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST)),
    ) as server:
        for path, expected_type in (
            ("/", "text/html"),
            ("/dashboard-assets/app.js", "text/javascript"),
            ("/dashboard-assets/style.css", "text/css"),
        ):
            status, content_type, headers, body = _request_without_origin(server, path)
            assert status == 200
            assert content_type.startswith(expected_type)
            assert headers["cache-control"] == "no-store"
            assert headers["x-content-type-options"] == "nosniff"
            assert headers["content-security-policy"]
            assert body

        status, content_type, _, body = _request_without_origin(server, "/api/state")
        assert status == 200
        assert content_type.startswith("application/json")
        state = json.loads(body.decode("utf-8"))
        assert state["summary"]["available_count"] == 15
        assert state["csrf_token"]

        status, error, _ = _json_request(
            server,
            "GET",
            path="/api/state",
            headers={"Origin": "http://127.0.0.1:invalid"},
        )
        assert status == 403
        assert error == {"error": "origin_rejected"}

        status, _, _, body = _request_without_origin(
            server, "/dashboard-assets/../human_api.py"
        )
        assert status == 404
        assert json.loads(body.decode("utf-8")) == {"error": "not_found"}


def test_javascript_click_contract_never_reports_copy_or_product_link() -> None:
    node_script = r"""
      import { createClickHandler, safeProductUrl, messageForApiError, setDashboardOffline } from './rakuten-room-inago/dashboard-assets/app.js';
      const calls = [];
      const messages = [];
      const state = { items: [{ offer_key: 'shop:item1', body: 'fixture body' }] };
      const offlineButtons = [{ disabled: false }, { disabled: false }];
      const offlineStatus = { textContent: '' };
      setDashboardOffline({
        querySelector: () => offlineStatus,
        querySelectorAll: () => offlineButtons,
      }, '保存サーバーに接続できません。');
      const handler = createClickHandler({
        getState: () => state,
        clipboard: { writeText: async (text) => calls.push(`copy:${text}`) },
        onReport: async (action) => calls.push(`report:${action}`),
        setMessage: (message) => messages.push(message),
      });
      const button = (dataset) => ({ dataset });
      const event = (value) => ({ target: { closest: () => value } });
      await handler(event(button({ copy: 'shop:item1' })));
      await handler(event(null));
      await handler(event(button({ reportAction: 'posted', offerKey: 'shop:item1' })));
      const failedCopy = createClickHandler({
        getState: () => state,
        clipboard: { writeText: async () => { throw new Error('clipboard blocked'); } },
        onReport: async (action) => calls.push(`unexpected-report:${action}`),
        setMessage: (message) => messages.push(message),
      });
      await failedCopy(event(button({ copy: 'shop:item1' })));
      process.stdout.write(JSON.stringify({
        calls,
        messages,
        unsafe: [
          safeProductUrl('javascript:alert(1)'),
          safeProductUrl('file:///C:/Windows/win.ini'),
          safeProductUrl('https://user:pass@example.com/'),
          safeProductUrl('https://127.1/private'),
          safeProductUrl('https://0x7f000001/private'),
        ],
        stale: messageForApiError('stale_revision'),
        storage: messageForApiError('storage_unavailable'),
        offline: messageForApiError('connection_unavailable'),
        offlineDisabled: offlineButtons.map((button) => button.disabled),
        offlineStatus: offlineStatus.textContent,
      }));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)
    assert payload["calls"] == ["copy:fixture body", "report:posted"]
    assert any("コピーできませんでした" in message for message in payload["messages"])
    assert payload["unsafe"] == [None, None, None, None, None]
    assert "再読み込み" in payload["stale"]
    assert "保存できません" in payload["storage"]
    assert "接続できません" in payload["offline"]
    assert payload["offlineDisabled"] == [True, True]
    assert "保存サーバーに接続できません" in payload["offlineStatus"]
