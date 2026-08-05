CREATE TABLE IF NOT EXISTS problem_types (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS problem_cause_categories (
    id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    party_type TEXT CHECK (party_type IN ('customer', 'supplier', 'user')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS problems (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    problem_type_id INTEGER NOT NULL REFERENCES problem_types(id),
    cause_category_id INTEGER NOT NULL REFERENCES problem_cause_categories(id),
    cause_object_id BIGINT,
    assigned_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_by_user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'investigating', 'waiting', 'resolved')),
    is_demo BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

-- Keeps this migration safely re-runnable in development databases where an
-- earlier draft of the problem tracker may already have created the table.
ALTER TABLE problems ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS problem_objects (
    problem_id BIGINT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    object_type TEXT NOT NULL CHECK (object_type IN ('customer', 'supplier')),
    object_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (problem_id, object_type, object_id)
);

CREATE TABLE IF NOT EXISTS problem_updates (
    id BIGSERIAL PRIMARY KEY,
    problem_id BIGINT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    update_text TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS problem_status_history (
    id BIGSERIAL PRIMARY KEY,
    problem_id BIGINT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    changed_by_user_id INTEGER NOT NULL REFERENCES users(id),
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS problem_tickets (
    problem_id BIGINT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    ticket_id BIGINT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    created_by_user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (problem_id, ticket_id)
);

CREATE INDEX IF NOT EXISTS idx_problems_status ON problems(status);
CREATE INDEX IF NOT EXISTS idx_problems_type ON problems(problem_type_id);
CREATE INDEX IF NOT EXISTS idx_problems_cause ON problems(cause_category_id, cause_object_id);
CREATE INDEX IF NOT EXISTS idx_problems_assignee ON problems(assigned_user_id);
CREATE INDEX IF NOT EXISTS idx_problems_created_at ON problems(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_problems_is_demo ON problems(is_demo) WHERE is_demo = TRUE;
CREATE INDEX IF NOT EXISTS idx_problem_objects_type_id ON problem_objects(object_type, object_id);
CREATE INDEX IF NOT EXISTS idx_problem_updates_problem_created ON problem_updates(problem_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_problem_status_history_problem_changed ON problem_status_history(problem_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_problem_tickets_ticket ON problem_tickets(ticket_id);

INSERT INTO problem_types (name, sort_order)
VALUES
    ('Documentation', 10),
    ('Incorrect specification / revision', 20),
    ('Pricing', 30),
    ('Quantity', 40),
    ('Quality', 50),
    ('Delivery', 60),
    ('Communication', 70),
    ('Data entry', 80),
    ('System / technical', 90),
    ('Other', 100)
ON CONFLICT (name) DO NOTHING;

INSERT INTO problem_cause_categories (code, name, party_type, sort_order)
VALUES
    ('supplier_error', 'Supplier error', 'supplier', 10),
    ('customer_error', 'Customer error', 'customer', 20),
    ('user_error', 'Internal user error', 'user', 30),
    ('internal_process', 'Internal process error', NULL, 40),
    ('system_error', 'System error', NULL, 50),
    ('carrier_logistics', 'Carrier / logistics error', NULL, 60),
    ('external_other', 'External / other', NULL, 70),
    ('unknown', 'Unknown / under investigation', NULL, 80)
ON CONFLICT (code) DO NOTHING;
