CREATE TABLE IF NOT EXISTS market_intelligence_cache (
    tag_id INTEGER PRIMARY KEY REFERENCES industry_tags(id),
    analysis TEXT,
    suggestions JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_market_intelligence_cache_updated_at
    ON market_intelligence_cache(updated_at);
