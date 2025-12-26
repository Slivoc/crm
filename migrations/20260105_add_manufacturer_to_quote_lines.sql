-- Add manufacturer text columns to supplier and customer quote lines

ALTER TABLE parts_list_supplier_quote_lines
ADD COLUMN manufacturer TEXT;

ALTER TABLE customer_quote_lines
ADD COLUMN manufacturer TEXT;
