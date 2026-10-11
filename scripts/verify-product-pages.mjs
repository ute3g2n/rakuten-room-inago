import { chromium } from 'playwright-core';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { scoreHeatEvidence } from './heat-scoring-lib.mjs';
import {
  buildRakutenSearchUrl,
  buildShopHomeUrl,
  collectProductGalleryImageUrls,
  extractPointPromotionText,
  extractProductReviewCountText,
  makeSearchEvidence,
  parseProductReviewCount,
  parsePromotionDeadline,
  parseRecentReviewRows,
  parseShopReviewCount,
} from './heat-evidence-collector-lib.mjs';
import { validateCandidates, verifyBatch } from './verify-product-pages-lib.mjs';

function parseArgs(args) {
  if (args.length === 1 && args[0] === '--help') return { help: true };
  if (args.length !== 4 || args[0] !== '--input' || args[2] !== '--output' || !args[1] || !args[3]) {
    throw new Error('Usage: verify-product-pages --input candidates.json --output snapshot.json');
  }
  return { input: path.resolve(args[1]), output: path.resolve(args[3]) };
}

function awardBadges(text) {
  const terms = [
    '楽天ショップ・オブ・ザ・イヤー', '楽天ショップオブザイヤー',
    'ショップ・オブ・ザ・イヤー', 'ショップオブザイヤー',
  ];
  return terms.filter((term) => text.includes(term));
}

async function readProductPage(page, candidate, observedAt) {
  await page.goto(candidate.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  const finalUrl = new URL(page.url());
  if (finalUrl.hostname !== 'item.rakuten.co.jp') throw new Error('商品ページ以外へ遷移しました');
  const data = await page.evaluate(() => {
    const meta = (name) => document.querySelector(`meta[property="${name}"],meta[name="${name}"]`)?.content?.trim() || null;
    const text = (selector, root = document) => root.querySelector(selector)?.textContent?.trim() || null;
    const number = (value) => {
      const normalized = String(value || '').replace(/[,，円\s]/g, '');
      return /^\d+$/.test(normalized) ? Number(normalized) : null;
    };
    const productRoot = document.querySelector('#item-page-app') || document;
    const title = text('h1', productRoot) || meta('og:title') || document.title;
    const price = meta('product:price:amount') || document.querySelector('[itemprop="price"]')?.getAttribute('content') || text('[itemprop="price"]');
    const availabilityNode = document.querySelector('[itemprop="availability"]');
    const availabilityValue = availabilityNode?.getAttribute('content')
      || availabilityNode?.getAttribute('href') || availabilityNode?.textContent || '';
    const bodyText = document.body?.innerText || '';
    const promotionAreaText = document.querySelector('#rakutenLimitedId_cart')?.innerText || null;
    const shopCode = location.pathname.split('/').filter(Boolean)[0] || '';
    const imageSources = [...document.querySelectorAll('div[class*="image-wrapper"] img')]
      .map((image) => image.currentSrc || image.src || image.dataset.src || '')
    const reviewAnchor = [...document.querySelectorAll('a[href*="review.rakuten.co.jp/item/1/"]')]
      .find((anchor) => !anchor.href.includes('/wd/')) || null;
    const reviewUrl = reviewAnchor?.href || null;
    const shopReviewUrl = [...document.querySelectorAll('a[href*="review.rakuten.co.jp/rd/0_"]')]
      .map((anchor) => anchor.href)[0] || null;
    const couponTexts = [...productRoot.querySelectorAll('[class*="coupon"], [data-testid*="coupon"]')]
      .map((node) => node.innerText?.trim()).filter(Boolean);
    const titleHasCoupon = /クーポン/.test(title) && /(?:OFF|円|%)/.test(title);
    const couponText = [...new Set([...couponTexts, ...(titleHasCoupon ? [title] : [])])].join('\n') || null;
    const pointTexts = [...productRoot.querySelectorAll('[class*="point"], [data-testid*="point"], img[alt*="ポイントアップ"]')]
      .map((node) => (node.innerText || node.getAttribute('alt') || '').trim()).filter(Boolean);
    const pointsText = [...new Set(pointTexts)].join('\n') || null;
    const pointsPresent = /ポイントアップ|ポイント\s*\d+\s*倍|P\s*\d+\s*倍/i.test(`${pointsText || ''}\n${bodyText.slice(0, 1800)}`);
    const reviewCountText = meta('reviewCount') || document.querySelector('[itemprop="reviewCount"]')?.getAttribute('content');
    const reviewCount = number(reviewCountText) ?? null;
    const reviewAverageText = meta('ratingValue') || document.querySelector('[itemprop="ratingValue"]')?.getAttribute('content');
    const reviewAverage = reviewAverageText && /^\d+(\.\d+)?$/.test(reviewAverageText) ? Number(reviewAverageText) : null;
    return {
      pageUrl: location.href,
      name: title,
      modelCandidate: text('[itemprop="mpn"]', productRoot) || meta('product:mpn'),
      priceYen: number(price),
      availabilityValue,
      visibleText: bodyText,
      promotionAreaText,
      pointsText: pointsText?.slice(0, 500) || null,
      couponText: couponText?.slice(0, 500) || null,
      reviewCount,
      reviewAverage,
      reviewUrl,
      shopReviewUrl,
      awardBadges: [],
      imageSources,
      shopCode,
      imageUrls: [],
      imageCount: 0,
      productReviewCountText: reviewAnchor?.innerText || null,
      couponPresent: Boolean(couponText),
      pointsPresent,
    };
  });

  data.shopHomeUrl = buildShopHomeUrl(data.pageUrl);
  data.imageUrls = collectProductGalleryImageUrls(data.imageSources, data.shopCode);
  data.imageCount = data.imageUrls.length;
  data.reviewCount ??= parseProductReviewCount(data.productReviewCountText,
    extractProductReviewCountText(data.visibleText));
  data.pointsText ||= extractPointPromotionText(data.promotionAreaText)
    || extractPointPromotionText(data.visibleText.slice(0, 1800));
  data.pointsPresent ||= Boolean(data.pointsText && /ポイントアップ|ポイント\s*\d+\s*倍|P\s*\d+\s*倍/i.test(data.pointsText));
  delete data.imageSources;
  delete data.shopCode;
  delete data.productReviewCountText;
  delete data.promotionAreaText;
  const couponDeadline = parsePromotionDeadline(data.couponText, observedAt);
  const pointsDeadline = parsePromotionDeadline(data.pointsText, observedAt);
  data.awardBadges = [...new Set(awardBadges(data.visibleText))];
  const heatEvidence = {
    candidate_id: candidate.id,
    product_url: candidate.url,
    observed_at: observedAt,
    expected_search_queries: candidate.search_queries,
    search_results: [],
    shop: {
      status: 'unavailable',
      source_url: data.shopHomeUrl || data.shopReviewUrl || data.pageUrl,
      review_count: null,
      award_badges: data.awardBadges,
    },
    images: {
      status: data.imageCount > 0 ? 'observed' : 'unavailable',
      source_url: data.pageUrl,
      count: data.imageCount,
      image_urls: data.imageUrls,
      first_image_url: data.imageUrls[0] ?? null,
      first_image_has_text_overlay: null,
      has_comparison_or_use_scene: null,
      material_only: null,
    },
    promotions: {
      status: 'observed',
      source_url: data.pageUrl,
      coupon_present: data.couponPresent,
      coupon_text: data.couponText,
      coupon_deadline: couponDeadline,
      coupon_amount_yen: null,
      small_coupon: null,
      points_present: data.pointsPresent,
      points_text: data.pointsText,
      points_deadline: pointsDeadline,
    },
    reviews: {
      status: data.reviewCount === 0 ? 'observed' : 'unavailable',
      source_url: data.reviewUrl || data.pageUrl,
      order: data.reviewCount === 0 ? 'newest' : 'unknown',
      complete: data.reviewCount === 0,
      review_count: data.reviewCount,
      reviews: [],
    },
    review_url: data.reviewUrl,
    shop_home_url: data.shopHomeUrl,
    shop_review_url: data.shopReviewUrl,
  };
  return {
    ...data,
    couponDeadline,
    heatEvidence,
  };
}

async function collectShopAndProductReviews(page, snapshots, observedAt) {
  for (const snapshot of snapshots) {
    const evidence = snapshot.heat_evidence;
    if (!evidence) continue;

    const shopTargets = [
      { url: evidence.shop_home_url, kind: 'shop_home' },
      { url: evidence.shop_review_url, kind: 'shop_reviews' },
    ].filter((target) => target.url);
    let shopFetchError = null;
    for (const target of shopTargets) {
      try {
        const shopUrl = new URL(target.url);
        if (target.kind === 'shop_home' &&
            (shopUrl.hostname !== 'item.rakuten.co.jp' || !/^\/[^/]+\/$/.test(shopUrl.pathname))) {
          throw new Error('ショップトップURLが対象外です');
        }
        if (target.kind === 'shop_reviews' && shopUrl.hostname !== 'review.rakuten.co.jp') {
          throw new Error('ショップレビューURLが対象外です');
        }
        await page.goto(shopUrl.toString(), { waitUntil: 'domcontentloaded', timeout: 25000 });
        await page.waitForTimeout(800);
        const body = await page.locator('body').innerText({ timeout: 5000 }).catch(() => '');
        const reviewCount = parseShopReviewCount(body);
        const badges = [...new Set([...evidence.shop.award_badges, ...awardBadges(body)])];
        evidence.shop = {
          status: reviewCount !== null || badges.length > 0 ? 'observed' : 'unavailable',
          source_url: page.url(),
          review_count: reviewCount,
          award_badges: badges,
        };
        if (reviewCount !== null) break;
      } catch (error) {
        shopFetchError = error.message;
      }
    }
    if (evidence.shop.status !== 'observed' && shopFetchError) {
      evidence.shop.fetch_error = shopFetchError;
    }

    if (evidence.reviews.review_count === 0) continue;
    if (!evidence.review_url) continue;
    try {
      const reviewUrl = new URL(evidence.review_url);
      if (reviewUrl.hostname !== 'review.rakuten.co.jp' || !/\/item\/1\//.test(reviewUrl.pathname)) {
        throw new Error('商品レビューURLが対象外です');
      }
      await page.goto(reviewUrl.toString(), { waitUntil: 'domcontentloaded', timeout: 25000 });
      await page.waitForTimeout(1200);
      const sort = await page.evaluate(() => [...document.querySelectorAll('select')].flatMap((select, selectIndex) =>
        [...select.options].map((option) => ({ selectIndex, value: option.value,
          text: option.textContent?.trim() || '', selected: option.selected })))
        .find((option) => option.text.includes('新着レビュー順')) || null);
      let newestSelected = false;
      if (sort) {
        if (!sort.selected) {
          await page.locator('select').nth(sort.selectIndex).selectOption(sort.value, { timeout: 3000 });
          await page.waitForTimeout(600);
        }
        newestSelected = true;
      } else {
        const newLink = page.locator('a,button,[role="button"]').filter({ hasText: '新着レビュー順' }).first();
        if (await newLink.count().catch(() => 0)) {
          await newLink.click({ timeout: 3000 }).catch(() => {});
          await page.waitForTimeout(600);
          newestSelected = true;
        }
      }
      const body = await page.locator('body').innerText({ timeout: 5000 }).catch(() => '');
      const reviews = parseRecentReviewRows(body);
      const total = evidence.reviews.review_count;
      const complete = newestSelected && Number.isInteger(total) && reviews.length >= Math.min(10, total);
      evidence.reviews = {
        status: complete ? 'observed' : 'unavailable',
        source_url: page.url(),
        observed_at: observedAt,
        order: newestSelected ? 'newest' : 'unknown',
        complete,
        review_count: total,
        reviews,
        error: complete ? null : '新着順のレビュー10件を十分に取得できませんでした',
      };
    } catch (error) {
      evidence.reviews = { ...evidence.reviews, status: 'unavailable', order: 'unknown',
        complete: false, error: error.message };
    }
  }
}

async function collectSearchEvidence(page, candidates, snapshots, observedAt) {
  const ownersByKeyword = new Map();
  for (const candidate of candidates) {
    for (const keyword of candidate.search_queries) {
      const owners = ownersByKeyword.get(keyword) ?? [];
      owners.push(candidate);
      ownersByKeyword.set(keyword, owners);
    }
  }
  const snapshotById = new Map(snapshots.map((snapshot) => [snapshot.candidate_id, snapshot]));
  const entries = [...ownersByKeyword.entries()];
  for (let index = 0; index < entries.length; index += 1) {
    const [keyword, owners] = entries[index];
    const pageUrl = buildRakutenSearchUrl(keyword);
    try {
      await page.goto(pageUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForTimeout(500);
      const searchPage = await page.evaluate(() => {
        const cards = [...document.querySelectorAll('.searchresultitem[data-track-card="search"], .searchresultitem')]
          .map((card, index) => {
            const link = [...card.querySelectorAll('a[href*="item.rakuten.co.jp"]')][0];
            if (!link) return null;
            const explicitPr = card.getAttribute('data-track-type')?.toLowerCase() === 'pr' ||
              card.getAttribute('data-track-card')?.toLowerCase() === 'pr' ||
              [...card.querySelectorAll('[aria-label], [title], span, [class*="pr-label"], [class*="pr-badge"]')]
                .some((node) => /^(?:pr|広告)$/i.test((node.getAttribute('aria-label') || node.getAttribute('title') || node.innerText || '').trim()));
            const rawPosition = Number(card.getAttribute('data-position-absolute'));
            return {
              url: link.href,
              position: Number.isInteger(rawPosition) && rawPosition > 0 ? rawPosition : index + 1,
              placement: explicitPr ? 'pr' : 'organic',
              placement_evidence: explicitPr ? 'visible_pr_label_or_pr_marker' : 'search_result_card_without_pr_marker',
              title: link.innerText?.trim() || link.querySelector('img')?.alt || null,
            };
          }).filter(Boolean);
        return { title: document.title, body: document.body?.innerText || '', cards };
      });
      if (!/検索結果/.test(searchPage.title) && !/検索結果/.test(searchPage.body.slice(0, 1200))) {
        throw new Error('検索結果ページの表示を確認できません');
      }
      for (const candidate of owners) {
        const snapshot = snapshotById.get(candidate.id);
        if (!snapshot?.heat_evidence) continue;
        snapshot.heat_evidence.search_results.push(makeSearchEvidence({
          candidate, keyword, pageUrl: page.url(), observedAt, cards: searchPage.cards,
        }));
      }
    } catch (error) {
      for (const candidate of owners) {
        const snapshot = snapshotById.get(candidate.id);
        if (!snapshot?.heat_evidence) continue;
        snapshot.heat_evidence.search_results.push(makeSearchEvidence({
          candidate, keyword, pageUrl, observedAt, error: error.message,
        }));
      }
    }
    if (index < entries.length - 1) await page.waitForTimeout(1200);
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write('Usage: verify-product-pages --input candidates.json --output snapshot.json\n');
    return;
  }
  const input = JSON.parse(await readFile(options.input, 'utf8'));
  const candidates = validateCandidates(Array.isArray(input) ? input : input.candidates);
  const observedAt = new Date().toISOString();
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage();
    const result = await verifyBatch(candidates, (candidate) => readProductPage(page, candidate, observedAt), observedAt);
    await collectShopAndProductReviews(page, result.snapshots, observedAt);
    await collectSearchEvidence(page, candidates, result.snapshots, observedAt);
    const heatScores = result.snapshots.map((snapshot) => scoreHeatEvidence(snapshot.heat_evidence || {
      candidate_id: snapshot.candidate_id,
      product_url: snapshot.requested_url,
      observed_at: observedAt,
    }));
    const output = { ...result, heat_scores: heatScores };
    await mkdir(path.dirname(options.output), { recursive: true });
    await writeFile(options.output, `${JSON.stringify(output, null, 2)}\n`, 'utf8');
    if (result.unavailable) process.exitCode = 2;
  } finally {
    await browser.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`verify-product-pages: ${error.message}\n`);
    process.exitCode = 1;
  });
}
