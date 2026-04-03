CREATE TABLE IF NOT EXISTS qpl_supplier_prefix_instructions (
    id SERIAL PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    manufacturer_name TEXT NOT NULL,
    manufacturer_name_normalized TEXT NOT NULL,
    prefix TEXT NOT NULL,
    prefix_length INTEGER NOT NULL,
    instruction_text TEXT NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_qpl_supplier_prefix_instructions_rule
    ON qpl_supplier_prefix_instructions (supplier_id, manufacturer_name_normalized, prefix, prefix_length);

CREATE INDEX IF NOT EXISTS idx_qpl_supplier_prefix_instructions_supplier
    ON qpl_supplier_prefix_instructions (supplier_id);

CREATE INDEX IF NOT EXISTS idx_qpl_supplier_prefix_instructions_prefix
    ON qpl_supplier_prefix_instructions (prefix, prefix_length);
