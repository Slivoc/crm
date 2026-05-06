ALTER TABLE parts_lists
ADD COLUMN IF NOT EXISTS bom_header_id INTEGER REFERENCES bom_headers(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_parts_lists_bom_header_id
    ON parts_lists(bom_header_id);
