"""Date-scoped, version-aware, directly opened posting history contracts."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from automation.inago.repository import initialize_database


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_VERSION = "fixture-history-html"
TIMESTAMP = "2026-10-15T13:00:00+09:00"
CHROME_CANDIDATES = (
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Microsoft/Edge/Application/msedge.exe",
)

_FILE_BROWSER_SCRIPT = r"""
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const [fileUrl, executablePath, playwrightModule, latestBody, oldBody] = process.argv.slice(2);
const { chromium } = await import(pathToFileURL(playwrightModule).href);
const browser = await chromium.launch({
  headless: true,
  executablePath,
  args: ['--no-first-run', '--no-default-browser-check'],
});
try {
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (text) => { window.__fixtureCopiedText = text; } },
    });
  });
  const page = await context.newPage();
  const networkRequests = [];
  const pageErrors = [];
  page.on('request', (request) => {
    if (/^https?:/u.test(request.url())) networkRequests.push(request.url());
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(fileUrl, { waitUntil: 'load' });
  assert.equal(new URL(page.url()).protocol, 'file:');
  assert.equal(await page.locator('nav[aria-label="日付を選択"] button[data-target-date]').count(), 35);
  assert.ok((await page.locator('nav').innerText()).includes('2026-09-11'));

  const selectDate = (value) => page.locator(`button[data-target-date="${value}"]`);
  await selectDate('2026-10-15').click();
  assert.equal(await page.locator('#selected-date').textContent(), '2026-10-15の投稿内容');
  assert.equal(await page.locator('#daily-count').textContent(), '1');
  assert.equal(await page.locator('.draft-card').count(), 1);
  assert.equal(await page.locator('.body-text').first().inputValue(), latestBody);
  assert.equal(await page.locator('img').count(), 0);
  assert.equal(await page.locator('.draft-card').first().getAttribute('data-draft-id'), 'D-HISTORY-LATEST');
  await page.locator('button[data-copy-draft-id="D-HISTORY-LATEST"][data-copy-version="2"]').click();
  assert.equal(await page.evaluate(() => window.__fixtureCopiedText), latestBody);

  await selectDate('2026-09-14').click();
  assert.equal(await page.locator('#daily-count').textContent(), '1');
  assert.equal(await page.locator('.body-text').first().inputValue(), oldBody);
  assert.equal(await page.locator('.draft-card').first().getAttribute('data-draft-id'), 'D-HISTORY-OLD');
  await page.locator('button[data-copy-draft-id="D-HISTORY-OLD"][data-copy-version="1"]').click();
  assert.equal(await page.evaluate(() => window.__fixtureCopiedText), oldBody);

  await selectDate('2026-10-12').click();
  assert.equal(await page.locator('#daily-count').textContent(), '0');
  assert.equal(await page.locator('.draft-card').count(), 0);
  assert.ok((await page.locator('#history-message').textContent()).includes('本文はありません'));

  await selectDate('2026-10-15').click();
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileWidth = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  assert.equal(mobileWidth.viewport, 390);
  assert.ok(mobileWidth.scroll <= 390, JSON.stringify(mobileWidth));
  await page.emulateMedia({ media: 'print' });
  const printStyle = await page.locator('button[data-copy-draft-id]').evaluate((element) =>
    getComputedStyle(element).display);
  assert.equal(printStyle, 'none');
  assert.notEqual(await page.locator('.draft-card').first().evaluate((element) =>
    getComputedStyle(element).display), 'none');
  assert.deepEqual(networkRequests, []);
  assert.deepEqual(pageErrors, []);

  const failureContext = await browser.newContext();
  await failureContext.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async () => { throw new Error('clipboard denied'); } },
    });
  });
  const failurePage = await failureContext.newPage();
  failurePage.on('request', (request) => {
    if (/^https?:/u.test(request.url())) networkRequests.push(request.url());
  });
  failurePage.on('pageerror', (error) => pageErrors.push(error.message));
  await failurePage.goto(fileUrl, { waitUntil: 'load' });
  await failurePage.locator('button[data-target-date="2026-10-15"]').click();
  await failurePage.locator('button[data-copy-draft-id="D-HISTORY-LATEST"]').click();
  await failurePage.waitForFunction(() => document.querySelector('#history-message')
    ?.textContent.includes('Ctrl+C'));
  const fallback = await failurePage.locator('.body-text').first().evaluate((element) => ({
    selected: element.selectionStart === 0 && element.selectionEnd === element.value.length,
    active: document.activeElement === element,
  }));
  assert.deepEqual(fallback, { selected: true, active: true });
  assert.deepEqual(networkRequests, []);
  assert.deepEqual(pageErrors, []);
  process.stdout.write(JSON.stringify({
    fileProtocol: true,
    dates: 35,
    selectedCopies: 2,
    emptyDateCount: 0,
    mobileWidth,
    printCopyButton: printStyle,
    clipboardFallback: fallback,
    networkRequests: networkRequests.length,
    pageErrors: pageErrors.length,
  }));
} finally {
  await browser.close();
}
"""


@pytest.fixture
def history_connection():
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
        (SETTINGS_VERSION, TIMESTAMP),
    )
    try:
        yield connection
    finally:
        connection.close()


def _load_history_api():
    from automation.inago.dashboard_history import (
        DashboardHistoryExportError,
        render_dashboard_history_html,
        write_dashboard_history_html,
    )

    return (
        DashboardHistoryExportError,
        render_dashboard_history_html,
        write_dashboard_history_html,
    )


def _insert_run(
    connection: sqlite3.Connection,
    target_date: str,
    *,
    status: str = "complete",
    committed_count: int = 0,
) -> str:
    run_id = f"RUN-HISTORY-{target_date}"
    started_at = f"{target_date}T13:00:00+09:00"
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, ?, 'daily', ?, ?, ?, ?, ?, ?, 0, ?, NULL, 'not_started', ?)
        """,
        (
            run_id,
            target_date,
            status,
            SETTINGS_VERSION,
            f"seed-{target_date}",
            started_at,
            f"{target_date}T17:45:00+09:00",
            f"{target_date}T18:00:00+09:00",
            committed_count,
            f"{target_date}T18:00:00+09:00",
        ),
    )
    return run_id


def _insert_offer(
    connection: sqlite3.Connection,
    *,
    offer_key: str,
    item_id: str,
    name: str,
    url: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES (?, 'shop_history', ?, ?, ?, '[]', 'G01-T01', '[]', ?, ?, '[]')
        """,
        (offer_key, item_id, name, url, TIMESTAMP, TIMESTAMP),
    )


def _insert_draft(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    offer_key: str,
    draft_id: str,
    version: int,
    body: str,
    status: str,
) -> None:
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'fixture-copy-1', 'confirmed-0.3',
                ?, NULL, 'P01', '[]')
        """,
        (draft_id, offer_key, run_id, version, body, TIMESTAMP, TIMESTAMP, status),
    )


def _embedded_history(html: str) -> dict[str, object]:
    import re

    match = re.search(
        r'<script id="dashboard-history-data" type="application/json">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def _seed_history_fixture(connection: sqlite3.Connection) -> tuple[str, str]:
    latest_body = '最新本文🦗\n</script><img src=x onerror=alert(1)>'
    old_body = "31日より前の過去版本文"
    shared_offer = "shop_history:item_shared"
    _insert_offer(
        connection,
        offer_key=shared_offer,
        item_id="item_shared",
        name="同一商品・匿名fixture",
        url="https://item.rakuten.co.jp/shop_history/shared/",
    )
    dates = [date(2026, 10, 15) - timedelta(days=offset) for offset in range(35)]
    run_ids: dict[str, str] = {}
    for day in dates:
        day_text = day.isoformat()
        is_partial = day_text == "2026-10-15"
        is_empty = day_text == "2026-10-12"
        run_ids[day_text] = _insert_run(
            connection,
            day_text,
            status="partial" if is_partial else "shortfall" if is_empty else "complete",
            committed_count=0 if is_empty else 1 if day_text in {
                "2026-10-15", "2026-10-14", "2026-09-14", "2026-09-11"
            } else 0,
        )

    _insert_draft(
        connection,
        run_id=run_ids["2026-10-15"],
        offer_key=shared_offer,
        draft_id="D-HISTORY-LATEST",
        version=2,
        body=latest_body,
        status="human_edited",
    )
    _insert_offer(
        connection,
        offer_key="shop_history:item_skipped",
        item_id="item_skipped",
        name="見送り済みfixture商品",
        url="https://item.rakuten.co.jp/shop_history/skipped/",
    )
    _insert_draft(
        connection,
        run_id=run_ids["2026-10-14"],
        offer_key="shop_history:item_skipped",
        draft_id="D-HISTORY-SKIPPED",
        version=1,
        body="見送り報告済み本文",
        status="skipped",
    )
    _insert_offer(
        connection,
        offer_key="shop_history:item-excluded",
        item_id="item-excluded",
        name="投稿対象外fixture商品",
        url="https://item.rakuten.co.jp/shop_history/excluded/",
    )
    _insert_draft(
        connection,
        run_id=run_ids["2026-09-11"],
        offer_key="shop_history:item-excluded",
        draft_id="D-HISTORY-EXCLUDED",
        version=1,
        body="既投稿のため対象外の本文",
        status="do_not_post_already_posted",
    )
    _insert_draft(
        connection,
        run_id=run_ids["2026-09-14"],
        offer_key=shared_offer,
        draft_id="D-HISTORY-OLD",
        version=1,
        body=old_body,
        status="posted",
    )
    return latest_body, old_body


def test_history_export_keeps_zero_days_old_runs_and_distinct_versions(
    history_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    _, _, write_history = _load_history_api()
    latest_body, old_body = _seed_history_fixture(history_connection)
    changes_before = history_connection.total_changes
    output_path = tmp_path / "posting-history.html"

    write_history(
        history_connection,
        output_path=output_path,
        runtime_root=tmp_path,
    )

    html = output_path.read_text(encoding="utf-8")
    payload = _embedded_history(html)
    assert history_connection.total_changes == changes_before
    days = payload["days"]
    assert len(days) == 35
    assert days[0]["target_date"] == "2026-10-15"
    assert days[-1]["target_date"] == "2026-09-11"
    latest = days[0]
    assert latest["run_status"] == "一部完了"
    assert latest["item_count"] == 1
    assert latest["items"][0] == {
        "draft_id": "D-HISTORY-LATEST",
        "draft_version": 2,
        "product_name": "同一商品・匿名fixture",
        "product_url": "https://item.rakuten.co.jp/shop_history/shared/",
        "body": latest_body,
        "status": "本人編集済み",
        "target_date": "2026-10-15",
        "offer_key": "shop_history:item_shared",
        "receipt_revision": 0,
        "events": [],
    }
    by_date = {day["target_date"]: day for day in days}
    assert by_date["2026-09-14"]["items"][0]["draft_id"] == "D-HISTORY-OLD"
    assert by_date["2026-09-14"]["items"][0]["draft_version"] == 1
    assert by_date["2026-09-14"]["items"][0]["body"] == old_body
    assert by_date["2026-09-14"]["items"][0]["status"] == "投稿反映済み"
    assert by_date["2026-10-14"]["items"][0]["status"] == "見送り反映済み"
    assert by_date["2026-09-11"]["items"][0]["status"] == "既投稿のため後続版は投稿対象外"
    assert by_date["2026-10-12"]["item_count"] == 0
    assert by_date["2026-10-12"]["items"] == []
    assert "D-HISTORY-OLD" in html and "D-HISTORY-LATEST" in html
    assert "</script><img" not in html


def test_history_renderer_has_date_sidebar_and_embedded_assets() -> None:
    _, render_history, _ = _load_history_api()
    html = render_history(
        {
            "days": [
                {
                    "target_date": "2026-10-15",
                    "run_status": "complete",
                    "items": [],
                },
                {
                    "target_date": "2026-10-14",
                    "run_status": "complete",
                    "items": [],
                },
            ]
        }
    )

    assert '<nav id="date-navigation" aria-label="日付を選択">' in html
    assert "data-target-date" in html
    assert 'id="dashboard-history-data"' in html
    assert "<style>" in html and "<script src=" not in html
    assert "<link " not in html
    assert "fetch(`${API_ORIGIN}${path}`" in html


def test_browser_selects_and_copies_the_exact_date_version_from_file(
    history_connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _, _, write_history = _load_history_api()
    executable = next((path for path in CHROME_CANDIDATES if path.is_file()), None)
    node = shutil.which("node")
    playwright_module = PROJECT_ROOT / "node_modules" / "playwright-core" / "index.mjs"
    if executable is None or node is None or not playwright_module.is_file():
        pytest.skip("Chrome/Edge, Node.js, or playwright-core is unavailable")
    latest_body, old_body = _seed_history_fixture(history_connection)
    output_path = tmp_path / "posting-history.html"
    write_history(
        history_connection,
        output_path=output_path,
        runtime_root=tmp_path,
    )
    script_path = tmp_path / "history-file-browser-test.mjs"
    script_path.write_text(_FILE_BROWSER_SCRIPT, encoding="utf-8")
    file_url = output_path.resolve().as_uri()
    result = subprocess.run(
        [
            node,
            str(script_path),
            file_url,
            str(executable),
            str(playwright_module),
            latest_body,
            old_body,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-1000:]
    browser_result = json.loads(result.stdout)
    assert browser_result["fileProtocol"] is True
    assert browser_result["dates"] == 35
    assert browser_result["selectedCopies"] == 2
    assert browser_result["emptyDateCount"] == 0
    assert browser_result["mobileWidth"] == {"viewport": 390, "scroll": 390}
    assert browser_result["printCopyButton"] == "none"
    assert browser_result["clipboardFallback"] == {"selected": True, "active": True}
    assert browser_result["networkRequests"] == 0
    assert browser_result["pageErrors"] == 0
