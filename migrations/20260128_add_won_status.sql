-- Add "Won" status for parts lists
-- This status indicates the customer has placed an order for the quoted parts

INSERT INTO parts_list_statuses (name, display_order)
SELECT 'Won', COALESCE(MAX(display_order), 0) + 1
FROM parts_list_statuses
ON CONFLICT (name) DO NOTHING;
