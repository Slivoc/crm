-- PostgreSQL Schema for CRM Database
-- Generated from database.db
-- Tables starting with 'old' have been excluded

CREATE TABLE account_activity_log (

    id SERIAL PRIMARY KEY,

    account_id INTEGER NOT NULL,

    journal_entry_id INTEGER NOT NULL,

    transaction_DATE DATE NOT NULL,

    debit_amount DECIMAL(15,2) DEFAULT 0,

    credit_amount DECIMAL(15,2) DEFAULT 0,

    balance_after DECIMAL(15,2) NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (account_id) REFERENCES chart_of_accounts(id),

    FOREIGN KEY (journal_entry_id) REFERENCES journal_entries(id)

);

CREATE TABLE account_reconciliations (

    id SERIAL PRIMARY KEY,

    account_id INTEGER NOT NULL,

    statement_DATE DATE NOT NULL,

    statement_balance DECIMAL(15,2) NOT NULL,

    is_reconciled BOOLEAN DEFAULT FALSE,

    reconciled_by INTEGER,

    reconciled_at TIMESTAMP,

    notes TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (account_id) REFERENCES chart_of_accounts(id),

    FOREIGN KEY (reconciled_by) REFERENCES users(id)

);

CREATE TABLE account_types (

    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL,

    description TEXT,

    normal_balance TEXT NOT NULL,  
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE acknowledgments (id SERIAL PRIMARY KEY, sales_order_id INTEGER NOT NULL, acknowledgment_pdf TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE ai_tag_suggestions (

    id SERIAL PRIMARY KEY,

    customer_id INTEGER,

    suggested_tag TEXT,

    frequency INTEGER DEFAULT 1,

    reviewed BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id)

);

CREATE TABLE alternative_part_numbers (
    id SERIAL PRIMARY KEY,
    part_number_id INTEGER NOT NULL,
    customer TEXT NOT NULL,
    customer_part_number TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (part_number_id) REFERENCES "old_part_numbers"(id) ON DELETE CASCADE
);

CREATE TABLE app_settings (

CREATE TABLE call_list (
    id SERIAL PRIMARY KEY,
    contact_id INTEGER NOT NULL,
    salesperson_id INTEGER NOT NULL,
    added_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    priority INTEGER DEFAULT 0,  -- Optional: 0=normal, 1=high, 2=urgent
    is_active BOOLEAN DEFAULT TRUE,  -- 1=active, 0=removed from list
    snoozed_until TIMESTAMP,
    removed_DATE TIMESTAMP,

    file_id INTEGER NOT NULL,

    PRIMARY KEY (bom_header_id, file_id),

    FOREIGN KEY (bom_header_id) REFERENCES bom_headers(id),

    FOREIGN KEY (file_id) REFERENCES files(id)

);

CREATE TABLE bom_headers (

    id SERIAL PRIMARY KEY,

    base_part_number TEXT UNIQUE,  
    name TEXT NOT NULL,            
    description TEXT,

    type TEXT NOT NULL,            
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (base_part_number) REFERENCES part_numbers(base_part_number)

);

CREATE TABLE bom_lines (

    id SERIAL PRIMARY KEY,

    bom_header_id INTEGER NOT NULL,

    parent_line_id INTEGER,        
    base_part_number TEXT NOT NULL,

    quantity INTEGER NOT NULL,

    reference_designator TEXT,     
    notes TEXT,

    position INTEGER,              
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, guide_price DECIMAL(10, 2) DEFAULT NULL,

    FOREIGN KEY (bom_header_id) REFERENCES bom_headers(id),

    FOREIGN KEY (parent_line_id) REFERENCES bom_lines(id),

    FOREIGN KEY (base_part_number) REFERENCES part_numbers(base_part_number)

);

CREATE TABLE bom_pricing (

    id SERIAL PRIMARY KEY,

    bom_line_id INTEGER NOT NULL,

    offer_line_id INTEGER NOT NULL,

    notes TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (bom_line_id) REFERENCES bom_lines(id),

    FOREIGN KEY (offer_line_id) REFERENCES offer_lines(id)

);

CREATE TABLE bom_revisions (

    id SERIAL PRIMARY KEY,

    bom_header_id INTEGER NOT NULL,

    revision TEXT NOT NULL,

    effective_DATE DATE NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    created_by INTEGER NOT NULL,

    notes TEXT,

    FOREIGN KEY (bom_header_id) REFERENCES bom_headers(id),

    FOREIGN KEY (created_by) REFERENCES users(id)

);

CREATE TABLE call_list (

    id SERIAL PRIMARY KEY,

    contact_id INTEGER NOT NULL,

    salesperson_id INTEGER NOT NULL,

    added_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    notes TEXT,

    priority INTEGER DEFAULT 0,  -- Optional: 0=normal, 1=high, 2=urgent

    is_active BOOLEAN DEFAULT TRUE,  -- 1=active, 0=removed from list

    removed_DATE TIMESTAMP,

    FOREIGN KEY (contact_id) REFERENCES contacts(id),

    FOREIGN KEY (salesperson_id) REFERENCES salespeople(id),

    UNIQUE(contact_id, salesperson_id, is_active)  -- Prevent duplicates for active entries

);

CREATE TABLE chart_of_accounts (

    id SERIAL PRIMARY KEY,

    account_number TEXT NOT NULL UNIQUE,

    account_name TEXT NOT NULL,

    account_type_id INTEGER NOT NULL,

    parent_account_id INTEGER,

    description TEXT,

    is_active BOOLEAN DEFAULT TRUE,

    balance DECIMAL(15,2) DEFAULT 0,

    currency_id INTEGER NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (account_type_id) REFERENCES account_types(id),

    FOREIGN KEY (parent_account_id) REFERENCES chart_of_accounts(id),

    FOREIGN KEY (currency_id) REFERENCES currencies(id)

);

CREATE TABLE company_types (

    id SERIAL PRIMARY KEY,

    type TEXT NOT NULL,

    description TEXT,

    parent_type_id INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE contact_communications (

    id SERIAL PRIMARY KEY,

    DATE TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    contact_id INTEGER NOT NULL,

    customer_id INTEGER NOT NULL,

    salesperson_id INTEGER NOT NULL,

    communication_type TEXT NOT NULL, 
    notes TEXT, upDATE_id INTEGER,

    FOREIGN KEY (contact_id) REFERENCES contacts(id),

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (salesperson_id) REFERENCES legacy_salesperson(id)

);

CREATE TABLE contact_list_members (

    id SERIAL PRIMARY KEY,

    list_id INTEGER NOT NULL,

    contact_id INTEGER NOT NULL,

    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (list_id) REFERENCES contact_lists(id) ON DELETE CASCADE,

    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE,

    UNIQUE(list_id, contact_id) 
);

CREATE TABLE contact_lists (

    id SERIAL PRIMARY KEY,

    name TEXT

);

CREATE TABLE contact_statuses (

    id SERIAL PRIMARY KEY,

    name VARCHAR(50) NOT NULL UNIQUE,

    description TEXT,

    color VARCHAR(7) DEFAULT '#6c757d', -- Hex color code for display

    is_active BOOLEAN DEFAULT TRUE,

    sort_order INTEGER DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE contacts (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER,
    name TEXT NOT NULL,
    email TEXT NOT NULL, job_title TEXT, second_name TEXT, notes TEXT, upDATEd_at TIMESTAMP, status_id INTEGER DEFAULT 1, phone TEXT, timezone TEXT DEFAULT 'UTC',
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE cq_lines (

    id SERIAL PRIMARY KEY,

    cq_id INTEGER NOT NULL,

    transaction_header_id TEXT,

    transaction_item_id TEXT,

    base_part_number TEXT NOT NULL,

    part_number TEXT NOT NULL,

    description TEXT,

    condition_code TEXT,

    quantity_requested INTEGER DEFAULT 0,

    quantity_quoted INTEGER DEFAULT 0,

    quantity_allocated INTEGER DEFAULT 0,

    unit_of_measure TEXT DEFAULT 'EA',

    tran_type TEXT,

    base_currency TEXT,

    foreign_currency TEXT,

    unit_cost NUMERIC DEFAULT 0.0,

    unit_price NUMERIC DEFAULT 0.0,

    total_price NUMERIC DEFAULT 0.0,

    total_foreign_price NUMERIC DEFAULT 0.0,

    tax NUMERIC DEFAULT 0.0,

    total_cost NUMERIC DEFAULT 0.0,

    for_price NUMERIC DEFAULT 0.0,

    lead_days INTEGER DEFAULT 0,

    created_by TEXT,

    sales_person TEXT,

    core_charge NUMERIC DEFAULT 0.0,

    traceability TEXT,

    is_no_quote BOOLEAN DEFAULT FALSE,

    line_number INTEGER DEFAULT 0,

    serial_number TEXT,

    tag_or_certificate_number TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (cq_id) REFERENCES cqs(id) ON DELETE CASCADE,

    FOREIGN KEY (base_part_number) REFERENCES part_numbers(base_part_number)

);

CREATE TABLE cqs (

    id SERIAL PRIMARY KEY,

    cq_number TEXT NOT NULL UNIQUE,

    customer_id INTEGER NOT NULL,

    status TEXT DEFAULT 'Created',

    entry_DATE DATE,

    due_DATE DATE,

    currency_id INTEGER,

    sales_person TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,

    FOREIGN KEY (currency_id) REFERENCES currencies(id)

);

CREATE TABLE currencies (
    id SERIAL PRIMARY KEY,
    currency_code TEXT UNIQUE NOT NULL,
    exchange_rate_to_eur NUMERIC NOT NULL
, symbol TEXT);

CREATE TABLE customer_addresses (id SERIAL PRIMARY KEY, customer_id INTEGER NOT NULL, address TEXT NOT NULL, city TEXT NOT NULL, postal_code TEXT NOT NULL, country TEXT NOT NULL, is_default_shipping BOOLEAN DEFAULT FALSE, is_default_invoicing BOOLEAN DEFAULT FALSE);

CREATE TABLE customer_associations (

    id SERIAL PRIMARY KEY,

    main_customer_id INTEGER NOT NULL,

    associated_customer_id INTEGER NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    notes TEXT,

    FOREIGN KEY (main_customer_id) REFERENCES customers(id) ON DELETE CASCADE,

    FOREIGN KEY (associated_customer_id) REFERENCES customers(id) ON DELETE CASCADE,

    -- Prevent duplicate associations and self-associations

    UNIQUE(main_customer_id, associated_customer_id),

    CHECK(main_customer_id != associated_customer_id)

);

CREATE TABLE customer_boms (

    customer_id INTEGER NOT NULL,

    bom_header_id INTEGER NOT NULL,

    reference TEXT,               
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (customer_id, bom_header_id),

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (bom_header_id) REFERENCES bom_headers(id)

);

CREATE TABLE customer_company_types (

    customer_id INTEGER NOT NULL,

    company_type_id INTEGER NOT NULL,

    PRIMARY KEY (customer_id, company_type_id),

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (company_type_id) REFERENCES company_types(id)

);

CREATE TABLE customer_development_answers (

    id SERIAL PRIMARY KEY,

    customer_id INTEGER NOT NULL,

    development_point_id INTEGER NOT NULL,

    answer TEXT,

    answered_by INTEGER, -- user_id who provided the answer

    answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE,

    FOREIGN KEY (development_point_id) REFERENCES development_points (id) ON DELETE CASCADE,

    UNIQUE(customer_id, development_point_id) -- One answer per customer per point

);

CREATE TABLE customer_domains (

    id SERIAL PRIMARY KEY,

    customer_id INTEGER NOT NULL,

    domain TEXT NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    UNIQUE(customer_id, domain)

);

CREATE TABLE customer_enrichment_status (

    customer_id SERIAL PRIMARY KEY,

    status TEXT,  
    last_attempt TIMESTAMP,

    error_message TEXT,

    attempts INTEGER DEFAULT 0,

    FOREIGN KEY (customer_id) REFERENCES customers(id)

);

CREATE TABLE customer_industries (

    customer_id INTEGER NOT NULL,

    industry_id INTEGER NOT NULL,

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (industry_id) REFERENCES industries(id),

    PRIMARY KEY (customer_id, industry_id)

);

CREATE TABLE customer_industry_tags (

    customer_id INTEGER NOT NULL,

    tag_id INTEGER NOT NULL,

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (tag_id) REFERENCES industry_tags(id),

    PRIMARY KEY (customer_id, tag_id)

);

CREATE TABLE customer_insights (

    id SERIAL PRIMARY KEY,

    name VARCHAR(100),

    title VARCHAR(255),

    description TEXT,

    query TEXT,

    chart_type VARCHAR(50),  
    refresh_interval INTEGER DEFAULT 3600,  
    enabled BOOLEAN DEFAULT TRUE,

    display_order INTEGER

);

CREATE TABLE customer_monthly_targets (

    id SERIAL PRIMARY KEY,

    salesperson_id INTEGER NOT NULL,

    customer_id INTEGER NOT NULL,

    target_month TEXT NOT NULL, -- Format: 'YYYY-MM'

    target_amount NUMERIC DEFAULT 0,

    is_locked INTEGER DEFAULT 0, -- 0 = Auto-calculated, 1 = Manually overridden

    notes TEXT,

    comments TEXT,

    response TEXT,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(salesperson_id, customer_id, target_month)

);

CREATE TABLE customer_part_numbers (
                id SERIAL PRIMARY KEY,
                base_part_number TEXT NOT NULL,
                customer_part_number TEXT NOT NULL,
                customer_id INTEGER NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );

CREATE TABLE customer_quote_lines (
    id SERIAL PRIMARY KEY,
    parts_list_line_id INTEGER NOT NULL,
    
    -- Part number display options
    display_part_number TEXT,           -- Custom display P/N (defaults to customer_part_number)
    quoted_part_number TEXT,            -- What we're actually quoting (may differ from display)
    manufacturer TEXT,
    
    -- Costing (all in GBP for calculations)
    base_cost_gbp NUMERIC,                 -- Cost converted to GBP (from chosen_cost + currency conversion)
    margin_percent NUMERIC DEFAULT 0,      -- Profit margin % (NOT markup)
    quote_price_gbp NUMERIC,               -- Selling price in GBP = base_cost_gbp / (1 - margin/100)
    

    -- Status

    is_no_bid INTEGER DEFAULT 0,        -- 1 if we're declining to quote this line

    

    -- Notes

    line_notes TEXT,                    -- Internal notes about this quote line

    

    -- Timestamps

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP, delivery_per_unit NUMERIC DEFAULT 0, delivery_per_line NUMERIC DEFAULT 0, quoted_status TEXT DEFAULT 'created' 

CHECK(quoted_status IN ('created', 'quoted', 'no_bid')), lead_days INTEGER DEFAULT NULL, standard_condition TEXT, standard_certs TEXT,

    

    FOREIGN KEY (parts_list_line_id) REFERENCES parts_list_lines(id) ON DELETE CASCADE,

    UNIQUE(parts_list_line_id)          -- One quote line per parts list line

);

CREATE TABLE customer_status (
    id SERIAL PRIMARY KEY,
    status TEXT NOT NULL
);

CREATE TABLE customer_upDATEs (
    id SERIAL PRIMARY KEY,
    DATE TEXT NOT NULL,
    customer_id INTEGER NOT NULL,
    salesperson_id INTEGER,
    upDATE_text TEXT NOT NULL, communication_type TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers(id),
    FOREIGN KEY (salesperson_id) REFERENCES salespeople(id)
);

CREATE TABLE "customers" (

    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL,

    primary_contact_id INTEGER,

    payment_terms TEXT,

    incoterms TEXT,

    salesperson_id INTEGER,

    status_id INTEGER REFERENCES customer_status(id),

    currency_id INTEGER DEFAULT 3,

    system_code TEXT,

    description TEXT,

    estimated_revenue NUMERIC,

    website TEXT,

    country VARCHAR(2) CHECK (country = UPPER(country)),

    upDATEd_at TIMESTAMP,

    apollo_id TEXT,  
    budget NUMERIC, watch BOOLEAN DEFAULT FALSE, preferred_currency_id INTEGER DEFAULT 1, logo_url TEXT, notes TEXT, fleet_size INTEGER, priority INTEGER REFERENCES priorities(id),

    FOREIGN KEY (primary_contact_id) REFERENCES contacts(id),

    FOREIGN KEY (salesperson_id) REFERENCES salespeople(id)

);

CREATE TABLE dashboard_panels (

    id SERIAL PRIMARY KEY,

    user_id INTEGER,

    query_id INTEGER NOT NULL,

    display_type TEXT NOT NULL,

    panel_title TEXT,

    panel_order INTEGER,

    DATE_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP

, column_mappings TEXT, formatting_rules TEXT, header_styles TEXT, summary_calculation TEXT, panel_height TEXT DEFAULT '400px', panel_width TEXT DEFAULT '100%', background_color TEXT DEFAULT '#ffffff', text_color TEXT DEFAULT '#000000', column_styles TEXT);

CREATE TABLE deepdive_curated_customers (

    id SERIAL PRIMARY KEY,

    deepdive_id INTEGER NOT NULL,

    customer_id INTEGER NOT NULL,

    notes TEXT,

    order_index INTEGER DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (deepdive_id) REFERENCES geographic_deepdives(id) ON DELETE CASCADE,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,

    UNIQUE(deepdive_id, customer_id)

);

CREATE TABLE deepdive_customer_links (

    id SERIAL PRIMARY KEY,

    deepdive_id INTEGER NOT NULL,

    customer_id INTEGER NOT NULL,

    linked_text TEXT NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (deepdive_id) REFERENCES geographic_deepdives(id) ON DELETE CASCADE,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,

    UNIQUE(deepdive_id, customer_id, linked_text)

);

CREATE TABLE development_points (

    id SERIAL PRIMARY KEY,

    question TEXT NOT NULL,

    description TEXT,

    order_index INTEGER NOT NULL DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE email_logs (

    id SERIAL PRIMARY KEY,

    template_id INTEGER NOT NULL,

    contact_id INTEGER NOT NULL,

    customer_id INTEGER,

    subject TEXT NOT NULL,

    recipient_email TEXT NOT NULL,

    sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    status TEXT NOT NULL CHECK (status IN ('sent', 'error')),

    error_message TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (template_id) REFERENCES email_templates (id),

    FOREIGN KEY (contact_id) REFERENCES contacts (id),

    FOREIGN KEY (customer_id) REFERENCES customers (id)

);

CREATE TABLE email_signatures (

    id SERIAL PRIMARY KEY,

    signature_html TEXT NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

, name TEXT NOT NULL DEFAULT '', user_id INTEGER, is_default BOOLEAN DEFAULT FALSE);

CREATE TABLE email_templates (

    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL,

    subject TEXT NOT NULL,

    body TEXT NOT NULL,

    description TEXT,

    is_active BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE "emails" (

    id SERIAL PRIMARY KEY,

    customer_id INTEGER,

    contact_id INTEGER,

    sender_email TEXT,

    recipient_email TEXT,

    subject TEXT,

    sent_DATE TEXT,

    direction TEXT,

    sync_status TEXT,

    uid TEXT,

    folder TEXT,

    message_id TEXT UNIQUE  
);

CREATE TABLE excess_stock_files (

    excess_stock_list_id INTEGER,  
    file_id INTEGER,               
    PRIMARY KEY (excess_stock_list_id, file_id),

    FOREIGN KEY (excess_stock_list_id) REFERENCES excess_stock_lists(id),

    FOREIGN KEY (file_id) REFERENCES files(id)

);

CREATE TABLE excess_stock_lines (

    id SERIAL PRIMARY KEY,

    excess_stock_list_id INTEGER,  
    base_part_number TEXT NOT NULL,  
    quantity INTEGER,               
    DATE_code TEXT,                 
    manufacturer TEXT,              
    FOREIGN KEY (excess_stock_list_id) REFERENCES excess_stock_lists(id)

);

CREATE TABLE excess_stock_lists (

    id SERIAL PRIMARY KEY,

    email TEXT,                 
    customer_id INTEGER,        
    supplier_id INTEGER,        
    entered_DATE TEXT,          
    status TEXT DEFAULT 'new',  
    upload_DATE TEXT            
);

CREATE TABLE "files" (
    id SERIAL PRIMARY KEY,
    filename TEXT,
    filepath TEXT,
    upload_DATE DATE
, description TEXT, import_type TEXT);

CREATE TABLE financial_report_mappings (

    id SERIAL PRIMARY KEY,

    report_setting_id INTEGER NOT NULL,

    account_id INTEGER NOT NULL,

    report_section TEXT NOT NULL, 
    report_line TEXT NOT NULL,

    display_order INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (report_setting_id) REFERENCES financial_report_settings(id),

    FOREIGN KEY (account_id) REFERENCES chart_of_accounts(id)

);

CREATE TABLE financial_report_settings (

    id SERIAL PRIMARY KEY,

    report_type TEXT NOT NULL, 
    report_name TEXT NOT NULL,

    description TEXT,

    is_default BOOLEAN DEFAULT FALSE,

    created_by INTEGER NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (created_by) REFERENCES users(id)

);

CREATE TABLE fiscal_periods (

    id SERIAL PRIMARY KEY,

    fiscal_year_id INTEGER NOT NULL,

    period_name TEXT NOT NULL,

    start_DATE DATE NOT NULL,

    end_DATE DATE NOT NULL,

    is_closed BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (fiscal_year_id) REFERENCES fiscal_years(id)

);

CREATE TABLE fiscal_years (

    id SERIAL PRIMARY KEY,

    year_name TEXT NOT NULL,

    start_DATE DATE NOT NULL,

    end_DATE DATE NOT NULL,

    is_closed BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE geographic_deepdives (

    id SERIAL PRIMARY KEY,

    country TEXT NOT NULL,

    tag_id INTEGER NOT NULL,

    title TEXT NOT NULL,

    content TEXT NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (tag_id) REFERENCES industry_tags (id),

    UNIQUE(country, tag_id)

);

CREATE TABLE ignored_domains (

    id SERIAL PRIMARY KEY,

    domain TEXT NOT NULL UNIQUE,

    reason TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    created_by TEXT

);

CREATE TABLE ils_search_results (

    id SERIAL PRIMARY KEY,

    search_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    base_part_number TEXT NOT NULL,

    part_number TEXT NOT NULL,

    ils_company_name TEXT NOT NULL,

    ils_cage_code TEXT,

    supplier_id INTEGER,

    quantity TEXT,

    condition_code TEXT,

    description TEXT,

    price TEXT,

    phone TEXT,

    email TEXT,

    distance TEXT,

    supplier_comment TEXT,

    alt_part_number TEXT,

    exchange TEXT,

    serial_number TEXT,

    fax TEXT,

    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)

);

CREATE TABLE ils_supplier_mappings (

    id SERIAL PRIMARY KEY,

    ils_company_name TEXT NOT NULL,

    ils_cage_code TEXT,

    supplier_id INTEGER,

    created_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    notes TEXT,

    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),

    UNIQUE(ils_company_name, ils_cage_code)

);

CREATE TABLE import_column_maps (

    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL,  
    import_type TEXT NOT NULL,  
    mapping JSON NOT NULL,  
    is_default BOOLEAN DEFAULT FALSE,  
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

, file_id INTEGER);

CREATE TABLE import_headers (

    id SERIAL PRIMARY KEY,

    import_column_map_id INTEGER NOT NULL,

    column_name TEXT NOT NULL,

    sample_value TEXT,

    FOREIGN KEY (import_column_map_id) REFERENCES import_column_maps(id)

);

CREATE TABLE import_settings (

    id SERIAL PRIMARY KEY,

    directory TEXT NOT NULL

, mapping_id INTEGER);

CREATE TABLE import_status (

    id SERIAL PRIMARY KEY,

    file_id INTEGER NOT NULL,

    import_type TEXT NOT NULL,  
    processed INTEGER DEFAULT 0,

    created INTEGER DEFAULT 0,

    skipped INTEGER DEFAULT 0,

    errors TEXT,  
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TEXT, status TEXT, upDATEd INTEGER DEFAULT 0, mapping TEXT,

    FOREIGN KEY (file_id) REFERENCES files(id)

);

CREATE TABLE industries (

    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL

);

CREATE TABLE industry_tags (

    id SERIAL PRIMARY KEY,

    tag TEXT NOT NULL

, description TEXT, parent_tag_id INTEGER);

CREATE TABLE invoice_discounts (

    id SERIAL PRIMARY KEY,

    invoice_id INTEGER NOT NULL,

    discount_type TEXT NOT NULL CHECK (discount_type IN ('Percentage', 'Fixed')),

    discount_value DECIMAL(10,2) NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (invoice_id) REFERENCES invoices(id)

);

CREATE TABLE invoice_files (

    id SERIAL PRIMARY KEY,

    invoice_id INTEGER NOT NULL,

    file_id INTEGER NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (invoice_id) REFERENCES invoices(id),

    FOREIGN KEY (file_id) REFERENCES files(id)

);

CREATE TABLE invoice_lines (

    id SERIAL PRIMARY KEY,

    invoice_id INTEGER NOT NULL,

    sales_order_line_id INTEGER NOT NULL,

    base_part_number TEXT NOT NULL,

    quantity INTEGER NOT NULL,

    unit_price DECIMAL(10,2) NOT NULL,

    line_total DECIMAL(10,2) NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, original_amount DECIMAL(10, 2) NOT NULL DEFAULT 0, converted_amount DECIMAL(10, 2) NOT NULL DEFAULT 0, conversion_rate DECIMAL(10, 6) NOT NULL DEFAULT 1.0, currency_id INTEGER,

    FOREIGN KEY (invoice_id) REFERENCES invoices(id),

    FOREIGN KEY (sales_order_line_id) REFERENCES sales_order_lines(id)

);

CREATE TABLE invoice_payments (

    id SERIAL PRIMARY KEY,

    invoice_id INTEGER NOT NULL,

    payment_DATE DATE NOT NULL,

    payment_method TEXT NOT NULL CHECK (payment_method IN ('Bank Transfer', 'Credit Card', 'PayPal', 'Cheque', 'Cash')),

    amount_paid DECIMAL(10,2) NOT NULL,

    reference TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (invoice_id) REFERENCES invoices(id)

);

CREATE TABLE invoice_taxes (

    id SERIAL PRIMARY KEY,

    invoice_id INTEGER NOT NULL,

    tax_rate_id INTEGER NOT NULL,

    tax_amount DECIMAL(10,2) NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (invoice_id) REFERENCES invoices(id),

    FOREIGN KEY (tax_rate_id) REFERENCES tax_rates(id)

);

CREATE TABLE invoices (

    id SERIAL PRIMARY KEY,

    invoice_number TEXT NOT NULL UNIQUE,

    sales_order_id INTEGER NOT NULL,

    customer_id INTEGER NOT NULL,

    billing_address_id INTEGER NOT NULL,

    invoice_DATE DATE NOT NULL,

    due_DATE DATE NOT NULL,

    currency_id INTEGER NOT NULL,

    total_amount DECIMAL(10,2) NOT NULL,

    status TEXT NOT NULL CHECK (status IN ('Draft', 'Sent', 'Paid', 'Overdue', 'Cancelled')),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (sales_order_id) REFERENCES sales_orders(id),

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (billing_address_id) REFERENCES customer_addresses(id),

    FOREIGN KEY (currency_id) REFERENCES currencies(id)

);

CREATE TABLE journal_entries (

    id SERIAL PRIMARY KEY,

    entry_DATE DATE NOT NULL,

    fiscal_period_id INTEGER NOT NULL,

    journal_entry_type_id INTEGER NOT NULL,

    reference_number TEXT,

    description TEXT,

    currency_id INTEGER NOT NULL,

    exchange_rate DECIMAL(10,6) DEFAULT 1.0,

    is_posted BOOLEAN DEFAULT FALSE,

    created_by INTEGER NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (fiscal_period_id) REFERENCES fiscal_periods(id),

    FOREIGN KEY (journal_entry_type_id) REFERENCES journal_entry_types(id),

    FOREIGN KEY (currency_id) REFERENCES currencies(id),

    FOREIGN KEY (created_by) REFERENCES users(id)

);

CREATE TABLE journal_entry_lines (

    id SERIAL PRIMARY KEY,

    journal_entry_id INTEGER NOT NULL,

    account_id INTEGER NOT NULL,

    description TEXT,

    debit_amount DECIMAL(15,2) DEFAULT 0,

    credit_amount DECIMAL(15,2) DEFAULT 0,

    foreign_amount DECIMAL(15,2),  
    customer_id INTEGER,

    supplier_id INTEGER,

    invoice_id INTEGER,

    purchase_order_id INTEGER,

    project_id INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (journal_entry_id) REFERENCES journal_entries(id),

    FOREIGN KEY (account_id) REFERENCES chart_of_accounts(id),

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),

    FOREIGN KEY (invoice_id) REFERENCES invoices(id),

    FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id),

    FOREIGN KEY (project_id) REFERENCES projects(id)

);

CREATE TABLE journal_entry_types (

    id SERIAL PRIMARY KEY,

    type_name TEXT NOT NULL,

    description TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE manufacturers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL
, merged_into INTEGER REFERENCES manufacturers(id));

CREATE TABLE offer_files (
    offer_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    PRIMARY KEY (offer_id, file_id),
    FOREIGN KEY (offer_id) REFERENCES offers(offer_id),
    FOREIGN KEY (file_id) REFERENCES files(file_id)
);

CREATE TABLE "offer_lines" (

    id SERIAL PRIMARY KEY,

    offer_id INTEGER,

    base_part_number TEXT,

    line_number TEXT,

    manufacturer_id INTEGER,

    quantity INTEGER,

    price NUMERIC,

    lead_time INTEGER,

    requested_base_part_number TEXT, internal_notes TEXT, DATEcode TEXT, spq INTEGER, packaging TEXT, rohs BOOLEAN, coc BOOLEAN,

    FOREIGN KEY(offer_id) REFERENCES offers(id),

    FOREIGN KEY(base_part_number) REFERENCES part_numbers(base_part_number),

    FOREIGN KEY(manufacturer_id) REFERENCES manufacturers(id)

);

CREATE TABLE offers (
    id SERIAL PRIMARY KEY,
    supplier_id INTEGER,
    valid_to DATE,
    supplier_reference TEXT,
    file_id INTEGER, price NUMERIC, lead_time INTEGER, currency_id INTEGER, email_content TEXT,
    FOREIGN KEY(file_id) REFERENCES files(id),
    FOREIGN KEY(supplier_id) REFERENCES "old_suppliers"(id)
);

-- Skipped: old_rfq_lines
-- Skipped: old_rfqs
-- Skipped: old_suppliers
CREATE TABLE part_alt_group_members (

    group_id            INTEGER NOT NULL,

    base_part_number    TEXT NOT NULL,

    PRIMARY KEY (group_id, base_part_number),

    FOREIGN KEY (group_id) REFERENCES part_alt_groups(id),

    FOREIGN KEY (base_part_number) REFERENCES part_numbers(base_part_number)

);

CREATE TABLE part_alt_groups (

    id              SERIAL PRIMARY KEY,

    description     TEXT,               -- optional, e.g. "CR3212-2-2 family"

    created_at      TEXT DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE part_categories (

    category_id SERIAL PRIMARY KEY,

    category_name TEXT NOT NULL,

    description TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE part_manufacturers (
    base_part_number TEXT,
    manufacturer_id INTEGER,
    FOREIGN KEY (base_part_number) REFERENCES part_numbers(base_part_number),
    FOREIGN KEY (manufacturer_id) REFERENCES manufacturers(id)
);

CREATE TABLE "part_numbers" (
    base_part_number TEXT PRIMARY KEY,
    part_number TEXT NOT NULL,
    system_part_number TEXT,
    created_at TIMESTAMP
, stock INTEGER, DATEcode TEXT, target_price DECIMAL(10,2), SPQ INTEGER, packaging TEXT, rohs BOOLEAN, category_id INTEGER REFERENCES part_categories(category_id), mkp_category TEXT);

CREATE TABLE parts_list_line_suggested_suppliers (

    id SERIAL PRIMARY KEY,

    parts_list_line_id INTEGER NOT NULL,

    supplier_id INTEGER NOT NULL,

    source_type TEXT,

    DATE_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (parts_list_line_id) REFERENCES parts_list_lines(id) ON DELETE CASCADE,

    FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE CASCADE,

    UNIQUE(parts_list_line_id, supplier_id)

);

CREATE TABLE parts_list_line_supplier_emails (

    id SERIAL PRIMARY KEY,

    parts_list_line_id INTEGER NOT NULL,

    supplier_id INTEGER NOT NULL,

    DATE_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    sent_by_user_id INTEGER,

    email_subject TEXT,

    email_body TEXT,

    recipient_email TEXT,

    recipient_name TEXT,

    notes TEXT,

    FOREIGN KEY (parts_list_line_id) REFERENCES parts_list_lines(id) ON DELETE CASCADE,

    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),

    FOREIGN KEY (sent_by_user_id) REFERENCES users(id)

);

CREATE TABLE parts_list_line_suppliers (

    id SERIAL PRIMARY KEY,

    parts_list_line_id INTEGER NOT NULL,

    supplier_id INTEGER,

    supplier_name TEXT,  -- For ILS suppliers not in our system

    cost NUMERIC,

    currency_id INTEGER,

    lead_days INTEGER,

    source_type TEXT,  -- 'stock', 'vq', 'po', 'ils', 'manual'

    source_reference TEXT,  -- movement_id, vq_id, po_id, etc.

    condition_code TEXT,  -- For stock/ILS items

    notes TEXT,

    is_preferred BOOLEAN DEFAULT FALSE,  -- Flag one as the recommended option

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (parts_list_line_id) REFERENCES parts_list_lines(id) ON DELETE CASCADE,

    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),

    FOREIGN KEY (currency_id) REFERENCES currencies(id)

);

CREATE TABLE parts_list_lines (

    id SERIAL PRIMARY KEY,

    parts_list_id INTEGER NOT NULL,

      line_number NUMERIC(10,2) NOT NULL,
    customer_part_number TEXT NOT NULL,  -- Raw part number as customer provided it

      base_part_number TEXT,  -- Normalized part number for lookups
      description TEXT,
      category TEXT,

      quantity INTEGER NOT NULL DEFAULT 1,

      parent_line_id INTEGER,
      line_type TEXT NOT NULL DEFAULT 'normal',
    

    -- Chosen/selected options (to be populated later)

    chosen_supplier_id INTEGER,

    chosen_cost NUMERIC,

    chosen_price NUMERIC,

    chosen_currency_id INTEGER,

    chosen_lead_days INTEGER,
    chosen_source_type TEXT,
    chosen_source_reference TEXT,

    

    -- Additional fields

    customer_notes TEXT,  -- Any notes from customer about this line

    internal_notes TEXT,  -- Internal notes about this line

    

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP, chosen_qty INTEGER,

    

      FOREIGN KEY (parts_list_id) REFERENCES parts_lists(id) ON DELETE CASCADE,
      FOREIGN KEY (parent_line_id) REFERENCES parts_list_lines(id) ON DELETE CASCADE,
    FOREIGN KEY (chosen_supplier_id) REFERENCES suppliers(id),

      FOREIGN KEY (chosen_currency_id) REFERENCES currencies(id)

  );

CREATE TABLE project_parts_list_lines (

    id SERIAL PRIMARY KEY,

    project_id INTEGER NOT NULL,

    line_number NUMERIC(10,2) NOT NULL,
    customer_part_number TEXT NOT NULL,
    description TEXT,
    category TEXT,
    comment TEXT,
    line_type TEXT NOT NULL DEFAULT 'normal',
    total_quantity INTEGER,
      usage_by_year TEXT,
      status TEXT DEFAULT 'pending',
      parts_list_id INTEGER REFERENCES parts_lists(id) ON DELETE SET NULL,
      parts_list_line_id INTEGER REFERENCES parts_list_lines(id) ON DELETE SET NULL,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

      FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,

    CONSTRAINT project_parts_list_lines_status_check
        CHECK (status IN ('pending', 'linked', 'no_bid', 'ignore'))

);

CREATE TABLE parts_list_no_response_dismissals (
            id SERIAL PRIMARY KEY,
            email_id INTEGER NOT NULL UNIQUE,
            dismissed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE parts_list_statuses (

    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL UNIQUE,

    display_order INTEGER NOT NULL DEFAULT 0

);

CREATE TABLE parts_list_supplier_quote_lines (
    id SERIAL PRIMARY KEY,
    supplier_quote_id INTEGER NOT NULL,
    parts_list_line_id INTEGER NOT NULL,
    quoted_part_number VARCHAR(100), -- Supplier's part number (may differ from ours)
    manufacturer TEXT,
    quantity_quoted INTEGER,
    unit_price DECIMAL(15,4),
    lead_time_days INTEGER,
    condition_code VARCHAR(10),
    certifications TEXT, -- Free text for certs
    is_no_bid BOOLEAN DEFAULT FALSE,

    line_notes TEXT,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (supplier_quote_id) REFERENCES parts_list_supplier_quotes(id) ON DELETE CASCADE,

    FOREIGN KEY (parts_list_line_id) REFERENCES parts_list_lines(id) ON DELETE CASCADE

);

CREATE TABLE parts_list_supplier_quotes (

    id SERIAL PRIMARY KEY,

    parts_list_id INTEGER NOT NULL,

    supplier_id INTEGER NOT NULL,

    quote_reference VARCHAR(100),

    quote_DATE DATE,

    currency_id INTEGER NOT NULL DEFAULT 3, -- Default to GBP

    notes TEXT,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    created_by_user_id INTEGER,

    email_message_id TEXT,

    email_conversation_id TEXT,

    FOREIGN KEY (parts_list_id) REFERENCES parts_lists(id) ON DELETE CASCADE,

    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),

    FOREIGN KEY (currency_id) REFERENCES currencies(id),

    FOREIGN KEY (created_by_user_id) REFERENCES users(id)

);

CREATE TABLE parts_lists (

    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL,

    customer_id INTEGER,

    salesperson_id INTEGER NOT NULL,

    status_id INTEGER NOT NULL DEFAULT 1,

    notes TEXT,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,

    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (salesperson_id) REFERENCES salespeople(id),

    FOREIGN KEY (status_id) REFERENCES parts_list_statuses(id)

);

CREATE TABLE portal_api_log (

    id SERIAL PRIMARY KEY,

    endpoint TEXT,

    method TEXT,

    portal_user_id INTEGER,

    customer_id INTEGER,

    request_data TEXT,

    response_status INTEGER,

    ip_address TEXT,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (portal_user_id) REFERENCES portal_users(id),

    FOREIGN KEY (customer_id) REFERENCES customers(id)

);

CREATE TABLE portal_customer_margins (

    id SERIAL PRIMARY KEY,

    customer_id INTEGER NOT NULL,

    stock_margin_percentage NUMERIC,          -- Override for stock pricing margin

    vq_margin_percentage NUMERIC,             -- Override for VQ estimate margin

    po_margin_percentage NUMERIC,             -- Override for PO estimate margin

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,

    UNIQUE(customer_id)  -- One margin setting per customer

);

CREATE TABLE portal_customer_pricing (

    id SERIAL PRIMARY KEY,

    customer_id INTEGER NOT NULL,

    base_part_number TEXT NOT NULL,

    price NUMERIC NOT NULL,

    currency_id INTEGER NOT NULL DEFAULT 3,  -- Default GBP

    valid_from DATE,                          -- Start DATE (NULL = immediate)

    valid_until DATE,                         -- End DATE (NULL = no expiry)

    notes TEXT,

    is_active BOOLEAN DEFAULT TRUE,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,

    FOREIGN KEY (currency_id) REFERENCES currencies(id)

);

CREATE TABLE portal_pricing_agreement_requests (

    id SERIAL PRIMARY KEY,

    portal_user_id INTEGER NOT NULL,

    customer_id INTEGER NOT NULL,

    part_number TEXT NOT NULL,

    base_part_number TEXT NOT NULL,

    quantity INTEGER DEFAULT 1,

    reference_number TEXT UNIQUE NOT NULL,

    customer_notes TEXT,

    internal_notes TEXT,

    status TEXT DEFAULT 'pending', -- pending, approved, rejected

    DATE_submitted TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_processed TIMESTAMP,

    processed_by_user_id INTEGER,

    FOREIGN KEY (portal_user_id) REFERENCES portal_users(id),

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (processed_by_user_id) REFERENCES users(id)

);

CREATE TABLE portal_purchase_order_lines (

    id SERIAL PRIMARY KEY,

    

    portal_purchase_order_id INTEGER NOT NULL,

    line_number INTEGER NOT NULL,

    

    -- Part Information

    part_number VARCHAR(100) NOT NULL,

    base_part_number VARCHAR(100) NOT NULL,

    description TEXT,

    

    -- Quantity & Pricing

    quantity INTEGER NOT NULL,

    unit_price DECIMAL(10,2) NOT NULL,

    line_total DECIMAL(10,2) NOT NULL,

    

    -- Source of pricing (for reference)

    price_source VARCHAR(50),  -- 'pricing_agreement', 'stock', 'quoted', etc.

    portal_quote_request_line_id INTEGER,  -- Link to original quote line if applicable

    

    -- Fulfillment Status

    status VARCHAR(50) DEFAULT 'pending',  -- pending, allocated, picked, shipped, delivered

    quantity_shipped INTEGER DEFAULT 0,

    DATE_shipped DATETIME,

    

    -- Stock Movement Reference (when shipped)

    stock_movement_id INTEGER,

    

    -- Notes

    notes TEXT,

    

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    

    FOREIGN KEY (portal_purchase_order_id) REFERENCES portal_purchase_orders(id) ON DELETE CASCADE,

    FOREIGN KEY (portal_quote_request_line_id) REFERENCES portal_quote_request_lines(id),

    FOREIGN KEY (stock_movement_id) REFERENCES stock_movements(id)

);

CREATE TABLE portal_purchase_orders (

    id SERIAL PRIMARY KEY,

    

    -- Reference & Relationships

    portal_user_id INTEGER NOT NULL,

    customer_id INTEGER NOT NULL,

    portal_quote_request_id INTEGER,  -- Link to original quote request if applicable

    po_reference VARCHAR(100) NOT NULL UNIQUE,  -- Customer's PO reference number

    

    -- Order Details

    total_value DECIMAL(10,2) NOT NULL,

    currency_id INTEGER DEFAULT 3,  -- Default to GBP

    line_count INTEGER NOT NULL,

    

    -- Status & Tracking

    status VARCHAR(50) DEFAULT 'submitted',  -- submitted, acknowledged, processing, dispatched, completed, cancelled

    DATE_submitted TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_acknowledged DATETIME,

    DATE_dispatched DATETIME,

    

    -- Customer Information

    customer_notes TEXT,

    

    -- Delivery Address

    delivery_company VARCHAR(200),

    delivery_street VARCHAR(200),

    delivery_city VARCHAR(100),

    delivery_zip VARCHAR(20),

    delivery_country VARCHAR(100),

    

    -- Invoice Address

    invoice_company VARCHAR(200),

    invoice_street VARCHAR(200),

    invoice_city VARCHAR(100),

    invoice_zip VARCHAR(20),

    invoice_country VARCHAR(100),

    same_as_delivery BOOLEAN DEFAULT TRUE,

    

    -- Authorization/Confirmation

    authorizer_name VARCHAR(200),  -- Person who confirmed the PO

    authorizer_title VARCHAR(200),  -- Their job title

    authorization_timestamp DATETIME,

    

    -- Internal Notes

    internal_notes TEXT,

    assigned_to INTEGER,  -- User ID of person handling this order

    

    -- Timestamps

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    

    FOREIGN KEY (portal_user_id) REFERENCES portal_users(id),

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (portal_quote_request_id) REFERENCES portal_quote_requests(id),

    FOREIGN KEY (currency_id) REFERENCES currencies(id),

    FOREIGN KEY (assigned_to) REFERENCES users(id)

);

CREATE TABLE portal_quote_request_lines (

    id SERIAL PRIMARY KEY,

    portal_quote_request_id INTEGER NOT NULL,

    line_number INTEGER,

    part_number TEXT NOT NULL,

    base_part_number TEXT,

    quantity INTEGER NOT NULL,

    quoted_price NUMERIC,

    quoted_currency_id INTEGER,

    quoted_lead_days INTEGER,

    status TEXT DEFAULT 'pending', -- pending, quoted, no_bid

    FOREIGN KEY (portal_quote_request_id) REFERENCES portal_quote_requests(id),

    FOREIGN KEY (quoted_currency_id) REFERENCES currencies(id)

);

CREATE TABLE portal_quote_requests (

    id SERIAL PRIMARY KEY,

    portal_user_id INTEGER NOT NULL,

    customer_id INTEGER NOT NULL,

    parts_list_id INTEGER,

    reference_number TEXT UNIQUE,

    status TEXT DEFAULT 'pending', -- pending, processing, quoted, declined

    customer_notes TEXT,

    DATE_submitted TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_processed DATETIME,

    processed_by_user_id INTEGER,

    FOREIGN KEY (portal_user_id) REFERENCES portal_users(id),

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (parts_list_id) REFERENCES parts_lists(id),

    FOREIGN KEY (processed_by_user_id) REFERENCES users(id)

);

CREATE TABLE portal_search_history (

    id SERIAL PRIMARY KEY,

    portal_user_id INTEGER NOT NULL,

    customer_id INTEGER NOT NULL,

    search_type TEXT NOT NULL, -- 'quote_analysis', 'common_parts', 'pricing_agreements', 'suggested_parts'

    parts_searched TEXT, -- JSON array of parts searched

    parts_count INTEGER DEFAULT 0,

    DATE_searched TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    ip_address TEXT,

    user_agent TEXT,

    

    FOREIGN KEY (portal_user_id) REFERENCES portal_users(id),

    FOREIGN KEY (customer_id) REFERENCES customers(id)

);

CREATE TABLE portal_settings (

    id SERIAL PRIMARY KEY,

    setting_key TEXT NOT NULL UNIQUE,

    setting_value TEXT,

    description TEXT,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE portal_suggested_parts (

    id SERIAL PRIMARY KEY,

    customer_id INTEGER NOT NULL,

    base_part_number TEXT NOT NULL,

    notes TEXT,

    priority INTEGER DEFAULT 0,

    is_active BOOLEAN DEFAULT TRUE,

    suggested_by_user_id INTEGER,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    DATE_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (suggested_by_user_id) REFERENCES users(id)

);

CREATE TABLE portal_users (

    id SERIAL PRIMARY KEY,

    customer_id INTEGER NOT NULL,

    email TEXT NOT NULL UNIQUE,

    password_hash TEXT NOT NULL,

    first_name TEXT,

    last_name TEXT,

    is_active BOOLEAN DEFAULT TRUE,

    last_login DATETIME,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id)

);

CREATE TABLE price_breaks (
    id SERIAL PRIMARY KEY,
    price_list_item_id INTEGER,
    quantity INTEGER,
    price NUMERIC,
    FOREIGN KEY (price_list_item_id) REFERENCES price_list_items(id)
);

CREATE TABLE price_list_items (
    id SERIAL PRIMARY KEY,
    price_list_id INTEGER,
    part_number TEXT,
    base_part_number TEXT,
    lead_time INTEGER,
    FOREIGN KEY (price_list_id) REFERENCES price_lists(id)
);

CREATE TABLE price_lists (
    id SERIAL PRIMARY KEY,
    supplier_id INTEGER,
    valid_from DATE,
    valid_to DATE,
    name_reference TEXT,
    currency_id INTEGER,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (currency_id) REFERENCES currencies(id)
);

CREATE TABLE priorities (

    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL UNIQUE,

    color TEXT NOT NULL

);

CREATE TABLE project_files (

    project_id INTEGER NOT NULL,

    file_id INTEGER NOT NULL,

    PRIMARY KEY (project_id, file_id),

    FOREIGN KEY (project_id) REFERENCES projects(id),

    FOREIGN KEY (file_id) REFERENCES files(id)

);

CREATE TABLE project_rfqs (

    project_id INTEGER NOT NULL,

    rfq_id INTEGER NOT NULL,

    PRIMARY KEY (project_id, rfq_id),

    FOREIGN KEY (project_id) REFERENCES projects(id),

    FOREIGN KEY (rfq_id) REFERENCES rfqs(id)

);

CREATE TABLE project_stage_salespeople (

    stage_id INTEGER NOT NULL,

    salesperson_id INTEGER NOT NULL,

    PRIMARY KEY (stage_id, salesperson_id),

    FOREIGN KEY (stage_id) REFERENCES project_stages(id),

    FOREIGN KEY (salesperson_id) REFERENCES salespeople(id)

);

CREATE TABLE project_stages (

    id SERIAL PRIMARY KEY,

    project_id INTEGER NOT NULL,

    name TEXT NOT NULL,

    description TEXT,

    parent_stage_id INTEGER,

    stage_order INTEGER, 
    status_id INTEGER NOT NULL,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    due_DATE TIMESTAMP,

    recurrence_id INTEGER,

    FOREIGN KEY (project_id) REFERENCES projects(id),

    FOREIGN KEY (parent_stage_id) REFERENCES project_stages(id),

    FOREIGN KEY (status_id) REFERENCES project_statuses(id),

    FOREIGN KEY (recurrence_id) REFERENCES recurrence_types(id)

);

CREATE TABLE project_statuses (

    id SERIAL PRIMARY KEY,

    status TEXT NOT NULL

);

CREATE TABLE project_upDATEs (

    id SERIAL PRIMARY KEY,

    project_id INTEGER NOT NULL,

    salesperson_id INTEGER NOT NULL,

    comment TEXT NOT NULL,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP, stage_id INTEGER,

    FOREIGN KEY (project_id) REFERENCES projects(id),

    FOREIGN KEY (salesperson_id) REFERENCES salespeople(id)

);

CREATE TABLE projects (

    id SERIAL PRIMARY KEY,

    customer_id INTEGER NOT NULL,

    salesperson_id INTEGER NOT NULL,

    status_id INTEGER DEFAULT 1, name TEXT, description TEXT, next_stage_id INTEGER REFERENCES project_stages(id), next_stage_deadline TIMESTAMP, estimated_value DECIMAL(10,2),

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (salesperson_id) REFERENCES salespeople(id),

    FOREIGN KEY (status_id) REFERENCES project_statuses(id)

);

CREATE TABLE purchase_order_lines (id SERIAL PRIMARY KEY, purchase_order_id INTEGER NOT NULL, line_number INTEGER NOT NULL, base_part_number TEXT NOT NULL, quantity INTEGER NOT NULL, price NUMERIC NOT NULL, ship_DATE DATE, promised_DATE DATE, status_id INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, sales_order_line_id INTEGER, received_quantity INTEGER DEFAULT 0);

CREATE TABLE purchase_order_statuses (

    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL,

    description TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE purchase_orders (id SERIAL PRIMARY KEY, purchase_order_ref TEXT NOT NULL, supplier_id INTEGER NOT NULL, DATE_issued DATE NOT NULL, incoterms TEXT, payment_terms TEXT, purchase_status_id INTEGER NOT NULL, currency_id INTEGER NOT NULL, delivery_address_id INTEGER, billing_address_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total_value NUMERIC);

CREATE TABLE reconciliation_items (

    id SERIAL PRIMARY KEY,

    reconciliation_id INTEGER NOT NULL,

    journal_entry_line_id INTEGER NOT NULL,

    is_cleared BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (reconciliation_id) REFERENCES account_reconciliations(id),

    FOREIGN KEY (journal_entry_line_id) REFERENCES journal_entry_lines(id)

);

CREATE TABLE recurrence_types (

    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL,

    interval INTEGER NOT NULL 
);

CREATE TABLE recurring_journal_template_lines (

    id SERIAL PRIMARY KEY,

    template_id INTEGER NOT NULL,

    account_id INTEGER NOT NULL,

    description TEXT,

    debit_amount DECIMAL(15,2) DEFAULT 0,

    credit_amount DECIMAL(15,2) DEFAULT 0,

    distribution_type TEXT DEFAULT 'fixed', 
    customer_id INTEGER,

    supplier_id INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (template_id) REFERENCES recurring_journal_templates(id),

    FOREIGN KEY (account_id) REFERENCES chart_of_accounts(id),

    FOREIGN KEY (customer_id) REFERENCES customers(id),

    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)

);

CREATE TABLE recurring_journal_templates (

    id SERIAL PRIMARY KEY,

    template_name TEXT NOT NULL,

    description TEXT,

    journal_entry_type_id INTEGER NOT NULL,

    frequency TEXT NOT NULL, 
    next_DATE DATE NOT NULL,

    end_DATE DATE,

    is_active BOOLEAN DEFAULT TRUE,

    created_by INTEGER NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (journal_entry_type_id) REFERENCES journal_entry_types(id),

    FOREIGN KEY (created_by) REFERENCES users(id)

);

CREATE TABLE requisition_references (
    id SERIAL PRIMARY KEY,
    top_level_requisition_id INTEGER,
    requisition_id INTEGER,
    FOREIGN KEY (top_level_requisition_id) REFERENCES top_level_requisitions(id),
    FOREIGN KEY (requisition_id) REFERENCES requisitions(id)
);

CREATE TABLE requisitions (
            id SERIAL PRIMARY KEY,
            rfq_id INTEGER,
            supplier_id INTEGER,
            DATE TEXT,
            base_part_number TEXT,
            quantity INTEGER, rfq_line_id INTEGER,
            FOREIGN KEY (rfq_id) REFERENCES "old_rfqs"(id),
            FOREIGN KEY (supplier_id) REFERENCES "old_suppliers"(id)
        );

CREATE TABLE rfq_files (
    rfq_id INTEGER,
    file_id INTEGER,
    PRIMARY KEY (rfq_id, file_id),
    FOREIGN KEY (rfq_id) REFERENCES "old_rfqs"(id),
    FOREIGN KEY (file_id) REFERENCES files(id)
);

CREATE TABLE rfq_line_part_alternatives (

    rfq_line_id INTEGER,

    primary_base_part_number VARCHAR(50),

    alternative_base_part_number VARCHAR(50),

    PRIMARY KEY (rfq_line_id, primary_base_part_number, alternative_base_part_number),

    FOREIGN KEY (rfq_line_id) REFERENCES rfq_lines(id)

);

CREATE TABLE "rfq_lines" (

    id SERIAL PRIMARY KEY,

    rfq_id INTEGER,

    line_number TEXT,

    base_part_number TEXT,

    quantity INTEGER,

    suggested_suppliers TEXT,

    chosen_supplier INTEGER,

    cost NUMERIC,

    supplier_lead_time INTEGER,

    margin NUMERIC,

    price NUMERIC,

    lead_time INTEGER,

    line_value NUMERIC,

    note TEXT,

    internal_notes TEXT,

    manufacturer_id INTEGER,

    offer_id INTEGER,

    status_id INTEGER,

    cost_currency INTEGER REFERENCES currencies(id),

    base_cost NUMERIC,

    offered_base_part_number TEXT

, DATEcode TEXT, taret_price DECIMAL(10,2), spq INTEGER, packaging TEXT, rohs BOOLEAN, coc BOOLEAN);

CREATE TABLE rfq_upDATEs (

    id SERIAL PRIMARY KEY,

    rfq_id INTEGER NOT NULL,

    user_id INTEGER NOT NULL,

    upDATE_text TEXT,

    upDATE_type TEXT NOT NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (rfq_id) REFERENCES rfqs(id),

    FOREIGN KEY (user_id) REFERENCES users(id)

);

CREATE TABLE rfqs (
    id SERIAL PRIMARY KEY,
    entered_DATE TEXT,
    customer_id INTEGER,
    contact_id INTEGER,
    customer_ref TEXT,
    currency INTEGER REFERENCES currencies(id),
    status TEXT,
    email TEXT,
    salesperson_id INTEGER,
    primary_file_id INTEGER
);

CREATE TABLE sales_order_lines (id SERIAL PRIMARY KEY, sales_order_id INTEGER NOT NULL, line_number INTEGER NOT NULL, base_cost NUMERIC, price NUMERIC NOT NULL, quantity INTEGER NOT NULL, delivery_DATE DATE, requested_DATE DATE, promise_DATE DATE, ship_DATE DATE, sales_status_id INTEGER NOT NULL, note TEXT, rfq_line_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, base_part_number TEXT, shipped BOOLEAN DEFAULT FALSE, shipped_quantity NUMERIC DEFAULT 0);

CREATE TABLE sales_orders (id SERIAL PRIMARY KEY, sales_order_ref TEXT NOT NULL, customer_id INTEGER NOT NULL, customer_po_ref TEXT, salesperson_id INTEGER NOT NULL, contact_name TEXT, DATE_entered DATE NOT NULL, incoterms TEXT, payment_terms TEXT, sales_status_id INTEGER NOT NULL, currency_id INTEGER NOT NULL, shipping_address_id INTEGER, invoicing_address_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total_value NUMERIC);

CREATE TABLE sales_statuses (id SERIAL PRIMARY KEY, status_name TEXT NOT NULL);

CREATE TABLE salespeople (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL
, system_ref TEXT, is_active BOOLEAN DEFAULT TRUE);

CREATE TABLE salesperson_engagement_settings (
            salesperson_id SERIAL PRIMARY KEY,
            overdue_threshold_days INTEGER DEFAULT 14,
            customer_status_filter TEXT,  -- JSON array of status IDs
            contact_status_filter TEXT,   -- JSON array of status IDs
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE salesperson_monthly_goals (

    id SERIAL PRIMARY KEY,

    salesperson_id INTEGER NOT NULL,

    target_month TEXT NOT NULL, -- 'YYYY-MM'

    goal_amount NUMERIC DEFAULT 0,

    upDATEd_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(salesperson_id, target_month)

);

CREATE TABLE salesperson_user_link (

    id SERIAL PRIMARY KEY,

    user_id INTEGER NOT NULL,

    legacy_salesperson_id INTEGER NOT NULL,

    FOREIGN KEY (user_id) REFERENCES users (id),

    FOREIGN KEY (legacy_salesperson_id) REFERENCES salespeople (id)

);

CREATE TABLE saved_queries (

    id SERIAL PRIMARY KEY,

    query_name TEXT NOT NULL,

    query TEXT NOT NULL,

    chart_type TEXT NOT NULL,

    label_column_1 TEXT NOT NULL,

    label_column_2 TEXT,

    value_column_1 TEXT NOT NULL,

    value_column_2 TEXT,

    DATE_saved TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE settings (

    key TEXT PRIMARY KEY,

    value TEXT NOT NULL

);

-- Skipped: sqlite_sequence
CREATE TABLE stage_files (

    stage_id INTEGER NOT NULL,

    file_id INTEGER NOT NULL,

    PRIMARY KEY (stage_id, file_id),

    FOREIGN KEY (stage_id) REFERENCES project_stages(id) ON DELETE CASCADE,

    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE

);

CREATE TABLE stage_upDATEs (

    id SERIAL PRIMARY KEY,

    stage_id INTEGER NOT NULL,

    salesperson_id INTEGER NOT NULL,

    comment TEXT NOT NULL,

    DATE_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (stage_id) REFERENCES project_stages(id) ON DELETE CASCADE,

    FOREIGN KEY (salesperson_id) REFERENCES salespeople(id) ON DELETE SET NULL

);

CREATE TABLE statuses (
    id SERIAL PRIMARY KEY,
    status TEXT NOT NULL UNIQUE
);

CREATE TABLE stock_movements (

    movement_id SERIAL PRIMARY KEY,

    base_part_number TEXT REFERENCES part_numbers(base_part_number),

    movement_type TEXT NOT NULL CHECK (movement_type IN ('IN', 'OUT')),

    quantity INTEGER NOT NULL,

    DATEcode TEXT,

    cost_per_unit DECIMAL(10,2),

    movement_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    reference TEXT,

    notes TEXT

, available_quantity INTEGER, parent_movement_id INTEGER);

CREATE TABLE "supplier_contacts" (

    id SERIAL PRIMARY KEY,

    customer_id INTEGER,  
    first_name TEXT NOT NULL,

    second_name TEXT NOT NULL,

    email_address TEXT NOT NULL,

    supplier_id INTEGER REFERENCES suppliers(id),

    FOREIGN KEY (customer_id) REFERENCES customers(id)

);

CREATE TABLE supplier_domains (

    id SERIAL PRIMARY KEY,

    supplier_id INTEGER NOT NULL,

    domain TEXT NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),

    UNIQUE(supplier_id, domain)

);

CREATE TABLE suppliers (
    id SERIAL PRIMARY KEY,
    name TEXT,
    contact_name TEXT,
    contact_email TEXT,
    contact_phone TEXT,
    buffer INTEGER,
    currency INTEGER REFERENCES currencies(id),
    fornitore TEXT
, delivery_cost DECIMAL(10, 2) DEFAULT 0, minimum_line_value DECIMAL(10, 2) DEFAULT 0, standard_condition TEXT, standard_certs TEXT, warning TEXT);

CREATE TABLE sync_metadata (

    id SERIAL PRIMARY KEY,

    last_synced_DATE DATETIME NOT NULL

);

CREATE TABLE tax_rates (

    id SERIAL PRIMARY KEY,

    tax_name TEXT NOT NULL,

    tax_percentage DECIMAL(5,2) NOT NULL,

    country TEXT NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE template_industry_tags (

    template_id INTEGER,

    industry_tag_id INTEGER,

    PRIMARY KEY (template_id, industry_tag_id),

    FOREIGN KEY (template_id) REFERENCES email_templates(id) ON DELETE CASCADE,

    FOREIGN KEY (industry_tag_id) REFERENCES industry_tags(id) ON DELETE CASCADE

);

CREATE TABLE template_placeholders (

    id SERIAL PRIMARY KEY,

    placeholder_key TEXT NOT NULL UNIQUE,

    description TEXT NOT NULL,

    example_value TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);

CREATE TABLE top_level_requisitions (
    id SERIAL PRIMARY KEY,
    created_at TEXT,
    reference TEXT
);

CREATE TABLE user_permissions (

    id SERIAL PRIMARY KEY,

    user_id INTEGER NOT NULL,

    permissions INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (user_id) REFERENCES users (id),

    UNIQUE(user_id)

);

CREATE TABLE user_roles (

    id SERIAL PRIMARY KEY,

    name TEXT UNIQUE NOT NULL

);

CREATE TABLE users (

    id SERIAL PRIMARY KEY,

    username TEXT UNIQUE NOT NULL,

    password_hash TEXT NOT NULL,

    user_type TEXT NOT NULL DEFAULT 'normal' CHECK (user_type IN ('admin', 'normal', 'view_only')),

    role TEXT,

    picture_url TEXT,

    is_active BOOLEAN DEFAULT TRUE,  
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    created_by INTEGER,

    modified_at TIMESTAMP,

    modified_by INTEGER

, email TEXT);

CREATE TABLE vq_lines (

    id SERIAL PRIMARY KEY,

    vq_id INTEGER NOT NULL,

    vendor_response_id TEXT,

    transaction_id TEXT,

    transaction_item_id TEXT,

    base_part_number TEXT,

    part_number TEXT,

    pn_quoted TEXT,

    description TEXT,

    condition_code TEXT,

    quantity_quoted INTEGER,

    quantity_requested INTEGER,

    unit_of_measure TEXT DEFAULT 'EA',

    lead_days INTEGER,

    vendor_price DECIMAL(10, 2),

    item_total DECIMAL(10, 2),

    line_number INTEGER, foreign_currency TEXT, quoted_DATE DATE,

    FOREIGN KEY (vq_id) REFERENCES vqs(id) ON DELETE CASCADE

);

CREATE TABLE vqs (

    id SERIAL PRIMARY KEY,

    vq_number TEXT UNIQUE NOT NULL,

    supplier_id INTEGER,

    status TEXT DEFAULT 'Created',

    entry_DATE DATE,

    expiration_DATE DATE,

    currency_id INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),

    FOREIGN KEY (currency_id) REFERENCES currencies(id)

);

CREATE TABLE watched_industry_tags (

    id SERIAL PRIMARY KEY,

    user_id INTEGER NOT NULL,

    tag_id INTEGER NOT NULL,

    FOREIGN KEY (user_id) REFERENCES users(id),

    FOREIGN KEY (tag_id) REFERENCES industry_tags(id),

    UNIQUE (user_id, tag_id) 
);
