-- Add per-user Monroe scraping settings
CREATE TABLE IF NOT EXISTS user_monroe_settings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE,
    auto_search_new_parts BOOLEAN DEFAULT FALSE,  -- Search Monroe for all new parts lists
    auto_create_supplier_offer BOOLEAN DEFAULT FALSE,  -- Auto-create supplier offer when results come in
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_user_monroe_settings_user_id ON user_monroe_settings(user_id);
