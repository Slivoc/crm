-- Add MRO score field to customers table
ALTER TABLE customers ADD COLUMN IF NOT EXISTS mro_score INTEGER;

-- Seed company types (using ON CONFLICT to avoid duplicates if re-run)
INSERT INTO company_types (type, description) VALUES
    ('Operator', 'Aircraft or helicopter operator (airline, charter, corporate, HEMS, etc.)'),
    ('MRO', 'Maintenance, Repair and Overhaul facility'),
    ('OEM', 'Original Equipment Manufacturer'),
    ('Distributor', 'Parts distributor or broker'),
    ('Parts Manufacturer', 'Manufacturer of aircraft parts or components (PMA, hardware, etc.)')
ON CONFLICT DO NOTHING;
