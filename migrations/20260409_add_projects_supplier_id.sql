ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_projects_supplier_id
    ON projects(supplier_id);
