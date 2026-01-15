ALTER TABLE parts_lists
    ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_parts_lists_project_id
    ON parts_lists(project_id);
