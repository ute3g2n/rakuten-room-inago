import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveAvailability, validateCandidates, verifyBatch } from '../scripts/verify-product-pages-lib.mjs';

const product = { id: 'C001', url: 'https://item.rakuten.co.jp/shop/item/' };

test('selected product page snapshot keeps observed facts and missing fields distinct', async () => {
  const result = await verifyBatch([product], async () => ({
    pageUrl: product.url, name: '確認済み商品', priceYen: 1200,
    reviewCount: 0, availability: 'in_stock',
  }), '2026-09-25T04:00:00Z');
  assert.equal(result.observed, 1);
  assert.equal(result.snapshots[0].price_yen, 1200);
  assert.equal(result.snapshots[0].review_count, 0);
  assert.equal(result.snapshots[0].coupon_deadline, null);
});

test('fetch failure remains unavailable instead of zero', async () => {
  const result = await verifyBatch([product], async () => { throw new Error('page failed'); });
  assert.equal(result.unavailable, 1);
  assert.equal(result.snapshots[0].price_yen, null);
  assert.equal(result.snapshots[0].status, 'unavailable');
});

test('availability falls back to the explicit visible stock quantity and keeps its evidence', () => {
  assert.deepEqual(resolveAvailability('', '販売期間\n2026/09/17 00:00 ～ 2026/10/01 07:59\n在庫数\n99,977'), {
    availability: 'in_stock', quantity: 99977, evidence: 'visible_stock_quantity',
  });
  assert.deepEqual(resolveAvailability('', '在庫数 0'), {
    availability: 'out_of_stock', quantity: 0, evidence: 'visible_stock_quantity',
  });
  assert.deepEqual(resolveAvailability('http://schema.org/InStock', '在庫数 0'), {
    availability: null, quantity: 0, evidence: 'conflicting_signals',
  });
  assert.deepEqual(resolveAvailability('', '在庫状況を確認できません'), {
    availability: null, quantity: null, evidence: null,
  });
});

test('page snapshots carry visible stock quantity through to the product verification input', async () => {
  const result = await verifyBatch([product], async () => ({
    pageUrl: product.url, name: '在庫表示商品', priceYen: 3000,
    availabilityValue: '', visibleText: '販売期間\n在庫数\n99,977',
  }), '2026-09-25T12:00:00+09:00');
  assert.equal(result.snapshots[0].availability, 'in_stock');
  assert.equal(result.snapshots[0].availability_quantity, 99977);
  assert.equal(result.snapshots[0].availability_evidence, 'visible_stock_quantity');
});

test('only Rakuten item pages and up to fifteen distinct candidates are accepted', () => {
  assert.throws(() => validateCandidates([{ id: 'X', url: 'http://localhost/admin' }]));
  assert.throws(() => validateCandidates(Array.from({ length: 16 }, (_, n) => ({ id: `C${n}`, url: product.url }))));
  assert.throws(() => validateCandidates([product, product]));
});
