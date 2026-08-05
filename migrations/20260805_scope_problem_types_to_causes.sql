-- Problem types belong to a cause. Existing global types are cloned once for
-- every cause they have historically been used with, then problems are pointed
-- at the appropriate scoped copy.

BEGIN TRANSACTION;

ALTER TABLE problem_types
    ADD COLUMN IF NOT EXISTS cause_category_id INTEGER;

ALTER TABLE problem_types
    DROP CONSTRAINT IF EXISTS problem_types_name_key;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'problem_types_cause_category_id_fkey'
    ) THEN
        ALTER TABLE problem_types
            ADD CONSTRAINT problem_types_cause_category_id_fkey
            FOREIGN KEY (cause_category_id)
            REFERENCES problem_cause_categories(id);
    END IF;
END $$;

CREATE TEMP TABLE problem_type_cause_pairs ON COMMIT DROP AS
SELECT DISTINCT pt.id AS old_type_id, p.cause_category_id
FROM problem_types pt
JOIN problems p ON p.problem_type_id = pt.id
WHERE pt.cause_category_id IS NULL;

INSERT INTO problem_types (name, is_active, sort_order, cause_category_id)
SELECT pt.name, pt.is_active, pt.sort_order, pair.cause_category_id
FROM problem_type_cause_pairs pair
JOIN problem_types pt ON pt.id = pair.old_type_id
WHERE NOT EXISTS (
    SELECT 1
    FROM problem_types scoped
    WHERE scoped.cause_category_id = pair.cause_category_id
      AND LOWER(scoped.name) = LOWER(pt.name)
);

UPDATE problems p
SET problem_type_id = scoped.id
FROM problem_types old_type, problem_types scoped
WHERE p.problem_type_id = old_type.id
  AND old_type.cause_category_id IS NULL
  AND scoped.cause_category_id = p.cause_category_id
  AND LOWER(scoped.name) = LOWER(old_type.name);

-- Unused legacy global options have no meaningful cause and are deliberately
-- retired. New options are created under a cause while a problem is logged.
DELETE FROM problem_types WHERE cause_category_id IS NULL;

-- The old uniqueness rule was case-sensitive. Consolidate any historical
-- variants such as "Revision" and "revision" before enforcing scoped,
-- case-insensitive uniqueness.
WITH canonical AS (
    SELECT cause_category_id, LOWER(name) AS normalised_name, MIN(id) AS keep_id
    FROM problem_types
    GROUP BY cause_category_id, LOWER(name)
), duplicates AS (
    SELECT pt.id AS duplicate_id, canonical.keep_id
    FROM problem_types pt
    JOIN canonical
      ON canonical.cause_category_id = pt.cause_category_id
     AND canonical.normalised_name = LOWER(pt.name)
    WHERE pt.id != canonical.keep_id
)
UPDATE problems p
SET problem_type_id = duplicates.keep_id
FROM duplicates
WHERE p.problem_type_id = duplicates.duplicate_id;

WITH canonical AS (
    SELECT cause_category_id, LOWER(name) AS normalised_name, MIN(id) AS keep_id
    FROM problem_types
    GROUP BY cause_category_id, LOWER(name)
)
DELETE FROM problem_types pt
USING canonical
WHERE pt.cause_category_id = canonical.cause_category_id
  AND LOWER(pt.name) = canonical.normalised_name
  AND pt.id != canonical.keep_id;

ALTER TABLE problem_types
    ALTER COLUMN cause_category_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_problem_types_cause_category
    ON problem_types(cause_category_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_problem_types_cause_name_ci
    ON problem_types(cause_category_id, LOWER(name));

COMMIT;
