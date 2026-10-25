from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "automation" / "inago" / "adapters" / "product_page_inputs.mjs"
FIXTURE = ROOT / "tests_inago" / "fixtures" / "product-page-item-desc.html"
EVIDENCE = ROOT / "worklogs" / "projects" / "implementation" / "item-desc-capture-20261025" / "C03" / "evidence"
CHROME_CANDIDATES = (
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Microsoft/Edge/Application/msedge.exe",
)


_BROWSER_RUNNER = r"""
import assert from 'node:assert/strict';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const [fixturePath, evidenceDirectory, productRoot, executablePath, playwrightModule, modulePath] = process.argv.slice(1);
const { chromium } = await import(pathToFileURL(playwrightModule).href);
const { saveProductPageInputs } = await import(pathToFileURL(modulePath).href);
const videoDirectory = path.join(evidenceDirectory, 'video');
await mkdir(videoDirectory, { recursive: true });
await mkdir(productRoot, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath, args: ['--disable-gpu', '--no-sandbox'] });
const context = await browser.newContext({
  viewport: { width: 1280, height: 900 },
  recordVideo: { dir: videoDirectory, size: { width: 1280, height: 900 } },
});
await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
const page = await context.newPage();
const video = page.video();
const recordedVideoPath = video ? await video.path() : null;
const beforePath = path.join(evidenceDirectory, 'before.png');
const afterPath = path.join(evidenceDirectory, 'after.png');
const tracePath = path.join(evidenceDirectory, 'trace.zip');
const videoPath = path.join(evidenceDirectory, 'item-desc-capture.webm');
let output;
try {
  await page.goto(pathToFileURL(fixturePath).href, { waitUntil: 'load' });
  await page.waitForFunction(() => window.fixtureReady === true);
  await page.screenshot({ path: beforePath });
  const result = await saveProductPageInputs({
    page,
    product: { shop: 'fixture-shop', item: 'browser-item-desc', url: page.url() },
    request: { run_id: 'CAP-BROWSER-ITEM-DESC', attempt_id: 'CAP-BROWSER-ITEM-DESC-ATTEMPT' },
    rowIndex: 1,
    config: { product_page_root: productRoot },
    resolveImageHost: async () => [{ address: '93.184.216.34' }],
  });
  await page.waitForFunction(() => document.querySelector('#item-lazy').dataset.loaded === 'true' && document.querySelector('#item-late'));
  await page.screenshot({ path: afterPath });
  const productDirectory = path.join(productRoot, 'CAP-BROWSER-ITEM-DESC', 'shop-Zml4dHVyZS1zaG9w', 'item-YnJvd3Nlci1pdGVtLWRlc2M');
  const manifest = JSON.parse(await readFile(path.join(productDirectory, 'page.json'), 'utf8'));
  const bytesByFile = {};
  for (const image of manifest.images)
    if (image.file) bytesByFile[image.file] = (await readFile(path.join(productDirectory, image.file))).toString('base64');
  assert.equal(result.state, 'captured');
  assert.equal(manifest.selectors.at(-1).selector, 'span.item_desc');
  assert.match(manifest.selectors.at(-1).outer_html[0], /item_desc lazy fixture text/);
  assert.match(manifest.selectors.at(-1).outer_html[0], /data-loaded="true"/);
  assert.match(manifest.selectors.at(-1).outer_html[0], /item-late/);
  assert.equal(manifest.lazy_load_state, 'settled');
  assert.equal(manifest.images.length, 3);
  assert.deepEqual(manifest.images.map(image => image.status), ['downloaded', 'downloaded', 'downloaded']);
  const dataImage = manifest.images.find(image => image.source_kind === 'data');
  const blobImage = manifest.images.find(image => image.source_kind === 'blob');
  const blobSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><circle cx="12" cy="12" r="10" fill="#d14c7b"/></svg>';
  assert.ok(dataImage && blobImage);
  assert.ok(dataImage.occurrences.some(item => item.selector === 'span.item_desc'));
  assert.ok(blobImage.occurrences.some(item => item.selector === 'span.item_desc'));
  const lateImage = manifest.images.find(image => image.occurrences.some(item => item.dom_position > 2 && item.selector === 'span.item_desc'));
  assert.ok(lateImage);
  assert.ok(bytesByFile[dataImage.file]);
  assert.equal(bytesByFile[blobImage.file], Buffer.from(blobSvg, 'utf8').toString('base64'));
  output = { browser_version: browser.version(), result, manifest, bytesByFile, screenshots: [beforePath, afterPath], trace: tracePath, video: videoPath };
} finally {
  await context.tracing.stop({ path: tracePath }).catch(() => {});
  await context.close();
  if (video) {
    await video.saveAs(videoPath).catch(() => {});
    const videoRoot = `${path.resolve(videoDirectory)}${path.sep}`;
    if (recordedVideoPath && path.resolve(recordedVideoPath).startsWith(videoRoot))
      await rm(recordedVideoPath, { force: true }).catch(() => {});
  }
  await browser.close();
}
await writeFile(path.join(evidenceDirectory, 'browser-result.json'), JSON.stringify(output, null, 2) + '\n', 'utf8');
console.log(JSON.stringify(output));
"""


def test_item_desc_lazy_images_are_captured_with_visual_evidence(tmp_path: Path) -> None:
    executable = next((path for path in CHROME_CANDIDATES if path.is_file()), None)
    node = shutil.which("node")
    playwright_module = ROOT / "node_modules" / "playwright-core" / "index.mjs"
    if executable is None or node is None or not playwright_module.is_file():
        reason = "local Chrome/Edge, Node.js, or playwright-core is unavailable"
        pytest.skip(reason)

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            _BROWSER_RUNNER,
            str(FIXTURE),
            str(EVIDENCE),
            str(tmp_path / "product-page-root"),
            str(executable),
            str(playwright_module),
            str(MODULE),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-5000:] + completed.stdout[-1000:]
    result = json.loads(completed.stdout)
    assert result["manifest"]["selectors"][-1]["selector"] == "span.item_desc"
    assert len(result["manifest"]["images"]) == 3
    assert all((EVIDENCE / filename).is_file() for filename in (
        "before.png",
        "after.png",
        "trace.zip",
        "item-desc-capture.webm",
        "browser-result.json",
    ))
