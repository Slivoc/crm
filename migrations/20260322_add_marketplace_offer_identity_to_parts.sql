ALTER TABLE part_numbers ADD COLUMN mkp_offer_product_id TEXT;
ALTER TABLE part_numbers ADD COLUMN mkp_offer_product_id_type TEXT DEFAULT 'SKU';
