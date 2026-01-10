-- Track email scan status per contact
CREATE TABLE IF NOT EXISTS contact_email_scan_status (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    contact_email TEXT NOT NULL,
    last_scan_at TIMESTAMP,
    last_scan_success BOOLEAN DEFAULT TRUE,
    last_scan_error TEXT,
    total_emails_found INTEGER DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, contact_email)
);

CREATE INDEX IF NOT EXISTS idx_contact_email_scan_user ON contact_email_scan_status(user_id);
CREATE INDEX IF NOT EXISTS idx_contact_email_scan_contact ON contact_email_scan_status(contact_email);
