import { chromium } from 'playwright-core';
import { access, appendFile, mkdir, readFile, readdir, rename, writeFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { homedir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import {
  jstNow,
  parseDailyTable,
  parseOrderDetailCsv,
  previousDays,
  recentOrderMonths,
  sameMetrics,
  sameOrderDetails,
} from './affiliate-report-lib.mjs';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const defaultReportDir = path.join(projectRoot, 'rakuten-room-kai', 'affiliate-report');
let reportDir = defaultReportDir;
const profileDir = path.join(process.env.LOCALAPPDATA || path.join(homedir(), 'AppData', 'Local'),
  'rakuten-room-bakemono', 'affiliate-playwright-profile');
// CodexのAppDataとWindowsタスクのAppDataは分離される場合があるため、共有できる作業フォルダの隣に置く。
const authStateDir = path.join(path.dirname(projectRoot), 'rakuten-room-bakemono-private');
const authStatePath = path.join(authStateDir, 'auth-state.json');
const reportBase = 'https://affiliate.rakuten.co.jp/report/monthly';
const orderReportBase = 'https://affiliate.rakuten.co.jp/report/order';
const firstActiveDate = '2026-09-23'; // 楽天アフィリエイト登録日。登録前を運用成果に含めない。
const mode = process.argv[2] || 'check';
const execFileAsync = promisify(execFile);

function log(message) { process.stdout.write(`${message}\n`); }

async function writeStatus(message) {
  await mkdir(reportDir, { recursive: true });
  const checkedAt = new Date().toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' });
  await writeFile(path.join(reportDir, 'status.txt'), `確認日時（日本時間）: ${checkedAt}\n${message}\n`, 'utf8');
}

async function lockDownDirectory(directory) {
  await mkdir(directory, { recursive: true });
  if (process.platform !== 'win32') return;
  const { USERDOMAIN, USERNAME } = process.env;
  if (!USERDOMAIN || !USERNAME) throw new Error('Windowsユーザー名を取得できません');
  const account = `${USERDOMAIN}\\${USERNAME}`;
  await execFileAsync('icacls.exe', [directory, '/inheritance:r', '/grant:r',
    `${account}:(OI)(CI)F`, 'SYSTEM:(OI)(CI)F', 'Administrators:(OI)(CI)F', '/C', '/Q']);
  if ((await readdir(directory)).length === 0) return;
  const children = path.join(directory, '*');
  await execFileAsync('icacls.exe', [children, '/grant:r', `${account}:F`, 'SYSTEM:F', 'Administrators:F',
    '/T', '/C', '/Q']);
  await execFileAsync('icacls.exe', [children, '/inheritance:r', '/T', '/C', '/Q']);
  await execFileAsync('icacls.exe', [children, '/grant', `${account}:(OI)(CI)F`, 'SYSTEM:(OI)(CI)F',
    'Administrators:(OI)(CI)F', '/T', '/C', '/Q']);
}

async function lockDownAuthState() {
  await lockDownDirectory(authStateDir);
}

async function appendRun(status, detail = '') {
  await mkdir(reportDir, { recursive: true });
  await appendFile(path.join(reportDir, 'runs.jsonl'), `${JSON.stringify({
    checkedAt: new Date().toISOString(), status, detail,
  })}\n`, 'utf8');
}

async function readLatest() {
  try { return JSON.parse(await readFile(path.join(reportDir, 'latest.json'), 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return { byDate: {}, orderDetailsByMonth: {} }; throw error; }
}

async function saveLatest(data) {
  await mkdir(reportDir, { recursive: true });
  const target = path.join(reportDir, 'latest.json');
  const temporary = `${target}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
  await rename(temporary, target);
}

async function waitForMonthlyTable(page, month) {
  const expected = `${month.slice(0, 4)}年${month.slice(5, 7)}月`;
  await page.goto(`${reportBase}?kpi_date=${month}&date=${month}`, { waitUntil: 'domcontentloaded', timeout: 45000 });
  if (!page.url().startsWith('https://affiliate.rakuten.co.jp/')) {
    throw new Error(`ログインが必要です（遷移先: ${new URL(page.url()).hostname}）。npm run affiliate:login を実行してください`);
  }
  try {
    await page.waitForFunction((label) => {
      const selected = document.querySelector('#monthlyTableSelectedDate');
      const rows = document.querySelectorAll('table.-monthly tbody tr');
      return selected?.value === label && [...rows].some((row) => /^\d{4}-\d{2}-\d{2}$/.test(row.cells[0]?.textContent?.trim() || ''));
    }, expected, { timeout: 45000 });
  } catch {
    throw new Error('日別レポートを取得できません。ログイン状態か画面構成を確認してください');
  }
  const table = await page.locator('table.-monthly').evaluate((element) => ({
    headers: [...element.querySelectorAll('thead th')].map((cell) => cell.textContent.trim()),
    rows: [...element.querySelectorAll('tbody tr')].map((row) => [...row.querySelectorAll('td')].map((cell) => cell.textContent.trim())),
  }));
  return parseDailyTable(table.headers, table.rows, month);
}

async function fetchOrderDetails(page, month) {
  const reportUrl = `${orderReportBase}?order_type=count&date=${month}`;
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.origin === 'https://affiliate.rakuten.co.jp'
      && url.pathname === '/api/report/order'
      && url.searchParams.get('date') === month
      && url.searchParams.get('offset') === '0';
  }, { timeout: 45000 });
  await page.goto(reportUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
  try {
    const response = await responsePromise;
    if (new URL(page.url()).origin !== 'https://affiliate.rakuten.co.jp') {
      throw new Error(`ログインが必要です（遷移先: ${new URL(page.url()).hostname}）`);
    }
    if (!response.ok()) throw new Error(`注文詳細レポート通信がHTTP ${response.status()}でした`);
    await page.waitForFunction(() => {
      const headers = [...document.querySelectorAll('table.-detail thead th')]
        .map((cell) => cell.textContent?.trim() || '');
      return ['発生日時', '成果報酬', '売上金額', 'ジャンル', 'ショップ名', '商品名', 'ステータス', 'リンクタイプ']
        .every((label) => headers.includes(label));
    }, null, { timeout: 45000 });
  } catch (error) {
    await responsePromise.catch(() => {});
    throw new Error('注文詳細レポートを取得できません。ログイン状態か画面構成を確認してください');
  }
  const csvLink = page.locator('a[href*="/api/report/download/order"]');
  const href = await csvLink.getAttribute('href');
  if (!href) throw new Error('注文詳細CSVのリンクを取得できませんでした');
  const csvUrl = new URL(href, page.url());
  if (csvUrl.origin !== 'https://affiliate.rakuten.co.jp'
    || csvUrl.pathname !== '/api/report/download/order'
    || csvUrl.searchParams.get('format') !== 'csv'
    || csvUrl.searchParams.get('date') !== month) {
    throw new Error('注文詳細CSVのリンク先が想定外です');
  }
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 45000 }),
    csvLink.evaluate((element) => element.click()),
  ]);
  const failure = await download.failure();
  if (failure) throw new Error(`注文詳細CSVをダウンロードできませんでした: ${failure}`);
  const stream = await download.createReadStream();
  if (!stream) throw new Error('注文詳細CSVを読み取れませんでした');
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const csv = Buffer.concat(chunks).toString('utf8');
  return { source: csvUrl.href, orders: parseOrderDetailCsv(csv, month) };
}

async function login(context) {
  const page = context.pages()[0] || await context.newPage();
  await page.goto(reportBase, { waitUntil: 'domcontentloaded', timeout: 45000 });
  log('Edgeで楽天アフィリエイトにログインしてください。ID・パスワードをスクリプトには入力しません。');
  log('日別レポートが表示されたら自動でブラウザを閉じます（最大10分）。');
  await page.waitForFunction(() => [...document.querySelectorAll('table.-monthly tbody tr')]
    .some((row) => /^\d{4}-\d{2}-\d{2}$/.test(row.cells[0]?.textContent?.trim() || '')), null, { timeout: 600000 });
  await lockDownAuthState();
  await context.storageState({ path: authStatePath });
  log('ログインを確認しました。');
}

async function check(context) {
  const { date: today, hour } = jstNow();
  const targets = previousDays(today, 7, firstActiveDate);
  const dailyMonths = [...new Set(targets.map((date) => date.slice(0, 7)))];
  // 登録当日も、初回ログインと画面読取の試験ができるよう当月を開く。
  if (dailyMonths.length === 0) dailyMonths.push(today.slice(0, 7));
  const orderMonths = recentOrderMonths(today, targets, firstActiveDate);
  const page = context.pages()[0] || await context.newPage();
  const found = new Map();
  const orderDetails = new Map();
  for (const month of dailyMonths) {
    const rows = await waitForMonthlyTable(page, month);
    for (const [date, row] of rows) found.set(date, row);
  }
  for (const month of orderMonths) {
    orderDetails.set(month, await fetchOrderDetails(page, month));
  }
  const previous = await readLatest();
  const next = {
    ...previous,
    updatedAt: new Date().toISOString(),
    byDate: { ...(previous.byDate || {}) },
    orderDetailsByMonth: { ...(previous.orderDetailsByMonth || {}) },
  };
  const changes = [];
  const orderChanges = [];
  for (const date of targets) {
    const row = found.get(date);
    if (!row) throw new Error(`${date} の行がレポートにありません。0件として保存しません`);
    const entry = {
      ...row,
      status: 'observed',
      provisional: date === targets[0] && date === previousDays(today, 1, firstActiveDate)[0] && hour < 12,
      observedAt: new Date().toISOString(),
      source: `${reportBase}?kpi_date=${date.slice(0, 7)}&date=${date.slice(0, 7)}`,
    };
    const old = next.byDate[date];
    next.byDate[date] = entry;
    if (!sameMetrics(old, entry) || old.provisional !== entry.provisional) {
      changes.push({ ...entry, previous: old ? {
        clicks: old.clicks, ordersUnconfirmed: old.ordersUnconfirmed,
        salesJpyUnconfirmed: old.salesJpyUnconfirmed,
        commissionJpyUnconfirmed: old.commissionJpyUnconfirmed,
        provisional: old.provisional,
      } : null });
    }
  }
  for (const [month, detail] of orderDetails) {
    const entry = {
      month,
      status: 'observed',
      observedAt: new Date().toISOString(),
      source: detail.source,
      orders: detail.orders.filter((order) => order.occurredAt.slice(0, 10) >= firstActiveDate),
    };
    const old = next.orderDetailsByMonth[month];
    next.orderDetailsByMonth[month] = entry;
    if (!sameOrderDetails(old, entry)) {
      orderChanges.push({ ...entry, previous: old ? { status: old.status, orders: old.orders } : null });
    }
  }
  await mkdir(reportDir, { recursive: true });
  if (changes.length) await appendFile(path.join(reportDir, 'observations.jsonl'),
    changes.map((entry) => JSON.stringify(entry)).join('\n') + '\n', 'utf8');
  if (orderChanges.length) await appendFile(path.join(reportDir, 'order-details-observations.jsonl'),
    orderChanges.map((entry) => JSON.stringify(entry)).join('\n') + '\n', 'utf8');
  await saveLatest(next);
  const observedOrders = orderMonths.reduce((sum, month) => sum + next.orderDetailsByMonth[month].orders.length, 0);
  await appendRun('observed', `日別対象 ${targets.length}日・変更 ${changes.length}日、注文明細 ${observedOrders}件・変更 ${orderChanges.length}か月`);
  const orderMessage = `注文明細: ${observedOrders}件（${orderMonths.join('、')}を確認済み）`;
  if (!targets.length) {
    const message = `${today}: 登録日以降の確定した対象日はまだありません。日別表の読取は成功しました。\n${orderMessage}`;
    log(message);
    await writeStatus(message);
  }
  else {
    const yesterday = next.byDate[targets[0]];
    const message = `${targets[0]}: クリック ${yesterday.clicks}、売上 ${yesterday.salesJpyUnconfirmed}円、未確定報酬 ${yesterday.commissionJpyUnconfirmed}円${yesterday.provisional ? '（正午前の暫定値）' : ''}`;
    log(message);
    log(`直近 ${targets.length}日を確認、数値の新規記録・変更 ${changes.length}日。`);
    log(orderMessage);
    await writeStatus(`${message}\n直近 ${targets.length}日を確認。数値の新規記録・変更: ${changes.length}日。\n${orderMessage}`);
  }
}

if (!['login', 'check'].includes(mode)) {
  process.stderr.write('使い方: node scripts/check-affiliate.mjs [login|check]\n');
  process.exitCode = 2;
} else {
  let context;
  let browser;
  try {
    if (process.argv.length > 3) {
      if (mode !== 'check' || process.argv[3] !== '--output-dir' || !process.argv[4] || process.argv.length !== 5) {
        throw new Error('無効な出力先指定です');
      }
      const candidate = path.resolve(process.argv[4]);
      const runRoot = path.join(projectRoot, 'rakuten-room-kai', 'orchestrator', 'runs');
      if (!candidate.startsWith(`${runRoot}${path.sep}`) || path.basename(candidate) !== 'affiliate-report') {
        throw new Error('dry-run出力先はorchestrator run配下に限定されます');
      }
      reportDir = candidate;
    }
    await lockDownDirectory(profileDir);
    await lockDownDirectory(reportDir);
    if (mode === 'login') {
      context = await chromium.launchPersistentContext(profileDir, {
        channel: 'msedge', headless: false, acceptDownloads: false,
        viewport: { width: 1360, height: 900 },
      });
      await login(context);
    } else {
      await access(authStatePath).catch((error) => {
        throw new Error(`保存済みログインを読めません (${error.code})。npm run affiliate:login を実行してください`);
      });
      browser = await chromium.launch({ channel: 'msedge', headless: true });
      context = await browser.newContext({ storageState: authStatePath, acceptDownloads: true,
        viewport: { width: 1360, height: 900 } });
      await check(context);
    }
  } catch (error) {
    const message = error.message?.split('\n')[0] || '不明なエラー';
    process.stderr.write(`取得失敗: ${message}\n`);
    if (mode === 'check' && (process.argv.length === 3 || reportDir !== defaultReportDir)) {
      await appendRun('unavailable', message);
      await writeStatus(`未取得: ${message}\n前回の値を今回の結果として使わないでください。`);
    }
    process.exitCode = 1;
  } finally {
    await context?.close();
    await browser?.close();
  }
}
