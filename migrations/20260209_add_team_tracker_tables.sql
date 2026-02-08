-- Team Tracker: Main table for tracking focus accounts in team meetings
CREATE TABLE IF NOT EXISTS team_tracker_entries (
    id SERIAL PRIMARY KEY,
    salesperson_id INTEGER NOT NULL REFERENCES salespeople(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),

    -- Lifecycle
    date_added DATE NOT NULL DEFAULT CURRENT_DATE,
    is_active BOOLEAN DEFAULT TRUE,
    archived_at TIMESTAMP,

    -- Targets (text for flexible formatting like "10k/month" or "50k contract")
    long_term_target TEXT,
    short_term_target TEXT,

    -- Current action tracking
    current_action TEXT,
    action_date DATE,

    -- Status fields
    progress TEXT,
    comments TEXT,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Team Tracker: Next steps as checkable items with history
CREATE TABLE IF NOT EXISTS team_tracker_next_steps (
    id SERIAL PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES team_tracker_entries(id) ON DELETE CASCADE,

    description TEXT NOT NULL,
    is_completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMP,
    completed_by INTEGER REFERENCES users(id),

    position INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER REFERENCES users(id)
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_team_tracker_entries_active ON team_tracker_entries(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_team_tracker_entries_salesperson ON team_tracker_entries(salesperson_id);
CREATE INDEX IF NOT EXISTS idx_team_tracker_next_steps_entry ON team_tracker_next_steps(entry_id);
