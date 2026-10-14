import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { callRakutenApi } from './rakuten-api-lib.mjs';
import { loadRakutenCredentials } from './rakuten-credentials.mjs';
import {
  buildBatchQueries,
  classifyResearchItems,
  collectApiSearches,
  completeResearchKeywords,
  deduplicateSearchResults,
  summarizeResearchBatch,
  writeResearchSnapshot,
} from './research-batch-lib.mjs';
import {
  buildResearchBrief,
  currentJstDate,
  parseCompletedPosts,
  parseProductLedger,
} from './research-planner-lib.mjs';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const RESEARCH_DIR = path.join(ROOT, 'rakuten-room-kai', 'research');

function parseArgs(args) {
  const options = { outputDir: null, briefJson: null, planJson: null, help: false };
  for (let index = 0; index < args.length; index += 1) {
    const flag = args[index];
    if (flag === '--help') options.help = true;
    else if (flag === '--output-dir' && args[index + 1]) options.outputDir = path.resolve(args[++index]);
    else if (flag === '--brief-json' && args[index + 1]) options.briefJson = path.resolve(args[++index]);
    else if (flag === '--plan-json' && args[index + 1]) options.planJson = path.resolve(args[++index]);
    else throw new Error('Usage: research:collect [--output-dir PATH] [--brief-json PATH] [--plan-json PATH]');
  }
  return options;
}

async function readJson(relativePath) {
  return JSON.parse(await readFile(path.join(ROOT, relativePath), 'utf8'));
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write('Usage: research:collect [--output-dir PATH] [--brief-json PATH] [--plan-json PATH]\n');
    return;
  }
  const collectedAt = new Date();
  const asOf = currentJstDate(collectedAt);
  const [productsText, postsText, plan, genreMap, searchConfig] = await Promise.all([
    readFile(path.join(ROOT, 'rakuten-room', 'products.md'), 'utf8'),
    readFile(path.join(ROOT, 'rakuten-room', 'posts-log.md'), 'utf8'),
    readJson('rakuten-room-kai/research/genre-plan.json'),
    readJson('rakuten-room-kai/research/genre-map.json'),
    readJson('rakuten-room-kai/research/search-queries.json'),
  ]);
  const products = parseProductLedger(productsText);
  const posts = parseCompletedPosts(postsText);
  const inventoryBrief = options.briefJson
    ? JSON.parse(await readFile(options.briefJson, 'utf8'))
    : buildResearchBrief({ products, posts, plan, genreMap, asOf });
  if (!Array.isArray(inventoryBrief.researchQueue) || !Number.isInteger(inventoryBrief.totals?.shortage)) {
    throw new Error('調査ブリーフJSONが不正です');
  }
  let effectiveSearchConfig = searchConfig;
  if (options.planJson) {
    const modelPlan = JSON.parse(await readFile(options.planJson, 'utf8'));
    if (!Array.isArray(modelPlan.search_queries)) throw new Error('検索計画JSONが不正です');
    const keywordsByGenre = completeResearchKeywords({
      planned: modelPlan.search_queries,
      genres: plan.genres,
      defaults: searchConfig.keywordsByGenre,
    });
    effectiveSearchConfig = { ...searchConfig, keywordsByGenre };
  }
  const queries = buildBatchQueries(inventoryBrief.researchQueue, effectiveSearchConfig);

  let results = [];
  let errors = [];
  if (queries.length > 0) {
    const credentials = await loadRakutenCredentials();
    ({ results, errors } = await collectApiSearches({
      queries,
      delayMs: searchConfig.minimumDelayMs,
      search: (query) => callRakutenApi('search', {
        keyword: query.keyword,
        hits: query.hits,
      }, credentials),
    }));
  }

  const items = classifyResearchItems(
    deduplicateSearchResults(results),
    products,
    posts,
  );
  const summary = summarizeResearchBatch({ results, errors, items });
  const snapshot = {
    schemaVersion: 1,
    asOf,
    collectedAt: collectedAt.toISOString(),
    inventory: {
      shortage: inventoryBrief.totals.shortage,
      queue: inventoryBrief.researchQueue.map(({ id, name, shortage, coverageDays }) => ({
        genreId: id, genreName: name, shortage, coverageDays,
      })),
    },
    summary,
    queries: [
      ...results.map(({ genreId, genreName, keyword, hits, response }) => ({
        genreId, genreName, keyword, hits,
        returned: response.items.length,
        totalMatches: response.count,
        pageCount: response.pageCount,
      })),
      ...errors.map(({ genreId, genreName, keyword, message }) => ({
        genreId, genreName, keyword, error: message,
      })),
    ],
    items,
  };

  const outputDirectory = options.outputDir || path.join(RESEARCH_DIR, 'api-results');
  const outputPath = await writeResearchSnapshot(outputDirectory, snapshot, collectedAt);
  process.stdout.write(`保存先: ${path.relative(ROOT, outputPath)}\n`);
  process.stdout.write(`検索 ${summary.succeededQueries}/${summary.totalQueries}件成功`);
  if (summary.failedQueries > 0) process.stdout.write(`、失敗 ${summary.failedQueries}件`);
  process.stdout.write(`\nAPI結果 ${summary.rawResults}件 → 重複除外後 ${summary.uniqueResults}件\n`);
  process.stdout.write(`新規候補 ${summary.newCandidates}件／投稿済み ${summary.alreadyPosted}件／計画中 ${summary.planned}件／台帳登録済み ${summary.alreadyInLedger}件\n`);
  if (summary.failedQueries > 0) process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`research:collect: ${error.message}\n`);
  process.exitCode = 1;
});
