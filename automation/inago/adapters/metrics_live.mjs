import { appendFile, lstat, mkdir, writeFile } from 'node:fs/promises';
import { lstatSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { chromium } from 'playwright-core';

import { normalizeDailyResponse, normalizeOrderCsv, orderMonths, previousDailyDates } from './affiliate.mjs';
import { normalizeRoomMetricsResponse } from './room_metrics.mjs';
import {
  deduplicateObservations,
  extractInitialState,
  paginationLimitReached,
  validateRoomUrl,
} from '../../../scripts/room-likes-lib.mjs';
import { parseDailyTable } from '../../../scripts/affiliate-report-lib.mjs';

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const AFFILIATE_BASE = 'https://affiliate.rakuten.co.jp';
const AFFILIATE_REPORT = `${AFFILIATE_BASE}/report/monthly`;
const AFFILIATE_ORDERS = `${AFFILIATE_BASE}/report/order`;
const AFFILIATE_ACTIVE_DATE = '2026-09-23';
const MAX_ROOM_PAGES = 100;
const MAX_TIMEOUT_MS = 30 * 60 * 1000;

function isoNow() {
  return new Date().toISOString();
}

function checkedTimestamp(value, label) {
  if (typeof value !== 'string' || Number.isNaN(Date.parse(value))) {
    throw new Error(`${label}_invalid`);
  }
  return value;
}

function jstDate(value = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(value).map(({ type, value: part }) => [type, part]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function timeoutFor(deadlineEpochMs) {
  const remaining = Math.floor(deadlineEpochMs - Date.now());
  if (remaining <= 0) throw new Error('shared_deadline_exhausted');
  return Math.min(45_000, remaining);
}

function safeErrorCode(error) {
  const message = String(error?.message ?? '').toLowerCase();
  if (message.includes('shared_deadline_exhausted')) return 'shared_deadline_exhausted';
  if (message.includes('timeout') || message.includes('timed out')) return 'timeout';
  if (message.includes('login') || message.includes('authentication')) return 'authentication_required';
  if (message.includes('http_401') || message.includes('http 401')) return 'http_401';
  if (message.includes('http_403') || message.includes('http 403')) return 'http_403';
  if (message.includes('http_429') || message.includes('http 429')) return 'http_429';
  if (message.includes('http_5') || /http 5\d\d/.test(message)) return 'upstream_5xx';
  if (message.includes('contract') || message.includes('columns changed')) return 'source_contract_changed';
  if (message.includes('room_url') || message.includes('room url')) return 'room_url_unavailable';
  return 'source_unavailable';
}

function chromeExecutable() {
  const configured = process.env.INAGO_BROWSER_EXECUTABLE?.trim();
  if (configured) {
    if (!path.isAbsolute(configured)) throw new Error('configured_browser_path_invalid');
    try {
      if (statSync(configured).isFile()) return path.resolve(configured);
    } catch {
      // Fail closed rather than silently switching the production browser.
    }
    throw new Error('configured_browser_unavailable');
  }
  const candidates = [
    process.env['PROGRAMFILES(X86)']
      ? path.join(process.env['PROGRAMFILES(X86)'], 'Google', 'Chrome', 'Application', 'chrome.exe')
      : null,
    process.env.PROGRAMFILES
      ? path.join(process.env.PROGRAMFILES, 'Google', 'Chrome', 'Application', 'chrome.exe')
      : null,
    process.env['PROGRAMFILES(X86)']
      ? path.join(process.env['PROGRAMFILES(X86)'], 'Microsoft', 'Edge', 'Application', 'msedge.exe')
      : null,
    process.env.PROGRAMFILES
      ? path.join(process.env.PROGRAMFILES, 'Microsoft', 'Edge', 'Application', 'msedge.exe')
      : null,
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      // Try the next installed supported browser.
    }
  }
  throw new Error('supported_browser_unavailable');
}

export { chromeExecutable };

function validateDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)
    || Number.isNaN(Date.parse(`${value}T00:00:00Z`))) {
    throw new Error('target_date_invalid');
  }
  return value;
}

function toOffsetDateTime(value) {
  if (value == null || String(value).trim() === '') return null;
  const text = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(text)) return text;
  const jst = text.match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})$/);
  if (!jst) throw new Error('room_post_timestamp_invalid');
  return `${jst[1]}T${jst[2]}+09:00`;
}

export function normalizeAffiliateMetrics({
  observedAt,
  expectedDates,
  dailyRows,
  dailyStatus = 'observed',
  dailyErrorCode = null,
  orderMonths: monthResponses,
}) {
  checkedTimestamp(observedAt, 'observed_at');
  if (!Array.isArray(expectedDates) || !Array.isArray(dailyRows) || !Array.isArray(monthResponses)) {
    throw new Error('affiliate_response_invalid');
  }
  const daily = normalizeDailyResponse({
    status: dailyStatus,
    observedAt,
    expectedDates,
    rows: dailyRows,
    emptyMeansZero: false,
    errorCode: dailyErrorCode,
  });
  const orders = [];
  const windows = [];
  for (const monthResponse of monthResponses) {
    const month = monthResponse?.month;
    if (typeof month !== 'string' || !/^\d{4}-\d{2}$/.test(month)) {
      throw new Error('affiliate_order_month_invalid');
    }
    if (monthResponse.status === 'observed') {
      const normalized = normalizeOrderCsv(monthResponse.csv, month);
      const scopedOrders = normalized.orders.map((order) => ({
        ...order,
        window: `affiliate-orders:${month}`,
      }));
      orders.push(...scopedOrders);
      windows.push({
        window: `affiliate-orders:${month}`,
        count: scopedOrders.length,
        observedAt,
        availability: 'observed',
      });
    } else if (monthResponse.status === 'not_present') {
      windows.push({
        window: `affiliate-orders:${month}`,
        count: null,
        observedAt,
        availability: 'not_present',
        errorCode: monthResponse.errorCode || 'source_not_present',
      });
    } else if (monthResponse.status === 'unavailable') {
      windows.push({
        window: `affiliate-orders:${month}`,
        count: null,
        observedAt,
        availability: 'unavailable',
        errorCode: monthResponse.errorCode || 'source_unavailable',
      });
    } else {
      throw new Error('affiliate_order_status_invalid');
    }
  }
  const statuses = [daily.status, ...windows.map((entry) => entry.availability)];
  const hasObserved = statuses.some((status) => status === 'observed');
  const hasUnavailable = statuses.some((status) => status !== 'observed');
  const status = !hasObserved ? 'unavailable' : hasUnavailable ? 'partial' : 'observed';
  return {
    status,
    observedAt,
    daily,
    orders,
    orderWindows: windows,
  };
}

export function normalizeRoomMetricsFromObservations({
  status = 'observed', errorCode = null, observedAt, roomUrl, observations,
}) {
  if (!Array.isArray(observations)) throw new Error('room_observations_invalid');
  return normalizeRoomMetricsResponse({
    status,
    observedAt,
    roomUrl,
    errorCode,
    posts: observations.map((entry) => ({
      postId: entry.postId,
      roomPostUrl: entry.postUrl,
      itemUrl: entry.itemUrl || null,
      likes: Number.isSafeInteger(entry.likes) && entry.likes >= 0 ? entry.likes : null,
      createdAt: toOffsetDateTime(entry.createdAt),
      isPublic: true,
    })),
  });
}

function roomItemUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password
      || url.hostname.toLowerCase() !== 'item.rakuten.co.jp') return null;
    url.search = '';
    url.hash = '';
    return url.href;
  } catch {
    return null;
  }
}

export function roomPayloadToMetricsPage(payload, {
  roomUrl, userId, observedAt = isoNow(), allowTerminalNotFound = false,
}) {
  const normalizedRoomUrl = validateRoomUrl(roomUrl);
  if (!/^\d+$/.test(String(userId ?? ''))) throw new Error('room_user_id_unavailable');
  if (allowTerminalNotFound && payload?.status === 'not found'
    && payload?.code === 200 && Array.isArray(payload?.data)
    && payload.data.length === 0 && !payload?.meta?.next_page) {
    return {
      entries: [], nextPage: null,
      readFailures: { invalidEntries: 0, missingLikes: 0, missingItemUrls: 0, missingTimestamps: 0 },
    };
  }
  if (payload?.status !== 'success' || payload?.code !== 200 || !Array.isArray(payload?.data)) {
    throw new Error('room_public_response_invalid');
  }
  const roomSlug = new URL(normalizedRoomUrl).pathname.split('/')[1];
  const readFailures = { invalidEntries: 0, missingLikes: 0, missingItemUrls: 0, missingTimestamps: 0 };
  const entries = [];
  for (const collect of payload.data) {
    const postId = String(collect?.id ?? '');
    if (!/^\d+$/.test(postId)) {
      readFailures.invalidEntries += 1;
      continue;
    }
    const itemUrl = roomItemUrl(collect?.item?.url);
    const rawCreatedAt = String(collect?.created_at ?? '').trim();
    const createdAt = rawCreatedAt ? toOffsetDateTime(rawCreatedAt) : null;
    const likes = Number.isSafeInteger(collect?.likes) && collect.likes >= 0
      ? collect.likes : null;
    if (likes === null) readFailures.missingLikes += 1;
    if (itemUrl === null) readFailures.missingItemUrls += 1;
    if (createdAt === null) readFailures.missingTimestamps += 1;
    entries.push({
      observation: {
        postId,
        postUrl: `https://room.rakuten.co.jp/${roomSlug}/${postId}`,
        itemUrl,
        likes,
        createdAt,
        observedAt,
      },
      isPinned: collect?.is_pinned === true
        || collect?.is_pinned === 1 || collect?.is_pinned === '1',
    });
  }
  const rawNextPage = payload.meta?.next_page;
  if (!rawNextPage) return { entries, nextPage: null, readFailures };
  let nextPage;
  try {
    nextPage = new URL(rawNextPage);
  } catch {
    throw new Error('room_next_page_invalid');
  }
  if (nextPage.protocol !== 'https:' || nextPage.hostname !== 'room.rakuten.co.jp'
    || nextPage.username || nextPage.password
    || nextPage.pathname !== `/api/${userId}/collects`) {
    throw new Error('room_next_page_invalid');
  }
  return { entries, nextPage: nextPage.toString(), readFailures };
}

function selectRecentMetricPage(entries, cutoffAt, observedAt) {
  const cutoff = Date.parse(cutoffAt);
  const observed = Date.parse(observedAt);
  const observations = [];
  let pinnedExcluded = 0;
  let outOfRange = 0;
  for (const entry of entries) {
    if (entry.isPinned) {
      pinnedExcluded += 1;
      continue;
    }
    const createdAt = entry.observation.createdAt;
    if (createdAt === null) {
      observations.push(entry.observation);
      continue;
    }
    const created = Date.parse(createdAt);
    if (Number.isNaN(created)) throw new Error('room_post_timestamp_invalid');
    if (created > observed) throw new Error('room_post_after_observation');
    if (created < cutoff) outOfRange += 1;
    else observations.push(entry.observation);
  }
  return {
    observations,
    pinnedExcluded,
    outOfRange,
    reachedCutoff: outOfRange > 0 && observations.length === 0,
  };
}

async function affiliateTable(page, month, deadlineEpochMs) {
  const timeout = timeoutFor(deadlineEpochMs);
  await page.goto(`${AFFILIATE_REPORT}?kpi_date=${month}&date=${month}`, {
    waitUntil: 'domcontentloaded', timeout,
  });
  let destination;
  try {
    destination = new URL(page.url());
  } catch {
    throw new Error('affiliate_destination_invalid');
  }
  if (destination.origin !== AFFILIATE_BASE) throw new Error('authentication_required');
  const expected = `${month.slice(0, 4)}年${month.slice(5, 7)}月`;
  await page.waitForFunction((label) => {
    const selected = document.querySelector('#monthlyTableSelectedDate');
    const rows = document.querySelectorAll('table.-monthly tbody tr');
    return selected?.value === label && [...rows].some(
      (row) => /^\d{4}-\d{2}-\d{2}$/.test(row.cells[0]?.textContent?.trim() || ''),
    );
  }, expected, { timeout: timeoutFor(deadlineEpochMs) }).catch(() => {
    throw new Error('affiliate_daily_table_unavailable');
  });
  const table = await page.locator('table.-monthly').evaluate((element) => ({
    headers: [...element.querySelectorAll('thead th')].map((cell) => cell.textContent.trim()),
    rows: [...element.querySelectorAll('tbody tr')]
      .map((row) => [...row.querySelectorAll('td')].map((cell) => cell.textContent.trim())),
  }));
  return parseDailyTable(table.headers, table.rows, month);
}

async function affiliateOrderCsv(page, month, deadlineEpochMs) {
  const reportUrl = `${AFFILIATE_ORDERS}?order_type=count&date=${month}`;
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.origin === AFFILIATE_BASE
      && url.pathname === '/api/report/order'
      && url.searchParams.get('date') === month
      && url.searchParams.get('offset') === '0';
  }, { timeout: timeoutFor(deadlineEpochMs) });
  await page.goto(reportUrl, { waitUntil: 'domcontentloaded', timeout: timeoutFor(deadlineEpochMs) });
  let response;
  try {
    response = await responsePromise;
  } catch {
    throw new Error('affiliate_order_report_unavailable');
  }
  if (!response.ok()) throw new Error(`http_${response.status()}`);
  await page.waitForFunction(() => {
    const headers = [...document.querySelectorAll('table.-detail thead th')]
      .map((cell) => cell.textContent?.trim() || '');
    return ['発生日時', '成果報酬', '売上金額', 'ジャンル', 'ショップ名', '商品名', 'ステータス', 'リンクタイプ']
      .every((label) => headers.includes(label));
  }, null, { timeout: timeoutFor(deadlineEpochMs) }).catch(() => {
    throw new Error('affiliate_order_table_unavailable');
  });
  const csvLink = page.locator('a[href*="/api/report/download/order"]');
  const href = await csvLink.getAttribute('href');
  if (!href) throw new Error('affiliate_order_csv_link_missing');
  const csvUrl = new URL(href, page.url());
  if (csvUrl.origin !== AFFILIATE_BASE
    || csvUrl.pathname !== '/api/report/download/order'
    || csvUrl.searchParams.get('format') !== 'csv'
    || csvUrl.searchParams.get('date') !== month) {
    throw new Error('affiliate_order_csv_link_invalid');
  }
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: timeoutFor(deadlineEpochMs) }),
    csvLink.evaluate((element) => element.click()),
  ]);
  if (await download.failure()) throw new Error('affiliate_order_csv_download_failed');
  const stream = await download.createReadStream();
  if (!stream) throw new Error('affiliate_order_csv_unreadable');
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}

async function collectAffiliate({ browserExecutable, authStatePath, targetDate, deadlineEpochMs, logEvent }) {
  const info = await lstat(authStatePath).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink()) throw new Error('auth_state_unavailable');
  const browser = await chromium.launch({ executablePath: browserExecutable, headless: true });
  let context;
  try {
    context = await browser.newContext({
      storageState: authStatePath,
      acceptDownloads: true,
      locale: 'ja-JP',
      timezoneId: 'Asia/Tokyo',
    });
    const page = await context.newPage();
    page.setDefaultTimeout(timeoutFor(deadlineEpochMs));
    page.setDefaultNavigationTimeout(timeoutFor(deadlineEpochMs));
    const expectedDates = previousDailyDates(targetDate, AFFILIATE_ACTIVE_DATE, 7);
    const dailyMonths = [...new Set(expectedDates.map((date) => date.slice(0, 7)))];
    if (dailyMonths.length === 0) dailyMonths.push(targetDate.slice(0, 7));
    await logEvent('affiliate', 'daily_collection_started', { monthCount: dailyMonths.length });
    const dailyByDate = new Map();
    let dailyErrorCode = null;
    for (const month of dailyMonths) {
      try {
        for (const [date, row] of await affiliateTable(page, month, deadlineEpochMs)) {
          dailyByDate.set(date, {
            date,
            clicks: row.clicks,
            salesCount: row.ordersUnconfirmed,
            salesYen: row.salesJpyUnconfirmed,
            commissionUnconfirmedYen: row.commissionJpyUnconfirmed,
          });
        }
      } catch (error) {
        dailyErrorCode = safeErrorCode(error);
        await logEvent('affiliate', 'daily_month_unavailable', {
          month,
          errorCode: dailyErrorCode,
        });
      }
    }
    const dailyRows = expectedDates.filter((date) => dailyByDate.has(date))
      .map((date) => dailyByDate.get(date));
    const dailyStatus = dailyErrorCode ? 'partial' : 'observed';
    const months = orderMonths(targetDate, AFFILIATE_ACTIVE_DATE);
    await logEvent('affiliate', 'order_collection_started', { monthCount: months.length });
    const orderResponses = [];
    for (const month of months) {
      try {
        orderResponses.push({
          month,
          status: 'observed',
          csv: await affiliateOrderCsv(page, month, deadlineEpochMs),
        });
        await logEvent('affiliate', 'order_month_observed', { month });
      } catch (error) {
        const errorCode = safeErrorCode(error);
        orderResponses.push({ month, status: 'unavailable', errorCode });
        await logEvent('affiliate', 'order_month_unavailable', { month, errorCode });
      }
    }
    const observedAt = isoNow();
    return normalizeAffiliateMetrics({
      observedAt,
      expectedDates,
      dailyRows,
      dailyStatus,
      dailyErrorCode,
      orderMonths: orderResponses,
    });
  } finally {
    await context?.close();
    await browser.close();
  }
}

export function resolveOwnerRoomUrl(value = process.env.INAGO_OWNER_ROOM_URL) {
  if (typeof value !== 'string' || !value.trim()) throw new Error('room_url_unavailable');
  try {
    return validateRoomUrl(value);
  } catch {
    throw new Error('room_url_unavailable');
  }
}

async function collectRoom({ browserExecutable, roomUrl, observedAt, deadlineEpochMs, logEvent }) {
  const browser = await chromium.launch({ executablePath: browserExecutable, headless: true });
  let context;
  try {
    context = await browser.newContext({
      locale: 'ja-JP',
      timezoneId: 'Asia/Tokyo',
    });
    const page = await context.newPage();
    page.setDefaultTimeout(timeoutFor(deadlineEpochMs));
    page.setDefaultNavigationTimeout(timeoutFor(deadlineEpochMs));
    await page.goto(roomUrl, { waitUntil: 'domcontentloaded', timeout: timeoutFor(deadlineEpochMs) });
    await page.waitForFunction(
      () => [...document.scripts].some((script) => script.textContent?.includes('window.__INITIAL_STATE__')),
      null,
      { timeout: timeoutFor(deadlineEpochMs) },
    ).catch(() => { throw new Error('room_initial_state_unavailable'); });
    const scripts = await page.locator('script').allTextContents();
    const initialScript = scripts.find((script) => script.includes('window.__INITIAL_STATE__'));
    if (!initialScript) throw new Error('room_initial_state_unavailable');
    const state = extractInitialState(initialScript);
    const userId = String(state.userData?.id ?? '');
    if (!/^\d+$/.test(userId)) throw new Error('room_user_id_unavailable');
    await logEvent('room', 'public_profile_loaded', {});
    const cutoffAt = new Date(Date.parse(observedAt) - 30 * 24 * 60 * 60 * 1000).toISOString();
    let endpoint = `https://room.rakuten.co.jp/api/${userId}/collects?api_version=1`;
    const visited = new Set();
    const observations = [];
    const readFailures = { invalidEntries: 0, missingLikes: 0, missingItemUrls: 0, missingTimestamps: 0 };
    let partial = false;
    while (endpoint) {
      if (paginationLimitReached(visited, endpoint, MAX_ROOM_PAGES)) {
        throw new Error('room_pagination_limit');
      }
      visited.add(endpoint);
      const response = await page.evaluate(async (url) => {
        const result = await fetch(url, {
          method: 'GET',
          credentials: 'omit',
          headers: { Accept: 'application/json' },
        });
        let payload = null;
        try { payload = await result.json(); } catch { /* malformed payload remains null */ }
        return { status: result.status, payload };
      }, endpoint);
      if (response.status !== 200) throw new Error(`http_${response.status}`);
      const pageData = roomPayloadToMetricsPage(response.payload, {
        roomUrl,
        userId,
        observedAt,
        allowTerminalNotFound: visited.size > 1,
      });
      for (const key of Object.keys(readFailures)) {
        readFailures[key] += pageData.readFailures[key];
      }
      const selected = selectRecentMetricPage(pageData.entries, cutoffAt, observedAt);
      observations.push(...selected.observations);
      if (Object.values(pageData.readFailures).some((count) => count > 0)) partial = true;
      await logEvent('room', 'public_page_observed', {
        pageCount: visited.size,
        entryCount: pageData.entries.length,
        inWindowCount: selected.observations.length,
        reachedWindowStart: selected.reachedCutoff,
      });
      if (selected.reachedCutoff) break;
      endpoint = pageData.nextPage;
    }
    const unique = deduplicateObservations(observations).observations;
    const normalized = normalizeRoomMetricsFromObservations({
      status: partial ? 'partial' : 'observed',
      errorCode: partial ? 'room_fields_unavailable' : null,
      observedAt,
      roomUrl,
      observations: unique,
    });
    return { ...normalized, readFailures };
  } finally {
    await context?.close();
    await browser.close();
  }
}

function hasReparsePoint(candidate) {
  const absolute = path.resolve(candidate);
  const root = path.parse(absolute).root;
  const parts = absolute.slice(root.length).split(/[\\/]+/).filter(Boolean);
  let current = root;
  for (const part of parts) {
    current = path.join(current, part);
    try {
      const info = lstatSync(current);
      if (info.isSymbolicLink() || (info.attributes & 0x400)) return true;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  return false;
}

function validateOutputRoot(root) {
  const localAppData = process.env.LOCALAPPDATA;
  const runtimeRoot = process.env.INAGO_RUNTIME_ROOT;
  if (!localAppData && !runtimeRoot) throw new Error('local_app_data_unavailable');
  const expected = runtimeRoot
    ? path.resolve(runtimeRoot, 'artifacts', 'metrics')
    : path.resolve(localAppData, 'InagoStep22Private', 'step-22');
  const resolved = path.resolve(root);
  if (resolved.toLowerCase() !== expected.toLowerCase() || hasReparsePoint(resolved)) {
    throw new Error('private_output_root_rejected');
  }
  return resolved;
}

async function writeJsonExclusive(target, value) {
  await writeFile(target, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: 'utf8',
    flag: 'wx',
  });
}

export async function runStep22Collection({ outputRoot, targetDate = jstDate(), deadlineSeconds = 1200 }) {
  const safeRoot = validateOutputRoot(outputRoot);
  const checkedDate = validateDate(targetDate);
  if (!Number.isInteger(deadlineSeconds) || deadlineSeconds < 1 || deadlineSeconds * 1000 > MAX_TIMEOUT_MS) {
    throw new Error('deadline_seconds_invalid');
  }
  const rootInfo = await lstat(safeRoot).catch(() => null);
  if (!rootInfo?.isDirectory() || rootInfo.isSymbolicLink()) {
    throw new Error('private_output_root_missing');
  }
  const startedAt = isoNow();
  const stamp = startedAt.replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
  const attemptDir = path.join(safeRoot, `metrics-${stamp}`);
  await mkdir(attemptDir, { recursive: false });
  const logEvent = async (source, event, details) => {
    const row = { at: isoNow(), source, event, ...details };
    await appendFile(path.join(attemptDir, 'events.jsonl'), `${JSON.stringify(row)}\n`, {
      encoding: 'utf8',
      flag: 'a',
    });
    process.stderr.write(`${JSON.stringify(row)}\n`);
  };
  const deadlineEpochMs = Date.now() + deadlineSeconds * 1000;
  const browserExecutable = chromeExecutable();
  const authStatePath = path.resolve(process.env.INAGO_AFFILIATE_AUTH_STATE
    || path.resolve(PROJECT_ROOT, '..', 'rakuten-room-bakemono-private', 'auth-state.json'));
  const roomUrl = resolveOwnerRoomUrl();
  const affiliateStartedAt = isoNow();
  let affiliate;
  await logEvent('affiliate', 'source_started', {});
  try {
    affiliate = await collectAffiliate({
      browserExecutable,
      authStatePath,
      targetDate: checkedDate,
      deadlineEpochMs,
      logEvent,
    });
    await logEvent('affiliate', 'source_finished', {
      status: affiliate.status,
      dailyDateCount: affiliate.daily.rows.length,
      orderWindowCount: affiliate.orderWindows.length,
      orderCount: affiliate.orders.length,
    });
  } catch (error) {
    await logEvent('affiliate', 'source_failed', { errorCode: safeErrorCode(error) });
    affiliate = {
      status: 'unavailable',
      observedAt: isoNow(),
      errorCode: safeErrorCode(error),
      daily: { status: 'unavailable', errorCode: safeErrorCode(error), rows: [] },
      orders: [],
      orderWindows: [],
    };
  }
  const roomStartedAt = isoNow();
  let room;
  await logEvent('room', 'source_started', {});
  try {
    room = await collectRoom({
      browserExecutable,
      roomUrl,
      observedAt: roomStartedAt,
      deadlineEpochMs,
      logEvent,
    });
    await logEvent('room', 'source_finished', {
      status: room.status,
      postCount: room.posts.length,
      readFailures: room.readFailures || null,
    });
  } catch (error) {
    await logEvent('room', 'source_failed', { errorCode: safeErrorCode(error) });
    room = {
      status: 'unavailable',
      observedAt: isoNow(),
      roomUrl,
      windowStart: null,
      posts: [],
      errorCode: safeErrorCode(error),
    };
  }
  const finishedAt = isoNow();
  const manifest = {
    version: 'step22-live-metrics-1',
    targetDate: checkedDate,
    startedAt,
    finishedAt,
    deadlineSeconds,
    sources: {
      affiliate: {
        status: affiliate.status,
        startedAt: affiliateStartedAt,
        observedAt: affiliate.observedAt,
        dailyDateCount: affiliate.daily?.rows?.length ?? 0,
        orderWindowCount: affiliate.orderWindows?.length ?? 0,
        orderCount: affiliate.orders?.length ?? 0,
        errorCode: affiliate.errorCode || affiliate.daily?.errorCode || null,
      },
      room: {
        status: room.status,
        startedAt: roomStartedAt,
        observedAt: room.observedAt,
        postCount: room.posts?.length ?? 0,
        windowStart: room.windowStart,
        errorCode: room.errorCode || null,
        readFailures: room.readFailures || null,
      },
    },
    readOnly: true,
    sourceAccountUrlOmitted: true,
    rawOutputs: ['affiliate.json', 'room.json'],
  };
  await writeJsonExclusive(path.join(attemptDir, 'affiliate.json'), affiliate);
  await writeJsonExclusive(path.join(attemptDir, 'room.json'), room);
  await writeJsonExclusive(path.join(attemptDir, 'manifest.json'), manifest);
  return { attemptDir, manifest };
}

function isMain() {
  const argvPath = process.argv[1];
  return Boolean(argvPath) && pathToFileURL(path.resolve(argvPath)).href === import.meta.url;
}

async function main() {
  const args = process.argv.slice(2);
  const options = new Map();
  for (let index = 0; index < args.length; index += 2) {
    if (!args[index]?.startsWith('--') || !args[index + 1]) {
      process.stderr.write('Usage: node metrics_live.mjs --output-root <private-root> [--target-date YYYY-MM-DD] [--deadline-seconds N]\n');
      process.exitCode = 2;
      return;
    }
    options.set(args[index], args[index + 1]);
  }
  if (!options.has('--output-root')
    || [...options.keys()].some((key) => !['--output-root', '--target-date', '--deadline-seconds'].includes(key))) {
    process.stderr.write('Usage: node metrics_live.mjs --output-root <private-root> [--target-date YYYY-MM-DD] [--deadline-seconds N]\n');
    process.exitCode = 2;
    return;
  }
  const deadlineSeconds = options.has('--deadline-seconds')
    ? Number(options.get('--deadline-seconds')) : 1200;
  const result = await runStep22Collection({
    outputRoot: options.get('--output-root'),
    targetDate: options.get('--target-date') || jstDate(),
    deadlineSeconds,
  });
  process.stdout.write(`${JSON.stringify({ attemptDir: result.attemptDir, manifest: result.manifest })}\n`);
}

if (isMain()) {
  main().catch((error) => {
    process.stderr.write(`STEP22 metrics collection failed: ${safeErrorCode(error)}\n`);
    process.exitCode = 1;
  });
}
