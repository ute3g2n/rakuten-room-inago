// Clean browser integration against the isolated local history server.
const { chromium, expect: unused } = require('playwright-core');
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
  const shot = (name) => page.screenshot({ path: path.join(evidence, name), fullPage: false });
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
    await shot('03-before-api-ack.png');
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
  } finally {
    await context.tracing.stop({ path: path.join(evidence, 'trace.zip') });
    await context.close();
    await browser.close();
  }
})().catch((error) => { console.error(error.stack); process.exitCode = 1; });
