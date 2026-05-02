ALTER TABLE portal_quote_request_lines
ADD COLUMN IF NOT EXISTS submitted_target_price_gbp NUMERIC;

CREATE TABLE IF NOT EXISTS portal_saved_boms (
    id SERIAL PRIMARY KEY,
    portal_user_id INTEGER NOT NULL REFERENCES portal_users(id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    search_history_id INTEGER REFERENCES portal_search_history(id) ON DELETE SET NULL,
    bom_header_id INTEGER NOT NULL REFERENCES bom_headers(id) ON DELETE CASCADE,
    bom_name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_portal_saved_boms_portal_user_created
    ON portal_saved_boms(portal_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_portal_saved_boms_search_history
    ON portal_saved_boms(search_history_id);
