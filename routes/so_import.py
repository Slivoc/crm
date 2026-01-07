from flask import Blueprint, render_template, request, jsonify
from db import execute as db_execute, db_cursor, _using_postgres
import pandas as pd
import os
from datetime import datetime

so_import_bp = Blueprint('so_import', __name__, url_prefix='/so-import')


# -----------------------------
# Postgres compatibility helpers
# -----------------------------
def _execute_with_cursor(cursor, query, params=None):
    """
    Execute a query with automatic placeholder translation for Postgres.
    Translates '?' to '%s' when using Postgres.
    """
    if params is None:
        params = []
    
    if _using_postgres():
        # Translate '?' to '%s' for psycopg2
        query = query.replace('?', '%s')
    
    cursor.execute(query, params)
    return cursor


def create_base_part_number(part_number):
    """Create a base part number by removing common suffixes and normalizing"""
    if not part_number:
        return None

    # Convert to uppercase and strip whitespace
    base = str(part_number).upper().strip()

    # Remove common suffixes that don't change the base part
    # (adjust these based on your part numbering system)
    suffixes_to_remove = ['-OH', '-SV', '-RP', '-NS', '-AR', '-TR']
    for suffix in suffixes_to_remove:
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break

    return base


def create_part_on_demand(cursor, system_part_number, part_number=None):
    """
    Create a part on-demand during import, handling cases where base_part_number already exists.
    If base_part_number exists, reuse it and just add the new system_part_number.
    Returns the base_part_number.
    """
    if part_number is None:
        part_number = system_part_number

    base_part_number = create_base_part_number(system_part_number)

    # Check if base_part_number already exists
    _execute_with_cursor(cursor, """
        SELECT base_part_number FROM part_numbers 
        WHERE base_part_number = ? 
        LIMIT 1
    """, (base_part_number,))
    existing = cursor.fetchone()

    if existing:
        # Base part exists, just add this system_part_number as a variant
        print(f"  Base part '{base_part_number}' exists, adding system part '{system_part_number}'")
        try:
            _execute_with_cursor(cursor, """
                INSERT INTO part_numbers (base_part_number, part_number, system_part_number)
                VALUES (?, ?, ?)
            """, (base_part_number, part_number, system_part_number))
        except Exception as e:
            # System part number already exists, just return the base
            if "UNIQUE constraint" in str(e) or "duplicate key" in str(e).lower():
                print(f"  System part '{system_part_number}' already exists")
                pass
            else:
                raise
    else:
        # New base part, create it
        _execute_with_cursor(cursor, """
            INSERT INTO part_numbers (base_part_number, part_number, system_part_number)
            VALUES (?, ?, ?)
        """, (base_part_number, part_number, system_part_number))
        print(f"  Created new base part '{base_part_number}'")

    return base_part_number


def validate_date(date_value):
    """Validate and convert various date formats to YYYY-MM-DD"""
    if pd.isna(date_value):
        return None

    try:
        # If it's already a datetime object
        if isinstance(date_value, datetime):
            return date_value.strftime('%Y-%m-%d')

        # Try parsing as string
        date_str = str(date_value).strip()
        if not date_str or date_str.lower() == 'nan':
            return None

        # Try common date formats
        for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y']:
            try:
                parsed_date = datetime.strptime(date_str, fmt)
                return parsed_date.strftime('%Y-%m-%d')
            except ValueError:
                continue

        # If all else fails, return today's date
        return datetime.now().strftime('%Y-%m-%d')
    except Exception as e:
        print(f"Error validating date '{date_value}': {e}")
        return datetime.now().strftime('%Y-%m-%d')


@so_import_bp.route('/')
def import_page():
    """Render the sales order import page"""
    return render_template('so_import.html')


@so_import_bp.route('/upload', methods=['POST'])
def upload_so_file():
    """Handle sales order CSV upload and processing"""
    if 'file' not in request.files:
        return jsonify(success=False, message="No file provided"), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify(success=False, message="No file selected"), 400

    if not file.filename.endswith('.csv'):
        return jsonify(success=False, message="Only CSV files are supported"), 400

    try:
        # Save file temporarily
        import tempfile
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.csv') as temp_file:
            file.save(temp_file.name)
            temp_path = temp_file.name

        # Read CSV
        df = pd.read_csv(temp_path, encoding='utf-8-sig')
        print(f"Read CSV: {len(df)} rows, {len(df.columns)} columns")
        print(f"Columns: {df.columns.tolist()}")

        # Process the data
        results = process_so_csv(df)

        # Clean up
        os.unlink(temp_path)

        return jsonify(success=True, results=results)

    except Exception as e:
        print(f"Error processing file: {e}")
        import traceback
        traceback.print_exc()
        return jsonify(success=False, message=str(e)), 500


def process_so_csv(df):
    """
    Process sales order CSV with hardcoded column mappings
    Expected columns from ERP export:
    0: transactionHeaderId
    1: transactionItemId
    2: itemNumber (line number)
    3: signal
    4: transactionalNumber (SO number)
    5: status
    6: companyId (customer code)
    7: companyName (customer name)
    8: partId
    9: partNumber
    10: description
    11: conditionCode
    12: quantityOrdered
    13: quantityShipped
    14: quantityInvoiced
    15: quantityAllocated
    16: unitsOfMeasure
    17: tranType
    18: unitCost
    19: unitPrice
    20: totalCost
    21: totalPrice
    22: foreignPrice
    23: totalForeignPrice
    24: tax
    25: leadDays
    26: createdBy
    27: salesPerson
    28: coreCharge
    29: coreDueDate
    30: coreReturnedDate
    31: traceability
    32: baseCurrency
    33: foreignCurrency
    34: entryDate
    35: isCharge
    36: qtyBoLinked
    37: dueDate
    38: unit
    39: subUnit
    """
    results = {
        'parts': {'processed': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': []},
        'customers': {'processed': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': []},
        'sales_orders': {'processed': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': []},
        'order_lines': {'processed': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': []}
    }

    print("Starting sales order import...")
    print(f"Total rows to process: {len(df)}")

    # Step 1: Process parts
    print("\n" + "=" * 60)
    print("STEP 1: Processing Parts")
    print("=" * 60)
    process_parts(df, results['parts'])

    # Step 2: Process customers
    print("\n" + "=" * 60)
    print("STEP 2: Processing Customers")
    print("=" * 60)
    process_customers(df, results['customers'])

    # Step 3: Process sales orders
    print("\n" + "=" * 60)
    print("STEP 3: Processing Sales Orders")
    print("=" * 60)
    process_sales_orders(df, results['sales_orders'])

    # Step 4: Process order lines
    print("\n" + "=" * 60)
    print("STEP 4: Processing Order Lines")
    print("=" * 60)
    process_order_lines(df, results['order_lines'])

    # Step 5: Calculate missing totals
    print("\n" + "=" * 60)
    print("STEP 5: Calculating Order Totals")
    print("=" * 60)
    calculate_order_totals(results)

    print("\n" + "=" * 60)
    print("IMPORT COMPLETE")
    print("=" * 60)
    print_summary(results)

    return results


def process_parts(df, results):
    """Process unique parts from the CSV"""
    with db_cursor(commit=True) as cursor:
        # Column 9: partNumber (use as both part_number and system_part_number)
        unique_parts = df[df.iloc[:, 9].notna()].iloc[:, 9].unique()

        print(f"Found {len(unique_parts)} unique parts")

        # Get existing parts
        _execute_with_cursor(cursor,"SELECT system_part_number, base_part_number FROM part_numbers")
        existing_parts = {row['system_part_number']: row['base_part_number'] for row in cursor.fetchall()}

        for part_number in unique_parts:
            part_number = str(part_number).strip()
            if not part_number or part_number.lower() == 'nan':
                continue

            results['processed'] += 1

            try:
                if part_number in existing_parts:
                    results['skipped'] += 1
                    continue

                # Use create_part_on_demand which handles base_part_number collisions
                base_part_number = create_part_on_demand(cursor, part_number, part_number)
                existing_parts[part_number] = base_part_number
                results['created'] += 1
                print(f"Created part: {part_number} -> {base_part_number}")

            except Exception as e:
                results['errors'].append(f"Part {part_number}: {str(e)}")
                print(f"Error creating part {part_number}: {e}")
                import traceback
                traceback.print_exc()

        print(f"Parts: Created={results['created']}, Skipped={results['skipped']}, Errors={len(results['errors'])}")


def process_customers(df, results):
    """Process unique customers from the CSV, using salesperson from the first associated order line"""
    with db_cursor(commit=True) as cursor:
        # Get unique customers with their first associated salesperson (columns 6: companyId, 7: companyName, 27: salesPerson)
        unique_customers = df[[df.columns[6], df.columns[7], df.columns[27]]].drop_duplicates(subset=[df.columns[6]])

        print(f"Found {len(unique_customers)} unique customers")

        # Get salesperson mapping
        _execute_with_cursor(cursor,"SELECT id, name FROM salespeople")
        salespeople = {row['name']: row['id'] for row in cursor.fetchall()}

        for idx, row in unique_customers.iterrows():
            customer_code = str(row.iloc[0]).strip()
            customer_name = str(row.iloc[1]).strip()
            salesperson_name = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else None

            if not customer_code or customer_code.lower() == 'nan':
                continue

            results['processed'] += 1

            try:
                # Resolve salesperson ID from the order line's salesperson
                sp_id = 1  # Default fallback
                if salesperson_name and salesperson_name in salespeople:
                    sp_id = salespeople[salesperson_name]

                _execute_with_cursor(cursor,"""
                    SELECT id FROM customers WHERE system_code = ?
                """, (customer_code,))
                existing = cursor.fetchone()

                if existing:
                    # Skip existing customers entirely - don't update anything
                    results['skipped'] += 1
                else:
                    # Create new customer with order's salesperson
                    _execute_with_cursor(cursor,"""
                        INSERT INTO customers (system_code, name, salesperson_id, status_id)
                        VALUES (?, ?, ?, 3)
                    """, (customer_code, customer_name, sp_id))
                    results['created'] += 1
                    print(f"Created customer: {customer_code} - {customer_name} (SP: {salesperson_name or 'Default'})")

            except Exception as e:
                results['errors'].append(f"Customer {customer_code}: {str(e)}")
                print(f"Error processing customer {customer_code}: {e}")

        print(f"Customers: Created={results['created']}, Skipped={results['skipped']}")


def process_sales_orders(df, results):
    """Process unique sales orders from the CSV"""
    with db_cursor(commit=True) as db:
        # Get unique orders (column 4: transactionalNumber = SO number)
        unique_orders = df.drop_duplicates(subset=[df.columns[4]])

        print(f"Found {len(unique_orders)} unique sales orders")

        cursor = db

        # Get salesperson mapping (using name match since CSV has full names)
        _execute_with_cursor(cursor, "SELECT id, name FROM salespeople")
        salespeople = {row['name']: row['id'] for row in cursor.fetchall()}

        for idx, row in unique_orders.iterrows():
            so_ref = str(row.iloc[4]).strip()  # transactionalNumber
            customer_code = str(row.iloc[6]).strip()  # companyId
            customer_po = None  # Not in this CSV format
            order_date = row.iloc[34]  # entryDate
            salesperson_name = str(row.iloc[27]).strip() if pd.notna(row.iloc[27]) else None  # salesPerson

            if not so_ref or so_ref.lower() == 'nan':
                continue

            results['processed'] += 1

            try:
                # Check if order exists
                _execute_with_cursor(cursor,"""
                    SELECT id FROM sales_orders WHERE sales_order_ref = ?
                """, (so_ref,))
                existing = cursor.fetchone()

                if existing:
                    results['skipped'] += 1
                    continue

                # Get customer (created in previous step if new)
                _execute_with_cursor(cursor,"""
                    SELECT id, currency_id FROM customers WHERE system_code = ?
                """, (customer_code,))
                customer = cursor.fetchone()

                if not customer:
                    results['errors'].append(f"Order {so_ref}: Customer {customer_code} not found")
                    continue

                # Get salesperson ID for the order
                salesperson_id = 1
                if salesperson_name and salesperson_name in salespeople:
                    salesperson_id = salespeople[salesperson_name]

                # Note: Customer's salesperson was already set/updated in process_customers using data from orders

                # Parse date
                date_entered = validate_date(order_date) if pd.notna(order_date) else datetime.now().strftime(
                    '%Y-%m-%d')

                # Create sales order
                _execute_with_cursor(cursor,"""
                    INSERT INTO sales_orders (
                        sales_order_ref, customer_id, customer_po_ref,
                        date_entered, currency_id, sales_status_id, salesperson_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (so_ref, customer['id'], customer_po, date_entered,
                      customer['currency_id'] or 1, 1, salesperson_id))

                results['created'] += 1
                print(f"Created order: {so_ref}")

            except Exception as e:
                results['errors'].append(f"Order {so_ref}: {str(e)}")
                print(f"Error processing order {so_ref}: {e}")

        # Auto-committed by db_cursor
        print(f"Sales Orders: Created={results['created']}, Skipped={results['skipped']}")


def process_order_lines(df, results):
    """Process order lines from the CSV with extensive debugging"""
    def _parse_quantity(value):
        if pd.isna(value):
            raise ValueError("Quantity is missing")
        try:
            qty_value = float(str(value).strip())
        except (ValueError, TypeError):
            raise ValueError(f"Invalid quantity '{value}'")
        if pd.isna(qty_value):
            raise ValueError("Quantity is missing")
        return int(round(qty_value))

    def _parse_price(value):
        if pd.isna(value):
            return 0.0
        try:
            price_value = float(str(value).strip())
        except (ValueError, TypeError):
            return 0.0
        if pd.isna(price_value):
            return 0.0
        return price_value

    with db_cursor(commit=True) as db:
        cursor = db

        # Pre-fetch lookups
        _execute_with_cursor(cursor,"SELECT id, sales_order_ref FROM sales_orders")
        orders = {row['sales_order_ref']: row['id'] for row in cursor.fetchall()}

        _execute_with_cursor(cursor,"SELECT system_part_number, base_part_number FROM part_numbers")
        parts = {row['system_part_number']: row['base_part_number'] for row in cursor.fetchall()}

        print(f"Processing {len(df)} order lines")
        print(f"Loaded {len(orders)} orders, {len(parts)} parts")
        print(f"\nFirst 5 SO refs in lookup: {list(orders.keys())[:5]}")

        for idx, row in df.iterrows():
            # Extract raw values first for debugging
            so_ref_raw = row.iloc[4]
            line_no_raw = row.iloc[2]
            part_number_raw = row.iloc[9]
            quantity_raw = row.iloc[12]
            unit_price_raw = row.iloc[19]

            so_ref = str(so_ref_raw).strip()  # transactionalNumber
            line_no = line_no_raw  # itemNumber
            part_number = str(part_number_raw).strip()  # partNumber
            quantity = quantity_raw  # quantityOrdered
            unit_price = unit_price_raw  # unitPrice

            print(f"\n--- DataFrame Row {idx} (Excel row {idx + 2}) ---")
            print(f"  SO Ref: '{so_ref}' (raw: {so_ref_raw})")
            print(f"  Line: {line_no} (raw: {line_no_raw})")
            print(f"  Part: '{part_number}' (raw: {part_number_raw})")
            print(f"  Qty: {quantity} (raw: {quantity_raw})")
            print(f"  Price: {unit_price} (raw: {unit_price_raw})")

            if not so_ref or so_ref.lower() == 'nan':
                print(f"  ❌ SKIPPED: Invalid SO ref")
                results['skipped'] += 1
                continue

            results['processed'] += 1

            try:
                # Validate data
                line_number = int(float(line_no))
                qty = _parse_quantity(quantity)
                price = _parse_price(unit_price)

                print(f"  Parsed: Line#{line_number}, Qty={qty}, Price={price}")

                # Get order and part
                order_id = orders.get(so_ref)
                base_part = parts.get(part_number)

                if not order_id:
                    print(f"  ❌ ERROR: Order '{so_ref}' not found in orders dict")
                    print(f"  Available orders: {list(orders.keys())[:10]}")
                    results['errors'].append(
                        f"Row {idx} (Excel row {idx + 2}, Line {line_number}): Order {so_ref} not found")
                    results['skipped'] += 1
                    continue

                print(f"  ✓ Found order_id: {order_id}")

                if not base_part:
                    # Try to create part on-demand
                    print(f"  ⚠️  Creating missing part: {part_number}")
                    try:
                        base_part = create_part_on_demand(cursor, part_number, part_number)
                        parts[part_number] = base_part
                        print(f"  ✓ Created/found base_part: {base_part}")
                    except Exception as e:
                        print(f"  ❌ ERROR creating part: {e}")
                        import traceback
                        traceback.print_exc()
                        results['errors'].append(
                            f"Row {idx} (Excel row {idx + 2}): Could not create part '{part_number}'")
                        results['skipped'] += 1
                        continue
                else:
                    print(f"  ✓ Found base_part: {base_part}")

                # Check if line exists
                _execute_with_cursor(cursor,"""
                    SELECT id FROM sales_order_lines 
                    WHERE sales_order_id = ? AND line_number = ?
                """, (order_id, line_number))
                existing = cursor.fetchone()

                if existing:
                    existing_id = existing['id'] if isinstance(existing, dict) else existing[0]
                    print(f"  ⏭️  SKIPPED: Line already exists (id={existing_id})")
                    results['skipped'] += 1
                    continue

                # Create line
                _execute_with_cursor(cursor,"""
                    INSERT INTO sales_order_lines (
                        sales_order_id, line_number, base_part_number,
                        quantity, price, sales_status_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (order_id, line_number, base_part, qty, price, 1))

                print(f"  ✅ CREATED: Line {line_number} for order {so_ref}")
                results['created'] += 1

            except Exception as e:
                error_msg = f"Row {idx} (Excel row {idx + 2}, Line {line_no}): {str(e)}"
                results['errors'].append(error_msg)
                print(f"  ❌ ERROR: {e}")
                import traceback
                traceback.print_exc()

        # Auto-committed by db_cursor
        print(f"\n{'=' * 60}")
        print(
            f"Order Lines: Created={results['created']}, Skipped={results['skipped']}, Errors={len(results['errors'])}")

        if results['errors']:
            print(f"\nERRORS DETAIL:")
            for error in results['errors']:
                print(f"  - {error}")


def calculate_order_totals(results):
    """Calculate total_value for sales orders"""
    with db_cursor(commit=True) as db:
        cursor = db
        _execute_with_cursor(cursor, """
            SELECT DISTINCT so.id, so.sales_order_ref
            FROM sales_orders so
            INNER JOIN sales_order_lines sol ON so.id = sol.sales_order_id
            WHERE (so.total_value IS NULL OR so.total_value = 0)
            AND sol.price IS NOT NULL
        """)
        orders_needing_totals = cursor.fetchall()

        calculated = 0
        for order in orders_needing_totals:
            order_id = order['id'] if isinstance(order, dict) else order[0]
            _execute_with_cursor(cursor, """
                SELECT COALESCE(SUM(quantity * COALESCE(price, 0)), 0) as total
                FROM sales_order_lines 
                WHERE sales_order_id = ? AND price IS NOT NULL
            """, (order_id,))
            total = cursor.fetchone()

            if total:
                total_value = total['total'] if isinstance(total, dict) else total[0]
                if total_value > 0:
                    _execute_with_cursor(cursor, """
                        UPDATE sales_orders SET total_value = ? WHERE id = ?
                    """, (total_value, order_id))
                    calculated += 1

        if calculated > 0:
            # Auto-committed by db_cursor
            print(f"Calculated totals for {calculated} orders")
            results['sales_orders']['totals_calculated'] = calculated


def print_summary(results):
    """Print import summary"""
    print("\nIMPORT SUMMARY:")
    for step, data in results.items():
        print(f"\n{step.upper()}:")
        print(f"  Processed: {data['processed']}")
        print(f"  Created: {data['created']}")
        if data['updated'] > 0:
            print(f"  Updated: {data['updated']}")
        print(f"  Skipped: {data['skipped']}")
        if data['errors']:
            print(f"  Errors: {len(data['errors'])}")
            for err in data['errors'][:5]:
                print(f"    - {err}")
            if len(data['errors']) > 5:
                print(f"    ... and {len(data['errors']) - 5} more")
