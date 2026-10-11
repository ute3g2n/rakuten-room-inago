export function normalizeRakutenItemUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.hostname !== 'item.rakuten.co.jp') return null;
    const path = url.pathname.replace(/\/+$/, '');
    if (!/^\/[^/]+\/[^/]+$/.test(path)) return null;
    return `https://item.rakuten.co.jp${path}`;
  } catch {
    return null;
  }
}

export function buildShopHomeUrl(itemUrl) {
  const normalizedItemUrl = normalizeRakutenItemUrl(itemUrl);
  if (!normalizedItemUrl) return null;
  const url = new URL(normalizedItemUrl);
  const shopCode = url.pathname.split('/').filter(Boolean)[0];
  return shopCode ? `${url.origin}/${shopCode}/` : null;
}

export function buildRakutenSearchUrl(keyword) {
  if (typeof keyword !== 'string' || !keyword.trim()) throw new Error('検索語が空です');
  return `https://search.rakuten.co.jp/search/mall/${encodeURIComponent(keyword.trim())}/`;
}

export function collectProductGalleryImageUrls(sources, shopCode) {
  if (!Array.isArray(sources) || typeof shopCode !== 'string' || !shopCode) return [];
  const urls = [];
  for (const source of sources) {
    try {
      const url = new URL(source);
      if (!['tshop.r10s.jp', 'image.rakuten.co.jp'].includes(url.hostname) ||
          !url.pathname.includes(`/${shopCode}/`) || !/\.(?:jpe?g|png|webp|gif)$/i.test(url.pathname)) continue;
      urls.push(`${url.origin}${url.pathname}`);
    } catch { /* Ignore lazy-load placeholders and malformed URLs. */ }
  }
  return [...new Set(urls)];
}

export function parseProductReviewCount(...values) {
  for (const value of values) {
    if (typeof value !== 'string' || !value.trim()) continue;
    const match = value.match(/[（(]\s*([\d,]+)\s*件\s*[）)]/);
    if (!match) continue;
    const count = Number(match[1].replace(/,/g, ''));
    if (Number.isSafeInteger(count) && count >= 0) return count;
  }
  return null;
}

export function extractProductReviewCountText(text) {
  if (typeof text !== 'string') return null;
  const start = text.indexOf('この商品を購入された方のレビュー');
  if (start < 0) return null;
  return text.slice(start, start + 500).match(/[（(]\s*[\d,]+\s*件\s*[）)]/)?.[0] ?? null;
}

export function extractPointPromotionText(text) {
  if (typeof text !== 'string') return null;
  const lines = [...new Set(text.split(/\r?\n/).map((line) => line.trim()).filter((line) =>
    /ポイントアップ|ポイント\s*\d+\s*倍|P\s*\d+\s*倍/i.test(line)))];
  return lines.length ? lines.join('\n').slice(0, 1000) : null;
}

export function parsePromotionDeadline(text, observedAt) {
  if (typeof text !== 'string' || typeof observedAt !== 'string') return null;
  const observed = new Date(observedAt);
  if (!Number.isFinite(observed.getTime())) return null;
  const validDate = (year, month, day, hour, minute) => {
    const candidate = new Date(Date.UTC(year, month - 1, day, hour, minute));
    if (candidate.getUTCFullYear() !== year || candidate.getUTCMonth() !== month - 1 ||
        candidate.getUTCDate() !== day || hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
    return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}T${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:00+09:00`;
  };
  const explicit = [...text.matchAll(/(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s*(?:\([日月火水木金土]\)|（[日月火水木金土]）)?\s*(\d{1,2}):(\d{2})/g)].at(-1);
  if (explicit) return validDate(...explicit.slice(1).map(Number));

  const monthDay = [...text.matchAll(/(\d{1,2})\s*(?:\/|月)\s*(\d{1,2})\s*日?\s*(\d{1,2})(?::(\d{2})|時)/g)].at(-1);
  const observedParts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(observed).map(({ type, value }) => [type, value]));
  const year = Number(observedParts.year);
  const observedMonth = Number(observedParts.month);
  const observedDay = Number(observedParts.day);
  if (monthDay) {
    const [, month, day, hour, minute] = monthDay.map(Number);
    if (month < observedMonth || (month === observedMonth && day < observedDay)) return null;
    return validDate(year, month, day, hour, minute ?? 0);
  }

  const dayOnly = [...text.matchAll(/(?:～|〜|~|-)\s*(\d{1,2})\s*日\s*(\d{1,2})(?::(\d{2})|時)/g)].at(-1);
  if (!dayOnly) return null;
  const [, day, hour, minute] = dayOnly.map(Number);
  if (day < observedDay) return null;
  return validDate(year, observedMonth, day, hour, minute ?? 0);
}

export function parseRecentReviewRows(text) {
  if (typeof text !== 'string') return [];
  const start = text.indexOf('レビュー一覧');
  if (start < 0) return [];
  const section = text.slice(start);
  const pattern = /(?:^|\n)\s*([1-5])\s*\n\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s*\n/g;
  const rows = [];
  for (const match of section.matchAll(pattern)) {
    const [, stars, year, month, day] = match;
    const iso = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const parsed = new Date(`${iso}T00:00:00Z`);
    if (Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === iso) {
      rows.push({ stars: Number(stars), date: iso });
      if (rows.length === 10) break;
    }
  }
  return rows;
}

export function parseShopReviewCount(text) {
  if (typeof text !== 'string') return null;
  const start = text.indexOf('ショップレビュー');
  const section = start >= 0 ? text.slice(start, start + 800) : '';
  const match = start >= 0
    ? section.match(/[（(]\s*([\d,，]+)\s*件\s*[）)]/)
    : text.match(/\d{1,2}\.\d{1,2}\s*[（(]\s*([\d,，]+)\s*件\s*[）)]/);
  if (!match) return null;
  const count = Number(match[1].replace(/[,，]/g, ''));
  return Number.isSafeInteger(count) && count >= 0 ? count : null;
}

export function makeSearchEvidence({ candidate, keyword, pageUrl, observedAt, cards, error }) {
  const base = { keyword, page_url: pageUrl ?? buildRakutenSearchUrl(keyword), observed_at: observedAt };
  if (error) return { ...base, status: 'unavailable', matches: null, error: String(error) };
  const candidateUrl = normalizeRakutenItemUrl(candidate?.url);
  if (!candidateUrl || !Array.isArray(cards)) {
    return { ...base, status: 'unavailable', matches: null, error: '候補URLまたは検索結果の形式が不正です' };
  }
  const matches = cards.filter((card) => normalizeRakutenItemUrl(card?.url) === candidateUrl)
    .map((card) => ({
      position: Number.isInteger(card.position) && card.position > 0 ? card.position : null,
      placement: card.placement === 'pr' || card.placement === 'organic' ? card.placement : null,
      placement_evidence: card.placement_evidence ?? null,
      title: typeof card.title === 'string' && card.title ? card.title : null,
      item_url: card.url,
    }));
  return { ...base, status: 'observed', matches, error: null };
}
