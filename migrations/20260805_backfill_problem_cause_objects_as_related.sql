-- A customer or supplier identified as the responsible party must also be a
-- related object so company-based retrieval finds the problem consistently.

INSERT INTO problem_objects (problem_id, object_type, object_id)
SELECT p.id, pc.party_type, p.cause_object_id
FROM problems p
JOIN problem_cause_categories pc ON pc.id = p.cause_category_id
WHERE p.cause_object_id IS NOT NULL
  AND pc.party_type IN ('customer', 'supplier')
ON CONFLICT DO NOTHING;
