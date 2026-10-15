ALTER TABLE human_action ADD COLUMN correction_action_ref TEXT
    REFERENCES human_action(action_id);
