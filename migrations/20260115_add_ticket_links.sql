CREATE TABLE IF NOT EXISTS ticket_links (
    id BIGSERIAL PRIMARY KEY,
    ticket_id BIGINT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    link_type TEXT NOT NULL CHECK (link_type IN ('customer', 'supplier')),
    object_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ticket_id, link_type, object_id)
);

CREATE INDEX IF NOT EXISTS idx_ticket_links_ticket_id ON ticket_links(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_links_type_object ON ticket_links(link_type, object_id);
