-- Add table to track active scraping sessions
CREATE TABLE IF NOT EXISTS supplier_scrape_status (
    id SERIAL PRIMARY KEY,
    supplier_key TEXT NOT NULL,  -- 'monroe', etc.
    parts_list_id INTEGER REFERENCES parts_lists(id),
    parts_list_name TEXT,
    status TEXT NOT NULL,  -- 'queued', 'in_progress', 'completed', 'failed'
    total_lines INTEGER DEFAULT 0,
    processed_lines INTEGER DEFAULT 0,
    successful_lines INTEGER DEFAULT 0,
    failed_lines INTEGER DEFAULT 0,
    current_part_number TEXT,
    error_message TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    triggered_by_user_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_supplier_scrape_status_supplier
ON supplier_scrape_status(supplier_key, status);

CREATE INDEX IF NOT EXISTS idx_supplier_scrape_status_started
ON supplier_scrape_status(started_at DESC);
