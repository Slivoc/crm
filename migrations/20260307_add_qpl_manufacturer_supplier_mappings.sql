CREATE TABLE IF NOT EXISTS qpl_manufacturer_supplier_mappings (
    id SERIAL PRIMARY KEY,
    manufacturer_name TEXT NOT NULL,
    manufacturer_name_normalized TEXT NOT NULL,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_qpl_manufacturer_supplier_mappings_normalized
    ON qpl_manufacturer_supplier_mappings (manufacturer_name_normalized);

CREATE INDEX IF NOT EXISTS idx_qpl_manufacturer_supplier_mappings_supplier
    ON qpl_manufacturer_supplier_mappings (supplier_id);
