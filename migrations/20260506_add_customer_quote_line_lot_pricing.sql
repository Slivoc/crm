ALTER TABLE customer_quote_lines
ADD COLUMN IF NOT EXISTS lot_pricing_enabled BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE customer_quote_lines
ADD COLUMN IF NOT EXISTS lot_purchase_multiple NUMERIC;

ALTER TABLE customer_quote_lines
ADD COLUMN IF NOT EXISTS lot_purchase_quantity NUMERIC;

ALTER TABLE customer_quote_lines
ADD COLUMN IF NOT EXISTS lot_source_unit_cost_gbp NUMERIC;

ALTER TABLE customer_quote_lines
ADD COLUMN IF NOT EXISTS lot_total_cost_gbp NUMERIC;
