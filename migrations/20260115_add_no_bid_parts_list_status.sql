-- Add "No Bid" status for parts lists
INSERT INTO parts_list_statuses (name, display_order)
SELECT 'No Bid', COALESCE(MAX(display_order), 0) + 1
FROM parts_list_statuses
ON CONFLICT (name) DO NOTHING;
