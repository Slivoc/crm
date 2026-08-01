CREATE TABLE IF NOT EXISTS news_editorial_reviews (
    article_id INTEGER PRIMARY KEY REFERENCES news_articles(id) ON DELETE CASCADE,
    tv_recommended BOOLEAN NOT NULL,
    email_recommended BOOLEAN NOT NULL,
    editorial_score INTEGER NOT NULL CHECK (editorial_score BETWEEN 0 AND 100),
    event_key VARCHAR(240),
    reasoning TEXT NOT NULL,
    model_provider VARCHAR(40) NOT NULL DEFAULT 'openai',
    model_name VARCHAR(120) NOT NULL,
    reviewed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_news_editorial_reviews_tv
ON news_editorial_reviews(tv_recommended, editorial_score DESC);

CREATE INDEX IF NOT EXISTS idx_news_editorial_reviews_email
ON news_editorial_reviews(email_recommended, editorial_score DESC);
