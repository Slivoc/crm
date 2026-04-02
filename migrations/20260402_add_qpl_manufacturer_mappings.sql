CREATE TABLE IF NOT EXISTS qpl_manufacturer_mappings (
    id SERIAL PRIMARY KEY,
    qpl_manufacturer_name TEXT NOT NULL,
    qpl_manufacturer_name_normalized TEXT NOT NULL,
    manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_qpl_manufacturer_mappings_normalized
    ON qpl_manufacturer_mappings (qpl_manufacturer_name_normalized);

CREATE INDEX IF NOT EXISTS idx_qpl_manufacturer_mappings_manufacturer
    ON qpl_manufacturer_mappings (manufacturer_id);
