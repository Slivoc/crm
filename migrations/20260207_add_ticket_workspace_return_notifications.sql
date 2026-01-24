ALTER TABLE ticket_workspace_members
ADD COLUMN IF NOT EXISTS notify_ticket_returns BOOLEAN NOT NULL DEFAULT TRUE;
