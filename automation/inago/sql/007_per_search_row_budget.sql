-- Preserve existing search rows and references; only the per-request budget constraint changes.
CREATE TABLE search_attempt_v7 (
    attempt_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES run(run_id),
    query TEXT NOT NULL,
    parent_refs_json TEXT NOT NULL CHECK (json_valid(parent_refs_json) AND json_type(parent_refs_json) = 'array'),
    level TEXT NOT NULL CHECK (level IN ('combined', 'parent', 'related', 'theme_fallback')),
    attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    source_status TEXT NOT NULL CHECK (source_status IN ('observed', 'partial', 'unavailable', 'invalid')),
    source_order TEXT NOT NULL CHECK (source_order = 'internal'),
    max_posts INTEGER NOT NULL CHECK (max_posts BETWEEN 1 AND 50),
    retry_of TEXT REFERENCES search_attempt(attempt_id),
    error_code TEXT,
    query_kind TEXT NOT NULL DEFAULT 'legacy'
        CHECK (query_kind IN ('legacy', 'related', 'theme_fallback')),
    keyword_snapshot_ref TEXT REFERENCES keyword_snapshot(snapshot_id),
    selected_word_ref TEXT,
    observed_posts INTEGER NOT NULL DEFAULT 0 CHECK (observed_posts BETWEEN 0 AND 50),
    loaded_posts INTEGER CHECK (loaded_posts IS NULL OR loaded_posts >= observed_posts),
    end_reason TEXT CHECK (end_reason IS NULL OR end_reason IN ('limit_reached', 'exhausted', 'deadline', 'error', 'invalid')),
    condition_evidence_ref TEXT,
    CHECK ((query_kind = 'legacy' AND level IN ('combined', 'parent'))
        OR (query_kind = 'related' AND level = 'related'
            AND length(trim(keyword_snapshot_ref)) > 0
            AND keyword_snapshot_ref IS NOT NULL
            AND length(trim(selected_word_ref)) > 0 AND selected_word_ref IS NOT NULL)
        OR (query_kind = 'theme_fallback' AND level = 'theme_fallback'
            AND keyword_snapshot_ref IS NULL AND selected_word_ref IS NULL)),
    UNIQUE(run_id, attempt_no)
);
INSERT INTO search_attempt_v7 (attempt_id, run_id, query, parent_refs_json, level, attempt_no, started_at, ended_at, source_status, source_order, max_posts, retry_of, error_code, query_kind, keyword_snapshot_ref, selected_word_ref, observed_posts, loaded_posts, end_reason, condition_evidence_ref)
    SELECT attempt_id, run_id, query, parent_refs_json, level, attempt_no, started_at, ended_at, source_status, source_order, max_posts, retry_of, error_code, query_kind, keyword_snapshot_ref, selected_word_ref, observed_posts, loaded_posts, end_reason, condition_evidence_ref FROM search_attempt;
DROP TABLE search_attempt;
ALTER TABLE search_attempt_v7 RENAME TO search_attempt;

