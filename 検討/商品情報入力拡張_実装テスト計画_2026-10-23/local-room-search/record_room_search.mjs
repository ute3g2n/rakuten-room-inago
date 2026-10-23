import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = process.cwd();
const requireFromAdapter = createRequire(
  path.join(repoRoot, 'automation', 'inago', 'adapters', 'public_sources.mjs'),
);
const { chromium } = requireFromAdapter('playwright-core');

const outputRoot = path.dirname(fileURLToPath(import.meta.url));
const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
const screenshotsDir = path.join(outputRoot, 'screenshots');
const recordingsDir = path.join(outputRoot, 'recordings');
const framesDir = path.join(recordingsDir, `frames-${timestamp}`);
const resultsDir = path.join(outputRoot, 'results');
const resultPath = path.join(resultsDir, `local-run-${timestamp}.json`);
const tracePath = path.join(recordingsDir, `room-search-${timestamp}.zip`);
const settingsPath = process.env.INAGO_LOCAL_SETTINGS_PATH || path.join(
  repoRoot,
  'rakuten-room-inago',
  'runtime',
  'config',
  'settings.json',
);
const query = process.env.INAGO_LOCAL_ROOM_SEARCH_QUERY || '学び ポケット';
const resultWaitTimeoutMs = Number(process.env.INAGO_LOCAL_RESULT_TIMEOUT_MS || 45_000);
const slowMoMs = Number(process.env.INAGO_LOCAL_SLOWMO_MS || 0);
const recordVideo = process.env.INAGO_LOCAL_VIDEO === '1';
const profileRoot = process.env.LOCALAPPDATA;

if (!profileRoot) throw new Error('LOCALAPPDATA is unavailable');
const settings = JSON.parse(await readFile(settingsPath, 'utf8'));
const chromeExecutable = settings.browser?.chrome_executable;
if (typeof chromeExecutable !== 'string' || !path.isAbsolute(chromeExecutable))
  throw new Error('configured Chrome executable is unavailable');

await Promise.all([
  mkdir(screenshotsDir, { recursive: true }),
  mkdir(recordingsDir, { recursive: true }),
  mkdir(framesDir, { recursive: true }),
  mkdir(resultsDir, { recursive: true }),
]);
const screenshotPath = path.join(screenshotsDir, `room-search-${timestamp}.png`);
const searchBoxScreenshotPath = path.join(
  screenshotsDir,
  `room-search-box-${timestamp}.png`,
);
const url = new URL('https://room.rakuten.co.jp/search/item');
for (const [key, value] of Object.entries({
  keyword: query,
  colle: '',
  comment: '',
  like: '300',
  user_id: '',
  user_name: '',
  original_photo: '0',
})) url.searchParams.set(key, value);

let context;
let page;
let traceStarted = false;
let result;
let resultVideo;
const visualFrames = [];
let stage = 'browser_launch';
try {
  context = await chromium.launchPersistentContext(
    path.join(profileRoot, 'InagoPlaywrightProfile'),
    {
      executablePath: chromeExecutable,
      headless: false,
      slowMo: slowMoMs,
      locale: 'ja-JP',
      viewport: { width: 1440, height: 900 },
      ...(recordVideo
        ? { recordVideo: { dir: recordingsDir, size: { width: 1440, height: 900 } } }
        : {}),
    },
  );
  stage = 'page_create';
  const existingPages = context.pages();
  const pageCountBeforeNavigation = existingPages.length;
  page = existingPages.find(candidate => {
    try {
      return new URL(candidate.url()).hostname !== 'playwright';
    } catch {
      return true;
    }
  }) || await context.newPage();
  await page.bringToFront();
  const video = recordVideo ? page.video() : null;
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  traceStarted = true;
  stage = 'room_navigation';
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 30_000 });

  stage = 'search_box_wait';
  const searchBox = page.locator('#search-box');
  const searchBoxVisible = await searchBox
    .waitFor({ state: 'visible', timeout: 20_000 })
    .then(() => true)
    .catch(() => false);
  const queryInSearchBox = searchBoxVisible
    ? await searchBox.inputValue().catch(() => null)
    : null;

  // Wait for an explicit result state. A blank result list alone is not a no-hit.
  stage = 'result_render_wait';
  const resultCards = page.locator('.items-search > .item');
  const noResultMessage = page.getByText('ご指定の検索条件に該当する商品はありません。', {
    exact: true,
  });
  const resultSamples = [];
  const resultWaitStartedAt = Date.now();
  const deadline = Date.now() + resultWaitTimeoutMs;
  let resultCardCount = 0;
  let noResultMessageVisible = false;
  while (Date.now() < deadline) {
    resultCardCount = await resultCards.count().catch(() => 0);
    noResultMessageVisible = await noResultMessage.isVisible().catch(() => false);
    const framePath = path.join(
      framesDir,
      `frame-${String(resultSamples.length).padStart(3, '0')}.jpg`,
    );
    await page.screenshot({ path: framePath, type: 'jpeg', quality: 70, timeout: 10_000 });
    visualFrames.push(path.relative(outputRoot, framePath).replaceAll('\\', '/'));
    resultSamples.push({
      elapsed_ms: Date.now() - resultWaitStartedAt,
      result_card_count: resultCardCount,
      no_result_message_visible: noResultMessageVisible,
      visual_frame: visualFrames.at(-1),
    });
    if (resultCardCount > 0 || noResultMessageVisible) break;
    await page.waitForTimeout(1_000);
  }
  await page.waitForTimeout(1_000);
  const settledFramePath = path.join(framesDir, 'frame-settled.jpg');
  await page.screenshot({
    path: settledFramePath,
    type: 'jpeg',
    quality: 70,
    timeout: 10_000,
  });
  visualFrames.push(path.relative(outputRoot, settledFramePath).replaceAll('\\', '/'));

  if (process.env.INAGO_LOCAL_PAUSE === '1') {
    stage = 'debug_pause';
    const pauseScreenshotPath = path.join(
      screenshotsDir,
      `room-search-debug-pause-${timestamp}.png`,
    );
    await page.screenshot({ path: pauseScreenshotPath, timeout: 10_000 });
    process.stderr.write(JSON.stringify({
      debug_pause_screenshot: path.relative(outputRoot, pauseScreenshotPath).replaceAll('\\', '/'),
    }) + '\n');
    await page.pause();
  }

  stage = 'screenshot';
  await page.screenshot({ path: screenshotPath, fullPage: false, timeout: 10_000 });
  if (searchBoxVisible)
    await searchBox.screenshot({ path: searchBoxScreenshotPath, timeout: 10_000 });

  result = {
    schema_version: 1,
    run_id: 'INAGO-2026-10-23',
    observed_at: new Date().toISOString(),
    query,
    query_in_search_box: queryInSearchBox,
    search_box_visible: searchBoxVisible,
    page_count_before_navigation: pageCountBeforeNavigation,
    result_card_count: resultCardCount,
    no_result_message_visible: noResultMessageVisible,
    result_status: resultCardCount > 0
      ? 'results'
      : noResultMessageVisible
        ? 'no_results'
        : 'unresolved_at_timeout',
    result_wait_timeout_ms: resultWaitTimeoutMs,
    result_samples: resultSamples,
    screen_capture_mode: 'playwright_screenshot_frames',
    debug_mode: {
      api_log: process.env.DEBUG === 'pw:api',
      slow_mo_ms: slowMoMs,
      video_requested: recordVideo,
    },
    visual_frames: visualFrames,
    screenshot: path.relative(outputRoot, screenshotPath).replaceAll('\\', '/'),
    search_box_screenshot: searchBoxVisible
      ? path.relative(outputRoot, searchBoxScreenshotPath).replaceAll('\\', '/')
      : null,
    trace: path.relative(outputRoot, tracePath).replaceAll('\\', '/'),
    video: null,
  };
  resultVideo = video;
} catch (error) {
  const errorSummary = String(error?.message || '')
    .split(/\r?\n/, 1)[0]
    .replace(/[A-Za-z]:\\Users\\[^\\\s]+/gi, 'C:\\Users\\<user>')
    .slice(0, 240);
  result = {
    schema_version: 1,
    run_id: 'INAGO-2026-10-23',
    observed_at: new Date().toISOString(),
    query,
    failed_stage: stage,
    error_class: error?.name || 'Error',
    error_summary: errorSummary,
    configured_executable_exists: existsSync(chromeExecutable),
    configured_executable_leaf: path.basename(chromeExecutable),
    screenshot: null,
    search_box_screenshot: null,
    trace: path.relative(outputRoot, tracePath).replaceAll('\\', '/'),
    video: null,
  };
  process.exitCode = 1;
} finally {
  if (context && traceStarted) {
    await context.tracing.stop({ path: tracePath }).catch(() => {});
  }
  if (page && !page.isClosed()) await page.close().catch(() => {});
  if (context) await context.close().catch(() => {});
}

if (result) {
  result.trace_status = existsSync(tracePath) ? 'saved' : 'unavailable';
  if (resultVideo) {
    const recordedVideoPath = await resultVideo.path().catch(() => null);
    result.video = recordedVideoPath
      ? path.relative(outputRoot, recordedVideoPath).replaceAll('\\', '/')
      : null;
  }
  result.video_status = result.video ? 'saved' : recordVideo ? 'unavailable' : 'not_requested';
}

await writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
process.stdout.write(`${JSON.stringify(result)}\n`);
