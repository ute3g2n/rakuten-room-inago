import test from 'node:test';
import assert from 'node:assert/strict';
import { buildRakutenSearchUrl, buildShopHomeUrl, collectProductGalleryImageUrls, extractPointPromotionText, extractProductReviewCountText, makeSearchEvidence, normalizeRakutenItemUrl, parseProductReviewCount, parsePromotionDeadline, parseRecentReviewRows, parseShopReviewCount } from '../scripts/heat-evidence-collector-lib.mjs';

const candidate = {
  id: 'P13',
  url: 'https://item.rakuten.co.jp/f042081-kakuda/aa0599/?rafcid=tracking',
};

test('normalizes Rakuten item pages by shop/item path and ignores tracking parameters', () => {
  assert.equal(normalizeRakutenItemUrl(candidate.url), 'https://item.rakuten.co.jp/f042081-kakuda/aa0599');
  assert.equal(normalizeRakutenItemUrl('https://item.rakuten.co.jp/other/item/'), 'https://item.rakuten.co.jp/other/item');
  assert.equal(normalizeRakutenItemUrl('https://example.test/other/item'), null);
});

test('builds the shop top-page URL from a Rakuten item URL', () => {
  assert.equal(buildShopHomeUrl('https://item.rakuten.co.jp/myrepi-2/4903301234999ss/?rafcid=tracking'),
    'https://item.rakuten.co.jp/myrepi-2/');
  assert.equal(buildShopHomeUrl('https://item.rakuten.co.jp/ecprime500/20250227115629_187/'),
    'https://item.rakuten.co.jp/ecprime500/');
  assert.equal(buildShopHomeUrl('https://example.com/shop/item/'), null);
});

test('builds a first-page Rakuten search URL with a safely encoded query', () => {
  assert.equal(buildRakutenSearchUrl('電気圧力鍋 時短'),
    'https://search.rakuten.co.jp/search/mall/%E9%9B%BB%E6%B0%97%E5%9C%A7%E5%8A%9B%E9%8D%8B%20%E6%99%82%E7%9F%AD/');
  assert.throws(() => buildRakutenSearchUrl('  '));
});

test('collects product gallery images outside the empty item root and deduplicates URLs', () => {
  assert.deepEqual(collectProductGalleryImageUrls([
    'https://tshop.r10s.jp/f042081-kakuda/cabinet/item/main.jpg?fitin=720%3A720',
    'https://image.rakuten.co.jp/f042081-kakuda/cabinet/item/sub.png',
    'https://tshop.r10s.jp/f042081-kakuda/cabinet/item/main.jpg?another=tracking',
    'https://tshop.r10s.jp/other-shop/cabinet/item/other.jpg',
    'data:image/gif;base64,broken',
  ], 'f042081-kakuda'), [
    'https://tshop.r10s.jp/f042081-kakuda/cabinet/item/main.jpg',
    'https://image.rakuten.co.jp/f042081-kakuda/cabinet/item/sub.png',
  ]);
});

test('parses product review totals in either ASCII or full-width parentheses', () => {
  assert.equal(parseProductReviewCount('レビュー (1,234件)'), 1234);
  assert.equal(parseProductReviewCount('レビュー（1,234件）'), 1234);
  assert.equal(parseProductReviewCount('レビュー（ 2 件）'), 2);
  assert.equal(parseProductReviewCount('レビューはありません'), null);
});

test('extracts only the target product review count from the review section', () => {
  const text = 'この商品を購入された方のレビュー すべてのレビューを見る（ 2 件） 他の商品レビュー（999件）';
  assert.equal(extractProductReviewCountText(text), '（ 2 件）');
  assert.equal(extractProductReviewCountText('この商品を購入された方のレビュー レビュー投稿なし'), null);
});

test('records exact item matches, first-page position, PR/organic placement, and source URL', () => {
  const evidence = makeSearchEvidence({
    candidate,
    keyword: '電気圧力鍋',
    pageUrl: 'https://search.rakuten.co.jp/search/mall/%E9%9B%BB%E6%B0%97%E5%9C%A7%E5%8A%9B%E9%8D%8B/',
    observedAt: '2026-09-25T12:00:00+09:00',
    cards: [
      { url: 'https://item.rakuten.co.jp/f042081-kakuda/aa0599/?scid=ad', position: 2,
        placement: 'pr', title: '圧力鍋' },
      { url: 'https://item.rakuten.co.jp/f042081-kakuda/aa0599/', position: 11,
        placement: 'organic', title: '電気圧力鍋' },
      { url: 'https://item.rakuten.co.jp/other/aa0599/', position: 12,
        placement: 'organic', title: '別店舗の同型商品' },
    ],
  });
  assert.equal(evidence.status, 'observed');
  assert.equal(evidence.matches.length, 2);
  assert.deepEqual(evidence.matches.map((match) => match.placement), ['pr', 'organic']);
  assert.deepEqual(evidence.matches.map((match) => match.position), [2, 11]);
  assert.match(evidence.page_url, /search\/mall/);
});

test('keeps failed searches unavailable instead of treating them as zero matches', () => {
  const evidence = makeSearchEvidence({ candidate, keyword: '電気圧力鍋',
    pageUrl: 'https://search.rakuten.co.jp/search/mall/%E9%9B%BB%E6%B0%97%E5%9C%A7%E5%8A%9B%E9%8D%8B/',
    observedAt: '2026-09-25T12:00:00+09:00', error: 'navigation timed out' });
  assert.equal(evidence.status, 'unavailable');
  assert.equal(evidence.matches, null);
  assert.equal(evidence.error, 'navigation timed out');
});

test('parses explicit and same-month Japanese promotion deadlines without inventing vague dates', () => {
  assert.equal(parsePromotionDeadline('10%OFFクーポン 2026/09/28 9:59まで', '2026-09-25T12:00:00+09:00'),
    '2026-09-28T09:59:00+09:00');
  assert.equal(parsePromotionDeadline('ポイントアップ期間：2026/09/25(金) 23:59まで', '2026-09-25T12:00:00+09:00'),
    '2026-09-25T23:59:00+09:00');
  assert.equal(parsePromotionDeadline('19日20時～28日9:59', '2026-09-25T12:00:00+09:00'),
    '2026-09-28T09:59:00+09:00');
  assert.equal(parsePromotionDeadline('次回使えるクーポン', '2026-09-25T12:00:00+09:00'), null);
  assert.equal(parsePromotionDeadline('8日 9:59まで', '2026-09-25T12:00:00+09:00'), null);
});

test('preserves visible point-up copy and deadline as source evidence', () => {
  const body = '今この商品はポイント5倍\n通常商品 ⇒ 全会員5倍 ポイントアップキャンペーン中\nポイントアップ期間：2026/09/25(金) 23:59まで';
  assert.equal(extractPointPromotionText(body), body);
  assert.equal(extractPointPromotionText('ポイント1倍\n通常ポイント'), 'ポイント1倍');
});

test('parses review dates and stars only from explicit review rows and shop totals', () => {
  const rows = parseRecentReviewRows('レビュー一覧\n並び替え\n新着レビュー順\n5\n2026/09/25\nレビュー本文\n4\n2026/09/23\n別のレビュー');
  assert.deepEqual(rows, [{ stars: 5, date: '2026-09-25' }, { stars: 4, date: '2026-09-23' }]);
  assert.equal(parseShopReviewCount('ショップレビュー\n店舗名\n4.64\n(1,234件)'), 1234);
  assert.equal(parseShopReviewCount('ショップレビュー\n店舗名\nレビューをお待ちしています'), null);
  assert.equal(parseShopReviewCount('マイレピ P&Gストア\n★4.81 （4,392件）\n39ショップ'), 4392);
  assert.equal(parseShopReviewCount('マイレピ P&Gストア\n4.81 (0件)\n39ショップ'), 0);
  assert.equal(parseShopReviewCount('店舗ページ\n商品レビュー (999件)'), null);
});
