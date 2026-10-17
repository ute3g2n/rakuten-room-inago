CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    applied_at TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE daily_check (
    check_id TEXT PRIMARY KEY NOT NULL,
    source_launch_id TEXT NOT NULL UNIQUE,
    source_run_id TEXT,
    target_date TEXT NOT NULL,
    check_status TEXT NOT NULL CHECK (check_status IN (
        'normal', 'normal_shortfall', 'abnormal', 'unknown', 'incomplete'
    )),
    reason_code TEXT,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE incident (
    incident_id TEXT PRIMARY KEY NOT NULL,
    dedupe_key TEXT NOT NULL,
    component TEXT NOT NULL,
    event_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'open', 'in_progress', 'blocked', 'resolved', 'closed'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_incident_per_dedupe_key
    ON incident(dedupe_key) WHERE status IN ('open', 'in_progress', 'blocked');

CREATE TABLE incident_run (
    incident_id TEXT NOT NULL REFERENCES incident(incident_id),
    check_id TEXT NOT NULL REFERENCES daily_check(check_id),
    source_launch_id TEXT NOT NULL UNIQUE,
    source_run_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (incident_id, check_id)
);

CREATE TABLE release (
    release_id TEXT PRIMARY KEY NOT NULL,
    author_id TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    result_commit TEXT NOT NULL,
    result_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    case_manifest_sha256 TEXT NOT NULL CHECK (length(case_manifest_sha256) = 64),
    state TEXT NOT NULL CHECK (state IN ('frozen', 'withdrawn')),
    created_at TEXT NOT NULL
);

CREATE TABLE release_required_test (
    release_id TEXT NOT NULL REFERENCES release(release_id),
    case_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    PRIMARY KEY (release_id, case_id),
    UNIQUE (release_id, sequence)
);

CREATE TABLE test_ledger (
    test_result_id TEXT PRIMARY KEY NOT NULL,
    release_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    result_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'PASS', 'FAIL', 'SKIP', 'XFAIL', 'ERROR', 'NOT_RUN', 'UNVERIFIED'
    )),
    evidence_ref TEXT,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (release_id, case_id)
        REFERENCES release_required_test(release_id, case_id),
    UNIQUE (release_id, case_id, attempt)
);

CREATE TABLE review_result (
    review_id TEXT PRIMARY KEY NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('release', 'step_attempt')),
    scope_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    reviewed_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'approved', 'changes_requested', 'rejected', 'unavailable'
    )),
    reviewed_at TEXT NOT NULL
);
CREATE INDEX review_result_scope_version
    ON review_result(scope_type, scope_id, reviewed_at);

CREATE TABLE deployment (
    deployment_id TEXT PRIMARY KEY NOT NULL,
    release_id TEXT NOT NULL REFERENCES release(release_id),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    result_commit TEXT NOT NULL,
    config_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'deployed', 'failed', 'blocked', 'rolled_back'
    )),
    deployed_at TEXT NOT NULL,
    UNIQUE (release_id, attempt)
);

CREATE TABLE release_gate (
    release_id TEXT PRIMARY KEY NOT NULL REFERENCES release(release_id),
    status TEXT NOT NULL CHECK (status IN ('blocked', 'ready')),
    reasons_json TEXT NOT NULL CHECK (
        json_valid(reasons_json) AND json_type(reasons_json) = 'array'
    ),
    evaluated_at TEXT NOT NULL,
    result_commit TEXT NOT NULL
);

CREATE TABLE maintenance_job (
    job_id TEXT PRIMARY KEY NOT NULL,
    incident_id TEXT NOT NULL REFERENCES incident(incident_id),
    release_id TEXT NOT NULL REFERENCES release(release_id),
    source_run_id TEXT,
    result_version TEXT NOT NULL,
    lease_key TEXT,
    lease_generation INTEGER CHECK (lease_generation IS NULL OR lease_generation > 0),
    resume_count INTEGER NOT NULL DEFAULT 0 CHECK (resume_count >= 0),
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'blocked', 'complete', 'failed', 'cancelled'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    terminal_reason_code TEXT,
    UNIQUE (incident_id, release_id),
    CHECK ((lease_key IS NULL) = (lease_generation IS NULL))
);
CREATE UNIQUE INDEX one_active_maintenance_job
    ON maintenance_job((1)) WHERE status IN ('queued', 'running');

CREATE TABLE lease (
    lease_key TEXT PRIMARY KEY NOT NULL,
    owner_id TEXT NOT NULL,
    owner_pid INTEGER NOT NULL CHECK (owner_pid > 0),
    owner_start_time TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    state TEXT NOT NULL CHECK (state IN ('held', 'released')),
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE step_attempt (
    attempt_id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES maintenance_job(job_id),
    step_key TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    source_run_id TEXT,
    lease_key TEXT NOT NULL,
    lease_generation INTEGER NOT NULL CHECK (lease_generation > 0),
    status TEXT NOT NULL CHECK (status IN (
        'running', 'interrupted', 'complete', 'failed', 'blocked',
        'cancelled', 'handoff_accepted'
    )),
    result_version TEXT,
    output_path TEXT,
    output_sha256 TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE (job_id, step_key, attempt_no),
    UNIQUE (job_id, sequence, attempt_no),
    CHECK ((output_path IS NULL) = (output_sha256 IS NULL))
);

CREATE TABLE handoff_artifact (
    attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES step_attempt(attempt_id),
    source_run_id TEXT,
    result_version TEXT NOT NULL,
    output_path TEXT NOT NULL,
    output_sha256 TEXT NOT NULL CHECK (length(output_sha256) = 64),
    accepted_at TEXT NOT NULL
);
