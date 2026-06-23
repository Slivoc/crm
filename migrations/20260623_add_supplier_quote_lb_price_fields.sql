ALTER TABLE parts_list_supplier_quote_lines
ADD COLUMN IF NOT EXISTS price_entered_as_lb boolean DEFAULT false;

ALTER TABLE parts_list_supplier_quote_lines
ADD COLUMN IF NOT EXISTS lb_unit_price numeric(15,4);

ALTER TABLE parts_list_supplier_quote_lines
ADD COLUMN IF NOT EXISTS pieces_per_pound_used numeric(15,4);
