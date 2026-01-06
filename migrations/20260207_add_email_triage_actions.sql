CREATE TABLE IF NOT EXISTS email_triage_actions (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    message_id TEXT NOT NULL,
    conversation_id TEXT,
    triage_type TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, message_id, triage_type)
);

CREATE INDEX IF NOT EXISTS idx_email_triage_actions_user_id ON email_triage_actions(user_id);
CREATE INDEX IF NOT EXISTS idx_email_triage_actions_message_id ON email_triage_actions(message_id);
CREATE INDEX IF NOT EXISTS idx_email_triage_actions_triage_type ON email_triage_actions(triage_type);
