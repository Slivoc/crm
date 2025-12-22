--
-- PostgreSQL database dump
--

\restrict Zhyhvs17HdmeDgwj2hh8yCrP9IGxHrIKnkDrmIF1n9wuInAGtU64RoJByV7qecT

-- Dumped from database version 18.1
-- Dumped by pg_dump version 18.1

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS '';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: account_activity_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.account_activity_log (
    id integer NOT NULL,
    account_id integer NOT NULL,
    journal_entry_id integer NOT NULL,
    transaction_date date NOT NULL,
    debit_amount numeric(15,2) DEFAULT 0,
    credit_amount numeric(15,2) DEFAULT 0,
    balance_after numeric(15,2) NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: account_activity_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.account_activity_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: account_activity_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.account_activity_log_id_seq OWNED BY public.account_activity_log.id;


--
-- Name: account_reconciliations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.account_reconciliations (
    id integer NOT NULL,
    account_id integer NOT NULL,
    statement_date date NOT NULL,
    statement_balance numeric(15,2) NOT NULL,
    is_reconciled boolean DEFAULT false,
    reconciled_by integer,
    reconciled_at timestamp without time zone,
    notes text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: account_reconciliations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.account_reconciliations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: account_reconciliations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.account_reconciliations_id_seq OWNED BY public.account_reconciliations.id;


--
-- Name: account_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.account_types (
    id integer NOT NULL,
    name text NOT NULL,
    description text,
    normal_balance text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: account_types_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.account_types_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: account_types_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.account_types_id_seq OWNED BY public.account_types.id;


--
-- Name: acknowledgments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.acknowledgments (
    id integer NOT NULL,
    sales_order_id integer NOT NULL,
    acknowledgment_pdf text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: acknowledgments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.acknowledgments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: acknowledgments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.acknowledgments_id_seq OWNED BY public.acknowledgments.id;


--
-- Name: ai_tag_suggestions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_tag_suggestions (
    id integer NOT NULL,
    customer_id integer,
    suggested_tag text,
    frequency integer DEFAULT 1,
    reviewed boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: ai_tag_suggestions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ai_tag_suggestions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ai_tag_suggestions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ai_tag_suggestions_id_seq OWNED BY public.ai_tag_suggestions.id;


--
-- Name: alternative_part_numbers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alternative_part_numbers (
    id integer NOT NULL,
    part_number_id integer NOT NULL,
    customer text NOT NULL,
    customer_part_number text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: alternative_part_numbers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.alternative_part_numbers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: alternative_part_numbers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.alternative_part_numbers_id_seq OWNED BY public.alternative_part_numbers.id;


--
-- Name: app_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_settings (
    key text NOT NULL,
    value text NOT NULL
);


--
-- Name: bom_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bom_files (
    bom_header_id integer NOT NULL,
    file_id integer NOT NULL
);


--
-- Name: bom_headers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bom_headers (
    id integer NOT NULL,
    base_part_number text,
    name text NOT NULL,
    description text,
    type text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: bom_headers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bom_headers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bom_headers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bom_headers_id_seq OWNED BY public.bom_headers.id;


--
-- Name: bom_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bom_lines (
    id integer NOT NULL,
    bom_header_id integer NOT NULL,
    parent_line_id integer,
    base_part_number text NOT NULL,
    quantity integer NOT NULL,
    reference_designator text,
    notes text,
    "position" integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    guide_price numeric(10,2) DEFAULT NULL::numeric
);


--
-- Name: bom_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bom_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bom_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bom_lines_id_seq OWNED BY public.bom_lines.id;


--
-- Name: bom_pricing; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bom_pricing (
    id integer NOT NULL,
    bom_line_id integer NOT NULL,
    offer_line_id integer NOT NULL,
    notes text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: bom_pricing_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bom_pricing_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bom_pricing_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bom_pricing_id_seq OWNED BY public.bom_pricing.id;


--
-- Name: bom_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bom_revisions (
    id integer NOT NULL,
    bom_header_id integer NOT NULL,
    revision text NOT NULL,
    effective_date date NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    created_by integer NOT NULL,
    notes text
);


--
-- Name: bom_revisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bom_revisions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bom_revisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bom_revisions_id_seq OWNED BY public.bom_revisions.id;


--
-- Name: call_list; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_list (
    id integer NOT NULL,
    contact_id integer NOT NULL,
    salesperson_id integer NOT NULL,
    added_date timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    notes text,
    priority integer DEFAULT 0,
    is_active boolean DEFAULT true,
    removed_date timestamp without time zone
);


--
-- Name: call_list_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.call_list_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: call_list_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.call_list_id_seq OWNED BY public.call_list.id;


--
-- Name: chart_of_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chart_of_accounts (
    id integer NOT NULL,
    account_number text NOT NULL,
    account_name text NOT NULL,
    account_type_id integer NOT NULL,
    parent_account_id integer,
    description text,
    is_active boolean DEFAULT true,
    balance numeric(15,2) DEFAULT 0,
    currency_id integer NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: chart_of_accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.chart_of_accounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chart_of_accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.chart_of_accounts_id_seq OWNED BY public.chart_of_accounts.id;


--
-- Name: company_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_types (
    id integer NOT NULL,
    type text NOT NULL,
    description text,
    parent_type_id integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: company_types_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.company_types_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: company_types_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.company_types_id_seq OWNED BY public.company_types.id;


--
-- Name: contact_communications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contact_communications (
    id integer NOT NULL,
    date text DEFAULT CURRENT_TIMESTAMP NOT NULL,
    contact_id integer NOT NULL,
    customer_id integer NOT NULL,
    salesperson_id integer NOT NULL,
    communication_type text NOT NULL,
    notes text,
    update_id integer
);


--
-- Name: contact_communications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contact_communications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contact_communications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contact_communications_id_seq OWNED BY public.contact_communications.id;


--
-- Name: contact_list_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contact_list_members (
    id integer NOT NULL,
    list_id integer NOT NULL,
    contact_id integer NOT NULL,
    added_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: contact_list_members_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contact_list_members_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contact_list_members_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contact_list_members_id_seq OWNED BY public.contact_list_members.id;


--
-- Name: contact_lists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contact_lists (
    id integer NOT NULL,
    name text
);


--
-- Name: contact_lists_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contact_lists_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contact_lists_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contact_lists_id_seq OWNED BY public.contact_lists.id;


--
-- Name: contact_statuses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contact_statuses (
    id integer NOT NULL,
    name character varying(50) NOT NULL,
    description text,
    color character varying(7) DEFAULT '#6c757d'::character varying,
    is_active boolean DEFAULT true,
    sort_order integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: contact_statuses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contact_statuses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contact_statuses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contact_statuses_id_seq OWNED BY public.contact_statuses.id;


--
-- Name: contacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contacts (
    id integer NOT NULL,
    customer_id integer,
    name text NOT NULL,
    email text NOT NULL,
    job_title text,
    second_name text,
    notes text,
    updated_at timestamp without time zone,
    status_id integer DEFAULT 1,
    phone text,
    timezone text DEFAULT 'UTC'::text
);


--
-- Name: contacts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contacts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contacts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contacts_id_seq OWNED BY public.contacts.id;


--
-- Name: cq_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cq_lines (
    id integer NOT NULL,
    cq_id integer NOT NULL,
    transaction_header_id text,
    transaction_item_id text,
    base_part_number text NOT NULL,
    part_number text NOT NULL,
    description text,
    condition_code text,
    quantity_requested integer DEFAULT 0,
    quantity_quoted integer DEFAULT 0,
    quantity_allocated integer DEFAULT 0,
    unit_of_measure text DEFAULT 'EA'::text,
    tran_type text,
    base_currency text,
    foreign_currency text,
    unit_cost numeric DEFAULT 0.0,
    unit_price numeric DEFAULT 0.0,
    total_price numeric DEFAULT 0.0,
    total_foreign_price numeric DEFAULT 0.0,
    tax numeric DEFAULT 0.0,
    total_cost numeric DEFAULT 0.0,
    for_price numeric DEFAULT 0.0,
    lead_days integer DEFAULT 0,
    created_by text,
    sales_person text,
    core_charge numeric DEFAULT 0.0,
    traceability text,
    is_no_quote boolean DEFAULT false,
    line_number integer DEFAULT 0,
    serial_number text,
    tag_or_certificate_number text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: cq_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cq_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cq_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cq_lines_id_seq OWNED BY public.cq_lines.id;


--
-- Name: cqs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cqs (
    id integer NOT NULL,
    cq_number text NOT NULL,
    customer_id integer NOT NULL,
    status text DEFAULT 'Created'::text,
    entry_date date,
    due_date date,
    currency_id integer,
    sales_person text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: cqs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cqs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cqs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cqs_id_seq OWNED BY public.cqs.id;


--
-- Name: currencies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.currencies (
    id integer NOT NULL,
    currency_code text NOT NULL,
    exchange_rate_to_eur numeric NOT NULL,
    symbol text
);


--
-- Name: currencies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.currencies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: currencies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.currencies_id_seq OWNED BY public.currencies.id;


--
-- Name: customer_addresses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_addresses (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    address text NOT NULL,
    city text NOT NULL,
    postal_code text NOT NULL,
    country text NOT NULL,
    is_default_shipping boolean DEFAULT false,
    is_default_invoicing boolean DEFAULT false
);


--
-- Name: customer_addresses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_addresses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_addresses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_addresses_id_seq OWNED BY public.customer_addresses.id;


--
-- Name: customer_associations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_associations (
    id integer NOT NULL,
    main_customer_id integer NOT NULL,
    associated_customer_id integer NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    notes text,
    CONSTRAINT customer_associations_check CHECK ((main_customer_id <> associated_customer_id))
);


--
-- Name: customer_associations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_associations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_associations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_associations_id_seq OWNED BY public.customer_associations.id;


--
-- Name: customer_boms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_boms (
    customer_id integer NOT NULL,
    bom_header_id integer NOT NULL,
    reference text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: customer_company_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_company_types (
    customer_id integer NOT NULL,
    company_type_id integer NOT NULL
);


--
-- Name: customer_development_answers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_development_answers (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    development_point_id integer NOT NULL,
    answer text,
    answered_by integer,
    answered_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: customer_development_answers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_development_answers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_development_answers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_development_answers_id_seq OWNED BY public.customer_development_answers.id;


--
-- Name: customer_domains; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_domains (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    domain text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: customer_domains_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_domains_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_domains_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_domains_id_seq OWNED BY public.customer_domains.id;


--
-- Name: customer_enrichment_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_enrichment_status (
    customer_id integer NOT NULL,
    status text,
    last_attempt timestamp without time zone,
    error_message text,
    attempts integer DEFAULT 0
);


--
-- Name: customer_enrichment_status_customer_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_enrichment_status_customer_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_enrichment_status_customer_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_enrichment_status_customer_id_seq OWNED BY public.customer_enrichment_status.customer_id;


--
-- Name: customer_industries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_industries (
    customer_id integer NOT NULL,
    industry_id integer NOT NULL
);


--
-- Name: customer_industry_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_industry_tags (
    customer_id integer NOT NULL,
    tag_id integer NOT NULL
);


--
-- Name: customer_insights; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_insights (
    id integer NOT NULL,
    name character varying(100),
    title character varying(255),
    description text,
    query text,
    chart_type character varying(50),
    refresh_interval integer DEFAULT 3600,
    enabled boolean DEFAULT true,
    display_order integer
);


--
-- Name: customer_insights_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_insights_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_insights_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_insights_id_seq OWNED BY public.customer_insights.id;


--
-- Name: customer_monthly_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_monthly_targets (
    id integer NOT NULL,
    salesperson_id integer NOT NULL,
    customer_id integer NOT NULL,
    target_month text NOT NULL,
    target_amount numeric DEFAULT 0,
    is_locked integer DEFAULT 0,
    notes text,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: customer_monthly_targets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_monthly_targets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_monthly_targets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_monthly_targets_id_seq OWNED BY public.customer_monthly_targets.id;


--
-- Name: customer_part_numbers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_part_numbers (
    id integer NOT NULL,
    base_part_number text NOT NULL,
    customer_part_number text NOT NULL,
    customer_id integer NOT NULL
);


--
-- Name: customer_part_numbers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_part_numbers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_part_numbers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_part_numbers_id_seq OWNED BY public.customer_part_numbers.id;


--
-- Name: customer_quote_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_quote_lines (
    id integer NOT NULL,
    parts_list_line_id integer NOT NULL,
    display_part_number text,
    quoted_part_number text,
    base_cost_gbp numeric,
    margin_percent numeric DEFAULT 0,
    quote_price_gbp numeric,
    is_no_bid integer DEFAULT 0,
    line_notes text,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    delivery_per_unit numeric DEFAULT 0,
    delivery_per_line numeric DEFAULT 0,
    quoted_status text DEFAULT 'created'::text,
    lead_days integer,
    standard_condition text,
    standard_certs text,
    CONSTRAINT customer_quote_lines_quoted_status_check CHECK ((quoted_status = ANY (ARRAY['created'::text, 'quoted'::text, 'no_bid'::text])))
);


--
-- Name: customer_quote_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_quote_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_quote_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_quote_lines_id_seq OWNED BY public.customer_quote_lines.id;


--
-- Name: customer_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_status (
    id integer NOT NULL,
    status text NOT NULL
);


--
-- Name: customer_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_status_id_seq OWNED BY public.customer_status.id;


--
-- Name: customer_updates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_updates (
    id integer NOT NULL,
    date text NOT NULL,
    customer_id integer NOT NULL,
    salesperson_id integer,
    update_text text NOT NULL,
    communication_type text
);


--
-- Name: customer_updates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_updates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_updates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_updates_id_seq OWNED BY public.customer_updates.id;


--
-- Name: customers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customers (
    id integer NOT NULL,
    name text NOT NULL,
    primary_contact_id integer,
    payment_terms text,
    incoterms text,
    salesperson_id integer,
    status_id integer,
    currency_id integer DEFAULT 3,
    system_code text,
    description text,
    estimated_revenue numeric,
    website text,
    country character varying(2),
    updated_at timestamp without time zone,
    apollo_id text,
    budget numeric,
    watch boolean DEFAULT false,
    preferred_currency_id integer DEFAULT 1,
    logo_url text,
    notes text,
    fleet_size integer,
    priority integer,
    CONSTRAINT customers_country_check CHECK (((country)::text = upper((country)::text)))
);


--
-- Name: customers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customers_id_seq OWNED BY public.customers.id;


--
-- Name: dashboard_panels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dashboard_panels (
    id integer NOT NULL,
    user_id integer,
    query_id integer NOT NULL,
    display_type text NOT NULL,
    panel_title text,
    panel_order integer,
    date_added timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    column_mappings text,
    formatting_rules text,
    header_styles text,
    summary_calculation text,
    panel_height text DEFAULT '400px'::text,
    panel_width text DEFAULT '100%'::text,
    background_color text DEFAULT '#ffffff'::text,
    text_color text DEFAULT '#000000'::text,
    column_styles text
);


--
-- Name: dashboard_panels_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dashboard_panels_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dashboard_panels_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dashboard_panels_id_seq OWNED BY public.dashboard_panels.id;


--
-- Name: deepdive_curated_customers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deepdive_curated_customers (
    id integer NOT NULL,
    deepdive_id integer NOT NULL,
    customer_id integer NOT NULL,
    notes text,
    order_index integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: deepdive_curated_customers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.deepdive_curated_customers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: deepdive_curated_customers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.deepdive_curated_customers_id_seq OWNED BY public.deepdive_curated_customers.id;


--
-- Name: deepdive_customer_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deepdive_customer_links (
    id integer NOT NULL,
    deepdive_id integer NOT NULL,
    customer_id integer NOT NULL,
    linked_text text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: deepdive_customer_links_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.deepdive_customer_links_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: deepdive_customer_links_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.deepdive_customer_links_id_seq OWNED BY public.deepdive_customer_links.id;


--
-- Name: development_points; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.development_points (
    id integer NOT NULL,
    question text NOT NULL,
    description text,
    order_index integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: development_points_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.development_points_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: development_points_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.development_points_id_seq OWNED BY public.development_points.id;


--
-- Name: email_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_logs (
    id integer NOT NULL,
    template_id integer NOT NULL,
    contact_id integer NOT NULL,
    customer_id integer,
    subject text NOT NULL,
    recipient_email text NOT NULL,
    sent_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    status text NOT NULL,
    error_message text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT email_logs_status_check CHECK ((status = ANY (ARRAY['sent'::text, 'error'::text])))
);


--
-- Name: email_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.email_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: email_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.email_logs_id_seq OWNED BY public.email_logs.id;


--
-- Name: email_signatures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_signatures (
    id integer NOT NULL,
    signature_html text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    name text DEFAULT ''::text NOT NULL,
    user_id integer,
    is_default boolean DEFAULT false
);


--
-- Name: email_signatures_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.email_signatures_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: email_signatures_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.email_signatures_id_seq OWNED BY public.email_signatures.id;


--
-- Name: email_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_templates (
    id integer NOT NULL,
    name text NOT NULL,
    subject text NOT NULL,
    body text NOT NULL,
    description text,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: email_templates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.email_templates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: email_templates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.email_templates_id_seq OWNED BY public.email_templates.id;


--
-- Name: emails; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.emails (
    id integer NOT NULL,
    customer_id integer,
    contact_id integer,
    sender_email text,
    recipient_email text,
    subject text,
    sent_date text,
    direction text,
    sync_status text,
    uid text,
    folder text,
    message_id text
);


--
-- Name: emails_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.emails_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: emails_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.emails_id_seq OWNED BY public.emails.id;


--
-- Name: excess_stock_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.excess_stock_files (
    excess_stock_list_id integer NOT NULL,
    file_id integer NOT NULL
);


--
-- Name: excess_stock_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.excess_stock_lines (
    id integer NOT NULL,
    excess_stock_list_id integer,
    base_part_number text NOT NULL,
    quantity integer,
    date_code text,
    manufacturer text
);


--
-- Name: excess_stock_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.excess_stock_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: excess_stock_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.excess_stock_lines_id_seq OWNED BY public.excess_stock_lines.id;


--
-- Name: excess_stock_lists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.excess_stock_lists (
    id integer NOT NULL,
    email text,
    customer_id integer,
    supplier_id integer,
    entered_date text,
    status text DEFAULT 'new'::text,
    upload_date text
);


--
-- Name: excess_stock_lists_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.excess_stock_lists_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: excess_stock_lists_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.excess_stock_lists_id_seq OWNED BY public.excess_stock_lists.id;


--
-- Name: files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.files (
    id integer NOT NULL,
    filename text,
    filepath text,
    upload_date date,
    description text,
    import_type text
);


--
-- Name: files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.files_id_seq OWNED BY public.files.id;


--
-- Name: financial_report_mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.financial_report_mappings (
    id integer NOT NULL,
    report_setting_id integer NOT NULL,
    account_id integer NOT NULL,
    report_section text NOT NULL,
    report_line text NOT NULL,
    display_order integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: financial_report_mappings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.financial_report_mappings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: financial_report_mappings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.financial_report_mappings_id_seq OWNED BY public.financial_report_mappings.id;


--
-- Name: financial_report_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.financial_report_settings (
    id integer NOT NULL,
    report_type text NOT NULL,
    report_name text NOT NULL,
    description text,
    is_default boolean DEFAULT false,
    created_by integer NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: financial_report_settings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.financial_report_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: financial_report_settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.financial_report_settings_id_seq OWNED BY public.financial_report_settings.id;


--
-- Name: fiscal_periods; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fiscal_periods (
    id integer NOT NULL,
    fiscal_year_id integer NOT NULL,
    period_name text NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    is_closed boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: fiscal_periods_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fiscal_periods_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fiscal_periods_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fiscal_periods_id_seq OWNED BY public.fiscal_periods.id;


--
-- Name: fiscal_years; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fiscal_years (
    id integer NOT NULL,
    year_name text NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    is_closed boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: fiscal_years_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fiscal_years_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fiscal_years_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fiscal_years_id_seq OWNED BY public.fiscal_years.id;


--
-- Name: geographic_deepdives; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.geographic_deepdives (
    id integer NOT NULL,
    country text NOT NULL,
    tag_id integer NOT NULL,
    title text NOT NULL,
    content text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: geographic_deepdives_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.geographic_deepdives_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: geographic_deepdives_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.geographic_deepdives_id_seq OWNED BY public.geographic_deepdives.id;


--
-- Name: ignored_domains; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ignored_domains (
    id integer NOT NULL,
    domain text NOT NULL,
    reason text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    created_by text
);


--
-- Name: ignored_domains_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ignored_domains_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ignored_domains_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ignored_domains_id_seq OWNED BY public.ignored_domains.id;


--
-- Name: ils_search_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ils_search_results (
    id integer NOT NULL,
    search_date timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    base_part_number text NOT NULL,
    part_number text NOT NULL,
    ils_company_name text NOT NULL,
    ils_cage_code text,
    supplier_id integer,
    quantity text,
    condition_code text,
    description text,
    price text,
    phone text,
    email text,
    distance text,
    supplier_comment text,
    alt_part_number text,
    exchange text,
    serial_number text,
    fax text
);


--
-- Name: ils_search_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ils_search_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ils_search_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ils_search_results_id_seq OWNED BY public.ils_search_results.id;


--
-- Name: ils_supplier_mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ils_supplier_mappings (
    id integer NOT NULL,
    ils_company_name text NOT NULL,
    ils_cage_code text,
    supplier_id integer,
    created_date timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    notes text
);


--
-- Name: ils_supplier_mappings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ils_supplier_mappings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ils_supplier_mappings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ils_supplier_mappings_id_seq OWNED BY public.ils_supplier_mappings.id;


--
-- Name: import_column_maps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.import_column_maps (
    id integer NOT NULL,
    name text NOT NULL,
    import_type text NOT NULL,
    mapping json NOT NULL,
    is_default boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    file_id integer
);


--
-- Name: import_column_maps_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.import_column_maps_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: import_column_maps_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.import_column_maps_id_seq OWNED BY public.import_column_maps.id;


--
-- Name: import_headers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.import_headers (
    id integer NOT NULL,
    import_column_map_id integer NOT NULL,
    column_name text NOT NULL,
    sample_value text
);


--
-- Name: import_headers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.import_headers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: import_headers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.import_headers_id_seq OWNED BY public.import_headers.id;


--
-- Name: import_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.import_settings (
    id integer NOT NULL,
    directory text NOT NULL,
    mapping_id integer
);


--
-- Name: import_settings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.import_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: import_settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.import_settings_id_seq OWNED BY public.import_settings.id;


--
-- Name: import_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.import_status (
    id integer NOT NULL,
    file_id integer NOT NULL,
    import_type text NOT NULL,
    processed integer DEFAULT 0,
    created integer DEFAULT 0,
    skipped integer DEFAULT 0,
    errors text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    completed_at text,
    status text,
    updated integer DEFAULT 0,
    mapping text
);


--
-- Name: import_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.import_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: import_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.import_status_id_seq OWNED BY public.import_status.id;


--
-- Name: industries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.industries (
    id integer NOT NULL,
    name text NOT NULL
);


--
-- Name: industries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.industries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: industries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.industries_id_seq OWNED BY public.industries.id;


--
-- Name: industry_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.industry_tags (
    id integer NOT NULL,
    tag text NOT NULL,
    description text,
    parent_tag_id integer
);


--
-- Name: industry_tags_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.industry_tags_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: industry_tags_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.industry_tags_id_seq OWNED BY public.industry_tags.id;


--
-- Name: invoice_discounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_discounts (
    id integer NOT NULL,
    invoice_id integer NOT NULL,
    discount_type text NOT NULL,
    discount_value numeric(10,2) NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT invoice_discounts_discount_type_check CHECK ((discount_type = ANY (ARRAY['Percentage'::text, 'Fixed'::text])))
);


--
-- Name: invoice_discounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.invoice_discounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invoice_discounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.invoice_discounts_id_seq OWNED BY public.invoice_discounts.id;


--
-- Name: invoice_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_files (
    id integer NOT NULL,
    invoice_id integer NOT NULL,
    file_id integer NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: invoice_files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.invoice_files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invoice_files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.invoice_files_id_seq OWNED BY public.invoice_files.id;


--
-- Name: invoice_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_lines (
    id integer NOT NULL,
    invoice_id integer NOT NULL,
    sales_order_line_id integer NOT NULL,
    base_part_number text NOT NULL,
    quantity integer NOT NULL,
    unit_price numeric(10,2) NOT NULL,
    line_total numeric(10,2) NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    original_amount numeric(10,2) DEFAULT 0 NOT NULL,
    converted_amount numeric(10,2) DEFAULT 0 NOT NULL,
    conversion_rate numeric(10,6) DEFAULT 1.0 NOT NULL,
    currency_id integer
);


--
-- Name: invoice_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.invoice_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invoice_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.invoice_lines_id_seq OWNED BY public.invoice_lines.id;


--
-- Name: invoice_payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_payments (
    id integer NOT NULL,
    invoice_id integer NOT NULL,
    payment_date date NOT NULL,
    payment_method text NOT NULL,
    amount_paid numeric(10,2) NOT NULL,
    reference text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT invoice_payments_payment_method_check CHECK ((payment_method = ANY (ARRAY['Bank Transfer'::text, 'Credit Card'::text, 'PayPal'::text, 'Cheque'::text, 'Cash'::text])))
);


--
-- Name: invoice_payments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.invoice_payments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invoice_payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.invoice_payments_id_seq OWNED BY public.invoice_payments.id;


--
-- Name: invoice_taxes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_taxes (
    id integer NOT NULL,
    invoice_id integer NOT NULL,
    tax_rate_id integer NOT NULL,
    tax_amount numeric(10,2) NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: invoice_taxes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.invoice_taxes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invoice_taxes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.invoice_taxes_id_seq OWNED BY public.invoice_taxes.id;


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoices (
    id integer NOT NULL,
    invoice_number text NOT NULL,
    sales_order_id integer NOT NULL,
    customer_id integer NOT NULL,
    billing_address_id integer NOT NULL,
    invoice_date date NOT NULL,
    due_date date NOT NULL,
    currency_id integer NOT NULL,
    total_amount numeric(10,2) NOT NULL,
    status text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT invoices_status_check CHECK ((status = ANY (ARRAY['Draft'::text, 'Sent'::text, 'Paid'::text, 'Overdue'::text, 'Cancelled'::text])))
);


--
-- Name: invoices_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.invoices_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invoices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.invoices_id_seq OWNED BY public.invoices.id;


--
-- Name: journal_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.journal_entries (
    id integer NOT NULL,
    entry_date date NOT NULL,
    fiscal_period_id integer NOT NULL,
    journal_entry_type_id integer NOT NULL,
    reference_number text,
    description text,
    currency_id integer NOT NULL,
    exchange_rate numeric(10,6) DEFAULT 1.0,
    is_posted boolean DEFAULT false,
    created_by integer NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: journal_entries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.journal_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: journal_entries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.journal_entries_id_seq OWNED BY public.journal_entries.id;


--
-- Name: journal_entry_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.journal_entry_lines (
    id integer NOT NULL,
    journal_entry_id integer NOT NULL,
    account_id integer NOT NULL,
    description text,
    debit_amount numeric(15,2) DEFAULT 0,
    credit_amount numeric(15,2) DEFAULT 0,
    foreign_amount numeric(15,2),
    customer_id integer,
    supplier_id integer,
    invoice_id integer,
    purchase_order_id integer,
    project_id integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: journal_entry_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.journal_entry_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: journal_entry_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.journal_entry_lines_id_seq OWNED BY public.journal_entry_lines.id;


--
-- Name: journal_entry_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.journal_entry_types (
    id integer NOT NULL,
    type_name text NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: journal_entry_types_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.journal_entry_types_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: journal_entry_types_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.journal_entry_types_id_seq OWNED BY public.journal_entry_types.id;


--
-- Name: manufacturers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manufacturers (
    id integer NOT NULL,
    name text NOT NULL,
    merged_into integer
);


--
-- Name: manufacturers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.manufacturers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: manufacturers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.manufacturers_id_seq OWNED BY public.manufacturers.id;


--
-- Name: offer_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.offer_files (
    offer_id integer NOT NULL,
    file_id integer NOT NULL
);


--
-- Name: offer_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.offer_lines (
    id integer NOT NULL,
    offer_id integer,
    base_part_number text,
    line_number text,
    manufacturer_id integer,
    quantity integer,
    price numeric,
    lead_time integer,
    requested_base_part_number text,
    internal_notes text,
    datecode text,
    spq integer,
    packaging text,
    rohs boolean,
    coc boolean
);


--
-- Name: offer_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.offer_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: offer_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.offer_lines_id_seq OWNED BY public.offer_lines.id;


--
-- Name: offers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.offers (
    id integer NOT NULL,
    supplier_id integer,
    valid_to date,
    supplier_reference text,
    file_id integer,
    price numeric,
    lead_time integer,
    currency_id integer,
    email_content text
);


--
-- Name: offers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.offers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: offers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.offers_id_seq OWNED BY public.offers.id;


--
-- Name: part_alt_group_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_alt_group_members (
    group_id integer NOT NULL,
    base_part_number text NOT NULL
);


--
-- Name: part_alt_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_alt_groups (
    id integer NOT NULL,
    description text,
    created_at text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: part_alt_groups_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.part_alt_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: part_alt_groups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.part_alt_groups_id_seq OWNED BY public.part_alt_groups.id;


--
-- Name: part_categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_categories (
    category_id integer NOT NULL,
    category_name text NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: part_categories_category_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.part_categories_category_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: part_categories_category_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.part_categories_category_id_seq OWNED BY public.part_categories.category_id;


--
-- Name: part_manufacturers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_manufacturers (
    base_part_number text,
    manufacturer_id integer
);


--
-- Name: part_numbers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_numbers (
    base_part_number text NOT NULL,
    part_number text NOT NULL,
    system_part_number text,
    created_at timestamp without time zone,
    stock integer,
    datecode text,
    target_price numeric(10,2),
    spq integer,
    packaging text,
    rohs boolean,
    category_id integer,
    mkp_category text
);


--
-- Name: parts_list_line_suggested_suppliers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts_list_line_suggested_suppliers (
    id integer NOT NULL,
    parts_list_line_id integer NOT NULL,
    supplier_id integer NOT NULL,
    source_type text,
    date_added timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: parts_list_line_suggested_suppliers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.parts_list_line_suggested_suppliers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: parts_list_line_suggested_suppliers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.parts_list_line_suggested_suppliers_id_seq OWNED BY public.parts_list_line_suggested_suppliers.id;


--
-- Name: parts_list_line_supplier_emails; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts_list_line_supplier_emails (
    id integer NOT NULL,
    parts_list_line_id integer NOT NULL,
    supplier_id integer NOT NULL,
    date_sent timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    sent_by_user_id integer,
    email_subject text,
    email_body text,
    recipient_email text,
    recipient_name text,
    notes text
);


--
-- Name: parts_list_line_supplier_emails_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.parts_list_line_supplier_emails_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: parts_list_line_supplier_emails_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.parts_list_line_supplier_emails_id_seq OWNED BY public.parts_list_line_supplier_emails.id;


--
-- Name: parts_list_line_suppliers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts_list_line_suppliers (
    id integer NOT NULL,
    parts_list_line_id integer NOT NULL,
    supplier_id integer,
    supplier_name text,
    cost numeric,
    currency_id integer,
    lead_days integer,
    source_type text,
    source_reference text,
    condition_code text,
    notes text,
    is_preferred boolean DEFAULT false,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: parts_list_line_suppliers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.parts_list_line_suppliers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: parts_list_line_suppliers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.parts_list_line_suppliers_id_seq OWNED BY public.parts_list_line_suppliers.id;


--
-- Name: parts_list_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts_list_lines (
    id integer NOT NULL,
    parts_list_id integer NOT NULL,
    line_number numeric(10,2) NOT NULL,
    customer_part_number text NOT NULL,
    base_part_number text,
    quantity integer DEFAULT 1 NOT NULL,
    parent_line_id integer,
    line_type text DEFAULT 'normal'::text NOT NULL,
    chosen_supplier_id integer,
    chosen_cost numeric,
    chosen_price numeric,
    chosen_currency_id integer,
    chosen_lead_days integer,
    customer_notes text,
    internal_notes text,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    chosen_qty integer
);


--
-- Name: parts_list_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.parts_list_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: parts_list_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.parts_list_lines_id_seq OWNED BY public.parts_list_lines.id;


--
-- Name: parts_list_no_response_dismissals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts_list_no_response_dismissals (
    id integer NOT NULL,
    email_id integer NOT NULL,
    dismissed_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: parts_list_no_response_dismissals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.parts_list_no_response_dismissals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: parts_list_no_response_dismissals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.parts_list_no_response_dismissals_id_seq OWNED BY public.parts_list_no_response_dismissals.id;


--
-- Name: parts_list_statuses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts_list_statuses (
    id integer NOT NULL,
    name text NOT NULL,
    display_order integer DEFAULT 0 NOT NULL
);


--
-- Name: parts_list_statuses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.parts_list_statuses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: parts_list_statuses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.parts_list_statuses_id_seq OWNED BY public.parts_list_statuses.id;


--
-- Name: parts_list_supplier_quote_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts_list_supplier_quote_lines (
    id integer NOT NULL,
    supplier_quote_id integer NOT NULL,
    parts_list_line_id integer NOT NULL,
    quoted_part_number character varying(100),
    quantity_quoted integer,
    unit_price numeric(15,4),
    lead_time_days integer,
    condition_code character varying(10),
    certifications text,
    is_no_bid boolean DEFAULT false,
    line_notes text,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: parts_list_supplier_quote_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.parts_list_supplier_quote_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: parts_list_supplier_quote_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.parts_list_supplier_quote_lines_id_seq OWNED BY public.parts_list_supplier_quote_lines.id;


--
-- Name: parts_list_supplier_quotes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts_list_supplier_quotes (
    id integer NOT NULL,
    parts_list_id integer NOT NULL,
    supplier_id integer NOT NULL,
    quote_reference character varying(100),
    quote_date date,
    currency_id integer DEFAULT 3 NOT NULL,
    notes text,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id integer
);


--
-- Name: parts_list_supplier_quotes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.parts_list_supplier_quotes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: parts_list_supplier_quotes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.parts_list_supplier_quotes_id_seq OWNED BY public.parts_list_supplier_quotes.id;


--
-- Name: parts_lists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts_lists (
    id integer NOT NULL,
    name text NOT NULL,
    customer_id integer,
    salesperson_id integer NOT NULL,
    status_id integer DEFAULT 1 NOT NULL,
    notes text,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    contact_id integer
);


--
-- Name: parts_lists_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.parts_lists_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: parts_lists_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.parts_lists_id_seq OWNED BY public.parts_lists.id;


--
-- Name: portal_api_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_api_log (
    id integer NOT NULL,
    endpoint text,
    method text,
    portal_user_id integer,
    customer_id integer,
    request_data text,
    response_status integer,
    ip_address text,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: portal_api_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_api_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_api_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_api_log_id_seq OWNED BY public.portal_api_log.id;


--
-- Name: portal_customer_margins; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_customer_margins (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    stock_margin_percentage numeric,
    vq_margin_percentage numeric,
    po_margin_percentage numeric,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: portal_customer_margins_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_customer_margins_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_customer_margins_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_customer_margins_id_seq OWNED BY public.portal_customer_margins.id;


--
-- Name: portal_customer_pricing; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_customer_pricing (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    base_part_number text NOT NULL,
    price numeric NOT NULL,
    currency_id integer DEFAULT 3 NOT NULL,
    valid_from date,
    valid_until date,
    notes text,
    is_active boolean DEFAULT true,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: portal_customer_pricing_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_customer_pricing_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_customer_pricing_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_customer_pricing_id_seq OWNED BY public.portal_customer_pricing.id;


--
-- Name: portal_pricing_agreement_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_pricing_agreement_requests (
    id integer NOT NULL,
    portal_user_id integer NOT NULL,
    customer_id integer NOT NULL,
    part_number text NOT NULL,
    base_part_number text NOT NULL,
    quantity integer DEFAULT 1,
    reference_number text NOT NULL,
    customer_notes text,
    internal_notes text,
    status text DEFAULT 'pending'::text,
    date_submitted timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_processed timestamp without time zone,
    processed_by_user_id integer
);


--
-- Name: portal_pricing_agreement_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_pricing_agreement_requests_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_pricing_agreement_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_pricing_agreement_requests_id_seq OWNED BY public.portal_pricing_agreement_requests.id;


--
-- Name: portal_purchase_order_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_purchase_order_lines (
    id integer NOT NULL,
    portal_purchase_order_id integer NOT NULL,
    line_number integer NOT NULL,
    part_number character varying(100) NOT NULL,
    base_part_number character varying(100) NOT NULL,
    description text,
    quantity integer NOT NULL,
    unit_price numeric(10,2) NOT NULL,
    line_total numeric(10,2) NOT NULL,
    price_source character varying(50),
    portal_quote_request_line_id integer,
    status character varying(50) DEFAULT 'pending'::character varying,
    quantity_shipped integer DEFAULT 0,
    date_shipped timestamp without time zone,
    stock_movement_id integer,
    notes text,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: portal_purchase_order_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_purchase_order_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_purchase_order_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_purchase_order_lines_id_seq OWNED BY public.portal_purchase_order_lines.id;


--
-- Name: portal_purchase_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_purchase_orders (
    id integer NOT NULL,
    portal_user_id integer NOT NULL,
    customer_id integer NOT NULL,
    portal_quote_request_id integer,
    po_reference character varying(100) NOT NULL,
    total_value numeric(10,2) NOT NULL,
    currency_id integer DEFAULT 3,
    line_count integer NOT NULL,
    status character varying(50) DEFAULT 'submitted'::character varying,
    date_submitted timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_acknowledged timestamp without time zone,
    date_dispatched timestamp without time zone,
    customer_notes text,
    delivery_company character varying(200),
    delivery_street character varying(200),
    delivery_city character varying(100),
    delivery_zip character varying(20),
    delivery_country character varying(100),
    invoice_company character varying(200),
    invoice_street character varying(200),
    invoice_city character varying(100),
    invoice_zip character varying(20),
    invoice_country character varying(100),
    same_as_delivery boolean DEFAULT true,
    authorizer_name character varying(200),
    authorizer_title character varying(200),
    authorization_timestamp timestamp without time zone,
    internal_notes text,
    assigned_to integer,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: portal_purchase_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_purchase_orders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_purchase_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_purchase_orders_id_seq OWNED BY public.portal_purchase_orders.id;


--
-- Name: portal_quote_request_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_quote_request_lines (
    id integer NOT NULL,
    portal_quote_request_id integer NOT NULL,
    line_number integer,
    part_number text NOT NULL,
    base_part_number text,
    quantity integer NOT NULL,
    quoted_price numeric,
    quoted_currency_id integer,
    quoted_lead_days integer,
    status text DEFAULT 'pending'::text
);


--
-- Name: portal_quote_request_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_quote_request_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_quote_request_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_quote_request_lines_id_seq OWNED BY public.portal_quote_request_lines.id;


--
-- Name: portal_quote_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_quote_requests (
    id integer NOT NULL,
    portal_user_id integer NOT NULL,
    customer_id integer NOT NULL,
    parts_list_id integer,
    reference_number text,
    status text DEFAULT 'pending'::text,
    customer_notes text,
    date_submitted timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_processed timestamp without time zone,
    processed_by_user_id integer
);


--
-- Name: portal_quote_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_quote_requests_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_quote_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_quote_requests_id_seq OWNED BY public.portal_quote_requests.id;


--
-- Name: portal_search_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_search_history (
    id integer NOT NULL,
    portal_user_id integer NOT NULL,
    customer_id integer NOT NULL,
    search_type text NOT NULL,
    parts_searched text,
    parts_count integer DEFAULT 0,
    date_searched timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    ip_address text,
    user_agent text
);


--
-- Name: portal_search_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_search_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_search_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_search_history_id_seq OWNED BY public.portal_search_history.id;


--
-- Name: portal_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_settings (
    id integer NOT NULL,
    setting_key text NOT NULL,
    setting_value text,
    description text,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: portal_settings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_settings_id_seq OWNED BY public.portal_settings.id;


--
-- Name: portal_suggested_parts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_suggested_parts (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    base_part_number text NOT NULL,
    notes text,
    priority integer DEFAULT 0,
    is_active boolean DEFAULT true,
    suggested_by_user_id integer,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_modified timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: portal_suggested_parts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_suggested_parts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_suggested_parts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_suggested_parts_id_seq OWNED BY public.portal_suggested_parts.id;


--
-- Name: portal_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portal_users (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    first_name text,
    last_name text,
    is_active boolean DEFAULT true,
    last_login timestamp without time zone,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: portal_users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portal_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portal_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portal_users_id_seq OWNED BY public.portal_users.id;


--
-- Name: price_breaks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_breaks (
    id integer NOT NULL,
    price_list_item_id integer,
    quantity integer,
    price numeric
);


--
-- Name: price_breaks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.price_breaks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: price_breaks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.price_breaks_id_seq OWNED BY public.price_breaks.id;


--
-- Name: price_list_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_list_items (
    id integer NOT NULL,
    price_list_id integer,
    part_number text,
    base_part_number text,
    lead_time integer
);


--
-- Name: price_list_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.price_list_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: price_list_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.price_list_items_id_seq OWNED BY public.price_list_items.id;


--
-- Name: price_lists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_lists (
    id integer NOT NULL,
    supplier_id integer,
    valid_from date,
    valid_to date,
    name_reference text,
    currency_id integer
);


--
-- Name: price_lists_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.price_lists_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: price_lists_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.price_lists_id_seq OWNED BY public.price_lists.id;


--
-- Name: priorities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.priorities (
    id integer NOT NULL,
    name text NOT NULL,
    color text NOT NULL
);


--
-- Name: priorities_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.priorities_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: priorities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.priorities_id_seq OWNED BY public.priorities.id;


--
-- Name: project_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_files (
    project_id integer NOT NULL,
    file_id integer NOT NULL
);


--
-- Name: project_rfqs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_rfqs (
    project_id integer NOT NULL,
    rfq_id integer NOT NULL
);


--
-- Name: project_stage_salespeople; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_stage_salespeople (
    stage_id integer NOT NULL,
    salesperson_id integer NOT NULL
);


--
-- Name: project_stages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_stages (
    id integer NOT NULL,
    project_id integer NOT NULL,
    name text NOT NULL,
    description text,
    parent_stage_id integer,
    stage_order integer,
    status_id integer NOT NULL,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    due_date timestamp without time zone,
    recurrence_id integer
);


--
-- Name: project_stages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.project_stages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: project_stages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.project_stages_id_seq OWNED BY public.project_stages.id;


--
-- Name: project_statuses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_statuses (
    id integer NOT NULL,
    status text NOT NULL
);


--
-- Name: project_statuses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.project_statuses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: project_statuses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.project_statuses_id_seq OWNED BY public.project_statuses.id;


--
-- Name: project_updates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_updates (
    id integer NOT NULL,
    project_id integer NOT NULL,
    salesperson_id integer NOT NULL,
    comment text NOT NULL,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    stage_id integer
);


--
-- Name: project_updates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.project_updates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: project_updates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.project_updates_id_seq OWNED BY public.project_updates.id;


--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    salesperson_id integer NOT NULL,
    status_id integer DEFAULT 1,
    name text,
    description text,
    next_stage_id integer,
    next_stage_deadline timestamp without time zone,
    estimated_value numeric(10,2)
);


--
-- Name: projects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.projects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: projects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.projects_id_seq OWNED BY public.projects.id;


--
-- Name: purchase_order_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.purchase_order_lines (
    id integer NOT NULL,
    purchase_order_id integer NOT NULL,
    line_number integer NOT NULL,
    base_part_number text NOT NULL,
    quantity integer NOT NULL,
    price numeric NOT NULL,
    ship_date date,
    promised_date date,
    status_id integer NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    sales_order_line_id integer,
    received_quantity integer DEFAULT 0
);


--
-- Name: purchase_order_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.purchase_order_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: purchase_order_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.purchase_order_lines_id_seq OWNED BY public.purchase_order_lines.id;


--
-- Name: purchase_order_statuses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.purchase_order_statuses (
    id integer NOT NULL,
    name text NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: purchase_order_statuses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.purchase_order_statuses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: purchase_order_statuses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.purchase_order_statuses_id_seq OWNED BY public.purchase_order_statuses.id;


--
-- Name: purchase_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.purchase_orders (
    id integer NOT NULL,
    purchase_order_ref text NOT NULL,
    supplier_id integer NOT NULL,
    date_issued date NOT NULL,
    incoterms text,
    payment_terms text,
    purchase_status_id integer NOT NULL,
    currency_id integer NOT NULL,
    delivery_address_id integer,
    billing_address_id integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    total_value numeric
);


--
-- Name: purchase_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.purchase_orders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: purchase_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.purchase_orders_id_seq OWNED BY public.purchase_orders.id;


--
-- Name: reconciliation_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reconciliation_items (
    id integer NOT NULL,
    reconciliation_id integer NOT NULL,
    journal_entry_line_id integer NOT NULL,
    is_cleared boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: reconciliation_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.reconciliation_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: reconciliation_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.reconciliation_items_id_seq OWNED BY public.reconciliation_items.id;


--
-- Name: recurrence_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recurrence_types (
    id integer NOT NULL,
    name text NOT NULL,
    "interval" integer NOT NULL
);


--
-- Name: recurrence_types_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.recurrence_types_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: recurrence_types_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.recurrence_types_id_seq OWNED BY public.recurrence_types.id;


--
-- Name: recurring_journal_template_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recurring_journal_template_lines (
    id integer NOT NULL,
    template_id integer NOT NULL,
    account_id integer NOT NULL,
    description text,
    debit_amount numeric(15,2) DEFAULT 0,
    credit_amount numeric(15,2) DEFAULT 0,
    distribution_type text DEFAULT 'fixed'::text,
    customer_id integer,
    supplier_id integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: recurring_journal_template_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.recurring_journal_template_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: recurring_journal_template_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.recurring_journal_template_lines_id_seq OWNED BY public.recurring_journal_template_lines.id;


--
-- Name: recurring_journal_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recurring_journal_templates (
    id integer NOT NULL,
    template_name text NOT NULL,
    description text,
    journal_entry_type_id integer NOT NULL,
    frequency text NOT NULL,
    next_date date NOT NULL,
    end_date date,
    is_active boolean DEFAULT true,
    created_by integer NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: recurring_journal_templates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.recurring_journal_templates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: recurring_journal_templates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.recurring_journal_templates_id_seq OWNED BY public.recurring_journal_templates.id;


--
-- Name: requisition_references; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.requisition_references (
    id integer NOT NULL,
    top_level_requisition_id integer,
    requisition_id integer
);


--
-- Name: requisition_references_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.requisition_references_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: requisition_references_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.requisition_references_id_seq OWNED BY public.requisition_references.id;


--
-- Name: requisitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.requisitions (
    id integer NOT NULL,
    rfq_id integer,
    supplier_id integer,
    date text,
    base_part_number text,
    quantity integer,
    rfq_line_id integer
);


--
-- Name: requisitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.requisitions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: requisitions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.requisitions_id_seq OWNED BY public.requisitions.id;


--
-- Name: rfq_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rfq_files (
    rfq_id integer NOT NULL,
    file_id integer NOT NULL
);


--
-- Name: rfq_line_part_alternatives; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rfq_line_part_alternatives (
    rfq_line_id integer NOT NULL,
    primary_base_part_number character varying(50) NOT NULL,
    alternative_base_part_number character varying(50) CONSTRAINT rfq_line_part_alternatives_alternative_base_part_numbe_not_null NOT NULL
);


--
-- Name: rfq_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rfq_lines (
    id integer NOT NULL,
    rfq_id integer,
    line_number text,
    base_part_number text,
    quantity integer,
    suggested_suppliers text,
    chosen_supplier integer,
    cost numeric,
    supplier_lead_time integer,
    margin numeric,
    price numeric,
    lead_time integer,
    line_value numeric,
    note text,
    internal_notes text,
    manufacturer_id integer,
    offer_id integer,
    status_id integer,
    cost_currency integer,
    base_cost numeric,
    offered_base_part_number text,
    datecode text,
    taret_price numeric(10,2),
    spq integer,
    packaging text,
    rohs boolean,
    coc boolean
);


--
-- Name: rfq_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rfq_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rfq_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rfq_lines_id_seq OWNED BY public.rfq_lines.id;


--
-- Name: rfq_updates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rfq_updates (
    id integer NOT NULL,
    rfq_id integer NOT NULL,
    user_id integer NOT NULL,
    update_text text,
    update_type text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: rfq_updates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rfq_updates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rfq_updates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rfq_updates_id_seq OWNED BY public.rfq_updates.id;


--
-- Name: rfqs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rfqs (
    id integer NOT NULL,
    entered_date text,
    customer_id integer,
    contact_id integer,
    customer_ref text,
    currency integer,
    status text,
    email text,
    salesperson_id integer,
    primary_file_id integer
);


--
-- Name: rfqs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rfqs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rfqs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rfqs_id_seq OWNED BY public.rfqs.id;


--
-- Name: sales_order_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sales_order_lines (
    id integer NOT NULL,
    sales_order_id integer NOT NULL,
    line_number integer NOT NULL,
    base_cost numeric,
    price numeric NOT NULL,
    quantity integer NOT NULL,
    delivery_date date,
    requested_date date,
    promise_date date,
    ship_date date,
    sales_status_id integer NOT NULL,
    note text,
    rfq_line_id integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    base_part_number text,
    shipped boolean DEFAULT false,
    shipped_quantity numeric DEFAULT 0
);


--
-- Name: sales_order_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sales_order_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sales_order_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sales_order_lines_id_seq OWNED BY public.sales_order_lines.id;


--
-- Name: sales_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sales_orders (
    id integer NOT NULL,
    sales_order_ref text NOT NULL,
    customer_id integer NOT NULL,
    customer_po_ref text,
    salesperson_id integer,
    contact_name text,
    date_entered date NOT NULL,
    incoterms text,
    payment_terms text,
    sales_status_id integer NOT NULL,
    currency_id integer NOT NULL,
    shipping_address_id integer,
    invoicing_address_id integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    total_value numeric
);


--
-- Name: sales_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sales_orders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sales_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sales_orders_id_seq OWNED BY public.sales_orders.id;


--
-- Name: sales_statuses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sales_statuses (
    id integer NOT NULL,
    status_name text NOT NULL
);


--
-- Name: sales_statuses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sales_statuses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sales_statuses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sales_statuses_id_seq OWNED BY public.sales_statuses.id;


--
-- Name: salespeople; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.salespeople (
    id integer NOT NULL,
    name text NOT NULL,
    system_ref text,
    is_active boolean DEFAULT true
);


--
-- Name: salespeople_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.salespeople_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: salespeople_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.salespeople_id_seq OWNED BY public.salespeople.id;


--
-- Name: salesperson_engagement_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.salesperson_engagement_settings (
    salesperson_id integer NOT NULL,
    overdue_threshold_days integer DEFAULT 14,
    customer_status_filter text,
    contact_status_filter text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: salesperson_engagement_settings_salesperson_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.salesperson_engagement_settings_salesperson_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: salesperson_engagement_settings_salesperson_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.salesperson_engagement_settings_salesperson_id_seq OWNED BY public.salesperson_engagement_settings.salesperson_id;


--
-- Name: salesperson_monthly_goals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.salesperson_monthly_goals (
    id integer NOT NULL,
    salesperson_id integer NOT NULL,
    target_month text NOT NULL,
    goal_amount numeric DEFAULT 0,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: salesperson_monthly_goals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.salesperson_monthly_goals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: salesperson_monthly_goals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.salesperson_monthly_goals_id_seq OWNED BY public.salesperson_monthly_goals.id;


--
-- Name: salesperson_user_link; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.salesperson_user_link (
    id integer NOT NULL,
    user_id integer NOT NULL,
    legacy_salesperson_id integer NOT NULL
);


--
-- Name: salesperson_user_link_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.salesperson_user_link_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: salesperson_user_link_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.salesperson_user_link_id_seq OWNED BY public.salesperson_user_link.id;


--
-- Name: saved_queries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.saved_queries (
    id integer NOT NULL,
    query_name text NOT NULL,
    query text NOT NULL,
    chart_type text NOT NULL,
    label_column_1 text NOT NULL,
    label_column_2 text,
    value_column_1 text NOT NULL,
    value_column_2 text,
    date_saved timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: saved_queries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.saved_queries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: saved_queries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.saved_queries_id_seq OWNED BY public.saved_queries.id;


--
-- Name: settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.settings (
    key text NOT NULL,
    value text NOT NULL
);


--
-- Name: stage_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stage_files (
    stage_id integer NOT NULL,
    file_id integer NOT NULL
);


--
-- Name: stage_updates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stage_updates (
    id integer NOT NULL,
    stage_id integer NOT NULL,
    salesperson_id integer NOT NULL,
    comment text NOT NULL,
    date_created timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: stage_updates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.stage_updates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: stage_updates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.stage_updates_id_seq OWNED BY public.stage_updates.id;


--
-- Name: statuses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.statuses (
    id integer NOT NULL,
    status text NOT NULL
);


--
-- Name: statuses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.statuses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: statuses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.statuses_id_seq OWNED BY public.statuses.id;


--
-- Name: stock_movements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stock_movements (
    movement_id integer NOT NULL,
    base_part_number text,
    movement_type text NOT NULL,
    quantity integer NOT NULL,
    datecode text,
    cost_per_unit numeric(10,2),
    movement_date timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    reference text,
    notes text,
    available_quantity integer,
    parent_movement_id integer,
    CONSTRAINT stock_movements_movement_type_check CHECK ((movement_type = ANY (ARRAY['IN'::text, 'OUT'::text])))
);


--
-- Name: stock_movements_movement_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.stock_movements_movement_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: stock_movements_movement_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.stock_movements_movement_id_seq OWNED BY public.stock_movements.movement_id;


--
-- Name: supplier_contacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supplier_contacts (
    id integer NOT NULL,
    customer_id integer,
    first_name text NOT NULL,
    second_name text NOT NULL,
    email_address text NOT NULL,
    supplier_id integer
);


--
-- Name: supplier_contacts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.supplier_contacts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: supplier_contacts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.supplier_contacts_id_seq OWNED BY public.supplier_contacts.id;


--
-- Name: supplier_domains; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supplier_domains (
    id integer NOT NULL,
    supplier_id integer NOT NULL,
    domain text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: supplier_domains_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.supplier_domains_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: supplier_domains_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.supplier_domains_id_seq OWNED BY public.supplier_domains.id;


--
-- Name: suppliers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.suppliers (
    id integer NOT NULL,
    name text,
    contact_name text,
    contact_email text,
    contact_phone text,
    buffer integer,
    currency integer,
    fornitore text,
    delivery_cost numeric(10,2) DEFAULT 0,
    minimum_line_value numeric(10,2) DEFAULT 0,
    standard_condition text,
    standard_certs text
);


--
-- Name: suppliers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.suppliers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: suppliers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.suppliers_id_seq OWNED BY public.suppliers.id;


--
-- Name: sync_metadata; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_metadata (
    id integer NOT NULL,
    last_synced_date timestamp without time zone NOT NULL
);


--
-- Name: sync_metadata_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sync_metadata_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sync_metadata_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sync_metadata_id_seq OWNED BY public.sync_metadata.id;


--
-- Name: tax_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tax_rates (
    id integer NOT NULL,
    tax_name text NOT NULL,
    tax_percentage numeric(5,2) NOT NULL,
    country text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: tax_rates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tax_rates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tax_rates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tax_rates_id_seq OWNED BY public.tax_rates.id;


--
-- Name: template_industry_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.template_industry_tags (
    template_id integer NOT NULL,
    industry_tag_id integer NOT NULL
);


--
-- Name: template_placeholders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.template_placeholders (
    id integer NOT NULL,
    placeholder_key text NOT NULL,
    description text NOT NULL,
    example_value text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: template_placeholders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.template_placeholders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: template_placeholders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.template_placeholders_id_seq OWNED BY public.template_placeholders.id;


--
-- Name: top_level_requisitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.top_level_requisitions (
    id integer NOT NULL,
    created_at text,
    reference text
);


--
-- Name: top_level_requisitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.top_level_requisitions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: top_level_requisitions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.top_level_requisitions_id_seq OWNED BY public.top_level_requisitions.id;


--
-- Name: user_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_permissions (
    id integer NOT NULL,
    user_id integer NOT NULL,
    permissions integer DEFAULT 0 NOT NULL
);


--
-- Name: user_permissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_permissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_permissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_permissions_id_seq OWNED BY public.user_permissions.id;


--
-- Name: user_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_roles (
    id integer NOT NULL,
    name text NOT NULL
);


--
-- Name: user_roles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_roles_id_seq OWNED BY public.user_roles.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username text NOT NULL,
    password_hash text NOT NULL,
    user_type text DEFAULT 'normal'::text NOT NULL,
    role text,
    picture_url text,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    created_by integer,
    modified_at timestamp without time zone,
    modified_by integer,
    email text,
    CONSTRAINT users_user_type_check CHECK ((user_type = ANY (ARRAY['admin'::text, 'normal'::text, 'view_only'::text])))
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: vq_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vq_lines (
    id integer NOT NULL,
    vq_id integer NOT NULL,
    vendor_response_id text,
    transaction_id text,
    transaction_item_id text,
    base_part_number text,
    part_number text,
    pn_quoted text,
    description text,
    condition_code text,
    quantity_quoted integer,
    quantity_requested integer,
    unit_of_measure text DEFAULT 'EA'::text,
    lead_days integer,
    vendor_price numeric(10,2),
    item_total numeric(10,2),
    line_number integer,
    foreign_currency text,
    quoted_date date
);


--
-- Name: vq_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.vq_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: vq_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.vq_lines_id_seq OWNED BY public.vq_lines.id;


--
-- Name: vqs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vqs (
    id integer NOT NULL,
    vq_number text NOT NULL,
    supplier_id integer,
    status text DEFAULT 'Created'::text,
    entry_date date,
    expiration_date date,
    currency_id integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: vqs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.vqs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: vqs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.vqs_id_seq OWNED BY public.vqs.id;


--
-- Name: watched_industry_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watched_industry_tags (
    id integer NOT NULL,
    user_id integer NOT NULL,
    tag_id integer NOT NULL
);


--
-- Name: watched_industry_tags_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.watched_industry_tags_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: watched_industry_tags_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.watched_industry_tags_id_seq OWNED BY public.watched_industry_tags.id;


--
-- Name: account_activity_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_activity_log ALTER COLUMN id SET DEFAULT nextval('public.account_activity_log_id_seq'::regclass);


--
-- Name: account_reconciliations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_reconciliations ALTER COLUMN id SET DEFAULT nextval('public.account_reconciliations_id_seq'::regclass);


--
-- Name: account_types id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_types ALTER COLUMN id SET DEFAULT nextval('public.account_types_id_seq'::regclass);


--
-- Name: acknowledgments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acknowledgments ALTER COLUMN id SET DEFAULT nextval('public.acknowledgments_id_seq'::regclass);


--
-- Name: ai_tag_suggestions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_tag_suggestions ALTER COLUMN id SET DEFAULT nextval('public.ai_tag_suggestions_id_seq'::regclass);


--
-- Name: alternative_part_numbers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alternative_part_numbers ALTER COLUMN id SET DEFAULT nextval('public.alternative_part_numbers_id_seq'::regclass);


--
-- Name: bom_headers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bom_headers ALTER COLUMN id SET DEFAULT nextval('public.bom_headers_id_seq'::regclass);


--
-- Name: bom_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bom_lines ALTER COLUMN id SET DEFAULT nextval('public.bom_lines_id_seq'::regclass);


--
-- Name: bom_pricing id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bom_pricing ALTER COLUMN id SET DEFAULT nextval('public.bom_pricing_id_seq'::regclass);


--
-- Name: bom_revisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bom_revisions ALTER COLUMN id SET DEFAULT nextval('public.bom_revisions_id_seq'::regclass);


--
-- Name: call_list id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_list ALTER COLUMN id SET DEFAULT nextval('public.call_list_id_seq'::regclass);


--
-- Name: chart_of_accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chart_of_accounts ALTER COLUMN id SET DEFAULT nextval('public.chart_of_accounts_id_seq'::regclass);


--
-- Name: company_types id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_types ALTER COLUMN id SET DEFAULT nextval('public.company_types_id_seq'::regclass);


--
-- Name: contact_communications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_communications ALTER COLUMN id SET DEFAULT nextval('public.contact_communications_id_seq'::regclass);


--
-- Name: contact_list_members id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_list_members ALTER COLUMN id SET DEFAULT nextval('public.contact_list_members_id_seq'::regclass);


--
-- Name: contact_lists id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_lists ALTER COLUMN id SET DEFAULT nextval('public.contact_lists_id_seq'::regclass);


--
-- Name: contact_statuses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_statuses ALTER COLUMN id SET DEFAULT nextval('public.contact_statuses_id_seq'::regclass);


--
-- Name: contacts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contacts ALTER COLUMN id SET DEFAULT nextval('public.contacts_id_seq'::regclass);


--
-- Name: cq_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cq_lines ALTER COLUMN id SET DEFAULT nextval('public.cq_lines_id_seq'::regclass);


--
-- Name: cqs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cqs ALTER COLUMN id SET DEFAULT nextval('public.cqs_id_seq'::regclass);


--
-- Name: currencies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.currencies ALTER COLUMN id SET DEFAULT nextval('public.currencies_id_seq'::regclass);


--
-- Name: customer_addresses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_addresses ALTER COLUMN id SET DEFAULT nextval('public.customer_addresses_id_seq'::regclass);


--
-- Name: customer_associations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_associations ALTER COLUMN id SET DEFAULT nextval('public.customer_associations_id_seq'::regclass);


--
-- Name: customer_development_answers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_development_answers ALTER COLUMN id SET DEFAULT nextval('public.customer_development_answers_id_seq'::regclass);


--
-- Name: customer_domains id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_domains ALTER COLUMN id SET DEFAULT nextval('public.customer_domains_id_seq'::regclass);


--
-- Name: customer_enrichment_status customer_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_enrichment_status ALTER COLUMN customer_id SET DEFAULT nextval('public.customer_enrichment_status_customer_id_seq'::regclass);


--
-- Name: customer_insights id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_insights ALTER COLUMN id SET DEFAULT nextval('public.customer_insights_id_seq'::regclass);


--
-- Name: customer_monthly_targets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_monthly_targets ALTER COLUMN id SET DEFAULT nextval('public.customer_monthly_targets_id_seq'::regclass);


--
-- Name: customer_part_numbers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_part_numbers ALTER COLUMN id SET DEFAULT nextval('public.customer_part_numbers_id_seq'::regclass);


--
-- Name: customer_quote_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_quote_lines ALTER COLUMN id SET DEFAULT nextval('public.customer_quote_lines_id_seq'::regclass);


--
-- Name: customer_status id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_status ALTER COLUMN id SET DEFAULT nextval('public.customer_status_id_seq'::regclass);


--
-- Name: customer_updates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_updates ALTER COLUMN id SET DEFAULT nextval('public.customer_updates_id_seq'::regclass);


--
-- Name: customers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers ALTER COLUMN id SET DEFAULT nextval('public.customers_id_seq'::regclass);


--
-- Name: dashboard_panels id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dashboard_panels ALTER COLUMN id SET DEFAULT nextval('public.dashboard_panels_id_seq'::regclass);


--
-- Name: deepdive_curated_customers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deepdive_curated_customers ALTER COLUMN id SET DEFAULT nextval('public.deepdive_curated_customers_id_seq'::regclass);


--
-- Name: deepdive_customer_links id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deepdive_customer_links ALTER COLUMN id SET DEFAULT nextval('public.deepdive_customer_links_id_seq'::regclass);


--
-- Name: development_points id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.development_points ALTER COLUMN id SET DEFAULT nextval('public.development_points_id_seq'::regclass);


--
-- Name: email_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_logs ALTER COLUMN id SET DEFAULT nextval('public.email_logs_id_seq'::regclass);


--
-- Name: email_signatures id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_signatures ALTER COLUMN id SET DEFAULT nextval('public.email_signatures_id_seq'::regclass);


--
-- Name: email_templates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_templates ALTER COLUMN id SET DEFAULT nextval('public.email_templates_id_seq'::regclass);


--
-- Name: emails id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emails ALTER COLUMN id SET DEFAULT nextval('public.emails_id_seq'::regclass);


--
-- Name: excess_stock_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.excess_stock_lines ALTER COLUMN id SET DEFAULT nextval('public.excess_stock_lines_id_seq'::regclass);


--
-- Name: excess_stock_lists id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.excess_stock_lists ALTER COLUMN id SET DEFAULT nextval('public.excess_stock_lists_id_seq'::regclass);


--
-- Name: files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files ALTER COLUMN id SET DEFAULT nextval('public.files_id_seq'::regclass);


--
-- Name: financial_report_mappings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.financial_report_mappings ALTER COLUMN id SET DEFAULT nextval('public.financial_report_mappings_id_seq'::regclass);


--
-- Name: financial_report_settings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.financial_report_settings ALTER COLUMN id SET DEFAULT nextval('public.financial_report_settings_id_seq'::regclass);


--
-- Name: fiscal_periods id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiscal_periods ALTER COLUMN id SET DEFAULT nextval('public.fiscal_periods_id_seq'::regclass);


--
-- Name: fiscal_years id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiscal_years ALTER COLUMN id SET DEFAULT nextval('public.fiscal_years_id_seq'::regclass);


--
-- Name: geographic_deepdives id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.geographic_deepdives ALTER COLUMN id SET DEFAULT nextval('public.geographic_deepdives_id_seq'::regclass);


--
-- Name: ignored_domains id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ignored_domains ALTER COLUMN id SET DEFAULT nextval('public.ignored_domains_id_seq'::regclass);


--
-- Name: ils_search_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ils_search_results ALTER COLUMN id SET DEFAULT nextval('public.ils_search_results_id_seq'::regclass);


--
-- Name: ils_supplier_mappings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ils_supplier_mappings ALTER COLUMN id SET DEFAULT nextval('public.ils_supplier_mappings_id_seq'::regclass);


--
-- Name: import_column_maps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_column_maps ALTER COLUMN id SET DEFAULT nextval('public.import_column_maps_id_seq'::regclass);


--
-- Name: import_headers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_headers ALTER COLUMN id SET DEFAULT nextval('public.import_headers_id_seq'::regclass);


--
-- Name: import_settings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_settings ALTER COLUMN id SET DEFAULT nextval('public.import_settings_id_seq'::regclass);


--
-- Name: import_status id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_status ALTER COLUMN id SET DEFAULT nextval('public.import_status_id_seq'::regclass);


--
-- Name: industries id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industries ALTER COLUMN id SET DEFAULT nextval('public.industries_id_seq'::regclass);


--
-- Name: industry_tags id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industry_tags ALTER COLUMN id SET DEFAULT nextval('public.industry_tags_id_seq'::regclass);


--
-- Name: invoice_discounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_discounts ALTER COLUMN id SET DEFAULT nextval('public.invoice_discounts_id_seq'::regclass);


--
-- Name: invoice_files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_files ALTER COLUMN id SET DEFAULT nextval('public.invoice_files_id_seq'::regclass);


--
-- Name: invoice_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_lines ALTER COLUMN id SET DEFAULT nextval('public.invoice_lines_id_seq'::regclass);


--
-- Name: invoice_payments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_payments ALTER COLUMN id SET DEFAULT nextval('public.invoice_payments_id_seq'::regclass);


--
-- Name: invoice_taxes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_taxes ALTER COLUMN id SET DEFAULT nextval('public.invoice_taxes_id_seq'::regclass);


--
-- Name: invoices id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices ALTER COLUMN id SET DEFAULT nextval('public.invoices_id_seq'::regclass);


--
-- Name: journal_entries id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journal_entries ALTER COLUMN id SET DEFAULT nextval('public.journal_entries_id_seq'::regclass);


--
-- Name: journal_entry_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journal_entry_lines ALTER COLUMN id SET DEFAULT nextval('public.journal_entry_lines_id_seq'::regclass);


--
-- Name: journal_entry_types id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journal_entry_types ALTER COLUMN id SET DEFAULT nextval('public.journal_entry_types_id_seq'::regclass);


--
-- Name: manufacturers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manufacturers ALTER COLUMN id SET DEFAULT nextval('public.manufacturers_id_seq'::regclass);


--
-- Name: offer_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offer_lines ALTER COLUMN id SET DEFAULT nextval('public.offer_lines_id_seq'::regclass);


--
-- Name: offers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offers ALTER COLUMN id SET DEFAULT nextval('public.offers_id_seq'::regclass);


--
-- Name: part_alt_groups id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_alt_groups ALTER COLUMN id SET DEFAULT nextval('public.part_alt_groups_id_seq'::regclass);


--
-- Name: part_categories category_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_categories ALTER COLUMN category_id SET DEFAULT nextval('public.part_categories_category_id_seq'::regclass);


--
-- Name: parts_list_line_suggested_suppliers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_line_suggested_suppliers ALTER COLUMN id SET DEFAULT nextval('public.parts_list_line_suggested_suppliers_id_seq'::regclass);


--
-- Name: parts_list_line_supplier_emails id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_line_supplier_emails ALTER COLUMN id SET DEFAULT nextval('public.parts_list_line_supplier_emails_id_seq'::regclass);


--
-- Name: parts_list_line_suppliers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_line_suppliers ALTER COLUMN id SET DEFAULT nextval('public.parts_list_line_suppliers_id_seq'::regclass);


--
-- Name: parts_list_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_lines ALTER COLUMN id SET DEFAULT nextval('public.parts_list_lines_id_seq'::regclass);


--
-- Name: parts_list_no_response_dismissals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_no_response_dismissals ALTER COLUMN id SET DEFAULT nextval('public.parts_list_no_response_dismissals_id_seq'::regclass);


--
-- Name: parts_list_statuses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_statuses ALTER COLUMN id SET DEFAULT nextval('public.parts_list_statuses_id_seq'::regclass);


--
-- Name: parts_list_supplier_quote_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_supplier_quote_lines ALTER COLUMN id SET DEFAULT nextval('public.parts_list_supplier_quote_lines_id_seq'::regclass);


--
-- Name: parts_list_supplier_quotes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_supplier_quotes ALTER COLUMN id SET DEFAULT nextval('public.parts_list_supplier_quotes_id_seq'::regclass);


--
-- Name: parts_lists id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_lists ALTER COLUMN id SET DEFAULT nextval('public.parts_lists_id_seq'::regclass);


--
-- Name: portal_api_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_api_log ALTER COLUMN id SET DEFAULT nextval('public.portal_api_log_id_seq'::regclass);


--
-- Name: portal_customer_margins id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_customer_margins ALTER COLUMN id SET DEFAULT nextval('public.portal_customer_margins_id_seq'::regclass);


--
-- Name: portal_customer_pricing id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_customer_pricing ALTER COLUMN id SET DEFAULT nextval('public.portal_customer_pricing_id_seq'::regclass);


--
-- Name: portal_pricing_agreement_requests id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_pricing_agreement_requests ALTER COLUMN id SET DEFAULT nextval('public.portal_pricing_agreement_requests_id_seq'::regclass);


--
-- Name: portal_purchase_order_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_purchase_order_lines ALTER COLUMN id SET DEFAULT nextval('public.portal_purchase_order_lines_id_seq'::regclass);


--
-- Name: portal_purchase_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_purchase_orders ALTER COLUMN id SET DEFAULT nextval('public.portal_purchase_orders_id_seq'::regclass);


--
-- Name: portal_quote_request_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_quote_request_lines ALTER COLUMN id SET DEFAULT nextval('public.portal_quote_request_lines_id_seq'::regclass);


--
-- Name: portal_quote_requests id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_quote_requests ALTER COLUMN id SET DEFAULT nextval('public.portal_quote_requests_id_seq'::regclass);


--
-- Name: portal_search_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_search_history ALTER COLUMN id SET DEFAULT nextval('public.portal_search_history_id_seq'::regclass);


--
-- Name: portal_settings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_settings ALTER COLUMN id SET DEFAULT nextval('public.portal_settings_id_seq'::regclass);


--
-- Name: portal_suggested_parts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_suggested_parts ALTER COLUMN id SET DEFAULT nextval('public.portal_suggested_parts_id_seq'::regclass);


--
-- Name: portal_users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_users ALTER COLUMN id SET DEFAULT nextval('public.portal_users_id_seq'::regclass);


--
-- Name: price_breaks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_breaks ALTER COLUMN id SET DEFAULT nextval('public.price_breaks_id_seq'::regclass);


--
-- Name: price_list_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_list_items ALTER COLUMN id SET DEFAULT nextval('public.price_list_items_id_seq'::regclass);


--
-- Name: price_lists id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_lists ALTER COLUMN id SET DEFAULT nextval('public.price_lists_id_seq'::regclass);


--
-- Name: priorities id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.priorities ALTER COLUMN id SET DEFAULT nextval('public.priorities_id_seq'::regclass);


--
-- Name: project_stages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_stages ALTER COLUMN id SET DEFAULT nextval('public.project_stages_id_seq'::regclass);


--
-- Name: project_statuses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_statuses ALTER COLUMN id SET DEFAULT nextval('public.project_statuses_id_seq'::regclass);


--
-- Name: project_updates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_updates ALTER COLUMN id SET DEFAULT nextval('public.project_updates_id_seq'::regclass);


--
-- Name: projects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects ALTER COLUMN id SET DEFAULT nextval('public.projects_id_seq'::regclass);


--
-- Name: purchase_order_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_lines ALTER COLUMN id SET DEFAULT nextval('public.purchase_order_lines_id_seq'::regclass);


--
-- Name: purchase_order_statuses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_statuses ALTER COLUMN id SET DEFAULT nextval('public.purchase_order_statuses_id_seq'::regclass);


--
-- Name: purchase_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders ALTER COLUMN id SET DEFAULT nextval('public.purchase_orders_id_seq'::regclass);


--
-- Name: reconciliation_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_items ALTER COLUMN id SET DEFAULT nextval('public.reconciliation_items_id_seq'::regclass);


--
-- Name: recurrence_types id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurrence_types ALTER COLUMN id SET DEFAULT nextval('public.recurrence_types_id_seq'::regclass);


--
-- Name: recurring_journal_template_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurring_journal_template_lines ALTER COLUMN id SET DEFAULT nextval('public.recurring_journal_template_lines_id_seq'::regclass);


--
-- Name: recurring_journal_templates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurring_journal_templates ALTER COLUMN id SET DEFAULT nextval('public.recurring_journal_templates_id_seq'::regclass);


--
-- Name: requisition_references id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requisition_references ALTER COLUMN id SET DEFAULT nextval('public.requisition_references_id_seq'::regclass);


--
-- Name: requisitions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requisitions ALTER COLUMN id SET DEFAULT nextval('public.requisitions_id_seq'::regclass);


--
-- Name: rfq_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_lines ALTER COLUMN id SET DEFAULT nextval('public.rfq_lines_id_seq'::regclass);


--
-- Name: rfq_updates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_updates ALTER COLUMN id SET DEFAULT nextval('public.rfq_updates_id_seq'::regclass);


--
-- Name: rfqs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfqs ALTER COLUMN id SET DEFAULT nextval('public.rfqs_id_seq'::regclass);


--
-- Name: sales_order_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_order_lines ALTER COLUMN id SET DEFAULT nextval('public.sales_order_lines_id_seq'::regclass);


--
-- Name: sales_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_orders ALTER COLUMN id SET DEFAULT nextval('public.sales_orders_id_seq'::regclass);


--
-- Name: sales_statuses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_statuses ALTER COLUMN id SET DEFAULT nextval('public.sales_statuses_id_seq'::regclass);


--
-- Name: salespeople id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salespeople ALTER COLUMN id SET DEFAULT nextval('public.salespeople_id_seq'::regclass);


--
-- Name: salesperson_engagement_settings salesperson_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salesperson_engagement_settings ALTER COLUMN salesperson_id SET DEFAULT nextval('public.salesperson_engagement_settings_salesperson_id_seq'::regclass);


--
-- Name: salesperson_monthly_goals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salesperson_monthly_goals ALTER COLUMN id SET DEFAULT nextval('public.salesperson_monthly_goals_id_seq'::regclass);


--
-- Name: salesperson_user_link id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salesperson_user_link ALTER COLUMN id SET DEFAULT nextval('public.salesperson_user_link_id_seq'::regclass);


--
-- Name: saved_queries id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_queries ALTER COLUMN id SET DEFAULT nextval('public.saved_queries_id_seq'::regclass);


--
-- Name: stage_updates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stage_updates ALTER COLUMN id SET DEFAULT nextval('public.stage_updates_id_seq'::regclass);


--
-- Name: statuses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.statuses ALTER COLUMN id SET DEFAULT nextval('public.statuses_id_seq'::regclass);


--
-- Name: stock_movements movement_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_movements ALTER COLUMN movement_id SET DEFAULT nextval('public.stock_movements_movement_id_seq'::regclass);


--
-- Name: supplier_contacts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_contacts ALTER COLUMN id SET DEFAULT nextval('public.supplier_contacts_id_seq'::regclass);


--
-- Name: supplier_domains id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_domains ALTER COLUMN id SET DEFAULT nextval('public.supplier_domains_id_seq'::regclass);


--
-- Name: suppliers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suppliers ALTER COLUMN id SET DEFAULT nextval('public.suppliers_id_seq'::regclass);


--
-- Name: sync_metadata id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_metadata ALTER COLUMN id SET DEFAULT nextval('public.sync_metadata_id_seq'::regclass);


--
-- Name: tax_rates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tax_rates ALTER COLUMN id SET DEFAULT nextval('public.tax_rates_id_seq'::regclass);


--
-- Name: template_placeholders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.template_placeholders ALTER COLUMN id SET DEFAULT nextval('public.template_placeholders_id_seq'::regclass);


--
-- Name: top_level_requisitions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.top_level_requisitions ALTER COLUMN id SET DEFAULT nextval('public.top_level_requisitions_id_seq'::regclass);


--
-- Name: user_permissions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions ALTER COLUMN id SET DEFAULT nextval('public.user_permissions_id_seq'::regclass);


--
-- Name: user_roles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles ALTER COLUMN id SET DEFAULT nextval('public.user_roles_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: vq_lines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vq_lines ALTER COLUMN id SET DEFAULT nextval('public.vq_lines_id_seq'::regclass);


--
-- Name: vqs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vqs ALTER COLUMN id SET DEFAULT nextval('public.vqs_id_seq'::regclass);


--
-- Name: watched_industry_tags id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watched_industry_tags ALTER COLUMN id SET DEFAULT nextval('public.watched_industry_tags_id_seq'::regclass);


--
-- Name: account_activity_log account_activity_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_activity_log
    ADD CONSTRAINT account_activity_log_pkey PRIMARY KEY (id);


--
-- Name: account_reconciliations account_reconciliations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_reconciliations
    ADD CONSTRAINT account_reconciliations_pkey PRIMARY KEY (id);


--
-- Name: account_types account_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_types
    ADD CONSTRAINT account_types_pkey PRIMARY KEY (id);


--
-- Name: acknowledgments acknowledgments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acknowledgments
    ADD CONSTRAINT acknowledgments_pkey PRIMARY KEY (id);


--
-- Name: ai_tag_suggestions ai_tag_suggestions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_tag_suggestions
    ADD CONSTRAINT ai_tag_suggestions_pkey PRIMARY KEY (id);


--
-- Name: alternative_part_numbers alternative_part_numbers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alternative_part_numbers
    ADD CONSTRAINT alternative_part_numbers_pkey PRIMARY KEY (id);


--
-- Name: app_settings app_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_pkey PRIMARY KEY (key);


--
-- Name: bom_files bom_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bom_files
    ADD CONSTRAINT bom_files_pkey PRIMARY KEY (bom_header_id, file_id);


--
-- Name: bom_headers bom_headers_base_part_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bom_headers
    ADD CONSTRAINT bom_headers_base_part_number_key UNIQUE (base_part_number);


--
-- Name: bom_headers bom_headers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bom_headers
    ADD CONSTRAINT bom_headers_pkey PRIMARY KEY (id);


--
-- Name: bom_lines bom_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bom_lines
    ADD CONSTRAINT bom_lines_pkey PRIMARY KEY (id);


--
-- Name: bom_pricing bom_pricing_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bom_pricing
    ADD CONSTRAINT bom_pricing_pkey PRIMARY KEY (id);


--
-- Name: bom_revisions bom_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bom_revisions
    ADD CONSTRAINT bom_revisions_pkey PRIMARY KEY (id);


--
-- Name: call_list call_list_contact_id_salesperson_id_is_active_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_list
    ADD CONSTRAINT call_list_contact_id_salesperson_id_is_active_key UNIQUE (contact_id, salesperson_id, is_active);


--
-- Name: call_list call_list_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_list
    ADD CONSTRAINT call_list_pkey PRIMARY KEY (id);


--
-- Name: chart_of_accounts chart_of_accounts_account_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chart_of_accounts
    ADD CONSTRAINT chart_of_accounts_account_number_key UNIQUE (account_number);


--
-- Name: chart_of_accounts chart_of_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chart_of_accounts
    ADD CONSTRAINT chart_of_accounts_pkey PRIMARY KEY (id);


--
-- Name: company_types company_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_types
    ADD CONSTRAINT company_types_pkey PRIMARY KEY (id);


--
-- Name: contact_communications contact_communications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_communications
    ADD CONSTRAINT contact_communications_pkey PRIMARY KEY (id);


--
-- Name: contact_list_members contact_list_members_list_id_contact_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_list_members
    ADD CONSTRAINT contact_list_members_list_id_contact_id_key UNIQUE (list_id, contact_id);


--
-- Name: contact_list_members contact_list_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_list_members
    ADD CONSTRAINT contact_list_members_pkey PRIMARY KEY (id);


--
-- Name: contact_lists contact_lists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_lists
    ADD CONSTRAINT contact_lists_pkey PRIMARY KEY (id);


--
-- Name: contact_statuses contact_statuses_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_statuses
    ADD CONSTRAINT contact_statuses_name_key UNIQUE (name);


--
-- Name: contact_statuses contact_statuses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_statuses
    ADD CONSTRAINT contact_statuses_pkey PRIMARY KEY (id);


--
-- Name: contacts contacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT contacts_pkey PRIMARY KEY (id);


--
-- Name: cq_lines cq_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cq_lines
    ADD CONSTRAINT cq_lines_pkey PRIMARY KEY (id);


--
-- Name: cqs cqs_cq_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cqs
    ADD CONSTRAINT cqs_cq_number_key UNIQUE (cq_number);


--
-- Name: cqs cqs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cqs
    ADD CONSTRAINT cqs_pkey PRIMARY KEY (id);


--
-- Name: currencies currencies_currency_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.currencies
    ADD CONSTRAINT currencies_currency_code_key UNIQUE (currency_code);


--
-- Name: currencies currencies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.currencies
    ADD CONSTRAINT currencies_pkey PRIMARY KEY (id);


--
-- Name: customer_addresses customer_addresses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_addresses
    ADD CONSTRAINT customer_addresses_pkey PRIMARY KEY (id);


--
-- Name: customer_associations customer_associations_main_customer_id_associated_customer__key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_associations
    ADD CONSTRAINT customer_associations_main_customer_id_associated_customer__key UNIQUE (main_customer_id, associated_customer_id);


--
-- Name: customer_associations customer_associations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_associations
    ADD CONSTRAINT customer_associations_pkey PRIMARY KEY (id);


--
-- Name: customer_boms customer_boms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_boms
    ADD CONSTRAINT customer_boms_pkey PRIMARY KEY (customer_id, bom_header_id);


--
-- Name: customer_company_types customer_company_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_company_types
    ADD CONSTRAINT customer_company_types_pkey PRIMARY KEY (customer_id, company_type_id);


--
-- Name: customer_development_answers customer_development_answers_customer_id_development_point__key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_development_answers
    ADD CONSTRAINT customer_development_answers_customer_id_development_point__key UNIQUE (customer_id, development_point_id);


--
-- Name: customer_development_answers customer_development_answers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_development_answers
    ADD CONSTRAINT customer_development_answers_pkey PRIMARY KEY (id);


--
-- Name: customer_domains customer_domains_customer_id_domain_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_domains
    ADD CONSTRAINT customer_domains_customer_id_domain_key UNIQUE (customer_id, domain);


--
-- Name: customer_domains customer_domains_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_domains
    ADD CONSTRAINT customer_domains_pkey PRIMARY KEY (id);


--
-- Name: customer_enrichment_status customer_enrichment_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_enrichment_status
    ADD CONSTRAINT customer_enrichment_status_pkey PRIMARY KEY (customer_id);


--
-- Name: customer_industries customer_industries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_industries
    ADD CONSTRAINT customer_industries_pkey PRIMARY KEY (customer_id, industry_id);


--
-- Name: customer_industry_tags customer_industry_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_industry_tags
    ADD CONSTRAINT customer_industry_tags_pkey PRIMARY KEY (customer_id, tag_id);


--
-- Name: customer_insights customer_insights_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_insights
    ADD CONSTRAINT customer_insights_pkey PRIMARY KEY (id);


--
-- Name: customer_monthly_targets customer_monthly_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_monthly_targets
    ADD CONSTRAINT customer_monthly_targets_pkey PRIMARY KEY (id);


--
-- Name: customer_monthly_targets customer_monthly_targets_salesperson_id_customer_id_target__key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_monthly_targets
    ADD CONSTRAINT customer_monthly_targets_salesperson_id_customer_id_target__key UNIQUE (salesperson_id, customer_id, target_month);


--
-- Name: customer_part_numbers customer_part_numbers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_part_numbers
    ADD CONSTRAINT customer_part_numbers_pkey PRIMARY KEY (id);


--
-- Name: customer_quote_lines customer_quote_lines_parts_list_line_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_quote_lines
    ADD CONSTRAINT customer_quote_lines_parts_list_line_id_key UNIQUE (parts_list_line_id);


--
-- Name: customer_quote_lines customer_quote_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_quote_lines
    ADD CONSTRAINT customer_quote_lines_pkey PRIMARY KEY (id);


--
-- Name: customer_status customer_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_status
    ADD CONSTRAINT customer_status_pkey PRIMARY KEY (id);


--
-- Name: customer_updates customer_updates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_updates
    ADD CONSTRAINT customer_updates_pkey PRIMARY KEY (id);


--
-- Name: customers customers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_pkey PRIMARY KEY (id);


--
-- Name: dashboard_panels dashboard_panels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dashboard_panels
    ADD CONSTRAINT dashboard_panels_pkey PRIMARY KEY (id);


--
-- Name: deepdive_curated_customers deepdive_curated_customers_deepdive_id_customer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deepdive_curated_customers
    ADD CONSTRAINT deepdive_curated_customers_deepdive_id_customer_id_key UNIQUE (deepdive_id, customer_id);


--
-- Name: deepdive_curated_customers deepdive_curated_customers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deepdive_curated_customers
    ADD CONSTRAINT deepdive_curated_customers_pkey PRIMARY KEY (id);


--
-- Name: deepdive_customer_links deepdive_customer_links_deepdive_id_customer_id_linked_text_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deepdive_customer_links
    ADD CONSTRAINT deepdive_customer_links_deepdive_id_customer_id_linked_text_key UNIQUE (deepdive_id, customer_id, linked_text);


--
-- Name: deepdive_customer_links deepdive_customer_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deepdive_customer_links
    ADD CONSTRAINT deepdive_customer_links_pkey PRIMARY KEY (id);


--
-- Name: development_points development_points_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.development_points
    ADD CONSTRAINT development_points_pkey PRIMARY KEY (id);


--
-- Name: email_logs email_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_logs
    ADD CONSTRAINT email_logs_pkey PRIMARY KEY (id);


--
-- Name: email_signatures email_signatures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_signatures
    ADD CONSTRAINT email_signatures_pkey PRIMARY KEY (id);


--
-- Name: email_templates email_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_templates
    ADD CONSTRAINT email_templates_pkey PRIMARY KEY (id);


--
-- Name: emails emails_message_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emails
    ADD CONSTRAINT emails_message_id_key UNIQUE (message_id);


--
-- Name: emails emails_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emails
    ADD CONSTRAINT emails_pkey PRIMARY KEY (id);


--
-- Name: excess_stock_files excess_stock_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.excess_stock_files
    ADD CONSTRAINT excess_stock_files_pkey PRIMARY KEY (excess_stock_list_id, file_id);


--
-- Name: excess_stock_lines excess_stock_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.excess_stock_lines
    ADD CONSTRAINT excess_stock_lines_pkey PRIMARY KEY (id);


--
-- Name: excess_stock_lists excess_stock_lists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.excess_stock_lists
    ADD CONSTRAINT excess_stock_lists_pkey PRIMARY KEY (id);


--
-- Name: files files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT files_pkey PRIMARY KEY (id);


--
-- Name: financial_report_mappings financial_report_mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.financial_report_mappings
    ADD CONSTRAINT financial_report_mappings_pkey PRIMARY KEY (id);


--
-- Name: financial_report_settings financial_report_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.financial_report_settings
    ADD CONSTRAINT financial_report_settings_pkey PRIMARY KEY (id);


--
-- Name: fiscal_periods fiscal_periods_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiscal_periods
    ADD CONSTRAINT fiscal_periods_pkey PRIMARY KEY (id);


--
-- Name: fiscal_years fiscal_years_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiscal_years
    ADD CONSTRAINT fiscal_years_pkey PRIMARY KEY (id);


--
-- Name: geographic_deepdives geographic_deepdives_country_tag_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.geographic_deepdives
    ADD CONSTRAINT geographic_deepdives_country_tag_id_key UNIQUE (country, tag_id);


--
-- Name: geographic_deepdives geographic_deepdives_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.geographic_deepdives
    ADD CONSTRAINT geographic_deepdives_pkey PRIMARY KEY (id);


--
-- Name: ignored_domains ignored_domains_domain_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ignored_domains
    ADD CONSTRAINT ignored_domains_domain_key UNIQUE (domain);


--
-- Name: ignored_domains ignored_domains_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ignored_domains
    ADD CONSTRAINT ignored_domains_pkey PRIMARY KEY (id);


--
-- Name: ils_search_results ils_search_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ils_search_results
    ADD CONSTRAINT ils_search_results_pkey PRIMARY KEY (id);


--
-- Name: ils_supplier_mappings ils_supplier_mappings_ils_company_name_ils_cage_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ils_supplier_mappings
    ADD CONSTRAINT ils_supplier_mappings_ils_company_name_ils_cage_code_key UNIQUE (ils_company_name, ils_cage_code);


--
-- Name: ils_supplier_mappings ils_supplier_mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ils_supplier_mappings
    ADD CONSTRAINT ils_supplier_mappings_pkey PRIMARY KEY (id);


--
-- Name: import_column_maps import_column_maps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_column_maps
    ADD CONSTRAINT import_column_maps_pkey PRIMARY KEY (id);


--
-- Name: import_headers import_headers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_headers
    ADD CONSTRAINT import_headers_pkey PRIMARY KEY (id);


--
-- Name: import_settings import_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_settings
    ADD CONSTRAINT import_settings_pkey PRIMARY KEY (id);


--
-- Name: import_status import_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_status
    ADD CONSTRAINT import_status_pkey PRIMARY KEY (id);


--
-- Name: industries industries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industries
    ADD CONSTRAINT industries_pkey PRIMARY KEY (id);


--
-- Name: industry_tags industry_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industry_tags
    ADD CONSTRAINT industry_tags_pkey PRIMARY KEY (id);


--
-- Name: invoice_discounts invoice_discounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_discounts
    ADD CONSTRAINT invoice_discounts_pkey PRIMARY KEY (id);


--
-- Name: invoice_files invoice_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_files
    ADD CONSTRAINT invoice_files_pkey PRIMARY KEY (id);


--
-- Name: invoice_lines invoice_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_lines
    ADD CONSTRAINT invoice_lines_pkey PRIMARY KEY (id);


--
-- Name: invoice_payments invoice_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_payments
    ADD CONSTRAINT invoice_payments_pkey PRIMARY KEY (id);


--
-- Name: invoice_taxes invoice_taxes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_taxes
    ADD CONSTRAINT invoice_taxes_pkey PRIMARY KEY (id);


--
-- Name: invoices invoices_invoice_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_invoice_number_key UNIQUE (invoice_number);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (id);


--
-- Name: journal_entries journal_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journal_entries
    ADD CONSTRAINT journal_entries_pkey PRIMARY KEY (id);


--
-- Name: journal_entry_lines journal_entry_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journal_entry_lines
    ADD CONSTRAINT journal_entry_lines_pkey PRIMARY KEY (id);


--
-- Name: journal_entry_types journal_entry_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journal_entry_types
    ADD CONSTRAINT journal_entry_types_pkey PRIMARY KEY (id);


--
-- Name: manufacturers manufacturers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manufacturers
    ADD CONSTRAINT manufacturers_pkey PRIMARY KEY (id);


--
-- Name: offer_files offer_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offer_files
    ADD CONSTRAINT offer_files_pkey PRIMARY KEY (offer_id, file_id);


--
-- Name: offer_lines offer_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offer_lines
    ADD CONSTRAINT offer_lines_pkey PRIMARY KEY (id);


--
-- Name: offers offers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offers
    ADD CONSTRAINT offers_pkey PRIMARY KEY (id);


--
-- Name: part_alt_group_members part_alt_group_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_alt_group_members
    ADD CONSTRAINT part_alt_group_members_pkey PRIMARY KEY (group_id, base_part_number);


--
-- Name: part_alt_groups part_alt_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_alt_groups
    ADD CONSTRAINT part_alt_groups_pkey PRIMARY KEY (id);


--
-- Name: part_categories part_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_categories
    ADD CONSTRAINT part_categories_pkey PRIMARY KEY (category_id);


--
-- Name: part_numbers part_numbers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_numbers
    ADD CONSTRAINT part_numbers_pkey PRIMARY KEY (base_part_number);


--
-- Name: parts_list_line_suggested_suppliers parts_list_line_suggested_sup_parts_list_line_id_supplier_i_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_line_suggested_suppliers
    ADD CONSTRAINT parts_list_line_suggested_sup_parts_list_line_id_supplier_i_key UNIQUE (parts_list_line_id, supplier_id);


--
-- Name: parts_list_line_suggested_suppliers parts_list_line_suggested_suppliers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_line_suggested_suppliers
    ADD CONSTRAINT parts_list_line_suggested_suppliers_pkey PRIMARY KEY (id);


--
-- Name: parts_list_line_supplier_emails parts_list_line_supplier_emails_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_line_supplier_emails
    ADD CONSTRAINT parts_list_line_supplier_emails_pkey PRIMARY KEY (id);


--
-- Name: parts_list_line_suppliers parts_list_line_suppliers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_line_suppliers
    ADD CONSTRAINT parts_list_line_suppliers_pkey PRIMARY KEY (id);


--
-- Name: parts_list_lines parts_list_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_lines
    ADD CONSTRAINT parts_list_lines_pkey PRIMARY KEY (id);


--
-- Name: parts_list_no_response_dismissals parts_list_no_response_dismissals_email_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_no_response_dismissals
    ADD CONSTRAINT parts_list_no_response_dismissals_email_id_key UNIQUE (email_id);


--
-- Name: parts_list_no_response_dismissals parts_list_no_response_dismissals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_no_response_dismissals
    ADD CONSTRAINT parts_list_no_response_dismissals_pkey PRIMARY KEY (id);


--
-- Name: parts_list_statuses parts_list_statuses_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_statuses
    ADD CONSTRAINT parts_list_statuses_name_key UNIQUE (name);


--
-- Name: parts_list_statuses parts_list_statuses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_statuses
    ADD CONSTRAINT parts_list_statuses_pkey PRIMARY KEY (id);


--
-- Name: parts_list_supplier_quote_lines parts_list_supplier_quote_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_supplier_quote_lines
    ADD CONSTRAINT parts_list_supplier_quote_lines_pkey PRIMARY KEY (id);


--
-- Name: parts_list_supplier_quotes parts_list_supplier_quotes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_list_supplier_quotes
    ADD CONSTRAINT parts_list_supplier_quotes_pkey PRIMARY KEY (id);


--
-- Name: parts_lists parts_lists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts_lists
    ADD CONSTRAINT parts_lists_pkey PRIMARY KEY (id);


--
-- Name: portal_api_log portal_api_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_api_log
    ADD CONSTRAINT portal_api_log_pkey PRIMARY KEY (id);


--
-- Name: portal_customer_margins portal_customer_margins_customer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_customer_margins
    ADD CONSTRAINT portal_customer_margins_customer_id_key UNIQUE (customer_id);


--
-- Name: portal_customer_margins portal_customer_margins_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_customer_margins
    ADD CONSTRAINT portal_customer_margins_pkey PRIMARY KEY (id);


--
-- Name: portal_customer_pricing portal_customer_pricing_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_customer_pricing
    ADD CONSTRAINT portal_customer_pricing_pkey PRIMARY KEY (id);


--
-- Name: portal_pricing_agreement_requests portal_pricing_agreement_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_pricing_agreement_requests
    ADD CONSTRAINT portal_pricing_agreement_requests_pkey PRIMARY KEY (id);


--
-- Name: portal_pricing_agreement_requests portal_pricing_agreement_requests_reference_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_pricing_agreement_requests
    ADD CONSTRAINT portal_pricing_agreement_requests_reference_number_key UNIQUE (reference_number);


--
-- Name: portal_purchase_order_lines portal_purchase_order_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_purchase_order_lines
    ADD CONSTRAINT portal_purchase_order_lines_pkey PRIMARY KEY (id);


--
-- Name: portal_purchase_orders portal_purchase_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_purchase_orders
    ADD CONSTRAINT portal_purchase_orders_pkey PRIMARY KEY (id);


--
-- Name: portal_purchase_orders portal_purchase_orders_po_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_purchase_orders
    ADD CONSTRAINT portal_purchase_orders_po_reference_key UNIQUE (po_reference);


--
-- Name: portal_quote_request_lines portal_quote_request_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_quote_request_lines
    ADD CONSTRAINT portal_quote_request_lines_pkey PRIMARY KEY (id);


--
-- Name: portal_quote_requests portal_quote_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_quote_requests
    ADD CONSTRAINT portal_quote_requests_pkey PRIMARY KEY (id);


--
-- Name: portal_quote_requests portal_quote_requests_reference_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_quote_requests
    ADD CONSTRAINT portal_quote_requests_reference_number_key UNIQUE (reference_number);


--
-- Name: portal_search_history portal_search_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_search_history
    ADD CONSTRAINT portal_search_history_pkey PRIMARY KEY (id);


--
-- Name: portal_settings portal_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_settings
    ADD CONSTRAINT portal_settings_pkey PRIMARY KEY (id);


--
-- Name: portal_settings portal_settings_setting_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_settings
    ADD CONSTRAINT portal_settings_setting_key_key UNIQUE (setting_key);


--
-- Name: portal_suggested_parts portal_suggested_parts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_suggested_parts
    ADD CONSTRAINT portal_suggested_parts_pkey PRIMARY KEY (id);


--
-- Name: portal_users portal_users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_users
    ADD CONSTRAINT portal_users_email_key UNIQUE (email);


--
-- Name: portal_users portal_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portal_users
    ADD CONSTRAINT portal_users_pkey PRIMARY KEY (id);


--
-- Name: price_breaks price_breaks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_breaks
    ADD CONSTRAINT price_breaks_pkey PRIMARY KEY (id);


--
-- Name: price_list_items price_list_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_list_items
    ADD CONSTRAINT price_list_items_pkey PRIMARY KEY (id);


--
-- Name: price_lists price_lists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_lists
    ADD CONSTRAINT price_lists_pkey PRIMARY KEY (id);


--
-- Name: priorities priorities_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.priorities
    ADD CONSTRAINT priorities_name_key UNIQUE (name);


--
-- Name: priorities priorities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.priorities
    ADD CONSTRAINT priorities_pkey PRIMARY KEY (id);


--
-- Name: project_files project_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_files
    ADD CONSTRAINT project_files_pkey PRIMARY KEY (project_id, file_id);


--
-- Name: project_rfqs project_rfqs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_rfqs
    ADD CONSTRAINT project_rfqs_pkey PRIMARY KEY (project_id, rfq_id);


--
-- Name: project_stage_salespeople project_stage_salespeople_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_stage_salespeople
    ADD CONSTRAINT project_stage_salespeople_pkey PRIMARY KEY (stage_id, salesperson_id);


--
-- Name: project_stages project_stages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_stages
    ADD CONSTRAINT project_stages_pkey PRIMARY KEY (id);


--
-- Name: project_statuses project_statuses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_statuses
    ADD CONSTRAINT project_statuses_pkey PRIMARY KEY (id);


--
-- Name: project_updates project_updates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_updates
    ADD CONSTRAINT project_updates_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: purchase_order_lines purchase_order_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_lines
    ADD CONSTRAINT purchase_order_lines_pkey PRIMARY KEY (id);


--
-- Name: purchase_order_statuses purchase_order_statuses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_statuses
    ADD CONSTRAINT purchase_order_statuses_pkey PRIMARY KEY (id);


--
-- Name: purchase_orders purchase_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_pkey PRIMARY KEY (id);


--
-- Name: reconciliation_items reconciliation_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_items
    ADD CONSTRAINT reconciliation_items_pkey PRIMARY KEY (id);


--
-- Name: recurrence_types recurrence_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurrence_types
    ADD CONSTRAINT recurrence_types_pkey PRIMARY KEY (id);


--
-- Name: recurring_journal_template_lines recurring_journal_template_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurring_journal_template_lines
    ADD CONSTRAINT recurring_journal_template_lines_pkey PRIMARY KEY (id);


--
-- Name: recurring_journal_templates recurring_journal_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurring_journal_templates
    ADD CONSTRAINT recurring_journal_templates_pkey PRIMARY KEY (id);


--
-- Name: requisition_references requisition_references_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requisition_references
    ADD CONSTRAINT requisition_references_pkey PRIMARY KEY (id);


--
-- Name: requisitions requisitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requisitions
    ADD CONSTRAINT requisitions_pkey PRIMARY KEY (id);


--
-- Name: rfq_files rfq_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_files
    ADD CONSTRAINT rfq_files_pkey PRIMARY KEY (rfq_id, file_id);


--
-- Name: rfq_line_part_alternatives rfq_line_part_alternatives_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_line_part_alternatives
    ADD CONSTRAINT rfq_line_part_alternatives_pkey PRIMARY KEY (rfq_line_id, primary_base_part_number, alternative_base_part_number);


--
-- Name: rfq_lines rfq_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_lines
    ADD CONSTRAINT rfq_lines_pkey PRIMARY KEY (id);


--
-- Name: rfq_updates rfq_updates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_updates
    ADD CONSTRAINT rfq_updates_pkey PRIMARY KEY (id);


--
-- Name: rfqs rfqs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfqs
    ADD CONSTRAINT rfqs_pkey PRIMARY KEY (id);


--
-- Name: sales_order_lines sales_order_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_order_lines
    ADD CONSTRAINT sales_order_lines_pkey PRIMARY KEY (id);


--
-- Name: sales_orders sales_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_orders
    ADD CONSTRAINT sales_orders_pkey PRIMARY KEY (id);


--
-- Name: sales_statuses sales_statuses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_statuses
    ADD CONSTRAINT sales_statuses_pkey PRIMARY KEY (id);


--
-- Name: salespeople salespeople_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salespeople
    ADD CONSTRAINT salespeople_pkey PRIMARY KEY (id);


--
-- Name: salesperson_engagement_settings salesperson_engagement_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salesperson_engagement_settings
    ADD CONSTRAINT salesperson_engagement_settings_pkey PRIMARY KEY (salesperson_id);


--
-- Name: salesperson_monthly_goals salesperson_monthly_goals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salesperson_monthly_goals
    ADD CONSTRAINT salesperson_monthly_goals_pkey PRIMARY KEY (id);


--
-- Name: salesperson_monthly_goals salesperson_monthly_goals_salesperson_id_target_month_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salesperson_monthly_goals
    ADD CONSTRAINT salesperson_monthly_goals_salesperson_id_target_month_key UNIQUE (salesperson_id, target_month);


--
-- Name: salesperson_user_link salesperson_user_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salesperson_user_link
    ADD CONSTRAINT salesperson_user_link_pkey PRIMARY KEY (id);


--
-- Name: saved_queries saved_queries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_queries
    ADD CONSTRAINT saved_queries_pkey PRIMARY KEY (id);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: stage_files stage_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stage_files
    ADD CONSTRAINT stage_files_pkey PRIMARY KEY (stage_id, file_id);


--
-- Name: stage_updates stage_updates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stage_updates
    ADD CONSTRAINT stage_updates_pkey PRIMARY KEY (id);


--
-- Name: statuses statuses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.statuses
    ADD CONSTRAINT statuses_pkey PRIMARY KEY (id);


--
-- Name: statuses statuses_status_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.statuses
    ADD CONSTRAINT statuses_status_key UNIQUE (status);


--
-- Name: stock_movements stock_movements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_movements
    ADD CONSTRAINT stock_movements_pkey PRIMARY KEY (movement_id);


--
-- Name: supplier_contacts supplier_contacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_contacts
    ADD CONSTRAINT supplier_contacts_pkey PRIMARY KEY (id);


--
-- Name: supplier_domains supplier_domains_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_domains
    ADD CONSTRAINT supplier_domains_pkey PRIMARY KEY (id);


--
-- Name: supplier_domains supplier_domains_supplier_id_domain_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_domains
    ADD CONSTRAINT supplier_domains_supplier_id_domain_key UNIQUE (supplier_id, domain);


--
-- Name: suppliers suppliers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suppliers
    ADD CONSTRAINT suppliers_pkey PRIMARY KEY (id);


--
-- Name: sync_metadata sync_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_metadata
    ADD CONSTRAINT sync_metadata_pkey PRIMARY KEY (id);


--
-- Name: tax_rates tax_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tax_rates
    ADD CONSTRAINT tax_rates_pkey PRIMARY KEY (id);


--
-- Name: template_industry_tags template_industry_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.template_industry_tags
    ADD CONSTRAINT template_industry_tags_pkey PRIMARY KEY (template_id, industry_tag_id);


--
-- Name: template_placeholders template_placeholders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.template_placeholders
    ADD CONSTRAINT template_placeholders_pkey PRIMARY KEY (id);


--
-- Name: template_placeholders template_placeholders_placeholder_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.template_placeholders
    ADD CONSTRAINT template_placeholders_placeholder_key_key UNIQUE (placeholder_key);


--
-- Name: top_level_requisitions top_level_requisitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.top_level_requisitions
    ADD CONSTRAINT top_level_requisitions_pkey PRIMARY KEY (id);


--
-- Name: user_permissions user_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT user_permissions_pkey PRIMARY KEY (id);


--
-- Name: user_permissions user_permissions_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT user_permissions_user_id_key UNIQUE (user_id);


--
-- Name: user_roles user_roles_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_name_key UNIQUE (name);


--
-- Name: user_roles user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: vq_lines vq_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vq_lines
    ADD CONSTRAINT vq_lines_pkey PRIMARY KEY (id);


--
-- Name: vqs vqs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vqs
    ADD CONSTRAINT vqs_pkey PRIMARY KEY (id);


--
-- Name: vqs vqs_vq_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vqs
    ADD CONSTRAINT vqs_vq_number_key UNIQUE (vq_number);


--
-- Name: watched_industry_tags watched_industry_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watched_industry_tags
    ADD CONSTRAINT watched_industry_tags_pkey PRIMARY KEY (id);


--
-- Name: watched_industry_tags watched_industry_tags_user_id_tag_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watched_industry_tags
    ADD CONSTRAINT watched_industry_tags_user_id_tag_id_key UNIQUE (user_id, tag_id);


--
-- PostgreSQL database dump complete
--

\unrestrict Zhyhvs17HdmeDgwj2hh8yCrP9IGxHrIKnkDrmIF1n9wuInAGtU64RoJByV7qecT
