CREATE TABLE IF NOT EXISTS parts_list_ils_copy_queue (
    id SERIAL PRIMARY KEY,
    parts_list_id INTEGER,
    chunk_type TEXT NOT NULL,
    parts_json TEXT NOT NULL,
    note TEXT,
    created_by_user_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parts_list_id) REFERENCES parts_lists(id) ON DELETE SET NULL,
    FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ils_copy_queue_parts_list_id
    ON parts_list_ils_copy_queue(parts_list_id);

CREATE INDEX IF NOT EXISTS idx_ils_copy_queue_created_at
    ON parts_list_ils_copy_queue(created_at);
