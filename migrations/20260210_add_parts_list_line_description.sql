ALTER TABLE parts_list_lines
    ADD COLUMN IF NOT EXISTS description TEXT;
