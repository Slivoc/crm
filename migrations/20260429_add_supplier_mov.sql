-- Add minimum order value (MOV) support for suppliers
ALTER TABLE suppliers
    ADD COLUMN IF NOT EXISTS mov NUMERIC(12,2);

