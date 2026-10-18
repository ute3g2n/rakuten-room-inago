ALTER TABLE deployment ADD COLUMN source_commit TEXT;
ALTER TABLE deployment ADD COLUMN base_commit TEXT;
ALTER TABLE deployment ADD COLUMN tested_commit TEXT;
ALTER TABLE deployment ADD COLUMN pushed_commit TEXT;
ALTER TABLE deployment ADD COLUMN active_commit TEXT;
ALTER TABLE deployment ADD COLUMN remote_name TEXT;
ALTER TABLE deployment ADD COLUMN target_branch TEXT;
ALTER TABLE deployment ADD COLUMN reason_code TEXT;

CREATE TRIGGER deployment_identity_insert_valid
BEFORE INSERT ON deployment
WHEN NEW.source_commit IS NOT NULL
    AND (
        NEW.source_commit <> NEW.result_commit
        OR NEW.tested_commit <> NEW.result_commit
        OR NEW.base_commit IS NULL
        OR NEW.remote_name IS NULL
        OR NEW.target_branch IS NULL
    )
BEGIN
    SELECT RAISE(ABORT, 'deployment_identity_invalid');
END;

CREATE TRIGGER deployment_identity_immutable
BEFORE UPDATE OF source_commit, base_commit, tested_commit, remote_name, target_branch
ON deployment
WHEN OLD.source_commit IS NOT NEW.source_commit
    OR OLD.base_commit IS NOT NEW.base_commit
    OR OLD.tested_commit IS NOT NEW.tested_commit
    OR OLD.remote_name IS NOT NEW.remote_name
    OR OLD.target_branch IS NOT NEW.target_branch
BEGIN
    SELECT RAISE(ABORT, 'deployment_identity_immutable');
END;

CREATE TRIGGER deployment_success_exact_version
BEFORE INSERT ON deployment
WHEN NEW.status = 'deployed'
    AND (
        NEW.source_commit IS NULL
        OR NEW.tested_commit <> NEW.result_commit
        OR NEW.pushed_commit <> NEW.result_commit
        OR NEW.active_commit <> NEW.result_commit
        OR NEW.base_commit IS NULL
        OR NEW.remote_name IS NULL
        OR NEW.target_branch IS NULL
    )
BEGIN
    SELECT RAISE(ABORT, 'deployment_success_identity_invalid');
END;

CREATE TRIGGER deployment_success_insert_host_only
BEFORE INSERT ON deployment
WHEN NEW.status = 'deployed'
    AND maintenance_deployment_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'deployment_capability_invalid');
END;

CREATE TRIGGER deployment_success_exact_version_update
BEFORE UPDATE OF status, pushed_commit, active_commit ON deployment
WHEN NEW.status = 'deployed'
    AND (
        NEW.source_commit IS NULL
        OR NEW.tested_commit <> NEW.result_commit
        OR NEW.pushed_commit <> NEW.result_commit
        OR NEW.active_commit <> NEW.result_commit
        OR NEW.base_commit IS NULL
        OR NEW.remote_name IS NULL
        OR NEW.target_branch IS NULL
    )
BEGIN
    SELECT RAISE(ABORT, 'deployment_success_identity_invalid');
END;

CREATE TRIGGER deployment_success_update_host_only
BEFORE UPDATE OF status ON deployment
WHEN NEW.status = 'deployed'
    AND maintenance_deployment_authorized() <> 1
BEGIN
    SELECT RAISE(ABORT, 'deployment_capability_invalid');
END;

CREATE TRIGGER deployment_final_state_once
BEFORE UPDATE OF status ON deployment
WHEN OLD.status <> 'pending' OR NEW.status = 'pending'
BEGIN
    SELECT RAISE(ABORT, 'deployment_final_state_immutable');
END;
