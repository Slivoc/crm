ALTER TABLE portal_quote_request_lines
ADD COLUMN IF NOT EXISTS submitted_estimated_price NUMERIC;

ALTER TABLE portal_quote_request_lines
ADD COLUMN IF NOT EXISTS submitted_estimated_currency TEXT;

ALTER TABLE portal_quote_request_lines
ADD COLUMN IF NOT EXISTS submitted_estimated_lead_days INTEGER;

ALTER TABLE portal_quote_request_lines
ADD COLUMN IF NOT EXISTS submitted_price_source TEXT;
