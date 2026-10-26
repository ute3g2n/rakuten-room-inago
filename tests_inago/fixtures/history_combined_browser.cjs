const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
const path = require('node:path');

(async () => {
  const evidence = process.env.INAGO_TEST_HISTORY_EVIDENCE;
  const mode = process.env.INAGO_TEST_COMBINED_MODE;
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.INAGO_TEST_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, recordVideo: { dir: evidence } });
  const origin = new URL(process.env.INAGO_TEST_HISTORY_URL).origin;
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
  await context.route('https://example.invalid/**', (route) => route.fulfill({
    contentType: 'text/html', body: '<!doctype html><title>Isolated product fixture</title><p>Product fixture</p>',
  }));
  if (mode === 'clipboard-failure') {
    await context.addInitScript(() => {
      navigator.clipboard.writeText = async () => { throw new Error('fixture clipboard unavailable'); };
    });
  }
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  const page = await context.newPage();
  const card = page.locator('article[data-draft-id="D-A01"]');
  const copy = card.locator('button[data-copy-draft-id]');
  const combined = card.getByRole('button', { name: 'コピー+チェック+遷移', exact: true });
  const checked = card.locator('input[data-report-posted-draft-id]');
  const until = async (condition) => {
    const deadline = Date.now() + 4000;
    while (!(await condition())) {
      assert(Date.now() < deadline, 'combined action did not reach its expected state');
      await page.waitForTimeout(30);
    }
  };
  const shot = async (name) => {
    await copy.evaluate((node) => node.scrollIntoView({ block: 'center' }));
    await page.screenshot({ path: path.join(evidence, name) });
  };
  let tabs = 0;
  try {
    await page.goto(process.env.INAGO_TEST_HISTORY_URL);
    await page.locator('[data-history-connection="ready"]').waitFor();
    await page.locator('button[data-target-date="2026-10-12"]').click();
    await combined.waitFor({ timeout: 1500 });
    await shot('01-before-combined.png');
    const a = await copy.boundingBox();
    const b = await combined.boundingBox();
    assert(b.x - a.x - a.width >= 15.5 && Math.abs(a.y - b.y) < 2, 'buttons must have a 16px gap');
    const copyColor = await copy.evaluate((node) => getComputedStyle(node).backgroundColor);
    const combinedColor = await combined.evaluate((node) => getComputedStyle(node).backgroundColor);
    assert.notEqual(combinedColor, copyColor, 'combined action must have its own non-green color');
    if (mode === 'missing-url') {
      assert(await combined.isDisabled());
      await copy.click();
      assert.equal(await checked.isChecked(), false);
      console.log(JSON.stringify({ mode, new_tabs: 0 }));
      return;
    }
    const expectedBody = await card.locator('textarea.body-text').inputValue();
    if (mode === 'success') {
      await copy.click();
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), expectedBody);
      assert.equal(await checked.isChecked(), false);
      const state = await (await page.request.get(origin + '/api/history-state')).json();
      assert.equal(state.pending_reports.length, 0, 'ordinary copy must not submit a report');
    }
    if (mode === 'offline') {
      await page.route('**/api/reports', (route) => route.fulfill({
        status: 503, contentType: 'application/json', body: '{"error":"storage_unavailable"}',
      }));
    }
    const openCombined = async () => {
      await page.bringToFront();
      const opened = page.waitForEvent('popup');
      await combined.click();
      const product = await opened;
      tabs += 1;
      await product.waitForURL('https://example.invalid/combined-product', { waitUntil: 'domcontentloaded' });
      assert.equal(product.url(), 'https://example.invalid/combined-product');
      assert.equal(await product.evaluate(() => window.opener), null);
      await product.close();
      await page.bringToFront();
    };
    await openCombined();
    await until(() => checked.isChecked());
    if (mode === 'offline') {
      await until(async () => (await page.locator('#outbox-count').textContent()).includes('1件'));
      await page.unroute('**/api/reports');
      await page.reload();
      await page.locator('[data-history-connection="ready"]').waitFor();
      await page.locator('button[data-target-date="2026-10-12"]').click();
    }
    await until(async () => (await card.locator('[data-report-status]').textContent()).includes('SQLite受付済み'));
    if (mode !== 'clipboard-failure') {
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), expectedBody);
    } else {
      await until(async () => (await page.locator('#history-message').textContent()).includes('コピーできません'));
      await page.waitForTimeout(350); // Include the SQLite/SSE redraw after receipt.
      const selection = await card.locator('textarea.body-text').evaluate((node) => ({
        focused: document.activeElement === node, start: node.selectionStart, end: node.selectionEnd,
      }));
      assert.deepEqual(selection, { focused: true, start: 0, end: expectedBody.length });
    }
    await shot('02-after-combined.png');
    if (mode === 'success') {
      await openCombined();
      assert(await checked.isChecked(), 'ON must never become OFF after a repeated combined click');
      await until(async () => (await page.locator('#history-message').textContent()).includes('ONのまま'));
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), expectedBody);
      await shot('03-already-on-preserved.png');
    }
    console.log(JSON.stringify({ mode, new_tabs: tabs }));
  } catch (error) {
    await page.screenshot({ path: path.join(evidence, 'failure.png') }).catch(() => {});
    throw error;
  } finally {
    await context.tracing.stop({ path: path.join(evidence, 'trace.zip') });
    await context.close();
    await browser.close();
  }
})().catch((error) => { console.error(error.stack); process.exitCode = 1; });
