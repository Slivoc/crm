CREATE TABLE IF NOT EXISTS office_dashboard_customer_focus_summaries (
    customer_id BIGINT PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    similar_companies JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_provider VARCHAR(80) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE office_dashboard_customer_focus_summaries IS
    'Cached Perplexity research used by Customer in Focus office-TV slides.';
