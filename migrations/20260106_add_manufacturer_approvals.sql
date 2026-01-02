CREATE TABLE IF NOT EXISTS manufacturer_approval_imports (
    id BIGSERIAL PRIMARY KEY,
    source_file TEXT,
    imported_by TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    row_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS manufacturer_approvals (
    id BIGSERIAL PRIMARY KEY,
    import_id BIGINT REFERENCES manufacturer_approval_imports(id) ON DELETE SET NULL,
    manufacturer_code TEXT,
    manufacturer_name TEXT NOT NULL,
    location TEXT,
    country TEXT,
    cage_code TEXT,
    approval_status TEXT,
    data_type TEXT,
    standard TEXT,
    airbus_material TEXT,
    airbus_material_text TEXT,
    interchangeability_flag TEXT,
    manufacturer_part_number TEXT,
    usage_restriction TEXT,
    p_status TEXT,
    p_status_text TEXT,
    status_change_date DATE,
    qir_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_manufacturer_approvals_unique_key
    ON manufacturer_approvals (airbus_material, manufacturer_part_number, manufacturer_name, cage_code, location);

CREATE INDEX IF NOT EXISTS idx_manufacturer_approvals_airbus_material
    ON manufacturer_approvals (airbus_material);

CREATE INDEX IF NOT EXISTS idx_manufacturer_approvals_mpn
    ON manufacturer_approvals (manufacturer_part_number);

CREATE INDEX IF NOT EXISTS idx_manufacturer_approvals_cage_code
    ON manufacturer_approvals (cage_code);

CREATE INDEX IF NOT EXISTS idx_manufacturer_approvals_manufacturer_name
    ON manufacturer_approvals (manufacturer_name);
