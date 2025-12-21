-- Adds standard condition and standard cert fields to suppliers
BEGIN TRANSACTION;

ALTER TABLE suppliers ADD COLUMN standard_condition TEXT;
ALTER TABLE suppliers ADD COLUMN standard_certs TEXT;

-- Backfill existing rows with empty strings so UI shows blanks instead of null
UPDATE suppliers SET standard_condition = '' WHERE standard_condition IS NULL;
UPDATE suppliers SET standard_certs = '' WHERE standard_certs IS NULL;

COMMIT;
