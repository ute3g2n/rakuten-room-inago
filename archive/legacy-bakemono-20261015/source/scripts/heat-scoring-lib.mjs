const FACTOR_NAMES = [
  'exposure_duplicates',
  'shop_strength',
  'image_investment',
  'promotion_strength',
  'review_freshness',
  'keyword_exposure',
];

function unresolved(reason, evidence = {}) {
  return { points: null, status: 'unconfirmed', reason, evidence };
}

function scored(points, reason, evidence = {}) {
  return { points, status: 'scored', reason, evidence };
}

function validObserved(record) {
  return record && record.status === 'observed';
}

function searchFactors(results, expectedQueries) {
  if (!Array.isArray(results)) {
    return {
      exposure: unresolved('検索結果ページが未取得です'),
      keywords: unresolved('検索結果ページが未取得です'),
    };
  }
  const observed = results.filter((item) => validObserved(item) &&
    typeof item.keyword === 'string' && Array.isArray(item.matches));
  if (observed.length === 0) {
    return {
      exposure: unresolved('観測済みの検索結果がありません'),
      keywords: unresolved('観測済みの検索結果がありません'),
    };
  }

  const pages = observed.map((item) => {
    const placements = [...new Set(item.matches.map((match) => match?.placement)
      .filter((placement) => placement === 'pr' || placement === 'organic'))];
    return {
      keyword: item.keyword,
      page_url: item.page_url ?? null,
      match_count: item.matches.length,
      placements,
      matches: item.matches,
    };
  });
  const matchingPages = pages.filter((page) => page.match_count > 0);
  const matchingKeywords = [...new Set(matchingPages.map((page) => page.keyword))];
  const expected = Array.isArray(expectedQueries) ? [...new Set(expectedQueries.filter((item) =>
    typeof item === 'string' && item.trim()).map((item) => item.trim()))] : [];
  const observedKeywords = new Set(observed.map((page) => page.keyword.trim()));
  const completeKeywordCoverage = expected.length >= 5 && expected.length <= 8 &&
    expected.every((keyword) => observedKeywords.has(keyword));
  if (matchingPages.length === 0) {
    const evidence = {
      matching_keywords: 0,
      keywords: [],
      expected_queries: expected,
      observed_query_count: observedKeywords.size,
      pages,
    };
    return {
      exposure: completeKeywordCoverage ? scored(0,
        '調査した検索キーワードの1ページ目に掲載なし', evidence)
        : unresolved('掲載なしを確定するための検索語調査が不足しています', evidence),
      keywords: completeKeywordCoverage ? scored(0,
        '調査した検索キーワードで掲載なし', evidence)
        : unresolved('掲載なしを確定するための検索語調査が不足しています', evidence),
    };
  }

  const maxPage = matchingPages.reduce((best, page) => page.match_count > best.match_count ? page : best);
  const hasPrAndOrganic = matchingPages.some((page) =>
    page.placements.includes('pr') && page.placements.includes('organic'));
  const exposurePoints = maxPage.match_count >= 3 || hasPrAndOrganic ? 2
    : maxPage.match_count === 2 ? 1 : 0;
  const exposureReason = hasPrAndOrganic ? '同一キーワードの1ページ目にPR枠と通常枠の両方で掲載'
    : maxPage.match_count >= 3 ? '同一キーワードの1ページ目に3回以上掲載'
      : maxPage.match_count === 2 ? '同一キーワードの1ページ目に2回掲載'
        : '同一キーワードの1ページ目に1回掲載';
  const keywordScore = matchingKeywords.length >= 3 ? scored(2,
    `${matchingKeywords.length}種類以上の検索キーワードで掲載`, {
      matching_keywords: matchingKeywords.length,
      keywords: matchingKeywords,
      expected_queries: expected,
      observed_query_count: observedKeywords.size,
      pages,
    }) : completeKeywordCoverage ? scored(matchingKeywords.length === 2 ? 1 : 0,
    `${matchingKeywords.length}種類の検索キーワードで掲載`, {
      matching_keywords: matchingKeywords.length,
      keywords: matchingKeywords,
      expected_queries: expected,
      observed_query_count: observedKeywords.size,
      pages,
    }) : unresolved('5〜8件の検索語をすべて調査できていません', {
      matching_keywords: matchingKeywords.length,
      keywords: matchingKeywords,
      expected_queries: expected,
      observed_query_count: observedKeywords.size,
      pages,
    });

  return {
    exposure: scored(exposurePoints, exposureReason, {
      keyword: maxPage.keyword,
      page_url: maxPage.page_url,
      max_same_page_count: maxPage.match_count,
      pr_and_organic: hasPrAndOrganic,
      pages,
    }),
    keywords: keywordScore,
  };
}

function shopFactor(shop) {
  if (!validObserved(shop) || !Array.isArray(shop.award_badges) ||
      (shop.review_count !== null && (!Number.isInteger(shop.review_count) || shop.review_count < 0))) {
    return unresolved('ショップレビュー数または受賞表示を確認できません', shop ?? {});
  }
  const award = shop.award_badges.length > 0;
  if (!award && shop.review_count === null) {
    return unresolved('ショップレビュー数または受賞表示を確認できません', shop);
  }
  const points = award || shop.review_count >= 1000 ? 2 : shop.review_count >= 100 ? 1 : 0;
  return scored(points, award ? '受賞バッジを確認' : `ショップレビュー${shop.review_count}件`, {
    source_url: shop.source_url ?? null,
    review_count: shop.review_count,
    award_badges: shop.award_badges,
  });
}

function imageFactor(images) {
  if (!validObserved(images) || !Number.isInteger(images.count) || images.count < 0) {
    return unresolved('商品画像または画像枚数を確認できません', images ?? {});
  }
  const evidence = {
    source_url: images.source_url ?? null,
    count: images.count,
    first_image_has_text_overlay: images.first_image_has_text_overlay ?? null,
    has_comparison_or_use_scene: images.has_comparison_or_use_scene ?? null,
    material_only: images.material_only ?? null,
  };
  if (images.count >= 7) return scored(2, '商品画像が7枚以上', evidence);
  if (images.first_image_has_text_overlay === true) {
    if (images.has_comparison_or_use_scene === true) {
      return scored(2, '1枚目に訴求文があり、比較表または使用シーン画像も確認', evidence);
    }
    if (images.has_comparison_or_use_scene === false) {
      return scored(1, '1枚目に訴求テキストを確認', evidence);
    }
    return unresolved('比較表・使用シーン画像の確認が不足しています', evidence);
  }
  if (images.count <= 3 && images.material_only === true) {
    return scored(0, '素材写真のみで3枚以下', evidence);
  }
  return unresolved('画像枚数だけでは素材写真・訴求内容を判定できません', evidence);
}

function withinThreeDays(deadline, observedAt) {
  if (typeof deadline !== 'string' || typeof observedAt !== 'string') return false;
  const end = Date.parse(deadline);
  const start = Date.parse(observedAt);
  return Number.isFinite(end) && Number.isFinite(start) && end >= start && end - start <= 3 * 24 * 60 * 60 * 1000;
}

function promotionFactor(promotions, observedAt) {
  if (!validObserved(promotions) || typeof promotions.coupon_present !== 'boolean' ||
      typeof promotions.points_present !== 'boolean') {
    return unresolved('クーポン・ポイント施策の表示状態を確認できません', promotions ?? {});
  }
  const evidence = {
    source_url: promotions.source_url ?? null,
    coupon_present: promotions.coupon_present,
    coupon_deadline: promotions.coupon_deadline ?? null,
    coupon_amount_yen: promotions.coupon_amount_yen ?? null,
    small_coupon: promotions.small_coupon ?? null,
    points_present: promotions.points_present,
    points_deadline: promotions.points_deadline ?? null,
  };
  if (!promotions.coupon_present && !promotions.points_present) {
    return scored(0, 'クーポン・ポイントアップの表示なし', evidence);
  }
  if (promotions.coupon_present && promotions.points_present) {
    return scored(2, 'クーポンとポイントアップを併用', evidence);
  }
  if (withinThreeDays(promotions.coupon_deadline, observedAt) ||
      withinThreeDays(promotions.points_deadline, observedAt)) {
    return scored(2, '販促の終了期限が3日以内', evidence);
  }
  if (promotions.points_present && !promotions.coupon_present) {
    return scored(1, 'ポイント倍率のみ', evidence);
  }
  if (promotions.coupon_present && promotions.small_coupon === true) {
    return scored(1, '小額クーポンのみ', evidence);
  }
  return unresolved('クーポン単独の金額区分が基準未定義です', evidence);
}

function dateOnly(value) {
  if (typeof value !== 'string') return null;
  const match = value.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) return null;
  return parsed;
}

function japanDateOnly(value) {
  if (typeof value !== 'string') return null;
  const instant = new Date(value);
  if (!Number.isFinite(instant.getTime())) return null;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(instant).map(({ type, value: part }) => [type, part]));
  return new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)));
}

function reviewFactor(reviews, observedAt) {
  if (!validObserved(reviews) || reviews.order !== 'newest' || reviews.complete !== true ||
      !Array.isArray(reviews.reviews) || reviews.reviews.length > 10 || !japanDateOnly(observedAt)) {
    return unresolved('新しい順のレビュー最大10件を確認できません', reviews ?? {});
  }
  if (reviews.reviews.some((review) => !dateOnly(review.date) || !Number.isInteger(review.stars) ||
      review.stars < 1 || review.stars > 5)) {
    return unresolved('レビューの日付または星評価が不足しています', {
      source_url: reviews.source_url ?? null,
      order: reviews.order,
      reviews: reviews.reviews,
    });
  }
  if (reviews.reviews.length === 0 && reviews.review_count !== 0) {
    return unresolved('レビュー一覧が空ですが総レビュー数を確認できません', reviews);
  }

  const observedDate = japanDateOnly(observedAt);
  const daysAgo = (value) => Math.floor((observedDate - dateOnly(value)) / 86400000);
  const in7 = reviews.reviews.filter((review) => daysAgo(review.date) >= 0 && daysAgo(review.date) < 7);
  const in30 = reviews.reviews.filter((review) => daysAgo(review.date) >= 0 && daysAgo(review.date) < 30);
  const highStars = in7.filter((review) => review.stars >= 4).length;
  const evidence = {
    source_url: reviews.source_url ?? null,
    order: reviews.order,
    latest_reviews: reviews.reviews,
    last_7_days: in7.length,
    last_30_days: in30.length,
    last_7_days_star_4_5: highStars,
  };
  const matches = [];
  if (in30.length < 3) matches.push({ points: 0, reason: '直近30日のレビューが3件未満' });
  if (in7.length >= 1 && in7.length <= 2) matches.push({ points: 1, reason: '直近7日にレビュー1〜2件' });
  if (in7.length >= 3 && highStars > in7.length / 2) {
    matches.push({ points: 2, reason: '直近7日に3件以上あり、★4〜5が過半数' });
  }
  if (matches.length === 0) {
    return unresolved('確認したレビュー件数・星分布が採点表の条件に一致しません', evidence);
  }
  const best = matches.reduce((current, entry) => entry.points > current.points ? entry : current);
  return scored(best.points, best.reason, evidence);
}

function grade(score) {
  if (score >= 9) return 'A';
  if (score >= 6) return 'B';
  return 'C';
}

export function scoreHeatEvidence(evidence = {}) {
  const search = searchFactors(evidence.search_results, evidence.expected_search_queries);
  const factors = {
    exposure_duplicates: search.exposure,
    shop_strength: shopFactor(evidence.shop),
    image_investment: imageFactor(evidence.images),
    promotion_strength: promotionFactor(evidence.promotions, evidence.observed_at),
    review_freshness: reviewFactor(evidence.reviews, evidence.observed_at),
    keyword_exposure: search.keywords,
  };
  const confirmed = Object.values(factors).filter((factor) => factor.points !== null);
  const score = confirmed.reduce((total, factor) => total + factor.points, 0);
  const complete = confirmed.length === FACTOR_NAMES.length;
  const provisionalGrade = grade(score);
  return {
    scoring_method: 'legacy-heat-6-factor-v1',
    candidate_id: evidence.candidate_id ?? null,
    product_url: evidence.product_url ?? null,
    observed_at: evidence.observed_at ?? null,
    score,
    confirmed_max: confirmed.length * 2,
    full_max: 12,
    confirmed_factors: confirmed.length,
    unconfirmed_factors: FACTOR_NAMES.filter((name) => factors[name].points === null),
    grade: complete ? provisionalGrade : null,
    provisional_grade: provisionalGrade,
    status: complete ? 'complete' : score >= 6 ? 'provisional' : 'hold',
    factors,
  };
}
