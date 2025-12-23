ALTER TABLE customer_quote_lines
ADD COLUMN quoted_on timestamp;

UPDATE customer_quote_lines
SET quoted_on = date_modified
WHERE quoted_status = 'quoted'
  AND quoted_on IS NULL;
