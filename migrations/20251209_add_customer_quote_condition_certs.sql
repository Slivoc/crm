-- Adds condition/certs to customer_quote_lines for customer quotes
BEGIN TRANSACTION;

ALTER TABLE customer_quote_lines ADD COLUMN standard_condition TEXT;
ALTER TABLE customer_quote_lines ADD COLUMN standard_certs TEXT;

-- Backfill existing rows with empty strings
UPDATE customer_quote_lines SET standard_condition = '' WHERE standard_condition IS NULL;
UPDATE customer_quote_lines SET standard_certs = '' WHERE standard_certs IS NULL;

COMMIT;
