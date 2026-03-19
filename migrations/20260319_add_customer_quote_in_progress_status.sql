ALTER TABLE customer_quote_lines
DROP CONSTRAINT IF EXISTS customer_quote_lines_quoted_status_check;

ALTER TABLE customer_quote_lines
ADD CONSTRAINT customer_quote_lines_quoted_status_check
CHECK (
    quoted_status = ANY (
        ARRAY[
            'created'::text,
            'in_progress'::text,
            'quoted'::text,
            'no_bid'::text
        ]
    )
);
