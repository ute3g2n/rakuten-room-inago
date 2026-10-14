import test from 'node:test';
import assert from 'node:assert/strict';
import { scoreHeatEvidence } from '../scripts/heat-scoring-lib.mjs';

const at = '2026-09-25T12:00:00+09:00';
const source = 'https://example.test/observed';

function completeEvidence(overrides = {}) {
  return {
    candidate_id: 'P01',
    product_url: 'https://item.rakuten.co.jp/shop/item/',
    observed_at: at,
    expected_search_queries: ['悩み商品', '用途商品', '属性商品', '追加語4', '追加語5'],
    search_results: [
      { keyword: '悩み商品', page_url: source, observed_at: at, status: 'observed',
        matches: [{ placement: 'pr' }, { placement: 'organic' }] },
      { keyword: '用途商品', page_url: source, observed_at: at, status: 'observed',
        matches: [{ placement: 'organic' }] },
      { keyword: '属性商品', page_url: source, observed_at: at, status: 'observed',
        matches: [{ placement: 'organic' }] },
      { keyword: '追加語4', page_url: source, observed_at: at, status: 'observed', matches: [] },
      { keyword: '追加語5', page_url: source, observed_at: at, status: 'observed', matches: [] },
    ],
    shop: { status: 'observed', source_url: source, review_count: 1000, award_badges: [] },
    images: { status: 'observed', source_url: source, count: 7,
      first_image_has_text_overlay: null, has_comparison_or_use_scene: null,
      material_only: null },
    promotions: { status: 'observed', source_url: source, coupon_present: true,
      points_present: true, coupon_deadline: '2026-09-28T09:59:00+09:00',
      points_deadline: null, small_coupon: null },
    reviews: { status: 'observed', source_url: source, order: 'newest',
      complete: true, reviews: [
        { date: '2026-09-25', stars: 5 },
        { date: '2026-09-24', stars: 5 },
        { date: '2026-09-23', stars: 4 },
      ] },
    ...overrides,
  };
}

test('scores all six legacy criteria from observed facts and preserves their evidence', () => {
  const result = scoreHeatEvidence(completeEvidence());
  assert.deepEqual(Object.values(result.factors).map((factor) => factor.points), [2, 2, 2, 2, 2, 2]);
  assert.equal(result.score, 12);
  assert.equal(result.confirmed_max, 12);
  assert.equal(result.grade, 'A');
  assert.equal(result.status, 'complete');
  assert.equal(result.factors.exposure_duplicates.evidence.keyword, '悩み商品');
  assert.equal(result.factors.exposure_duplicates.evidence.page_url, source);
  assert.equal(result.factors.review_freshness.evidence.last_7_days, 3);
});

test('does not turn absent or unobserved facts into zero and holds 0/0 instead of C', () => {
  const result = scoreHeatEvidence({ candidate_id: 'P02', product_url: source, observed_at: at });
  assert.deepEqual(Object.values(result.factors).map((factor) => factor.points), [null, null, null, null, null, null]);
  assert.equal(result.score, 0);
  assert.equal(result.confirmed_max, 0);
  assert.equal(result.grade, null);
  assert.equal(result.provisional_grade, 'C');
  assert.equal(result.status, 'hold');
});

test('uses per-query first-page repetitions for exposure and distinct observed query hits for keyword exposure', () => {
  const evidence = completeEvidence({
    search_results: [
      { keyword: '一つ目', page_url: source, status: 'observed',
        matches: [{ placement: 'organic' }, { placement: 'organic' }] },
      { keyword: '二つ目', page_url: source, status: 'observed',
        matches: [{ placement: 'organic' }] },
      { keyword: '三つ目', page_url: source, status: 'observed', matches: [] },
      { keyword: '四つ目', page_url: source, status: 'observed', matches: [] },
      { keyword: '五つ目', page_url: source, status: 'observed', matches: [] },
    ],
    expected_search_queries: ['一つ目', '二つ目', '三つ目', '四つ目', '五つ目'],
  });
  const result = scoreHeatEvidence(evidence);
  assert.equal(result.factors.exposure_duplicates.points, 1);
  assert.equal(result.factors.exposure_duplicates.evidence.max_same_page_count, 2);
  assert.equal(result.factors.keyword_exposure.points, 1);
  assert.equal(result.factors.keyword_exposure.evidence.matching_keywords, 2);
});

test('keyword exposure stays unconfirmed until its five to eight planned searches are observed', () => {
  const result = scoreHeatEvidence(completeEvidence({
    expected_search_queries: ['一つ目', '二つ目', '三つ目', '四つ目', '五つ目'],
    search_results: [{ keyword: '一つ目', page_url: source, status: 'observed',
      matches: [{ placement: 'organic' }] }],
  }));
  assert.equal(result.factors.keyword_exposure.points, null);
  assert.match(result.factors.keyword_exposure.reason, /5〜8/);
});

test('scores zero exposure when every planned search was observed and none listed the product', () => {
  const searches = ['一つ目', '二つ目', '三つ目', '四つ目', '五つ目'].map((keyword) => ({
    keyword,
    page_url: source,
    observed_at: at,
    status: 'observed',
    matches: [],
  }));
  const result = scoreHeatEvidence(completeEvidence({
    search_results: searches,
    expected_search_queries: searches.map((search) => search.keyword),
  }));
  assert.equal(result.factors.exposure_duplicates.points, 0);
  assert.equal(result.factors.keyword_exposure.points, 0);
  assert.equal(result.factors.keyword_exposure.evidence.matching_keywords, 0);
});

test('shop review count boundaries and award badge use the legacy thresholds', () => {
  const scoreForShop = (review_count, award_badges = []) => scoreHeatEvidence(completeEvidence({
    shop: { status: 'observed', source_url: source, review_count, award_badges },
  })).factors.shop_strength.points;
  assert.equal(scoreForShop(99), 0);
  assert.equal(scoreForShop(100), 1);
  assert.equal(scoreForShop(999), 1);
  assert.equal(scoreForShop(1000), 2);
  assert.equal(scoreForShop(12, ['ショップ・オブ・ザ・イヤー']), 2);
  assert.equal(scoreForShop(null, ['ショップ・オブ・ザ・イヤー']), 2);
});

test('image scoring distinguishes count-based, overlay, comparison, and unknown evidence', () => {
  const scoreForImages = (images) => scoreHeatEvidence(completeEvidence({
    images: { status: 'observed', source_url: source, ...images },
  })).factors.image_investment.points;
  assert.equal(scoreForImages({ count: 3, material_only: true }), 0);
  assert.equal(scoreForImages({ count: 4, first_image_has_text_overlay: true,
    has_comparison_or_use_scene: false }), 1);
  assert.equal(scoreForImages({ count: 4, first_image_has_text_overlay: true,
    has_comparison_or_use_scene: true }), 2);
  assert.equal(scoreForImages({ count: 7 }), 2);
  assert.equal(scoreForImages({ count: 4, first_image_has_text_overlay: false,
    has_comparison_or_use_scene: false, material_only: null }), null);
});

test('promotion scoring handles no promotion, points only, combined campaigns, and undefined small-coupon threshold', () => {
  const scoreFor = (promotions) => scoreHeatEvidence(completeEvidence({
    promotions: { status: 'observed', source_url: source, ...promotions },
  })).factors.promotion_strength.points;
  assert.equal(scoreFor({ coupon_present: false, points_present: false }), 0);
  assert.equal(scoreFor({ coupon_present: false, points_present: true }), 1);
  assert.equal(scoreFor({ coupon_present: true, points_present: true }), 2);
  assert.equal(scoreFor({ coupon_present: true, points_present: false, small_coupon: true }), 1);
  assert.equal(scoreFor({ coupon_present: true, points_present: false, small_coupon: null }), null);
  assert.equal(scoreFor({ coupon_present: true, points_present: false,
    coupon_deadline: '2026-09-27T23:59:00+09:00' }), 2);
});

test('review scoring keeps the higher overlapping criterion and leaves undefined ratings unconfirmed', () => {
  const scoreFor = (reviews) => scoreHeatEvidence(completeEvidence({
    reviews: { status: 'observed', source_url: source, order: 'newest', complete: true, reviews },
  })).factors.review_freshness;
  assert.equal(scoreFor([{ date: '2026-09-24', stars: 5 }, { date: '2026-09-10', stars: 3 }]).points, 1);
  assert.equal(scoreFor([{ date: '2026-08-01', stars: 5 }, { date: '2026-07-01', stars: 4 }]).points, 0);
  assert.equal(scoreFor([{ date: '2026-09-24', stars: 5 }, { date: '2026-09-23', stars: 4 },
    { date: '2026-09-22', stars: 3 }]).points, 2);
  assert.equal(scoreFor([{ date: '2026-09-24', stars: 5 }, { date: '2026-09-23', stars: 2 },
    { date: '2026-09-22', stars: 3 }]).points, null);
});

test('uses Japan calendar dates for review freshness when observation timestamps are UTC', () => {
  const result = scoreHeatEvidence(completeEvidence({
    observed_at: '2026-09-24T23:30:00Z',
    reviews: { status: 'observed', source_url: source, order: 'newest', complete: true,
      reviews: [{ date: '2026-09-25', stars: 5 }] },
  }));
  assert.equal(result.factors.review_freshness.points, 1);
  assert.equal(result.factors.review_freshness.evidence.last_7_days, 1);
});

test('provisional totals are not linearly rescaled and unknown factors cannot exclude a product as C', () => {
  const evidence = completeEvidence({
    shop: { status: 'unavailable', source_url: source, review_count: null, award_badges: [] },
  });
  const result = scoreHeatEvidence(evidence);
  assert.equal(result.score, 10);
  assert.equal(result.confirmed_max, 10);
  assert.equal(result.provisional_grade, 'A');
  assert.equal(result.grade, null);
  assert.equal(result.status, 'provisional');

  const low = scoreHeatEvidence({
    candidate_id: 'P03', product_url: source, observed_at: at,
    search_results: [{ keyword: '一つ', page_url: source, status: 'observed', matches: [{ placement: 'organic' }] }],
    shop: { status: 'observed', source_url: source, review_count: 50, award_badges: [] },
    images: { status: 'unavailable' }, promotions: { status: 'unavailable' },
    reviews: { status: 'unavailable' },
  });
  assert.equal(low.score, 0);
  assert.equal(low.provisional_grade, 'C');
  assert.equal(low.grade, null);
  assert.equal(low.status, 'hold');
});
