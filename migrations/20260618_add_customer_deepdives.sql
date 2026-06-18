CREATE TABLE IF NOT EXISTS customer_deepdives (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    overview TEXT,
    strategy_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(customer_id)
);

CREATE TABLE IF NOT EXISTS customer_deepdive_companies (
    id SERIAL PRIMARY KEY,
    customer_deepdive_id INTEGER NOT NULL REFERENCES customer_deepdives(id) ON DELETE CASCADE,
    related_customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL CHECK (relationship_type IN ('potential', 'existing')),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(customer_deepdive_id, related_customer_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_customer_deepdives_customer_id
    ON customer_deepdives(customer_id);

CREATE INDEX IF NOT EXISTS idx_customer_deepdive_companies_deepdive_id
    ON customer_deepdive_companies(customer_deepdive_id);

CREATE INDEX IF NOT EXISTS idx_customer_deepdive_companies_related_customer_id
    ON customer_deepdive_companies(related_customer_id);
