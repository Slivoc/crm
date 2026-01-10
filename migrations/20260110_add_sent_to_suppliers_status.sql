-- Add "Sent to Suppliers" status for parts lists
-- This status is automatically set when a user contacts a supplier through the system

-- Insert the new status with display_order 2 (between typical early statuses and "Quoted")
-- First, shift existing statuses with display_order >= 2 to make room
UPDATE parts_list_statuses SET display_order = display_order + 1 WHERE display_order >= 2;

-- Insert the new status
INSERT INTO parts_list_statuses (name, display_order)
VALUES ('Sent to Suppliers', 2)
ON CONFLICT (name) DO NOTHING;
