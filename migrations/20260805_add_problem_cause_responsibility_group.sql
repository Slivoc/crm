-- Make report responsibility an explicit, administrator-managed cause property.
ALTER TABLE problem_cause_categories
ADD COLUMN IF NOT EXISTS responsibility_group TEXT;

UPDATE problem_cause_categories
SET responsibility_group = CASE
    WHEN party_type = 'customer' THEN 'customer'
    WHEN party_type = 'supplier' THEN 'supplier'
    WHEN party_type = 'user' OR code IN ('internal_process', 'system_error') THEN 'internal'
    ELSE 'other'
END
WHERE responsibility_group IS NULL;

ALTER TABLE problem_cause_categories
ALTER COLUMN responsibility_group SET DEFAULT 'other';

ALTER TABLE problem_cause_categories
ALTER COLUMN responsibility_group SET NOT NULL;

ALTER TABLE problem_cause_categories
DROP CONSTRAINT IF EXISTS problem_cause_categories_responsibility_group_check;

ALTER TABLE problem_cause_categories
ADD CONSTRAINT problem_cause_categories_responsibility_group_check
CHECK (responsibility_group IN ('internal', 'supplier', 'customer', 'other'));
