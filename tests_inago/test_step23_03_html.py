"""Standalone, file-openable posting dashboard export contracts."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from automation.inago.repository import initialize_database


SETTINGS_VERSION = "fixture-standalone-html"
TIMESTAMP = "2026-10-15T13:00:00+09:00"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
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

const [fileUrl, executablePath, playwrightModule, expectedBody] = process.argv.slice(2);
const { chromium } = await import(pathToFileURL(playwrightModule).href);
const browser = await chromium.launch({
  headless: true,
  executablePath,
  args: ['--no-first-run', '--no-default-browser-check'],
});
try {
  const page = await browser.newPage();
  const networkRequests = [];
  const pageErrors = [];
  page.on('request', (request) => {
    if (/^https?:/u.test(request.url())) networkRequests.push(request.url());
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(fileUrl, { waitUntil: 'load' });
  const rendered = await page.evaluate(() => ({
    protocol: location.protocol,
    title: document.title,
    count: document.querySelector('#draft-count')?.textContent,
    cards: document.querySelectorAll('#draft-list .draft-card').length,
    body: document.querySelector('#draft-list .body-text')?.textContent,
    productName: document.querySelector('#draft-list h2')?.textContent,
    linkCount: document.querySelectorAll('#draft-list a').length,
    injectedImages: document.querySelectorAll('#draft-list img').length,
    eventAttributes: [...document.querySelectorAll('*')]
      .flatMap((element) => [...element.attributes])
      .filter((attribute) => /^on/iu.test(attribute.name)).length,
  }));
  assert.equal(rendered.protocol, 'file:');
  assert.equal(rendered.title, 'イナゴスキル 投稿下書き');
  assert.equal(rendered.count, '1');
  assert.equal(rendered.cards, 1);
  assert.equal(rendered.body, expectedBody);
  assert.equal(rendered.productName, '匿名fixture商品1');
  assert.equal(rendered.linkCount, 1);
  assert.equal(rendered.injectedImages, 0);
  assert.equal(rendered.eventAttributes, 0);
  assert.deepEqual(networkRequests, []);
  assert.deepEqual(pageErrors, []);
  process.stdout.write(JSON.stringify({ fileProtocol: true, bodyVisible: true,
    networkRequests: networkRequests.length, pageErrors: pageErrors.length }));
} finally {
  await browser.close();
}
"""


@pytest.fixture
def dashboard_connection():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    initialize_database(connection)
    try:
        yield connection
    finally:
        connection.close()


def _load_export_api():
    from automation.inago.dashboard_export import (
        DashboardExportError,
        write_standalone_dashboard_html,
    )

    return DashboardExportError, write_standalone_dashboard_html


def _seed_daily_run(connection: sqlite3.Connection, *, committed_count: int) -> None:
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
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('RUN-HTML', '2026-10-15', 'daily', 'complete', ?, 'seed-html',
                ?, '2026-10-15T17:45:00+09:00', '2026-10-15T18:00:00+09:00',
                0, ?, NULL, 'not_started', ?)
        """,
        (SETTINGS_VERSION, TIMESTAMP, committed_count, TIMESTAMP),
    )


def _insert_draft(
    connection: sqlite3.Connection,
    *,
    number: int,
    status: str,
    body: str,
    url: str,
    reservation_status: str = "active",
) -> None:
    suffix = f"{number:02d}"
    offer_key = f"shop_test:item_{suffix}"
    draft_id = f"D-HTML-{suffix}"
    connection.execute(
        """
        INSERT INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES (?, 'shop_test', ?, ?, ?, '[]', 'G01-T01', '[]', ?, ?, '[]')
        """,
        (offer_key, f"item_{suffix}", f"匿名fixture商品{number}", url, TIMESTAMP, TIMESTAMP),
    )
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES (?, ?, 'RUN-HTML', 1, ?, ?, ?, 'fixture-copy-1', 'confirmed-0.3',
                ?, NULL, 'P01', '[]')
        """,
        (draft_id, offer_key, body, TIMESTAMP, TIMESTAMP, status),
    )
    released = reservation_status == "released"
    connection.execute(
        """
        INSERT INTO reservation
            (reservation_id, offer_key, draft_id, status, created_at,
             released_at, release_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"RES-HTML-{suffix}",
            offer_key,
            draft_id,
            reservation_status,
            TIMESTAMP,
            TIMESTAMP if released else None,
            "skipped" if released else None,
        ),
    )


def _embedded_data(html: str) -> dict[str, object]:
    match = re.search(
        r'<script id="dashboard-data" type="application/json">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


class _ElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))


def test_export_embeds_only_confirmed_drafts_and_escapes_untrusted_text(
    dashboard_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    _, write_dashboard = _load_export_api()
    _seed_daily_run(dashboard_connection, committed_count=2)
    body = '絵文字🦗\n改行 & "引用" </script><img src=x onerror=alert(1)>'
    _insert_draft(
        dashboard_connection,
        number=1,
        status="committed",
        body=body,
        url="https://item.rakuten.co.jp/shop/item/?from=dashboard#private-fragment",
    )
    _insert_draft(
        dashboard_connection,
        number=2,
        status="human_edited",
        body="本人が確認した本文",
        url="javascript:alert(1)",
    )
    _insert_draft(
        dashboard_connection,
        number=3,
        status="skipped",
        body="見送り済み本文は対象外",
        url="https://item.rakuten.co.jp/shop/skipped/",
        reservation_status="released",
    )
    changes_before = dashboard_connection.total_changes

    output_path = tmp_path / "posting-dashboard.html"
    write_dashboard(
        dashboard_connection,
        output_path=output_path,
        runtime_root=tmp_path,
    )

    html = output_path.read_text(encoding="utf-8")
    payload = _embedded_data(html)
    assert dashboard_connection.total_changes == changes_before
    assert set(payload) == {"run_date", "items"}
    assert payload["run_date"] == "2026-10-15"
    assert len(payload["items"]) == 2
    assert payload["items"][0] == {
        "product_name": "匿名fixture商品1",
        "product_url": "https://item.rakuten.co.jp/shop/item/?from=dashboard",
        "body": body,
        "status": "保存済み下書き",
        "draft_version": 1,
        "target_date": "2026-10-15",
    }
    assert payload["items"][1]["product_url"] is None
    assert payload["items"][1]["status"] == "本人編集済み"
    assert "offer_key" not in html
    assert "draft_id" not in html
    assert "order_count" not in html
    assert "access_token" not in html
    assert "</script><img" not in html

    elements = _ElementCollector()
    elements.feed(html)
    assert all(tag != "img" for tag, _ in elements.tags)
    assert all(
        not (attribute or "").casefold().startswith("on")
        for _, attrs in elements.tags
        for attribute, _ in attrs
    )
    assert "<style>" in html and "<script src=" not in html
    assert "<link " not in html and "fetch(" not in html
    assert "data.items.length" in html


def test_empty_database_exports_a_truthful_zero_item_page(
    dashboard_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    _, write_dashboard = _load_export_api()
    output_path = tmp_path / "empty-dashboard.html"

    write_dashboard(
        dashboard_connection,
        output_path=output_path,
        runtime_root=tmp_path,
    )

    html = output_path.read_text(encoding="utf-8")
    payload = _embedded_data(html)
    assert payload == {"run_date": None, "items": []}
    assert 'id="draft-count">0<' in html
    assert "保存済みの本文はありません" in html


def test_atomic_replace_failure_preserves_previous_html_and_cleans_temp_files(
    dashboard_connection: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automation.inago import dashboard_export

    _, write_dashboard = _load_export_api()
    output_path = tmp_path / "posting-dashboard.html"
    output_path.write_text("previous-good-dashboard", encoding="utf-8")

    def fail_replace(source: str | Path, target: str | Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(dashboard_export.os, "replace", fail_replace)
    with pytest.raises(dashboard_export.DashboardExportError):
        write_dashboard(
            dashboard_connection,
            output_path=output_path,
            runtime_root=tmp_path,
        )

    assert output_path.read_text(encoding="utf-8") == "previous-good-dashboard"
    assert list(tmp_path.glob(".posting-dashboard.html.*.tmp")) == []


def test_export_rejects_output_path_outside_runtime(
    dashboard_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    error_type, write_dashboard = _load_export_api()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.html"

    with pytest.raises(error_type):
        write_dashboard(
            dashboard_connection,
            output_path=outside,
            runtime_root=tmp_path,
        )

    assert not outside.exists()


def test_export_rejects_runtime_reparse_paths(
    dashboard_connection: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automation.inago import dashboard_export

    error_type, write_dashboard = _load_export_api()
    monkeypatch.setattr(dashboard_export, "_has_reparse_point", lambda _path: True)

    with pytest.raises(error_type):
        write_dashboard(
            dashboard_connection,
            output_path=tmp_path / "posting-dashboard.html",
            runtime_root=tmp_path,
        )

    assert not (tmp_path / "posting-dashboard.html").exists()


def test_reparse_detector_checks_regular_paths_and_symlinks(tmp_path: Path) -> None:
    from automation.inago.dashboard_export import _has_reparse_point

    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    assert not _has_reparse_point(ordinary)

    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    assert _has_reparse_point(link)
    assert _has_reparse_point(link / "nested" / "dashboard.html")


def test_browser_opens_generated_html_directly_from_file_url(
    dashboard_connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _, write_dashboard = _load_export_api()
    executable = next((path for path in CHROME_CANDIDATES if path.is_file()), None)
    node = shutil.which("node")
    playwright_module = PROJECT_ROOT / "node_modules" / "playwright-core" / "index.mjs"
    if executable is None or node is None or not playwright_module.is_file():
        pytest.skip("Chrome/Edge, Node.js, or playwright-core is unavailable")

    _seed_daily_run(dashboard_connection, committed_count=1)
    body = '本文🦗\n"記号" & </script><img src=x onerror=alert(1)>'
    _insert_draft(
        dashboard_connection,
        number=1,
        status="committed",
        body=body,
        url="https://item.rakuten.co.jp/shop/item/",
    )
    output_path = tmp_path / "posting-dashboard.html"
    write_dashboard(
        dashboard_connection,
        output_path=output_path,
        runtime_root=tmp_path,
    )

    script_path = tmp_path / "open-file-dashboard.mjs"
    script_path.write_text(_FILE_BROWSER_SCRIPT, encoding="utf-8")
    file_url = output_path.resolve().as_uri()
    result = subprocess.run(
        [
            node,
            str(script_path),
            file_url,
            str(executable),
            str(playwright_module),
            body,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-1000:]
    assert json.loads(result.stdout) == {
        "fileProtocol": True,
        "bodyVisible": True,
        "networkRequests": 0,
        "pageErrors": 0,
    }
