CREATE TABLE worker_queue (
    queue_id TEXT PRIMARY KEY NOT NULL,
    source_launch_id TEXT NOT NULL UNIQUE REFERENCES daily_launch(source_launch_id),
    worker_job_id TEXT NOT NULL UNIQUE,
    target_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'complete', 'blocked')),
    check_id TEXT REFERENCES daily_check(check_id),
    claim_owner_id TEXT,
    claim_generation INTEGER CHECK (claim_generation IS NULL OR claim_generation > 0),
    claimed_at TEXT,
    completed_at TEXT,
    reason_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((claim_owner_id IS NULL) = (claim_generation IS NULL)),
    CHECK (
        (status = 'queued' AND check_id IS NULL AND claim_owner_id IS NULL
            AND claim_generation IS NULL AND claimed_at IS NULL
            AND completed_at IS NULL AND reason_code IS NULL)
        OR
        (status = 'complete' AND check_id IS NOT NULL AND claim_owner_id IS NOT NULL
            AND claim_generation IS NOT NULL AND claimed_at IS NOT NULL
            AND completed_at IS NOT NULL AND reason_code IS NULL)
        OR
        (status = 'blocked' AND check_id IS NULL AND claimed_at IS NOT NULL
            AND completed_at IS NULL AND reason_code IS NOT NULL)
    )
);

CREATE INDEX worker_queue_pending_idx
ON worker_queue(status, target_date, created_at, queue_id)
WHERE status = 'queued';

INSERT INTO worker_queue (
    queue_id, source_launch_id, worker_job_id, target_date, status, created_at, updated_at
)
SELECT lower(hex(randomblob(16))), dl.source_launch_id,
       'worker-' || lower(hex(randomblob(16))), dl.target_date, 'queued',
       dl.completed_at, dl.completed_at
FROM daily_launch AS dl
JOIN daily_check AS dc ON dc.source_launch_id = dl.source_launch_id
WHERE dl.status <> 'running';

CREATE TRIGGER worker_queue_identity_immutable
BEFORE UPDATE OF queue_id, source_launch_id, worker_job_id, target_date, created_at
ON worker_queue
BEGIN
    SELECT RAISE(ABORT, 'worker_queue_identity_immutable');
END;

CREATE TRIGGER worker_queue_no_delete
BEFORE DELETE ON worker_queue
BEGIN
    SELECT RAISE(ABORT, 'worker_queue_no_delete');
END;
