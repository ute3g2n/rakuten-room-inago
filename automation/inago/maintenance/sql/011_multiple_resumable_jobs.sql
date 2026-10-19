DROP INDEX one_active_maintenance_job;

DROP INDEX IF EXISTS one_running_maintenance_job;

DROP TRIGGER maintenance_job_resume_event_insert_guard;

CREATE TRIGGER maintenance_job_resume_event_insert_guard
BEFORE INSERT ON maintenance_job_resume_event
WHEN NOT EXISTS (
    SELECT 1
    FROM maintenance_job AS j
    JOIN incident_run AS ir ON ir.incident_id = j.incident_id
    JOIN job_input_manifest AS m ON m.job_id = j.job_id
    WHERE j.job_id = NEW.job_id
      AND j.status IN ('blocked', 'failed', 'cancelled')
      AND NEW.resume_count = j.resume_count + 1
      AND ir.source_launch_id = m.source_launch_id
      AND ir.source_run_id IS m.source_run_id
      AND m.source_launch_id = NEW.source_launch_id
      AND m.source_run_id IS j.source_run_id
)
BEGIN
    SELECT RAISE(ABORT, 'maintenance_job_resume_event_invalid');
END;

DROP TRIGGER maintenance_job_resume_transition_guard;

CREATE TRIGGER maintenance_job_resume_transition_guard
BEFORE UPDATE OF status ON maintenance_job
WHEN OLD.status IN ('blocked', 'failed', 'cancelled') AND NEW.status = 'queued'
 AND NOT EXISTS (
    SELECT 1
    FROM maintenance_job_resume_event AS e
    JOIN job_input_manifest AS m ON m.job_id = OLD.job_id
    WHERE e.job_id = OLD.job_id
      AND e.source_launch_id = m.source_launch_id
      AND m.source_run_id IS OLD.source_run_id
      AND e.resume_count = OLD.resume_count + 1
      AND NEW.resume_count = OLD.resume_count
      AND NEW.finished_at IS NULL
      AND NEW.terminal_reason_code IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'maintenance_job_resume_event_required');
END;
