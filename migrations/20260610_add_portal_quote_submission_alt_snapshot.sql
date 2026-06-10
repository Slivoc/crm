ALTER TABLE portal_quote_request_lines
ADD COLUMN IF NOT EXISTS submitted_estimated_part_number TEXT;

ALTER TABLE portal_quote_request_lines
ADD COLUMN IF NOT EXISTS submitted_is_global_alternative BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE portal_quote_request_lines
ADD COLUMN IF NOT EXISTS submitted_alternative_to_part_number TEXT;
