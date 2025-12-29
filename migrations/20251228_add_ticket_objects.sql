CREATE TABLE IF NOT EXISTS ticket_objects (
    id BIGSERIAL PRIMARY KEY,
    ticket_id BIGINT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    object_type TEXT NOT NULL CHECK (object_type IN ('customer', 'supplier')),
    object_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticket_id, object_type, object_id)
);

CREATE INDEX IF NOT EXISTS idx_ticket_objects_object_lookup
    ON ticket_objects(object_type, object_id);
CREATE INDEX IF NOT EXISTS idx_ticket_objects_ticket_id
    ON ticket_objects(ticket_id);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'tickets'
          AND column_name = 'customer_id'
    ) THEN
        INSERT INTO ticket_objects (ticket_id, object_type, object_id, created_at, updated_at)
        SELECT id AS ticket_id,
               'customer' AS object_type,
               customer_id AS object_id,
               CURRENT_TIMESTAMP,
               CURRENT_TIMESTAMP
        FROM tickets
        WHERE customer_id IS NOT NULL
        ON CONFLICT (ticket_id, object_type, object_id) DO NOTHING;
    END IF;
END
$$;
