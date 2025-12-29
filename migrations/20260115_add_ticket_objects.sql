CREATE TABLE IF NOT EXISTS ticket_objects (
    ticket_id BIGINT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    object_type TEXT NOT NULL CHECK (object_type IN ('customer', 'supplier')),
    object_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticket_id, object_type, object_id)
);

CREATE INDEX IF NOT EXISTS idx_ticket_objects_type_id ON ticket_objects(object_type, object_id);
