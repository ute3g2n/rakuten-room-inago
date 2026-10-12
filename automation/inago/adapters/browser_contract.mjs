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
  if (keys.join(",") !== [...REQUIRED_KEYS].sort().join(",")) {
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
    Object.entries(QUERY).some(([key, value]) => payload[key] !== value)
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
