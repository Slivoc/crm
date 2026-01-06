CREATE TABLE IF NOT EXISTS graph_email_sync_status (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    last_sync_at TIMESTAMP,
    last_sync_success BOOLEAN NOT NULL DEFAULT FALSE,
    last_sync_error TEXT,
    delta_link TEXT,
    total_cached_messages INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_graph_email_sync_status_last_sync_at ON graph_email_sync_status(last_sync_at);
