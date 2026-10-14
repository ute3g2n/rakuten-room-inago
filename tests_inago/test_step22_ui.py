"""Browser-level dashboard checks against an isolated fixture database."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from automation.inago.clock import FakeClock
from automation.inago.human_api import create_server
from automation.inago.report_apply import apply_pending_reports
from automation.inago.repository import initialize_database
from tests_inago.test_step05 import _seed_fifteen_drafts


ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
CHROME_CANDIDATES = (
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Microsoft/Edge/Application/msedge.exe",
)


_BROWSER_SCRIPT = r"""
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const [origin, evidenceDirectory, phase, executablePath, playwrightModule] = process.argv.slice(2);
const { chromium } = await import(pathToFileURL(playwrightModule).href);
const browser = await chromium.launch({
  headless: true,
  executablePath,
  args: ['--no-first-run', '--no-default-browser-check'],
});
const result = { phase, externalRequests: [] };
const trackRequests = (page) => page.on('request', (request) => {
  try {
    if (new URL(request.url()).origin !== origin) result.externalRequests.push(request.url());
  } catch {
    result.externalRequests.push('invalid-url');
  }
});
const connected = async (page) => {
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelector('#connection-status')?.textContent
    .includes('保存サーバーに接続しました。'));
};
const reportButton = (page) => page.locator('#product-list article').first()
  .locator('button[data-report-action="posted"]');

try {
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (value) => { window.__fixtureClipboard = value; } },
    });
  });

  if (phase === 'main') {
    const desktop = await context.newPage();
    trackRequests(desktop);
    await connected(desktop);
    assert.equal(await desktop.title(), 'イナゴスキル 投稿下書き');
    assert.deepEqual(await desktop.locator('.summary-card strong').allTextContents(),
      ['15', '15', '0', '0']);
    assert.equal(await desktop.locator('#product-list article').count(), 15);
    assert.equal(await desktop.locator('#product-list a').count(), 15);
    assert.equal(await desktop.locator('#product-list textarea[readonly]').count(), 15);
    await desktop.screenshot({ path: `${evidenceDirectory}/dashboard-desktop.png`, fullPage: true });
    result.desktop = { viewport: { width: 1365, height: 900 }, cards: 15, safeLinks: 15 };

    await desktop.locator('#product-list button[data-copy]').first().click();
    await desktop.waitForFunction(() => document.querySelector('#action-message')?.textContent
      .includes('投稿報告は送信していません。'));
    const copiedText = await desktop.evaluate(() => window.__fixtureClipboard);
    assert.equal(copiedText, '匿名fixture本文1');
    result.copy = { copiedFixtureText: true, reportSubmitted: false };

    await desktop.setViewportSize({ width: 390, height: 844 });
    const mobileLayout = await desktop.evaluate(() => {
      const rect = (element) => {
        const box = element.getBoundingClientRect();
        return { x: box.x, y: box.y, width: box.width, right: box.right };
      };
      return {
        viewport: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
        summary: [...document.querySelectorAll('.summary-card')].slice(0, 4).map(rect),
        products: [...document.querySelectorAll('#product-list article')].slice(0, 2).map(rect),
      };
    });
    assert.equal(mobileLayout.viewport, 390);
    assert.ok(mobileLayout.scroll <= 390, JSON.stringify(mobileLayout));
    assert.equal(new Set(mobileLayout.summary.map((box) => Math.round(box.x))).size, 2,
      JSON.stringify(mobileLayout));
    assert.equal(mobileLayout.products[0].x, mobileLayout.products[1].x,
      JSON.stringify(mobileLayout));
    assert.ok(mobileLayout.products[1].y > mobileLayout.products[0].y,
      JSON.stringify(mobileLayout));
    await desktop.screenshot({ path: `${evidenceDirectory}/dashboard-mobile.png`, fullPage: true });
    result.mobile = mobileLayout;

    await desktop.emulateMedia({ media: 'print' });
    const printDisplay = await desktop.evaluate(() => ({
      copyButton: getComputedStyle(document.querySelector('button[data-copy]')).display,
      reportButton: getComputedStyle(document.querySelector('button[data-report-action]')).display,
      card: getComputedStyle(document.querySelector('.product-card')).display,
      textarea: getComputedStyle(document.querySelector('.product-card textarea')).display,
    }));
    assert.equal(printDisplay.copyButton, 'none');
    assert.equal(printDisplay.reportButton, 'none');
    assert.notEqual(printDisplay.card, 'none');
    assert.notEqual(printDisplay.textarea, 'none');
    await desktop.pdf({ path: `${evidenceDirectory}/dashboard-print.pdf`, format: 'A4', printBackground: true });
    result.print = printDisplay;
    await desktop.close();

    const offlineContext = await browser.newContext();
    await offlineContext.route('**/api/state', (route) => route.abort());
    const offline = await offlineContext.newPage();
    trackRequests(offline);
    await offline.goto(origin, { waitUntil: 'domcontentloaded' });
    await offline.waitForFunction(() => document.querySelector('#connection-status')?.textContent
      .includes('保存サーバーに接続できません。'));
    assert.equal(await offline.locator('button[data-copy], button[data-report-action]').count(), 0);
    assert.equal(await offline.evaluate(() => localStorage.length), 0);
    result.offline = { actionControls: 0, browserStorageEntries: 0 };
    await offlineContext.close();

    for (const failure of [
      { name: 'revision', status: 409, error: 'stale_revision', expected: '別の報告が先に受け付けられました。' },
      { name: 'storage', status: 503, error: 'storage_unavailable', expected: '報告を保存できませんでした。' },
    ]) {
      const failureContext = await browser.newContext();
      await failureContext.route('**/api/reports', (route) => route.fulfill({
        status: failure.status,
        contentType: 'application/json',
        body: JSON.stringify({ saved: false, error: failure.error }),
      }));
      const failurePage = await failureContext.newPage();
      trackRequests(failurePage);
      await connected(failurePage);
      await reportButton(failurePage).click();
      await failurePage.waitForFunction((expected) => document.querySelector('#action-message')
        ?.textContent.includes(expected), failure.expected);
      result[failure.name] = { status: failure.status, messageObserved: true };
      await failureContext.close();
    }

    const postedContext = await browser.newContext();
    const posted = await postedContext.newPage();
    trackRequests(posted);
    await connected(posted);
    await reportButton(posted).click();
    await posted.waitForFunction(() => document.querySelector('#action-message')?.textContent
      .includes('報告を受け付けました。管理への反映は翌日の日次処理で行います。'));
    await posted.waitForFunction(() => document.querySelector('[data-pending-count]')?.textContent === '1');
    assert.equal(await posted.locator('#product-list').innerText().then((text) =>
      text.includes('報告受付済み・翌日管理反映待ち')), true);
    result.pending = { pendingCount: 1, generatedCount: 15, reportedPostedCount: 0 };
    await posted.screenshot({ path: `${evidenceDirectory}/dashboard-pending.png`, fullPage: true });
    await postedContext.close();
    assert.deepEqual(result.externalRequests, []);
  } else if (phase === 'applied') {
    const page = await context.newPage();
    trackRequests(page);
    await connected(page);
    assert.deepEqual(await page.locator('.summary-card strong').allTextContents(),
      ['14', '15', '1', '0']);
    assert.ok((await page.locator('#recent-reports').innerText()).includes('管理反映済み'));
    assert.equal(await page.locator('#product-list article').count(), 14);
    result.applied = { available: 14, generated: 15, posted: 1, pending: 0,
      appliedLabelVisible: true };
    await page.screenshot({ path: `${evidenceDirectory}/dashboard-applied.png`, fullPage: true });
    assert.deepEqual(result.externalRequests, []);
  } else {
    throw new Error(`unsupported phase: ${phase}`);
  }

  process.stdout.write(JSON.stringify(result));
} finally {
  await browser.close();
}
"""


def _connect_database(path: Path) -> Any:
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

    return connect


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


def _run_browser_phase(origin: str, evidence: Path, phase: str) -> dict[str, Any]:
    executable = next((path for path in CHROME_CANDIDATES if path.is_file()), None)
    if executable is None:
        pytest.skip("ローカルに確認済みのChromeまたはEdge実行ファイルがありません")
    script_path = evidence / "dashboard-browser-test.mjs"
    script_path.write_text(_BROWSER_SCRIPT, encoding="utf-8")
    result = subprocess.run(
        [
            "node",
            str(script_path),
            origin,
            str(evidence),
            phase,
            str(executable),
            str(ROOT / "node_modules" / "playwright-core" / "index.mjs"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Playwright dashboard phase failed: "
            + result.stderr[-4000:]
            + result.stdout[-1000:]
        )
    return json.loads(result.stdout)


def test_dashboard_actual_browser_fixture_lifecycle(tmp_path: Path) -> None:
    """Exercise the shipped UI at desktop/mobile/print, then apply a fake report."""

    database_path = tmp_path / "dashboard-e2e.sqlite"
    connect = _connect_database(database_path)
    connection = connect()
    try:
        _seed_fifteen_drafts(connection)
    finally:
        connection.close()

    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))
    evidence = tmp_path / "browser-evidence"
    evidence.mkdir()
    server = create_server(connection_factory=connect, clock=clock)
    server_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    server_thread.start()
    try:
        first = _run_browser_phase(server.origin, evidence, "main")
        assert first["desktop"]["cards"] == 15
        assert first["copy"] == {"copiedFixtureText": True, "reportSubmitted": False}
        assert first["mobile"]["scroll"] <= 390
        assert first["print"]["copyButton"] == "none"
        assert first["offline"]["actionControls"] == 0
        assert first["revision"]["messageObserved"] is True
        assert first["storage"]["messageObserved"] is True
        assert first["pending"] == {
            "pendingCount": 1,
            "generatedCount": 15,
            "reportedPostedCount": 0,
        }
        assert first["externalRequests"] == []

        connection = connect()
        try:
            action = connection.execute(
                "SELECT action, apply_status, draft_id, draft_version "
                "FROM human_action ORDER BY reported_at"
            ).fetchall()
            assert [tuple(row) for row in action] == [("posted", "pending", "D-A01", 1)]
            assert (
                connection.execute("SELECT COUNT(*) FROM publication_event").fetchone()[
                    0
                ]
                == 0
            )
            assert (
                connection.execute(
                    "SELECT committed_count FROM run WHERE run_id='R1'"
                ).fetchone()[0]
                == 15
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM reservation WHERE status='active'"
                ).fetchone()[0]
                == 15
            )

            _add_next_day_run(connection)
            clock.advance(15 * 60 * 60)
            applied = apply_pending_reports(connection, run_id="R2", clock=clock)
            assert applied.applied_action_ids
            assert applied.pending_action_ids == ()
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
                == 14
            )
            assert (
                connection.execute(
                    "SELECT committed_count FROM run WHERE run_id='R1'"
                ).fetchone()[0]
                == 15
            )
        finally:
            connection.close()

        second = _run_browser_phase(server.origin, evidence, "applied")
        assert second["applied"] == {
            "available": 14,
            "generated": 15,
            "posted": 1,
            "pending": 0,
            "appliedLabelVisible": True,
        }
        assert second["externalRequests"] == []
        evidence_summary = {
            "fixture_only": True,
            "server": "automation.inago.human_api.create_server",
            "screenshots_and_pdf": sorted(
                path.name
                for path in evidence.iterdir()
                if path.suffix in {".png", ".pdf"}
            ),
            "main": first,
            "applied": second,
            "database": {
                "pending_actions_before_apply": 1,
                "publication_events_after_apply": 1,
                "generated_count_unchanged": 15,
                "active_reservations_after_apply": 14,
            },
        }
        (evidence / "summary.json").write_text(
            json.dumps(evidence_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    finally:
        server.shutdown()
        server_thread.join(timeout=5)
        server.server_close()
        assert not server_thread.is_alive()
