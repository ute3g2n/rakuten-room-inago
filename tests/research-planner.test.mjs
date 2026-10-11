import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildResearchBrief,
  normalizeProductUrl,
  parseCompletedPosts,
  parseProductLedger,
  renderMarkdownBrief,
} from '../scripts/research-planner-lib.mjs';

const productLedger = `# 候補\n\n## 候補一覧\n\n|ID|商品名|URL|確認日|最終投稿日|\n|---|---|---|---|---|\n|P01|料理A|https://item.rakuten.co.jp/shop/a/?scid=x|2026-09-23||\n|P02|料理B|https://item.rakuten.co.jp/shop/b/|2026-09-16||\n|P03|料理C|https://item.rakuten.co.jp/shop/c/|2026-09-23|2026-09-22|\n|P04|新候補|https://item.rakuten.co.jp/shop/d/|2026-09-23||\n`;

const postsLog = `# 投稿ログ\n\n## 直近の投稿\n\n|日付|商品|実施|\n|---|---|---|\n|2026-09-22|P03 料理C|実施済み|\n|2026-09-23|P01 料理A|計画|\n`;

const plan = {
  provisionalAcceptanceRate: 0.4,
  maxPagesPerBatch: 20,
  freshnessDays: 7,
  genres: [
    { id: 'cooking', name: '料理', postsPerDay: 1, inventoryTarget: 3 },
    { id: 'home', name: '住まい', postsPerDay: 1, inventoryTarget: 1 },
  ],
};

test('商品台帳から候補を読み、実施済みだけを除外。計画は残し、期限超過と未分類は在庫に含めない', () => {
  const brief = buildResearchBrief({
    products: parseProductLedger(productLedger),
    posts: parseCompletedPosts(postsLog),
    plan,
    genreMap: { P01: 'cooking', P02: 'cooking', P03: 'cooking' },
    asOf: '2026-09-23',
  });

  assert.deepEqual(brief.postedIds, ['P03']);
  assert.deepEqual(brief.plannedIds, ['P01']);
  assert.deepEqual(brief.eligibleCandidates.map((candidate) => candidate.id), ['P01']);
  assert.deepEqual(brief.staleIds, ['P02']);
  assert.deepEqual(brief.unmappedIds, ['P04']);
  assert.deepEqual(brief.genres.map(({ stocked, shortage, estimatedPages }) => ({ stocked, shortage, estimatedPages })), [
    { stocked: 1, shortage: 2, estimatedPages: 5 },
    { stocked: 0, shortage: 1, estimatedPages: 3 },
  ]);
  assert.deepEqual(brief.totals, {
    inventoryTarget: 4, stocked: 1, shortage: 3, estimatedPages: 8, estimatedBatches: 2,
  });
});

test('同一URLの候補を二重在庫に数えず、確認リストへ出す', () => {
  const duplicateLedger = productLedger.replace(
    '|P04|新候補|https://item.rakuten.co.jp/shop/d/|2026-09-23||',
    '|P04|新候補|https://item.rakuten.co.jp/shop/a/?other=1|2026-09-23||',
  );
  const brief = buildResearchBrief({
    products: parseProductLedger(duplicateLedger),
    posts: parseCompletedPosts(postsLog),
    plan,
    genreMap: { P01: 'cooking', P02: 'cooking', P03: 'cooking', P04: 'home' },
    asOf: '2026-09-23',
  });

  assert.deepEqual(brief.duplicateUrls[0].ids, ['P01', 'P04']);
  assert.deepEqual(brief.eligibleCandidates.map((candidate) => candidate.id), []);
  assert.match(renderMarkdownBrief(brief), /同一URLの候補：P01\/P04/);
});

test('重複IDや不正日付を見逃さない', () => {
  const duplicateIdLedger = productLedger.replace(
    '|P04|新候補|https://item.rakuten.co.jp/shop/d/|2026-09-23||',
    '|P01|新候補|https://item.rakuten.co.jp/shop/d/|2026-09-23||',
  );
  assert.throws(() => parseProductLedger(duplicateIdLedger), /P01 が重複/);
  assert.throws(() => buildResearchBrief({
    products: parseProductLedger(productLedger),
    posts: parseCompletedPosts(postsLog),
    plan,
    genreMap: {},
    asOf: '2026-02-30',
  }), /日付はYYYY-MM-DD/);
});

test('楽天商品URLはクエリ・フラグメントを落として比較する', () => {
  assert.equal(normalizeProductUrl('https://ITEM.RAKUTEN.CO.JP/shop/item/?x=1#top'),
    'https://item.rakuten.co.jp/shop/item');
});
