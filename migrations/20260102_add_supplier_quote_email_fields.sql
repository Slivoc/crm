ALTER TABLE parts_list_supplier_quotes ADD COLUMN IF NOT EXISTS email_message_id TEXT;
ALTER TABLE parts_list_supplier_quotes ADD COLUMN IF NOT EXISTS email_conversation_id TEXT;

CREATE INDEX IF NOT EXISTS idx_supplier_quotes_email_message_id ON parts_list_supplier_quotes(email_message_id);
CREATE INDEX IF NOT EXISTS idx_supplier_quotes_email_conversation_id ON parts_list_supplier_quotes(email_conversation_id);
