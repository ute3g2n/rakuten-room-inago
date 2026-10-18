CREATE TABLE maintenance_job_resume_event (
    resume_event_id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES maintenance_job(job_id),
    source_launch_id TEXT NOT NULL,
    resume_count INTEGER NOT NULL CHECK (resume_count > 0),
    reason_code TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    resumed_at TEXT NOT NULL,
    UNIQUE (job_id, resume_count)
);

CREATE INDEX maintenance_job_resume_history_idx
ON maintenance_job_resume_event(job_id, resume_count);

CREATE TRIGGER maintenance_job_resume_event_insert_guard
BEFORE INSERT ON maintenance_job_resume_event
WHEN NOT EXISTS (
    SELECT 1
    FROM maintenance_job AS j
    JOIN incident AS i ON i.incident_id = j.incident_id
    JOIN incident_run AS ir ON ir.incident_id = i.incident_id
    JOIN daily_check AS dc ON dc.check_id = ir.check_id
    JOIN job_input_manifest AS m ON m.job_id = j.job_id
    JOIN lease AS l ON l.lease_key = 'maintenance:worker'
    WHERE j.job_id = NEW.job_id
      AND j.status = 'blocked'
      AND j.source_run_id IS NULL
      AND j.terminal_reason_code = NEW.reason_code
      AND NEW.resume_count = j.resume_count + 1
      AND j.lease_key = 'maintenance:worker'
      AND j.lease_generation IS NOT NULL
      AND NEW.generation > j.lease_generation
      AND NEW.source_launch_id GLOB 'historical-*'
      AND ir.source_launch_id = NEW.source_launch_id
      AND ir.source_run_id IS NULL
      AND dc.source_launch_id = NEW.source_launch_id
      AND dc.source_run_id IS NULL
      AND dc.check_status = 'abnormal'
      AND dc.reason_code = 'historical_daily_failure_observed'
      AND i.component = 'daily_pipeline'
      AND i.event_code = 'historical_daily_failure'
      AND m.incident_id = i.incident_id
      AND m.source_launch_id = NEW.source_launch_id
      AND m.source_run_id IS NULL
      AND m.target_date = dc.target_date
      AND l.state = 'held'
      AND l.owner_id = NEW.owner_id
      AND l.generation = NEW.generation
      AND l.expires_at > NEW.resumed_at
)
BEGIN
    SELECT RAISE(ABORT, 'maintenance_job_resume_event_invalid');
END;

CREATE TRIGGER maintenance_job_resume_event_no_update
BEFORE UPDATE ON maintenance_job_resume_event
BEGIN
    SELECT RAISE(ABORT, 'maintenance_job_resume_event_immutable');
END;

CREATE TRIGGER maintenance_job_resume_event_no_delete
BEFORE DELETE ON maintenance_job_resume_event
BEGIN
    SELECT RAISE(ABORT, 'maintenance_job_resume_event_immutable');
END;

CREATE TRIGGER maintenance_job_resume_transition_guard
BEFORE UPDATE OF status ON maintenance_job
WHEN OLD.status = 'blocked' AND NEW.status = 'queued'
 AND NOT EXISTS (
    SELECT 1
    FROM maintenance_job_resume_event AS e
    JOIN job_input_manifest AS m ON m.job_id = e.job_id
    WHERE e.job_id = OLD.job_id
      AND e.source_launch_id = m.source_launch_id
      AND e.resume_count = OLD.resume_count + 1
      AND e.reason_code = OLD.terminal_reason_code
      AND e.generation > COALESCE(OLD.lease_generation, 0)
      AND NEW.lease_key IS OLD.lease_key
      AND NEW.lease_generation IS OLD.lease_generation
      AND NEW.resume_count = OLD.resume_count
      AND NEW.finished_at IS NULL
      AND NEW.terminal_reason_code IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'maintenance_job_resume_event_required');
END;
