-- Add parent/child support for parts list lines and allow decimal line numbers.

ALTER TABLE parts_list_lines
    ALTER COLUMN line_number TYPE NUMERIC(10,2)
    USING line_number::numeric;

ALTER TABLE parts_list_lines
    ADD COLUMN IF NOT EXISTS parent_line_id INTEGER,
    ADD COLUMN IF NOT EXISTS line_type TEXT NOT NULL DEFAULT 'normal';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'parts_list_lines_parent_line_id_fkey'
    ) THEN
        ALTER TABLE parts_list_lines
            ADD CONSTRAINT parts_list_lines_parent_line_id_fkey
            FOREIGN KEY (parent_line_id)
            REFERENCES parts_list_lines(id)
            ON DELETE CASCADE;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'parts_list_lines_line_type_check'
    ) THEN
        ALTER TABLE parts_list_lines
            ADD CONSTRAINT parts_list_lines_line_type_check
            CHECK (line_type IN ('normal', 'price_break', 'alternate'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_parts_list_lines_parent_line_id
    ON parts_list_lines(parent_line_id);

CREATE INDEX IF NOT EXISTS idx_parts_list_lines_list_parent
    ON parts_list_lines(parts_list_id, parent_line_id);
