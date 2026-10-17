ALTER TABLE daily_launch ADD COLUMN owner_start_time TEXT;
ALTER TABLE daily_launch ADD COLUMN child_start_time TEXT;

CREATE INDEX daily_launch_target_status_idx
ON daily_launch(target_date, status, started_at, source_launch_id);

CREATE INDEX daily_launch_running_idx
ON daily_launch(target_date, started_at, source_launch_id)
WHERE status = 'running';

DROP TRIGGER daily_launch_identity_immutable;
CREATE TRIGGER daily_launch_identity_immutable
BEFORE UPDATE OF source_launch_id, target_date, owner_pid, owner_start_time, started_at
ON daily_launch
BEGIN
    SELECT RAISE(ABORT, 'daily_launch_identity_immutable');
END;

DROP TRIGGER daily_launch_child_pid_immutable;
CREATE TRIGGER daily_launch_child_identity_immutable
BEFORE UPDATE OF child_pid, child_start_time ON daily_launch
WHEN (OLD.child_pid IS NOT NULL AND NEW.child_pid IS NOT OLD.child_pid)
    OR (OLD.child_start_time IS NOT NULL AND NEW.child_start_time IS NOT OLD.child_start_time)
BEGIN
    SELECT RAISE(ABORT, 'daily_launch_child_identity_immutable');
END;

CREATE TABLE watchdog_queue (
    queue_id TEXT PRIMARY KEY NOT NULL,
    occurrence_key TEXT NOT NULL UNIQUE,
    target_date TEXT NOT NULL,
    source_launch_id TEXT REFERENCES daily_launch(source_launch_id),
    status TEXT NOT NULL CHECK (status IN (
        'unknown', 'awaiting_completion', 'resolved', 'blocked'
    )),
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX watchdog_queue_source_launch_unique
ON watchdog_queue(source_launch_id)
WHERE source_launch_id IS NOT NULL;

CREATE INDEX watchdog_queue_open_idx
ON watchdog_queue(status, target_date, created_at)
WHERE status IN ('unknown', 'awaiting_completion', 'blocked');

CREATE INDEX watchdog_queue_target_date_idx
ON watchdog_queue(target_date, created_at, queue_id);

CREATE INDEX watchdog_queue_reconcile_idx
ON watchdog_queue(target_date, created_at, queue_id)
WHERE status IN ('unknown', 'awaiting_completion');

CREATE TABLE watchdog_cursor (
    cursor_key TEXT PRIMARY KEY NOT NULL CHECK (cursor_key = 'daily'),
    reconciled_through_date TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TRIGGER watchdog_cursor_monotonic
BEFORE UPDATE OF reconciled_through_date ON watchdog_cursor
WHEN NEW.reconciled_through_date < OLD.reconciled_through_date
BEGIN
    SELECT RAISE(ABORT, 'watchdog_cursor_cannot_move_backward');
END;

CREATE TRIGGER watchdog_cursor_no_delete
BEFORE DELETE ON watchdog_cursor
BEGIN
    SELECT RAISE(ABORT, 'watchdog_cursor_no_delete');
END;

CREATE TRIGGER watchdog_queue_identity_immutable
BEFORE UPDATE OF queue_id, occurrence_key, target_date, created_at ON watchdog_queue
BEGIN
    SELECT RAISE(ABORT, 'watchdog_queue_identity_immutable');
END;

CREATE TRIGGER watchdog_queue_source_launch_immutable
BEFORE UPDATE OF source_launch_id ON watchdog_queue
WHEN OLD.source_launch_id IS NOT NULL
    AND NEW.source_launch_id IS NOT OLD.source_launch_id
BEGIN
    SELECT RAISE(ABORT, 'watchdog_queue_source_launch_immutable');
END;

CREATE TRIGGER watchdog_queue_status_transition
BEFORE UPDATE OF status ON watchdog_queue
WHEN NOT (
    OLD.status = NEW.status
    OR (OLD.status = 'unknown' AND NEW.status IN ('awaiting_completion', 'resolved', 'blocked'))
    OR (OLD.status = 'awaiting_completion' AND NEW.status IN ('resolved', 'blocked'))
)
BEGIN
    SELECT RAISE(ABORT, 'watchdog_queue_status_transition_invalid');
END;

CREATE TRIGGER watchdog_queue_no_delete
BEFORE DELETE ON watchdog_queue
BEGIN
    SELECT RAISE(ABORT, 'watchdog_queue_no_delete');
END;
