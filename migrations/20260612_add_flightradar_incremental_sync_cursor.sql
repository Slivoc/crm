ALTER TABLE customer_flightradar_links
    ADD COLUMN IF NOT EXISTS activity_sync_cursor_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_activity_sync_window_from TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_activity_sync_window_to TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_customer_flightradar_links_activity_cursor
    ON customer_flightradar_links(is_active, activity_sync_cursor_at, last_activity_sync_at);
