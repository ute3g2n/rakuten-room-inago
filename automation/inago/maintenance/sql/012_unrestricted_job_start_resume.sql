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

CREATE TRIGGER repair_host_review_execution_bind
BEFORE INSERT ON repair_host_review_execution
WHEN NOT EXISTS (
    SELECT 1
    FROM step_attempt attempt
    JOIN maintenance_job job ON job.job_id = attempt.job_id
    JOIN release parent ON parent.release_id = job.release_id
    JOIN maintenance_ai_session session
      ON session.job_id = NEW.job_id
     AND session.session_key = NEW.reviewer_session_key
     AND session.thread_id = NEW.reviewer_id
    JOIN maintenance_ai_observation observation
      ON observation.job_id = session.job_id
     AND observation.session_key = session.session_key
     AND observation.thread_id = session.thread_id
     AND observation.status = 'verified'
    WHERE attempt.attempt_id = NEW.attempt_id
      AND attempt.job_id = NEW.job_id
      AND attempt.step_key = 'maintenance_repair'
      AND attempt.sequence = 3
      AND attempt.status = 'running'
      AND parent.author_id = NEW.author_id
      AND NEW.reviewer_id <> NEW.author_id
      AND session.parent_key = 'B'
      AND session.role_name = 'common-reviewer'
      AND session.sandbox_json = '{"networkAccess":false,"type":"readOnly"}'
)
BEGIN
    SELECT RAISE(ABORT, 'repair_host_review_execution_binding_invalid');
END;

CREATE TRIGGER repair_review_receipt_attempt_bind
BEFORE INSERT ON repair_review_receipt
WHEN NOT EXISTS (
    SELECT 1
    FROM step_attempt attempt
    JOIN maintenance_job job ON job.job_id = attempt.job_id
    JOIN release parent ON parent.release_id = job.release_id
    WHERE attempt.attempt_id = NEW.attempt_id
      AND attempt.job_id = NEW.job_id
      AND attempt.step_key = 'maintenance_repair'
      AND attempt.sequence = 3
      AND attempt.status = 'running'
      AND parent.author_id = NEW.author_id
      AND NEW.reviewer_id <> NEW.author_id
      AND EXISTS (
          SELECT 1 FROM repair_host_review_execution execution
          WHERE execution.host_receipt_id = NEW.host_receipt_id
            AND execution.job_id = NEW.job_id
            AND execution.attempt_id = NEW.attempt_id
            AND execution.result_version = NEW.result_version
            AND execution.author_id = NEW.author_id
            AND execution.reviewer_id = NEW.reviewer_id
            AND execution.reviewed_output_sha256 = NEW.reviewed_output_sha256
            AND execution.code_sha256 = NEW.code_sha256
            AND execution.patch_sha256 = NEW.patch_sha256
            AND execution.test_ledger_sha256 = NEW.test_ledger_sha256
            AND execution.approved = 1
            AND execution.unresolved_critical_findings_json = '[]'
      )
)
BEGIN
    SELECT RAISE(ABORT, 'repair_review_receipt_attempt_mismatch');
END;
