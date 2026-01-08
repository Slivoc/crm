-- Add pieces_per_pound column to part_numbers table
ALTER TABLE part_numbers ADD COLUMN pieces_per_pound DECIMAL(10,3);
