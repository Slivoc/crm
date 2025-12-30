ALTER TABLE parts_list_lines
ADD COLUMN chosen_source_type TEXT,
ADD COLUMN chosen_source_reference TEXT;

UPDATE parts_list_lines
SET chosen_source_type = 'stock'
WHERE internal_notes ILIKE 'Using stock%';

UPDATE parts_list_lines
SET internal_notes = NULLIF(trim(regexp_replace(internal_notes, '(?im)^Using stock.*(\\r?\\n)?', '', 'g')), '')
WHERE internal_notes ILIKE 'Using stock%';
