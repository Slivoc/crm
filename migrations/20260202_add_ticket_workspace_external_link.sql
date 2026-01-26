ALTER TABLE ticket_workspaces ADD COLUMN IF NOT EXISTS is_external BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE ticket_workspaces ADD COLUMN IF NOT EXISTS external_instance_id TEXT;
ALTER TABLE ticket_workspaces ADD COLUMN IF NOT EXISTS external_base_url TEXT;
ALTER TABLE ticket_workspaces ADD COLUMN IF NOT EXISTS external_workspace_uuid TEXT;
ALTER TABLE ticket_workspaces ADD COLUMN IF NOT EXISTS external_workspace_key TEXT;
