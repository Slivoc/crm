CREATE TABLE IF NOT EXISTS portal_standard_part_pricing (
    id SERIAL PRIMARY KEY,
    base_part_number TEXT NOT NULL UNIQUE,
    price NUMERIC NOT NULL,
    currency_id INTEGER NOT NULL REFERENCES currencies(id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    date_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    date_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_portal_standard_part_pricing_active
    ON portal_standard_part_pricing(base_part_number, is_active);
