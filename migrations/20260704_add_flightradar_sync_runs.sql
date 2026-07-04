CREATE TABLE IF NOT EXISTS flightradar_sync_runs (
    id SERIAL PRIMARY KEY,
    sync_type TEXT NOT NULL DEFAULT 'activity',
    source TEXT NOT NULL DEFAULT 'manual',
    mode TEXT,
    ok BOOLEAN NOT NULL DEFAULT FALSE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_seconds NUMERIC(12, 3),
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    lookback_hours INTEGER,
    chunk_hours INTEGER,
    max_requests INTEGER,
    request_count INTEGER NOT NULL DEFAULT 0,
    link_count INTEGER NOT NULL DEFAULT 0,
    processed_link_count INTEGER NOT NULL DEFAULT 0,
    flight_count INTEGER NOT NULL DEFAULT 0,
    logged_flight_count INTEGER NOT NULL DEFAULT 0,
    refreshed_aircraft_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    stopped_reason TEXT,
    error_message TEXT,
    result_payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_flightradar_sync_runs_completed_at
    ON flightradar_sync_runs(completed_at DESC);

CREATE INDEX IF NOT EXISTS idx_flightradar_sync_runs_customer_completed
    ON flightradar_sync_runs(customer_id, completed_at DESC);

CREATE INDEX IF NOT EXISTS idx_flightradar_sync_runs_status
    ON flightradar_sync_runs(sync_type, source, ok, stopped_reason);
