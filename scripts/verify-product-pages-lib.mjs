export function validateCandidates(candidates) {
  if (!Array.isArray(candidates) || candidates.length > 15) throw new Error('候補は最大15件です');
  const ids = new Set();
  return candidates.map((candidate) => {
    if (!candidate || typeof candidate.id !== 'string' || !/^[A-Za-z0-9_-]+$/.test(candidate.id)) {
      throw new Error('候補IDが不正です');
    }
    if (ids.has(candidate.id)) throw new Error('候補IDが重複しています');
    ids.add(candidate.id);
    let url;
    try { url = new URL(candidate.url); } catch { throw new Error('商品URLが不正です'); }
    if (url.protocol !== 'https:' || url.hostname !== 'item.rakuten.co.jp' || !/^\/[^/]+\/[^/]+\/?$/.test(url.pathname)) {
      throw new Error('楽天市場の商品URLだけを確認できます');
    }
    const searchQueries = candidate.search_queries ?? [];
    if (!Array.isArray(searchQueries) || searchQueries.length > 8 ||
        searchQueries.some((query) => typeof query !== 'string' || !query.trim())) {
      throw new Error('検索語は空でない文字列を8件以内で指定してください');
    }
    return { id: candidate.id, url: url.toString(), search_queries: [...new Set(searchQueries.map((query) => query.trim()))] };
  });
}

export function resolveAvailability(structuredValue, visibleText = '') {
  const normalized = String(structuredValue || '');
  const schemaAvailability = /InStock/i.test(normalized) ? 'in_stock'
    : /OutOfStock|SoldOut/i.test(normalized) ? 'out_of_stock' : null;
  const stockMatch = String(visibleText || '').match(/在庫数\s*[:：]?\s*([\d,，]+)/);
  const quantity = stockMatch ? Number(stockMatch[1].replace(/[,，]/g, '')) : null;
  const visibleAvailability = Number.isSafeInteger(quantity) ? quantity > 0 ? 'in_stock' : 'out_of_stock' : null;
  if (schemaAvailability && visibleAvailability && schemaAvailability !== visibleAvailability) {
    return { availability: null, quantity, evidence: 'conflicting_signals' };
  }
  if (schemaAvailability) {
    return { availability: schemaAvailability, quantity, evidence: 'schema_availability' };
  }
  if (visibleAvailability) {
    return { availability: visibleAvailability, quantity, evidence: 'visible_stock_quantity' };
  }
  return { availability: null, quantity: null, evidence: null };
}

export function toSnapshot(candidate, observedAt, pageData, error = null) {
  if (error) return {
    candidate_id: candidate.id, requested_url: candidate.url, observed_at: observedAt,
    status: 'unavailable', page_url: null, name: null, model_candidate: null,
    price_yen: null, availability: null, points_text: null, coupon_text: null,
    availability_quantity: null, availability_evidence: null,
    coupon_deadline: null, review_count: null, review_average: null,
    heat_evidence: null, error: String(error),
  };
  const availability = resolveAvailability(pageData.availabilityValue, pageData.visibleText);
  return {
    candidate_id: candidate.id, requested_url: candidate.url, observed_at: observedAt,
    status: 'observed', page_url: pageData.pageUrl || candidate.url,
    name: pageData.name || null, model_candidate: pageData.modelCandidate || null,
    price_yen: Number.isInteger(pageData.priceYen) && pageData.priceYen >= 0 ? pageData.priceYen : null,
    availability: pageData.availability || availability.availability || null,
    availability_quantity: pageData.availabilityQuantity ?? availability.quantity,
    availability_evidence: pageData.availabilityEvidence ?? availability.evidence,
    points_text: pageData.pointsText || null,
    coupon_text: pageData.couponText || null, coupon_deadline: pageData.couponDeadline || null,
    review_count: Number.isInteger(pageData.reviewCount) && pageData.reviewCount >= 0 ? pageData.reviewCount : null,
    review_average: Number.isFinite(pageData.reviewAverage) ? pageData.reviewAverage : null,
    heat_evidence: pageData.heatEvidence ?? null,
    error: null,
  };
}

export async function verifyBatch(candidates, loadPage, observedAt = new Date().toISOString()) {
  const valid = validateCandidates(candidates);
  const snapshots = [];
  for (const candidate of valid) {
    try {
      const pageData = await loadPage(candidate);
      snapshots.push(toSnapshot(candidate, observedAt, pageData));
    } catch (error) {
      snapshots.push(toSnapshot(candidate, observedAt, null, error.message));
    }
  }
  return { schema_version: 1, observed_at: observedAt, requested: valid.length,
    observed: snapshots.filter((entry) => entry.status === 'observed').length,
    unavailable: snapshots.filter((entry) => entry.status === 'unavailable').length,
    snapshots };
}
