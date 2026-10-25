// Clean browser integration against the isolated local history server.
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
const readline = require('node:readline');
const path = require('node:path');

(async () => {
  const evidence = process.env.INAGO_TEST_HISTORY_EVIDENCE;
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.INAGO_TEST_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, recordVideo: { dir: evidence } });
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  const page = await context.newPage();
  const ready = async (target) => target.locator('[data-history-connection="ready"]').waitFor({ timeout: 15000 });
  const selectOldDay = async (target) => target.locator('button[data-target-date="2026-10-12"]').click();
  const checkbox = (target, id) => target.locator(`input[data-report-posted-draft-id="${id}"]`);
  const shot = async (name, id = 'D-A01') => {
    const control = checkbox(page, id);
    if (await control.count()) await control.evaluate((node) => node.scrollIntoView({ block: 'center' }));
    // SSE can replace a card while locator.screenshot waits for layout stability.
    await page.screenshot({ path: path.join(evidence, name), fullPage: false });
  };
  const until = async (condition, timeout = 3000) => {
    const deadline = Date.now() + timeout;
    while (!(await condition())) {
      assert(Date.now() < deadline, 'history state did not synchronize before deadline');
      await page.waitForTimeout(50);
    }
  };
  try {
    await page.goto(process.env.INAGO_TEST_HISTORY_URL);
    await ready(page);
    assert.equal(new URL(page.url()).hash, '');
    await selectOldDay(page);
    await shot('01-before-check.png');
    if (process.env.INAGO_TEST_HISTORY_MODE === 'rejected-outbox') {
      const origin = new URL(page.url()).origin;
      const snapshot = await (await page.request.get(origin + '/api/history-state')).json();
      await page.route('**/api/history-state', (route) => route.fulfill({ json: snapshot }));
      await page.route('**/api/reports', (route) => route.fulfill({
        status: 503, contentType: 'application/json', body: '{"error":"storage_unavailable"}',
      }));
      await checkbox(page, 'D-A01').check();
      await until(async () => (await page.locator('#outbox-count').textContent()).includes('1件'));
      const other = await page.request.post(origin + '/api/reports', {
        headers: { Origin: origin, 'X-CSRF-Token': snapshot.csrf_token },
        data: { action_id: 'ACT-OTHER-TAB', offer_key: 'shop_test:item_A01', draft_id: 'D-A01',
          draft_version: 1, expected_receipt_revision: 0, action: 'posted', reason: null,
          correction_ref: null, actual_posted_at: null },
      });
      assert.equal(other.status(), 201);
      await page.unroute('**/api/reports');
      await page.unroute('**/api/history-state');
      await checkbox(page, 'D-A02').check();
      await until(async () => (await page.locator('article[data-draft-id="D-A02"] [data-report-status]').textContent()).includes('SQLite受付済み'));
      await page.unroute('**/api/history-state');
      await page.reload();
      await ready(page);
      await selectOldDay(page);
      assert(await checkbox(page, 'D-A02').isChecked());
      assert.match(await page.locator('article[data-draft-id="D-A01"] [data-report-status]').textContent(), /要確認/);
      await shot('rejected-report-does-not-stop-other-product.png');
      console.log(JSON.stringify({ phase: 'rejected_outbox_isolated' }));
      return;
    }
    if (process.env.INAGO_TEST_HISTORY_MODE === 'queued-order') {
      await page.route('**/api/reports', (route) => route.fulfill({
        status: 503, contentType: 'application/json', body: '{"error":"storage_unavailable"}',
      }));
      await checkbox(page, 'D-A01').check();
      await until(async () => (await page.locator('#outbox-count').textContent()).includes('1件'));
      await page.unroute('**/api/reports');
      await checkbox(page, 'D-A01').uncheck();
      await until(async () => (await page.locator('#outbox-count').textContent()) === '');
      await page.reload();
      await ready(page);
      await selectOldDay(page);
      assert.equal(await checkbox(page, 'D-A01').isChecked(), false);
      await shot('queued-actions-in-order.png');
      console.log(JSON.stringify({ phase: 'queued_order_preserved' }));
      return;
    }
    if (process.env.INAGO_TEST_HISTORY_MODE === 'receipt-revision') {
      await page.route('**/api/history-state', (route) => route.fulfill({
        status: 503, contentType: 'application/json', body: '{"error":"storage_unavailable"}',
      }));
      await checkbox(page, 'D-A01').check();
      await until(async () => (await page.locator('#history-message').textContent()).includes('SQLiteには保存済み'));
      assert(await checkbox(page, 'D-A01').isChecked());
      await checkbox(page, 'D-A01').uncheck();
      await until(async () => /SQLiteには保存済み|ブラウザー保留に保存/.test(await page.locator('#history-message').textContent()));
      await page.locator('#history-reconnect').click();
      await page.locator('[data-history-connection="unavailable"]').waitFor();
      assert.equal(await checkbox(page, 'D-A01').isChecked(), false);
      await page.unroute('**/api/history-state');
      await page.reload();
      await ready(page);
      await selectOldDay(page);
      assert.equal(await checkbox(page, 'D-A01').isChecked(), false);
      await shot('receipt-revision-after-reload.png');
      console.log(JSON.stringify({ phase: 'receipt_revision_preserved' }));
      return;
    }
    await checkbox(page, 'D-A01').check();
    await until(async () => (await page.locator('article[data-draft-id="D-A01"] [data-report-status]').textContent()).includes('SQLite受付済み'));
    await shot('02-sqlite-received.png');

    // Reload while the POST has not reached the server: IndexedDB must retain it.
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    await page.route('**/api/reports', async (route) => {
      await pending;
      await route.continue().catch(() => {});
    }, { times: 1 });
    await checkbox(page, 'D-A02').check();
    await until(async () => (await page.locator('#outbox-count').textContent()).includes('1件'));
    await shot('03-before-api-ack.png', 'D-A02');
    const navigation = page.reload();
    release();
    await navigation;
    await ready(page);
    await selectOldDay(page);
    await until(() => checkbox(page, 'D-A02').isChecked());
    await until(async () => (await page.locator('#outbox-count').textContent()) === '');
    assert(await checkbox(page, 'D-A01').isChecked());
    console.log(JSON.stringify({ phase: 'posted' }));

    // Python adds the next day's draft, applies reports, and regenerates the HTML.
    await page.locator('button[data-target-date="2026-10-13"]').waitFor({ timeout: 3000 });
    await selectOldDay(page);
    await until(async () => (await page.locator('article[data-draft-id="D-A01"] [data-report-status]').textContent()).includes('管理反映済み'));
    assert(await checkbox(page, 'D-A01').isChecked());
    assert(await checkbox(page, 'D-A02').isChecked());
    await shot('04-daily-update-preserved.png');
    console.log(JSON.stringify({ phase: 'daily_update' }));
    const input = readline.createInterface({ input: process.stdin });
    await new Promise((resolve) => input.once('line', resolve));
    input.close();
    await page.reload();
    await ready(page);
    await selectOldDay(page);
    assert(await checkbox(page, 'D-A01').isChecked());
    assert(await checkbox(page, 'D-A02').isChecked());
    await shot('05-server-restarted.png');

    const second = await context.newPage();
    await second.goto(new URL('/posting-history.html', page.url()).href);
    await ready(second);
    await selectOldDay(second);
    await checkbox(page, 'D-A01').uncheck();
    await until(async () => !(await checkbox(second, 'D-A01').isChecked()), 2500);
    await page.reload();
    await ready(page);
    await selectOldDay(page);
    assert.equal(await checkbox(page, 'D-A01').isChecked(), false);
    await shot('06-correction-synced-and-reloaded.png');
    console.log(JSON.stringify({ phase: 'restarted_and_corrected' }));
  } catch (error) {
    await shot('failure.png').catch(() => {});
    throw error;
  } finally {
    await context.tracing.stop({ path: path.join(evidence, 'trace.zip') });
    await context.close();
    await browser.close();
  }
})().catch((error) => { console.error(error.stack); process.exitCode = 1; });
