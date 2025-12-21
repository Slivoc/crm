"""Direct migration of part_numbers with debugging"""
import sqlite3
import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
import os

load_dotenv()

print("Direct Part Numbers Migration")
print("=" * 60)

# Connect
sqlite_conn = sqlite3.connect('database.db')
pg_conn = psycopg2.connect(os.getenv('DATABASE_URL'))
pg_conn.autocommit = False

# Get SQLite data
print("📂 Reading from SQLite...")
sqlite_cursor = sqlite_conn.cursor()
sqlite_cursor.execute("""
    SELECT base_part_number, part_number, system_part_number, 
           created_at, stock, datecode, target_price, SPQ, 
           packaging, rohs, category_id, mkp_category
    FROM part_numbers
""")
rows = sqlite_cursor.fetchall()
print(f"   Found {len(rows):,} rows")

# Show sample
print("\nSample data:")
for row in rows[:3]:
    print(f"   {row[0]} | {row[1]} | SPQ={row[7]}")

# Prepare PostgreSQL insert
print("\n🐘 Inserting into PostgreSQL...")
pg_cursor = pg_conn.cursor()

# Check current count
pg_cursor.execute("SELECT COUNT(*) FROM part_numbers")
before_count = pg_cursor.fetchone()[0]
print(f"   Before: {before_count} rows")

try:
    # Convert rohs booleans
    converted_rows = []
    for row in rows:
        row_list = list(row)
        # Convert rohs (index 9) from 0/1 to boolean
        if row_list[9] is not None:
            row_list[9] = bool(row_list[9])
        converted_rows.append(tuple(row_list))
    
    # Insert in batches
    insert_sql = """
        INSERT INTO part_numbers (
            base_part_number, part_number, system_part_number,
            created_at, stock, datecode, target_price, spq,
            packaging, rohs, category_id, mkp_category
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    print(f"   Inserting {len(converted_rows):,} rows...")
    execute_batch(pg_cursor, insert_sql, converted_rows, page_size=1000)
    
    # Check count before commit
    pg_cursor.execute("SELECT COUNT(*) FROM part_numbers")
    after_count = pg_cursor.fetchone()[0]
    print(f"   After insert (before commit): {after_count} rows")
    
    # Commit
    print("   Committing transaction...")
    pg_conn.commit()
    
    # Verify after commit
    pg_cursor.execute("SELECT COUNT(*) FROM part_numbers")
    final_count = pg_cursor.fetchone()[0]
    print(f"   After commit: {final_count} rows")
    
    # Show sample
    pg_cursor.execute("SELECT base_part_number, part_number, spq FROM part_numbers LIMIT 3")
    print("\n   PostgreSQL samples:")
    for row in pg_cursor.fetchall():
        print(f"      {row[0]} | {row[1]} | spq={row[2]}")
    
    print("\n✅ Migration completed successfully!")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    print(f"   Error type: {type(e).__name__}")
    import traceback
    traceback.print_exc()
    pg_conn.rollback()
finally:
    sqlite_conn.close()
    pg_conn.close()
