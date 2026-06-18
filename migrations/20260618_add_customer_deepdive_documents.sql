ALTER TABLE customer_deepdives
    ADD COLUMN IF NOT EXISTS perplexity_suggestions JSONB DEFAULT '[]'::jsonb;

ALTER TABLE customer_deepdives
    ADD COLUMN IF NOT EXISTS perplexity_document TEXT DEFAULT '';
