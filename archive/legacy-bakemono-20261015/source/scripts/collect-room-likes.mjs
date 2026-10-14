import { chromium } from 'playwright-core';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  appendRoomRun,
  collectToObservation,
  deduplicateObservations,
  extractInitialState,
  findRoomUrlInText,
  jstCalendarRangeStart,
  listCardFromRaw,
  matchRecentCollects,
  parseRoomLikesArgs,
  paginationLimitReached,
  persistObservedRoomRun,
  publicCollectsPayloadToPage,
  selectRecentUnpinnedPage,
  validateRoomUrl,
} from './room-likes-lib.mjs';

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_OUTPUT_DIR = path.join(PROJECT_ROOT, 'rakuten-room-kai', 'room-metrics');
const PUBLIC_PROFILE_DIR = path.join(
  process.env.LOCALAPPDATA || path.join(homedir(), 'AppData', 'Local'),
  'rakuten-room-bakemono', 'room-public-playwright-profile',
);
const LIST_CARD_SELECTOR = 'div[class*="collect--"]:has(a[class*="link-image--"])';

function log(message) {
  process.stdout.write(`${message}\n`);
}

async function roomUrlFromAccount() {
  const candidates = [
    path.join(PROJECT_ROOT, 'rakuten-room-kai', 'account.md'),
    path.join(PROJECT_ROOT, 'rakuten-room', 'account.md'),
  ];
  for (const candidate of candidates) {
    try {
      const roomUrl = findRoomUrlInText(await readFile(candidate, 'utf8'));
      if (roomUrl) return roomUrl;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  throw new Error('ROOM URLがありません。--room-url または account.md に指定してください');
}

async function initialState(page) {
  const scripts = await page.locator('script').allTextContents();
  const source = scripts.find((text) => text.includes('window.__INITIAL_STATE__'));
  if (!source) throw new Error('公開ページのINITIAL_STATEを取得できません');
  return extractInitialState(source);
}

async function waitForRoomList(page, roomUrl) {
  await page.goto(roomUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.locator(LIST_CARD_SELECTOR).first().waitFor({ state: 'attached', timeout: 45000 });
}

async function scrollForCards(page, targetCount) {
  let previousCount = 0;
  let stableRounds = 0;
  for (let round = 0; round < 30; round += 1) {
    const count = await page.locator(LIST_CARD_SELECTOR).count();
    if (count >= targetCount) return count;
    if (count === previousCount) stableRounds += 1;
    else stableRounds = 0;
    if (stableRounds >= 3) return count;
    previousCount = count;
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(700);
  }
  return page.locator(LIST_CARD_SELECTOR).count();
}

async function readListCards(page, maxItems) {
  const rawCards = await page.locator(LIST_CARD_SELECTOR).evaluateAll((elements, limit) => elements.slice(0, limit).map((card) => ({
      imageUrl: card.querySelector('a[class*="link-image--"] img')?.src ?? '',
      content: card.querySelector('[class*="social-text-area--"]')?.textContent?.trim() ?? '',
      socialText: card.querySelector('[class*="social-data-wrapper--"]')?.textContent ?? '',
    })), maxItems);
  return rawCards.map(listCardFromRaw);
}

function validCards(cards) {
  const invalid = cards.filter((card) => !card.imageUrl
    || !Number.isInteger(card.likes) || card.likes < 0
    || !Number.isInteger(card.comments) || card.comments < 0);
  if (invalid.length > 0) {
    throw new Error(`一覧のいいね数等を読めない投稿が${invalid.length}件あります。0件として保存しません`);
  }
  return cards;
}

async function returnToList(page, roomUrl, requiredCards) {
  try {
    await page.goBack({ waitUntil: 'commit', timeout: 15000 });
    await page.waitForURL(roomUrl, { timeout: 15000 });
    if (validateRoomUrl(page.url()) !== roomUrl) throw new Error('一覧以外へ戻りました');
    await page.locator(LIST_CARD_SELECTOR).first().waitFor({ state: 'attached', timeout: 30000 });
  } catch {
    await waitForRoomList(page, roomUrl);
  }
  await scrollForCards(page, requiredCards);
}

async function resolveUnmatchedCard(page, roomUrl, card, observedAt, shouldReturnToList) {
  const cards = page.locator(LIST_CARD_SELECTOR);
  if (await cards.count() <= card.index) await scrollForCards(page, card.index + 1);
  const target = cards.nth(card.index);
  const current = await target.evaluate((element) => ({
    imageUrl: element.querySelector('a[class*="link-image--"] img')?.src ?? '',
    content: element.querySelector('[class*="social-text-area--"]')?.textContent?.trim() ?? '',
  }));
  if (current.imageUrl !== card.imageUrl || current.content !== card.content) {
    throw new Error(`一覧の${card.index + 1}件目が巡回中に変わりました`);
  }

  await Promise.all([
    page.waitForURL((url) => /^\/[^/]+\/\d+$/.test(url.pathname), { timeout: 30000 }),
    target.locator('a[class*="link-image--"]').click({ timeout: 30000 }),
  ]);
  const state = await initialState(page);
  if (state.pageType !== 'itemdetail' || !state.collectData) {
    throw new Error(`一覧の${card.index + 1}件目の投稿詳細を取得できません`);
  }
  const observation = collectToObservation(state.collectData, roomUrl, observedAt);
  if (shouldReturnToList) await returnToList(page, roomUrl, card.index + 1);
  return observation;
}

async function writeStatus(outputDir, message) {
  await mkdir(outputDir, { recursive: true });
  await writeFile(path.join(outputDir, 'status.txt'), `${message}\n`, 'utf8');
}

async function requestPublicCollectsPage(page, endpoint, roomUrl, userId, observedAt, allowTerminalNotFound = false) {
  const response = await page.evaluate(async (url) => {
    const result = await fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    return { status: result.status, text: await result.text() };
  }, endpoint);
  if (response.status !== 200) {
    throw new Error(`公開ROOM一覧の取得に失敗しました（HTTP ${response.status}）`);
  }
  let payload;
  try {
    payload = JSON.parse(response.text);
  } catch {
    throw new Error('公開ROOM一覧の応答を読めません');
  }
  return publicCollectsPayloadToPage(payload, { roomUrl, userId, observedAt, allowTerminalNotFound });
}

function roomUserId(state) {
  const userId = String(state.userData?.id ?? '');
  if (!/^\d+$/.test(userId)) throw new Error('ROOMユーザーIDを読めません');
  return userId;
}

async function collectFromPublicListEndpoint(page, roomUrl, state, maxItems, observedAt) {
  const userId = roomUserId(state);
  let endpoint = `https://room.rakuten.co.jp/api/${userId}/collects?api_version=1`;
  const visited = new Set();
  const observations = [];

  while (endpoint && observations.length < maxItems) {
    if (paginationLimitReached(visited, endpoint, 30)) {
      throw new Error('公開ROOM一覧のページ送りを完了できません');
    }
    visited.add(endpoint);
    const pageData = await requestPublicCollectsPage(
      page, endpoint, roomUrl, userId, observedAt, visited.size > 1,
    );
    observations.push(...pageData.observations);
    endpoint = pageData.nextPage;
  }
  return observations.slice(0, maxItems);
}

async function collectRecentDays(page, roomUrl, state, days, observedAt) {
  const userId = roomUserId(state);
  const cutoffAt = jstCalendarRangeStart(observedAt, days);
  const startDateJst = new Date(Date.parse(cutoffAt) + (9 * 60 * 60 * 1000))
    .toISOString().slice(0, 10);
  let endpoint = `https://room.rakuten.co.jp/api/${userId}/collects?api_version=1`;
  const visited = new Set();
  const observations = [];
  let pinnedExcluded = 0;
  let outOfRange = 0;
  let reachedCutoff = false;
  let newestOutOfRangeAt = null;

  while (endpoint && !reachedCutoff) {
    if (paginationLimitReached(visited, endpoint, 100)) {
      throw new Error('指定期間のページ送りを完了できません');
    }
    visited.add(endpoint);
    const pageData = await requestPublicCollectsPage(
      page, endpoint, roomUrl, userId, observedAt, visited.size > 1,
    );
    const selected = selectRecentUnpinnedPage(pageData.entries, cutoffAt);
    observations.push(...selected.observations);
    pinnedExcluded += selected.pinnedExcluded;
    outOfRange += selected.outOfRange;
    if (selected.newestOutOfRangeAt
      && (!newestOutOfRangeAt || selected.newestOutOfRangeAt > newestOutOfRangeAt)) {
      newestOutOfRangeAt = selected.newestOutOfRangeAt;
    }
    reachedCutoff = selected.reachedCutoff;
    endpoint = pageData.nextPage;
  }

  const deduplicated = deduplicateObservations(observations);
  return {
    observations: deduplicated.observations,
    days,
    cutoffAt,
    startDateJst,
    pages: visited.size,
    pinnedExcluded,
    duplicates: deduplicated.duplicates,
    outOfRange,
    newestOutOfRangeAt,
    reachedCutoff,
  };
}

async function collectFromRenderedCards(page, roomUrl, state, maxItems, observedAt) {
  const availableCards = await scrollForCards(page, maxItems);
  const requested = Math.min(maxItems, availableCards);
  const cards = validCards(await readListCards(page, requested));
  const recentCollects = Array.isArray(state.userData?.recent_collects)
    ? state.userData.recent_collects : [];
  const matched = matchRecentCollects(cards, recentCollects, roomUrl, observedAt);
  const observations = [...matched.resolved];
  const failures = [];
  log(`DOM一覧 ${cards.length}件を取得、埋め込みデータで ${matched.resolved.length}件を照合。`);

  for (const [unresolvedIndex, card] of matched.unresolved.entries()) {
    try {
      log(`投稿詳細を確認中: ${card.index + 1}/${cards.length}`);
      const shouldReturnToList = unresolvedIndex < matched.unresolved.length - 1;
      observations.push(await resolveUnmatchedCard(
        page, roomUrl, card, observedAt, shouldReturnToList,
      ));
    } catch (error) {
      failures.push({ index: card.index, detail: error.message?.split('\n')[0] || '取得失敗' });
      const hasMoreDetails = unresolvedIndex < matched.unresolved.length - 1;
      if (hasMoreDetails && page.url() !== roomUrl) {
        await returnToList(page, roomUrl, card.index + 1);
      }
    }
  }
  return { observations, failures };
}

async function collectRoomLikes(options) {
  const roomUrl = options.roomUrl ?? await roomUrlFromAccount();
  const outputDir = path.resolve(PROJECT_ROOT, options.outputDir ?? DEFAULT_OUTPUT_DIR);
  const observedAt = new Date().toISOString();
  let context;
  try {
    await mkdir(PUBLIC_PROFILE_DIR, { recursive: true });
    context = await chromium.launchPersistentContext(PUBLIC_PROFILE_DIR, {
      channel: 'msedge',
      headless: options.headless,
      viewport: { width: 1360, height: 900 },
      acceptDownloads: false,
    });
    const page = context.pages()[0] || await context.newPage();
    await waitForRoomList(page, roomUrl);
    const state = await initialState(page);
    let observations;
    let failures = [];
    let range = null;
    if (options.days !== null) {
      range = await collectRecentDays(page, roomUrl, state, options.days, observedAt);
      observations = range.observations;
      log(`JST直近${range.days}日を${range.pages}ページ巡回し、通常投稿 ${observations.length}件を確認。`);
      log(`固定投稿除外 ${range.pinnedExcluded}件／重複除外 ${range.duplicates}件／期間外確認 ${range.outOfRange}件。`);
    } else {
      try {
        observations = await collectFromPublicListEndpoint(
          page, roomUrl, state, options.maxItems, observedAt,
        );
        log(`公開ROOM一覧データから ${observations.length}件を確認。`);
      } catch (endpointError) {
        log(`公開ROOM一覧データを使えません。DOM巡回へ切り替えます: ${endpointError.message}`);
        const fallback = await collectFromRenderedCards(
          page, roomUrl, state, options.maxItems, observedAt,
        );
        observations = fallback.observations;
        failures = fallback.failures;
      }
    }

    const unique = deduplicateObservations(observations).observations;
    if (unique.length === 0 && options.days === null) {
      throw new Error('確認済み投稿がありません。0件として保存しません');
    }
    const result = await persistObservedRoomRun({
      outputDir,
      roomUrl,
      observedAt,
      observations: unique,
      replacePosts: options.days !== null,
      scope: range ? {
        kind: 'days',
        days: range.days,
        startAt: range.cutoffAt,
        startDateJst: range.startDateJst,
        newestOutOfRangeAt: range.newestOutOfRangeAt,
      } : null,
    });
    if (failures.length > 0) {
      await appendRoomRun(outputDir, {
        observedAt,
        status: 'partial',
        detail: `取得 ${unique.length}件、未取得 ${failures.length}件: ${failures.map((failure) => `#${failure.index + 1} ${failure.detail}`).join(' / ')}`,
        counts: { observed: unique.length, unavailable: failures.length },
      });
    }
    const rangeStatus = range
      ? `\n対象: JST直近${range.days}日（開始 ${range.startDateJst} 00:00:00 JST）\n直近の期間外投稿日: ${range.newestOutOfRangeAt ?? 'なし'}\nページ ${range.pages}／固定投稿除外 ${range.pinnedExcluded}件／重複除外 ${range.duplicates}件`
      : '';
    const status = `確認日時: ${observedAt}\nROOM: ${roomUrl}${rangeStatus}\n実ページ確認 ${unique.length}件／取得失敗 ${failures.length}件／変更 ${result.changed}件`;
    await writeStatus(outputDir, status);
    log(`ROOM公開ページ: ${roomUrl}`);
    log(`実ページ確認 ${unique.length}件／⚠取得失敗 ${failures.length}件／変更 ${result.changed}件`);
    log(`保存先: ${path.relative(PROJECT_ROOT, outputDir)}`);
    for (const observation of unique.slice(0, 10)) {
      log(`${observation.postId}｜いいね ${observation.likes}｜${observation.itemName}`);
    }
    if (failures.length > 0) process.exitCode = 1;
  } catch (error) {
    const message = error.message?.split('\n')[0] || '不明なエラー';
    await appendRoomRun(outputDir, { observedAt, status: 'unavailable', detail: message });
    await writeStatus(outputDir, `確認日時: ${observedAt}\nROOM: ${roomUrl}\n未取得: ${message}\n前回値を今回値として使わないでください。`);
    throw error;
  } finally {
    await context?.close();
  }
}

let options;
try {
  options = parseRoomLikesArgs(process.argv.slice(2));
  await collectRoomLikes(options);
} catch (error) {
  process.stderr.write(`取得失敗: ${error.message?.split('\n')[0] || '不明なエラー'}\n`);
  process.exitCode = 1;
}
