CREATE TABLE IF NOT EXISTS customer_flightradar_flights (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    link_id INTEGER REFERENCES customer_flightradar_links(id) ON DELETE SET NULL,
    flight_dedupe_key TEXT NOT NULL,
    fr24_id TEXT,
    registration TEXT,
    aircraft_type TEXT,
    flight TEXT,
    callsign TEXT,
    operating_as VARCHAR(4),
    painted_as VARCHAR(4),
    origin_iata VARCHAR(3),
    origin_icao VARCHAR(4),
    destination_iata VARCHAR(3),
    destination_icao VARCHAR(4),
    datetime_takeoff TIMESTAMPTZ,
    datetime_landed TIMESTAMPTZ,
    first_seen TIMESTAMPTZ,
    last_seen TIMESTAMPTZ,
    flight_time_seconds NUMERIC(12, 3),
    estimated_flight_hours NUMERIC(12, 4),
    cycle_count INTEGER NOT NULL DEFAULT 0,
    flight_ended BOOLEAN,
    actual_distance_km NUMERIC(12, 3),
    circle_distance_km NUMERIC(12, 3),
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT customer_flightradar_flights_unique
        UNIQUE (customer_id, flight_dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_customer_flightradar_flights_customer_time
    ON customer_flightradar_flights(customer_id, first_seen DESC, datetime_takeoff DESC);

CREATE INDEX IF NOT EXISTS idx_customer_flightradar_flights_registration
    ON customer_flightradar_flights(customer_id, registration, first_seen DESC);

CREATE INDEX IF NOT EXISTS idx_customer_flightradar_flights_fr24_id
    ON customer_flightradar_flights(fr24_id)
    WHERE fr24_id IS NOT NULL;
