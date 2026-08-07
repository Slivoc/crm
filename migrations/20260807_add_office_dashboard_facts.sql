CREATE TABLE IF NOT EXISTS office_dashboard_facts (
    id BIGSERIAL PRIMARY KEY,
    topic VARCHAR(180) NOT NULL,
    title VARCHAR(180) NOT NULL,
    subtitle VARCHAR(240) NOT NULL DEFAULT '',
    facts JSONB NOT NULL DEFAULT '[]'::jsonb,
    image_url TEXT NOT NULL DEFAULT '',
    image_credit VARCHAR(300) NOT NULL DEFAULT '',
    source_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'archived')),
    model_provider VARCHAR(80) NOT NULL DEFAULT '',
    created_by BIGINT,
    approved_by BIGINT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_office_dashboard_facts_status
    ON office_dashboard_facts (status, updated_at DESC);

COMMENT ON TABLE office_dashboard_facts IS
    'AI-researched aerospace fact slides reviewed by an administrator before appearing on the office TV.';
