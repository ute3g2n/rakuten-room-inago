PRAGMA defer_foreign_keys = ON;

DROP TRIGGER IF EXISTS maintenance_job_resume_event_insert_guard;
DROP TRIGGER IF EXISTS maintenance_job_resume_transition_guard;
DROP TRIGGER IF EXISTS repair_host_review_execution_bind;
DROP TRIGGER IF EXISTS repair_review_receipt_attempt_bind;

CREATE TABLE maintenance_job_new (
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
    CHECK ((lease_key IS NULL) = (lease_generation IS NULL))
);

INSERT INTO maintenance_job_new (
    job_id, incident_id, release_id, source_run_id, result_version,
    lease_key, lease_generation, resume_count, status, created_at,
    updated_at, finished_at, terminal_reason_code
)
SELECT
    job_id, incident_id, release_id, source_run_id, result_version,
    lease_key, lease_generation, resume_count, status, created_at,
    updated_at, finished_at, terminal_reason_code
FROM maintenance_job;

DROP TABLE maintenance_job;
ALTER TABLE maintenance_job_new RENAME TO maintenance_job;
