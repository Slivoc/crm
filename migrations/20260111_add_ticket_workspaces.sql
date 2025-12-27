CREATE TABLE IF NOT EXISTS ticket_workspaces (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ticket_workspace_members (
    workspace_id BIGINT NOT NULL REFERENCES ticket_workspaces(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, user_id)
);

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS workspace_id BIGINT REFERENCES ticket_workspaces(id);

CREATE INDEX IF NOT EXISTS idx_ticket_workspaces_name ON ticket_workspaces(name);
CREATE INDEX IF NOT EXISTS idx_ticket_workspace_members_user_id ON ticket_workspace_members(user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_workspace_id ON tickets(workspace_id);
