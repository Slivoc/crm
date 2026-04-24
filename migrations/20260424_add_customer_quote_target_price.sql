ALTER TABLE customer_quote_lines
ADD COLUMN IF NOT EXISTS target_price_gbp NUMERIC;
