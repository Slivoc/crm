ALTER TABLE ticket_workspaces ADD COLUMN IF NOT EXISTS workspace_key TEXT;
ALTER TABLE ticket_workspaces ADD COLUMN IF NOT EXISTS workspace_uuid TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_ticket_workspaces_workspace_key ON ticket_workspaces(workspace_key);
