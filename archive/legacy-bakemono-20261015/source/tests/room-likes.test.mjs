import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

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
} from '../scripts/room-likes-lib.mjs';

const ROOM_URL = 'https://room.rakuten.co.jp/room_5fd26c075d/items';

function sampleCollect(overrides = {}) {
  return {
    id: '1700394252119122',
    created_at: '2026-09-23 21:06:05',
    likes: 57,
    comments: 3,
    content: 'テスト投稿です\n#オリジナル写真',
    name: 'テスト商品',
    item: {
      key: 'shop-code:item-code',
      name: 'テスト商品',
      url: 'https://item.rakuten.co.jp/shop-code/item-code/?scid=room_pc_id_reg',
    },
    user: { username: 'room_5fd26c075d' },
    ...overrides,
  };
}

test('ROOMの商品一覧URLだけを受け付け、クエリを取り除く', () => {
  assert.equal(validateRoomUrl(`${ROOM_URL}?l-id=test`), ROOM_URL);
  assert.throws(() => validateRoomUrl('https://example.com/room_5fd26c075d/items'), /ROOM公開URL/);
  assert.throws(() => validateRoomUrl('https://room.rakuten.co.jp/room_5fd26c075d/profile'), /items/);
  assert.throws(() => validateRoomUrl('javascript:alert(1)'), /ROOM公開URL/);
});

test('アカウント文書からROOM URLを取り出す', () => {
  assert.equal(findRoomUrlInText(`名前：test\nROOM URL：${ROOM_URL}\n`), ROOM_URL);
  assert.equal(findRoomUrlInText('ROOM URL：未設定'), null);
});

test('後続スクリプトを含むINITIAL_STATEからJSONだけを安全に読む', () => {
  const script = `window.__INITIAL_STATE__ = {"pageType":"itemdetail","collectData":{"content":"{中括弧}と\\\"引用符\\\"","likes":0}};\n`
    + 'window.__REWIRED_SCHEMAS__ = ["pc"];';
  assert.deepEqual(extractInitialState(script), {
    pageType: 'itemdetail', collectData: { content: '{中括弧}と"引用符"', likes: 0 },
  });
  assert.throws(() => extractInitialState('window.other = {};'), /INITIAL_STATE/);
  assert.throws(() => extractInitialState('window.__INITIAL_STATE__ = {bad};'), /解析/);
});

test('投稿詳細を保存用の観測値へ変換し、商品URLを正規化する', () => {
  assert.deepEqual(collectToObservation(sampleCollect(), ROOM_URL, '2026-09-23T12:34:56.000Z'), {
    postId: '1700394252119122',
    postUrl: 'https://room.rakuten.co.jp/room_5fd26c075d/1700394252119122',
    itemKey: 'shop-code:item-code',
    itemName: 'テスト商品',
    itemUrl: 'https://item.rakuten.co.jp/shop-code/item-code',
    content: 'テスト投稿です\n#オリジナル写真',
    createdAt: '2026-09-23 21:06:05',
    likes: 57,
    comments: 3,
    observedAt: '2026-09-23T12:34:56.000Z',
    status: 'observed',
  });
});

test('欠落・不正な投稿値を0件として扱わない', () => {
  assert.throws(() => collectToObservation(sampleCollect({ likes: undefined }), ROOM_URL), /いいね数/);
  assert.throws(() => collectToObservation(sampleCollect({ likes: -1 }), ROOM_URL), /いいね数/);
  assert.throws(() => collectToObservation(sampleCollect({ id: 'abc' }), ROOM_URL), /投稿ID/);
  assert.throws(() => collectToObservation(sampleCollect({ item: null }), ROOM_URL), /商品URL/);
});

test('公開ROOM一覧の応答を投稿観測値と安全な次ページへ変換する', () => {
  const payload = {
    status: 'success',
    code: 200,
    data: [sampleCollect()],
    meta: {
      next_page: 'https://room.rakuten.co.jp/api/1000001434379224/collects?limit=20&after_id=1700394252119122',
    },
  };

  const page = publicCollectsPayloadToPage(payload, {
    roomUrl: ROOM_URL,
    userId: '1000001434379224',
    observedAt: '2026-09-23T15:00:00.000Z',
  });

  assert.equal(page.observations.length, 1);
  assert.equal(page.observations[0].postId, '1700394252119122');
  assert.equal(page.observations[0].likes, 57);
  assert.equal(page.entries[0].isPinned, false);
  assert.match(page.nextPage, /^https:\/\/room\.rakuten\.co\.jp\/api\/1000001434379224\/collects\?/);
});

test('JSTの暦日で当日を含む30日間の開始時刻を求める', () => {
  assert.equal(
    jstCalendarRangeStart('2026-09-23T14:59:59.000Z', 30),
    '2026-08-24T15:00:00.000Z',
  );
  assert.equal(
    jstCalendarRangeStart('2026-09-23T15:00:00.000Z', 30),
    '2026-08-25T15:00:00.000Z',
  );
  assert.throws(() => jstCalendarRangeStart('invalid', 30), /取得日時/);
});

test('古い固定投稿や期間外投稿が混在しても、期間内の通常投稿を選んで巡回を続ける', () => {
  const observedAt = '2026-09-23T12:00:00.000Z';
  const cutoffAt = jstCalendarRangeStart(observedAt, 30);
  const page = publicCollectsPayloadToPage({
    status: 'success',
    code: 200,
    data: [
      sampleCollect({ id: '1700000000000001', created_at: '2026-01-01 10:00:00', is_pinned: true }),
      sampleCollect({ id: '1700000000000002', created_at: '2026-09-23 10:00:00' }),
      sampleCollect({ id: '1700000000000003', created_at: '2026-08-25 00:00:00' }),
      sampleCollect({ id: '1700000000000004', created_at: '2026-08-24 23:59:59' }),
    ],
    meta: {},
  }, {
    roomUrl: ROOM_URL, userId: '1000001434379224', observedAt,
  });

  const selected = selectRecentUnpinnedPage(page.entries, cutoffAt);

  assert.deepEqual(selected.observations.map((entry) => entry.postId), [
    '1700000000000002', '1700000000000003',
  ]);
  assert.equal(selected.pinnedExcluded, 1);
  assert.equal(selected.outOfRange, 1);
  assert.equal(selected.newestOutOfRangeAt, '2026-08-24 23:59:59');
  assert.equal(selected.reachedCutoff, false);
});

test('期間内の通常投稿がないページまで進んだときだけ期間境界へ到達と判定する', () => {
  const observedAt = '2026-09-23T12:00:00.000Z';
  const page = publicCollectsPayloadToPage({
    status: 'success',
    code: 200,
    data: [
      sampleCollect({ id: '1700000000000001', created_at: '2026-01-01 10:00:00', is_pinned: true }),
      sampleCollect({ id: '1700000000000002', created_at: '2026-08-24 23:59:59' }),
    ],
    meta: {},
  }, {
    roomUrl: ROOM_URL, userId: '1000001434379224', observedAt,
  });

  const selected = selectRecentUnpinnedPage(
    page.entries, jstCalendarRangeStart(observedAt, 30),
  );

  assert.equal(selected.observations.length, 0);
  assert.equal(selected.pinnedExcluded, 1);
  assert.equal(selected.outOfRange, 1);
  assert.equal(selected.newestOutOfRangeAt, '2026-08-24 23:59:59');
  assert.equal(selected.reachedCutoff, true);
});

test('巡回上限と同一ページ再訪を停止として判定する', () => {
  assert.equal(paginationLimitReached(new Set(['one']), 'two', 30), false);
  assert.equal(paginationLimitReached(new Set(['one']), 'one', 30), true);
  assert.equal(paginationLimitReached(new Set(Array.from({ length: 30 }, (_, n) => `${n}`)), 'next', 30), true);
  assert.equal(paginationLimitReached(new Set(Array.from({ length: 100 }, (_, n) => `${n}`)), 'next', 100), true);
});

test('ページをまたぐ同一投稿IDは先頭の観測だけを残す', () => {
  const first = collectToObservation(sampleCollect(), ROOM_URL, '2026-09-23T12:00:00.000Z');
  const duplicate = { ...first, likes: 999 };
  const second = collectToObservation(sampleCollect({ id: '1700394252119123' }), ROOM_URL,
    '2026-09-23T12:00:00.000Z');

  const result = deduplicateObservations([first, duplicate, second]);

  assert.deepEqual(result.observations.map((entry) => entry.postId), [
    '1700394252119122', '1700394252119123',
  ]);
  assert.equal(result.observations[0].likes, 57);
  assert.equal(result.duplicates, 1);
});

test('公開ROOM一覧の壊れた応答や別ホストの次ページを拒否する', () => {
  assert.throws(() => publicCollectsPayloadToPage({
    status: 'success', code: 200, data: [{ ...sampleCollect(), likes: null }], meta: {},
  }, {
    roomUrl: ROOM_URL, userId: '1000001434379224', observedAt: '2026-09-23T15:00:00.000Z',
  }), /いいね数/);

  assert.throws(() => publicCollectsPayloadToPage({
    status: 'success', code: 200, data: [sampleCollect()],
    meta: { next_page: 'https://example.com/api/1000001434379224/collects?after_id=1' },
  }, {
    roomUrl: ROOM_URL, userId: '1000001434379224', observedAt: '2026-09-23T15:00:00.000Z',
  }), /次ページ/);
});

test('2ページ目の空のnot found終端だけを正常な巡回終了として扱う', () => {
  const terminal = { status: 'not found', code: 200, data: [], meta: {} };
  const options = { roomUrl: ROOM_URL, userId: '1000001434379224', observedAt: '2026-09-23T15:00:00.000Z' };
  assert.throws(() => publicCollectsPayloadToPage(terminal, options), /応答を読めません/);
  assert.deepEqual(publicCollectsPayloadToPage(terminal, { ...options, allowTerminalNotFound: true }),
    { observations: [], entries: [], nextPage: null });
  assert.throws(() => publicCollectsPayloadToPage({ ...terminal, data: [sampleCollect()] },
    { ...options, allowTerminalNotFound: true }), /応答を読めません/);
});

test('巡回CLIの件数・出力先・表示モードを検証する', () => {
  assert.deepEqual(parseRoomLikesArgs([
    '--room-url', ROOM_URL, '--max-items', '15', '--output-dir', 'tmp/likes', '--headful',
  ]), {
    roomUrl: ROOM_URL, maxItems: 15, days: null, outputDir: 'tmp/likes', headless: false,
  });
  assert.deepEqual(parseRoomLikesArgs([]), {
    roomUrl: null, maxItems: 30, days: null, outputDir: null, headless: true,
  });
  assert.equal(parseRoomLikesArgs(['--days', '30']).days, 30);
  assert.throws(() => parseRoomLikesArgs(['--max-items', '0']), /1〜450/);
  assert.throws(() => parseRoomLikesArgs(['--max-items', '451']), /1〜450/);
  assert.throws(() => parseRoomLikesArgs(['--days', '0']), /1〜365/);
  assert.throws(() => parseRoomLikesArgs(['--days', '366']), /1〜365/);
  assert.throws(() => parseRoomLikesArgs(['--days', '30', '--max-items', '10']), /同時に指定/);
  assert.throws(() => parseRoomLikesArgs(['--unknown']), /不明な引数/);
});

test('一覧カードを埋め込み済み直近投稿へ画像と本文で一意に照合する', () => {
  const recent = sampleCollect({
    image_top: { url: 'https://room.r10s.jp/example/post.jpg' },
  });
  const cards = [
    {
      index: 0,
      imageUrl: 'https://room.r10s.jp/example/post.jpg?thum=133&fitin=200:600',
      content: 'テスト投稿です\n#オリジナル写真', likes: 58, comments: 4,
    },
    {
      index: 1, imageUrl: 'https://room.r10s.jp/example/unknown.jpg',
      content: '未照合', likes: 1, comments: 0,
    },
  ];
  const matched = matchRecentCollects(cards, [recent], ROOM_URL, '2026-09-23T15:00:00.000Z');
  assert.equal(matched.resolved.length, 1);
  assert.equal(matched.resolved[0].postId, recent.id);
  assert.equal(matched.resolved[0].likes, 58);
  assert.equal(matched.resolved[0].comments, 4);
  assert.deepEqual(matched.unresolved.map((entry) => entry.index), [1]);
});

test('画像配信URLが異なっても本文が一意なら直近投稿へ照合する', () => {
  const recent = sampleCollect({
    image_top: { url: 'https://room.r10s.jp/example/original.jpg' },
  });
  const cards = [{
    index: 0,
    imageUrl: 'https://thumbnail.image.rakuten.co.jp/example/resized.jpg',
    content: recent.content,
    likes: 12,
    comments: 1,
  }];

  const matched = matchRecentCollects(cards, [recent], ROOM_URL, '2026-09-23T15:00:00.000Z');

  assert.equal(matched.resolved.length, 1);
  assert.equal(matched.resolved[0].postId, recent.id);
  assert.equal(matched.resolved[0].likes, 12);
  assert.equal(matched.unresolved.length, 0);
});

test('同じ画像・本文の候補が複数ある場合は推測で投稿IDを決めない', () => {
  const first = sampleCollect({ image_top: { url: 'https://room.r10s.jp/example/same.jpg' } });
  const second = { ...first, id: '1700394252119123' };
  const cards = [{
    index: 0, imageUrl: 'https://room.r10s.jp/example/same.jpg?thum=133',
    content: first.content, likes: 1, comments: 0,
  }];
  const matched = matchRecentCollects(cards, [first, second], ROOM_URL);
  assert.equal(matched.resolved.length, 0);
  assert.equal(matched.unresolved.length, 1);
});

test('投稿カードの反応専用領域から、いいね数とコメント数だけを読む', () => {
  assert.deepEqual(listCardFromRaw({
    imageUrl: 'https://room.r10s.jp/example.jpg?thum=133',
    content: '投稿本文',
    socialText: '\n 57　　0 \n',
  }, 4), {
    index: 4,
    imageUrl: 'https://room.r10s.jp/example.jpg?thum=133',
    content: '投稿本文',
    likes: 57,
    comments: 0,
  });
  assert.throws(() => listCardFromRaw({
    imageUrl: 'https://room.r10s.jp/example.jpg', content: '投稿本文', socialText: '反応なし',
  }, 0), /いいね数/);
});

test('最新値を更新し、同値の再実行では観測履歴を重複させない', async () => {
  const outputDir = await mkdtemp(path.join(tmpdir(), 'room-likes-test-'));
  try {
    const first = collectToObservation(sampleCollect(), ROOM_URL, '2026-09-23T12:00:00.000Z');
    const firstResult = await persistObservedRoomRun({
      outputDir, roomUrl: ROOM_URL, observedAt: first.observedAt, observations: [first],
    });
    assert.equal(firstResult.changed, 1);

    const same = { ...first, observedAt: '2026-09-23T13:00:00.000Z' };
    const sameResult = await persistObservedRoomRun({
      outputDir, roomUrl: ROOM_URL, observedAt: same.observedAt, observations: [same],
    });
    assert.equal(sameResult.changed, 0);

    const changed = { ...same, likes: 58, observedAt: '2026-09-23T14:00:00.000Z' };
    const changedResult = await persistObservedRoomRun({
      outputDir, roomUrl: ROOM_URL, observedAt: changed.observedAt, observations: [changed],
    });
    assert.equal(changedResult.changed, 1);

    const latest = JSON.parse(await readFile(path.join(outputDir, 'latest.json'), 'utf8'));
    assert.equal(latest.postsById[first.postId].likes, 58);
    assert.equal(latest.postsById[first.postId].observedAt, changed.observedAt);

    const observationLines = (await readFile(path.join(outputDir, 'observations.jsonl'), 'utf8')).trim().split('\n');
    assert.equal(observationLines.length, 2);
    assert.deepEqual(observationLines.map((line) => JSON.parse(line).likes), [57, 58]);

    const runLines = (await readFile(path.join(outputDir, 'runs.jsonl'), 'utf8')).trim().split('\n');
    assert.equal(runLines.length, 3);
  } finally {
    await rm(outputDir, { recursive: true, force: true });
  }
});

test('取得失敗は実行履歴だけへ記録し、前回の最新値を上書きしない', async () => {
  const outputDir = await mkdtemp(path.join(tmpdir(), 'room-likes-test-'));
  try {
    const observation = collectToObservation(sampleCollect(), ROOM_URL, '2026-09-23T12:00:00.000Z');
    await persistObservedRoomRun({
      outputDir, roomUrl: ROOM_URL, observedAt: observation.observedAt, observations: [observation],
    });
    const before = await readFile(path.join(outputDir, 'latest.json'), 'utf8');

    await appendRoomRun(outputDir, {
      observedAt: '2026-09-23T13:00:00.000Z', status: 'unavailable', detail: '公開ページを読めません',
    });

    assert.equal(await readFile(path.join(outputDir, 'latest.json'), 'utf8'), before);
    const runs = (await readFile(path.join(outputDir, 'runs.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(runs.at(-1).status, 'unavailable');
  } finally {
    await rm(outputDir, { recursive: true, force: true });
  }
});

test('期間巡回の最新値は範囲外だった以前の投稿を残さない', async () => {
  const outputDir = await mkdtemp(path.join(tmpdir(), 'room-likes-range-test-'));
  try {
    const old = collectToObservation(sampleCollect({ id: '1700000000000001' }), ROOM_URL,
      '2026-09-23T11:00:00.000Z');
    const recent = collectToObservation(sampleCollect({ id: '1700000000000002' }), ROOM_URL,
      '2026-09-23T11:00:00.000Z');
    await persistObservedRoomRun({
      outputDir, roomUrl: ROOM_URL, observedAt: old.observedAt, observations: [old, recent],
    });

    const second = { ...recent, observedAt: '2026-09-23T12:00:00.000Z' };
    const result = await persistObservedRoomRun({
      outputDir,
      roomUrl: ROOM_URL,
      observedAt: second.observedAt,
      observations: [second],
      replacePosts: true,
      scope: { kind: 'days', days: 30, startAt: '2026-08-24T15:00:00.000Z' },
    });

    assert.equal(result.changed, 0);
    const latest = JSON.parse(await readFile(path.join(outputDir, 'latest.json'), 'utf8'));
    assert.deepEqual(Object.keys(latest.postsById), ['1700000000000002']);
    assert.deepEqual(latest.scope, {
      kind: 'days', days: 30, startAt: '2026-08-24T15:00:00.000Z',
    });
  } finally {
    await rm(outputDir, { recursive: true, force: true });
  }
});

test('期間巡回で確認済み0件なら空の最新範囲として保存できる', async () => {
  const outputDir = await mkdtemp(path.join(tmpdir(), 'room-likes-empty-range-test-'));
  try {
    const old = collectToObservation(sampleCollect(), ROOM_URL, '2026-09-23T11:00:00.000Z');
    await persistObservedRoomRun({
      outputDir, roomUrl: ROOM_URL, observedAt: old.observedAt, observations: [old],
    });

    const result = await persistObservedRoomRun({
      outputDir,
      roomUrl: ROOM_URL,
      observedAt: '2026-09-23T12:00:00.000Z',
      observations: [],
      replacePosts: true,
      scope: { kind: 'days', days: 30, startAt: '2026-08-24T15:00:00.000Z' },
    });

    assert.deepEqual(result, { observed: 0, changed: 0 });
    const latest = JSON.parse(await readFile(path.join(outputDir, 'latest.json'), 'utf8'));
    assert.deepEqual(latest.postsById, {});
    const runs = (await readFile(path.join(outputDir, 'runs.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(runs.at(-1).status, 'observed');
    assert.equal(runs.at(-1).counts.observed, 0);
  } finally {
    await rm(outputDir, { recursive: true, force: true });
  }
});
