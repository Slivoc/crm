CREATE INDEX IF NOT EXISTS idx_contact_comms_contact_date
ON contact_communications(contact_id, date);

CREATE INDEX IF NOT EXISTS idx_call_list_salesperson_active
ON call_list(salesperson_id, is_active);
