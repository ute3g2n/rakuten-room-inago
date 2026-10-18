CREATE TABLE deployment_verification (
    verification_id TEXT PRIMARY KEY NOT NULL,
    deployment_id TEXT NOT NULL UNIQUE REFERENCES deployment(deployment_id),
    target_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'verified', 'recurred', 'not_observed', 'blocked')
    ),
    source_launch_id TEXT REFERENCES daily_launch(source_launch_id),
    check_id TEXT REFERENCES daily_check(check_id),
    observed_commit TEXT,
    evidence_ref TEXT,
    recurrence_incident_id TEXT REFERENCES incident(incident_id),
    reason_code TEXT,
    observed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (status = 'pending' AND source_launch_id IS NULL AND check_id IS NULL
            AND observed_commit IS NULL AND evidence_ref IS NULL
            AND recurrence_incident_id IS NULL AND reason_code IS NULL
            AND observed_at IS NULL)
        OR
        (status = 'verified' AND source_launch_id IS NOT NULL AND check_id IS NOT NULL
            AND observed_commit IS NOT NULL AND evidence_ref IS NOT NULL
            AND recurrence_incident_id IS NULL AND reason_code IS NULL
            AND observed_at IS NOT NULL)
        OR
        (status = 'recurred' AND source_launch_id IS NOT NULL AND check_id IS NOT NULL
            AND evidence_ref IS NOT NULL AND recurrence_incident_id IS NOT NULL
            AND reason_code IS NOT NULL AND observed_at IS NOT NULL)
        OR
        (status = 'blocked' AND source_launch_id IS NOT NULL AND check_id IS NOT NULL
            AND evidence_ref IS NOT NULL AND recurrence_incident_id IS NULL
            AND reason_code IS NOT NULL AND observed_at IS NOT NULL)
        OR
        (status = 'not_observed' AND source_launch_id IS NULL AND check_id IS NULL
            AND observed_commit IS NULL AND evidence_ref IS NULL
            AND recurrence_incident_id IS NULL AND reason_code IS NOT NULL
            AND observed_at IS NOT NULL)
    )
);

CREATE INDEX deployment_verification_pending_idx
ON deployment_verification(status, target_date, created_at)
WHERE status = 'pending';

CREATE TRIGGER deployment_verification_deployed_only
BEFORE INSERT ON deployment_verification
WHEN (SELECT status FROM deployment WHERE deployment_id = NEW.deployment_id) <> 'deployed'
BEGIN
    SELECT RAISE(ABORT, 'verification_deployment_not_deployed');
END;

CREATE TRIGGER deployment_verification_identity_immutable
BEFORE UPDATE OF verification_id, deployment_id, target_date, created_at
ON deployment_verification
BEGIN
    SELECT RAISE(ABORT, 'verification_identity_immutable');
END;

CREATE TRIGGER deployment_verification_terminal_once
BEFORE UPDATE ON deployment_verification
WHEN OLD.status <> 'pending' OR NEW.status = 'pending'
BEGIN
    SELECT RAISE(ABORT, 'verification_terminal_once');
END;

CREATE TRIGGER deployment_verification_no_delete
BEFORE DELETE ON deployment_verification
BEGIN
    SELECT RAISE(ABORT, 'verification_no_delete');
END;
