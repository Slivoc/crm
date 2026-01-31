-- Add Airbus Marketplace fields to part_numbers table
-- These fields allow customization of marketplace export per part

-- Product description for marketplace (defaults to part description if null)
ALTER TABLE part_numbers ADD COLUMN mkp_description TEXT;

-- Product name for marketplace (defaults to part_number if null)
ALTER TABLE part_numbers ADD COLUMN mkp_name TEXT;

-- Product summary for marketplace
ALTER TABLE part_numbers ADD COLUMN mkp_product_summary TEXT;

-- Product presentation/long description for marketplace
ALTER TABLE part_numbers ADD COLUMN mkp_product_presentation TEXT;

-- Unit of measure (EA, KG, M, etc.) - defaults to EA
ALTER TABLE part_numbers ADD COLUMN mkp_product_unit TEXT DEFAULT 'EA';

-- Package content quantity - defaults to 1
ALTER TABLE part_numbers ADD COLUMN mkp_package_content INTEGER DEFAULT 1;

-- Package content unit - defaults to EA
ALTER TABLE part_numbers ADD COLUMN mkp_package_content_unit TEXT DEFAULT 'EA';

-- Third level category/unit
ALTER TABLE part_numbers ADD COLUMN mkp_third_level TEXT DEFAULT 'EA';

-- Is the product dangerous goods (true/false)
ALTER TABLE part_numbers ADD COLUMN mkp_dangerous BOOLEAN DEFAULT FALSE;

-- Export Control Classification Number
ALTER TABLE part_numbers ADD COLUMN mkp_eccn TEXT;

-- Is the product serialized (true/false)
ALTER TABLE part_numbers ADD COLUMN mkp_serialized BOOLEAN DEFAULT FALSE;

-- Does product have log card requirement (true/false)
ALTER TABLE part_numbers ADD COLUMN mkp_log_card BOOLEAN DEFAULT FALSE;

-- EASA Form 1 requirement (true/false)
ALTER TABLE part_numbers ADD COLUMN mkp_easaf1 BOOLEAN DEFAULT FALSE;
