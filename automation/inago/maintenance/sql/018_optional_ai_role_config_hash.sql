PRAGMA defer_foreign_keys = ON;

DROP TRIGGER IF EXISTS maintenance_ai_session_immutable_update;
DROP TRIGGER IF EXISTS maintenance_ai_session_immutable_delete;

CREATE TABLE maintenance_ai_session_new (
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
    role_config_sha256 TEXT,
    sandbox_json TEXT NOT NULL CHECK (json_valid(sandbox_json)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, session_key)
);

INSERT INTO maintenance_ai_session_new (
    job_id, session_key, parent_key, role_name, thread_id, manifest_sha256,
    requested_model, requested_effort, requested_service_tier,
    requested_fast_mode, role_config_sha256, sandbox_json, created_at
)
SELECT
    job_id, session_key, parent_key, role_name, thread_id, manifest_sha256,
    requested_model, requested_effort, requested_service_tier,
    requested_fast_mode, role_config_sha256, sandbox_json, created_at
FROM maintenance_ai_session;

DROP TABLE maintenance_ai_session;
ALTER TABLE maintenance_ai_session_new RENAME TO maintenance_ai_session;

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
