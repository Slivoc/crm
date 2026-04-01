ALTER TABLE parts_lists
    ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_parts_lists_is_pinned
    ON parts_lists (is_pinned, date_modified DESC);
