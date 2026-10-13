/**
 * Fixture-facing Google Trends response validator.
 *
 * This module performs no browser, network, login, or UI operation. A permitted
 * unattended connector and its exact call contract remain unconfirmed; callers
 * may pass an observation here only after they obtain it through an authorized
 * path. Unknown or stale observations are stripped of all words.
 */

const QUERY = Object.freeze({
  source: "google_trends_public_ui",
  language: "ja",
  geo: "JP",
  search_type: "web",
  range: "90d",
  list_type: "Top",
});

const REQUIRED_KEYS = Object.freeze([
  "request_id",
  "snapshot_id",
  "run_id",
  "settings_version",
  "source",
  "parent_word",
  "current_parent",
  "refresh_complete",
  "language",
  "geo",
  "search_type",
  "range",
  "list_type",
  "status",
  "fetched_at",
  "error_code",
  "words",
]);
const SAFE_REF = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function invalid(errorCode) {
  return { status: "invalid", words: [], error_code: errorCode };
}

function validTimestamp(value) {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function validWord(word) {
  return (
    word !== null &&
    typeof word === "object" &&
    !Array.isArray(word) &&
    Object.keys(word).sort().join(",") ===
      ["popularity_index", "raw", "source_rank", "word_id"].sort().join(",") &&
    typeof word.word_id === "string" &&
    SAFE_REF.test(word.word_id) &&
    typeof word.raw === "string" &&
    word.raw.trim().length > 0 &&
    Number.isInteger(word.source_rank) &&
    word.source_rank > 0 &&
    (word.popularity_index === null ||
      (typeof word.popularity_index === "number" &&
        Number.isFinite(word.popularity_index) &&
        word.popularity_index >= 0))
  );
}

/**
 * Validate one exact-parent snapshot. Invalid responses are returned without
 * candidate words, so stale results cannot enter KEY or keyword selection.
 */
export function validateTrendsSnapshot(payload, expected) {
  return validateScopedTrendsSnapshot(payload, expected, QUERY, REQUIRED_KEYS);
}

// Q1.3 remains a historical reader above. Current daily data has no hierarchy:
// original Top20 rows enter the AI semantic-group job unchanged.
export function validateTrendsGroupedSnapshot(payload, expected) {
  if (!payload || typeof payload !== "object" || !Array.isArray(payload.words)) {
    return invalid("response_not_object");
  }
  const period = typeof payload.ui_period_label === "string"
    ? payload.ui_period_label.normalize("NFKC").replace(/\s+/g, "") : "";
  const labels = new Set(["過去3か月間", "過去3か月", "過去3ヶ月間", "過去3ヶ月", "過去90日間", "Past3months"]);
  if (payload.status === "observed" && !labels.has(period)) {
    return invalid("query_freshness_mismatch");
  }
  const result = validateScopedTrendsSnapshot(
    payload, expected, {...QUERY, range: "3m"}, [...REQUIRED_KEYS, "ui_period_label"]
  );
  if (result.status !== "observed") return result;
  return {...result, words: result.words.map((word, index) => ({word, index}))
    .sort((a,b) => a.word.source_rank - b.word.source_rank || a.index - b.index)
    .slice(0,20).map(row => row.word), ui_period_label: payload.ui_period_label};
}

function validateScopedTrendsSnapshot(payload, expected, scope, requiredKeys) {
  if (
    payload === null ||
    typeof payload !== "object" ||
    Array.isArray(payload) ||
    expected === null ||
    typeof expected !== "object"
  ) {
    return invalid("response_not_object");
  }
  const keys = Object.keys(payload).sort();
  if (keys.join(",") !== [...requiredKeys].sort().join(",")) {
    return invalid("response_shape_invalid");
  }
  if (
    !SAFE_REF.test(payload.request_id) ||
    !SAFE_REF.test(payload.snapshot_id) ||
    !SAFE_REF.test(payload.run_id) ||
    !SAFE_REF.test(payload.settings_version) ||
    typeof payload.parent_word !== "string" ||
    payload.parent_word.trim().length === 0 ||
    payload.request_id !== expected.requestId ||
    payload.run_id !== expected.runId ||
    payload.settings_version !== expected.settingsVersion ||
    payload.parent_word !== expected.parentWord ||
    Object.entries(scope).some(([key, value]) => payload[key] !== value)
  ) {
    return invalid("request_contract_mismatch");
  }
  if (!Array.isArray(payload.words) || !payload.words.every(validWord)) {
    return invalid("observed_words_invalid");
  }
  const wordIds = payload.words.map((word) => word.word_id);
  if (new Set(wordIds).size !== wordIds.length) {
    return invalid("duplicate_word_id");
  }
  if (payload.status === "unavailable") {
    if (
      payload.words.length !== 0 ||
      payload.current_parent !== null ||
      payload.refresh_complete !== false ||
      payload.fetched_at !== null ||
      typeof payload.error_code !== "string" ||
      payload.error_code.trim().length === 0
    ) {
      return invalid("failure_response_contains_stale_data");
    }
    return { status: "unavailable", words: [], error_code: payload.error_code };
  }
  if (payload.status !== "observed") {
    return invalid("status_invalid");
  }
  if (
    payload.current_parent !== expected.parentWord ||
    payload.refresh_complete !== true ||
    !validTimestamp(payload.fetched_at) ||
    payload.error_code !== null
  ) {
    return invalid("query_freshness_mismatch");
  }
  return {
    status: "observed",
    snapshot_id: payload.snapshot_id,
    words: payload.words,
    error_code: null,
  };
}

const ROOM_REQUIRED_KEYS = Object.freeze([
  "attempt_id",
  "run_id",
  "query",
  "parent_refs",
  "level",
  "likes_min",
  "max_posts",
  "current_search_id",
  "search_loaded",
  "internal_order_confirmed",
  "status",
  "fetched_at",
  "end_reached",
  "error_code",
  "posts",
]);
const ROOM_POST_KEYS = Object.freeze([
  "internal_index",
  "stable_room_id",
  "room_url",
  "likes",
  "comments",
  "item_url",
  "observed_shop_id",
  "observed_item_id",
  "image_ref",
  "actor_ref",
  "published_at",
  "evidence_ref",
  "observed_at",
  "availability",
]);
const ROOM_AVAILABILITY = new Set([
  "observed",
  "unknown",
  "unavailable",
  "not_present",
  "unmatched",
  "expired",
]);

function invalidRoom(errorCode) {
  return { status: "invalid", posts: [], error_code: errorCode };
}

function nullableId(value) {
  return value === null || (typeof value === "string" && SAFE_REF.test(value));
}

function nullableCount(value) {
  return value === null || (Number.isInteger(value) && value >= 0);
}

function nullableHttpUrl(value) {
  if (value === null) return true;
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return (
      ["http:", "https:"].includes(url.protocol) &&
      url.hostname.length > 0 &&
      url.username.length === 0 &&
      url.password.length === 0
    );
  } catch {
    return false;
  }
}

function validRoomPost(post) {
  if (
    post === null ||
    typeof post !== "object" ||
    Array.isArray(post) ||
    Object.keys(post).sort().join(",") !== [...ROOM_POST_KEYS].sort().join(",")
  ) {
    return false;
  }
  return (
    Number.isInteger(post.internal_index) &&
    post.internal_index > 0 &&
    nullableId(post.stable_room_id) &&
    nullableHttpUrl(post.room_url) &&
    nullableCount(post.likes) &&
    nullableCount(post.comments) &&
    nullableHttpUrl(post.item_url) &&
    nullableId(post.observed_shop_id) &&
    nullableId(post.observed_item_id) &&
    (post.image_ref === null || typeof post.image_ref === "string") &&
    (post.actor_ref === null || typeof post.actor_ref === "string") &&
    (post.published_at === null || validTimestamp(post.published_at)) &&
    typeof post.evidence_ref === "string" &&
    SAFE_REF.test(post.evidence_ref) &&
    validTimestamp(post.observed_at) &&
    ROOM_AVAILABILITY.has(post.availability)
  );
}

/**
 * Validate a fixture observation for one exact ROOM search. This function does
 * not open a browser or establish that a live connector is permitted.
 */
export function validateRoomSnapshot(payload, expected) {
  if (
    payload === null ||
    typeof payload !== "object" ||
    Array.isArray(payload) ||
    expected === null ||
    typeof expected !== "object"
  ) {
    return invalidRoom("response_not_object");
  }
  if (
    Object.keys(payload).sort().join(",") !==
    [...ROOM_REQUIRED_KEYS].sort().join(",")
  ) {
    return invalidRoom("response_shape_invalid");
  }
  if (
    !SAFE_REF.test(payload.attempt_id) ||
    !SAFE_REF.test(payload.run_id) ||
    typeof payload.query !== "string" ||
    payload.query.trim().length === 0 ||
    !Array.isArray(payload.parent_refs) ||
    payload.parent_refs.length === 0 ||
    !payload.parent_refs.every((ref) => typeof ref === "string" && SAFE_REF.test(ref)) ||
    new Set(payload.parent_refs).size !== payload.parent_refs.length ||
    !["combined", "parent"].includes(payload.level) ||
    payload.likes_min !== 300 ||
    payload.max_posts !== 50 ||
    payload.attempt_id !== expected.attemptId ||
    payload.run_id !== expected.runId ||
    payload.query !== expected.query ||
    JSON.stringify(payload.parent_refs) !== JSON.stringify(expected.parentRefs) ||
    payload.level !== expected.level
  ) {
    return invalidRoom("search_conditions_mismatch");
  }
  if (
    !["observed", "partial", "unavailable"].includes(payload.status) ||
    typeof payload.search_loaded !== "boolean" ||
    typeof payload.internal_order_confirmed !== "boolean" ||
    typeof payload.end_reached !== "boolean" ||
    !Array.isArray(payload.posts) ||
    payload.posts.length > 100 ||
    !payload.posts.every(validRoomPost)
  ) {
    return invalidRoom("response_schema_invalid");
  }
  if (
    payload.status === "unavailable" &&
    (payload.current_search_id !== null ||
      payload.search_loaded !== false ||
      payload.internal_order_confirmed !== false ||
      payload.fetched_at !== null ||
      payload.end_reached !== false ||
      typeof payload.error_code !== "string" ||
      payload.error_code.trim().length === 0 ||
      payload.posts.length !== 0)
  ) {
    return invalidRoom("failure_response_contains_stale_data");
  }
  if (payload.status === "unavailable") {
    return { status: "unavailable", posts: [], error_code: payload.error_code };
  }
  if (
    payload.current_search_id !== payload.attempt_id ||
    payload.search_loaded !== true ||
    payload.internal_order_confirmed !== true ||
    !validTimestamp(payload.fetched_at) ||
    (payload.status === "observed" && payload.error_code !== null) ||
    (payload.error_code !== null &&
      (typeof payload.error_code !== "string" || payload.error_code.trim().length === 0))
  ) {
    return invalidRoom("search_conditions_mismatch");
  }
  let lastIndex = 0;
  for (const post of payload.posts) {
    if (post.internal_index <= lastIndex) {
      return invalidRoom("internal_order_invalid");
    }
    lastIndex = post.internal_index;
  }
  return {
    status: payload.status,
    posts: payload.posts,
    current_search_id: payload.current_search_id,
    search_loaded: true,
    internal_order_confirmed: true,
    end_reached: payload.end_reached,
    fetched_at: payload.fetched_at,
    error_code: payload.error_code,
  };
}
