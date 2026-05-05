CREATE TABLE IF NOT EXISTS stock_building_requisition_list_suppliers (
    id SERIAL PRIMARY KEY,
    requisition_list_id INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    supplier_name_snapshot TEXT,
    manufacturer_names_snapshot TEXT,
    covered_part_count INTEGER NOT NULL DEFAULT 0,
    covered_parts_snapshot TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (requisition_list_id) REFERENCES stock_building_requisition_lists(id) ON DELETE CASCADE,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_building_requisition_list_suppliers_list_id
    ON stock_building_requisition_list_suppliers(requisition_list_id);

CREATE INDEX IF NOT EXISTS idx_stock_building_requisition_list_suppliers_supplier_id
    ON stock_building_requisition_list_suppliers(supplier_id);

CREATE TABLE IF NOT EXISTS stock_building_requisition_list_supplier_breaks (
    id SERIAL PRIMARY KEY,
    requisition_supplier_id INTEGER NOT NULL,
    break_quantity INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (requisition_supplier_id) REFERENCES stock_building_requisition_list_suppliers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stock_building_requisition_list_supplier_breaks_supplier_id
    ON stock_building_requisition_list_supplier_breaks(requisition_supplier_id);

WITH supplier_seed AS (
    SELECT
        rlp.requisition_list_id,
        rsl.supplier_id,
        MAX(rsl.supplier_name_snapshot) AS supplier_name_snapshot,
        STRING_AGG(DISTINCT COALESCE(NULLIF(TRIM(rsl.manufacturer_name), ''), ''), ', ' ORDER BY COALESCE(NULLIF(TRIM(rsl.manufacturer_name), ''), '')) AS manufacturer_names_snapshot,
        COUNT(DISTINCT rlp.base_part_number) AS covered_part_count,
        STRING_AGG(DISTINCT COALESCE(NULLIF(TRIM(rlp.part_number_snapshot), ''), rlp.base_part_number), ', ' ORDER BY COALESCE(NULLIF(TRIM(rlp.part_number_snapshot), ''), rlp.base_part_number)) AS covered_parts_snapshot,
        MIN(rsl.sort_order) AS first_sort_order
    FROM stock_building_requisition_supplier_lines rsl
    JOIN stock_building_requisition_list_parts rlp ON rlp.id = rsl.requisition_list_part_id
    GROUP BY rlp.requisition_list_id, rsl.supplier_id
)
INSERT INTO stock_building_requisition_list_suppliers
    (
        requisition_list_id,
        supplier_id,
        supplier_name_snapshot,
        manufacturer_names_snapshot,
        covered_part_count,
        covered_parts_snapshot,
        sort_order
    )
SELECT
    ss.requisition_list_id,
    ss.supplier_id,
    ss.supplier_name_snapshot,
    NULLIF(ss.manufacturer_names_snapshot, ''),
    ss.covered_part_count,
    ss.covered_parts_snapshot,
    COALESCE(ss.first_sort_order, 0)
FROM supplier_seed ss
WHERE NOT EXISTS (
    SELECT 1
    FROM stock_building_requisition_list_suppliers existing
    WHERE existing.requisition_list_id = ss.requisition_list_id
      AND existing.supplier_id = ss.supplier_id
);

WITH ranked_breaks AS (
    SELECT
        rls2.id AS requisition_supplier_id,
        rsb.break_quantity,
        ROW_NUMBER() OVER (
            PARTITION BY rls2.id
            ORDER BY rsb.break_quantity, rsb.id
        ) - 1 AS sort_order
    FROM stock_building_requisition_supplier_breaks rsb
    JOIN stock_building_requisition_supplier_lines rsl ON rsl.id = rsb.supplier_line_id
    JOIN stock_building_requisition_list_parts rlp ON rlp.id = rsl.requisition_list_part_id
    JOIN stock_building_requisition_list_suppliers rls2
      ON rls2.requisition_list_id = rlp.requisition_list_id
     AND rls2.supplier_id = rsl.supplier_id
)
INSERT INTO stock_building_requisition_list_supplier_breaks
    (requisition_supplier_id, break_quantity, sort_order)
SELECT DISTINCT
    rb.requisition_supplier_id,
    rb.break_quantity,
    rb.sort_order
FROM ranked_breaks rb
WHERE rb.sort_order < 6
  AND NOT EXISTS (
      SELECT 1
      FROM stock_building_requisition_list_supplier_breaks existing
      WHERE existing.requisition_supplier_id = rb.requisition_supplier_id
        AND existing.break_quantity = rb.break_quantity
  );
