ALTER TABLE customer_flightradar_links
    ADD COLUMN IF NOT EXISTS last_activity_sync_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_activity_sync_error TEXT;

CREATE TABLE IF NOT EXISTS customer_flightradar_aircraft_utilization (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    registration TEXT NOT NULL,
    aircraft_type TEXT,
    first_seen_at TIMESTAMPTZ,
    latest_seen_at TIMESTAMPTZ,
    total_flight_count INTEGER NOT NULL DEFAULT 0,
    total_flight_hours NUMERIC(12, 4) NOT NULL DEFAULT 0,
    total_cycles INTEGER NOT NULL DEFAULT 0,
    flight_count_7d INTEGER NOT NULL DEFAULT 0,
    flight_hours_7d NUMERIC(12, 4) NOT NULL DEFAULT 0,
    cycles_7d INTEGER NOT NULL DEFAULT 0,
    flight_count_30d INTEGER NOT NULL DEFAULT 0,
    flight_hours_30d NUMERIC(12, 4) NOT NULL DEFAULT 0,
    cycles_30d INTEGER NOT NULL DEFAULT 0,
    flight_count_90d INTEGER NOT NULL DEFAULT 0,
    flight_hours_90d NUMERIC(12, 4) NOT NULL DEFAULT 0,
    cycles_90d INTEGER NOT NULL DEFAULT 0,
    avg_daily_hours_30d NUMERIC(12, 4) NOT NULL DEFAULT 0,
    avg_daily_cycles_30d NUMERIC(12, 4) NOT NULL DEFAULT 0,
    top_routes JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT customer_flightradar_aircraft_utilization_unique
        UNIQUE (customer_id, registration)
);

CREATE INDEX IF NOT EXISTS idx_customer_flightradar_aircraft_utilization_customer
    ON customer_flightradar_aircraft_utilization(customer_id, latest_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_customer_flightradar_aircraft_utilization_registration
    ON customer_flightradar_aircraft_utilization(registration);
