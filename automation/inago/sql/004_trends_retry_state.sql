-- Durable per-theme Trends attempt budget. Existing runs and snapshots stay intact.
CREATE TABLE trends_acquisition (
    acquisition_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(acquisition_id)) > 0),
    run_id TEXT NOT NULL REFERENCES run(run_id),
    settings_version TEXT NOT NULL REFERENCES settings_version(version),
    event_kind TEXT NOT NULL CHECK (event_kind IN ('initial', 'additional')),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    theme_id TEXT NOT NULL CHECK (length(trim(theme_id)) > 0),
    query_word TEXT NOT NULL CHECK (length(trim(query_word)) > 0),
    seed TEXT NOT NULL CHECK (length(trim(seed)) > 0),
    status TEXT NOT NULL CHECK (status IN (
        'collecting', 'response_ready', 'observed', 'normal_zero',
        'exhausted', 'deadline', 'processing_error'
    )),
    attempts_used INTEGER NOT NULL CHECK (attempts_used BETWEEN 0 AND 5),
    next_retry_at TEXT,
    snapshot_id TEXT REFERENCES keyword_snapshot(snapshot_id),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (run_id, settings_version, theme_id),
    UNIQUE (run_id, event_kind, ordinal),
    CHECK ((status IN ('observed','normal_zero') AND snapshot_id IS NOT NULL)
        OR (status NOT IN ('observed','normal_zero') AND snapshot_id IS NULL))
);

CREATE TABLE trends_acquisition_attempt (
    request_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(request_id)) > 0),
    acquisition_id TEXT NOT NULL REFERENCES trends_acquisition(acquisition_id),
    attempt_no INTEGER NOT NULL CHECK (attempt_no BETWEEN 1 AND 5),
    retry_of TEXT REFERENCES trends_acquisition_attempt(request_id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'started', 'interrupted', 'unavailable', 'response_ready',
        'processing_error', 'deadline'
    )),
    error_code TEXT,
    evidence_ref TEXT,
    next_retry_at TEXT,
    response_json TEXT CHECK (response_json IS NULL OR
        (json_valid(response_json) AND json_type(response_json) = 'object')),
    UNIQUE (acquisition_id, attempt_no),
    CHECK ((attempt_no = 1 AND retry_of IS NULL) OR
           (attempt_no > 1 AND retry_of IS NOT NULL)),
    CHECK ((status = 'started' AND ended_at IS NULL) OR
           (status != 'started' AND ended_at IS NOT NULL))
);
