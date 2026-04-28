ALTER TABLE portal_quote_request_lines
ADD COLUMN IF NOT EXISTS parts_list_line_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_portal_quote_request_lines_parts_list_line_id
ON portal_quote_request_lines (parts_list_line_id);

UPDATE portal_quote_request_lines pqrl
SET parts_list_line_id = pll.id
FROM portal_quote_requests pqr
JOIN parts_list_lines pll
  ON pll.parts_list_id = pqr.parts_list_id
 AND pll.line_number = pqrl.line_number
WHERE pqrl.portal_quote_request_id = pqr.id
  AND pqrl.parts_list_line_id IS NULL;
