-- Q1.4 forward migration. The repository owns BEGIN/COMMIT and FK restoration.
-- Existing rows keep their original values; legacy query/range labels are history.

CREATE TABLE run_v3 (
    run_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(run_id)) > 0),
    target_date TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('setup', 'daily', 'status', 'recovery', 'config-change', 'export', 'serve', 'migrate', 'backup', 'restore-check', 'acceptance')),
    status TEXT NOT NULL CHECK (status IN ('created', 'running', 'complete', 'partial', 'failed', 'shortfall', 'missed')),
    settings_version TEXT NOT NULL REFERENCES settings_version(version),
    seed TEXT NOT NULL CHECK (length(trim(seed)) > 0),
    started_at TEXT NOT NULL,
    draft_deadline TEXT NOT NULL,
    end_deadline TEXT NOT NULL,
    attempt_used INTEGER NOT NULL CHECK (attempt_used >= 0),
    committed_count INTEGER NOT NULL CHECK (committed_count BETWEEN 0 AND 15),
    checkpoint TEXT,
    metrics_status TEXT NOT NULL CHECK (metrics_status IN ('pending', 'observed', 'partial', 'unavailable', 'not_started')),
    finished_at TEXT
);
INSERT INTO run_v3 (run_id, target_date, mode, status, settings_version, seed, started_at, draft_deadline, end_deadline, attempt_used, committed_count, checkpoint, metrics_status, finished_at)
    SELECT run_id, target_date, mode, status, settings_version, seed, started_at, draft_deadline, end_deadline, attempt_used, committed_count, checkpoint, metrics_status, finished_at FROM run;
DROP TABLE run;
ALTER TABLE run_v3 RENAME TO run;

CREATE TABLE search_attempt_v3 (
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
    max_posts INTEGER NOT NULL CHECK (max_posts = 50),
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
INSERT INTO search_attempt_v3 (attempt_id, run_id, query, parent_refs_json, level, attempt_no, started_at, ended_at, source_status, source_order, max_posts, retry_of, error_code)
    SELECT attempt_id, run_id, query, parent_refs_json, level, attempt_no, started_at, ended_at, source_status, source_order, max_posts, retry_of, error_code FROM search_attempt;
DROP TABLE search_attempt;
ALTER TABLE search_attempt_v3 RENAME TO search_attempt;

CREATE TABLE keyword_snapshot_v3 (
    snapshot_id TEXT PRIMARY KEY NOT NULL,
    parent_word TEXT NOT NULL,
    source TEXT NOT NULL,
    geo TEXT NOT NULL CHECK (geo = 'JP'),
    range TEXT NOT NULL CHECK (range IN ('90d', '3m')),
    search_type TEXT NOT NULL CHECK (search_type = 'web'),
    list_type TEXT NOT NULL CHECK (list_type = 'Top'),
    words_json TEXT NOT NULL CHECK (json_valid(words_json) AND json_type(words_json) = 'array'),
    fetched_at TEXT,
    availability TEXT NOT NULL CHECK (availability IN ('observed', 'unknown', 'unavailable', 'not_present', 'unmatched', 'expired')),
    filter_decisions_json TEXT NOT NULL CHECK (json_valid(filter_decisions_json) AND json_type(filter_decisions_json) = 'array'),
    ui_period_label TEXT,
    CHECK (range = '90d' OR availability != 'observed' OR
        (ui_period_label IS NOT NULL AND length(trim(ui_period_label)) > 0))
);
INSERT INTO keyword_snapshot_v3 (snapshot_id, parent_word, source, geo, range, search_type, list_type, words_json, fetched_at, availability, filter_decisions_json)
    SELECT snapshot_id, parent_word, source, geo, range, search_type, list_type, words_json, fetched_at, availability, filter_decisions_json FROM keyword_snapshot;
DROP TABLE keyword_snapshot;
ALTER TABLE keyword_snapshot_v3 RENAME TO keyword_snapshot;

CREATE UNIQUE INDEX one_daily_run_per_target_date
    ON run(target_date) WHERE mode = 'daily';
