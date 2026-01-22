-- Link project_parts_list_lines to parts_list_lines for per-line status lookups.

ALTER TABLE project_parts_list_lines
    ADD COLUMN IF NOT EXISTS parts_list_line_id INTEGER REFERENCES parts_list_lines(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_project_parts_list_lines_parts_list_line_id
    ON project_parts_list_lines(parts_list_line_id);
