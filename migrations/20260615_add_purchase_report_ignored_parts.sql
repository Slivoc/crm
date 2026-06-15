CREATE TABLE IF NOT EXISTS purchase_report_ignored_parts (
    id SERIAL PRIMARY KEY,
    base_part_number TEXT NOT NULL,
    display_part_number TEXT,
    ignored_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ignored_by_user_name TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(base_part_number)
);

CREATE INDEX IF NOT EXISTS idx_purchase_report_ignored_parts_base
    ON purchase_report_ignored_parts(base_part_number);
