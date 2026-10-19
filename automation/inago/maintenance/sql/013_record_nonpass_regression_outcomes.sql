DROP TRIGGER IF EXISTS repair_campaign_result_receipt_bind;

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
          OR receipt.run_kind = 'full_regression'
      )
)
BEGIN
    SELECT RAISE(ABORT, 'repair_campaign_result_receipt_mismatch');
END;

CREATE TABLE maintenance_workflow_stage_observation (
    observation_id TEXT PRIMARY KEY,
    invocation_id TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES maintenance_job(job_id),
    source_launch_id TEXT NOT NULL,
    source_run_id TEXT,
    target_date TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL,
    reason_code TEXT,
    observed_at TEXT NOT NULL,
    UNIQUE (invocation_id, stage_name)
);

CREATE INDEX maintenance_workflow_stage_observation_job_idx
    ON maintenance_workflow_stage_observation(job_id, observed_at, stage_name);
