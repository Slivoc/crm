CREATE TABLE IF NOT EXISTS stock_building_requisition_statuses (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_order INTEGER NOT NULL DEFAULT 0
);

INSERT INTO stock_building_requisition_statuses (name, display_order)
VALUES
    ('New', 1),
    ('Sent to Suppliers', 2),
    ('Complete', 3)
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS stock_building_requisition_lists (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    instructions TEXT,
    status_id INTEGER NOT NULL,
    assigned_user_id INTEGER,
    created_by_user_id INTEGER,
    date_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    date_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (status_id) REFERENCES stock_building_requisition_statuses(id),
    FOREIGN KEY (assigned_user_id) REFERENCES users(id),
    FOREIGN KEY (created_by_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_building_requisition_lists_status_id
    ON stock_building_requisition_lists(status_id);

CREATE INDEX IF NOT EXISTS idx_stock_building_requisition_lists_assigned_user_id
    ON stock_building_requisition_lists(assigned_user_id);

CREATE TABLE IF NOT EXISTS stock_building_requisition_list_parts (
    id SERIAL PRIMARY KEY,
    requisition_list_id INTEGER NOT NULL,
    base_part_number TEXT NOT NULL,
    part_number_snapshot TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (requisition_list_id) REFERENCES stock_building_requisition_lists(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stock_building_requisition_list_parts_list_id
    ON stock_building_requisition_list_parts(requisition_list_id);

CREATE INDEX IF NOT EXISTS idx_stock_building_requisition_list_parts_base_part
    ON stock_building_requisition_list_parts(base_part_number);

CREATE TABLE IF NOT EXISTS stock_building_requisition_supplier_lines (
    id SERIAL PRIMARY KEY,
    requisition_list_part_id INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    manufacturer_name TEXT,
    supplier_name_snapshot TEXT,
    latest_offer_price DECIMAL(15,4),
    latest_offer_currency_code TEXT,
    latest_offer_quote_reference TEXT,
    latest_offer_date TIMESTAMP,
    latest_offer_supplier_quote_id INTEGER,
    latest_offer_parts_list_id INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (requisition_list_part_id) REFERENCES stock_building_requisition_list_parts(id) ON DELETE CASCADE,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (latest_offer_supplier_quote_id) REFERENCES parts_list_supplier_quotes(id),
    FOREIGN KEY (latest_offer_parts_list_id) REFERENCES parts_lists(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_building_requisition_supplier_lines_part_id
    ON stock_building_requisition_supplier_lines(requisition_list_part_id);

CREATE INDEX IF NOT EXISTS idx_stock_building_requisition_supplier_lines_supplier_id
    ON stock_building_requisition_supplier_lines(supplier_id);

CREATE TABLE IF NOT EXISTS stock_building_requisition_supplier_breaks (
    id SERIAL PRIMARY KEY,
    supplier_line_id INTEGER NOT NULL,
    break_quantity INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (supplier_line_id) REFERENCES stock_building_requisition_supplier_lines(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stock_building_requisition_supplier_breaks_supplier_line_id
    ON stock_building_requisition_supplier_breaks(supplier_line_id);
