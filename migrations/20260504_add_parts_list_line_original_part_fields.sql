ALTER TABLE parts_list_lines
ADD COLUMN IF NOT EXISTS original_customer_part_number TEXT;

ALTER TABLE parts_list_lines
ADD COLUMN IF NOT EXISTS original_base_part_number TEXT;
