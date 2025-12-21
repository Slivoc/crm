-- Speed up ILS supplier mapping lookups and result aggregations (Postgres-friendly).
CREATE INDEX IF NOT EXISTS idx_ils_supplier_mappings_company_lower
    ON ils_supplier_mappings (LOWER(ils_company_name));

CREATE INDEX IF NOT EXISTS idx_ils_search_results_company
    ON ils_search_results (ils_company_name);
