"""R12 end-to-end contracts for owner history, report receipt, and next-day apply."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from automation.inago.clock import FakeClock
from automation.inago.dashboard_history import export_dashboard_history_html
from automation.inago.human_api import create_server
from automation.inago.report_apply import apply_pending_reports
from automation.inago.report_import import (
    ReportImportError,
    import_offline_report_batches,
)
from automation.inago.repository import initialize_database
from tests_inago.test_step05 import _seed_fifteen_drafts


_ROOT = Path(__file__).resolve().parents[1]
_HISTORY_JS = _ROOT / "rakuten-room-inago" / "dashboard-assets" / "history.js"
_JST = ZoneInfo("Asia/Tokyo")
_API_TOKEN = "r12-isolated-human-api-token-" + ("x" * 40)
_REPORT_TIME = "2026-10-12T21:30:00+09:00"
_NEXT_DAY = "2026-10-13"


def _database(path: Path) -> Callable[[], sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        initialize_database(connection)
        return connection

    connection = connect()
    try:
        _seed_fifteen_drafts(connection)
        _seed_two_past_drafts(connection)
    finally:
        connection.close()
    return connect


def _seed_two_past_drafts(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('R12-PAST', '2026-10-11', 'daily', 'complete', ?, ?, ?,
                NULL, NULL, 0, 2, NULL, 'observed', ?)
        """,
        (
            "confirmed-0.3-step46-all132",
            "R12-PAST-SEED",
            "2026-10-11T13:00:00+09:00",
            "2026-10-11T13:10:00+09:00",
        ),
    )
    for number in range(1, 3):
        offer_key = f"shop_past:item_{number:02}"
        draft_id = f"D-R12-PAST-{number:02}"
        timestamp = "2026-10-11T13:05:00+09:00"
        connection.execute(
            """
            INSERT INTO offer
                (offer_key, shop_id, item_id, name, url, image_refs_json,
                 main_theme_id, aux_theme_ids_json, first_seen, last_seen,
                 facts_json)
            VALUES (?, 'shop_past', ?, ?, ?, '[]', 'G01-T01', '[]', ?, ?, '[]')
            """,
            (
                offer_key,
                f"item_{number:02}",
                f"[試験用] 過去商品{number}",
                f"https://example.invalid/past/{number:02}",
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
            VALUES (?, ?, 'R12-PAST', 2, ?, ?, ?, 'fixture-copy',
                    'confirmed-0.3', 'committed', NULL, 'P01', '[]')
            """,
            (
                draft_id,
                offer_key,
                f"[試験用] 版2の過去本文{number}",
                timestamp,
                timestamp,
            ),
        )


def _insert_next_day_run(connection: sqlite3.Connection) -> str:
    run_id = "R12-N27-NEXT"
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, ?, 'daily', 'created', ?, ?, ?, NULL, NULL, 0, 0, NULL,
                'not_started', NULL)
        """,
        (
            run_id,
            _NEXT_DAY,
            "confirmed-0.3-step46-all132",
            "R12-N27-SEED",
            f"{_NEXT_DAY}T13:00:00+09:00",
        ),
    )
    return run_id


@contextmanager
def _running_api(
    connection_factory: Callable[[], sqlite3.Connection],
    *,
    clock: FakeClock,
    data_root: Path,
    auth_token: str | None = None,
) -> Iterator[Any]:
    server = create_server(
        connection_factory=connection_factory,
        clock=clock,
        bind_host="127.0.0.1",
        port=0,
        auth_token=auth_token,
        data_root=data_root,
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
        server.server_close()
        thread.join(timeout=5)


def _history_payload(path: Path) -> dict[str, Any]:
    html = path.read_text(encoding="utf-8")
    match = re.search(
        r'<script id="dashboard-history-data" type="application/json">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def _report(
    action_id: str,
    *,
    draft_id: str,
    offer_key: str,
    version: int = 1,
    revision: int = 0,
    action: str = "posted",
    reason: str | None = None,
    correction_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "offer_key": offer_key,
        "draft_id": draft_id,
        "draft_version": version,
        "expected_receipt_revision": revision,
        "action": action,
        "reason": reason,
        "correction_ref": correction_ref,
        "actual_posted_at": None,
    }


def _batch(*reports: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "reports": [
            {"queued_at": _REPORT_TIME, "report": report} for report in reports
        ],
    }


def _write_json_batch(root: Path, name: str, payload: dict[str, Any]) -> Path:
    inbox = root / "incoming-human-reports"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


_HISTORY_VM_SUPPORT = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.env.INAGO_HISTORY_JS, 'utf8');
const payload = JSON.parse(process.env.INAGO_PAYLOAD);
const API_ORIGIN = process.env.INAGO_API_ORIGIN;
const realFetch = globalThis.fetch.bind(globalThis);
const realDate = Date;
const requestObservations = [];
const fixedDate = '2026-10-12T12:30:00.000Z';
class FixtureDate extends realDate {
  constructor(...args) { super(...(args.length ? args : [fixedDate])); }
  static now() { return realDate.parse(fixedDate); }
}
let generatedId = 0;
const storedRows = [];
const objectStore = {
  getAll() { return { result: structuredClone(storedRows) }; },
  put(row) {
    const index = storedRows.findIndex((entry) => entry.action_id === row.action_id);
    if (index >= 0) storedRows.splice(index, 1);
    storedRows.push(structuredClone(row));
    return { result: row };
  },
  delete(id) {
    const index = storedRows.findIndex((entry) => entry.action_id === id);
    if (index >= 0) storedRows.splice(index, 1);
    return { result: undefined };
  },
};
const indexedDB = {
  open() {
    const request = {};
    const db = {
      objectStoreNames: { contains: () => true },
      transaction() {
        const tx = { objectStore: () => objectStore };
        setTimeout(() => tx.oncomplete?.(), 0);
        return tx;
      },
    };
    setTimeout(() => { request.result = db; request.onsuccess?.(); }, 0);
    return request;
  },
};
class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName.toLowerCase();
    this.dataset = {};
    this.textContent = '';
    this.hidden = false;
    this.children = [];
    this.listeners = new Map();
    this.attributes = {};
    this.checked = false;
    this.disabled = false;
    this.value = '';
    this._className = '';
    this.parentElement = null;
  }
  set className(value) { this._className = value; }
  get className() { return this._className; }
  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parentElement = null;
    this.children = [];
    this.textContent = '';
    this.append(...children);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  focus() {}
  select() {}
  get options() { return this.children; }
  click() {
    if (this.tagName === 'a') this.owner?.downloads.push({ href: this.href, download: this.download });
    return this.listeners.get('click')?.();
  }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  matches(selector) {
    if (/^[a-z]+$/.test(selector)) return this.tagName === selector;
    if (selector === '.posted-checkbox:checked') return this.className === 'posted-checkbox' && this.checked;
    if (selector === 'input[data-report-posted-draft-id]') return this.tagName === 'input' && this.dataset.reportPostedDraftId !== undefined;
    if (selector === 'button[data-report-action]') return this.tagName === 'button' && this.dataset.reportAction !== undefined;
    if (selector === 'button[data-report-action="skipped"]') return this.tagName === 'button' && this.dataset.reportAction === 'skipped';
    if (selector === 'button[data-report-action="correction"]') return this.tagName === 'button' && this.dataset.reportAction === 'correction';
    if (selector === 'button[data-target-date]') return this.tagName === 'button' && this.dataset.targetDate !== undefined;
    if (selector === 'button[data-copy-draft-id]') return this.tagName === 'button' && this.dataset.copyDraftId !== undefined;
    if (selector === '[data-report-status]') return this.dataset.reportStatus !== undefined;
    if (selector === '[data-correction-ref]') return this.dataset.correctionRef !== undefined;
    if (selector === '[data-correction-reason]') return this.dataset.correctionReason !== undefined;
    if (selector === '[data-actual-posted-at]') return this.dataset.actualPostedAt !== undefined;
    if (selector === 'details.correction-controls') return this.tagName === 'details' && this.className === 'correction-controls';
    const article = selector.match(/^article\[data-draft-id="([^"]+)"\]$/);
    if (article) return this.tagName === 'article' && this.dataset.draftId === article[1];
    return false;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    return this.descendants().filter((child) => selectors.some((item) => child.matches(item)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function makeBrowser() {
  const elements = new Map();
  const downloads = [];
  const blobs = new Map();
  let blobSequence = 0;
  let intervalCallback = null;
  const getElement = (id) => {
    if (!elements.has(id)) {
      const element = new Element();
      element.id = id;
      if (id === 'dashboard-history-data') element.textContent = JSON.stringify(payload);
      if (id === 'history-auth') element.hidden = true;
      element.owner = browser;
      elements.set(id, element);
    }
    return elements.get(id);
  };
  const documentListeners = new Map();
  const windowListeners = new Map();
  const clipboardWrites = [];
  const browser = {
    elements,
    downloads,
    blobs,
    clipboardWrites,
    requestObservations,
    get: getElement,
    get intervalCallback() { return intervalCallback; },
    get storedRows() { return storedRows.length; },
  };
  const document = {
    documentElement: { dataset: { apiOrigin: API_ORIGIN } },
    visibilityState: 'visible',
    getElementById: getElement,
    createElement(tagName) {
      const element = new Element(tagName);
      element.owner = browser;
      return element;
    },
    addEventListener(name, callback) { documentListeners.set(name, callback); },
  };
  const window = {
    prompt() { throw new Error('native prompt must not be used'); },
    setInterval(callback) { intervalCallback = callback; return 17; },
    addEventListener(name, callback) { windowListeners.set(name, callback); },
  };
  const navigator = {
    clipboard: { async writeText(value) { clipboardWrites.push(value); } },
  };
  const URLApi = {
    createObjectURL(blob) {
      const value = `blob:r12-${++blobSequence}`;
      blobs.set(value, blob);
      return value;
    },
    revokeObjectURL() {},
  };
  const fetch = async (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    headers.set('Origin', API_ORIGIN);
    const response = await realFetch(url, { ...options, headers });
    requestObservations.push({
      path: new URL(url).pathname,
      authenticated: headers.has('Authorization'),
      status: response.status,
    });
    return response;
  };
  const context = {
    document, window, indexedDB, fetch, navigator,
    setTimeout, clearTimeout, URL: URLApi, Blob, Date: FixtureDate,
    Promise, TypeError, JSON, Math, String, Number,
    CSS: { escape: (value) => value },
    crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(++generatedId).padStart(12, '0')}` },
    structuredClone, console,
  };
  vm.runInNewContext(source, context, { filename: 'history.js' });
  browser.window = window;
  browser.documentListeners = documentListeners;
  browser.windowListeners = windowListeners;
  return browser;
}
async function waitFor(predicate, label, details = () => ({})) {
  const end = Date.now() + 8000;
  while (!predicate()) {
    if (Date.now() > end) {
      throw new Error(`timed out waiting for ${label}: ${JSON.stringify(details())}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}
"""


def _run_node_script(
    tmp_path: Path,
    script: str,
    *,
    api_origin: str,
    payload: dict[str, Any],
    token: str | None = None,
) -> dict[str, Any]:
    node = shutil.which("node")
    assert node, "Node.js is required for the isolated history browser test"
    script_path = tmp_path / "history-browser-fixture.cjs"
    script_path.write_text(_HISTORY_VM_SUPPORT + script, encoding="utf-8")
    environment = os.environ.copy()
    environment["INAGO_HISTORY_JS"] = str(_HISTORY_JS)
    environment["INAGO_API_ORIGIN"] = api_origin
    environment["INAGO_PAYLOAD"] = json.dumps(payload, ensure_ascii=False)
    environment["INAGO_TOKEN"] = token or ""
    result = subprocess.run(
        [node, str(script_path)],
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, "the browser fixture must return its observations"
    return json.loads(lines[-1])


def _trace_events(data_root: Path, run_id: str) -> list[dict[str, Any]]:
    path = data_root / "logs" / "traces" / "2026-10-12" / f"{run_id}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_n26_history_date_copy_report_and_pending_receipt_trace(tmp_path: Path) -> None:
    """Date/version selection and owner actions connect to SQLite receipts."""

    database_path = tmp_path / "database" / "state.sqlite"
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    connect = _database(database_path)
    clock = FakeClock(datetime.fromisoformat(_REPORT_TIME))

    with _running_api(
        connect,
        clock=clock,
        data_root=runtime_root,
        auth_token=_API_TOKEN,
    ) as server:
        html_path = runtime_root / "posting-history.html"
        connection = connect()
        try:
            exported = export_dashboard_history_html(
                connection,
                output_path=html_path,
                runtime_root=runtime_root,
                api_origin=server.origin,
            )
        finally:
            connection.close()

        assert (exported.run_count, exported.draft_count) == (2, 17)
        html = html_path.read_text(encoding="utf-8")
        assert f'data-api-origin="{server.origin}"' in html
        assert "external" not in html.lower()
        payload = _history_payload(html_path)
        assert [day["target_date"] for day in payload["days"]] == [
            "2026-10-12",
            "2026-10-11",
        ]
        assert len(payload["days"][0]["items"]) == 15
        assert len(payload["days"][1]["items"]) == 2
        assert {item["draft_version"] for item in payload["days"][1]["items"]} == {2}

        browser_observation = _run_node_script(
            tmp_path,
            r"""
(async () => {
  const browser = makeBrowser();
  const get = browser.get;
  await waitFor(() => get('history-connection').dataset.historyConnection === 'auth-required', 'API token form');
  assert.ok(browser.requestObservations.some(({ authenticated, status }) =>
    authenticated === false && status === 401));
  get('history-api-token').value = process.env.INAGO_TOKEN;
  get('history-auth-submit').listeners.get('click')();
  await waitFor(() => get('history-connection').dataset.historyConnection === 'ready', 'API connection');

  const navigation = get('date-navigation');
  const oldDay = navigation.children.find((button) => button.dataset.targetDate === '2026-10-11');
  oldDay.listeners.get('click')();
  assert.equal(get('selected-date').textContent, '2026-10-11の投稿内容');
  assert.equal(get('daily-count').textContent, '2');
  const oldCard = get('draft-list').children.find((card) => card.dataset.draftId === 'D-R12-PAST-01');
  assert.equal(oldCard.querySelector('textarea').value, '[試験用] 版2の過去本文1');
  assert.match(oldCard.children.find((item) => item.tagName === 'p' && item.className === 'meta').textContent, /2版/);

  const currentDay = navigation.children.find((button) => button.dataset.targetDate === '2026-10-12');
  currentDay.listeners.get('click')();
  assert.equal(get('daily-count').textContent, '15');
      let currentCard = get('draft-list').children.find((card) => card.dataset.draftId === 'D-A01');
  const copy = currentCard.querySelector('button[data-copy-draft-id]');
  await copy.listeners.get('click')();
  assert.deepEqual(browser.clipboardWrites, ['匿名fixture本文1']);
  assert.match(get('history-message').textContent, /D-A01 1版の本文をコピーしました/);
  assert.equal(get('posted-count').textContent, '0');
  assert.equal(get('history-connection').dataset.historyConnection, 'ready');

  let checkbox = currentCard.querySelector('input[data-report-posted-draft-id]');
  checkbox.checked = true;
  checkbox.listeners.get('change')();
  await waitFor(() => browser.requestObservations.some((request) =>
    request.path === '/api/reports' && request.authenticated && request.status === 201),
  'authenticated posted report receipt', () => ({ requests: browser.requestObservations }));
  await waitFor(() => get('report-count-posted-pending').textContent === '1', 'posted receipt', () => ({
    message: get('history-message').textContent,
    connection: get('history-connection').dataset.historyConnection,
    disabled: currentCard.querySelector('input[data-report-posted-draft-id]').disabled,
    count: get('report-count-posted-pending').textContent,
    requests: browser.requestObservations,
  }));
      currentCard = get('draft-list').children.find((card) => card.dataset.draftId === 'D-A01');
  assert.match(currentCard.querySelector('[data-report-status]').textContent, /SQLite受付済み・翌日反映待ち/);
  checkbox = get('draft-list').children.find((card) => card.dataset.draftId === 'D-A01')
    .querySelector('input[data-report-posted-draft-id]');
  checkbox.checked = false;
  checkbox.listeners.get('change')();
  await waitFor(() => browser.requestObservations.filter((request) =>
    request.path === '/api/reports' && request.authenticated && request.status === 201).length === 2,
  'authenticated correction receipt', () => ({ requests: browser.requestObservations }));
  await waitFor(() => get('report-count-correction-pending').textContent === '1', 'checkbox-off correction receipt');
  assert.equal(checkbox.disabled, true);
  assert.equal(browser.promptCount || 0, 0);
  console.log(JSON.stringify({
    selectedDates: navigation.children.map((button) => button.dataset.targetDate),
    historicalItems: 2,
    historicalVersion: 2,
    currentItems: 15,
    copiedText: browser.clipboardWrites[0],
    promptCount: 0,
    pendingPosted: get('report-count-posted-pending').textContent,
    pendingCorrection: get('report-count-correction-pending').textContent,
    reportResponses: browser.requestObservations
      .filter((request) => request.path === '/api/reports')
      .map(({ authenticated, status }) => ({ authenticated, status })),
  }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
            api_origin=server.origin,
            payload=payload,
            token=_API_TOKEN,
        )

    assert browser_observation == {
        "selectedDates": ["2026-10-12", "2026-10-11"],
        "historicalItems": 2,
        "historicalVersion": 2,
        "currentItems": 15,
        "copiedText": "匿名fixture本文1",
        "promptCount": 0,
        "pendingPosted": "1",
        "pendingCorrection": "1",
        "reportResponses": [
            {"authenticated": True, "status": 201},
            {"authenticated": True, "status": 201},
        ],
    }

    connection = connect()
    try:
        actions = connection.execute(
            "SELECT action_id, action, draft_id, draft_version, receipt_revision, "
            "reason, correction_action_ref, apply_status FROM human_action "
            "WHERE offer_key='shop_test:item_A01' ORDER BY receipt_revision"
        ).fetchall()
        assert [tuple(row) for row in actions] == [
            (
                "ACT-00000000-0000-4000-8000-000000000001",
                "posted",
                "D-A01",
                1,
                1,
                None,
                None,
                "pending",
            ),
            (
                "ACT-00000000-0000-4000-8000-000000000002",
                "correction",
                "D-A01",
                1,
                2,
                "投稿済みチェック解除（本人操作）",
                "ACT-00000000-0000-4000-8000-000000000001",
                "pending",
            ),
        ]
        assert (
            connection.execute("SELECT COUNT(*) FROM publication_event").fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0]
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

    trace = _trace_events(runtime_root, "R1")
    receipt_events = [
        event
        for event in trace
        if event.get("component") == "human_api"
        and event.get("action_id", "").startswith("ACT-")
    ]
    assert {
        (
            event["action_id"],
            event["report_action"],
            event["receipt_revision"],
            event["apply_status"],
        )
        for event in receipt_events
        if event["phase"] == "end"
    } == {
        ("ACT-00000000-0000-4000-8000-000000000001", "posted", 1, "pending"),
        ("ACT-00000000-0000-4000-8000-000000000002", "correction", 2, "pending"),
    }


def _run_import_rejection(
    connect: Callable[[], sqlite3.Connection],
    root: Path,
    *,
    name: str,
    content: str,
    match: str,
) -> None:
    inbox = root / "incoming-human-reports"
    inbox.mkdir(parents=True, exist_ok=True)
    source = inbox / name
    source.write_text(content, encoding="utf-8")
    connection = connect()
    try:
        before = connection.execute("SELECT COUNT(*) FROM human_action").fetchone()[0]
        with pytest.raises(ReportImportError, match=match):
            import_offline_report_batches(
                connection,
                runtime_root=root,
                clock=FakeClock(datetime.fromisoformat(f"{_NEXT_DAY}T13:00:00+09:00")),
            )
        assert (
            connection.execute("SELECT COUNT(*) FROM human_action").fetchone()[0]
            == before
        )
        assert source.is_file()
    finally:
        connection.close()


def test_n27_browser_outbox_json_import_and_next_day_apply_trace(
    tmp_path: Path,
) -> None:
    """Outbox survives reload, ACK follows receipt, and JSON applies next day."""

    database_path = tmp_path / "database" / "state.sqlite"
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    connect = _database(database_path)
    availability = {"online": False}

    import automation.inago.human_api as human_api

    original_get = human_api._HumanAPIHandler.do_GET

    def toggleable_get(handler) -> None:
        if handler.path.startswith("/__test__/availability"):
            if not handler._origin_is_valid(require_origin=False):
                handler._send_json(403, {"error": "origin_rejected"})
                return
            selected = parse_qs(urlsplit(handler.path).query).get("value", ["0"])[0]
            availability["online"] = selected == "1"
            handler._send_json(200, {"online": availability["online"]})
            return
        original_get(handler)

    human_api._HumanAPIHandler.do_GET = toggleable_get

    def connection_factory() -> sqlite3.Connection:
        if not availability["online"]:
            raise sqlite3.OperationalError("fixture storage unavailable")
        return connect()

    try:
        with _running_api(
            connection_factory,
            clock=FakeClock(datetime.fromisoformat(_REPORT_TIME)),
            data_root=runtime_root,
        ) as server:
            html_path = runtime_root / "posting-history.html"
            connection = connect()
            try:
                export_dashboard_history_html(
                    connection,
                    output_path=html_path,
                    runtime_root=runtime_root,
                    api_origin=server.origin,
                )
            finally:
                connection.close()
            payload = _history_payload(html_path)

            browser_observation = _run_node_script(
                tmp_path,
                r"""
(async () => {
  const first = makeBrowser();
  const get = first.get;
  await waitFor(() => get('history-connection').dataset.historyConnection === 'unavailable', 'API outage');
  const firstCard = get('draft-list').children.find((card) => card.dataset.draftId === 'D-A01');
  let checkbox = firstCard.querySelector('input[data-report-posted-draft-id]');
  checkbox.checked = true;
  checkbox.listeners.get('change')();
  await waitFor(() => first.storedRows === 1, 'first offline report');
  await waitFor(() => get('history-connection').dataset.historyConnection === 'outbox', 'browser outbox state');
  checkbox = get('draft-list').children.find((card) => card.dataset.draftId === 'D-A01')
    .querySelector('input[data-report-posted-draft-id]');
  assert.equal(checkbox.checked, true);
  assert.equal(checkbox.disabled, false);

  const reloaded = makeBrowser();
  await waitFor(() => reloaded.get('history-connection').dataset.historyConnection === 'unavailable', 'reloaded offline page');
  await waitFor(() => reloaded.get('outbox-count').textContent.includes('1件'), 'restored IndexedDB outbox');
  checkbox = reloaded.get('draft-list').children.find((card) => card.dataset.draftId === 'D-A01')
    .querySelector('input[data-report-posted-draft-id]');
  assert.equal(checkbox.checked, true);
  assert.match(reloaded.get('draft-list').children[0].querySelector('[data-report-status]').textContent, /ブラウザー保留・SQLite未受付/);

  await setAvailability(true);
  reloaded.intervalCallback();
  await waitFor(() => reloaded.get('history-connection').dataset.historyConnection === 'ready'
    && reloaded.storedRows === 0, 'server receipt and outbox ACK');
  reloaded.intervalCallback();
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(reloaded.storedRows, 0, 'ACKed action is not posted again');

  await setAvailability(false);
  const secondCard = reloaded.get('draft-list').children.find((card) => card.dataset.draftId === 'D-A02');
  secondCard.querySelector('button[data-report-action="skipped"]').listeners.get('click')();
  await waitFor(() => reloaded.storedRows === 1, 'second offline report');
  const reopened = makeBrowser();
  await waitFor(() => reopened.get('history-connection').dataset.historyConnection === 'unavailable'
    && reopened.get('outbox-count').textContent.includes('1件'), 'outbox after browser close');
  reopened.get('export-outbox').listeners.get('click')();
  await waitFor(() => reopened.get('history-message').textContent.includes('JSONを書き出しました'), 'outbox JSON export');
  const download = reopened.downloads[0];
  assert.ok(download?.href);
  const batch = JSON.parse(await reopened.blobs.get(download.href).text());
  assert.equal(batch.schema_version, 1);
  assert.equal(batch.reports.length, 1);
  assert.equal(batch.reports[0].report.action, 'skipped');
  assert.equal(batch.reports[0].report.action_id, 'ACT-00000000-0000-4000-8000-000000000002');
  console.log(JSON.stringify({
    browserReloadRestored: true,
    outboxBeforeAck: 1,
    outboxAfterAck: 0,
    serverReportActions: 1,
    exportedAction: batch.reports[0].report.action,
    exportedActionId: batch.reports[0].report.action_id,
  }));
})().catch((error) => { console.error(error); process.exitCode = 1; });

async function setAvailability(online) {
  const response = await globalThis.fetch(
    `${API_ORIGIN}/__test__/availability?value=${online ? '1' : '0'}`,
    { headers: { Origin: API_ORIGIN } },
  );
  assert.equal(response.status, 200);
}
""",
                api_origin=server.origin,
                payload=payload,
            )

        exported_batch = {
            "schema_version": 1,
            "reports": [
                {
                    "queued_at": _REPORT_TIME,
                    "report": _report(
                        "ACT-00000000-0000-4000-8000-000000000002",
                        draft_id="D-A02",
                        offer_key="shop_test:item_A02",
                        action="skipped",
                    ),
                }
            ],
        }
        assert browser_observation == {
            "browserReloadRestored": True,
            "outboxBeforeAck": 1,
            "outboxAfterAck": 0,
            "serverReportActions": 1,
            "exportedAction": "skipped",
            "exportedActionId": "ACT-00000000-0000-4000-8000-000000000002",
        }
        assert exported_batch["reports"][0]["report"]["action"] == "skipped"

        incoming_file = _write_json_batch(
            runtime_root, "human-reports-r12.json", exported_batch
        )
        connection = connect()
        try:
            imported = import_offline_report_batches(
                connection,
                runtime_root=runtime_root,
                clock=FakeClock(datetime.fromisoformat(f"{_NEXT_DAY}T13:00:00+09:00")),
                trace_run_id="R12-N27-NEXT",
                trace_target_date=_NEXT_DAY,
            )
            assert (imported.files_processed, imported.reports_received) == (1, 1)
            assert not incoming_file.exists()
            assert (
                runtime_root / "processed-human-reports" / incoming_file.name
            ).is_file()
            rows = connection.execute(
                "SELECT action_id, action, apply_status, applied_run_id "
                "FROM human_action ORDER BY action_id"
            ).fetchall()
            assert [tuple(row) for row in rows] == [
                ("ACT-00000000-0000-4000-8000-000000000001", "posted", "pending", None),
                (
                    "ACT-00000000-0000-4000-8000-000000000002",
                    "skipped",
                    "pending",
                    None,
                ),
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

        duplicate_file = _write_json_batch(
            runtime_root, "human-reports-r12-retry.json", exported_batch
        )
        connection = connect()
        try:
            retry = import_offline_report_batches(
                connection,
                runtime_root=runtime_root,
                clock=FakeClock(datetime.fromisoformat(f"{_NEXT_DAY}T13:00:00+09:00")),
                trace_run_id="R12-N27-NEXT",
                trace_target_date=_NEXT_DAY,
            )
            assert retry.reports_received == 1
            assert not duplicate_file.exists()
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM human_action WHERE action_id=?",
                    ("ACT-00000000-0000-4000-8000-000000000002",),
                ).fetchone()[0]
                == 1
            )
            duplicate_payload = _batch(
                _report(
                    "ACT-N27-DUPLICATE-IN-BATCH",
                    draft_id="D-A03",
                    offer_key="shop_test:item_A03",
                ),
                _report(
                    "ACT-N27-DUPLICATE-IN-BATCH",
                    draft_id="D-A03",
                    offer_key="shop_test:item_A03",
                ),
            )
            _run_import_rejection(
                connect,
                tmp_path / "duplicate-action",
                name="human-reports-duplicate.json",
                content=json.dumps(duplicate_payload, ensure_ascii=False),
                match="repeats an action ID",
            )
            reversed_batch = _batch(
                _report(
                    "ACT-N27-REVERSED-CORRECTION",
                    draft_id="D-A03",
                    offer_key="shop_test:item_A03",
                    revision=1,
                    action="correction",
                    reason="fixture correction",
                    correction_ref="ACT-N27-REVERSED-POSTED",
                ),
                _report(
                    "ACT-N27-REVERSED-POSTED",
                    draft_id="D-A03",
                    offer_key="shop_test:item_A03",
                ),
            )
            _run_import_rejection(
                connect,
                tmp_path / "reversed-actions",
                name="human-reports-reversed.json",
                content=json.dumps(reversed_batch, ensure_ascii=False),
                match="contract conflict",
            )
            conflict_batch = _batch(
                _report(
                    "ACT-N27-COMPETE-POSTED",
                    draft_id="D-A04",
                    offer_key="shop_test:item_A04",
                ),
                _report(
                    "ACT-N27-COMPETE-SKIPPED",
                    draft_id="D-A04",
                    offer_key="shop_test:item_A04",
                    action="skipped",
                ),
            )
            _run_import_rejection(
                connect,
                tmp_path / "revision-conflict",
                name="human-reports-revision-conflict.json",
                content=json.dumps(conflict_batch, ensure_ascii=False),
                match="revision conflict",
            )
            old_version_batch = _batch(
                _report(
                    "ACT-N27-OLD-VERSION",
                    draft_id="D-R12-PAST-01",
                    offer_key="shop_past:item_01",
                    version=1,
                )
            )
            _run_import_rejection(
                connect,
                tmp_path / "old-version",
                name="human-reports-old-version.json",
                content=json.dumps(old_version_batch, ensure_ascii=False),
                match="contract conflict",
            )
            _run_import_rejection(
                connect,
                tmp_path / "invalid-json",
                name="human-reports-invalid.json",
                content='{"schema_version":1,"schema_version":1,"reports":[]}',
                match="JSON is invalid",
            )
            _insert_next_day_run(connection)
        finally:
            connection.close()

        connection = connect()
        try:
            applied = apply_pending_reports(
                connection,
                run_id="R12-N27-NEXT",
                clock=FakeClock(datetime.fromisoformat(f"{_NEXT_DAY}T13:00:00+09:00")),
                data_root=runtime_root,
            )
            assert applied.exploration_allowed is True
            assert applied.pending_action_ids == ()
            assert set(applied.applied_action_ids) == {
                "ACT-00000000-0000-4000-8000-000000000001",
                "ACT-00000000-0000-4000-8000-000000000002",
            }
            states = connection.execute(
                "SELECT action_id, action, apply_status, applied_run_id "
                "FROM human_action ORDER BY action_id"
            ).fetchall()
            assert [tuple(row) for row in states] == [
                (
                    "ACT-00000000-0000-4000-8000-000000000001",
                    "posted",
                    "applied",
                    "R12-N27-NEXT",
                ),
                (
                    "ACT-00000000-0000-4000-8000-000000000002",
                    "skipped",
                    "applied",
                    "R12-N27-NEXT",
                ),
            ]
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM publication_event WHERE valid=1"
                ).fetchone()[0]
                == 1
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM reservation WHERE status='active'"
                ).fetchone()[0]
                == 13
            )
            assert (
                connection.execute(
                    "SELECT committed_count FROM run WHERE run_id='R1'"
                ).fetchone()[0]
                == 15
            )
        finally:
            connection.close()

        connection = connect()
        try:
            repeated_apply = apply_pending_reports(
                connection,
                run_id="R12-N27-NEXT",
                clock=FakeClock(datetime.fromisoformat(f"{_NEXT_DAY}T13:10:00+09:00")),
                data_root=runtime_root,
            )
            assert repeated_apply.exploration_allowed is True
            assert repeated_apply.applied_action_ids == ()
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM publication_event WHERE valid=1"
                ).fetchone()[0]
                == 1
            )
        finally:
            connection.close()

    finally:
        human_api._HumanAPIHandler.do_GET = original_get

    original_run_trace = _trace_events(runtime_root, "R1")
    original_receipt = [
        event
        for event in original_run_trace
        if event.get("component") == "human_api"
        and event.get("action_id") == "ACT-00000000-0000-4000-8000-000000000001"
        and event.get("phase") == "end"
    ]
    assert len(original_receipt) == 1
    next_day_trace_path = (
        runtime_root / "logs" / "traces" / _NEXT_DAY / "R12-N27-NEXT.jsonl"
    )
    next_day_trace = [
        json.loads(line)
        for line in next_day_trace_path.read_text(encoding="utf-8").splitlines()
    ]
    imported = [
        event
        for event in next_day_trace
        if event.get("component") == "report_import"
        and event.get("action_id") == "ACT-00000000-0000-4000-8000-000000000002"
        and event.get("phase") == "end"
    ]
    applied = [
        event
        for event in next_day_trace
        if event.get("operation") == "report_apply"
        and event.get("action_id")
        in {
            "ACT-00000000-0000-4000-8000-000000000001",
            "ACT-00000000-0000-4000-8000-000000000002",
        }
        and event.get("phase") == "end"
    ]
    assert len(imported) == 2
    assert {event["action_id"] for event in applied} == {
        "ACT-00000000-0000-4000-8000-000000000001",
        "ACT-00000000-0000-4000-8000-000000000002",
    }
    assert all(event["apply_status"] == "applied" for event in applied)
