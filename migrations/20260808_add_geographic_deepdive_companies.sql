CREATE TABLE IF NOT EXISTS geographic_deepdive_companies (
    id BIGSERIAL PRIMARY KEY,
    deepdive_id INTEGER NOT NULL REFERENCES geographic_deepdives(id) ON DELETE CASCADE,
    company_name VARCHAR(300) NOT NULL,
    normalized_name VARCHAR(300) NOT NULL,
    company_type VARCHAR(120) NOT NULL DEFAULT '',
    role_summary TEXT NOT NULL DEFAULT '',
    why_relevant TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    country VARCHAR(120) NOT NULL DEFAULT '',
    source_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    mention_sections JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_main BOOLEAN NOT NULL DEFAULT FALSE,
    display_order INTEGER NOT NULL DEFAULT 0,
    matched_customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    match_confidence NUMERIC(5, 4),
    match_method VARCHAR(40) NOT NULL DEFAULT '',
    match_status VARCHAR(20) NOT NULL DEFAULT 'unmatched'
        CHECK (match_status IN ('matched', 'suggested', 'unmatched', 'confirmed', 'rejected')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (deepdive_id, normalized_name)
);

CREATE INDEX IF NOT EXISTS idx_geographic_deepdive_companies_deepdive
    ON geographic_deepdive_companies (deepdive_id, is_main DESC, display_order);

CREATE INDEX IF NOT EXISTS idx_geographic_deepdive_companies_customer
    ON geographic_deepdive_companies (matched_customer_id)
    WHERE matched_customer_id IS NOT NULL;

COMMENT ON TABLE geographic_deepdive_companies IS
    'Structured companies mentioned by Perplexity in a geographic deep dive, with conservative CRM customer matching.';
