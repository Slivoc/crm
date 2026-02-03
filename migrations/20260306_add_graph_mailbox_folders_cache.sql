-- Cache mailbox folders per user to avoid slow Graph API calls on page load

CREATE TABLE IF NOT EXISTS graph_mailbox_folders_cache (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    folders_json TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
