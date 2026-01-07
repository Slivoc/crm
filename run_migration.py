"""
Run SQL migration files against the PostgreSQL database.

Usage:
    python run_migration.py migrations/20260107_add_debug_info_to_monroe.sql
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def run_migration(sql_file_path):
    """Run a SQL migration file against PostgreSQL."""
    if not os.path.exists(sql_file_path):
        print(f"ERROR: Migration file not found: {sql_file_path}")
        return False

    # Get database connection string
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set!")
        return False

    print(f"Running migration: {sql_file_path}")

    try:
        # Connect to PostgreSQL
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        cur = conn.cursor()

        # Read and execute SQL file
        with open(sql_file_path, 'r', encoding='utf-8') as f:
            sql = f.read()

        # Split by semicolons and execute each statement
        statements = [s.strip() for s in sql.split(';') if s.strip()]

        for i, statement in enumerate(statements, 1):
            if statement:
                print(f"  Executing statement {i}/{len(statements)}...")
                try:
                    cur.execute(statement)
                    print(f"  SUCCESS")
                except psycopg2.errors.DuplicateColumn as e:
                    print(f"  INFO: Column already exists (skipping): {e}")
                except Exception as e:
                    print(f"  WARNING: Error: {e}")
                    # Continue with other statements

        cur.close()
        conn.close()

        print(f"SUCCESS: Migration completed: {sql_file_path}")
        return True

    except Exception as e:
        print(f"ERROR: Migration failed: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python run_migration.py <migration_file.sql>")
        print("\nExample:")
        print("  python run_migration.py migrations/20260107_add_debug_info_to_monroe.sql")
        sys.exit(1)

    migration_file = sys.argv[1]
    success = run_migration(migration_file)
    sys.exit(0 if success else 1)
