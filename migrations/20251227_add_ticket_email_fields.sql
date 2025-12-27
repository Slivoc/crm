-- Add email fields to tickets table for email-to-ticket integration
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS email_message_id TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS email_conversation_id TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS email_from TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS email_subject TEXT;

-- Index for looking up tickets by email conversation
CREATE INDEX IF NOT EXISTS idx_tickets_email_conversation_id ON tickets(email_conversation_id);
CREATE INDEX IF NOT EXISTS idx_tickets_email_message_id ON tickets(email_message_id);
