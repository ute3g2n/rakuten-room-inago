const ENDPOINTS = {
  search: 'https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701',
  ranking: 'https://openapi.rakuten.co.jp/ichibaranking/api/IchibaItem/Ranking/20220601',
};

const SEARCH_SORTS = new Set([
  'standard', '+affiliateRate', '-affiliateRate',
  '+reviewCount', '-reviewCount', '+reviewAverage', '-reviewAverage',
  '+itemPrice', '-itemPrice', '+updateTimestamp', '-updateTimestamp',
]);

function credential(value, name) {
  if (typeof value !== 'string' || !value.trim() || /[\r\n]/.test(value)) {
    throw new Error(`${name} が未設定または不正です`);
  }
  return value.trim();
}

function integerInRange(value, name, minimum, maximum) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} は${minimum}～${maximum}の整数で指定してください`);
  }
  return value;
}

function optionalText(value, name) {
  if (value === undefined || value === null || value === '') return null;
  if (typeof value !== 'string' || !value.trim() || /[\r\n]/.test(value)) {
    throw new Error(`${name} が不正です`);
  }
  return value.trim();
}

function genreId(value) {
  if (value === undefined || value === null || value === '') return null;
  const text = String(value);
  if (!/^[1-9]\d*$/.test(text)) throw new Error('genreId は正の整数で指定してください');
  return text;
}

export function buildRakutenRequest(kind, options, credentials, endpoints = ENDPOINTS) {
  if (!Object.hasOwn(endpoints, kind)) throw new Error(`未対応のAPIです: ${kind}`);
  const appId = credential(credentials.applicationId, 'RAKUTEN_APP_ID');
  const accessKey = credential(credentials.accessKey, 'RAKUTEN_ACCESS_KEY');
  const url = new URL(endpoints[kind]);
  url.searchParams.set('applicationId', appId);
  url.searchParams.set('format', 'json');
  url.searchParams.set('formatVersion', '2');

  if (kind === 'search') {
    const keyword = optionalText(options.keyword, 'keyword');
    const shopCode = optionalText(options.shopCode, 'shopCode');
    const itemCode = optionalText(options.itemCode, 'itemCode');
    const selectedGenre = genreId(options.genreId);
    if (!keyword && !shopCode && !itemCode && !selectedGenre) {
      throw new Error('検索には keyword・genreId・itemCode・shopCode のいずれかが必要です');
    }
    if (keyword) url.searchParams.set('keyword', keyword);
    if (shopCode) url.searchParams.set('shopCode', shopCode);
    if (itemCode) url.searchParams.set('itemCode', itemCode);
    if (selectedGenre) url.searchParams.set('genreId', selectedGenre);
    url.searchParams.set('hits', String(integerInRange(options.hits ?? 10, 'hits', 1, 30)));
    url.searchParams.set('page', String(integerInRange(options.page ?? 1, 'page', 1, 100)));
    if (options.sort !== undefined) {
      if (!SEARCH_SORTS.has(options.sort)) throw new Error('sort の指定値が楽天APIの対応値にありません');
      url.searchParams.set('sort', options.sort);
    }
  } else {
    const selectedGenre = genreId(options.genreId);
    if (selectedGenre) url.searchParams.set('genreId', selectedGenre);
    url.searchParams.set('page', String(integerInRange(options.page ?? 1, 'page', 1, 34)));
    if (options.period !== undefined) {
      if (options.period !== 'realtime') throw new Error('period は realtime のみ指定できます');
      url.searchParams.set('period', options.period);
    }
  }
  return { url, headers: { accessKey } };
}

function numericValue(value) {
  if (value === undefined || value === null || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function normalizeItems(payload) {
  const items = payload?.items ?? payload?.Items;
  if (!Array.isArray(items)) throw new Error('楽天APIの応答に商品配列がありません');
  return items.map((wrapper, index) => {
    const raw = wrapper?.item ?? wrapper?.Item ?? wrapper;
    if (typeof raw?.itemCode !== 'string' || typeof raw?.itemName !== 'string' ||
        typeof raw?.itemUrl !== 'string') {
      throw new Error(`楽天APIの商品${index + 1}件目にID・商品名・URLのいずれかがありません`);
    }
    return {
      itemCode: raw.itemCode,
      itemName: raw.itemName,
      itemUrl: raw.itemUrl,
      itemPrice: numericValue(raw.itemPrice),
      rank: numericValue(raw.rank),
      availability: numericValue(raw.availability),
      reviewCount: numericValue(raw.reviewCount),
      reviewAverage: numericValue(raw.reviewAverage),
      shopCode: raw.shopCode ?? null,
      shopName: raw.shopName ?? null,
      genreId: raw.genreId == null ? null : String(raw.genreId),
      affiliateUrl: raw.affiliateUrl ?? null,
    };
  });
}

export function normalizeRakutenResponse(kind, payload) {
  const items = normalizeItems(payload);
  if (kind === 'search') {
    return {
      kind,
      count: numericValue(payload.count),
      page: numericValue(payload.page),
      pageCount: numericValue(payload.pageCount),
      items,
    };
  }
  if (kind === 'ranking') {
    return {
      kind,
      title: payload.title ?? null,
      lastBuildDate: payload.lastBuildDate ?? null,
      items,
    };
  }
  throw new Error(`未対応のAPIです: ${kind}`);
}

export async function callRakutenApi(kind, options, credentials, {
  fetchImpl = globalThis.fetch,
  endpoints = ENDPOINTS,
  timeoutMs = 15_000,
} = {}) {
  const request = buildRakutenRequest(kind, options, credentials, endpoints);
  let response;
  try {
    response = await fetchImpl(request.url, {
      method: 'GET',
      headers: request.headers,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    if (error?.name === 'TimeoutError' || error?.name === 'AbortError') {
      throw new Error('楽天APIの応答がタイムアウトしました');
    }
    throw new Error('楽天APIへ接続できませんでした');
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`楽天APIがJSON以外を返しました（HTTP ${response.status}）`);
  }
  if (!response.ok) {
    const code = typeof payload?.error === 'string' && /^[a-z_]+$/.test(payload.error)
      ? ` / ${payload.error}` : '';
    const retry = response.status === 429 ? '。時間を空けて再実行してください' : '';
    throw new Error(`楽天APIがHTTP ${response.status}${code}を返しました${retry}`);
  }
  return normalizeRakutenResponse(kind, payload);
}
