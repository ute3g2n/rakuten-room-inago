import { appendFile, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';

const INITIAL_STATE_MARKER = 'window.__INITIAL_STATE__';
const POST_ID_RE = /^\d+$/;
const JST_OFFSET_MS = 9 * 60 * 60 * 1000;

export function validateRoomUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error('有効なROOM公開URLを指定してください');
  }
  if (url.protocol !== 'https:' || url.hostname !== 'room.rakuten.co.jp') {
    throw new Error('有効なROOM公開URLを指定してください');
  }
  const match = url.pathname.match(/^\/([A-Za-z0-9_-]+)\/items\/?$/);
  if (!match) throw new Error('ROOM URLは /<ROOM名>/items の形式で指定してください');
  return `https://room.rakuten.co.jp/${match[1]}/items`;
}

export function findRoomUrlInText(text) {
  const matches = String(text).match(/https:\/\/room\.rakuten\.co\.jp\/[A-Za-z0-9_-]+\/items\/?(?:\?[^\s]*)?/g) ?? [];
  for (const match of matches) {
    try {
      return validateRoomUrl(match);
    } catch {
      // 次の候補を確認する。
    }
  }
  return null;
}

export function extractInitialState(scriptText) {
  const markerIndex = scriptText.indexOf(INITIAL_STATE_MARKER);
  if (markerIndex < 0) throw new Error('INITIAL_STATEがありません');
  const equalsIndex = scriptText.indexOf('=', markerIndex + INITIAL_STATE_MARKER.length);
  const objectStart = scriptText.indexOf('{', equalsIndex + 1);
  if (equalsIndex < 0 || objectStart < 0) throw new Error('INITIAL_STATEを解析できません');

  let depth = 0;
  let inString = false;
  let escaped = false;
  let objectEnd = -1;
  for (let index = objectStart; index < scriptText.length; index += 1) {
    const character = scriptText[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') inString = true;
    else if (character === '{') depth += 1;
    else if (character === '}') {
      depth -= 1;
      if (depth === 0) {
        objectEnd = index + 1;
        break;
      }
    }
  }
  if (objectEnd < 0) throw new Error('INITIAL_STATEを解析できません');
  try {
    return JSON.parse(scriptText.slice(objectStart, objectEnd));
  } catch {
    throw new Error('INITIAL_STATEを解析できません');
  }
}

function canonicalHttpsUrl(value, label) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${label}を読めません`);
  }
  if (url.protocol !== 'https:') throw new Error(`${label}を読めません`);
  url.search = '';
  url.hash = '';
  url.hostname = url.hostname.toLowerCase();
  url.pathname = url.pathname.replace(/\/+$/, '');
  return url.toString().replace(/\/$/, '');
}

function nonNegativeInteger(value, label) {
  if (!Number.isInteger(value) || value < 0) throw new Error(`${label}を読めません`);
  return value;
}

function validIsoTime(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) throw new Error('取得日時が不正です');
  return date.toISOString();
}

export function jstCalendarRangeStart(observedAt, days) {
  const normalizedObservedAt = validIsoTime(observedAt);
  if (!Number.isInteger(days) || days < 1 || days > 365) {
    throw new Error('日数は1〜365の整数にしてください');
  }
  const shifted = new Date(Date.parse(normalizedObservedAt) + JST_OFFSET_MS);
  const startUtc = Date.UTC(
    shifted.getUTCFullYear(),
    shifted.getUTCMonth(),
    shifted.getUTCDate() - (days - 1),
  ) - JST_OFFSET_MS;
  return new Date(startUtc).toISOString();
}

function roomCreatedAtEpoch(value) {
  const match = String(value ?? '').match(
    /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/,
  );
  if (!match) throw new Error('ROOM投稿日を読めません');
  const parts = match.slice(1).map(Number);
  const [year, month, day, hour, minute, second] = parts;
  const epoch = Date.UTC(year, month - 1, day, hour, minute, second) - JST_OFFSET_MS;
  const roundTrip = new Date(epoch + JST_OFFSET_MS);
  const actual = [
    roundTrip.getUTCFullYear(), roundTrip.getUTCMonth() + 1, roundTrip.getUTCDate(),
    roundTrip.getUTCHours(), roundTrip.getUTCMinutes(), roundTrip.getUTCSeconds(),
  ];
  if (actual.some((part, index) => part !== parts[index])) {
    throw new Error('ROOM投稿日を読めません');
  }
  return epoch;
}

export function collectToObservation(collect, roomUrl, observedAt = new Date().toISOString()) {
  const normalizedRoomUrl = validateRoomUrl(roomUrl);
  if (!POST_ID_RE.test(String(collect?.id ?? ''))) throw new Error('投稿IDを読めません');
  if (!collect?.item?.url) throw new Error('商品URLを読めません');
  const roomSlug = new URL(normalizedRoomUrl).pathname.split('/')[1];
  return {
    postId: String(collect.id),
    postUrl: `https://room.rakuten.co.jp/${roomSlug}/${collect.id}`,
    itemKey: String(collect.item.key ?? ''),
    itemName: String(collect.name ?? collect.item.name ?? '').trim(),
    itemUrl: canonicalHttpsUrl(collect.item.url, '商品URL'),
    content: String(collect.content ?? '').trim(),
    createdAt: String(collect.created_at ?? ''),
    likes: nonNegativeInteger(collect.likes, 'いいね数'),
    comments: nonNegativeInteger(collect.comments ?? 0, 'コメント数'),
    observedAt: validIsoTime(observedAt),
    status: 'observed',
  };
}

export function publicCollectsPayloadToPage(payload, {
  roomUrl, userId, observedAt = new Date().toISOString(), allowTerminalNotFound = false,
}) {
  if (allowTerminalNotFound && payload?.status === 'not found' && payload?.code === 200
    && Array.isArray(payload?.data) && payload.data.length === 0 && !payload?.meta?.next_page) {
    return { observations: [], entries: [], nextPage: null };
  }
  if (payload?.status !== 'success' || payload?.code !== 200 || !Array.isArray(payload?.data)) {
    throw new Error('公開ROOM一覧の応答を読めません');
  }
  if (!/^\d+$/.test(String(userId ?? ''))) throw new Error('ROOMユーザーIDを読めません');

  const entries = payload.data.map((collect) => ({
    observation: collectToObservation(collect, roomUrl, observedAt),
    isPinned: collect.is_pinned === true || collect.is_pinned === 1 || collect.is_pinned === '1',
  }));
  const observations = entries.map((entry) => entry.observation);
  const rawNextPage = payload.meta?.next_page;
  if (!rawNextPage) return { observations, entries, nextPage: null };

  let nextPage;
  try {
    nextPage = new URL(rawNextPage);
  } catch {
    throw new Error('公開ROOM一覧の次ページを読めません');
  }
  const expectedPath = `/api/${userId}/collects`;
  if (nextPage.protocol !== 'https:'
    || nextPage.hostname !== 'room.rakuten.co.jp'
    || nextPage.pathname !== expectedPath) {
    throw new Error('公開ROOM一覧の次ページを読めません');
  }
  return { observations, entries, nextPage: nextPage.toString() };
}

export function selectRecentUnpinnedPage(entries, cutoffAt) {
  const cutoffEpoch = Date.parse(validIsoTime(cutoffAt));
  const observations = [];
  let pinnedExcluded = 0;
  let outOfRange = 0;
  let newestOutOfRangeAt = null;
  let newestOutOfRangeEpoch = Number.NEGATIVE_INFINITY;
  for (const entry of entries) {
    if (entry?.isPinned) {
      pinnedExcluded += 1;
      continue;
    }
    const createdAtEpoch = roomCreatedAtEpoch(entry?.observation?.createdAt);
    if (createdAtEpoch < cutoffEpoch) {
      outOfRange += 1;
      if (createdAtEpoch > newestOutOfRangeEpoch) {
        newestOutOfRangeEpoch = createdAtEpoch;
        newestOutOfRangeAt = entry.observation.createdAt;
      }
      continue;
    }
    observations.push(entry.observation);
  }
  return {
    observations,
    pinnedExcluded,
    outOfRange,
    newestOutOfRangeAt,
    reachedCutoff: outOfRange > 0 && observations.length === 0,
  };
}

export function deduplicateObservations(observations) {
  const unique = [];
  const seen = new Set();
  let duplicates = 0;
  for (const observation of observations) {
    if (seen.has(observation.postId)) {
      duplicates += 1;
      continue;
    }
    seen.add(observation.postId);
    unique.push(observation);
  }
  return { observations: unique, duplicates };
}

export function parseRoomLikesArgs(args) {
  const options = {
    roomUrl: null, maxItems: 30, days: null, outputDir: null, headless: true,
  };
  let maxItemsSpecified = false;
  let daysSpecified = false;
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === '--headful') {
      options.headless = false;
      continue;
    }
    const value = args[index + 1];
    if (argument === '--room-url') {
      if (!value) throw new Error('--room-url の値がありません');
      options.roomUrl = validateRoomUrl(value);
      index += 1;
    } else if (argument === '--max-items') {
      if (!/^\d+$/.test(value ?? '')) throw new Error('--max-items は1〜450の整数にしてください');
      options.maxItems = Number(value);
      if (options.maxItems < 1 || options.maxItems > 450) {
        throw new Error('--max-items は1〜450の整数にしてください');
      }
      maxItemsSpecified = true;
      index += 1;
    } else if (argument === '--days') {
      if (!/^\d+$/.test(value ?? '')) throw new Error('--days は1〜365の整数にしてください');
      options.days = Number(value);
      if (options.days < 1 || options.days > 365) {
        throw new Error('--days は1〜365の整数にしてください');
      }
      daysSpecified = true;
      index += 1;
    } else if (argument === '--output-dir') {
      if (!value) throw new Error('--output-dir の値がありません');
      options.outputDir = value;
      index += 1;
    } else {
      throw new Error(`不明な引数です: ${argument}`);
    }
  }
  if (maxItemsSpecified && daysSpecified) {
    throw new Error('--days と --max-items は同時に指定できません');
  }
  return options;
}

function comparableText(value) {
  return String(value ?? '').replace(/\r\n/g, '\n').trim();
}

function comparableImageUrl(value) {
  if (!value) return '';
  try {
    return canonicalHttpsUrl(value, '画像URL');
  } catch {
    return '';
  }
}

export function matchRecentCollects(cards, recentCollects, roomUrl, observedAt = new Date().toISOString()) {
  const usedPostIds = new Set();
  const resolved = [];
  const unresolved = [];
  for (const card of cards) {
    const imageUrl = comparableImageUrl(card.imageUrl);
    const content = comparableText(card.content);
    const contentMatches = recentCollects.filter((collect) => {
      if (usedPostIds.has(String(collect.id))) return false;
      return comparableText(collect.content) === content;
    });
    const imageMatches = contentMatches.filter((collect) => {
      const images = [collect.image_top?.url, collect.item?.picture?.url]
        .map(comparableImageUrl).filter(Boolean);
      return images.includes(imageUrl);
    });
    const matches = imageMatches.length === 1 ? imageMatches : contentMatches;
    if (matches.length !== 1) {
      unresolved.push(card);
      continue;
    }
    const collect = { ...matches[0], likes: card.likes, comments: card.comments };
    const observation = collectToObservation(collect, roomUrl, observedAt);
    usedPostIds.add(observation.postId);
    resolved.push(observation);
  }
  return { resolved, unresolved };
}

export function listCardFromRaw(raw, index) {
  const values = String(raw?.socialText ?? '').match(/\d[\d,]*/g) ?? [];
  if (values.length < 2) throw new Error(`一覧の${index + 1}件目のいいね数を読めません`);
  const [likes, comments] = values.slice(0, 2).map((value) => Number(value.replaceAll(',', '')));
  if (!raw?.imageUrl || !Number.isSafeInteger(likes) || !Number.isSafeInteger(comments)) {
    throw new Error(`一覧の${index + 1}件目のいいね数等を読めません`);
  }
  return {
    index,
    imageUrl: raw.imageUrl,
    content: String(raw.content ?? '').trim(),
    likes,
    comments,
  };
}

function sameObservation(previous, current) {
  return previous?.postId === current.postId
    && previous?.itemUrl === current.itemUrl
    && previous?.itemKey === current.itemKey
    && previous?.content === current.content
    && previous?.likes === current.likes
    && previous?.comments === current.comments
    && previous?.status === current.status;
}

async function readLatest(outputDir) {
  try {
    return JSON.parse(await readFile(path.join(outputDir, 'latest.json'), 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return { postsById: {} };
    throw error;
  }
}

async function writeJsonAtomic(targetPath, value) {
  const temporaryPath = `${targetPath}.${process.pid}.tmp`;
  await writeFile(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  await rename(temporaryPath, targetPath);
}

export async function appendRoomRun(outputDir, run) {
  await mkdir(outputDir, { recursive: true });
  const entry = {
    observedAt: validIsoTime(run.observedAt ?? new Date().toISOString()),
    status: String(run.status ?? 'unavailable'),
    detail: String(run.detail ?? ''),
    ...(run.counts ? { counts: run.counts } : {}),
  };
  await appendFile(path.join(outputDir, 'runs.jsonl'), `${JSON.stringify(entry)}\n`, 'utf8');
  return entry;
}

export async function persistObservedRoomRun({
  outputDir, roomUrl, observedAt, observations, replacePosts = false, scope = null,
}) {
  const normalizedRoomUrl = validateRoomUrl(roomUrl);
  const normalizedObservedAt = validIsoTime(observedAt);
  if (!Array.isArray(observations)
    || (observations.length === 0 && !(replacePosts && scope?.kind === 'days'))) {
    throw new Error('確認済み投稿がありません。0件として保存しません');
  }
  const uniqueIds = new Set(observations.map((entry) => entry.postId));
  if (uniqueIds.size !== observations.length) throw new Error('投稿IDが重複しています');

  await mkdir(outputDir, { recursive: true });
  const previous = await readLatest(outputDir);
  if (previous.roomUrl && previous.roomUrl !== normalizedRoomUrl) {
    throw new Error('別のROOMの最新値が保存されています。出力先を分けてください');
  }
  const previousPostsById = previous.postsById ?? {};
  const postsById = replacePosts ? {} : { ...previousPostsById };
  const changes = [];
  for (const observation of observations) {
    if (!POST_ID_RE.test(String(observation.postId ?? ''))) throw new Error('投稿IDを読めません');
    const old = previousPostsById[observation.postId];
    postsById[observation.postId] = observation;
    if (!sameObservation(old, observation)) {
      changes.push({ ...observation, previous: old ? {
        likes: old.likes, comments: old.comments, observedAt: old.observedAt,
      } : null });
    }
  }

  if (changes.length > 0) {
    await appendFile(path.join(outputDir, 'observations.jsonl'),
      `${changes.map((entry) => JSON.stringify(entry)).join('\n')}\n`, 'utf8');
  }
  await writeJsonAtomic(path.join(outputDir, 'latest.json'), {
    roomUrl: normalizedRoomUrl,
    updatedAt: normalizedObservedAt,
    ...(scope ? { scope } : {}),
    postsById,
  });
  await appendRoomRun(outputDir, {
    observedAt: normalizedObservedAt,
    status: 'observed',
    detail: `確認 ${observations.length}件、変更 ${changes.length}件`,
    counts: { observed: observations.length, changed: changes.length },
  });
  return { observed: observations.length, changed: changes.length };
}

export function paginationLimitReached(visited, endpoint, maxPages) {
  return visited.has(endpoint) || visited.size >= maxPages;
}
