CREATE INDEX IF NOT EXISTS idx_customer_flightradar_flights_activity_time
    ON customer_flightradar_flights (
        (COALESCE(first_seen, datetime_takeoff, created_at)) DESC
    );
