"""
Simplified Bulk Alternative Part Group Upload Script

Assumes:
1. All part numbers already exist in the database
2. Clears existing alternative groups before starting
3. Each CSV row = one new alternative group
"""

import csv
import sqlite3
import logging
from typing import List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_base_part_number(part_number: str) -> str:
    """Strip special characters from part number to create base part number"""
    if not part_number:
        return ''
    return ''.join(c for c in part_number if c.isalnum()).upper()


def get_db_connection(db_path: str = 'database.db'):
    """Create database connection with row factory"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def process_csv_row_simple(cursor, row_parts: List[str], row_number: int) -> dict:
    """
    Process a single CSV row - create a new group with all valid parts.
    
    Returns processing stats.
    """
    # Filter out empty values and create base part numbers
    base_parts = []
    
    for part in row_parts:
        part = part.strip()
        if part:
            base_pn = create_base_part_number(part)
            if base_pn:
                base_parts.append(base_pn)
    
    # Remove duplicates while preserving order
    seen = set()
    unique_base = []
    for base in base_parts:
        if base not in seen:
            seen.add(base)
            unique_base.append(base)
    
    if len(unique_base) < 2:
        logger.warning(f"Row {row_number}: Less than 2 valid parts, skipping")
        return {'group_created': False, 'parts_added': 0}
    
    # Create new group
    description = f"Alternative group from CSV row {row_number}"
    cursor.execute('INSERT INTO part_alt_groups (description) VALUES (?)', (description,))
    group_id = cursor.lastrowid
    
    # Add all parts to the group
    parts_added = 0
    for base_pn in unique_base:
        try:
            cursor.execute(
                'INSERT INTO part_alt_group_members (group_id, base_part_number) VALUES (?, ?)',
                (group_id, base_pn)
            )
            parts_added += 1
        except sqlite3.IntegrityError as e:
            logger.warning(f"Row {row_number}: Could not add part {base_pn} - {e}")
    
    logger.info(f"Row {row_number}: Created group {group_id} with {parts_added} parts")
    
    return {
        'group_created': True,
        'group_id': group_id,
        'parts_added': parts_added
    }


def clear_existing_alternatives(cursor):
    """Clear all existing alternative groups and members"""
    logger.info("Clearing existing alternative groups and members...")
    cursor.execute('DELETE FROM part_alt_group_members')
    cursor.execute('DELETE FROM part_alt_groups')
    logger.info("Existing alternatives cleared")


def process_csv_file_simple(csv_path: str, db_path: str = 'database.db', batch_size: int = 100):
    """
    Process entire CSV file - each row becomes a new alternative group.

    Args:
        csv_path: Path to CSV file
        db_path: Path to SQLite database
        batch_size: Number of rows to process before committing
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Clear existing alternatives first
    clear_existing_alternatives(cursor)
    conn.commit()

    # Statistics
    stats = {
        'rows_processed': 0,
        'groups_created': 0,
        'parts_added': 0,
        'rows_skipped': 0
    }
    
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:  # utf-8-sig to handle BOM
            reader = csv.reader(f)
            
            # Skip header row
            header = next(reader)
            logger.info(f"CSV Headers: {header}")
            
            row_number = 1
            for row in reader:
                row_number += 1
                
                try:
                    result = process_csv_row_simple(cursor, row, row_number)
                    
                    stats['rows_processed'] += 1
                    
                    if result['group_created']:
                        stats['groups_created'] += 1
                        stats['parts_added'] += result['parts_added']
                    else:
                        stats['rows_skipped'] += 1
                    
                    # Commit in batches
                    if row_number % batch_size == 0:
                        conn.commit()
                        logger.info(f"Committed batch at row {row_number}")
                        logger.info(f"Progress: {stats}")
                
                except Exception as e:
                    logger.error(f"Error processing row {row_number}: {e}")
                    logger.error(f"Row data: {row}")
                    # Continue processing other rows
            
            # Final commit
            conn.commit()
            logger.info("Final commit completed")
        
        # Print final statistics
        logger.info("=" * 60)
        logger.info("PROCESSING COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Rows processed: {stats['rows_processed']}")
        logger.info(f"Rows skipped: {stats['rows_skipped']}")
        logger.info(f"Groups created: {stats['groups_created']}")
        logger.info(f"Total part-group associations added: {stats['parts_added']}")
        logger.info("=" * 60)
        
        return stats
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        conn.rollback()
        raise
    
    finally:
        conn.close()


def verify_results(db_path: str = 'database.db'):
    """Quick verification of results"""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Count groups
    group_count = cursor.execute('SELECT COUNT(*) as cnt FROM part_alt_groups').fetchone()['cnt']
    
    # Count members
    member_count = cursor.execute('SELECT COUNT(*) as cnt FROM part_alt_group_members').fetchone()['cnt']
    
    # Find largest group
    largest_group = cursor.execute('''
        SELECT group_id, COUNT(*) as member_count 
        FROM part_alt_group_members 
        GROUP BY group_id 
        ORDER BY member_count DESC 
        LIMIT 1
    ''').fetchone()
    
    # Find smallest group
    smallest_group = cursor.execute('''
        SELECT group_id, COUNT(*) as member_count 
        FROM part_alt_group_members 
        GROUP BY group_id 
        ORDER BY member_count ASC 
        LIMIT 1
    ''').fetchone()
    
    # Average group size
    avg_size = cursor.execute('''
        SELECT AVG(member_count) as avg_size
        FROM (
            SELECT COUNT(*) as member_count 
            FROM part_alt_group_members 
            GROUP BY group_id
        )
    ''').fetchone()
    
    conn.close()
    
    logger.info("=" * 60)
    logger.info("VERIFICATION")
    logger.info("=" * 60)
    logger.info(f"Total alternative groups: {group_count}")
    logger.info(f"Total group memberships: {member_count}")
    if largest_group:
        logger.info(f"Largest group: {largest_group['member_count']} members (group_id: {largest_group['group_id']})")
    if smallest_group:
        logger.info(f"Smallest group: {smallest_group['member_count']} members (group_id: {smallest_group['group_id']})")
    if avg_size:
        logger.info(f"Average group size: {avg_size['avg_size']:.1f} members")
    logger.info("=" * 60)


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python bulk_alt_upload_simple.py <csv_file_path> [db_path]")
        sys.exit(1)
    
    csv_file = sys.argv[1]
    db_file = sys.argv[2] if len(sys.argv) > 2 else 'database.db'
    
    logger.info(f"Starting simplified bulk upload from {csv_file}")
    logger.info(f"Database: {db_file}")
    logger.info("NOTE: This will CLEAR existing alternatives and create NEW groups for each CSV row")
    
    # Process the file
    stats = process_csv_file_simple(csv_file, db_file)
    
    # Verify results
    verify_results(db_file)
