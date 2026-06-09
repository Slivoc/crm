CREATE TABLE IF NOT EXISTS purchase_report_runs (
    id SERIAL PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    generated_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    recipients TEXT,
    unordered_quote_count INTEGER NOT NULL DEFAULT 0,
    frequent_sales_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS purchase_report_run_items (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES purchase_report_runs(id) ON DELETE CASCADE,
    report_section TEXT NOT NULL,
    base_part_number TEXT NOT NULL,
    display_part_number TEXT,
    item_order INTEGER NOT NULL DEFAULT 0,
    item_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_purchase_report_run_items_run
    ON purchase_report_run_items(run_id, report_section, item_order);

CREATE INDEX IF NOT EXISTS idx_purchase_report_run_items_part
    ON purchase_report_run_items(report_section, base_part_number);

CREATE TABLE IF NOT EXISTS purchase_report_comments (
    id SERIAL PRIMARY KEY,
    report_section TEXT NOT NULL,
    base_part_number TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    user_name TEXT NOT NULL,
    comment TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(report_section, base_part_number, user_id)
);

CREATE INDEX IF NOT EXISTS idx_purchase_report_comments_part
    ON purchase_report_comments(report_section, base_part_number);
