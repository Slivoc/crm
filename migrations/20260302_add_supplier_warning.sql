-- Adds warning field to suppliers
BEGIN TRANSACTION;

ALTER TABLE suppliers ADD COLUMN warning TEXT;

-- Backfill existing rows with empty strings so UI shows blanks instead of null
UPDATE suppliers SET warning = '' WHERE warning IS NULL;

COMMIT;
