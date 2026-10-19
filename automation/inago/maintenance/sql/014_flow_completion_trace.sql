CREATE TABLE maintenance_operation_event (
    event_id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    parent_operation TEXT,
    job_id TEXT,
    source_launch_id TEXT,
    source_run_id TEXT,
    incident_id TEXT,
    invocation_id TEXT,
    stage TEXT,
    attempt_id TEXT,
    operation TEXT NOT NULL,
    phase TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    code_revision TEXT,
    reason_code TEXT,
    exception_chain_json TEXT NOT NULL DEFAULT '[]',
    exception_errno INTEGER,
    stack_frames_json TEXT NOT NULL DEFAULT '[]',
    capture_location TEXT,
    input_refs_json TEXT NOT NULL DEFAULT '[]',
    artifact_refs_json TEXT NOT NULL DEFAULT '[]',
    primary_sink TEXT,
    fallback_status TEXT,
    recovered_at TEXT
);

CREATE INDEX maintenance_operation_event_correlation_idx
    ON maintenance_operation_event(correlation_id, observed_at);
CREATE INDEX maintenance_operation_event_job_idx
    ON maintenance_operation_event(job_id, observed_at);

CREATE TRIGGER maintenance_operation_event_no_update
BEFORE UPDATE ON maintenance_operation_event
BEGIN
    SELECT RAISE(ABORT, 'maintenance_operation_event_append_only');
END;

CREATE TRIGGER maintenance_operation_event_no_delete
BEFORE DELETE ON maintenance_operation_event
BEGIN
    SELECT RAISE(ABORT, 'maintenance_operation_event_append_only');
END;
