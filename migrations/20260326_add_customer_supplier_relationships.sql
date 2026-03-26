-- Track supplier relationships for customers so parts list workflows can flag known suppliers.

CREATE TABLE IF NOT EXISTS customer_supplier_relationships (
    customer_id INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (customer_id, supplier_id),
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_customer_supplier_relationships_customer_id
    ON customer_supplier_relationships(customer_id);

CREATE INDEX IF NOT EXISTS idx_customer_supplier_relationships_supplier_id
    ON customer_supplier_relationships(supplier_id);
