-- Add project-level parts list lines and categories for parts list lines.

ALTER TABLE parts_list_lines
    ADD COLUMN IF NOT EXISTS category TEXT;

CREATE TABLE IF NOT EXISTS project_parts_list_lines (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL,
    line_number NUMERIC(10,2) NOT NULL,
    customer_part_number TEXT NOT NULL,
    description TEXT,
    category TEXT,
    comment TEXT,
    line_type TEXT NOT NULL DEFAULT 'normal',
    total_quantity INTEGER,
    usage_by_year TEXT,
    date_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    date_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'project_parts_list_lines_line_type_check'
    ) THEN
        ALTER TABLE project_parts_list_lines
            ADD CONSTRAINT project_parts_list_lines_line_type_check
            CHECK (line_type IN ('normal', 'price_break', 'alternate'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_project_parts_list_lines_project_id
    ON project_parts_list_lines(project_id);
