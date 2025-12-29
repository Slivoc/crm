ALTER TABLE ticket_workspaces
ADD COLUMN IF NOT EXISTS default_assignee_id INTEGER REFERENCES users(id);
