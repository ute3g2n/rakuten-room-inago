import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON = path.join(ROOT, '.venv-kai', 'Scripts', 'python.exe');
const FIXTURE = path.join(ROOT, 'tests_kai', 'e2e_dashboard_fixture.py');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const ARTIFACTS = path.join(ROOT, 'rakuten-room-kai', 'implementation', 'dashboard-e2e');
const COVERAGE = path.join(ROOT, 'rakuten-room-kai', 'implementation', 'path-coverage-dashboard.json');
const ENV = { ...process.env, PYTHONPATH: ROOT, PYTHONIOENCODING: 'utf-8' };
const results = [];
let browser;

function oneLine(child) {
  return new Promise((resolve, reject) => {
    let pending = '';
    child.once('error', reject);
    child.stdout.on('data', (chunk) => {
      pending += chunk.toString();
      const line = pending.split(/\r?\n/)[0];
      if (pending.includes('\n')) resolve(JSON.parse(line));
    });
    child.once('exit', (code) => reject(new Error(`fixture server exited ${code}`)));
  });
}

async function start(workspace) {
  const child = spawn(PYTHON, [FIXTURE, 'serve', workspace], { cwd: ROOT, env: ENV, windowsHide: true });
  const ready = await oneLine(child);
  return { child, origin: `http://127.0.0.1:${ready.port}` };
}

async function stop(server) {
  if (!server) return;
  server.child.kill();
  await new Promise((resolve) => server.child.once('exit', resolve));
}

async function prepare() {
  const workspace = await mkdtemp(path.join(tmpdir(), 'rr-kai-dashboard-e2e-'));
  const result = spawnSync(PYTHON, [FIXTURE, 'prepare', workspace], { cwd: ROOT, env: ENV, encoding: 'utf8', windowsHide: true });
  if (result.status !== 0) throw new Error(`fixture prepare failed: ${result.stderr}`);
  return workspace;
}

async function removeWorkspace(workspace) {
  const resolved = path.resolve(workspace);
  const prefix = path.resolve(tmpdir(), 'rr-kai-dashboard-e2e-');
  if (!resolved.startsWith(prefix)) throw new Error('unsafe temporary workspace removal');
  await rm(resolved, { recursive: true, force: true });
}

class DashboardPage {
  constructor(page, origin) {
    this.page = page;
    this.origin = origin;
  }
  async goto() {
    await this.page.goto(this.origin);
    await this.page.getByRole('status').getByText('保存サーバーに接続しました。', { exact: false }).waitFor();
  }
  day(day) { return this.page.locator(`#day-${day}`); }
  card(id) { return this.page.locator(`article[data-product-id="${id}"]`); }
  box(id) { return this.page.locator(`input[data-post-id="${id}"]`); }
  async check(id) {
    await this.box(id).click();
    await this.page.getByRole('status').getByText('投稿状態を保存しました。', { exact: false }).waitFor();
    await this.box(id).waitFor({ state: 'visible' });
  }
}

function scenario(id, title, run) {
  test(`${id} ${title}`, async () => {
    const workspace = await prepare();
    let server;
    let context;
    let page;
    let externalIntercepted = 0;
    const consoleErrors = [];
    const pageErrors = [];
    const artifacts = [];
    try {
      server = await start(workspace);
      context = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'], acceptDownloads: false });
      await context.tracing.start({ screenshots: true, snapshots: true });
      await context.route('**/*', async (route) => {
        const url = new URL(route.request().url());
        if (url.hostname === '127.0.0.1' || url.protocol === 'file:') return route.continue();
        externalIntercepted += 1;
        await route.fulfill({ status: 200, contentType: 'text/html', body: '<title>fixture product</title>' });
      });
      page = await context.newPage();
      page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
      page.on('pageerror', (error) => pageErrors.push(String(error)));
      const dashboard = new DashboardPage(page, server.origin);
      await dashboard.goto();
      await run({ workspace, server, context, page, dashboard, artifacts, replaceServer: (next) => { server = next; } });
      assert.equal(externalIntercepted, id === 'E2E-07' ? 1 : 0);
      assert.deepEqual(consoleErrors, []);
      assert.deepEqual(pageErrors, []);
      if (id === 'E2E-01') {
        const screenshot = path.join(ARTIFACTS, 'dashboard-success.png');
        await page.screenshot({ path: screenshot, fullPage: true });
        artifacts.push(screenshot);
      }
      results.push({ id, title, result: 'passed', run_id: `fixture:${id}`,
        external_network_sent: 0, external_requests_intercepted: externalIntercepted, artifacts });
    } catch (error) {
      if (page) {
        const screenshot = path.join(ARTIFACTS, `${id}-failure.png`);
        await page.screenshot({ path: screenshot, fullPage: true }).catch(() => {});
        artifacts.push(screenshot);
      }
      if (context) {
        const trace = path.join(ARTIFACTS, `${id}-trace.zip`);
        await context.tracing.stop({ path: trace }).catch(() => {});
        artifacts.push(trace);
        context = null;
      }
      results.push({ id, title, result: 'failed', run_id: `fixture:${id}`, artifacts, error: String(error) });
      throw error;
    } finally {
      if (context) await context.tracing.stop().catch(() => {});
      if (context) await context.close();
      await stop(server);
      await removeWorkspace(workspace);
    }
  });
}

before(async () => {
  await mkdir(ARTIFACTS, { recursive: true });
  browser = await chromium.launch({ executablePath: EDGE, headless: true });
});
after(async () => {
  await browser?.close();
  await writeFile(COVERAGE, `${JSON.stringify({ version: 1, browser: 'Microsoft Edge Chromium',
    acceptance: { '11-1': ['E2E-01', 'E2E-03'], '11-2': ['E2E-01', 'E2E-02'],
      '11-3': ['E2E-06', 'E2E-07'], '11-4': ['E2E-05'],
      '11-5': ['E2E-06', 'E2E-08'], '11-6': ['E2E-09', 'E2E-10', 'E2E-12', 'E2E-13'] },
    paths: results }, null, 2)}\n`, 'utf8');
});

scenario('E2E-01', 'three days order and initial disclosure', async ({ page, dashboard }) => {
  const links = await page.locator('[data-day-link]').evaluateAll((items) => items.map((item) => item.dataset.dayLink));
  const days = await page.locator('details[id^="day-"]').evaluateAll((items) => items.map((item) => item.id.slice(4)));
  assert.deepEqual(links, ['2026-09-27', '2026-09-26', '2026-09-25']);
  assert.deepEqual(days, links);
  assert.equal(await dashboard.day('2026-09-27').getAttribute('open'), '');
  assert.equal(await dashboard.day('2026-09-26').getAttribute('open'), null);
  assert.equal(await dashboard.day('2026-09-25').getAttribute('open'), null);
});

scenario('E2E-02', 'day navigation opens and scrolls', async ({ page, dashboard }) => {
  await page.locator('[data-day-link="2026-09-25"]').click();
  assert.equal(await dashboard.day('2026-09-25').getAttribute('open'), '');
  assert.equal(new URL(page.url()).hash, '#day-2026-09-25');
  assert.equal(await dashboard.day('2026-09-25').isVisible(), true);
});

scenario('E2E-03', 'same-day regeneration keeps one section and updates copy', async ({ workspace, page, dashboard }) => {
  const data = path.join(workspace, 'rakuten-room-kai');
  const draftPath = path.join(data, 'drafts.json');
  const planPath = path.join(data, 'daily-plans', '2026-09-27.json');
  const drafts = JSON.parse(await readFile(draftPath, 'utf8'));
  const plan = JSON.parse(await readFile(planPath, 'utf8'));
  drafts.drafts[2].body = '更新された本文😺';
  plan.posts[0].body = drafts.drafts[2].body;
  await writeFile(draftPath, JSON.stringify(drafts), 'utf8');
  await writeFile(planPath, JSON.stringify(plan), 'utf8');
  const result = spawnSync(PYTHON, [FIXTURE, 'regenerate', workspace], { cwd: ROOT, env: ENV, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  await page.reload();
  assert.equal(await page.locator('details[id="day-2026-09-27"]').count(), 1);
  assert.equal(await dashboard.card('P03').locator('.copy-body').textContent(), '更新された本文😺');
});

scenario('E2E-04', 'card ID name genre URL schedule and body match fixture', async ({ workspace, dashboard }) => {
  const data = path.join(workspace, 'rakuten-room-kai');
  for (const day of ['2026-09-25', '2026-09-26', '2026-09-27']) {
    const plan = JSON.parse(await readFile(path.join(data, 'daily-plans', `${day}.json`), 'utf8'));
    const post = plan.posts[0];
    const card = dashboard.card(post.product_id);
    assert.equal(await card.count(), 1);
    assert.equal(await card.locator('.product-link').getAttribute('href'), post.url);
    const meta = await card.locator('.post-meta').textContent();
    for (const text of [post.product_id, post.name, post.genre, post.scheduled_at]) assert.ok(meta.includes(text));
    assert.equal(await card.locator('.copy-body').textContent(), post.body);
  }
});

scenario('E2E-05', 'copy button writes only its own body', async ({ page, dashboard }) => {
  for (const id of ['P01', 'P02', 'P03']) {
    const day = { P01: '2026-09-25', P02: '2026-09-26', P03: '2026-09-27' }[id];
    await dashboard.day(day).evaluate((element) => { element.open = true; });
    await dashboard.card(id).locator('button[data-copy-id]').click();
    assert.equal((await page.evaluate(() => navigator.clipboard.readText())).replaceAll('\r\n', '\n'),
      await dashboard.card(id).locator('.copy-body').textContent());
  }
});

scenario('E2E-06', 'saved check survives reload and server restart', async ({ workspace, server, page, dashboard, replaceServer }) => {
  await dashboard.check('P03');
  assert.equal(JSON.parse(await readFile(path.join(workspace, 'rakuten-room-kai', 'post-status.json'), 'utf8')).items.P03.posted, true);
  assert.match(await dashboard.day('2026-09-27').locator('.day-count').textContent(), /1\/1/);
  await page.reload();
  await page.locator('#server-status').getByText('接続しました', { exact: false }).waitFor();
  assert.equal(await dashboard.box('P03').isChecked(), true);
  await stop(server);
  const next = await start(workspace);
  replaceServer(next);
  dashboard.origin = next.origin;
  await dashboard.goto();
  assert.equal(await dashboard.box('P03').isChecked(), true);
});

scenario('E2E-07', 'link copy and disclosure do not mark posted', async ({ page, dashboard }) => {
  const popupReady = page.waitForEvent('popup');
  await dashboard.card('P03').locator('.product-link').click();
  const popup = await popupReady;
  await popup.waitForLoadState();
  assert.match(popup.url(), /^https:\/\/item\.rakuten\.co\.jp\/test-shop\/item-3\//);
  await popup.close();
  await dashboard.card('P03').locator('button[data-copy-id]').click();
  await dashboard.day('2026-09-26').locator('summary').click();
  assert.equal(await dashboard.box('P03').isChecked(), false);
  assert.match(await dashboard.day('2026-09-27').locator('.day-count').textContent(), /0\/1/);
});

scenario('E2E-08', 'uncheck requires confirmation and reason, remains excluded', async ({ workspace, page, dashboard }) => {
  await dashboard.check('P03');
  const dismissConfirm = (dialog) => dialog.dismiss();
  page.on('dialog', dismissConfirm);
  await dashboard.box('P03').click();
  page.off('dialog', dismissConfirm);
  assert.equal(await dashboard.box('P03').isChecked(), true);
  const dismissReason = (dialog) => dialog.type() === 'confirm' ? dialog.accept() : dialog.dismiss();
  page.on('dialog', dismissReason);
  await dashboard.box('P03').click();
  page.off('dialog', dismissReason);
  assert.equal(await dashboard.box('P03').isChecked(), true);
  const correct = (dialog) => dialog.type() === 'confirm' ? dialog.accept() : dialog.accept('誤操作を訂正');
  page.on('dialog', correct);
  await dashboard.box('P03').click();
  page.off('dialog', correct);
  await page.waitForFunction(() => document.querySelector('input[data-post-id="P03"]')?.dataset.saved === 'false');
  const item = JSON.parse(await readFile(path.join(workspace, 'rakuten-room-kai', 'post-status.json'), 'utf8')).items.P03;
  assert.equal(item.posted, false);
  assert.equal(item.ever_marked_posted, true);
  assert.equal(item.correction_history.at(-1).reason, '誤操作を訂正');
});

scenario('E2E-09', 'server stop rolls check back and shows error', async ({ server, dashboard, replaceServer, page }) => {
  await stop(server);
  replaceServer(null);
  await page.evaluate(() => {
    const original = window.fetch;
    window.fetch = (input, options) => input === '/api/post-status'
      ? Promise.reject(new TypeError('server stopped')) : original(input, options);
  });
  await dashboard.box('P03').click();
  await page.getByRole('status').getByText('保存に失敗しました', { exact: false }).waitFor();
  assert.equal(await dashboard.box('P03').isChecked(), false);
});

scenario('E2E-10', 'HTTP 500 rolls check back', async ({ page, dashboard }) => {
  await page.evaluate(() => {
    const original = window.fetch;
    window.fetch = (input, options) => input === '/api/post-status'
      ? Promise.resolve(new Response('{"error":"TEST"}', { status: 500, headers: { 'Content-Type': 'application/json' } }))
      : original(input, options);
  });
  await dashboard.box('P03').click();
  await page.getByRole('status').getByText('保存に失敗しました', { exact: false }).waitFor();
  assert.equal(await dashboard.box('P03').isChecked(), false);
});

scenario('E2E-11', 'file display disables save and HTML remains escaped', async ({ workspace, page, dashboard }) => {
  const html = path.join(workspace, 'rakuten-room-kai', 'posting-dashboard.html');
  await page.goto(`file:///${html.replaceAll('\\', '/')}`);
  assert.equal(await dashboard.box('P03').isDisabled(), true);
  assert.match(await page.locator('#server-status').textContent(), /保存できません/);
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  assert.match(await dashboard.card('P03').locator('.copy-body').textContent(), /<img src=x onerror=/);
  assert.equal(await page.locator('article img').count(), 0);
});

scenario('E2E-12', 'duplicate ID missing draft and URL mismatch report errors', async ({ workspace, page }) => {
  const data = path.join(workspace, 'rakuten-room-kai');
  const initialPlan = JSON.parse(await readFile(path.join(data, 'daily-plans', '2026-09-27.json'), 'utf8'));
  const initialDrafts = JSON.parse(await readFile(path.join(data, 'drafts.json'), 'utf8'));
  for (const kind of ['duplicate', 'missing-draft', 'url-mismatch']) {
    const planPath = path.join(data, 'daily-plans', '2026-09-27.json');
    const draftPath = path.join(data, 'drafts.json');
    const plan = structuredClone(initialPlan);
    const drafts = structuredClone(initialDrafts);
    if (kind === 'duplicate') plan.posts.push({ ...plan.posts[0] });
    if (kind === 'missing-draft') drafts.drafts = drafts.drafts.filter((draft) => draft.product_id !== 'P03');
    if (kind === 'url-mismatch') drafts.drafts[2].url = 'https://item.rakuten.co.jp/other/mismatch/';
    await writeFile(planPath, JSON.stringify(plan), 'utf8');
    await writeFile(draftPath, JSON.stringify(drafts), 'utf8');
    const result = spawnSync(PYTHON, [FIXTURE, 'regenerate', workspace], { cwd: ROOT, env: ENV, encoding: 'utf8' });
    assert.notEqual(result.status, 0, kind);
    await page.reload();
    await page.locator('#server-status').getByText('生成エラー', { exact: false }).waitFor();
    assert.match(await page.locator('#server-status').textContent(), /不一致|重複|欠落|エラー/);
    assert.equal(await page.locator('article[data-product-id="P03"]').count(), 1);
  }
});

scenario('E2E-13', 'conflict response rolls check back and keeps saved count', async ({ page, dashboard, workspace }) => {
  await page.evaluate(() => {
    const original = window.fetch;
    window.fetch = (input, options) => input === '/api/post-status'
      ? Promise.resolve(new Response('{"error":"STATE_CONFLICT"}', { status: 409,
        headers: { 'Content-Type': 'application/json' } })) : original(input, options);
  });
  await dashboard.box('P03').click();
  await page.getByRole('status').getByText('保存に失敗しました', { exact: false }).waitFor();
  assert.equal(await dashboard.box('P03').isChecked(), false);
  assert.match(await dashboard.day('2026-09-27').locator('.day-count').textContent(), /0\/1/);
  const status = JSON.parse(await readFile(path.join(workspace, 'rakuten-room-kai', 'post-status.json'), 'utf8'));
  assert.deepEqual(status.items, {});
});

