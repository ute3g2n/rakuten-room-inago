CREATE TABLE job_input_manifest (
    job_id TEXT PRIMARY KEY NOT NULL REFERENCES maintenance_job(job_id),
    incident_id TEXT NOT NULL REFERENCES incident(incident_id),
    source_launch_id TEXT NOT NULL,
    source_run_id TEXT,
    target_date TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    created_at TEXT NOT NULL
);

CREATE TRIGGER job_input_manifest_immutable_update
BEFORE UPDATE ON job_input_manifest
BEGIN
    SELECT RAISE(ABORT, 'job_input_manifest_immutable');
END;

CREATE TRIGGER job_input_manifest_immutable_delete
BEFORE DELETE ON job_input_manifest
BEGIN
    SELECT RAISE(ABORT, 'job_input_manifest_immutable');
END;

CREATE TABLE maintenance_ai_session (
    job_id TEXT NOT NULL REFERENCES maintenance_job(job_id),
    session_key TEXT NOT NULL,
    parent_key TEXT NOT NULL CHECK (parent_key IN ('A', 'B')),
    role_name TEXT NOT NULL,
    thread_id TEXT NOT NULL UNIQUE,
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    requested_model TEXT NOT NULL,
    requested_effort TEXT NOT NULL,
    requested_service_tier TEXT NOT NULL CHECK (
        requested_service_tier IN ('default', 'priority')
    ),
    requested_fast_mode INTEGER NOT NULL CHECK (requested_fast_mode IN (0, 1)),
    role_config_sha256 TEXT NOT NULL CHECK (length(role_config_sha256) = 64),
    sandbox_json TEXT NOT NULL CHECK (json_valid(sandbox_json)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, session_key)
);

CREATE TRIGGER maintenance_ai_session_immutable_update
BEFORE UPDATE ON maintenance_ai_session
BEGIN
    SELECT RAISE(ABORT, 'maintenance_ai_session_immutable');
END;

CREATE TRIGGER maintenance_ai_session_immutable_delete
BEFORE DELETE ON maintenance_ai_session
BEGIN
    SELECT RAISE(ABORT, 'maintenance_ai_session_immutable');
END;

CREATE TABLE maintenance_ai_observation (
    observation_id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL,
    session_key TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('configured_unverified', 'verified', 'blocked')
    ),
    observed_model TEXT,
    observed_effort TEXT,
    observed_service_tier TEXT,
    reason_code TEXT,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (job_id, session_key)
        REFERENCES maintenance_ai_session(job_id, session_key)
);

CREATE TRIGGER maintenance_ai_observation_immutable_update
BEFORE UPDATE ON maintenance_ai_observation
BEGIN
    SELECT RAISE(ABORT, 'maintenance_ai_observation_immutable');
END;

CREATE TRIGGER maintenance_ai_observation_immutable_delete
BEFORE DELETE ON maintenance_ai_observation
BEGIN
    SELECT RAISE(ABORT, 'maintenance_ai_observation_immutable');
END;
