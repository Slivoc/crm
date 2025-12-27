CREATE TABLE IF NOT EXISTS graph_mailbox_deltas (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    delta_link TEXT,
    mailbox_email TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
