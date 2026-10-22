import { chromium } from 'playwright-core';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(here);
const targetUrl = 'https://item.rakuten.co.jp/meiji-ya/129/?scid=af_pc_room_pc_id_reg&sc2id=af_101_0_0';
await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true, channel: 'msedge' });
try {
  const context = await browser.newContext({
    locale: 'ja-JP',
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  let response;
  let navigationError = null;
  try {
    response = await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForLoadState('load', { timeout: 25000 }).catch(() => {});
    await page.locator('body').waitFor({ state: 'attached', timeout: 15000 });
    await page.waitForTimeout(2500);
  } catch (error) {
    navigationError = `${error?.name ?? 'Error'}: ${String(error?.message ?? error).slice(0, 500)}`;
  }

  const snapshot = await page.evaluate(() => {
    const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
    const getClasses = (node) => typeof node.className === 'string'
      ? node.className.split(/\s+/).filter(Boolean).slice(0, 12)
      : [];
    const nodeSummary = (node, depth = 0) => {
      const children = Array.from(node.children ?? []);
      const item = {
        tag: node.tagName?.toLowerCase() ?? 'unknown',
        id: node.id || null,
        classes: getClasses(node),
        role: node.getAttribute?.('role') || null,
        itemprop: node.getAttribute?.('itemprop') || null,
        itemtype: node.getAttribute?.('itemtype') || null,
        childCount: children.length,
        textLength: clean(node.innerText).length,
      };
      if (depth < 5 && children.length) {
        item.children = children.slice(0, 20).map((child) => nodeSummary(child, depth + 1));
        if (children.length > 20) item.omittedChildren = children.length - 20;
      }
      return item;
    };
    const all = Array.from(document.querySelectorAll('body *'));
    const countByTag = {};
    for (const node of all) {
      const tag = node.tagName.toLowerCase();
      countByTag[tag] = (countByTag[tag] ?? 0) + 1;
    }
    const describe = (node) => ({
      tag: node.tagName.toLowerCase(),
      id: node.id || null,
      classes: getClasses(node),
      role: node.getAttribute('role') || null,
      itemprop: node.getAttribute('itemprop') || null,
      itemtype: node.getAttribute('itemtype') || null,
      textLength: clean(node.innerText).length,
      childCount: node.children.length,
    });
    const jsonLd = Array.from(document.querySelectorAll('script[type="application/ld+json"]')).map((node) => {
      try {
        const value = JSON.parse(node.textContent || 'null');
        const objects = Array.isArray(value) ? value : [value];
        return {
          types: objects.flatMap((object) => {
            if (!object || typeof object !== 'object') return [];
            const type = object['@type'];
            return Array.isArray(type) ? type : (type ? [type] : []);
          }),
          topLevelKeys: objects.flatMap((object) => object && typeof object === 'object' ? Object.keys(object) : []).slice(0, 60),
        };
      } catch {
        return { parse: 'invalid_json' };
      }
    });
    const productSelectors = [
      '[itemtype*="Product"]', '[itemprop="name"]', '[itemprop="description"]',
      '[itemprop="offers"]', '[itemprop="price"]', '[itemprop="sku"]',
      '[class*="item_name"]', '[class*="price"]', '[class*="description"]',
    ];
    const productCandidates = productSelectors.map((selector) => ({
      selector,
      count: document.querySelectorAll(selector).length,
      nodes: Array.from(document.querySelectorAll(selector)).slice(0, 20).map(describe),
    }));
    const productMicrodata = Array.from(document.querySelectorAll('[itemtype*="Product"]')).map((root) => ({
      root: describe(root),
      fields: Array.from(root.querySelectorAll('[itemprop]')).map((node) => ({
        ...describe(node),
        contentAttributePresent: node.hasAttribute('content'),
        contentLength: node.hasAttribute('content') ? (node.getAttribute('content') ?? '').length : null,
        srcAttributePresent: node.hasAttribute('src'),
        hrefAttributePresent: node.hasAttribute('href'),
      })),
    }));
    const h1 = Array.from(document.querySelectorAll('h1')).slice(0, 1).map((node) => clean(node.innerText).slice(0, 160));
    const metaDescription = document.querySelector('meta[name="description"]')?.getAttribute('content') ?? null;
    return {
      capturedAt: new Date().toISOString(),
      finalUrl: location.href,
      title: document.title,
      readyState: document.readyState,
      language: document.documentElement.lang || null,
      viewport: { width: innerWidth, height: innerHeight, devicePixelRatio },
      document: {
        htmlChildCount: document.documentElement.children.length,
        headChildCount: document.head?.children.length ?? 0,
        bodyChildCount: document.body?.children.length ?? 0,
        bodyTextLength: clean(document.body?.innerText).length,
        headingCounts: Object.fromEntries([1, 2, 3, 4, 5, 6].map((n) => [`h${n}`, document.querySelectorAll(`h${n}`).length])),
        landmarkCounts: Object.fromEntries(['header', 'nav', 'main', 'article', 'aside', 'footer', 'form'].map((tag) => [tag, document.querySelectorAll(tag).length])),
        countByTag,
      },
      metadata: {
        canonical: document.querySelector('link[rel="canonical"]')?.href ?? null,
        metaDescriptionLength: metaDescription?.length ?? null,
        openGraphTypes: Array.from(document.querySelectorAll('meta[property^="og:"]')).map((node) => node.getAttribute('property')).slice(0, 40),
      },
      h1,
      landmarks: Array.from(document.querySelectorAll('header, nav, main, article, aside, footer, [role="main"]')).slice(0, 80).map(describe),
      jsonLd,
      productCandidates,
      productMicrodata,
      bodyOutline: Array.from(document.body?.children ?? []).slice(0, 40).map((node) => nodeSummary(node)),
      note: '構造確認用。ページtitleと先頭h1（存在する場合）のテキストを記録し、それ以外はDOM要素属性とテキスト長のみを記録。本文全文・画像・スクリプト本文は保存しない。',
    };
  });

  const result = {
    browser: `Playwright Chromium ${browser.version()}`,
    httpStatus: response?.status() ?? null,
    httpStatusText: response?.statusText() ?? null,
    navigationError,
    requestedUrl: targetUrl,
    ...snapshot,
  };
  const name = 'rakuten_meiji-ya_129_playwright_dom_snapshot.json';
  await writeFile(path.join(outDir, name), `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  console.log(JSON.stringify({
    output: path.join(outDir, name),
    browser: result.browser,
    httpStatus: result.httpStatus,
    navigationError: result.navigationError,
    finalUrl: result.finalUrl,
    title: result.title,
    bodyTextLength: result.document?.bodyTextLength,
    productSelectors: result.productCandidates?.map(({ selector, count }) => ({ selector, count })),
  }, null, 2));
  await context.close();
} finally {
  await browser.close();
}
