CREATE TABLE IF NOT EXISTS user_salesperson_assignments (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    salesperson_id INTEGER NOT NULL REFERENCES salespeople(id) ON DELETE CASCADE,
    is_default_mailbox_filter BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, salesperson_id)
);

INSERT INTO user_salesperson_assignments (
    user_id,
    salesperson_id,
    is_default_mailbox_filter
)
SELECT
    sul.user_id,
    sul.legacy_salesperson_id,
    TRUE
FROM salesperson_user_link sul
WHERE sul.user_id IS NOT NULL
  AND sul.legacy_salesperson_id IS NOT NULL
ON CONFLICT (user_id, salesperson_id) DO UPDATE
SET is_default_mailbox_filter = TRUE,
    updated_at = CURRENT_TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_user_salesperson_assignments_user
    ON user_salesperson_assignments(user_id);

CREATE INDEX IF NOT EXISTS idx_user_salesperson_assignments_salesperson
    ON user_salesperson_assignments(salesperson_id);
