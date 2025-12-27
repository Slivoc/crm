ALTER TABLE contact_communications ADD COLUMN IF NOT EXISTS email_message_id TEXT;
ALTER TABLE contact_communications ADD COLUMN IF NOT EXISTS email_direction TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_contact_communications_email_message_contact
ON contact_communications(email_message_id, contact_id);
