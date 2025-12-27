ALTER TABLE excess_stock_lines
ADD COLUMN unit_price NUMERIC;

ALTER TABLE excess_stock_lines
ADD COLUMN unit_price_currency_id INTEGER;
