-- R10: keep prior deadline values for audit, but do not apply run-wide cutoffs.
-- New daily runs store NULL. The repository owns BEGIN/COMMIT and FK checks.

CREATE TABLE run_v6 (
    run_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(run_id)) > 0),
    target_date TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('setup', 'daily', 'status', 'recovery', 'config-change', 'export', 'serve', 'migrate', 'backup', 'restore-check', 'acceptance')),
    status TEXT NOT NULL CHECK (status IN ('created', 'running', 'complete', 'partial', 'failed', 'shortfall', 'missed')),
    settings_version TEXT NOT NULL REFERENCES settings_version(version),
    seed TEXT NOT NULL CHECK (length(trim(seed)) > 0),
    started_at TEXT NOT NULL,
    draft_deadline TEXT,
    end_deadline TEXT,
    attempt_used INTEGER NOT NULL CHECK (attempt_used >= 0),
    committed_count INTEGER NOT NULL CHECK (committed_count BETWEEN 0 AND 15),
    checkpoint TEXT,
    metrics_status TEXT NOT NULL CHECK (metrics_status IN ('pending', 'observed', 'partial', 'unavailable', 'not_started')),
    finished_at TEXT
);

INSERT INTO run_v6 (
    run_id, target_date, mode, status, settings_version, seed, started_at,
    draft_deadline, end_deadline, attempt_used, committed_count, checkpoint,
    metrics_status, finished_at
)
SELECT
    run_id, target_date, mode, status, settings_version, seed, started_at,
    draft_deadline, end_deadline, attempt_used, committed_count, checkpoint,
    metrics_status, finished_at
FROM run;

DROP TABLE run;
ALTER TABLE run_v6 RENAME TO run;
CREATE UNIQUE INDEX one_daily_run_per_target_date
    ON run(target_date) WHERE mode = 'daily';
