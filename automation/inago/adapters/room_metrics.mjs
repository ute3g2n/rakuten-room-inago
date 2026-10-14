const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?(?:Z|[+-]\d{2}:\d{2})$/;

function timestamp(value, label, { nullable = false } = {}) {
  if (value === null && nullable) return null;
  if (typeof value !== 'string' || !ISO_RE.test(value) || Number.isNaN(Date.parse(value))) {
    throw new Error(`${label} must be an offset-aware timestamp${nullable ? ' or null' : ''}`);
  }
  return value;
}

function safeUrl(value, label, requiredHost) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${label} is required`);
  let parsed;
  try { parsed = new URL(value); } catch { throw new Error(`${label} is invalid`); }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password) {
    throw new Error(`${label} must be an HTTPS URL without credentials`);
  }
  const hostname = parsed.hostname.toLowerCase();
  if (hostname !== requiredHost) {
    throw new Error(`${label} host is outside the supported service`);
  }
  return parsed.href;
}

export function roomWindowStart(observedAt, days = 30) {
  timestamp(observedAt, 'observedAt');
  if (!Number.isInteger(days) || days < 1 || days > 30) {
    throw new Error('ROOM reaction window must be between 1 and 30 days');
  }
  const epoch = Date.parse(observedAt) - days * 24 * 60 * 60 * 1000;
  const jst = new Date(epoch + 9 * 60 * 60 * 1000).toISOString().slice(0, 19);
  return `${jst}+09:00`;
}

export function normalizeRoomMetricsResponse({
  status, observedAt, roomUrl, posts, errorCode = null,
}) {
  if (!['observed', 'partial', 'unavailable', 'not_present'].includes(status)) {
    throw new Error('ROOM metrics status is unsupported');
  }
  timestamp(observedAt, 'observedAt');
  const normalizedRoomUrl = safeUrl(roomUrl, 'roomUrl', 'room.rakuten.co.jp');
  const startAt = roomWindowStart(observedAt, 30);
  if (status === 'unavailable' || status === 'not_present') {
    return { status, observedAt, roomUrl: normalizedRoomUrl, windowStart: startAt, posts: [], errorCode };
  }
  if (!Array.isArray(posts)) throw new Error('ROOM posts must be an array');
  const seen = new Set();
  const normalized = [];
  for (const post of posts) {
    if (post?.isPublic === false) continue;
    const postId = String(post?.postId ?? '').trim();
    if (!postId || postId.length > 128) throw new Error('ROOM postId is required');
    if (seen.has(postId)) throw new Error('ROOM postId cannot repeat');
    seen.add(postId);
    const likes = post.likes;
    if (likes !== null && (!Number.isSafeInteger(likes) || likes < 0)) {
      throw new Error('ROOM likes must be a non-negative integer or null');
    }
    const createdAt = timestamp(post.createdAt, 'ROOM createdAt', { nullable: true });
    if (createdAt && Date.parse(createdAt) < Date.parse(startAt)) continue;
    if (createdAt && Date.parse(createdAt) > Date.parse(observedAt)) {
      throw new Error('ROOM post creation time is after the observation');
    }
    normalized.push({
      postId,
      roomPostUrl: safeUrl(post.roomPostUrl, 'roomPostUrl', 'room.rakuten.co.jp'),
      itemUrl: post.itemUrl === null ? null : safeUrl(post.itemUrl, 'itemUrl', 'item.rakuten.co.jp'),
      likes,
      createdAt,
    });
  }
  return {
    status,
    observedAt,
    roomUrl: normalizedRoomUrl,
    windowStart: startAt,
    posts: normalized,
    errorCode: status === 'partial' ? errorCode : null,
  };
}
