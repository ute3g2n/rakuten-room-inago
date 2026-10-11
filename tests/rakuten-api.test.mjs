import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import {
  buildRakutenRequest,
  callRakutenApi,
  normalizeRakutenResponse,
} from '../scripts/rakuten-api-lib.mjs';

const credentials = {
  applicationId: 'test-app-id',
  accessKey: 'secret-test-access-key',
};

const item = {
  itemCode: 'shop:item-1',
  itemName: 'テスト商品',
  itemUrl: 'https://item.rakuten.co.jp/shop/item-1/',
  itemPrice: 1980,
  reviewCount: 20,
  reviewAverage: 4.5,
  availability: 1,
};

test('商品検索は現行エンドポイントを使い、キーをURLに入れない', () => {
  const { url, headers } = buildRakutenRequest('search', {
    keyword: '時短 おかず', sort: '-reviewCount', hits: 10, page: 2,
  }, { ...credentials, affiliateId: 'test-affiliate-id' });
  assert.equal(url.pathname, '/ichibams/api/IchibaItem/Search/20260701');
  assert.equal(url.searchParams.get('keyword'), '時短 おかず');
  assert.equal(url.searchParams.get('sort'), '-reviewCount');
  assert.equal(url.searchParams.get('formatVersion'), '2');
  assert.equal(url.searchParams.get('applicationId'), 'test-app-id');
  assert.equal(url.searchParams.get('hits'), '10');
  assert.equal(url.searchParams.get('page'), '2');
  assert.equal(headers.accessKey, credentials.accessKey);
  assert.equal(url.toString().includes(credentials.accessKey), false);
  assert.equal(url.searchParams.has('affiliateId'), false);
});

test('ランキングは別エンドポイントで、ジャンルとページを指定できる', () => {
  const { url } = buildRakutenRequest('ranking', { genreId: '100283', page: 2 }, credentials);
  assert.equal(url.pathname, '/ichibaranking/api/IchibaItem/Ranking/20220601');
  assert.equal(url.searchParams.get('genreId'), '100283');
  assert.equal(url.searchParams.get('page'), '2');
  assert.equal(url.searchParams.has('hits'), false);
});

test('商品検索の応答を整形し、必須項目が欠けた応答は拒否する', () => {
  const result = normalizeRakutenResponse('search', {
    count: 1, page: 1, pageCount: 1, items: [item],
  });
  assert.deepEqual(result.items[0], {
    itemCode: 'shop:item-1', itemName: 'テスト商品',
    itemUrl: 'https://item.rakuten.co.jp/shop/item-1/',
    itemPrice: 1980, rank: null, availability: 1,
    reviewCount: 20, reviewAverage: 4.5,
    shopCode: null, shopName: null, genreId: null, affiliateUrl: null,
  });
  assert.throws(() => normalizeRakutenResponse('search', { items: [{ itemName: '不完全' }] }), /ID・商品名・URL/);
});

test('ランキングの旧ラップ形式も読み、順位を保持する', () => {
  const result = normalizeRakutenResponse('ranking', {
    title: '総合ランキング', lastBuildDate: '2026-09-23 12:00',
    items: [{ item: { ...item, rank: 1 } }],
  });
  assert.equal(result.items[0].rank, 1);
  assert.equal(result.title, '総合ランキング');
});

test('fetch呼び出しでaccessKeyヘッダーを使い、応答を処理する', async () => {
  let called = 0;
  const fetchImpl = async (url, init) => {
    called += 1;
    assert.equal(url.searchParams.get('keyword'), 'スープ');
    assert.equal(init.headers.accessKey, credentials.accessKey);
    return new Response(JSON.stringify({ count: 1, page: 1, pageCount: 1, items: [item] }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };
  const result = await callRakutenApi('search', { keyword: 'スープ', hits: 1 }, credentials, { fetchImpl });
  assert.equal(called, 1);
  assert.equal(result.items[0].itemCode, item.itemCode);
});

test('ローカルHTTPサーバーとの実通信で検索結果を受け取る', async () => {
  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    assert.equal(url.pathname, '/search');
    assert.equal(url.searchParams.get('keyword'), 'スープ');
    assert.equal(request.headers.accesskey, credentials.accessKey);
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ count: 1, page: 1, pageCount: 1, items: [item] }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  try {
    const result = await callRakutenApi('search', { keyword: 'スープ', hits: 1 }, credentials, {
      endpoints: { search: `http://127.0.0.1:${port}/search` },
    });
    assert.equal(result.items[0].itemCode, item.itemCode);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test('ローカルHTTPサーバーとの実通信でランキング結果を受け取る', async () => {
  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    assert.equal(url.pathname, '/ranking');
    assert.equal(url.searchParams.get('genreId'), '100283');
    assert.equal(request.headers.accesskey, credentials.accessKey);
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ title: '菓子ランキング', items: [{ ...item, rank: 1 }] }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  try {
    const result = await callRakutenApi('ranking', { genreId: '100283' }, credentials, {
      endpoints: { ranking: `http://127.0.0.1:${port}/ranking` },
    });
    assert.equal(result.items[0].rank, 1);
    assert.equal(result.title, '菓子ランキング');
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test('429は自動再試行せず、秘密情報をエラーに含めない', async () => {
  let called = 0;
  const fetchImpl = async () => {
    called += 1;
    return new Response(JSON.stringify({
      error: 'too_many_requests', error_description: credentials.accessKey,
    }), { status: 429 });
  };
  await assert.rejects(
    callRakutenApi('ranking', {}, credentials, { fetchImpl }),
    (error) => error.message.includes('429') &&
      error.message.includes('時間を空けて') &&
      !error.message.includes(credentials.accessKey),
  );
  assert.equal(called, 1);
});

test('5xxと不正JSONを成功した商品候補として扱わない', async () => {
  await assert.rejects(
    callRakutenApi('search', { keyword: 'スープ', hits: 1 }, credentials, {
      fetchImpl: async () => new Response(JSON.stringify({ error: 'server_error' }), { status: 503 }),
    }),
    /HTTP 503/,
  );
  await assert.rejects(
    callRakutenApi('search', { keyword: 'スープ', hits: 1 }, credentials, {
      fetchImpl: async () => new Response('{broken', { status: 200 }),
    }),
    /JSON以外/,
  );
});

test('パラメーターと認証情報を送信前に検証する', () => {
  assert.throws(() => buildRakutenRequest('search', {}, credentials), /いずれかが必要/);
  assert.throws(() => buildRakutenRequest('search', { keyword: 'スープ', hits: 31 }, credentials), /hits/);
  assert.throws(() => buildRakutenRequest('ranking', { page: 35 }, credentials), /page/);
  assert.throws(() => buildRakutenRequest('ranking', {}, { ...credentials, accessKey: '' }), /RAKUTEN_ACCESS_KEY/);
});
