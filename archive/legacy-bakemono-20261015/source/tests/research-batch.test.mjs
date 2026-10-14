import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {
  buildBatchQueries,
  completeResearchKeywords,
  classifyResearchItems,
  collectApiSearches,
  deduplicateSearchResults,
  summarizeResearchBatch,
  writeResearchSnapshot,
} from '../scripts/research-batch-lib.mjs';

const product = (itemCode, itemName, itemUrl) => ({
  itemCode, itemName, itemUrl, itemPrice: 1980, reviewCount: 10,
  reviewAverage: 4.5, availability: 1,
});

test('検索計画の不足語をジャンル既定語で補い、調査語を5〜8件に保つ', () => {
  const genres = [
    { id: 'cooking', name: '料理' },
    { id: 'kids', name: '子育て' },
  ];
  const keywords = completeResearchKeywords({
    planned: [{ genre: '料理', query: '自動調理' }, { genre: '料理', query: '時短料理' }],
    genres,
    defaults: {
      cooking: ['自動調理', '電気鍋', '時短家電', '作り置き', '圧力鍋'],
      kids: ['ベビー用品', '育児便利', '赤ちゃん玩具', 'おでかけ', '子育て時短'],
    },
  });
  assert.deepEqual(keywords.cooking, ['自動調理', '時短料理', '電気鍋', '時短家電', '作り置き']);
  assert.equal(keywords.kids.length, 5);
  assert.throws(() => completeResearchKeywords({
    planned: Array.from({ length: 9 }, (_, i) => ({ genre: '料理', query: `語${i}` })),
    genres: [genres[0]], defaults: { cooking: [] },
  }), /8件/);
  assert.throws(() => completeResearchKeywords({
    planned: [], genres: [genres[0]], defaults: { cooking: ['一語'] },
  }), /5件/);
});

test('在庫不足ジャンルだけを優先順に検索計画へ展開する', () => {
  const queries = buildBatchQueries([
    { id: 'kids', name: '子育て', shortage: 2 },
    { id: 'home', name: '住まい', shortage: 0 },
    { id: 'cooking', name: '料理', shortage: 3 },
  ], {
    hitsPerQuery: 10,
    minimumDelayMs: 1200,
    keywordsByGenre: {
      kids: ['子育て 時短'],
      cooking: ['時短 おかず', '調理家電 時短'],
    },
  });

  assert.deepEqual(queries.map(({ genreId, keyword }) => [genreId, keyword]), [
    ['kids', '子育て 時短'],
    ['cooking', '時短 おかず'],
    ['cooking', '調理家電 時短'],
  ]);
  assert.ok(queries.every((query) => query.hits === 10));
  assert.throws(() => buildBatchQueries([
    { id: 'home', name: '住まい', shortage: 1 },
  ], { hitsPerQuery: 10, minimumDelayMs: 1200, keywordsByGenre: {} }), /検索語/);
  assert.throws(() => buildBatchQueries([], {
    hitsPerQuery: 31, minimumDelayMs: 1200, keywordsByGenre: {},
  }), /hitsPerQuery/);
});

test('複数検索の結果を順番に取得し、途中失敗後も続行する', async () => {
  const queries = [
    { genreId: 'cooking', keyword: '時短 おかず', hits: 10 },
    { genreId: 'home', keyword: '収納', hits: 10 },
    { genreId: 'kids', keyword: '子育て', hits: 10 },
  ];
  const calls = [];
  const waits = [];
  const outcome = await collectApiSearches({
    queries,
    delayMs: 1200,
    wait: async (milliseconds) => waits.push(milliseconds),
    search: async (query) => {
      calls.push(query.keyword);
      if (query.genreId === 'home') throw new Error('HTTP 503');
      return { kind: 'search', count: 1, page: 1, pageCount: 1, items: [product(`${query.genreId}:1`, '商品', `https://item.rakuten.co.jp/${query.genreId}/1/`)] };
    },
  });

  assert.deepEqual(calls, ['時短 おかず', '収納', '子育て']);
  assert.deepEqual(waits, [1200, 1200]);
  assert.equal(outcome.results.length, 2);
  assert.deepEqual(outcome.errors.map(({ keyword, message }) => [keyword, message]), [['収納', 'HTTP 503']]);
});

test('API結果を商品コードまたは正規化URLで重複統合し、検索元を残す', () => {
  const results = deduplicateSearchResults([
    {
      genreId: 'cooking', genreName: '料理', keyword: '時短',
      response: { count: 2, items: [
        product('shop:item-1', '商品A', 'https://item.rakuten.co.jp/shop/item-1/?track=one'),
        product('shop:item-2', '商品B', 'https://item.rakuten.co.jp/shop/item-2/'),
      ] },
    },
    {
      genreId: 'home', genreName: '住まい', keyword: '収納',
      response: { count: 2, items: [
        product('shop:item-1', '商品A', 'https://item.rakuten.co.jp/shop/item-1/?track=two'),
        product('shop:alias', '商品A別表記', 'https://ITEM.RAKUTEN.CO.JP/shop/item-1/#details'),
      ] },
    },
  ]);

  assert.equal(results.length, 2);
  assert.equal(results[0].duplicateCount, 2);
  assert.deepEqual(results[0].sourceQueries.map(({ keyword }) => keyword), ['時短', '収納']);
  assert.deepEqual(results[0].alternateItemCodes, ['shop:alias']);
  assert.equal(results[0].normalizedUrl, 'https://item.rakuten.co.jp/shop/item-1');
});

test('投稿済み・計画中・台帳登録済み・新規候補を商品URLで照合する', () => {
  const items = [
    product('shop:posted', '投稿済み', 'https://item.rakuten.co.jp/shop/posted/?rafcid=x'),
    product('shop:planned', '計画中', 'https://item.rakuten.co.jp/shop/planned/'),
    product('shop:known', '台帳内', 'https://item.rakuten.co.jp/shop/known/'),
    product('shop:dated-post', '最終投稿日で投稿済み', 'https://item.rakuten.co.jp/shop/dated-post/'),
    product('shop:new', '新規', 'https://item.rakuten.co.jp/shop/new/'),
  ].map((item) => ({ ...item, normalizedUrl: new URL(item.itemUrl).origin + new URL(item.itemUrl).pathname.replace(/\/$/, '') }));
  const products = [
    { ID: 'P01', 商品名: '投稿済み', URL: 'https://item.rakuten.co.jp/shop/posted/', 最終投稿日: '' },
    { ID: 'P02', 商品名: '計画中', URL: 'https://item.rakuten.co.jp/shop/planned/', 最終投稿日: '' },
    { ID: 'P03', 商品名: '台帳内', URL: 'https://item.rakuten.co.jp/shop/known/', 最終投稿日: '' },
    { ID: 'P04', 商品名: '最終投稿日で投稿済み', URL: 'https://item.rakuten.co.jp/shop/dated-post/', 最終投稿日: '2026-09-22' },
  ];
  const classified = classifyResearchItems(items, products, {
    completedIds: ['P01'], plannedIds: ['P02'],
  });

  assert.deepEqual(classified.map(({ status, matchedProductIds, eligible }) => ({ status, matchedProductIds, eligible })), [
    { status: 'already-posted', matchedProductIds: ['P01'], eligible: false },
    { status: 'planned', matchedProductIds: ['P02'], eligible: false },
    { status: 'in-ledger', matchedProductIds: ['P03'], eligible: false },
    { status: 'already-posted', matchedProductIds: ['P04'], eligible: false },
    { status: 'new', matchedProductIds: [], eligible: true },
  ]);
});

test('結果サマリーは重複・既投稿・新規件数を数え、保存時に既存ファイルを上書きしない', async () => {
  const summary = summarizeResearchBatch({
    results: [
      { genreId: 'cooking', keyword: '時短', response: { count: 5, items: [
        product('a:1', 'A', 'https://item.rakuten.co.jp/a/1/'),
        product('b:1', 'B', 'https://item.rakuten.co.jp/b/1/'),
        product('c:1', 'C', 'https://item.rakuten.co.jp/c/1/'),
      ] } },
      { genreId: 'home', keyword: '収納', response: { count: 5, items: [
        product('a:1', 'A', 'https://item.rakuten.co.jp/a/1/?q=1'),
        product('d:1', 'D', 'https://item.rakuten.co.jp/d/1/'),
      ] } },
    ],
    errors: [{ genreId: 'kids', keyword: '子育て', message: 'HTTP 503' }],
    items: [
      { status: 'already-posted' }, { status: 'new' }, { status: 'in-ledger' }, { status: 'planned' },
    ],
  });
  assert.deepEqual(summary, {
    totalQueries: 3, succeededQueries: 2, failedQueries: 1,
    rawResults: 5, uniqueResults: 4, duplicatesRemoved: 1,
    newCandidates: 1, alreadyPosted: 1, alreadyInLedger: 1, planned: 1,
  });

  const directory = await mkdtemp(path.join(os.tmpdir(), 'rakuten-research-batch-'));
  try {
    const snapshot = { summary, items: [] };
    const now = new Date('2026-09-23T00:00:00.123Z');
    const savedPath = await writeResearchSnapshot(directory, snapshot, now);
    assert.deepEqual(JSON.parse(await readFile(savedPath, 'utf8')), snapshot);
    await assert.rejects(writeResearchSnapshot(directory, { summary: 'changed' }, now), { code: 'EEXIST' });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
