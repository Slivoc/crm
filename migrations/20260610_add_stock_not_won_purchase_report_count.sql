ALTER TABLE purchase_report_runs
    ADD COLUMN IF NOT EXISTS stock_not_won_count INTEGER NOT NULL DEFAULT 0;
