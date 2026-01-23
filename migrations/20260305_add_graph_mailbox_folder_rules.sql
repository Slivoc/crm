-- Map sender email addresses to Graph mailbox folders per user

CREATE TABLE IF NOT EXISTS graph_mailbox_folder_rules (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    email_address TEXT NOT NULL,
    graph_folder_id TEXT NOT NULL,
    graph_folder_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE (user_id, email_address)
);

CREATE INDEX IF NOT EXISTS idx_graph_mailbox_folder_rules_user_id
    ON graph_mailbox_folder_rules(user_id);
CREATE INDEX IF NOT EXISTS idx_graph_mailbox_folder_rules_email_address
    ON graph_mailbox_folder_rules(email_address);
