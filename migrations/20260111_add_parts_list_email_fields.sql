ALTER TABLE parts_lists ADD COLUMN IF NOT EXISTS email_message_id TEXT;
ALTER TABLE parts_lists ADD COLUMN IF NOT EXISTS email_conversation_id TEXT;

CREATE INDEX IF NOT EXISTS idx_parts_lists_email_message_id ON parts_lists(email_message_id);
CREATE INDEX IF NOT EXISTS idx_parts_lists_email_conversation_id ON parts_lists(email_conversation_id);
