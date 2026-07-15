ALTER TABLE parts_lists
    ADD COLUMN IF NOT EXISTS likelihood_score SMALLINT,
    ADD COLUMN IF NOT EXISTS expected_date DATE,
    ADD COLUMN IF NOT EXISTS expected_amount_gbp NUMERIC(14, 2);

ALTER TABLE parts_lists
    DROP CONSTRAINT IF EXISTS chk_parts_lists_likelihood_score;

ALTER TABLE parts_lists
    ADD CONSTRAINT chk_parts_lists_likelihood_score
    CHECK (likelihood_score IS NULL OR likelihood_score BETWEEN 0 AND 100);

ALTER TABLE parts_lists
    DROP CONSTRAINT IF EXISTS chk_parts_lists_expected_amount_gbp;

ALTER TABLE parts_lists
    ADD CONSTRAINT chk_parts_lists_expected_amount_gbp
    CHECK (expected_amount_gbp IS NULL OR expected_amount_gbp >= 0);

CREATE INDEX IF NOT EXISTS idx_parts_lists_pinned_forecast
    ON parts_lists (salesperson_id, is_pinned, expected_date, expected_amount_gbp DESC)
    WHERE is_pinned = TRUE;
