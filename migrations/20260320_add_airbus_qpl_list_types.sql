ALTER TABLE manufacturer_approval_imports
    ADD COLUMN IF NOT EXISTS approval_list_type TEXT NOT NULL DEFAULT 'airbus_fixed_wing',
    ADD COLUMN IF NOT EXISTS source_file_count INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS source_files_json JSONB,
    ADD COLUMN IF NOT EXISTS overwrite_existing BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE manufacturer_approvals
    ADD COLUMN IF NOT EXISTS approval_list_type TEXT NOT NULL DEFAULT 'airbus_fixed_wing';

UPDATE manufacturer_approval_imports
SET approval_list_type = 'airbus_fixed_wing'
WHERE approval_list_type IS NULL OR approval_list_type = '';

UPDATE manufacturer_approvals
SET approval_list_type = 'airbus_fixed_wing'
WHERE approval_list_type IS NULL OR approval_list_type = '';

DROP INDEX IF EXISTS idx_manufacturer_approvals_unique_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_manufacturer_approvals_unique_key
    ON manufacturer_approvals (
        approval_list_type,
        airbus_material,
        manufacturer_part_number,
        manufacturer_name,
        cage_code,
        location
    );

CREATE INDEX IF NOT EXISTS idx_manufacturer_approvals_list_type
    ON manufacturer_approvals (approval_list_type);

CREATE INDEX IF NOT EXISTS idx_manufacturer_approvals_list_type_airbus_material_base
    ON manufacturer_approvals (approval_list_type, airbus_material_base);

CREATE INDEX IF NOT EXISTS idx_manufacturer_approvals_list_type_mpn_base
    ON manufacturer_approvals (approval_list_type, manufacturer_part_number_base);

CREATE INDEX IF NOT EXISTS idx_manufacturer_approval_imports_list_type_imported_at
    ON manufacturer_approval_imports (approval_list_type, imported_at DESC);
