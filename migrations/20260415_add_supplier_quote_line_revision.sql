ALTER TABLE parts_list_supplier_quote_lines
ADD COLUMN IF NOT EXISTS revision TEXT;
