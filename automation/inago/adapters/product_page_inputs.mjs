import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { lookup } from 'node:dns/promises';
import { isIP } from 'node:net';

const SELECTORS = [
  ['sale_desc', 'span.sale_desc'],
  ['normal_reserve_item_name', 'span.normal_reserve_item_name'],
  ['normal_reserve_catch_copy', 'span.normal_reserve_catch_copy'],
  ['item_desc', 'span.item_desc'],
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

async function collectProductPage({ selectors }) {
  const observationStartedAt = performance.now();
  const maximumObservationMs = 2500;
  const observationDeadline = observationStartedAt + maximumObservationMs;
  const remainingObservationMs = () => Math.max(0, observationDeadline - performance.now());
  const waitForAnimationFrames = () => new Promise(resolve => {
    let finished = false;
    const finish = completed => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      resolve(completed);
    };
    const timer = setTimeout(() => finish(false), remainingObservationMs());
    requestAnimationFrame(() => {
      if (finished) return;
      if (remainingObservationMs() <= 0) return finish(false);
      requestAnimationFrame(() => finish(remainingObservationMs() > 0));
    });
  });
  const matchesByKey = new Map();
  for (const [key, selector] of selectors)
    matchesByKey.set(key, { selector, matches: Array.from(document.querySelectorAll(selector)) });

  const descriptionKeys = new Set(['sale_desc', 'item_desc']);
  const scrollRoots = [];
  for (const key of descriptionKeys)
    scrollRoots.push(...(matchesByKey.get(key)?.matches || []));
  let lastRelevantMutation = performance.now();
  const mutationObserver = typeof MutationObserver === 'function'
    ? new MutationObserver(() => { lastRelevantMutation = performance.now(); })
    : null;
  if (mutationObserver) for (const root of scrollRoots)
    mutationObserver.observe(root, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['src', 'srcset', 'data-src', 'data-srcset', 'data-original', 'data-lazy-src', 'data-lazy-srcset', 'data-image', 'data-loaded', 'style'],
    });
  let observationTimedOut = false;
  for (const element of scrollRoots) {
    if (remainingObservationMs() <= 0) { observationTimedOut = true; break; }
    element.scrollIntoView({ block: 'center', behavior: 'instant' });
    if (!await waitForAnimationFrames()) { observationTimedOut = true; break; }
  }
  const scrollImages = scrollRoots.flatMap(root => Array.from(root.querySelectorAll('img')));
  for (const image of scrollImages) {
    if (remainingObservationMs() <= 0) { observationTimedOut = true; break; }
    image.scrollIntoView({ block: 'center', behavior: 'instant' });
    if (!await waitForAnimationFrames()) { observationTimedOut = true; break; }
  }

  const normalizeResource = raw => {
    if (typeof raw !== 'string' || !raw.trim()) return '';
    const value = raw.trim();
    if (/^(?:data|blob):/i.test(value)) return value;
    try { return new URL(value, location.href).href; } catch { return value; }
  };
  const unresolvedLazySources = () => {
    const unresolved = [];
    for (const key of descriptionKeys) {
      const { matches = [], selector } = matchesByKey.get(key) || {};
      for (let matchIndex = 0; matchIndex < matches.length; matchIndex++) {
        const root = matches[matchIndex];
        const elements = [root, ...Array.from(root.querySelectorAll('*'))];
        for (let domPosition = 0; domPosition < elements.length; domPosition++) {
          const element = elements[domPosition];
          const tag = element.tagName.toLowerCase();
          if (!['img', 'source'].includes(tag)) continue;
          const provenance = { key, selector, match_index: matchIndex, dom_position: domPosition, tag };
          for (const [attribute, activeAttribute] of [
            ['data-src', 'src'], ['data-original', 'src'], ['data-lazy-src', 'src'], ['data-image', 'src'],
          ]) {
            const deferred = normalizeResource(element.getAttribute(attribute));
            if (!deferred) continue;
            const active = [element.getAttribute(activeAttribute), element.currentSrc]
              .map(normalizeResource).filter(Boolean);
            if (!active.includes(deferred)) unresolved.push({ ...provenance, attribute });
          }
          for (const attribute of ['data-srcset', 'data-lazy-srcset']) {
            const deferred = element.getAttribute(attribute)?.trim();
            if (!deferred) continue;
            const active = element.getAttribute('srcset')?.trim() || '';
            if (active !== deferred) unresolved.push({ ...provenance, attribute });
          }
        }
      }
    }
    return unresolved;
  };
  let lazyLoadState = 'settled';
  let lazyLoadReason = null;
  let unresolved = [];
  if (scrollRoots.length) {
    const minimumObservationMs = 500;
    const quietWindowMs = 150;
    while (!observationTimedOut && remainingObservationMs() > 0) {
      await new Promise(resolve => setTimeout(resolve, Math.min(50, remainingObservationMs())));
      const now = performance.now();
      unresolved = unresolvedLazySources();
      if (!unresolved.length && now - observationStartedAt >= minimumObservationMs && now - lastRelevantMutation >= quietWindowMs)
        break;
    }
    unresolved = unresolvedLazySources();
    if (observationTimedOut || remainingObservationMs() <= 0) {
      lazyLoadState = 'timed_out';
      lazyLoadReason = 'observation_timeout';
    } else if (unresolved.length) {
      lazyLoadState = 'timed_out';
      lazyLoadReason = 'unresolved_deferred_sources';
    }
  }
  const observationElapsedMs = performance.now() - observationStartedAt;
  mutationObserver?.disconnect();

  // Re-read after the bounded lazy-load observation so late child/source updates are retained.
  for (const [key, selector] of selectors)
    matchesByKey.set(key, { selector, matches: Array.from(document.querySelectorAll(selector)) });

  const candidates = [];
  const inlineCache = new Map();
  let order = 0;
  const add = async (raw, provenance) => {
    if (typeof raw !== 'string') return;
    const value = raw.trim().replace(/^url\((['"]?)(.*?)\1\)$/i, '$2');
    if (!value) return;
    let url;
    let sourceKind;
    let sourceError = null;
    try {
      if (/^data:/i.test(value)) {
        url = value;
        sourceKind = 'data';
      } else if (/^blob:/i.test(value)) {
        url = new URL(value, location.href).href;
        sourceKind = 'blob';
      } else {
        const parsed = new URL(value, location.href);
        url = parsed.href;
        if (!['http:', 'https:'].includes(parsed.protocol)) {
          sourceKind = 'unsupported';
          sourceError = 'source_type_error';
        } else sourceKind = 'http';
      }
    } catch {
      url = value;
      sourceKind = 'invalid';
      sourceError = 'invalid_source';
    }

    const candidate = { url, source_kind: sourceKind, source_error: sourceError, occurrence: { ...provenance, order: order++ } };
    if (sourceKind === 'blob') {
      if (!inlineCache.has(url)) {
        inlineCache.set(url, (async () => {
          try {
            const response = await fetch(url);
            if (!response.ok) return { inline_error: 'inline_fetch_error' };
            const bytes = new Uint8Array(await response.arrayBuffer());
            let binary = '';
            for (let offset = 0; offset < bytes.length; offset += 0x8000)
              binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
            return { inline_bytes_base64: btoa(binary), inline_content_type: response.headers.get('content-type') || '' };
          } catch (error) {
            return { inline_error: ['Error', 'TypeError', 'TimeoutError'].includes(error?.name) ? error.name : 'other' };
          }
        })());
      }
      Object.assign(candidate, await inlineCache.get(url));
    }
    candidates.push(candidate);
  };
  const srcsetUrls = value => {
    const urls = [];
    let position = 0;
    while (position < value.length) {
      while (position < value.length && /[\s,]/.test(value[position])) position++;
      if (position >= value.length) break;
      const start = position;
      while (position < value.length && !/\s/.test(value[position])) position++;
      let url = value.slice(start, position);
      let trailingCommas = 0;
      while (url.endsWith(',')) { url = url.slice(0, -1); trailingCommas++; }
      if (url) urls.push(url);
      if (trailingCommas === 0) {
        while (position < value.length && value[position] !== ',') position++;
        if (value[position] === ',') position++;
      }
    }
    return urls;
  };

  for (const key of descriptionKeys) {
    const { matches = [] } = matchesByKey.get(key) || {};
    for (let matchIndex = 0; matchIndex < matches.length; matchIndex++) {
      const root = matches[matchIndex];
      const elements = [root, ...Array.from(root.querySelectorAll('*'))];
      for (let domPosition = 0; domPosition < elements.length; domPosition++) {
        const element = elements[domPosition];
        const tag = element.tagName.toLowerCase();
        const provenance = { key, selector: matchesByKey.get(key).selector, match_index: matchIndex, dom_position: domPosition, tag };
        if (['img', 'source'].includes(tag)) {
          await add(element.currentSrc, { ...provenance, attribute: 'currentSrc' });
          for (const name of ['src', 'data-src', 'data-original', 'data-lazy-src', 'data-image'])
            await add(element.getAttribute(name), { ...provenance, attribute: name });
          for (const name of ['srcset', 'data-srcset', 'data-lazy-srcset']) {
            const value = element.getAttribute(name);
            if (value) for (const url of srcsetUrls(value)) await add(url, { ...provenance, attribute: name });
          }
        }
        for (const [attribute, css] of [['style', element.getAttribute('style') || ''], ['computedBackgroundImage', getComputedStyle(element).backgroundImage || '']])
          for (const match of css.matchAll(/url\((?:['"])?([^'")]+)(?:['"])?\)/gi))
            await add(match[1], { ...provenance, attribute });
      }
    }
  }

  const selectorData = [];
  for (const [key, selector] of selectors) {
    const matches = matchesByKey.get(key)?.matches || [];
    selectorData.push({ key, selector, status: matches.length ? (matches.some(el => el.innerHTML.trim()) ? 'captured' : 'empty') : 'not_found', outer_html: matches.map(el => el.outerHTML) });
  }
  const deduplicated = new Map();
  for (const candidate of candidates) {
    const current = deduplicated.get(candidate.url);
    if (current) current.occurrences.push(candidate.occurrence);
    else deduplicated.set(candidate.url, { ...candidate, occurrences: [candidate.occurrence] });
  }
  return {
    selectors: selectorData,
    images: Array.from(deduplicated.values()),
    page_url: location.href,
    lazy_load_state: lazyLoadState,
    lazy_load_reason: lazyLoadReason,
    lazy_load_observed_ms: Math.round(observationElapsedMs),
    lazy_load_unresolved: unresolved,
  };
}

function imageUrlForManifest(image) {
  if (['data', 'blob', 'invalid', 'unsupported'].includes(image.source_kind))
    return `${image.source_kind}:sha256:${createHash('sha256').update(image.url).digest('hex')}`;
  return image.url;
}

function decodeDataImage(value) {
  const comma = value.indexOf(',');
  if (!/^data:/i.test(value) || comma < 0) throw new Error('inline_decode_error');
  const header = value.slice(5, comma);
  const contentType = header.split(';', 1)[0] || 'text/plain';
  const payload = value.slice(comma + 1);
  let bytes;
  if (/;base64(?:;|$)/i.test(header)) {
    if (!/^[A-Za-z0-9+/]*={0,2}$/.test(payload)
      || payload.length % 4 === 1
      || (payload.includes('=') && payload.length % 4 !== 0))
      throw new Error('inline_decode_error');
    bytes = Buffer.from(payload, 'base64');
    if (!bytes.length || bytes.toString('base64').replace(/=+$/, '') !== payload.replace(/=+$/, ''))
      throw new Error('inline_decode_error');
  } else {
    const decoded = [];
    for (let index = 0; index < payload.length;) {
      if (payload[index] === '%') {
        if (!/^[0-9a-f]{2}$/i.test(payload.slice(index + 1, index + 3)))
          throw new Error('inline_decode_error');
        decoded.push(parseInt(payload.slice(index + 1, index + 3), 16));
        index += 3;
      } else {
        const character = String.fromCodePoint(payload.codePointAt(index));
        decoded.push(...Buffer.from(character, 'utf8'));
        index += character.length;
      }
    }
    bytes = Buffer.from(decoded);
  }
  if (!bytes.length) throw new Error('inline_decode_error');
  return { bytes, content_type: contentType };
}

async function saveImageRecord(folder, image, request, resolveImageHost, index, occurrences = image.occurrences) {
  const sourceKind = image.source_kind || (/^https?:/i.test(image.url || '') ? 'http' : 'unknown');
  const sourceUrl = image.url;
  const record = {
    index,
    url: imageUrlForManifest(image),
    source_kind: sourceKind,
    source_url_sha256: createHash('sha256').update(sourceUrl || '').digest('hex'),
    occurrences,
    status: 'download_failed',
    file: null,
    content_type: null,
    byte_length: null,
  };
  if (image.source_error) {
    record.status = image.source_error;
    return record;
  }
  try {
    let bytes;
    if (sourceKind === 'data') {
      const decoded = decodeDataImage(sourceUrl);
      record.content_type = decoded.content_type;
      if (!record.content_type.toLowerCase().startsWith('image/')) {
        record.status = 'content_type_error';
        record.content_type = record.content_type.toLowerCase();
        return record;
      }
      bytes = decoded.bytes;
    } else if (sourceKind === 'blob') {
      if (image.inline_error) {
        record.status = 'inline_fetch_error';
        record.error_class = image.inline_error;
        return record;
      }
      record.content_type = (image.inline_content_type || '').split(';', 1)[0].trim().toLowerCase();
      if (!record.content_type.startsWith('image/')) {
        record.status = 'content_type_error';
        return record;
      }
      bytes = Buffer.from(image.inline_bytes_base64 || '', 'base64');
    } else if (sourceKind === 'http') {
      const response = await fetchPublicImage(request, sourceUrl, { resolveHost: resolveImageHost });
      record.status = response.status;
      record.http_status = response.http_status;
      record.content_type = response.content_type || null;
      if (response.status !== 'downloaded') return record;
      bytes = response.bytes;
    } else {
      record.status = 'source_type_error';
      return record;
    }

    if (!bytes.length) {
      record.status = 'empty_image_error';
      return record;
    }

    const extension = ({ 'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp', 'image/avif': '.avif', 'image/svg+xml': '.svg' })[record.content_type] || '.bin';
    const fileName = `${String(index + 1).padStart(4, '0')}-${record.source_url_sha256.slice(0, 12)}${extension}`;
    await writeFile(path.join(folder, 'images', fileName), bytes);
    record.status = 'downloaded';
    record.file = `images/${fileName}`;
    record.byte_length = bytes.length;
  } catch (error) {
    record.error_class = safeErrorClass(error);
    if (sourceKind === 'data') record.status = 'inline_decode_error';
  }
  return record;
}

async function cleanupIsComplete(folder) {
  try {
    const value = JSON.parse(await readFile(path.join(folder, 'cleanup.json'), 'utf8'));
    return value.status === 'complete';
  } catch { return false; }
}

function safeErrorClass(error) {
  return ['Error', 'TypeError', 'TimeoutError'].includes(error?.name) ? error.name : 'other';
}

/** Capture complete rendered outerHTML and every image in both product description areas. */
export async function saveProductPageInputs({ page, product, request, rowIndex, config, resolveImageHost = null, retryFailedImages = false }) {
  const root = config.product_page_root;
  if (typeof root !== 'string' || !root) throw new Error('product_page_root_missing');
  const runId = request.run_id;
  if (typeof runId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(runId))
    throw new Error('product_run_id_invalid');
  const folder = path.join(root, safeRunSegment(runId), `shop-${segment(product.shop)}`, `item-${segment(product.item)}`);
  const pageFile = path.join(folder, 'page.json');
  const prior = await existingCapture(folder, runId, product.shop, product.item);
  if (prior) {
    if (!retryFailedImages || await cleanupIsComplete(folder)) return {
      page_ref: prior.page_ref,
      state: prior.html_state === 'captured' ? 'existing_preserved' : 'capture_failed',
      image_count: prior.images?.length ?? 0,
    };
    const failures = Array.isArray(prior.images) ? prior.images.filter(image => image?.status !== 'downloaded') : [];
    if (!failures.length) return { page_ref: prior.page_ref, state: 'existing_preserved', image_count: prior.images?.length ?? 0 };
    let recaptured = [];
    const needsInlineRecapture = failures.some(image => image.source_kind === 'data' || image.source_kind === 'blob');
    let recaptureErrorClass = null;
    if (needsInlineRecapture) {
      try {
        const fresh = await page.evaluate(collectProductPage, { selectors: SELECTORS });
        recaptured = fresh.images || [];
      } catch (error) {
        recaptureErrorClass = safeErrorClass(error);
      }
    }
    const freshByUrl = new Map(recaptured.map(image => [imageUrlForManifest(image), image]));
    const replacements = new Map();
    let attempted = 0;
    for (const failure of failures) {
      const sourceKind = failure.source_kind || (/^https?:/i.test(failure.url || '') ? 'http' : 'unknown');
      let candidate;
      if (sourceKind === 'http') candidate = { ...failure, source_kind: 'http', url: failure.url };
      else candidate = freshByUrl.get(failure.url);
      if (!candidate) continue;
      attempted++;
      replacements.set(failure.index, await saveImageRecord(folder, candidate, page.request, resolveImageHost, failure.index, failure.occurrences));
    }
    if (replacements.size) {
      prior.images = prior.images.map(image => replacements.get(image.index) || image);
      await atomicJson(pageFile, prior);
    }
    let retryDiagnostic = null;
    if (recaptureErrorClass) retryDiagnostic = {
      status: 'failed',
      reason: 'dom_recapture_failed',
      error_class: recaptureErrorClass,
    };
    else if (needsInlineRecapture && attempted === 0)
      retryDiagnostic = { status: 'not_retried', reason: 'candidate_not_found' };
    return {
      page_ref: prior.page_ref,
      state: attempted ? 'images_retried' : 'existing_preserved',
      image_count: prior.images?.length ?? 0,
      ...(retryDiagnostic ? { retry_diagnostic: retryDiagnostic } : {}),
    };
  }

  await mkdir(path.join(folder, 'images'), { recursive: true });
  let captured;
  try {
    captured = await page.evaluate(collectProductPage, { selectors: SELECTORS });
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
      html_error_class: safeErrorClass(error),
      selectors: SELECTORS.map(([key, selector]) => ({ key, selector, status: 'unavailable', outer_html: [] })),
      image_state: 'not_attempted',
      lazy_load_state: 'not_attempted',
      lazy_load_reason: null,
      lazy_load_observed_ms: 0,
      lazy_load_unresolved: [],
      images: [],
    };
    await atomicJson(pageFile, failed);
    return { page_ref: pageRef, state: 'capture_failed', image_count: 0 };
  }

  const imageRecords = captured.images;
  const images = [];
  for (let index = 0; index < imageRecords.length; index++)
    images.push(await saveImageRecord(folder, imageRecords[index], page.request, resolveImageHost, index));
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
    image_state: captured.lazy_load_state === 'timed_out' ? 'lazy_load_incomplete' : imageRecords.length ? 'captured' : 'no_images',
    lazy_load_state: captured.lazy_load_state || 'settled',
    lazy_load_reason: captured.lazy_load_reason || null,
    lazy_load_observed_ms: Number.isFinite(captured.lazy_load_observed_ms) ? captured.lazy_load_observed_ms : 0,
    lazy_load_unresolved: captured.lazy_load_unresolved || [],
    images,
  };
  await atomicJson(pageFile, output);
  return { page_ref: pageRef, state: 'captured', image_count: images.length };
}
