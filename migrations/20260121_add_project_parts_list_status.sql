-- Add status column to project_parts_list_lines for tracking no_bid/ignore status
-- Also add parts_list_id to link back to created parts lists

ALTER TABLE project_parts_list_lines
    ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending';

ALTER TABLE project_parts_list_lines
    ADD COLUMN IF NOT EXISTS parts_list_id INTEGER REFERENCES parts_lists(id) ON DELETE SET NULL;

-- Add constraint for valid status values (PostgreSQL)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'project_parts_list_lines_status_check'
    ) THEN
        ALTER TABLE project_parts_list_lines
            ADD CONSTRAINT project_parts_list_lines_status_check
            CHECK (status IN ('pending', 'linked', 'no_bid', 'ignore'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_project_parts_list_lines_status
    ON project_parts_list_lines(status);

CREATE INDEX IF NOT EXISTS idx_project_parts_list_lines_parts_list_id
    ON project_parts_list_lines(parts_list_id);
