CREATE TABLE IF NOT EXISTS customer_flightradar_links (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    airline_icao VARCHAR(4) NOT NULL,
    airline_iata VARCHAR(3),
    airline_name TEXT,
    match_mode VARCHAR(20) NOT NULL DEFAULT 'operating_as',
    default_bounds TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_verified_at TIMESTAMPTZ,
    last_live_sync_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT customer_flightradar_links_match_mode_check
        CHECK (match_mode IN ('operating_as', 'painted_as', 'both')),
    CONSTRAINT customer_flightradar_links_unique
        UNIQUE (customer_id, airline_icao, match_mode)
);

CREATE INDEX IF NOT EXISTS idx_customer_flightradar_links_customer
    ON customer_flightradar_links(customer_id, is_active);

CREATE TABLE IF NOT EXISTS customer_flightradar_aircraft (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    link_id INTEGER REFERENCES customer_flightradar_links(id) ON DELETE SET NULL,
    registration TEXT NOT NULL,
    hex TEXT,
    aircraft_type TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_fr24_id TEXT,
    last_flight TEXT,
    last_callsign TEXT,
    last_origin TEXT,
    last_destination TEXT,
    last_lat NUMERIC(9, 6),
    last_lon NUMERIC(9, 6),
    last_alt INTEGER,
    last_gspeed INTEGER,
    observed_count INTEGER NOT NULL DEFAULT 1,
    last_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT customer_flightradar_aircraft_unique
        UNIQUE (customer_id, registration)
);

CREATE INDEX IF NOT EXISTS idx_customer_flightradar_aircraft_customer
    ON customer_flightradar_aircraft(customer_id, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_customer_flightradar_aircraft_registration
    ON customer_flightradar_aircraft(registration);
