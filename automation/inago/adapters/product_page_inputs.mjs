import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { lookup } from 'node:dns/promises';
import { isIP } from 'node:net';

const SELECTORS = [
  ['sale_desc', 'span.sale_desc'],
  ['normal_reserve_item_name', 'span.normal_reserve_item_name'],
  ['normal_reserve_catch_copy', 'span.normal_reserve_catch_copy'],
];

const segment = value => Buffer.from(String(value), 'utf8').toString('base64url');
const safeRunSegment = value => {
  const reserved = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i;
  return /^[A-Za-z0-9._-]{1,128}$/.test(value) && !reserved.test(value) && !value.endsWith('.')
    ? value
    : `encoded-${segment(value)}`;
};
async function atomicJson(file, value) {
  const temporary = `${file}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  await rename(temporary, file);
}

async function existingCapture(folder, runId, shopId, itemId) {
  try {
    const value = JSON.parse(await readFile(path.join(folder, 'page.json'), 'utf8'));
    if (value.run_id === runId && value.shop_id === shopId && value.item_id === itemId && value.html_state === 'captured') return value;
  } catch {}
  return null;
}

function publicIPv4(address) {
  const octets = address.split('.').map(Number);
  if (octets.length !== 4 || octets.some(value => !Number.isInteger(value) || value < 0 || value > 255))
    return false;
  const [a, b, c, d] = octets;
  if (a === 0 || a === 10 || a === 127 || a >= 224) return false;
  if (a === 100 && b >= 64 && b <= 127) return false;
  if (a === 169 && b === 254) return false;
  if (a === 172 && b >= 16 && b <= 31) return false;
  if (a === 192 && (b === 168 || b === 0 || b === 2)) return false;
  if (a === 192 && b === 88 && c === 99) return false;
  if (a === 198 && (b === 18 || b === 19 || b === 51 && c === 100)) return false;
  if (a === 203 && b === 0 && c === 113) return false;
  if (a === 255 && b === 255 && c === 255 && d === 255) return false;
  return true;
}

function ipv6Parts(address) {
  let value = address.toLowerCase();
  if (value.includes('.')) {
    const lastColon = value.lastIndexOf(':');
    const ipv4 = value.slice(lastColon + 1);
    if (isIP(ipv4) !== 4) return null;
    const [a, b, c, d] = ipv4.split('.').map(Number);
    value = `${value.slice(0, lastColon)}:${((a << 8) | b).toString(16)}:${((c << 8) | d).toString(16)}`;
  }
  const halves = value.split('::');
  if (halves.length > 2) return null;
  const left = halves[0] ? halves[0].split(':') : [];
  const right = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
  const zeros = halves.length === 2 ? 8 - left.length - right.length : 0;
  const parts = [...left, ...Array(zeros).fill('0'), ...right];
  if (parts.length !== 8 || parts.some(part => !/^[0-9a-f]{1,4}$/.test(part))) return null;
  return parts.map(part => parseInt(part, 16));
}

function publicAddress(address) {
  const version = isIP(address);
  if (version === 4) return publicIPv4(address);
  if (version !== 6) return false;
  const parts = ipv6Parts(address);
  if (!parts) return false;
  if (parts.slice(0, 5).every(part => part === 0) && parts[5] === 0xffff) {
    const ipv4 = `${parts[6] >> 8}.${parts[6] & 255}.${parts[7] >> 8}.${parts[7] & 255}`;
    return publicIPv4(ipv4);
  }
  if ((parts[0] & 0xe000) !== 0x2000) return false;
  if (parts[0] === 0x2001 && parts[1] <= 0x01ff) return false;
  if (parts[0] === 0x2001 && parts[1] === 0x0db8) return false;
  if (parts[0] === 0x2002) return false;
  return true;
}

/** Resolve and accept only public HTTP(S) destinations. */
export async function isSafePublicImageUrl(rawUrl, { resolveHost = null } = {}) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return false;
  }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password)
    return false;
  if (url.port && !['80', '443'].includes(url.port)) return false;
  const host = url.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase();
  if (!host || host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local') || host.endsWith('.internal'))
    return false;
  if (isIP(host)) return publicAddress(host);
  try {
    const resolve = resolveHost || (name => lookup(name, { all: true, verbatim: true }));
    const answers = await resolve(host);
    return Array.isArray(answers) && answers.length > 0 && answers.every(
      answer => answer && typeof answer.address === 'string' && publicAddress(answer.address),
    );
  } catch {
    return false;
  }
}

/** Fetch one public image while checking every redirect destination. */
export async function fetchPublicImage(request, rawUrl, { resolveHost = null, maxRedirects = 5 } = {}) {
  let currentUrl = rawUrl;
  for (let redirectCount = 0; redirectCount <= maxRedirects; redirectCount++) {
    if (!await isSafePublicImageUrl(currentUrl, { resolveHost }))
      return { status: 'url_rejected' };
    const response = await request.get(currentUrl, { timeout: 20000, maxRedirects: 0 });
    const status = response.status();
    const headers = response.headers();
    if (status >= 300 && status < 400) {
      const location = headers.location;
      if (!location) return { status: 'http_error', http_status: status };
      if (redirectCount === maxRedirects) return { status: 'redirect_limit', http_status: status };
      try {
        currentUrl = new URL(location, currentUrl).href;
      } catch {
        return { status: 'url_rejected', http_status: status };
      }
      continue;
    }
    if (!response.ok()) return { status: 'http_error', http_status: status };
    const contentType = (headers['content-type'] || '').split(';', 1)[0].trim().toLowerCase();
    if (!contentType.startsWith('image/'))
      return { status: 'content_type_error', http_status: status, content_type: contentType || null };
    return {
      status: 'downloaded',
      http_status: status,
      content_type: contentType,
      bytes: await response.body(),
      final_url: currentUrl,
    };
  }
  return { status: 'redirect_limit' };
}

/** Capture complete rendered outerHTML and the sale description's image files. */
export async function saveProductPageInputs({ page, product, request, rowIndex, config, resolveImageHost = null }) {
  const root = config.product_page_root;
  if (typeof root !== 'string' || !root) throw new Error('product_page_root_missing');
  const runId = request.run_id;
  if (typeof runId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(runId))
    throw new Error('product_run_id_invalid');
  const folder = path.join(root, safeRunSegment(runId), `shop-${segment(product.shop)}`, `item-${segment(product.item)}`);
  const pageFile = path.join(folder, 'page.json');
  const prior = await existingCapture(folder, runId, product.shop, product.item);
  if (prior) return {
    page_ref: prior.page_ref,
    state: prior.html_state === 'captured' ? 'existing_preserved' : 'capture_failed',
    image_count: prior.images?.length ?? 0,
  };

  await mkdir(path.join(folder, 'images'), { recursive: true });
  let captured;
  try {
    captured = await page.evaluate(async ({ selectors }) => {
    const selectorData = [];
    const saleElements = [];
    const matchesByKey = new Map();
    for (const [key, selector] of selectors) {
      const matches = Array.from(document.querySelectorAll(selector));
      matchesByKey.set(key, { selector, matches });
      if (key === 'sale_desc') saleElements.push(...matches);
    }
    for (const element of saleElements) {
      element.scrollIntoView({ block: 'center', behavior: 'instant' });
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }
    const saleImages = saleElements.flatMap(root => Array.from(root.querySelectorAll('img')));
    for (const image of saleImages) {
      image.scrollIntoView({ block: 'center', behavior: 'instant' });
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }
    const candidates = [];
    const add = (raw, tag, attribute) => {
      if (typeof raw !== 'string') return;
      const value = raw.trim().replace(/^url\((['"]?)(.*?)\1\)$/i, '$2');
      if (!value || value.startsWith('data:') || value.startsWith('blob:')) return;
      try {
        const url = new URL(value, location.href);
        if (['http:', 'https:'].includes(url.protocol))
          candidates.push({ url: url.href, occurrence: { order: candidates.length, tag, attribute } });
      } catch {}
    };
    const elements = saleElements.flatMap(root => [root, ...root.querySelectorAll('*')]);
    for (const element of elements) {
      const tag = element.tagName.toLowerCase();
      if (['img', 'source'].includes(element.tagName.toLowerCase())) {
        add(element.currentSrc, tag, 'currentSrc');
        for (const name of ['src', 'data-src', 'data-original', 'data-lazy-src', 'data-image']) add(element.getAttribute(name), tag, name);
        for (const name of ['srcset', 'data-srcset', 'data-lazy-srcset']) {
          const value = element.getAttribute(name);
          if (value) for (const entry of value.split(',')) add(entry.trim().split(/\s+/)[0], tag, name);
        }
      }
      for (const [attribute, css] of [['style', element.getAttribute('style') || ''], ['computedBackgroundImage', getComputedStyle(element).backgroundImage || '']])
        for (const match of css.matchAll(/url\((?:['"])?([^'")]+)(?:['"])?\)/gi)) add(match[1], tag, attribute);
    }
    for (const [key, selector] of selectors) {
      const matches = matchesByKey.get(key)?.matches || [];
      selectorData.push({ key, selector, status: matches.length ? (matches.some(el => el.innerHTML.trim()) ? 'captured' : 'empty') : 'not_found', outer_html: matches.map(el => el.outerHTML) });
    }
    const deduplicated = new Map();
    for (const candidate of candidates) {
      const current = deduplicated.get(candidate.url);
      if (current) current.occurrences.push(candidate.occurrence);
      else deduplicated.set(candidate.url, { url: candidate.url, occurrences: [candidate.occurrence] });
    }
    return { selectors: selectorData, images: Array.from(deduplicated.values()), page_url: location.href };
    }, { selectors: SELECTORS });
  } catch (error) {
    const pageRef = `ppi:${createHash('sha256').update(`${runId}\0${product.shop}\0${product.item}`).digest('hex').slice(0, 40)}`;
    const failed = {
      schema_version: 1,
      page_ref: pageRef,
      run_id: runId,
      shop_id: product.shop,
      item_id: product.item,
      item_url: product.url,
      source_evidence_ref: `${request.attempt_id}:row:${rowIndex}`,
      captured_at: new Date().toISOString(),
      html_state: 'capture_failed',
      html_error_class: ['Error','TypeError','TimeoutError'].includes(error?.name) ? error.name : 'other',
      selectors: SELECTORS.map(([key, selector]) => ({ key, selector, status: 'unavailable', outer_html: [] })),
      image_state: 'not_attempted',
      images: [],
    };
    await atomicJson(pageFile, failed);
    return { page_ref: pageRef, state: 'capture_failed', image_count: 0 };
  }

  const imageRecords = captured.images;
  const images = [];
  for (let index = 0; index < imageRecords.length; index++) {
    const { url, occurrences } = imageRecords[index];
    const record = { index, url, occurrences, status: 'download_failed', file: null, content_type: null, byte_length: null };
    try {
      const response = await fetchPublicImage(page.request, url, {
        resolveHost: resolveImageHost,
      });
      record.status = response.status;
      record.http_status = response.http_status;
      record.content_type = response.content_type || null;
      if (response.status === 'downloaded') {
        const bytes = response.bytes;
        const ext = ({'image/jpeg':'.jpg','image/png':'.png','image/gif':'.gif','image/webp':'.webp','image/avif':'.avif','image/svg+xml':'.svg'})[record.content_type?.split(';')[0].toLowerCase()] || path.extname(new URL(url).pathname).slice(0, 8) || '.bin';
        const fileName = `${String(index + 1).padStart(4, '0')}-${createHash('sha256').update(url).digest('hex').slice(0, 12)}${ext}`;
        await writeFile(path.join(folder, 'images', fileName), bytes);
        record.file = `images/${fileName}`;
        record.byte_length = bytes.length;
      }
    } catch (error) {
      record.error_class = ['Error','TypeError','TimeoutError'].includes(error?.name) ? error.name : 'other';
    }
    images.push(record);
  }
  const pageRef = `ppi:${createHash('sha256').update(`${runId}\0${product.shop}\0${product.item}`).digest('hex').slice(0, 40)}`;
  const output = {
    schema_version: 1,
    page_ref: pageRef,
    run_id: runId,
    shop_id: product.shop,
    item_id: product.item,
    item_url: product.url,
    source_evidence_ref: `${request.attempt_id}:row:${rowIndex}`,
    captured_at: new Date().toISOString(),
    html_state: 'captured',
    selectors: captured.selectors,
    image_state: imageRecords.length ? 'captured' : 'no_images',
    images,
  };
  await atomicJson(pageFile, output);
  return { page_ref: pageRef, state: 'captured', image_count: images.length };
}
