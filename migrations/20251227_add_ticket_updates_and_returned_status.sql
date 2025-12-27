CREATE TABLE IF NOT EXISTS ticket_updates (
    id BIGSERIAL PRIMARY KEY,
    ticket_id BIGINT NOT NULL REFERENCES tickets(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    update_text TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ticket_updates_ticket_id ON ticket_updates(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_updates_created_at ON ticket_updates(created_at);

INSERT INTO ticket_statuses (name, is_closed, sort_order)
VALUES
    ('Returned', FALSE, 4)
ON CONFLICT (name) DO NOTHING;
