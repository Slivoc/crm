CREATE TABLE portal_access_requests (
    id SERIAL PRIMARY KEY,
    company_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    notes TEXT,
    status TEXT DEFAULT 'pending',
    internal_notes TEXT,
    processed_by_user_id INTEGER,
    processed_at TIMESTAMP,
    date_submitted TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT portal_access_requests_status_check
        CHECK (status IN ('pending', 'reviewed', 'approved', 'rejected'))
);

CREATE INDEX idx_portal_access_requests_email ON portal_access_requests (email);
CREATE INDEX idx_portal_access_requests_status ON portal_access_requests (status);
