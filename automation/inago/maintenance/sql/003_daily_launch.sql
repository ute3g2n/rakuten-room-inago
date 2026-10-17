CREATE TABLE daily_launch (
    source_launch_id TEXT PRIMARY KEY NOT NULL,
    target_date TEXT NOT NULL,
    owner_pid INTEGER NOT NULL CHECK (owner_pid > 0),
    started_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'running', 'complete', 'failed', 'blocked', 'unknown'
    )),
    child_pid INTEGER CHECK (child_pid IS NULL OR child_pid > 0),
    source_run_id TEXT,
    exit_code INTEGER CHECK (exit_code IS NULL OR exit_code BETWEEN 0 AND 255),
    completed_at TEXT,
    result_json TEXT,
    CHECK (
        (status = 'running' AND exit_code IS NULL AND completed_at IS NULL
            AND result_json IS NULL AND source_run_id IS NULL)
        OR
        (status <> 'running' AND exit_code IS NOT NULL AND completed_at IS NOT NULL
            AND result_json IS NOT NULL)
    )
);

CREATE TRIGGER daily_launch_identity_immutable
BEFORE UPDATE OF source_launch_id, target_date, owner_pid, started_at
ON daily_launch
BEGIN
    SELECT RAISE(ABORT, 'daily_launch_identity_immutable');
END;

CREATE TRIGGER daily_launch_child_pid_immutable
BEFORE UPDATE OF child_pid ON daily_launch
WHEN OLD.child_pid IS NOT NULL AND NEW.child_pid IS NOT OLD.child_pid
BEGIN
    SELECT RAISE(ABORT, 'daily_launch_child_pid_immutable');
END;

CREATE TRIGGER daily_launch_terminal_immutable
BEFORE UPDATE ON daily_launch
WHEN OLD.status <> 'running'
BEGIN
    SELECT RAISE(ABORT, 'daily_launch_terminal_immutable');
END;

CREATE TRIGGER daily_launch_no_delete
BEFORE DELETE ON daily_launch
BEGIN
    SELECT RAISE(ABORT, 'daily_launch_no_delete');
END;
