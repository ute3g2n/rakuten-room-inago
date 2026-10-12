CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    applied_at TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE settings_version (
    version TEXT PRIMARY KEY NOT NULL CHECK (length(trim(version)) > 0),
    confirmed_at TEXT,
    genres_json TEXT NOT NULL CHECK (json_valid(genres_json) AND json_type(genres_json) = 'array'),
    themes_json TEXT NOT NULL CHECK (json_valid(themes_json) AND json_type(themes_json) = 'array'),
    pattern_refs_json TEXT NOT NULL CHECK (json_valid(pattern_refs_json) AND json_type(pattern_refs_json) = 'array'),
    change_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE run (
    run_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(run_id)) > 0),
    target_date TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('setup', 'daily', 'status', 'recovery', 'config-change', 'export', 'serve', 'migrate', 'backup', 'restore-check', 'acceptance')),
    status TEXT NOT NULL CHECK (status IN ('created', 'running', 'complete', 'partial', 'failed', 'shortfall', 'missed')),
    settings_version TEXT NOT NULL REFERENCES settings_version(version),
    seed TEXT NOT NULL CHECK (length(trim(seed)) > 0),
    started_at TEXT NOT NULL,
    draft_deadline TEXT NOT NULL,
    end_deadline TEXT NOT NULL,
    attempt_used INTEGER NOT NULL CHECK (attempt_used BETWEEN 0 AND 9),
    committed_count INTEGER NOT NULL CHECK (committed_count BETWEEN 0 AND 15),
    checkpoint TEXT,
    metrics_status TEXT NOT NULL CHECK (metrics_status IN ('pending', 'observed', 'partial', 'unavailable', 'not_started')),
    finished_at TEXT
);
CREATE UNIQUE INDEX one_daily_run_per_target_date
    ON run(target_date) WHERE mode = 'daily';

CREATE TABLE search_attempt (
    attempt_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES run(run_id),
    query TEXT NOT NULL,
    parent_refs_json TEXT NOT NULL CHECK (json_valid(parent_refs_json) AND json_type(parent_refs_json) = 'array'),
    level TEXT NOT NULL CHECK (level IN ('combined', 'parent')),
    attempt_no INTEGER NOT NULL CHECK (attempt_no BETWEEN 1 AND 9),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    source_status TEXT NOT NULL CHECK (source_status IN ('observed', 'partial', 'unavailable', 'invalid')),
    source_order TEXT NOT NULL CHECK (source_order = 'internal'),
    max_posts INTEGER NOT NULL CHECK (max_posts = 50),
    retry_of TEXT REFERENCES search_attempt(attempt_id),
    error_code TEXT,
    UNIQUE(run_id, attempt_no)
);

CREATE TABLE source_post (
    source_key TEXT PRIMARY KEY NOT NULL,
    stable_room_id TEXT,
    room_url TEXT,
    attempt_id TEXT NOT NULL REFERENCES search_attempt(attempt_id),
    order_index INTEGER NOT NULL CHECK (order_index BETWEEN 1 AND 50),
    likes INTEGER CHECK (likes IS NULL OR likes >= 0),
    comments INTEGER CHECK (comments IS NULL OR comments >= 0),
    item_url TEXT,
    image_ref TEXT,
    actor_ref TEXT,
    published_at TEXT,
    evidence_ref TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    availability TEXT NOT NULL CHECK (availability IN ('observed', 'unknown', 'unavailable', 'not_present', 'unmatched', 'expired')),
    UNIQUE(attempt_id, order_index)
);

CREATE TABLE offer (
    offer_key TEXT PRIMARY KEY NOT NULL,
    shop_id TEXT NOT NULL CHECK (length(trim(shop_id)) > 0),
    item_id TEXT NOT NULL CHECK (length(trim(item_id)) > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    url TEXT NOT NULL CHECK (length(trim(url)) > 0),
    image_refs_json TEXT NOT NULL CHECK (json_valid(image_refs_json) AND json_type(image_refs_json) = 'array'),
    main_theme_id TEXT NOT NULL,
    aux_theme_ids_json TEXT NOT NULL CHECK (json_valid(aux_theme_ids_json) AND json_type(aux_theme_ids_json) = 'array'),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    facts_json TEXT NOT NULL CHECK (json_valid(facts_json) AND json_type(facts_json) = 'array'),
    UNIQUE(shop_id, item_id),
    CHECK (offer_key = shop_id || ':' || item_id)
);

CREATE TABLE candidate_decision (
    decision_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES run(run_id),
    offer_key TEXT REFERENCES offer(offer_key),
    source_key TEXT REFERENCES source_post(source_key),
    stage TEXT NOT NULL CHECK (length(trim(stage)) > 0),
    primary_reason TEXT NOT NULL CHECK (length(trim(primary_reason)) > 0),
    secondary_reasons_json TEXT NOT NULL CHECK (json_valid(secondary_reasons_json) AND json_type(secondary_reasons_json) = 'array'),
    representative_ref TEXT,
    representative_likes INTEGER CHECK (representative_likes IS NULL OR representative_likes >= 0),
    rule_version TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    CHECK (offer_key IS NOT NULL OR source_key IS NOT NULL)
);

CREATE TABLE keyword_snapshot (
    snapshot_id TEXT PRIMARY KEY NOT NULL,
    parent_word TEXT NOT NULL,
    source TEXT NOT NULL,
    geo TEXT NOT NULL CHECK (geo = 'JP'),
    range TEXT NOT NULL CHECK (range = '90d'),
    search_type TEXT NOT NULL CHECK (search_type = 'web'),
    list_type TEXT NOT NULL CHECK (list_type = 'Top'),
    words_json TEXT NOT NULL CHECK (json_valid(words_json) AND json_type(words_json) = 'array'),
    fetched_at TEXT,
    availability TEXT NOT NULL CHECK (availability IN ('observed', 'unknown', 'unavailable', 'not_present', 'unmatched', 'expired')),
    filter_decisions_json TEXT NOT NULL CHECK (json_valid(filter_decisions_json) AND json_type(filter_decisions_json) = 'array')
);

CREATE TABLE theme_selection (
    selection_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES run(run_id),
    theme_id TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK (event_kind IN ('initial', 'additional')),
    count_event_id TEXT NOT NULL UNIQUE,
    seed TEXT NOT NULL,
    result_word_refs_json TEXT NOT NULL CHECK (json_valid(result_word_refs_json) AND json_type(result_word_refs_json) = 'array'),
    selected_at TEXT NOT NULL
);

CREATE TABLE artifact (
    artifact_ref TEXT PRIMARY KEY NOT NULL,
    path TEXT NOT NULL,
    hash TEXT NOT NULL CHECK (length(trim(hash)) > 0),
    created_at TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('external_analysis_result', 'daily', 'metrics', 'generation')),
    retention_expires_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('available', 'expired', 'missing'))
);

CREATE TABLE draft (
    draft_id TEXT PRIMARY KEY NOT NULL,
    offer_key TEXT NOT NULL REFERENCES offer(offer_key),
    run_id TEXT NOT NULL REFERENCES run(run_id),
    version INTEGER NOT NULL CHECK (version >= 1),
    body TEXT NOT NULL CHECK (length(trim(body)) > 0),
    generated_at TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    pattern_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('committed', 'human_edited', 'posted', 'skipped', 'do_not_post_already_posted')),
    artifact_ref TEXT REFERENCES artifact(artifact_ref),
    pattern_id TEXT NOT NULL CHECK (pattern_id IN ('P01', 'P02', 'P03', 'P04', 'P05')),
    evidence_refs_json TEXT NOT NULL CHECK (json_valid(evidence_refs_json) AND json_type(evidence_refs_json) = 'array'),
    UNIQUE(draft_id, version),
    UNIQUE(offer_key, draft_id),
    UNIQUE(offer_key, draft_id, version),
    UNIQUE(offer_key, version),
    UNIQUE(run_id, offer_key)
);

CREATE TABLE generation_attempt (
    generation_attempt_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES run(run_id),
    offer_key TEXT NOT NULL REFERENCES offer(offer_key),
    draft_id TEXT REFERENCES draft(draft_id),
    attempt_no INTEGER NOT NULL CHECK (attempt_no BETWEEN 1 AND 3),
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'empty', 'error', 'in_progress')),
    artifact_ref TEXT,
    error_code TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE(run_id, offer_key, attempt_no)
);

CREATE TABLE reservation (
    reservation_id TEXT PRIMARY KEY NOT NULL,
    offer_key TEXT NOT NULL REFERENCES offer(offer_key),
    draft_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'released')),
    created_at TEXT NOT NULL,
    released_at TEXT,
    release_reason TEXT,
    CHECK (release_reason IS NULL OR release_reason IN ('posted', 'skipped', 'valid_prior_post')),
    CHECK ((status = 'active' AND released_at IS NULL AND release_reason IS NULL)
        OR (status = 'released' AND released_at IS NOT NULL AND release_reason IS NOT NULL)),
    FOREIGN KEY (offer_key, draft_id) REFERENCES draft(offer_key, draft_id)
);
CREATE UNIQUE INDEX one_active_reservation_per_offer
    ON reservation(offer_key) WHERE status = 'active';

CREATE TABLE publication_event (
    event_id TEXT PRIMARY KEY NOT NULL,
    offer_key TEXT NOT NULL REFERENCES offer(offer_key),
    draft_id TEXT,
    draft_version INTEGER,
    reported_at TEXT NOT NULL,
    actual_posted_at TEXT,
    room_url TEXT,
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('human_check', 'legacy_evidence', 'human_statement')),
    valid INTEGER NOT NULL CHECK (valid IN (0, 1)),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    CHECK ((draft_id IS NULL AND draft_version IS NULL)
        OR (draft_id IS NOT NULL AND draft_version IS NOT NULL AND draft_version >= 1)),
    FOREIGN KEY (offer_key, draft_id, draft_version) REFERENCES draft(offer_key, draft_id, version)
);

CREATE TABLE human_action (
    action_id TEXT PRIMARY KEY NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('posted', 'skipped', 'correction', 'edit')),
    offer_key TEXT NOT NULL REFERENCES offer(offer_key),
    draft_id TEXT,
    draft_version INTEGER NOT NULL CHECK (draft_version >= 1),
    prior_revision INTEGER NOT NULL CHECK (prior_revision >= 0),
    receipt_revision INTEGER NOT NULL CHECK (receipt_revision >= 1),
    reason TEXT,
    reported_at TEXT NOT NULL,
    old_event_ref TEXT REFERENCES publication_event(event_id),
    new_body TEXT,
    apply_status TEXT NOT NULL CHECK (apply_status IN ('pending', 'applied', 'rejected')),
    applied_at TEXT,
    applied_run_id TEXT REFERENCES run(run_id),
    apply_error TEXT,
    FOREIGN KEY (draft_id, draft_version) REFERENCES draft(draft_id, version),
    CHECK (action != 'correction' OR (reason IS NOT NULL AND length(trim(reason)) > 0)),
    CHECK (apply_status != 'applied' OR (applied_at IS NOT NULL AND applied_run_id IS NOT NULL)),
    FOREIGN KEY (offer_key, draft_id, draft_version) REFERENCES draft(offer_key, draft_id, version)
);

CREATE TABLE exclusion_key (
    offer_key TEXT PRIMARY KEY NOT NULL REFERENCES offer(offer_key),
    reason TEXT NOT NULL CHECK (reason = 'actual_posted'),
    evidence_refs_json TEXT NOT NULL CHECK (json_valid(evidence_refs_json) AND json_type(evidence_refs_json) = 'array' AND json_array_length(evidence_refs_json) > 0),
    effective INTEGER NOT NULL CHECK (effective IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE metric_observation (
    observation_id TEXT PRIMARY KEY NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('day_aggregate', 'room_post', 'order_item', 'entry_post')),
    ref TEXT NOT NULL,
    metric TEXT NOT NULL CHECK (metric IN ('clicks', 'likes', 'sales_count', 'sales_yen', 'commission_unconfirmed_yen')),
    value_json TEXT CHECK (value_json IS NULL OR json_valid(value_json)),
    unit TEXT NOT NULL CHECK (unit IN ('count', 'JPY', 'seconds', 'bytes', 'ratio', 'text')),
    observed_at TEXT NOT NULL,
    window TEXT,
    availability TEXT NOT NULL CHECK (availability IN ('observed', 'unknown', 'unavailable', 'not_present', 'unmatched', 'expired')),
    source_ref TEXT,
    provisional INTEGER CHECK (provisional IS NULL OR provisional IN (0, 1)),
    CHECK (value_json IS NULL OR json_type(value_json) IN ('integer', 'real', 'text', 'null')),
    CHECK (value_json IS NULL OR json_type(value_json) NOT IN ('integer', 'real') OR CAST(value_json AS REAL) >= 0)
);

CREATE TABLE revenue_order (
    order_ref TEXT PRIMARY KEY NOT NULL,
    is_temporary_ref INTEGER NOT NULL CHECK (is_temporary_ref IN (0, 1)),
    occurred_at TEXT,
    shop_name TEXT,
    product_name TEXT,
    item_code TEXT,
    product_url TEXT,
    sales_yen REAL CHECK (sales_yen IS NULL OR (typeof(sales_yen) IN ('integer', 'real') AND sales_yen >= 0)),
    commission_yen REAL CHECK (commission_yen IS NULL OR (typeof(commission_yen) IN ('integer', 'real') AND commission_yen >= 0)),
    order_status TEXT,
    link_type TEXT,
    source_ref TEXT NOT NULL REFERENCES artifact(artifact_ref),
    match_kind TEXT NOT NULL CHECK (match_kind IN ('unmatched', 'purchased_item_match', 'entry_post_confirmed')),
    matched_offer_key TEXT REFERENCES offer(offer_key),
    entry_post_ref TEXT REFERENCES publication_event(event_id),
    CHECK (match_kind != 'entry_post_confirmed' OR entry_post_ref IS NOT NULL)
);

CREATE TABLE recovery (
    operation_id TEXT PRIMARY KEY NOT NULL,
    type TEXT NOT NULL CHECK (type = 'recovery'),
    run_ref TEXT REFERENCES run(run_id),
    checkpoint TEXT,
    cause TEXT,
    owner TEXT,
    status TEXT NOT NULL,
    backup_path TEXT,
    restored_at TEXT,
    validation_json TEXT NOT NULL CHECK (json_valid(validation_json) AND json_type(validation_json) = 'object'),
    observed_at TEXT NOT NULL
);

CREATE TABLE backup (
    operation_id TEXT PRIMARY KEY NOT NULL,
    type TEXT NOT NULL CHECK (type = 'backup'),
    run_ref TEXT REFERENCES run(run_id),
    checkpoint TEXT,
    cause TEXT,
    owner TEXT,
    status TEXT NOT NULL,
    backup_path TEXT,
    restored_at TEXT,
    validation_json TEXT NOT NULL CHECK (json_valid(validation_json) AND json_type(validation_json) = 'object'),
    observed_at TEXT NOT NULL
);

CREATE TABLE restore_check (
    operation_id TEXT PRIMARY KEY NOT NULL,
    type TEXT NOT NULL CHECK (type = 'restore_check'),
    run_ref TEXT REFERENCES run(run_id),
    checkpoint TEXT,
    cause TEXT,
    owner TEXT,
    status TEXT NOT NULL,
    backup_path TEXT,
    restored_at TEXT,
    validation_json TEXT NOT NULL CHECK (json_valid(validation_json) AND json_type(validation_json) = 'object'),
    observed_at TEXT NOT NULL
);

CREATE TABLE ai_job (
    job_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES run(run_id),
    protocol_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    task_id TEXT NOT NULL CHECK (task_id IN ('T02', 'T03', 'T04', 'T05', 'T06', 'T07', 'T08', 'T09', 'T10', 'T11')),
    agent_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    target_date TEXT NOT NULL,
    settings_version TEXT NOT NULL REFERENCES settings_version(version),
    input_refs_json TEXT NOT NULL CHECK (json_valid(input_refs_json) AND json_type(input_refs_json) = 'array'),
    evidence_refs_json TEXT NOT NULL CHECK (json_valid(evidence_refs_json) AND json_type(evidence_refs_json) = 'array'),
    input_hash TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    resolved_model TEXT NOT NULL,
    reasoning_setting TEXT NOT NULL,
    auth_mode TEXT NOT NULL,
    result_status TEXT NOT NULL CHECK (result_status IN ('success', 'empty', 'error', 'timed_out', 'cancelled', 'late_result')),
    payload_json TEXT CHECK (payload_json IS NULL OR (json_valid(payload_json) AND json_type(payload_json) IN ('object', 'null'))),
    error_json TEXT CHECK (error_json IS NULL OR (json_valid(error_json) AND json_type(error_json) IN ('object', 'null'))),
    artifact_ref TEXT,
    accepted_at TEXT,
    adoption_status TEXT NOT NULL CHECK (adoption_status IN ('pending', 'accepted', 'rejected_contract', 'expired', 'conflict')),
    CHECK (adoption_status != 'accepted' OR accepted_at IS NOT NULL)
);
