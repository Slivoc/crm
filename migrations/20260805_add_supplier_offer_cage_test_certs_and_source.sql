-- Preserve richer supplier-offer metadata and the source used for extraction.
ALTER TABLE parts_list_supplier_quote_lines
ADD COLUMN IF NOT EXISTS cage_code TEXT;

ALTER TABLE parts_list_supplier_quote_lines
ADD COLUMN IF NOT EXISTS test_certs TEXT;

ALTER TABLE customer_quote_lines
ADD COLUMN IF NOT EXISTS cage_code TEXT;

ALTER TABLE customer_quote_lines
ADD COLUMN IF NOT EXISTS test_certs TEXT;

ALTER TABLE parts_list_supplier_quotes
ADD COLUMN IF NOT EXISTS source_artifact_path TEXT;

ALTER TABLE parts_list_supplier_quotes
ADD COLUMN IF NOT EXISTS source_artifact_filename TEXT;

ALTER TABLE parts_list_supplier_quotes
ADD COLUMN IF NOT EXISTS source_artifact_content_type TEXT;

ALTER TABLE parts_list_supplier_quotes
ADD COLUMN IF NOT EXISTS source_artifact_kind TEXT;

ALTER TABLE parts_list_supplier_quotes
ADD COLUMN IF NOT EXISTS source_artifact_size BIGINT;

ALTER TABLE parts_list_supplier_quotes
ADD COLUMN IF NOT EXISTS source_artifact_sha256 TEXT;
