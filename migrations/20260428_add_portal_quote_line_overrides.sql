ALTER TABLE portal_quote_request_lines
ADD COLUMN quoted_part_number TEXT;

ALTER TABLE portal_quote_request_lines
ADD COLUMN line_notes TEXT;

ALTER TABLE portal_quote_request_lines
ADD COLUMN manufacturer TEXT;

ALTER TABLE portal_quote_request_lines
ADD COLUMN revision TEXT;

ALTER TABLE portal_quote_request_lines
ADD COLUMN certs TEXT;
