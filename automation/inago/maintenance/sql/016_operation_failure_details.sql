ALTER TABLE maintenance_operation_event
    ADD COLUMN exception_reason_codes_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE maintenance_operation_event
    ADD COLUMN operation_context_json TEXT NOT NULL DEFAULT '{}';
