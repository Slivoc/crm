CREATE TABLE IF NOT EXISTS portal_search_history_lines (
    id SERIAL PRIMARY KEY,
    search_history_id INTEGER NOT NULL REFERENCES portal_search_history(id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    line_number INTEGER NOT NULL,
    requested_part_number TEXT NOT NULL,
    base_part_number TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    estimated_price NUMERIC,
    estimated_currency TEXT,
    price_source TEXT,
    has_price BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_portal_search_history_lines_search_history_id
    ON portal_search_history_lines(search_history_id);

CREATE INDEX IF NOT EXISTS idx_portal_search_history_lines_customer_base_created
    ON portal_search_history_lines(customer_id, base_part_number, created_at DESC);
