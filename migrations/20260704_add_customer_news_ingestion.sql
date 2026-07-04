-- Customer news ingestion: global RSS/GDELT collection, dedupe, and local matching.

CREATE TABLE IF NOT EXISTS news_sources (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('rss', 'gdelt_query', 'webpage', 'company_newsroom')),
    url TEXT,
    query TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    check_frequency_minutes INTEGER NOT NULL DEFAULT 1440,
    last_checked_at TIMESTAMP,
    last_success_at TIMESTAMP,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    sector_tag TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_news_sources_type_name
ON news_sources(source_type, name);

CREATE TABLE IF NOT EXISTS news_articles (
    id SERIAL PRIMARY KEY,
    source_id INTEGER REFERENCES news_sources(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    source_domain TEXT,
    source_name TEXT,
    published_at TIMESTAMP,
    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    summary_raw TEXT,
    body_excerpt TEXT,
    language TEXT,
    country TEXT,
    content_hash VARCHAR(64),
    title_hash VARCHAR(64),
    duplicate_of_article_id INTEGER REFERENCES news_articles(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_news_articles_canonical_url
ON news_articles(canonical_url);

CREATE INDEX IF NOT EXISTS idx_news_articles_published_at
ON news_articles(published_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_articles_title_hash
ON news_articles(title_hash);

CREATE TABLE IF NOT EXISTS customer_aliases (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'manual',
    weight INTEGER NOT NULL DEFAULT 80,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(customer_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_customer_aliases_alias
ON customer_aliases(alias);

CREATE TABLE IF NOT EXISTS article_customer_mentions (
    id SERIAL PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    matched_alias TEXT,
    match_type TEXT NOT NULL DEFAULT 'exact',
    confidence INTEGER NOT NULL DEFAULT 0,
    relevance_score INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(article_id, customer_id, matched_alias)
);

CREATE INDEX IF NOT EXISTS idx_article_customer_mentions_customer
ON article_customer_mentions(customer_id, relevance_score DESC);

CREATE TABLE IF NOT EXISTS article_platform_mentions (
    id SERIAL PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    confidence INTEGER NOT NULL DEFAULT 80,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(article_id, platform)
);

CREATE TABLE IF NOT EXISTS news_ai_summaries (
    id SERIAL PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
    model_provider TEXT NOT NULL,
    summary TEXT,
    commercial_angle TEXT,
    suggested_action TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(article_id, customer_id, model_provider)
);

CREATE TABLE IF NOT EXISTS news_feedback (
    id SERIAL PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    feedback_type TEXT NOT NULL CHECK (feedback_type IN ('relevant', 'not_relevant', 'duplicate', 'wrong_company', 'good_lead')),
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
