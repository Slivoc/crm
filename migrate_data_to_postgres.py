"""
Automated SQLite to PostgreSQL Data Migration Script

This script:
1. Extracts data from SQLite in correct order (respecting foreign keys)
2. Converts data types (booleans, dates, nulls)
3. Imports into PostgreSQL
4. Resets sequences
5. Provides progress tracking and error handling

Usage:
    python migrate_data_to_postgres.py [--dry-run] [--table TABLE_NAME]
"""

import argparse
from datetime import datetime
import os
import re
import sqlite3
import sys

import psycopg2
from psycopg2 import errorcodes, errors as pg_errors
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Database connections
SQLITE_DB = 'database.db'
POSTGRES_DSN = os.getenv('DATABASE_URL')
DEFAULT_SCHEMA_FILE = 'myapp_schema.sql'

DUPLICATE_PGCODES = {
    errorcodes.DUPLICATE_TABLE,
    errorcodes.DUPLICATE_SCHEMA,
    errorcodes.DUPLICATE_FUNCTION,
    errorcodes.DUPLICATE_COLUMN,
    errorcodes.DUPLICATE_OBJECT,
}

# Column name mappings (SQLite column -> PostgreSQL column)
COLUMN_MAPPINGS = {
    'part_numbers': {
        'SPQ': 'spq'  # SQLite uses uppercase, PostgreSQL uses lowercase
    }
}

# Migration order (respecting foreign key dependencies)
MIGRATION_ORDER = [
    # Phase 1: Independent lookup/config tables (no foreign keys)
    'currencies',
    'priorities',
    'industry_tags',
    'development_points',
    'customer_status',
    'contact_statuses',
    'sales_statuses',
    'purchase_order_statuses',
    'project_statuses',
    'parts_list_statuses',
    'manufacturers',
    'tax_rates',
    'recurrence_types',
    'journal_entry_types',
    'account_types',
    'fiscal_years',
    'fiscal_periods',
    'industries',
    'company_types',
    'statuses',
    'user_roles',
    'app_settings',
    'settings',
    'sync_metadata',
    'dashboard_panels',
    'template_placeholders',
    'email_templates',
    'email_signatures',
    'ignored_domains',
    'chart_of_accounts',
    'financial_report_settings',
    'financial_report_mappings',
    'import_settings',
    'portal_settings',
    
    # Phase 2: Users and Salespeople
    'users',
    'salespeople',
    'salesperson_user_link',
    'user_permissions',
    'email_logs',
    
    # Phase 3: Core Entities (Customers, Contacts, Parts, Suppliers)
    'customers',
    'customer_domains',
    'customer_enrichment_status',
    'customer_insights',
    'customer_addresses',
    'customer_company_types',
    'customer_industries',
    'contacts',
    'supplier_domains',
    'suppliers',
    'supplier_contacts',
    'part_numbers',
    'part_categories',
    'part_manufacturers',
    'alternative_part_numbers',
    'customer_part_numbers',
    
    # Phase 4: Part Alternatives & Groups
    'part_alt_groups',
    'part_alt_group_members',
    
    # Phase 5: Customer Relationships
    'customer_industry_tags',
    'customer_associations',
    'customer_development_answers',
    'customer_updates',
    'watched_industry_tags',
    'ai_tag_suggestions',
    'template_industry_tags',
    
    # Phase 6: Contact Management
    'contact_communications',
    'contact_lists',
    'contact_list_members',
    'call_list',
    
    # Phase 7: Geographic & Market Analysis
    'geographic_deepdives',
    'deepdive_curated_customers',
    'deepdive_customer_links',
    
    # Phase 8: Projects & Stages
    'projects',
    'project_updates',
    'project_stages',
    'project_stage_salespeople',
    'project_files',
    'stage_updates',
    'stage_files',
    
    # Phase 9: RFQs (Request for Quotes)
    'rfqs',
    'rfq_lines',
    'rfq_line_part_alternatives',
    'rfq_updates',
    'rfq_files',
    'project_rfqs',
    
    # Phase 10: Sales Orders
    'sales_orders',
    'sales_order_lines',
    
    # Phase 11: Purchase Orders
    'purchase_orders',
    'purchase_order_lines',
    
    # Phase 12: Customer Quotes (CQs)
    'cqs',
    'cq_lines',
    'customer_quote_lines',
    
    # Phase 13: Vendor Quotes (VQs)
    'vqs',
    'vq_lines',
    
    # Phase 14: Parts Lists
    'parts_lists',
    'parts_list_lines',
    'parts_list_line_suppliers',
    'parts_list_line_suggested_suppliers',
    'parts_list_line_supplier_emails',
    'parts_list_supplier_quotes',
    'parts_list_supplier_quote_lines',
    'parts_list_no_response_dismissals',
    
    # Phase 15: BOMs (Bill of Materials)
    'bom_headers',
    'bom_lines',
    'bom_revisions',
    'bom_files',
    'bom_pricing',
    'customer_boms',
    
    # Phase 16: Offers
    'offers',
    'offer_lines',
    'offer_files',
    
    # Phase 17: Excess Stock
    'excess_stock_lists',
    'excess_stock_files',
    'excess_stock_lines',
    
    # Phase 18: Price Lists
    'price_lists',
    'price_list_items',
    'price_breaks',
    
    # Phase 19: Stock Management
    'stock_movements',
    
    # Phase 20: Requisitions
    'requisitions',
    'top_level_requisitions',
    'requisition_references',
    
    # Phase 21: ILS (Inventory Locator Service)
    'ils_supplier_mappings',
    'ils_search_results',
    
    # Phase 22: Files & Emails
    'files',
    'emails',
    
    # Phase 23: Saved Queries
    'saved_queries',
    
    # Phase 24: Import Management
    'import_status',
    'import_headers',
    'import_column_maps',
    
    # Phase 25: Invoicing
    'invoices',
    'invoice_lines',
    'invoice_taxes',
    'invoice_discounts',
    'invoice_payments',
    'invoice_files',
    
    # Phase 26: Accounting
    'journal_entries',
    'journal_entry_lines',
    'recurring_journal_templates',
    'recurring_journal_template_lines',
    'account_reconciliations',
    'reconciliation_items',
    'account_activity_log',
    
    # Phase 27: Planning & Targeting
    'salesperson_monthly_goals',
    'customer_monthly_targets',
    'salesperson_engagement_settings',
    
    # Phase 28: Portal
    'portal_users',
    'portal_customer_pricing',
    'portal_customer_margins',
    'portal_suggested_parts',
    'portal_search_history',
    'portal_api_log',
    'portal_quote_requests',
    'portal_quote_request_lines',
    'portal_purchase_orders',
    'portal_purchase_order_lines',
    'portal_pricing_agreement_requests',
    
    # Phase 29: Acknowledgments
    'acknowledgments',
]

def get_table_columns(sqlite_conn, table_name):
    """Get column names and types from SQLite table"""
    cursor = sqlite_conn.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    return [(col[1], col[2].upper()) for col in columns]  # (name, type)

def get_pg_column_types(pg_conn, table_name):
    """Get actual PostgreSQL column types to detect booleans"""
    cursor = pg_conn.cursor()
    cursor.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = %s
    """, (table_name,))
    return {row[0]: row[1] for row in cursor.fetchall()}

def convert_value(value, col_type):
    """Convert SQLite value to PostgreSQL-compatible format"""
    if value is None:
        return None
    
    # Boolean conversion
    if col_type in ('BOOLEAN', 'BOOL'):
        if isinstance(value, (int, str)):
            return value in (1, '1', 'true', 'TRUE', True)
        return bool(value)
    
    # Empty string to NULL for numeric types
    if col_type in ('INTEGER', 'REAL', 'NUMERIC', 'DECIMAL') and value == '':
        return None
    
    # Date/Time handling
    if col_type in ('DATE', 'DATETIME', 'TIMESTAMP'):
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None
    
    return value

def table_exists(sqlite_conn, table_name):
    """Check if table exists in SQLite"""
    cursor = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None

def migrate_table(sqlite_conn, pg_conn, table_name, dry_run=False):
    """Migrate a single table from SQLite to PostgreSQL"""
    print(f"\n📦 Migrating table: {table_name}")
    
    # Check if table exists
    if not table_exists(sqlite_conn, table_name):
        print(f"   ⏭️  Table {table_name} doesn't exist in SQLite, skipping...")
        return True, 0
    
    try:
        # Get column information from SQLite
        columns = get_table_columns(sqlite_conn, table_name)
        if not columns:
            print(f"   ⚠️  No columns found for {table_name}, skipping...")
            return True, 0
        
        col_names = [col[0] for col in columns]
        sqlite_col_types = {col[0]: col[1] for col in columns}
        
        # Get actual PostgreSQL column types (for boolean detection)
        pg_col_types = get_pg_column_types(pg_conn, table_name)
        
        # Fetch all data from SQLite
        sqlite_cursor = sqlite_conn.execute(f"SELECT * FROM {table_name}")
        rows = sqlite_cursor.fetchall()
        
        if not rows:
            print(f"   ℹ️  Table {table_name} is empty")
            return True, 0
        
        print(f"   📊 Found {len(rows)} rows")
        
        if dry_run:
            print(f"   🔍 DRY RUN: Would migrate {len(rows)} rows")
            return True, len(rows)
        
        # Apply column name mappings and filter
        table_mappings = COLUMN_MAPPINGS.get(table_name, {})
        sqlite_to_pg_map = {}  # Maps SQLite column index to PG column name
        pg_col_names = []  # Final PG column names for insert
        
        for i, sqlite_col_name in enumerate(col_names):
            # Check if column needs renaming
            pg_col_name = table_mappings.get(sqlite_col_name, sqlite_col_name)
            
            # Check if column exists in PostgreSQL
            if pg_col_name in pg_col_types:
                sqlite_to_pg_map[i] = pg_col_name
                pg_col_names.append(pg_col_name)
            elif sqlite_col_name != pg_col_name:
                # Renamed column doesn't exist
                print(f"   ⚠️  Mapped column {sqlite_col_name} → {pg_col_name} not found in PostgreSQL")
        
        if len(pg_col_names) != len(col_names):
            skipped = set(col_names) - set([table_mappings.get(c, c) for c in col_names if table_mappings.get(c, c) in pg_col_types])
            if skipped:
                print(f"   ℹ️  Skipping columns: {skipped}")
        
        # Prepare PostgreSQL insert
        placeholders = ','.join(['%s'] * len(pg_col_names))
        insert_sql = f"INSERT INTO {table_name} ({','.join(pg_col_names)}) VALUES ({placeholders})"
        
        # Convert and insert data
        pg_cursor = pg_conn.cursor()
        converted_rows = []
        
        for row in rows:
            converted_row = []
            # Only process columns that are being migrated (in sqlite_to_pg_map)
            for sqlite_idx, pg_col_name in sorted(sqlite_to_pg_map.items()):
                value = row[sqlite_idx] if sqlite_idx < len(row) else None
                sqlite_col_name = col_names[sqlite_idx]
                
                # Use PostgreSQL type if available, otherwise fallback to SQLite type
                if pg_col_name in pg_col_types and pg_col_types[pg_col_name] == 'boolean':
                    # Force boolean conversion
                    converted_val = convert_value(value, 'BOOLEAN')
                else:
                    converted_val = convert_value(value, sqlite_col_types.get(sqlite_col_name, 'TEXT'))
                
                converted_row.append(converted_val)
            
            converted_rows.append(tuple(converted_row))
        
        # Batch insert for performance
        execute_batch(pg_cursor, insert_sql, converted_rows, page_size=100)
        
        print(f"   ✅ Migrated {len(rows)} rows successfully")
        return True, len(rows)
        
    except Exception as e:
        print(f"   ❌ Error migrating {table_name}: {str(e)}")
        return False, 0

def strip_psql_meta(sql_text):
    """Remove psql meta commands (lines starting with backslash)."""
    lines = []
    for line in sql_text.splitlines(keepends=True):
        if line.lstrip().startswith('\\'):
            continue
        lines.append(line)
    return ''.join(lines)

DOLLAR_TAG_RE = re.compile(r'\$[A-Za-z0-9_]*\$')

def split_sql_statements(sql_text):
    """Split a SQL script into separate statements while respecting quotes."""
    statements = []
    statement_chars = []
    in_single = False
    in_double = False
    dollar_tag = None
    i = 0
    length = len(sql_text)

    while i < length:
        remaining = sql_text[i:]

        if not in_single and not in_double and not dollar_tag:
            if remaining.startswith('--'):
                newline = sql_text.find('\n', i)
                i = length if newline == -1 else newline + 1
                continue

            if remaining.startswith('/*'):
                end = sql_text.find('*/', i + 2)
                i = length if end == -1 else end + 2
                continue

        if dollar_tag:
            if remaining.startswith(dollar_tag):
                statement_chars.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            statement_chars.append(sql_text[i])
            i += 1
            continue
        else:
            match = DOLLAR_TAG_RE.match(sql_text, i)
            if match and not in_single and not in_double:
                tag = match.group(0)
                statement_chars.append(tag)
                dollar_tag = tag
                i += len(tag)
                continue

        ch = sql_text[i]

        if not in_single and not in_double and not dollar_tag and ch == ';':
            statement_chars.append(ch)
            statement = ''.join(statement_chars).strip()
            if statement:
                statements.append(statement)
            statement_chars = []
            i += 1
            continue

        if ch == "'" and not in_double and not dollar_tag:
            if in_single and i + 1 < length and sql_text[i + 1] == "'":
                statement_chars.append("''")
                i += 2
                continue
            statement_chars.append(ch)
            in_single = not in_single
            i += 1
            continue

        if ch == '"' and not in_single and not dollar_tag:
            if in_double and i + 1 < length and sql_text[i + 1] == '"':
                statement_chars.append('""')
                i += 2
                continue
            statement_chars.append(ch)
            in_double = not in_double
            i += 1
            continue

        statement_chars.append(ch)
        i += 1

    remaining = ''.join(statement_chars).strip()
    if remaining:
        statements.append(remaining)
    return statements

def load_schema_statements(schema_path):
    """Read the schema SQL file and split it into executable statements."""
    with open(schema_path, 'r', encoding='utf-8') as schema_file:
        raw_sql = schema_file.read()
    cleaned = strip_psql_meta(raw_sql)
    return split_sql_statements(cleaned)

def drop_public_schema(pg_conn):
    """Drop and recreate the public schema to remove existing objects."""
    cursor = pg_conn.cursor()
    cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
    cursor.execute("CREATE SCHEMA public")
    cursor.execute("GRANT USAGE ON SCHEMA public TO public")
    cursor.execute("GRANT CREATE ON SCHEMA public TO public")
    pg_conn.commit()

def apply_schema(pg_conn, schema_path):
    """Apply schema statements from the provided SQL file to the Postgres database."""
    if not os.path.exists(schema_path):
        print(f"   ℹ️  Schema file '{schema_path}' not found, skipping schema apply.")
        return

    statements = load_schema_statements(schema_path)
    if not statements:
        print(f"   ℹ️  Schema file '{schema_path}' contains no executable statements.")
        return

    print(f"\n📁 Applying schema file: {schema_path}")
    saved_autocommit = pg_conn.autocommit
    applied = 0
    skipped = 0

    try:
        pg_conn.autocommit = True
        cursor = pg_conn.cursor()
        for statement in statements:
            trimmed = statement.strip()
            if not trimmed:
                continue
            try:
                cursor.execute(trimmed)
                applied += 1
            except psycopg2.Error as exc:
                if exc.pgcode in DUPLICATE_PGCODES:
                    skipped += 1
                    print(f"   ℹ️  Skipped schema statement ({exc.pgcode}): {exc}")
                    continue
                raise
        # Ensure we can find the objects we just created by resetting the search path to public
        cursor.execute("SET search_path TO public, pg_catalog")
    finally:
        pg_conn.autocommit = saved_autocommit

    print(f"📐 Applied {applied} schema statements (skipped {skipped}).")

def fix_schema_issues(pg_conn):
    """Fix known schema issues before migration"""
    cursor = pg_conn.cursor()
    fixes_applied = []
    
    try:
        # Fix 1: Allow NULL salesperson_id in sales_orders
        cursor.execute("""
            SELECT is_nullable 
            FROM information_schema.columns 
            WHERE table_name = 'sales_orders' AND column_name = 'salesperson_id'
        """)
        result = cursor.fetchone()
        if result and result[0] == 'NO':
            cursor.execute("ALTER TABLE sales_orders ALTER COLUMN salesperson_id DROP NOT NULL")
            fixes_applied.append("sales_orders.salesperson_id now allows NULL")
        
        pg_conn.commit()
        
        if fixes_applied:
            print("\n🔧 Applied schema fixes:")
            for fix in fixes_applied:
                print(f"   ✅ {fix}")
        
    except Exception as e:
        print(f"   ⚠️  Schema fix warning: {e}")
        pg_conn.rollback()

def reset_sequences(pg_conn, table_name):
    """Reset PostgreSQL sequence for a table's ID column"""
    try:
        pg_cursor = pg_conn.cursor()
        
        # Try to find and reset the sequence
        pg_cursor.execute(f"""
            SELECT pg_get_serial_sequence('{table_name}', 'id') as seq_name
        """)
        result = pg_cursor.fetchone()
        
        if result and result[0]:
            seq_name = result[0]
            pg_cursor.execute(f"""
                SELECT setval('{seq_name}', 
                    COALESCE((SELECT MAX(id) FROM {table_name}), 1)
                )
            """)
            print(f"   🔄 Reset sequence for {table_name}")
            
    except Exception as e:
        # Not all tables have sequences, that's okay
        pass

def main():
    parser = argparse.ArgumentParser(description='Migrate data from SQLite to PostgreSQL')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be migrated without actually doing it')
    parser.add_argument('--table', help='Migrate only a specific table')
    parser.add_argument('--skip-existing', action='store_true', help='Skip tables that already have data')
    parser.add_argument('--force', action='store_true', help='Delete existing data before migrating')
    parser.add_argument('--schema-file', help=f"Schema SQL file to apply before migration (default: {DEFAULT_SCHEMA_FILE})")
    parser.add_argument('--skip-schema', action='store_true', help='Skip applying the schema SQL before migrating')
    parser.add_argument('--drop-schema', action='store_true', help='Drop and recreate the public schema before applying the schema SQL')
    args = parser.parse_args()
    
    print("="*60)
    print("PostgreSQL Data Migration Tool")
    print("="*60)
    
    if args.dry_run:
        print("🔍 DRY RUN MODE - No changes will be made")
    
    # Check environment
    if not POSTGRES_DSN:
        print("❌ ERROR: DATABASE_URL environment variable not set!")
        sys.exit(1)
    
    if not os.path.exists(SQLITE_DB):
        print(f"❌ ERROR: SQLite database '{SQLITE_DB}' not found!")
        sys.exit(1)
    
    # Connect to databases
    print(f"\n📂 Connecting to SQLite: {SQLITE_DB}")
    sqlite_conn = sqlite3.connect(SQLITE_DB)
    
    print(f"🐘 Connecting to PostgreSQL...")
    try:
        pg_conn = psycopg2.connect(POSTGRES_DSN)
        pg_conn.autocommit = False
    except Exception as e:
        print(f"❌ Failed to connect to PostgreSQL: {e}")
        sys.exit(1)
    
    schema_file = args.schema_file or DEFAULT_SCHEMA_FILE

    # Apply schema + additional fixes (unless dry run)
    if not args.dry_run:
        if args.drop_schema:
            print("\n🗑️  Dropping existing schema (public)")
            drop_public_schema(pg_conn)
        if not args.skip_schema:
            apply_schema(pg_conn, schema_file)
        fix_schema_issues(pg_conn)
    
    # Determine tables to migrate
    tables_to_migrate = [args.table] if args.table else MIGRATION_ORDER
    
    # Statistics
    stats = {
        'total_tables': 0,
        'successful_tables': 0,
        'failed_tables': 0,
        'total_rows': 0,
        'skipped_tables': 0
    }
    
    start_time = datetime.now()
    
    try:
        for table_name in tables_to_migrate:
            stats['total_tables'] += 1
            
            # Force delete existing data if requested
            if args.force and not args.dry_run:
                pg_cursor = pg_conn.cursor()
                # Check if table exists in PostgreSQL first
                pg_cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = %s
                    )
                """, (table_name,))
                table_exists_in_pg = pg_cursor.fetchone()[0]
                
                if table_exists_in_pg:
                    pg_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    count = pg_cursor.fetchone()[0]
                    if count > 0:
                        print(f"\n🗑️  Deleting {count} existing rows from {table_name}")
                        pg_cursor.execute(f"DELETE FROM {table_name}")
                        pg_conn.commit()
                else:
                    print(f"\n⏭️  Table {table_name} doesn't exist in PostgreSQL yet, skipping...")
                    stats['skipped_tables'] += 1
                    continue
            
            # Check if table already has data (if skip-existing flag is set)
            elif args.skip_existing and not args.dry_run:
                pg_cursor = pg_conn.cursor()
                pg_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = pg_cursor.fetchone()[0]
                if count > 0:
                    print(f"\n⏭️  Skipping {table_name} (already has {count} rows)")
                    stats['skipped_tables'] += 1
                    continue
            
            # Migrate the table
            success, row_count = migrate_table(sqlite_conn, pg_conn, table_name, args.dry_run)
            
            if success:
                stats['successful_tables'] += 1
                stats['total_rows'] += row_count
                
                # Reset sequence if not dry run
                if not args.dry_run and row_count > 0:
                    reset_sequences(pg_conn, table_name)
                
                # Commit after each table
                if not args.dry_run:
                    pg_conn.commit()
            else:
                stats['failed_tables'] += 1
                pg_conn.rollback()
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Migration interrupted by user")
        pg_conn.rollback()
    except Exception as e:
        print(f"\n\n❌ Migration failed: {e}")
        pg_conn.rollback()
    finally:
        # Close connections
        sqlite_conn.close()
        pg_conn.close()
    
    # Print summary
    duration = datetime.now() - start_time
    print("\n" + "="*60)
    print("Migration Summary")
    print("="*60)
    print(f"Duration: {duration}")
    print(f"Total tables processed: {stats['total_tables']}")
    print(f"✅ Successful: {stats['successful_tables']}")
    print(f"❌ Failed: {stats['failed_tables']}")
    print(f"⏭️  Skipped: {stats['skipped_tables']}")
    print(f"📊 Total rows migrated: {stats['total_rows']:,}")
    print("="*60)
    
    if args.dry_run:
        print("\n🔍 This was a DRY RUN - no data was actually migrated")
    elif stats['failed_tables'] == 0:
        print("\n🎉 Migration completed successfully!")
    else:
        print(f"\n⚠️  Migration completed with {stats['failed_tables']} failures")
        sys.exit(1)

if __name__ == '__main__':
    main()
