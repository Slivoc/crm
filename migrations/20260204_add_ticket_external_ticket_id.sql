ALTER TABLE tickets ADD COLUMN IF NOT EXISTS external_ticket_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_tickets_external_ticket_id ON tickets(external_ticket_id);
