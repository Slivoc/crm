CREATE TABLE IF NOT EXISTS office_dashboard_employee (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name VARCHAR(120) NOT NULL,
    description VARCHAR(600) NOT NULL DEFAULT '',
    image_path TEXT,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE office_dashboard_employee IS
    'Singleton configuration for the employee featured on the office TV dashboard.';
