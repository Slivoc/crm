-- Support nested kits (kit lines that reference another BOM header)
ALTER TABLE bom_lines
    ADD COLUMN IF NOT EXISTS child_bom_header_id INTEGER REFERENCES bom_headers(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_bom_lines_child_bom_header_id
    ON bom_lines(child_bom_header_id);

-- Store accepted alternates per BOM line without duplicating BOM rows
CREATE TABLE IF NOT EXISTS bom_line_accepted_alternates (
    id SERIAL PRIMARY KEY,
    bom_line_id INTEGER NOT NULL REFERENCES bom_lines(id) ON DELETE CASCADE,
    alt_base_part_number TEXT NOT NULL REFERENCES part_numbers(base_part_number),
    preference_rank INTEGER NOT NULL DEFAULT 100,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (bom_line_id, alt_base_part_number)
);

CREATE INDEX IF NOT EXISTS idx_bom_line_accepted_alternates_line_rank
    ON bom_line_accepted_alternates(bom_line_id, preference_rank, id);
