CREATE TABLE IF NOT EXISTS graph_email_cache (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    message_id TEXT NOT NULL,
    conversation_id TEXT,
    subject TEXT,
    sender_name TEXT,
    sender_email TEXT,
    received_datetime TIMESTAMP,
    sent_datetime TIMESTAMP,
    body_preview TEXT,
    web_link TEXT,
    from_data JSONB,
    to_recipients JSONB,
    cc_recipients JSONB,
    has_attachments BOOLEAN,
    is_read BOOLEAN,
    importance TEXT,
    raw_message JSONB,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_graph_email_cache_user_id ON graph_email_cache(user_id);
CREATE INDEX IF NOT EXISTS idx_graph_email_cache_received_datetime ON graph_email_cache(received_datetime);
CREATE INDEX IF NOT EXISTS idx_graph_email_cache_conversation_id ON graph_email_cache(conversation_id);
