import { chromium } from 'playwright-core';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const outputRoot = path.join(here, '楽天商品要素調査_2026-10-22');
const selectors = [
  'span.sale_desc',
  'span.normal_reserve_item_name',
  'span.normal_reserve_catch_copy',
];
const products = [
  {
    directory: '01_meiji-ya_129',
    url: 'https://item.rakuten.co.jp/meiji-ya/129/?scid=af_pc_room_pc_id_reg&sc2id=af_101_0_0',
  },
  { directory: '02_dripcoffee_house-500-4', url: 'https://item.rakuten.co.jp/dripcoffee/house-500-4/' },
  { directory: '03_blueritual_kh001', url: 'https://item.rakuten.co.jp/blueritual/kh001/' },
  { directory: '04_sawaicoffee-tea_ac-sale-1960', url: 'https://item.rakuten.co.jp/sawaicoffee-tea/ac-sale-1960/' },
  { directory: '05_kkonlinestore_cafe07', url: 'https://item.rakuten.co.jp/kkonlinestore/cafe07/' },
];

const imageExtensions = new Map([
  ['image/jpeg', '.jpg'],
  ['image/png', '.png'],
  ['image/gif', '.gif'],
  ['image/webp', '.webp'],
  ['image/avif', '.avif'],
  ['image/svg+xml', '.svg'],
  ['image/bmp', '.bmp'],
]);
const allowedImageHosts = new Set(['image.rakuten.co.jp', 'www.rakuten.ne.jp']);

function safeImageUrl(rawUrl, baseUrl) {
  if (!rawUrl || rawUrl.startsWith('data:')) return null;
  try {
    const url = new URL(rawUrl, baseUrl);
    return url.protocol === 'https:' && allowedImageHosts.has(url.hostname.toLowerCase()) ? url.href : null;
  } catch {
    return null;
  }
}

async function downloadAllowedImage(initialUrl) {
  let currentUrl = initialUrl;
  for (let redirectCount = 0; redirectCount <= 5; redirectCount += 1) {
    const response = await fetch(currentUrl, {
      redirect: 'manual',
      signal: AbortSignal.timeout(30000),
    });
    if (![301, 302, 303, 307, 308].includes(response.status)) {
      const contentType = response.headers.get('content-type') ?? null;
      const declaredLength = Number(response.headers.get('content-length') ?? 0);
      if (!response.ok) {
        await response.body?.cancel();
        return { status: response.status, contentType, body: null, error: `HTTP ${response.status}` };
      }
      if (!String(contentType ?? '').toLowerCase().startsWith('image/')) {
        await response.body?.cancel();
        return { status: response.status, contentType, body: null, error: `Not an image response: ${contentType ?? 'content-type missing'}` };
      }
      if (declaredLength > 25 * 1024 * 1024) {
        await response.body?.cancel();
        return { status: response.status, contentType, body: null, error: `Skipped: response exceeds 25 MiB (${declaredLength} bytes)` };
      }

      const chunks = [];
      let byteCount = 0;
      const reader = response.body?.getReader();
      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          byteCount += value.byteLength;
          if (byteCount > 25 * 1024 * 1024) {
            await reader.cancel();
            return { status: response.status, contentType, body: null, error: `Skipped: response exceeds 25 MiB (${byteCount} bytes)` };
          }
          chunks.push(Buffer.from(value));
        }
      }
      return { status: response.status, contentType, body: Buffer.concat(chunks), error: null };
    }

    const location = response.headers.get('location');
    await response.body?.cancel();
    const nextUrl = safeImageUrl(location, currentUrl);
    if (!nextUrl) throw new Error('Redirect target is not an allowed HTTPS Rakuten image host');
    currentUrl = nextUrl;
  }
  throw new Error('Too many image redirects');
}

function extensionFrom(url, contentType) {
  const normalizedType = String(contentType ?? '').split(';')[0].trim().toLowerCase();
  if (imageExtensions.has(normalizedType)) return imageExtensions.get(normalizedType);
  try {
    const extension = path.extname(new URL(url).pathname).toLowerCase();
    return /^\.(jpe?g|png|gif|webp|avif|svg|bmp)$/.test(extension) ? extension : '.img';
  } catch {
    return '.img';
  }
}

function safeFilenamePart(value) {
  return value.replace(/[^a-zA-Z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'image';
}

async function writeJson(filePath, value) {
  await writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

await mkdir(outputRoot, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const results = [];

try {
  const context = await browser.newContext({
    locale: 'ja-JP',
    viewport: { width: 1440, height: 1000 },
  });

  for (const product of products) {
    const productDir = path.join(outputRoot, product.directory);
    const imagesDir = path.join(productDir, 'images');
    await mkdir(imagesDir, { recursive: true });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    let response;
    let navigationError = null;

    try {
      response = await page.goto(product.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForLoadState('load', { timeout: 25000 }).catch(() => {});
      await page.locator('body').waitFor({ state: 'attached', timeout: 15000 });
      for (const selector of selectors) {
        const nodes = page.locator(selector);
        const count = await nodes.count();
        for (let index = 0; index < count; index += 1) {
          await nodes.nth(index).evaluate((node) => node.scrollIntoView({ block: 'center' }));
          await page.waitForTimeout(150);
        }
      }
      await page.waitForTimeout(1200);
    } catch (error) {
      navigationError = `${error?.name ?? 'Error'}: ${String(error?.message ?? error).slice(0, 500)}`;
    }

    const pageData = await page.evaluate((targetSelectors) => {
      const textMarker = (text) => `text omitted; ${text.length} characters`;
      const redactMarkup = (root) => {
        const clone = root.cloneNode(true);
        const retainedAttributes = new Set([
          'class', 'id', 'role', 'itemprop', 'itemtype',
          'src', 'srcset', 'data-src', 'data-srcset', 'data-original', 'data-lazy-src', 'data-image',
          'width', 'height', 'border', 'cellpadding', 'cellspacing', 'colspan', 'rowspan', 'align', 'valign',
        ]);
        const redactAttributes = (element) => {
          for (const attribute of Array.from(element.attributes)) {
            const name = attribute.name.toLowerCase();
            if (name === 'style') {
              const backgroundUrls = Array.from(attribute.value.matchAll(/url\((?:['"])?(.*?)(?:['"])?\)/gi)).map((match) => match[1]);
              if (backgroundUrls.length) {
                element.setAttribute('style', backgroundUrls.map((url) => `background-image: url("${url}")`).join('; '));
              } else {
                element.removeAttribute(attribute.name);
              }
            } else if (/^on/i.test(name)) {
              element.removeAttribute(attribute.name);
            } else if (!retainedAttributes.has(name)) {
              element.setAttribute(attribute.name, `[omitted; ${attribute.value.length} characters]`);
            }
          }
        };
        const redactChildren = (parent) => {
          for (const child of Array.from(parent.childNodes)) {
            if (child.nodeType === Node.TEXT_NODE) {
              const value = child.nodeValue ?? '';
              if (value.length) child.replaceWith(document.createComment(textMarker(value)));
            } else if (child.nodeType === Node.COMMENT_NODE) {
              child.remove();
            } else if (child.nodeType === Node.ELEMENT_NODE) {
              redactAttributes(child);
              redactChildren(child.tagName === 'TEMPLATE' ? child.content : child);
            }
          }
        };
        redactAttributes(clone);
        redactChildren(clone);
        return clone.outerHTML;
      };
      const parseSrcset = (value) => value
        .split(',')
        .map((entry) => entry.trim().split(/\s+/)[0])
        .filter(Boolean);
      const candidatesFor = (root) => {
        const candidates = [];
        const add = (url, source, elementTag) => {
          if (url) candidates.push({ url, source, elementTag });
        };
        const elements = [
          ...(root.matches('img,source') ? [root] : []),
          ...root.querySelectorAll('img,source'),
        ];
        for (const element of elements) {
          const tag = element.tagName.toLowerCase();
          add(element.currentSrc, 'currentSrc', tag);
          for (const attribute of ['src', 'data-src', 'data-original', 'data-lazy-src', 'data-image']) {
            add(element.getAttribute(attribute), attribute, tag);
          }
          for (const attribute of ['srcset', 'data-srcset']) {
            for (const url of parseSrcset(element.getAttribute(attribute) ?? '')) {
              add(url, attribute, tag);
            }
          }
        }
        for (const element of [root, ...root.querySelectorAll('[style]')]) {
          const style = element.getAttribute?.('style') ?? '';
          for (const match of style.matchAll(/url\((?:['"])?(.*?)(?:['"])?\)/gi)) {
            add(match[1], 'inline-style-background', element.tagName.toLowerCase());
          }
        }
        return candidates;
      };

      return {
        finalUrl: location.href,
        titleLength: document.title.length,
        selectors: targetSelectors.map((selector) => {
          const nodes = Array.from(document.querySelectorAll(selector));
          return {
            selector,
            matches: nodes.map((node, index) => ({
              index: index + 1,
              tag: node.tagName.toLowerCase(),
              id: node.id || null,
              classes: typeof node.className === 'string' ? node.className.split(/\s+/).filter(Boolean) : [],
              textLength: node.textContent?.length ?? 0,
              elementChildCount: node.children.length,
              markup: redactMarkup(node),
              imageCandidates: candidatesFor(node),
            })),
          };
        }),
      };
    }, selectors);

    const imageByUrl = new Map();
    for (const selectorResult of pageData.selectors) {
      for (const match of selectorResult.matches) {
        const markupName = `${safeFilenamePart(selectorResult.selector.replaceAll('.', '_'))}_match_${String(match.index).padStart(2, '0')}.html`;
        const sanitizedMarkup = match.markup.replace(/[ \t]+$/gm, '');
        await writeFile(path.join(productDir, markupName), `${sanitizedMarkup}\n`, 'utf8');
        match.markupFile = markupName;
        delete match.markup;

        match.images = [];
        for (const candidate of match.imageCandidates) {
          const url = safeImageUrl(candidate.url, pageData.finalUrl);
          if (!url) continue;
          let imageRecord = imageByUrl.get(url);
          if (!imageRecord) {
            imageRecord = { url, sources: [], status: null, contentType: null, file: null, bytes: null, error: null };
            imageByUrl.set(url, imageRecord);
          }
          if (!imageRecord.sources.some((item) => item.selector === selectorResult.selector && item.matchIndex === match.index && item.source === candidate.source)) {
            imageRecord.sources.push({ selector: selectorResult.selector, matchIndex: match.index, source: candidate.source, elementTag: candidate.elementTag });
          }
        }
      }
    }

    let imageIndex = 0;
    for (const imageRecord of imageByUrl.values()) {
      imageIndex += 1;
      try {
        const downloaded = await downloadAllowedImage(imageRecord.url);
        imageRecord.status = downloaded.status;
        imageRecord.contentType = downloaded.contentType;
        imageRecord.error = downloaded.error;
        if (downloaded.body) {
          const filename = `image_${String(imageIndex).padStart(3, '0')}${extensionFrom(imageRecord.url, imageRecord.contentType)}`;
          await writeFile(path.join(imagesDir, filename), downloaded.body);
          imageRecord.file = `images/${filename}`;
          imageRecord.bytes = downloaded.body.length;
        }
      } catch (error) {
        imageRecord.error = `${error?.name ?? 'Error'}: ${String(error?.message ?? error).slice(0, 300)}`;
      }
    }

    const manifest = {
      capturedAt: new Date().toISOString(),
      browser: `Playwright Chromium ${browser.version()} (Microsoft Edge channel)`,
      requestedUrl: product.url,
      finalUrl: pageData.finalUrl,
      pageTitleLength: pageData.titleLength,
      httpStatus: response?.status() ?? null,
      navigationError,
      selectors: pageData.selectors.map(({ selector, matches }) => ({
        selector,
        matchCount: matches.length,
        matches: matches.map(({ index, tag, id, classes, textLength, elementChildCount, markupFile, images: _images, imageCandidates }) => ({
          index, tag, id, classes, textLength, elementChildCount, markupFile,
          imageCandidateCount: imageCandidates.length,
          imageUrls: Array.from(new Set(imageCandidates.map((candidate) => safeImageUrl(candidate.url, pageData.finalUrl)).filter(Boolean))),
        })),
      })),
      imageFiles: Array.from(imageByUrl.values()),
      note: 'HTMLファイルはタグ構造を保持し、テキストノード・HTMLコメント・alt/title等の属性値を省略しています。画像URLと構造識別に必要な属性値だけ残しています。商品説明文をそのまま再掲しないための構造確認用スナップショットです。画像は許可したRakuten画像ホストからのみダウンロードします。',
    };
    await writeJson(path.join(productDir, 'manifest.json'), manifest);
    await page.close();
    results.push({
      directory: product.directory,
      requestedUrl: product.url,
      finalUrl: pageData.finalUrl,
      httpStatus: manifest.httpStatus,
      navigationError,
      selectors: manifest.selectors.map(({ selector, matchCount }) => ({ selector, matchCount })),
      imageCount: manifest.imageFiles.filter((image) => image.file).length,
      uniqueImageUrlCount: manifest.imageFiles.length,
    });
    console.log(JSON.stringify(results.at(-1)));
  }

  await context.close();
} finally {
  await browser.close();
}

await writeJson(path.join(outputRoot, 'index.json'), {
  capturedAt: new Date().toISOString(),
  selectors,
  productCount: results.length,
  products: results,
  note: '各商品フォルダーのmanifest.jsonに詳細を記録しています。商品説明文を再掲しない構造確認用スナップショットとして、テキストノードと商品文の属性値は省略しています。',
});
