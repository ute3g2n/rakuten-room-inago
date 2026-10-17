ALTER TABLE step_attempt ADD COLUMN repair_predecessor_attempt_id TEXT;
ALTER TABLE step_attempt ADD COLUMN repair_predecessor_result_version TEXT;
ALTER TABLE step_attempt ADD COLUMN repair_predecessor_output_sha256 TEXT;

CREATE TRIGGER step_attempt_repair_predecessor_all_or_none
BEFORE INSERT ON step_attempt
WHEN NEW.step_key = 'maintenance_repair'
    AND (
        NEW.sequence <> 3
        OR NEW.repair_predecessor_attempt_id IS NULL
        OR NEW.repair_predecessor_result_version IS NULL
        OR NEW.repair_predecessor_output_sha256 IS NULL
    )
BEGIN
    SELECT RAISE(ABORT, 'repair_predecessor_pin_required');
END;

CREATE TRIGGER step_attempt_repair_predecessor_immutable
BEFORE UPDATE OF repair_predecessor_attempt_id,
    repair_predecessor_result_version,
    repair_predecessor_output_sha256 ON step_attempt
WHEN OLD.repair_predecessor_attempt_id IS NOT NULL
    AND (
        NEW.repair_predecessor_attempt_id IS NOT OLD.repair_predecessor_attempt_id
        OR NEW.repair_predecessor_result_version IS NOT OLD.repair_predecessor_result_version
        OR NEW.repair_predecessor_output_sha256 IS NOT OLD.repair_predecessor_output_sha256
    )
BEGIN
    SELECT RAISE(ABORT, 'repair_predecessor_pin_immutable');
END;

CREATE TABLE repair_campaign (
    campaign_id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES maintenance_job(job_id),
    attempt_id TEXT NOT NULL REFERENCES step_attempt(attempt_id),
    parent_campaign_id TEXT REFERENCES repair_campaign(campaign_id),
    code_sha256 TEXT NOT NULL CHECK (length(code_sha256) = 64),
    invocation_parent_id TEXT,
    state TEXT NOT NULL CHECK (state IN (
        'active', 'full_regression_started', 'full_regression_recorded',
        'full_regression_failed'
    )),
    full_regression_token_hash TEXT,
    full_regression_nonce_hash TEXT,
    full_regression_claimed_at TEXT,
    full_regression_lease_expires_at TEXT,
    full_snapshot_json TEXT,
    full_snapshot_sha256 TEXT,
    full_run_receipt_id TEXT,
    full_regression_failure_code TEXT,
    full_regression_owner_pid INTEGER,
    full_regression_owner_start_id TEXT,
    full_regression_child_pid INTEGER,
    full_regression_child_start_id TEXT,
    full_regression_child_state TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (state = 'active'
            AND full_regression_token_hash IS NULL
            AND full_regression_nonce_hash IS NULL
            AND full_regression_claimed_at IS NULL
            AND full_regression_lease_expires_at IS NULL
            AND full_snapshot_json IS NULL
            AND full_snapshot_sha256 IS NULL
            AND full_run_receipt_id IS NULL
            AND full_regression_failure_code IS NULL
            AND full_regression_owner_pid IS NULL
            AND full_regression_owner_start_id IS NULL
            AND full_regression_child_pid IS NULL
            AND full_regression_child_start_id IS NULL
            AND full_regression_child_state IS NULL)
        OR
        (state = 'full_regression_started'
            AND full_regression_token_hash IS NOT NULL
            AND ((full_regression_nonce_hash IS NULL
                    AND full_regression_claimed_at IS NULL)
                OR (full_regression_nonce_hash IS NOT NULL
                    AND full_regression_claimed_at IS NOT NULL))
            AND full_regression_lease_expires_at IS NOT NULL
            AND full_snapshot_json IS NOT NULL
            AND full_snapshot_sha256 IS NOT NULL
            AND full_run_receipt_id IS NULL
            AND full_regression_failure_code IS NULL
            AND full_regression_owner_pid > 0
            AND full_regression_owner_start_id IS NOT NULL
            AND full_regression_child_state IN ('not_started', 'started', 'exited')
            AND ((full_regression_child_state = 'not_started'
                    AND full_regression_child_pid IS NULL
                    AND full_regression_child_start_id IS NULL)
                OR (full_regression_child_state IN ('started', 'exited')
                    AND full_regression_child_pid > 0
                    AND full_regression_child_start_id IS NOT NULL)))
        OR
        (state = 'full_regression_recorded'
            AND full_regression_token_hash IS NOT NULL
            AND full_regression_nonce_hash IS NOT NULL
            AND full_regression_claimed_at IS NOT NULL
            AND full_regression_lease_expires_at IS NOT NULL
            AND full_snapshot_json IS NOT NULL
            AND full_snapshot_sha256 IS NOT NULL
            AND full_run_receipt_id IS NOT NULL
            AND full_regression_failure_code IS NULL
            AND full_regression_owner_pid > 0
            AND full_regression_owner_start_id IS NOT NULL
            AND full_regression_child_state IN ('not_started', 'started', 'exited')
            AND ((full_regression_child_state = 'not_started'
                    AND full_regression_child_pid IS NULL
                    AND full_regression_child_start_id IS NULL)
                OR (full_regression_child_state IN ('started', 'exited')
                    AND full_regression_child_pid > 0
                    AND full_regression_child_start_id IS NOT NULL)))
        OR
        (state = 'full_regression_failed'
            AND full_regression_token_hash IS NOT NULL
            AND ((full_regression_nonce_hash IS NULL
                    AND full_regression_claimed_at IS NULL)
                OR (full_regression_nonce_hash IS NOT NULL
                    AND full_regression_claimed_at IS NOT NULL))
            AND full_regression_lease_expires_at IS NOT NULL
            AND full_snapshot_json IS NOT NULL
            AND full_snapshot_sha256 IS NOT NULL
            AND full_run_receipt_id IS NULL
            AND full_regression_failure_code IS NOT NULL
            AND full_regression_owner_pid > 0
            AND full_regression_owner_start_id IS NOT NULL
            AND full_regression_child_state IN ('not_started', 'started', 'exited')
            AND ((full_regression_child_state = 'not_started'
                    AND full_regression_child_pid IS NULL
                    AND full_regression_child_start_id IS NULL)
                OR (full_regression_child_state IN ('started', 'exited')
                    AND full_regression_child_pid > 0
                    AND full_regression_child_start_id IS NOT NULL)))
    )
);

CREATE TABLE repair_campaign_case (
    campaign_id TEXT NOT NULL REFERENCES repair_campaign(campaign_id),
    semantic_case_id TEXT NOT NULL,
    node TEXT NOT NULL,
    semantic_signature TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    frozen INTEGER NOT NULL DEFAULT 0 CHECK (frozen IN (0, 1)),
    PRIMARY KEY (campaign_id, semantic_case_id),
    UNIQUE (campaign_id, node),
    UNIQUE (campaign_id, semantic_signature)
);

CREATE TABLE repair_campaign_result (
    result_id TEXT PRIMARY KEY NOT NULL,
    campaign_id TEXT NOT NULL,
    semantic_case_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN (
        'PASS', 'FAIL', 'NOT_RUN', 'SKIP', 'XFAIL', 'ERROR', 'UNVERIFIED'
    )),
    code_sha256 TEXT NOT NULL CHECK (length(code_sha256) = 64),
    receipt_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, semantic_case_id)
        REFERENCES repair_campaign_case(campaign_id, semantic_case_id),
    FOREIGN KEY (receipt_id) REFERENCES repair_run_receipt(receipt_id),
    UNIQUE (receipt_id, semantic_case_id)
);

CREATE TABLE repair_case_run_start (
    invocation_id TEXT PRIMARY KEY NOT NULL,
    campaign_id TEXT NOT NULL REFERENCES repair_campaign(campaign_id),
    attempt_id TEXT NOT NULL REFERENCES step_attempt(attempt_id),
    semantic_case_id TEXT NOT NULL,
    node TEXT NOT NULL,
    cases_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL CHECK (length(code_sha256) = 64),
    nonce_hash TEXT NOT NULL,
    invocation_parent_id TEXT,
    owner_pid INTEGER NOT NULL CHECK (owner_pid > 0),
    owner_start_id TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    child_pid INTEGER,
    child_start_id TEXT,
    child_state TEXT NOT NULL CHECK (child_state IN ('not_started', 'started', 'exited')),
    state TEXT NOT NULL CHECK (state IN ('started', 'recorded', 'failed')),
    receipt_id TEXT,
    failure_code TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (child_state = 'not_started' AND child_pid IS NULL AND child_start_id IS NULL)
        OR (child_state IN ('started', 'exited') AND child_pid > 0 AND child_start_id IS NOT NULL)
    ),
    CHECK ((state = 'started' AND receipt_id IS NULL AND failure_code IS NULL)
        OR (state = 'recorded' AND receipt_id IS NOT NULL AND failure_code IS NULL)
        OR (state = 'failed' AND receipt_id IS NULL AND failure_code IS NOT NULL)),
    FOREIGN KEY (campaign_id, semantic_case_id)
        REFERENCES repair_campaign_case(campaign_id, semantic_case_id)
);

CREATE TABLE repair_run_receipt (
    receipt_id TEXT PRIMARY KEY NOT NULL,
    campaign_id TEXT NOT NULL REFERENCES repair_campaign(campaign_id),
    attempt_id TEXT NOT NULL REFERENCES step_attempt(attempt_id),
    run_kind TEXT NOT NULL CHECK (run_kind IN ('case', 'full_regression')),
    semantic_case_id TEXT,
    invocation_id TEXT,
    token_hash TEXT,
    nonce_hash TEXT NOT NULL,
    code_sha256 TEXT NOT NULL CHECK (length(code_sha256) = 64),
    cases_json TEXT NOT NULL,
    results_json TEXT NOT NULL,
    exit_code INTEGER NOT NULL CHECK (exit_code BETWEEN 0 AND 255),
    collected INTEGER NOT NULL CHECK (collected >= 0),
    assertion_failures INTEGER NOT NULL CHECK (assertion_failures >= 0),
    errors INTEGER NOT NULL CHECK (errors >= 0),
    skips INTEGER NOT NULL CHECK (skips >= 0),
    collection_errors INTEGER NOT NULL CHECK (collection_errors >= 0),
    invocation_parent_id TEXT,
    receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256) = 64),
    created_at TEXT NOT NULL,
    consumed_at TEXT,
    CHECK (
        (run_kind = 'case' AND semantic_case_id IS NOT NULL
            AND invocation_id IS NOT NULL AND token_hash IS NULL)
        OR
        (run_kind = 'full_regression' AND semantic_case_id IS NULL
            AND invocation_id IS NULL AND token_hash IS NOT NULL)
    ),
    UNIQUE (receipt_id, campaign_id),
    FOREIGN KEY (campaign_id, semantic_case_id)
        REFERENCES repair_campaign_case(campaign_id, semantic_case_id)
);

CREATE TABLE repair_host_review_execution (
    host_receipt_id TEXT PRIMARY KEY NOT NULL CHECK (length(host_receipt_id) = 64),
    job_id TEXT NOT NULL REFERENCES maintenance_job(job_id),
    attempt_id TEXT NOT NULL REFERENCES step_attempt(attempt_id),
    result_version TEXT NOT NULL,
    author_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    reviewer_session_key TEXT NOT NULL,
    reviewer_turn_id TEXT NOT NULL,
    reviewer_output_sha256 TEXT NOT NULL CHECK (length(reviewer_output_sha256) = 64),
    reviewed_output_sha256 TEXT NOT NULL CHECK (length(reviewed_output_sha256) = 64),
    code_sha256 TEXT NOT NULL CHECK (length(code_sha256) = 64),
    patch_sha256 TEXT NOT NULL CHECK (length(patch_sha256) = 64),
    test_ledger_sha256 TEXT NOT NULL CHECK (length(test_ledger_sha256) = 64),
    approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
    unresolved_critical_findings_json TEXT NOT NULL
        CHECK (json_valid(unresolved_critical_findings_json)),
    created_at TEXT NOT NULL,
    UNIQUE (job_id, reviewer_session_key, reviewer_turn_id),
    FOREIGN KEY (job_id, reviewer_session_key)
        REFERENCES maintenance_ai_session(job_id, session_key)
);

CREATE TABLE repair_review_receipt (
    receipt_id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES maintenance_job(job_id),
    attempt_id TEXT NOT NULL REFERENCES step_attempt(attempt_id),
    result_version TEXT NOT NULL,
    author_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    author_role TEXT NOT NULL CHECK (author_role = 'implementer'),
    reviewer_role TEXT NOT NULL CHECK (reviewer_role = 'read_only_reviewer'),
    host_receipt_id TEXT NOT NULL,
    reviewed_output_sha256 TEXT NOT NULL CHECK (length(reviewed_output_sha256) = 64),
    code_sha256 TEXT NOT NULL CHECK (length(code_sha256) = 64),
    patch_sha256 TEXT NOT NULL CHECK (length(patch_sha256) = 64),
    test_ledger_sha256 TEXT NOT NULL CHECK (length(test_ledger_sha256) = 64),
    receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256) = 64),
    created_at TEXT NOT NULL,
    consumed_at TEXT,
    UNIQUE (attempt_id, result_version)
);

CREATE UNIQUE INDEX repair_run_receipt_token_unique
ON repair_run_receipt(campaign_id, token_hash)
WHERE run_kind = 'full_regression';

CREATE TRIGGER repair_campaign_host_only
BEFORE INSERT ON repair_campaign
WHEN maintenance_repair_runner_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'repair_runner_capability_invalid');
END;

CREATE TRIGGER repair_campaign_full_transition_host_only
BEFORE UPDATE OF state ON repair_campaign
WHEN NEW.state <> OLD.state
    AND maintenance_repair_runner_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'repair_runner_capability_invalid');
END;

CREATE TRIGGER repair_case_run_start_host_only
BEFORE INSERT ON repair_case_run_start
WHEN maintenance_repair_runner_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'repair_runner_capability_invalid');
END;

CREATE TRIGGER repair_case_run_start_update_host_only
BEFORE UPDATE ON repair_case_run_start
WHEN maintenance_repair_runner_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'repair_runner_capability_invalid');
END;

CREATE TRIGGER repair_run_receipt_host_only
BEFORE INSERT ON repair_run_receipt
WHEN maintenance_repair_runner_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'repair_runner_capability_invalid');
END;

CREATE TRIGGER repair_review_receipt_host_only
BEFORE INSERT ON repair_review_receipt
WHEN maintenance_repair_review_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'repair_review_capability_invalid');
END;

CREATE TRIGGER repair_host_review_execution_host_only
BEFORE INSERT ON repair_host_review_execution
WHEN maintenance_repair_review_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'repair_review_capability_invalid');
END;

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

CREATE TRIGGER repair_host_review_execution_immutable
BEFORE UPDATE ON repair_host_review_execution
BEGIN
    SELECT RAISE(ABORT, 'repair_host_review_execution_immutable');
END;

CREATE TRIGGER repair_host_review_execution_no_delete
BEFORE DELETE ON repair_host_review_execution
BEGIN
    SELECT RAISE(ABORT, 'repair_host_review_execution_no_delete');
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

CREATE TRIGGER repair_campaign_identity_immutable
BEFORE UPDATE OF campaign_id, job_id, attempt_id, parent_campaign_id, code_sha256,
    invocation_parent_id, created_at ON repair_campaign
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_identity_immutable');
END;

CREATE TRIGGER repair_campaign_state_transition
BEFORE UPDATE OF state ON repair_campaign
WHEN NOT (
    OLD.state = NEW.state
    OR (OLD.state = 'active' AND NEW.state = 'full_regression_started')
    OR (OLD.state = 'full_regression_started' AND NEW.state = 'full_regression_recorded')
    OR (OLD.state = 'full_regression_started' AND NEW.state = 'full_regression_failed')
)
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_state_transition_invalid');
END;

CREATE TRIGGER repair_campaign_token_immutable
BEFORE UPDATE OF full_regression_token_hash, full_regression_nonce_hash,
    full_regression_claimed_at, full_snapshot_json, full_snapshot_sha256,
    full_run_receipt_id, full_regression_failure_code,
    full_regression_lease_expires_at ON repair_campaign
WHEN OLD.full_regression_token_hash IS NOT NULL
    AND (
        NEW.full_regression_token_hash IS NOT OLD.full_regression_token_hash
        OR (NEW.full_regression_nonce_hash IS NOT OLD.full_regression_nonce_hash
            AND NOT (OLD.full_regression_nonce_hash IS NULL
                AND NEW.full_regression_nonce_hash IS NOT NULL
                AND OLD.full_regression_claimed_at IS NULL
                AND NEW.full_regression_claimed_at IS NOT NULL
                AND maintenance_repair_runner_authorized() = 1))
        OR (NEW.full_regression_claimed_at IS NOT OLD.full_regression_claimed_at
            AND NOT (OLD.full_regression_nonce_hash IS NULL
                AND NEW.full_regression_nonce_hash IS NOT NULL
                AND OLD.full_regression_claimed_at IS NULL
                AND NEW.full_regression_claimed_at IS NOT NULL
                AND maintenance_repair_runner_authorized() = 1))
        OR NEW.full_snapshot_json IS NOT OLD.full_snapshot_json
        OR NEW.full_snapshot_sha256 IS NOT OLD.full_snapshot_sha256
        OR NEW.full_regression_lease_expires_at IS NOT OLD.full_regression_lease_expires_at
        OR (NEW.full_regression_failure_code IS NOT OLD.full_regression_failure_code
            AND NOT (OLD.state = 'full_regression_started'
                AND NEW.state = 'full_regression_failed'
                AND OLD.full_regression_failure_code IS NULL
                AND NEW.full_regression_failure_code IS NOT NULL
                AND maintenance_repair_runner_authorized() = 1))
        OR (OLD.full_run_receipt_id IS NOT NULL
            AND NEW.full_run_receipt_id IS NOT OLD.full_run_receipt_id)
    )
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_token_immutable');
END;

CREATE TRIGGER repair_campaign_runner_identity_transition
BEFORE UPDATE OF full_regression_owner_pid, full_regression_owner_start_id,
    full_regression_child_pid, full_regression_child_start_id,
    full_regression_child_state ON repair_campaign
WHEN NOT (
    maintenance_repair_runner_authorized() = 1
    AND (
        (OLD.state = 'active' AND NEW.state = 'full_regression_started'
            AND OLD.full_regression_owner_pid IS NULL
            AND NEW.full_regression_owner_pid > 0
            AND OLD.full_regression_owner_start_id IS NULL
            AND NEW.full_regression_owner_start_id IS NOT NULL
            AND OLD.full_regression_child_state IS NULL
            AND NEW.full_regression_child_state = 'not_started'
            AND NEW.full_regression_child_pid IS NULL
            AND NEW.full_regression_child_start_id IS NULL)
        OR (OLD.state = 'full_regression_started'
            AND NEW.state IN ('full_regression_started', 'full_regression_failed')
            AND NEW.full_regression_owner_pid IS OLD.full_regression_owner_pid
            AND NEW.full_regression_owner_start_id IS OLD.full_regression_owner_start_id
            AND (
                (OLD.full_regression_child_state = NEW.full_regression_child_state
                    AND OLD.full_regression_child_pid IS NEW.full_regression_child_pid
                    AND OLD.full_regression_child_start_id IS NEW.full_regression_child_start_id)
                OR (OLD.full_regression_child_state = 'not_started'
                    AND NEW.full_regression_child_state = 'started'
                    AND OLD.full_regression_child_pid IS NULL
                    AND NEW.full_regression_child_pid > 0
                    AND OLD.full_regression_child_start_id IS NULL
                    AND NEW.full_regression_child_start_id IS NOT NULL)
                OR (OLD.full_regression_child_state = 'started'
                    AND NEW.full_regression_child_state = 'exited'
                    AND OLD.full_regression_child_pid IS NEW.full_regression_child_pid
                    AND OLD.full_regression_child_start_id IS NEW.full_regression_child_start_id)
            ))
    )
)
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_runner_identity_transition_invalid');
END;

CREATE TRIGGER repair_campaign_case_identity_immutable
BEFORE UPDATE OF campaign_id, semantic_case_id, node, semantic_signature
ON repair_campaign_case
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_case_identity_immutable');
END;

CREATE TRIGGER repair_campaign_case_insert_guard
BEFORE INSERT ON repair_campaign_case
WHEN maintenance_repair_runner_authorized() <> 1
    OR NOT EXISTS (
        SELECT 1 FROM repair_campaign c
        WHERE c.campaign_id = NEW.campaign_id AND c.state = 'active'
    )
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_case_insert_guard');
END;

CREATE TRIGGER repair_campaign_case_frozen_monotonic
BEFORE UPDATE OF frozen ON repair_campaign_case
WHEN NOT (
    OLD.frozen = NEW.frozen
    OR (
        OLD.frozen = 0 AND NEW.frozen = 1
        AND maintenance_repair_runner_authorized() = 1
        AND EXISTS (
            SELECT 1
            FROM repair_campaign_result result
            JOIN repair_run_receipt receipt ON receipt.receipt_id = result.receipt_id
            WHERE result.campaign_id = NEW.campaign_id
              AND result.semantic_case_id = NEW.semantic_case_id
              AND result.outcome = 'PASS'
              AND receipt.consumed_at IS NOT NULL
        )
    )
)
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_case_frozen_monotonic');
END;

CREATE TRIGGER repair_campaign_case_frozen_insert_guard
BEFORE INSERT ON repair_campaign_case
WHEN NEW.frozen = 1 AND NOT (
    maintenance_repair_runner_authorized() = 1
    AND EXISTS (
        WITH RECURSIVE campaign_ancestors(campaign_id) AS (
            SELECT parent_campaign_id
            FROM repair_campaign
            WHERE campaign_id = NEW.campaign_id
            UNION ALL
            SELECT parent.parent_campaign_id
            FROM repair_campaign parent
            JOIN campaign_ancestors ancestor
              ON parent.campaign_id = ancestor.campaign_id
            WHERE parent.parent_campaign_id IS NOT NULL
        )
        SELECT 1
        FROM campaign_ancestors ancestor
        JOIN repair_campaign_case source_case
          ON source_case.campaign_id = ancestor.campaign_id
         AND source_case.semantic_case_id = NEW.semantic_case_id
         AND source_case.node = NEW.node
         AND source_case.semantic_signature = NEW.semantic_signature
        JOIN repair_campaign_result result
          ON result.campaign_id = source_case.campaign_id
         AND result.semantic_case_id = source_case.semantic_case_id
         AND result.outcome = 'PASS'
        JOIN repair_run_receipt receipt
          ON receipt.receipt_id = result.receipt_id
         AND receipt.consumed_at IS NOT NULL
        WHERE ancestor.campaign_id IS NOT NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_case_frozen_insert_guard');
END;

CREATE TRIGGER repair_campaign_case_no_delete
BEFORE DELETE ON repair_campaign_case
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_case_no_delete');
END;

CREATE TRIGGER repair_campaign_result_no_update
BEFORE UPDATE ON repair_campaign_result
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_result_immutable');
END;

CREATE TRIGGER repair_campaign_result_no_delete
BEFORE DELETE ON repair_campaign_result
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_result_no_delete');
END;

CREATE TRIGGER repair_campaign_result_receipt_bind
BEFORE INSERT ON repair_campaign_result
WHEN NOT EXISTS (
    SELECT 1
    FROM repair_run_receipt receipt
    JOIN json_each(receipt.results_json) result
    WHERE receipt.receipt_id = NEW.receipt_id
      AND receipt.campaign_id = NEW.campaign_id
      AND receipt.code_sha256 = NEW.code_sha256
      AND receipt.consumed_at IS NOT NULL
      AND result.key = NEW.semantic_case_id
      AND result.value = NEW.outcome
      AND (
          (receipt.run_kind = 'case'
              AND receipt.semantic_case_id = NEW.semantic_case_id)
          OR
          (receipt.run_kind = 'full_regression'
              AND receipt.exit_code = 0
              AND receipt.errors = 0
              AND receipt.skips = 0
              AND receipt.collection_errors = 0
              AND result.value = 'PASS')
      )
)
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_result_receipt_mismatch');
END;

CREATE TRIGGER repair_run_receipt_bind_start
BEFORE INSERT ON repair_run_receipt
WHEN (NEW.run_kind = 'full_regression' AND NOT EXISTS (
    SELECT 1 FROM repair_campaign c
    WHERE c.campaign_id = NEW.campaign_id
      AND c.attempt_id = NEW.attempt_id
      AND c.code_sha256 = NEW.code_sha256
      AND c.invocation_parent_id IS NEW.invocation_parent_id
      AND c.state = 'full_regression_started'
      AND c.full_regression_token_hash IS NEW.token_hash
      AND c.full_regression_nonce_hash = NEW.nonce_hash
      AND c.full_snapshot_json = NEW.cases_json
      AND c.full_regression_claimed_at IS NOT NULL
)) OR (NEW.run_kind = 'case' AND NOT EXISTS (
    SELECT 1 FROM repair_case_run_start s
    WHERE s.invocation_id = NEW.invocation_id
      AND s.campaign_id = NEW.campaign_id
      AND s.attempt_id = NEW.attempt_id
      AND s.semantic_case_id = NEW.semantic_case_id
      AND s.code_sha256 = NEW.code_sha256
      AND s.nonce_hash = NEW.nonce_hash
      AND s.invocation_parent_id IS NEW.invocation_parent_id
      AND s.child_state IN ('not_started', 'exited')
      AND s.state = 'started'
      AND NEW.cases_json = s.cases_json
))
BEGIN
    SELECT RAISE(ABORT, 'repair_run_receipt_start_mismatch');
END;

CREATE TRIGGER repair_case_run_receipt_recorded
AFTER INSERT ON repair_run_receipt
WHEN NEW.run_kind = 'case'
BEGIN
    UPDATE repair_case_run_start SET state = 'recorded', receipt_id = NEW.receipt_id
    WHERE invocation_id = NEW.invocation_id AND state = 'started';
END;

CREATE TRIGGER repair_case_run_start_identity_immutable
BEFORE UPDATE OF invocation_id, campaign_id, attempt_id, semantic_case_id,
    node, cases_json, code_sha256, nonce_hash, invocation_parent_id, owner_pid,
    owner_start_id, lease_expires_at, created_at
ON repair_case_run_start
BEGIN
    SELECT RAISE(ABORT, 'repair_case_run_start_identity_immutable');
END;

CREATE TRIGGER repair_case_run_start_child_transition
BEFORE UPDATE OF child_pid, child_start_id, child_state ON repair_case_run_start
WHEN NOT (
    OLD.child_state = NEW.child_state
    AND OLD.child_pid IS NEW.child_pid
    AND OLD.child_start_id IS NEW.child_start_id
    OR OLD.child_state = 'not_started'
        AND NEW.child_state = 'started'
        AND OLD.child_pid IS NULL
        AND NEW.child_pid > 0
        AND OLD.child_start_id IS NULL
        AND NEW.child_start_id IS NOT NULL
    OR OLD.child_state = 'started'
        AND NEW.child_state = 'exited'
        AND OLD.child_pid IS NEW.child_pid
        AND OLD.child_start_id IS NEW.child_start_id
)
BEGIN
    SELECT RAISE(ABORT, 'repair_case_run_child_transition_invalid');
END;

CREATE TRIGGER repair_case_run_start_transition
BEFORE UPDATE OF state, receipt_id, failure_code ON repair_case_run_start
WHEN NOT (
    OLD.state = NEW.state AND OLD.receipt_id IS NEW.receipt_id
        AND OLD.failure_code IS NEW.failure_code
    OR OLD.state = 'started' AND NEW.state = 'recorded'
        AND OLD.receipt_id IS NULL AND NEW.receipt_id IS NOT NULL
        AND OLD.failure_code IS NULL AND NEW.failure_code IS NULL
        AND NEW.child_state IN ('not_started', 'exited')
    OR OLD.state = 'started' AND NEW.state = 'failed'
        AND OLD.receipt_id IS NULL AND NEW.receipt_id IS NULL
        AND OLD.failure_code IS NULL AND NEW.failure_code IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'repair_case_run_start_transition_invalid');
END;

CREATE TRIGGER repair_case_run_start_no_delete
BEFORE DELETE ON repair_case_run_start
BEGIN
    SELECT RAISE(ABORT, 'repair_case_run_start_no_delete');
END;

CREATE TRIGGER repair_run_receipt_immutable
BEFORE UPDATE OF receipt_id, campaign_id, attempt_id, run_kind,
    semantic_case_id, invocation_id, token_hash, nonce_hash, code_sha256,
    cases_json, results_json, exit_code, collected, assertion_failures, errors,
    skips, collection_errors, invocation_parent_id, receipt_sha256, created_at
ON repair_run_receipt
BEGIN
    SELECT RAISE(ABORT, 'repair_run_receipt_immutable');
END;

CREATE TRIGGER repair_run_receipt_consumption_once
BEFORE UPDATE OF consumed_at ON repair_run_receipt
WHEN OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL
    OR maintenance_repair_runner_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'repair_run_receipt_consumption_once');
END;

CREATE TRIGGER repair_run_receipt_no_delete
BEFORE DELETE ON repair_run_receipt
BEGIN
    SELECT RAISE(ABORT, 'repair_run_receipt_no_delete');
END;

CREATE TRIGGER repair_review_receipt_immutable
BEFORE UPDATE OF receipt_id, job_id, attempt_id, result_version, author_id,
    reviewer_id, author_role, reviewer_role, host_receipt_id,
    reviewed_output_sha256, code_sha256, patch_sha256, test_ledger_sha256,
    receipt_sha256, created_at ON repair_review_receipt
BEGIN
    SELECT RAISE(ABORT, 'repair_review_receipt_immutable');
END;

CREATE TRIGGER repair_review_receipt_consumption_once
BEFORE UPDATE OF consumed_at ON repair_review_receipt
WHEN OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL
    OR maintenance_repair_review_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'repair_review_receipt_consumption_once');
END;

CREATE TRIGGER repair_review_receipt_no_delete
BEFORE DELETE ON repair_review_receipt
BEGIN
    SELECT RAISE(ABORT, 'repair_review_receipt_no_delete');
END;
