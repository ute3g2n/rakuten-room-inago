"""HTTP history launch, durable reports, and SQLite change notifications."""

from __future__ import annotations

import http.client
import json
import queue
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from automation.inago.clock import FakeClock
from automation.inago.dashboard_history import export_dashboard_history_html
from automation.inago.human_api import create_server
from automation.inago.report_apply import apply_pending_reports
from tests_inago.test_daily_fix_history import _database, _insert_next_day_run
from tests_inago.test_step23_12_history_interactions import (
    _insert_offer_and_draft,
)


_ROOT = Path(__file__).resolve().parents[1]
_TOKEN = "isolated-history-web-" + "x" * 40


def _browser_evidence(tmp_path: Path) -> Path:
    evidence = (
        _ROOT
        / "非公開作業データ/イナゴ実装試験/posting-history-http-20261025"
        / tmp_path.parent.name
        / tmp_path.name
    )
    evidence.mkdir(parents=True, exist_ok=True)
    return evidence


class RunningHistory:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.connect = _database(root / "state.sqlite")
        self.server = None
        self.thread = None
        self.port = 0

    def start(self):
        self.server = create_server(
            connection_factory=self.connect,
            auth_token=_TOKEN,
            data_root=self.root,
            port=self.port,
            clock=FakeClock(datetime.fromisoformat("2026-10-12T21:30:00+09:00")),
        )
        self.port = self.server.server_port
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.thread.start()
        return self

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
            self.server = None

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.port}"


@contextmanager
def running_history(root: Path):
    app = RunningHistory(root).start()
    try:
        yield app
    finally:
        app.stop()


def request(app, method, path, *, payload=None, cookie=None, token=None, origin=None):
    headers = {"Origin": origin or app.origin}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload)
    client = http.client.HTTPConnection("127.0.0.1", app.port, timeout=4)
    try:
        client.request(method, path, body=body, headers=headers)
        response = client.getresponse()
        raw = response.read()
        content_type = response.getheader("Content-Type", "")
        data = json.loads(raw) if "application/json" in content_type else raw.decode()
        return response.status, dict(response.getheaders()), data
    finally:
        client.close()


def ticket(app):
    status, _, body = request(
        app, "POST", "/api/history-launch-ticket", payload={}, token=_TOKEN
    )
    assert status == 201
    assert body["service"] == "inago-history-v1"
    return body["ticket"]


def session(app):
    status, headers, _ = request(
        app, "POST", "/api/history-session", payload={"ticket": ticket(app)}
    )
    assert status == 200
    return headers["Set-Cookie"].split(";", 1)[0]


def test_http_history_session_is_single_use_and_survives_server_restart(tmp_path):
    with running_history(tmp_path / "runtime") as app:
        status, headers, html = request(app, "GET", "/posting-history.html")
        assert status == 200
        assert 'data-history-transport="http"' in html
        assert _TOKEN not in html
        assert "nonce-" in headers["Content-Security-Policy"]
        assert "匿名fixture本文1" not in html  # Unauthenticated shell has no DB data.
        launch_ticket = ticket(app)
        status, _, _ = request(
            app,
            "POST",
            "/api/history-session",
            payload={"ticket": launch_ticket},
            origin="http://example.invalid",
        )
        assert status == 403
        status, headers, _ = request(
            app, "POST", "/api/history-session", payload={"ticket": launch_ticket}
        )
        assert status == 200
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        assert "HttpOnly" in headers["Set-Cookie"]
        assert "SameSite=Strict" in headers["Set-Cookie"]
        assert (
            request(
                app, "POST", "/api/history-launch-ticket", payload={}, cookie=cookie
            )[0]
            == 401
        )
        assert (
            request(
                app, "POST", "/api/history-session", payload={"ticket": launch_ticket}
            )[0]
            == 401
        )
        status, _, state = request(app, "GET", "/api/history-state", cookie=cookie)
        assert status == 200 and len(state["days"]) == 2
        app.stop()
        app.start()
        assert request(app, "GET", "/api/history-state", cookie=cookie)[0] == 200
        assert (
            request(app, "GET", "/api/history-state", cookie=cookie + "broken")[0]
            == 401
        )


def test_history_events_notice_external_sqlite_commit(tmp_path):
    with running_history(tmp_path / "runtime") as app:
        cookie = session(app)
        client = http.client.HTTPConnection("127.0.0.1", app.port, timeout=3)
        try:
            client.request("GET", "/api/history-events", headers={"Cookie": cookie})
            response = client.getresponse()
            assert response.status == 200
            assert response.readline().strip() == b"event: history-changed"
            response.readline()
            response.readline()
            connection = app.connect()
            try:
                _insert_next_day_run(connection)
            finally:
                connection.close()
            assert response.readline().strip() == b"event: history-changed"
            status, _, state = request(app, "GET", "/api/history-state", cookie=cookie)
            assert status == 200
            assert state["days"][0]["target_date"] == "2026-10-13"
        finally:
            client.close()


def test_launcher_reuses_server_and_opens_one_time_authenticated_url(tmp_path):
    from automation.inago.history_launcher import launch_history
    from automation.inago.production_runtime import HumanApiRuntimeConfig

    with running_history(tmp_path / "runtime") as app:
        secret = app.root / "secrets" / "human-api-token.json"
        secret.parent.mkdir()
        secret.write_text(json.dumps({"token": _TOKEN}), encoding="utf-8")
        config = HumanApiRuntimeConfig(_ROOT, app.root, tmp_path / "backups", app.port)
        opened = []
        result = launch_history(
            config,
            config_path=tmp_path / "unused.json",
            open_browser=lambda url: opened.append(url) or True,
            start_server=lambda *_: (_ for _ in ()).throw(
                AssertionError("duplicate server")
            ),
        )
        assert result["server"] == "reused" and result["browser_opened"] is True
        assert len(opened) == 1
        assert urlsplit(opened[0]).path == "/posting-history.html"
        assert urlsplit(opened[0]).fragment.startswith("launch=")
        assert "#" not in result["url"] and _TOKEN not in opened[0]


def test_launcher_starts_server_and_waits_before_opening_page(tmp_path):
    import socket
    from types import SimpleNamespace
    from automation.inago.history_launcher import launch_history
    from automation.inago.production_runtime import HumanApiRuntimeConfig

    app = RunningHistory(tmp_path / "runtime")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        app.port = reservation.getsockname()[1]
    secret = app.root / "secrets/human-api-token.json"
    secret.parent.mkdir()
    secret.write_text(json.dumps({"token": _TOKEN}), encoding="utf-8")
    config = HumanApiRuntimeConfig(_ROOT, app.root, tmp_path / "backups", app.port)
    starts = []
    opened = []

    def start(config, config_path):
        starts.append(config.runtime_root)
        app.start()
        return SimpleNamespace(pid=12345, poll=lambda: None)

    try:
        result = launch_history(
            config,
            config_path=tmp_path / "unused.json",
            start_server=start,
            open_browser=lambda url: opened.append(url) or True,
        )
        assert result["server"] == "started" and len(starts) == len(opened) == 1
        assert request(app, "GET", "/health")[2]["status"] == "ready"
    finally:
        app.stop()


def test_launcher_log_failure_does_not_prevent_opening_page(
    tmp_path, monkeypatch, capsys
):
    from automation.inago.history_launcher import launch_history
    from automation.inago.production_runtime import HumanApiRuntimeConfig

    with running_history(tmp_path / "runtime") as app:
        secret = app.root / "secrets/human-api-token.json"
        secret.parent.mkdir()
        secret.write_text(json.dumps({"token": _TOKEN}), encoding="utf-8")
        original_open = Path.open

        def open_without_launcher_log(path, *args, **kwargs):
            if path.name == "history-launcher.jsonl":
                raise PermissionError("fixture log unavailable")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_without_launcher_log)
        config = HumanApiRuntimeConfig(_ROOT, app.root, tmp_path / "backups", app.port)
        result = launch_history(
            config,
            config_path=tmp_path / "unused.json",
            open_browser=lambda _: True,
        )
        assert result["browser_opened"] is True
        assert "ログを保存できません" in capsys.readouterr().err


def test_launcher_timeout_keeps_database_and_does_not_open_browser(
    tmp_path, monkeypatch
):
    import pytest
    from types import SimpleNamespace
    from urllib.error import URLError
    from automation.inago import history_launcher
    from automation.inago.production_runtime import HumanApiRuntimeConfig

    root = tmp_path / "runtime"
    connect = _database(root / "state.sqlite")
    connect().close()
    secret = root / "secrets/human-api-token.json"
    secret.parent.mkdir()
    secret.write_text(json.dumps({"token": _TOKEN}), encoding="utf-8")

    def unavailable(*_):
        raise URLError("fixture server unavailable")

    monkeypatch.setattr(history_launcher, "_ready_ticket", unavailable)
    config = HumanApiRuntimeConfig(_ROOT, root, tmp_path / "backups", 12345)
    with pytest.raises(history_launcher.HistoryLaunchError, match="起動待ち"):
        history_launcher.launch_history(
            config,
            config_path=tmp_path / "unused.json",
            timeout=0,
            start_server=lambda *_: SimpleNamespace(pid=1, poll=lambda: None),
            open_browser=lambda _: (_ for _ in ()).throw(AssertionError("not ready")),
        )
    connection = connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 17
    finally:
        connection.close()


def test_launcher_rejects_another_runtime_and_does_not_follow_redirects(tmp_path):
    import pytest
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from automation.inago.history_launcher import HistoryLaunchError, launch_history
    from automation.inago.production_runtime import HumanApiRuntimeConfig

    other = tmp_path / "other-runtime"
    connect = _database(other / "state.sqlite")
    connect().close()
    secret = other / "secrets/human-api-token.json"
    secret.parent.mkdir()
    secret.write_text(json.dumps({"token": _TOKEN}), encoding="utf-8")
    with running_history(tmp_path / "runtime") as app:
        config = HumanApiRuntimeConfig(_ROOT, other, tmp_path / "backups", app.port)
        with pytest.raises(HistoryLaunchError, match="別のDB"):
            launch_history(
                config, config_path=tmp_path / "unused.json", open_page=False
            )

    forwarded = []

    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == "/health":
                body = b'{"status":"ready"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                forwarded.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        def do_POST(self):
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{self.server.server_port}/leaked"
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = HumanApiRuntimeConfig(
            _ROOT, other, tmp_path / "backups", server.server_port
        )
        with pytest.raises(HistoryLaunchError, match="HTTP 302"):
            launch_history(
                config, config_path=tmp_path / "unused.json", open_page=False
            )
        assert forwarded == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_ticket_expiry_and_malformed_cookie_are_rejected(monkeypatch):
    from automation.inago import history_session

    monotonic = [100.0]
    wall_time = [1000.0]
    monkeypatch.setattr(history_session.time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(history_session.time, "time", lambda: wall_time[0])
    sessions = history_session.HistorySessions(_TOKEN)
    expired_ticket = sessions.issue_ticket()
    monotonic[0] += 61
    assert sessions.redeem(expired_ticket) is None
    cookie = sessions.redeem(sessions.issue_ticket())
    assert sessions.valid_cookie(cookie)
    assert not sessions.valid_cookie('inago_history_session="99999.a.あ"')
    wall_time[0] += history_session.SESSION_SECONDS + 1
    assert not sessions.valid_cookie(cookie)


def test_confirmed_receipt_revision_survives_failed_state_refresh(tmp_path):
    import os

    evidence = _browser_evidence(tmp_path)
    with running_history(tmp_path / "runtime") as app:
        environment = os.environ.copy()
        environment.update(
            {
                "INAGO_TEST_HISTORY_URL": app.origin
                + "/posting-history.html#launch="
                + ticket(app),
                "INAGO_TEST_HISTORY_EVIDENCE": str(evidence),
                "INAGO_TEST_HISTORY_MODE": "receipt-revision",
            }
        )
        result = subprocess.run(
            ["node", str(_ROOT / "tests_inago/fixtures/history_web_browser.cjs")],
            cwd=_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["phase"] == "receipt_revision_preserved"
        connection = app.connect()
        try:
            assert [
                tuple(row)
                for row in connection.execute(
                    "SELECT action,receipt_revision FROM human_action ORDER BY receipt_revision"
                )
            ] == [("posted", 1), ("correction", 2)]
        finally:
            connection.close()


def test_queued_on_then_off_is_sent_in_receipt_order_after_recovery(tmp_path):
    import os

    evidence = _browser_evidence(tmp_path)
    with running_history(tmp_path / "runtime") as app:
        environment = os.environ.copy()
        environment.update(
            {
                "INAGO_TEST_HISTORY_URL": app.origin
                + "/posting-history.html#launch="
                + ticket(app),
                "INAGO_TEST_HISTORY_EVIDENCE": str(evidence),
                "INAGO_TEST_HISTORY_MODE": "queued-order",
            }
        )
        result = subprocess.run(
            ["node", str(_ROOT / "tests_inago/fixtures/history_web_browser.cjs")],
            cwd=_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["phase"] == "queued_order_preserved"
        connection = app.connect()
        try:
            assert [
                tuple(row)
                for row in connection.execute(
                    "SELECT action,receipt_revision FROM human_action ORDER BY receipt_revision"
                )
            ] == [("posted", 1), ("correction", 2)]
        finally:
            connection.close()


def test_history_web_browser_persists_reports_daily_update_and_restart(tmp_path):
    """Actual clean Chromium context; production DB and ROOM are never touched."""
    import os

    evidence = _browser_evidence(tmp_path)
    with running_history(tmp_path / "runtime") as app:
        environment = os.environ.copy()
        environment["INAGO_TEST_HISTORY_URL"] = (
            app.origin + "/posting-history.html#launch=" + ticket(app)
        )
        environment["INAGO_TEST_HISTORY_EVIDENCE"] = str(evidence)
        browser = subprocess.Popen(
            ["node", str(_ROOT / "tests_inago/fixtures/history_web_browser.cjs")],
            cwd=_ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        messages = queue.Queue()

        def read_messages():
            for line in browser.stdout:
                messages.put(line)
            messages.put(None)

        threading.Thread(target=read_messages, daemon=True).start()

        def expect(phase):
            line = messages.get(timeout=35)
            if line is None:
                raise AssertionError(browser.stderr.read())
            assert json.loads(line)["phase"] == phase

        try:
            expect("posted")
            connection = app.connect()
            try:
                assert (
                    connection.execute(
                        "SELECT COUNT(*) FROM human_action WHERE action='posted'"
                    ).fetchone()[0]
                    == 2
                )
                run_id = _insert_next_day_run(connection)
                _insert_offer_and_draft(
                    connection,
                    run_id=run_id,
                    target_date="2026-10-13",
                    offer_key="shop_test:next_day",
                    draft_id="D-NEXT-DAY",
                    url="https://example.invalid/next-day",
                )
                applied = apply_pending_reports(
                    connection,
                    run_id=run_id,
                    clock=FakeClock(
                        datetime.fromisoformat("2026-10-13T13:00:00+09:00")
                    ),
                )
                assert len(applied.applied_action_ids) == 2
                connection.execute(
                    "UPDATE run SET status='complete', committed_count=1 WHERE run_id=?",
                    (run_id,),
                )
                export_dashboard_history_html(
                    connection,
                    output_path=app.root / "posting-history.html",
                    runtime_root=app.root,
                    api_origin=app.origin,
                )
            finally:
                connection.close()
            expect("daily_update")
            app.stop()
            app.start()
            browser.stdin.write("restart-complete\n")
            browser.stdin.flush()
            expect("restarted_and_corrected")
            browser.wait(timeout=15)
            assert browser.returncode == 0, browser.stderr.read()
            connection = app.connect()
            try:
                assert (
                    connection.execute(
                        "SELECT COUNT(*) FROM human_action WHERE action='correction' AND apply_status='pending'"
                    ).fetchone()[0]
                    == 1
                )
                assert (
                    connection.execute(
                        "SELECT COUNT(*) FROM publication_event WHERE valid=1"
                    ).fetchone()[0]
                    == 2
                )
            finally:
                connection.close()
        finally:
            if browser.poll() is None:
                browser.terminate()
                browser.wait(timeout=5)


def test_rejected_queued_report_does_not_block_another_product(tmp_path):
    import os

    evidence = _browser_evidence(tmp_path)
    with running_history(tmp_path / "runtime") as app:
        environment = os.environ.copy()
        environment.update(
            {
                "INAGO_TEST_HISTORY_URL": app.origin
                + "/posting-history.html#launch="
                + ticket(app),
                "INAGO_TEST_HISTORY_EVIDENCE": str(evidence),
                "INAGO_TEST_HISTORY_MODE": "rejected-outbox",
            }
        )
        result = subprocess.run(
            ["node", str(_ROOT / "tests_inago/fixtures/history_web_browser.cjs")],
            cwd=_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["phase"] == "rejected_outbox_isolated"
        connection = app.connect()
        try:
            assert (
                connection.execute("SELECT COUNT(*) FROM human_action").fetchone()[0]
                == 2
            )
        finally:
            connection.close()
