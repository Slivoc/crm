CREATE TABLE IF NOT EXISTS ticket_statuses (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_closed BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tickets (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    status_id INTEGER NOT NULL REFERENCES ticket_statuses(id),
    assigned_user_id INTEGER REFERENCES users(id),
    created_by_user_id INTEGER NOT NULL REFERENCES users(id),
    due_date DATE,
    is_private BOOLEAN NOT NULL DEFAULT FALSE,
    parent_ticket_id BIGINT REFERENCES tickets(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tickets_status_id ON tickets(status_id);
CREATE INDEX IF NOT EXISTS idx_tickets_assigned_user_id ON tickets(assigned_user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_created_by_user_id ON tickets(created_by_user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_parent_ticket_id ON tickets(parent_ticket_id);
CREATE INDEX IF NOT EXISTS idx_tickets_due_date ON tickets(due_date);

INSERT INTO ticket_statuses (name, is_closed, sort_order)
VALUES
    ('Open', FALSE, 1),
    ('In Progress', FALSE, 2),
    ('Blocked', FALSE, 3),
    ('Closed', TRUE, 99)
ON CONFLICT (name) DO NOTHING;
