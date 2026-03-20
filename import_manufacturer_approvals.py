"""
Import Airbus manufacturer/location approvals from one or more large XLSX files.

Examples:
    python import_manufacturer_approvals.py fixed1.xlsx fixed2.xlsx --list-type airbus_fixed_wing
    python import_manufacturer_approvals.py rotary.xlsx --list-type airbus_rotary
"""
import argparse
import logging
import os

from dotenv import load_dotenv
import psycopg2

from manufacturer_approval_importer import LIST_TYPES, process_workbooks

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Load Airbus manufacturer approvals into PostgreSQL.'
    )
    parser.add_argument(
        'xlsx_paths',
        nargs='+',
        help='One or more Airbus approvals XLSX/XLSM exports.',
    )
    parser.add_argument(
        '--list-type',
        choices=sorted(LIST_TYPES.keys()),
        default='airbus_fixed_wing',
        help='Approval list bucket to overwrite (default: airbus_fixed_wing).',
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=2000,
        help='Number of rows to upsert per batch (default: 2000).',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Limit number of data rows per workbook for smoke runs.',
    )
    parser.add_argument(
        '--append',
        action='store_true',
        help='Append into the selected list type instead of overwriting the existing dataset.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run the parser and SQL without committing changes.',
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    load_dotenv()
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL is required to import manufacturer approvals.')

    connection = psycopg2.connect(database_url)
    try:
        stats = process_workbooks(
            connection,
            workbook_paths=args.xlsx_paths,
            approval_list_type=args.list_type,
            batch_size=args.batch_size,
            limit=args.limit,
            overwrite_existing=not args.append,
            imported_by=os.getenv('USER') or 'cli',
            dry_run=args.dry_run,
        )
        logger.info(
            'Import %s complete for %s: files=%s rows_seen=%s rows_written=%s rows_skipped=%s deleted_previous_rows=%s',
            'dry-run' if args.dry_run else 'commit',
            args.list_type,
            stats['files_processed'],
            stats['rows_seen'],
            stats['rows_written'],
            stats['rows_skipped'],
            stats['deleted_previous_rows'],
        )
    finally:
        connection.close()


if __name__ == '__main__':
    main()
