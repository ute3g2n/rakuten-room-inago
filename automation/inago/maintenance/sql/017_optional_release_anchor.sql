PRAGMA defer_foreign_keys = ON;

CREATE TABLE maintenance_job_new (
    job_id TEXT PRIMARY KEY NOT NULL,
    incident_id TEXT NOT NULL REFERENCES incident(incident_id),
    release_id TEXT REFERENCES release(release_id),
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

-- NULL release IDs can represent more than one independently registered job.
CREATE UNIQUE INDEX maintenance_job_incident_release_unique
    ON maintenance_job(incident_id, release_id)
    WHERE release_id IS NOT NULL;
