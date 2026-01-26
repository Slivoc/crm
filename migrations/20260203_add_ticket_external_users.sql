ALTER TABLE tickets ADD COLUMN IF NOT EXISTS external_assignee_id TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS external_assignee_name TEXT;

CREATE TABLE IF NOT EXISTS external_ticket_users (
    id BIGSERIAL PRIMARY KEY,
    external_instance_id TEXT NOT NULL,
    external_workspace_uuid TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (external_instance_id, external_workspace_uuid, external_user_id)
);

CREATE INDEX IF NOT EXISTS idx_external_ticket_users_workspace
    ON external_ticket_users(external_instance_id, external_workspace_uuid);
