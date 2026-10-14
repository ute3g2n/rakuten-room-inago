import { chromium } from 'playwright-core';
import { readFile } from 'node:fs/promises';
import { extractInitialState, findRoomUrlInText, publicCollectsPayloadToPage } from '../../scripts/room-likes-lib.mjs';

const text = await readFile(new URL('../../rakuten-room/account.md', import.meta.url), 'utf8');
const roomUrl = findRoomUrlInText(text);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const page = await browser.newPage();
  await page.goto(roomUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  const scripts = await page.locator('script').allTextContents();
  const source = scripts.find((value) => value.includes('window.__INITIAL_STATE__'));
  const state = source ? extractInitialState(source) : null;
  const userId = state?.userData?.id;
  let endpoint = `https://room.rakuten.co.jp/api/${userId}/collects?api_version=1`;
  for (let index = 0; index < 5 && endpoint; index += 1) {
    const result = await page.evaluate(async (url) => {
      const response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const body = await response.text();
      let parsed = null;
      try { parsed = JSON.parse(body); } catch {}
      return { status: response.status, contentType: response.headers.get('content-type'),
        length: body.length, json: parsed !== null, beginsHtml: /^\s*</.test(body),
        empty: body.length === 0, parsed };
    }, endpoint);
    let mapped = null;
    let mappingError = null;
    try {
      mapped = result.parsed ? publicCollectsPayloadToPage(result.parsed, {
        roomUrl, userId: String(userId), observedAt: new Date().toISOString(),
      }) : null;
    } catch (error) {
      mappingError = error.message;
    }
    process.stdout.write(`${JSON.stringify({ page: index + 1, hasState: !!state, hasUserId: !!userId,
      status: result.status, contentType: result.contentType, length: result.length,
      json: result.json, beginsHtml: result.beginsHtml, empty: result.empty,
      payloadStatus: result.parsed?.status ?? null, payloadCode: result.parsed?.code ?? null,
      dataArray: Array.isArray(result.parsed?.data), dataCount: Array.isArray(result.parsed?.data) ? result.parsed.data.length : null,
      mappingError, observations: mapped?.observations.length ?? null, hasNext: !!mapped?.nextPage })}\n`);
    endpoint = mapped?.nextPage ?? null;
  }
} finally {
  await browser.close();
}
