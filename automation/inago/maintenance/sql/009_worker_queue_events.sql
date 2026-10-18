CREATE TABLE worker_queue_event (
    event_id TEXT PRIMARY KEY NOT NULL,
    queue_id TEXT NOT NULL REFERENCES worker_queue(queue_id),
    event_type TEXT NOT NULL CHECK (event_type IN ('blocked', 'resumed')),
    check_id TEXT REFERENCES daily_check(check_id),
    reason_code TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    occurred_at TEXT NOT NULL
);

CREATE INDEX worker_queue_event_history_idx
ON worker_queue_event(queue_id, occurred_at, event_id);

CREATE TRIGGER worker_queue_event_no_update
BEFORE UPDATE ON worker_queue_event
BEGIN
    SELECT RAISE(ABORT, 'worker_queue_event_immutable');
END;

CREATE TRIGGER worker_queue_event_no_delete
BEFORE DELETE ON worker_queue_event
BEGIN
    SELECT RAISE(ABORT, 'worker_queue_event_immutable');
END;

CREATE TABLE daily_result_observation (
    source_launch_id TEXT PRIMARY KEY NOT NULL REFERENCES daily_launch(source_launch_id),
    target_date TEXT NOT NULL,
    source_run_id TEXT,
    schema_version TEXT NOT NULL CHECK (schema_version = 'daily-result-observation-v1'),
    observation_json TEXT NOT NULL CHECK (json_valid(observation_json)),
    observation_sha256 TEXT NOT NULL CHECK (length(observation_sha256) = 64),
    captured_at TEXT NOT NULL
);

CREATE TRIGGER daily_result_observation_no_update
BEFORE UPDATE ON daily_result_observation
BEGIN
    SELECT RAISE(ABORT, 'daily_result_observation_immutable');
END;

CREATE TRIGGER daily_result_observation_no_delete
BEFORE DELETE ON daily_result_observation
BEGIN
    SELECT RAISE(ABORT, 'daily_result_observation_immutable');
END;

ALTER TABLE deployment ADD COLUMN previous_active_commit TEXT;

CREATE TRIGGER deployment_previous_active_immutable
BEFORE UPDATE OF previous_active_commit ON deployment
WHEN OLD.previous_active_commit IS NOT NULL
    AND NEW.previous_active_commit IS NOT OLD.previous_active_commit
BEGIN
    SELECT RAISE(ABORT, 'deployment_previous_active_immutable');
END;
