import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { normalizeProductUrl } from './research-planner-lib.mjs';

function addUnique(list, value) {
  if (value != null && !list.includes(value)) list.push(value);
}

export function completeResearchKeywords({ planned, genres, defaults, minimum = 5, maximum = 8 }) {
  if (!Array.isArray(planned) || !Array.isArray(genres) || !defaults || typeof defaults !== 'object') {
    throw new Error('検索計画の補完入力が不正です');
  }
  const names = new Map(genres.map((genre) => [genre.name, genre.id]));
  const byId = Object.fromEntries(genres.map((genre) => [genre.id, []]));
  for (const item of planned) {
    const id = names.get(item?.genre);
    if (!id || typeof item.query !== 'string' || !item.query.trim()) {
      throw new Error('検索計画に不正なジャンルまたは検索語があります');
    }
    addUnique(byId[id], item.query.trim());
  }
  for (const genre of genres) {
    if (byId[genre.id].length > maximum) throw new Error(`${genre.name}の検索語は最大${maximum}件です`);
    const fallback = defaults[genre.id];
    if (!Array.isArray(fallback)) throw new Error(`${genre.name}の既定検索語がありません`);
    for (const keyword of fallback) {
      if (byId[genre.id].length >= minimum) break;
      if (typeof keyword === 'string' && keyword.trim()) addUnique(byId[genre.id], keyword.trim());
    }
    if (byId[genre.id].length < minimum) throw new Error(`${genre.name}の検索語が${minimum}件に満たません`);
  }
  return byId;
}

export function buildBatchQueries(researchQueue, config) {
  if (!Array.isArray(researchQueue)) throw new Error('調査キューの形式が不正です');
  if (!Number.isInteger(config?.hitsPerQuery) || config.hitsPerQuery < 1 || config.hitsPerQuery > 30) {
    throw new Error('hitsPerQuery は1〜30の整数にしてください');
  }
  if (!Number.isInteger(config?.minimumDelayMs) || config.minimumDelayMs < 1000) {
    throw new Error('minimumDelayMs は1000ミリ秒以上にしてください');
  }
  if (!config.keywordsByGenre || typeof config.keywordsByGenre !== 'object' ||
      Array.isArray(config.keywordsByGenre)) {
    throw new Error('keywordsByGenre の形式が不正です');
  }

  const queries = [];
  for (const genre of researchQueue.filter((item) => item.shortage > 0)) {
    const keywords = config.keywordsByGenre[genre.id];
    if (!Array.isArray(keywords) || keywords.length === 0 ||
        keywords.some((keyword) => typeof keyword !== 'string' || !keyword.trim())) {
      throw new Error(`ジャンル「${genre.name}」の検索語が設定されていません`);
    }
    for (const keyword of keywords) {
      queries.push({
        genreId: genre.id,
        genreName: genre.name,
        keyword: keyword.trim(),
        hits: config.hitsPerQuery,
      });
    }
  }
  return queries;
}

export async function collectApiSearches({
  queries,
  search,
  delayMs = 1200,
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
}) {
  if (!Array.isArray(queries) || typeof search !== 'function') {
    throw new Error('検索クエリまたはAPI検索関数が不正です');
  }
  if (!Number.isInteger(delayMs) || delayMs < 1000) {
    throw new Error('API検索の間隔は1000ミリ秒以上にしてください');
  }

  const results = [];
  const errors = [];
  for (let index = 0; index < queries.length; index += 1) {
    const query = queries[index];
    try {
      const response = await search(query);
      results.push({ ...query, response });
    } catch (error) {
      errors.push({
        genreId: query.genreId,
        genreName: query.genreName,
        keyword: query.keyword,
        message: error instanceof Error ? error.message : '検索に失敗しました',
      });
    }
    if (index < queries.length - 1) await wait(delayMs);
  }
  return { results, errors };
}

export function deduplicateSearchResults(searchResults) {
  const items = [];
  const byCode = new Map();
  const byUrl = new Map();

  for (const searchResult of searchResults) {
    for (const item of searchResult.response.items) {
      const normalizedUrl = normalizeProductUrl(item.itemUrl);
      const existing = byCode.get(item.itemCode) ?? byUrl.get(normalizedUrl);
      const source = {
        genreId: searchResult.genreId,
        genreName: searchResult.genreName,
        keyword: searchResult.keyword,
      };

      if (existing) {
        existing.duplicateCount += 1;
        if (!existing.sourceQueries.some((entry) =>
          entry.genreId === source.genreId && entry.keyword === source.keyword)) {
          existing.sourceQueries.push(source);
        }
        addUnique(existing.itemCodes, item.itemCode);
        addUnique(existing.normalizedUrls, normalizedUrl);
        if (item.itemUrl !== existing.itemUrl) addUnique(existing.alternateItemUrls, item.itemUrl);
        byCode.set(item.itemCode, existing);
        byUrl.set(normalizedUrl, existing);
      } else {
        const entry = {
          ...item,
          normalizedUrl,
          normalizedUrls: [normalizedUrl],
          itemCodes: [item.itemCode],
          alternateItemUrls: [],
          sourceQueries: [source],
          duplicateCount: 0,
        };
        items.push(entry);
        byCode.set(item.itemCode, entry);
        byUrl.set(normalizedUrl, entry);
      }
    }
  }

  return items.map((item) => ({
    ...item,
    alternateItemCodes: item.itemCodes.filter((itemCode) => itemCode !== item.itemCode),
  }));
}

export function classifyResearchItems(items, products, posts) {
  const productsByUrl = new Map();
  const postedIds = new Set(posts.completedIds);
  const plannedIds = new Set(posts.plannedIds);

  for (const product of products) {
    const normalizedUrl = normalizeProductUrl(product.URL);
    productsByUrl.set(normalizedUrl, [...(productsByUrl.get(normalizedUrl) ?? []), product]);
    if (product['最終投稿日']) postedIds.add(product.ID);
  }

  return items.map((item) => {
    const urls = item.normalizedUrls ?? [item.normalizedUrl ?? normalizeProductUrl(item.itemUrl)];
    const matchedProducts = [...new Map(urls.flatMap((url) =>
      (productsByUrl.get(url) ?? []).map((product) => [product.ID, product]))).values()];
    const matchedProductIds = matchedProducts.map((product) => product.ID);
    const matchedPostedProductIds = matchedProductIds.filter((id) => postedIds.has(id));
    const matchedPlannedProductIds = matchedProductIds.filter((id) => plannedIds.has(id));
    let status = 'new';
    if (matchedPostedProductIds.length > 0) status = 'already-posted';
    else if (matchedPlannedProductIds.length > 0) status = 'planned';
    else if (matchedProductIds.length > 0) status = 'in-ledger';

    return {
      ...item,
      matchedProductIds,
      matchedPostedProductIds,
      matchedPlannedProductIds,
      status,
      eligible: status === 'new',
    };
  });
}

export function summarizeResearchBatch({ results, errors, items }) {
  const rawResults = results.reduce((total, result) => total + result.response.items.length, 0);
  const count = (status) => items.filter((item) => item.status === status).length;
  return {
    totalQueries: results.length + errors.length,
    succeededQueries: results.length,
    failedQueries: errors.length,
    rawResults,
    uniqueResults: items.length,
    duplicatesRemoved: rawResults - items.length,
    newCandidates: count('new'),
    alreadyPosted: count('already-posted'),
    alreadyInLedger: count('in-ledger'),
    planned: count('planned'),
  };
}

export async function writeResearchSnapshot(outputDirectory, snapshot, now = new Date()) {
  const timestamp = now.toISOString().replace(/[-:.]/g, '');
  const filename = `api-results-${timestamp}.json`;
  await mkdir(outputDirectory, { recursive: true });
  const outputPath = path.join(outputDirectory, filename);
  await writeFile(outputPath, `${JSON.stringify(snapshot, null, 2)}\n`, {
    encoding: 'utf8',
    flag: 'wx',
  });
  return outputPath;
}
