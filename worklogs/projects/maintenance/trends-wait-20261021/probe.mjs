/** One-off experiment. Does not change daily settings or write the run DB. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { chromium } from 'playwright-core';
import { main } from '../../../../automation/inago/adapters/public_sources.mjs';

const root = process.cwd();
assert.equal(root.toLowerCase().replaceAll('\\', '/'), 'c:/project/rakuten-room-inago');
const output = path.join(root, '非公開作業データ/trends-wait-20261021');
fs.mkdirSync(output, { recursive: true });
const settings = JSON.parse(fs.readFileSync(path.join(root,
  'rakuten-room-inago/runtime/config/settings.json'), 'utf8'));
const sourcePath = path.join(root, 'automation/inago/adapters/public_sources.mjs');
const events = [];
const delayMs = 1000;
let activePage;
const captureUi = !process.argv.includes('--self-check');
const submitEnter = process.argv.includes('--submit-enter');
const typeKeys = process.argv.includes('--type-keys');

async function captureSafely(read) {
  try { return await read(); }
  catch (error) { return { capture_error: error.name }; }
}

async function publicUiState(page) {
  return { path: new URL(page.url()).pathname,
    ready_state: await page.evaluate(() => document.readyState),
    visible_options: await page.getByRole('option').filter({ visible: true }).allTextContents(),
  };
}

// This process imports the production adapter unchanged. Its locator actions
// alone are wrapped; the wrappers disappear when this experiment exits.
function delayActions(page) {
  const prototype = Object.getPrototypeOf(page.locator('body'));
  const originals = new Map(['click', 'fill', 'press'].map(name => [name, prototype[name]]));
  for (const [name, original] of originals) {
    prototype[name] = async function (...args) {
      const optionsIndex = name === 'click' ? 0 : 1;
      const options = args[optionsIndex] ?? {};
      if (options.trial === true) return original.apply(this, args);
      if (submitEnter && name === 'click' && this.toString().includes("name: '調べる'")) {
        return page.getByLabel('検索', { exact: true }).and(page.locator('input'))
          .filter({ visible: true })
          .press('Enter', options);
      }
      const timeout = options.timeout ?? 10000;
      const started = performance.now();
      const remaining = () => {
        const value = Math.floor(timeout - (performance.now() - started));
        if (value <= 0) throw new Error('diagnostic_action_timeout');
        return value;
      };
      await originals.get('click').call(this, { trial: true, timeout: remaining() });
      if (name === 'fill') {
        while (!await this.isEditable({ timeout: remaining() })) {
          await page.waitForTimeout(Math.min(50, remaining()));
        }
      }
      const readyAt = performance.now();
      await page.waitForTimeout(delayMs);
      const actedAt = performance.now();
      const event = { action: name, selector: this.toString(),
        ready_after_ms: Math.round(readyAt - started),
        post_ready_wait_ms: Math.round(actedAt - readyAt), outcome: 'pending' };
      events.push(event);
      if (captureUi) {
        event.before = await captureSafely(() => publicUiState(page));
        event.target = await captureSafely(() => this.evaluate(element => ({ tag: element.tagName,
          type: element.getAttribute('type'), text: element.textContent.trim().slice(0, 100),
          value: 'value' in element ? element.value : null,
          disabled: element.hasAttribute('disabled'),
        })));
      }
      args[optionsIndex] = { ...options, timeout: remaining() };
      try {
        let value;
        if (typeKeys && name === 'fill') {
          assert.equal(await this.inputValue(), '', 'Key input variant requires an empty input');
          event.input_method = 'pressSequentially';
          value = await this.pressSequentially(args[0], { delay: 100, timeout: remaining() });
        } else { value = await original.apply(this, args); }
        event.outcome = 'complete';
        if (captureUi) event.after = await captureSafely(() => publicUiState(page));
        return value;
      } catch (error) {
        event.outcome = error.name;
        throw error;
      }
    };
  }
  return () => { for (const [name, original] of originals) prototype[name] = original; };
}

if (process.argv.includes('--self-check')) {
  const browser = await chromium.launch({ executablePath: settings.browser.chrome_executable,
    headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent('<input aria-label="probe"><button disabled>probe</button>' +
      '<script>setTimeout(()=>document.querySelector("button").disabled=false,600)</script>');
    const restore = delayActions(page);
    try {
      await page.getByRole('button').click({ timeout: 5000 });
      await page.getByRole('textbox').fill('probe', { timeout: 5000 });
      await page.getByRole('textbox').press('Enter', { timeout: 5000 });
      assert.equal(events.length, 3);
      assert.ok(events[0].ready_after_ms >= 400);
      assert.ok(events.every(event => event.post_ready_wait_ms >= 990 && event.outcome === 'complete'));
      console.log(JSON.stringify({ status: 'passed', events }));
    } finally { restore(); }
  } finally { await browser.close(); }
} else {
  const launch = chromium.launchPersistentContext.bind(chromium);
  let restore;
  chromium.launchPersistentContext = async (...args) => {
    const context = await launch(...args);
    const newPage = context.newPage.bind(context);
    const close = context.close.bind(context);
    context.newPage = async (...pageArgs) => {
      activePage = await newPage(...pageArgs);
      restore = delayActions(activePage);
      return activePage;
    };
    context.close = async () => {
      try {
        if (activePage && !activePage.isClosed()) {
          fs.writeFileSync(path.join(output, 'last-page-state.json'),
            JSON.stringify(await publicUiState(activePage), null, 2));
          fs.writeFileSync(path.join(output, 'last-page-text.txt'),
            await activePage.locator('body').innerText({ timeout: 1000 }));
          await activePage.screenshot({ path: path.join(output, 'last-page.png'), timeout: 5000 });
        }
      } catch { /* A closed/error page cannot always be captured. */ }
      finally { restore?.(); await close(); }
    };
    return context;
  };
  const input = { kind: 'trends', config: {
    environment: 'public_ui', timeout_seconds: 120,
    fixture_product_delay_ms: 0, fixture_browser_startup_delay_ms: 0,
    chrome_executable: settings.browser.chrome_executable,
    user_data_dir: settings.browser.user_data_dir, headless: false,
  }, request: {
    request_id: 'DIAG-TRENDS-20261021-WAIT1000', run_id: 'DIAG-TRENDS-20261021',
    settings_version: 'confirmed-0.3-step46-all132', parent_word: 'スキンケア',
    language: 'ja', geo: 'JP', search_type: 'web', range: '3m', list_type: 'Top',
    deadline_at: null,
  } };
  const startedAt = new Date().toISOString();
  let result;
  try {
    const response = await main(input);
    fs.writeFileSync(path.join(output, 'response.json'), JSON.stringify(response, null, 2));
    result = { status: response.response.status, error_code: null,
      word_count: response.response.words.length };
  } catch (error) {
    result = { status: 'unavailable', error_code: error.code ?? error.name,
      retry_after_seconds: error.retryAfterSeconds ?? null, word_count: 0 };
    process.exitCode = 1;
  }
  const evidence = { ...result, started_at: startedAt, finished_at: new Date().toISOString(),
    query: 'スキンケア', delay_ms: delayMs, submit_method: submitEnter ? 'enter' : 'button',
    input_method: typeKeys ? 'pressSequentially' : 'fill',
    source_sha256: crypto.createHash('sha256')
      .update(fs.readFileSync(sourcePath)).digest('hex'), events };
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence));
}
