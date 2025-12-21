CREATE INDEX IF NOT EXISTS idx_plsql_parts_list_line_id
ON parts_list_supplier_quote_lines(parts_list_line_id);

CREATE INDEX IF NOT EXISTS idx_plsql_supplier_quote_id
ON parts_list_supplier_quote_lines(supplier_quote_id);

CREATE INDEX IF NOT EXISTS idx_pll_base_part_number_parts_list_id
ON parts_list_lines(base_part_number, parts_list_id);

CREATE INDEX IF NOT EXISTS idx_plsq_parts_list_id
ON parts_list_supplier_quotes(parts_list_id);

CREATE INDEX IF NOT EXISTS idx_plsq_quote_date
ON parts_list_supplier_quotes(quote_date);
