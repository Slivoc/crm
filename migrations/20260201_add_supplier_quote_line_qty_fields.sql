ALTER TABLE parts_list_supplier_quote_lines
ADD COLUMN qty_available INTEGER;

ALTER TABLE parts_list_supplier_quote_lines
ADD COLUMN purchase_increment INTEGER;

ALTER TABLE parts_list_supplier_quote_lines
ADD COLUMN moq INTEGER;

ALTER TABLE monroe_search_results
ADD COLUMN purchase_increment INTEGER;
