-- Add user notifications table for toast notifications
-- This table stores notifications for users (e.g., scraping completed, import finished, etc.)

CREATE TABLE IF NOT EXISTS user_notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    notification_type TEXT NOT NULL,  -- e.g., 'scrape_complete', 'import_complete', 'error'
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    link_url TEXT,  -- Optional URL to link to (e.g., parts list page)
    link_text TEXT,  -- Optional text for the link button
    metadata TEXT,  -- JSON data for additional context
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_user_notifications_user_id ON user_notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_user_notifications_created_at ON user_notifications(created_at);
CREATE INDEX IF NOT EXISTS idx_user_notifications_is_read ON user_notifications(is_read);
