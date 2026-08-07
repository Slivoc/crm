ALTER TABLE office_dashboard_facts
    ADD COLUMN IF NOT EXISTS image_query VARCHAR(180) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS image_source VARCHAR(80) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS image_source_url TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS image_file_path TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS image_author VARCHAR(300) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS image_license VARCHAR(120) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS image_attribution VARCHAR(500) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS image_retrieved_at TIMESTAMP;

COMMENT ON COLUMN office_dashboard_facts.image_file_path IS
    'Locally cached slide image path; remote image APIs are never called while displaying the TV dashboard.';
