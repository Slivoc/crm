-- Add normalized base_part_number columns to manufacturer_approvals for searching
ALTER TABLE manufacturer_approvals
ADD COLUMN airbus_material_base TEXT,
ADD COLUMN manufacturer_part_number_base TEXT;

-- Create indexes on normalized columns for fast lookups
CREATE INDEX IF NOT EXISTS idx_manufacturer_approvals_airbus_material_base
    ON manufacturer_approvals (airbus_material_base);

CREATE INDEX IF NOT EXISTS idx_manufacturer_approvals_mpn_base
    ON manufacturer_approvals (manufacturer_part_number_base);

-- Function to normalize part numbers (remove special characters, convert to uppercase)
CREATE OR REPLACE FUNCTION normalize_part_number(part_number TEXT)
RETURNS TEXT AS $$
BEGIN
    IF part_number IS NULL THEN
        RETURN NULL;
    END IF;
    -- Remove dashes, spaces, and other special characters, convert to uppercase
    RETURN REGEXP_REPLACE(UPPER(part_number), '[^A-Z0-9]', '', 'g');
END;
$$ LANGUAGE plpgsql IMMUTABLE;
