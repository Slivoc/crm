-- Migration: Add sent_customer_news table for deduplication of news emails
-- Date: 2025-12-30
-- Purpose: Track previously sent news items to avoid repeating news in customer news emails

CREATE TABLE IF NOT EXISTS sent_customer_news (
    id SERIAL PRIMARY KEY,
    salesperson_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    news_hash VARCHAR(64) NOT NULL,  -- SHA256 hash of normalized headline for quick dedup
    headline TEXT NOT NULL,
    summary TEXT,
    source VARCHAR(255),
    published_date DATE,
    sent_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(salesperson_id, customer_id, news_hash)
);

-- Index for fast lookups when filtering news
CREATE INDEX IF NOT EXISTS idx_sent_customer_news_salesperson_customer 
ON sent_customer_news(salesperson_id, customer_id);

-- Index for cleanup of old records
CREATE INDEX IF NOT EXISTS idx_sent_customer_news_sent_at 
ON sent_customer_news(sent_at);

-- Comment explaining the table purpose
COMMENT ON TABLE sent_customer_news IS 'Tracks news items that have been sent to salespeople to prevent duplicate news in emails';
COMMENT ON COLUMN sent_customer_news.news_hash IS 'SHA256 hash of lowercase headline for exact duplicate detection';
