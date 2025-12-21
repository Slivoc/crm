from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime, timedelta
import csv
import io
import os
import traceback

from db import db_cursor
from models import (get_purchase_orders, get_purchase_order_by_id, insert_purchase_order, get_purchase_orders_count, get_purchase_orders_total_value,
                    update_purchase_order, get_purchase_order_lines, insert_purchase_order_line,
                    update_purchase_order_line, delete_purchase_order_line, get_suppliers, insert_purchase_order_line_from_suggestion,
                    get_all_sales_statuses, get_currencies, get_next_purchase_order_ref, update_sales_order_line_status,
                    get_open_sales_order_lines, get_purchase_order_id_from_line, update_purchase_order_line_field, update_sales_order_line_ship_date, get_purchase_suggestions)

purchase_orders_bp = Blueprint('purchase_orders', __name__)


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _insert_returning_id(cur, base_query, params=None):
    query = base_query
    if _using_postgres() and 'RETURNING' not in base_query.upper():
        query = f"{base_query} RETURNING id"

    cur = _execute_with_cursor(cur, query, params or [])
    if _using_postgres():
        row = cur.fetchone()
        return row['id'] if row else None
    return cur.lastrowid

@purchase_orders_bp.route('/new', methods=['GET', 'POST'])
def create_purchase_order():
    if request.method == 'POST':
        supplier_id = request.form['supplier_id']
        purchase_order_id = insert_purchase_order(supplier_id)
        flash('Purchase order created successfully.', 'success')
        return redirect(url_for('purchase_orders.edit_purchase_order', purchase_order_id=purchase_order_id))

    suppliers = get_suppliers()
    return render_template('new_purchase_order.html', suppliers=suppliers)


@purchase_orders_bp.route('/edit/<int:purchase_order_id>', methods=['GET', 'POST'])
def edit_purchase_order(purchase_order_id):
    # Fetch the purchase order and its lines
    purchase_order = get_purchase_order_by_id(purchase_order_id)
    purchase_order_lines = get_purchase_order_lines(purchase_order_id)

    # Fetch suppliers
    suppliers = get_suppliers()

    # Fetch sales statuses
    statuses = get_all_sales_statuses()

    # Initialize open sales order lines
    open_sales_order_lines = {}

    # Populate open sales order lines for each base_part_number in the purchase order lines
    for line in purchase_order_lines:
        base_part_number = line['base_part_number']
        open_sales_order_lines[base_part_number] = get_open_sales_order_lines(base_part_number)

    # Pass everything to the template
    return render_template(
        'purchase_order_edit.html',
        purchase_order=purchase_order,
        purchase_order_lines=purchase_order_lines,
        suppliers=suppliers,
        statuses=statuses,
        open_sales_order_lines=open_sales_order_lines  # Pass the variable to the template
    )


def add_purchase_order_line(purchase_order_id):
    line_number = request.form['line_number']
    base_part_number = request.form['base_part_number']
    quantity = request.form['quantity']
    price = request.form['price']
    ship_date = request.form['ship_date']
    promised_date = request.form['promised_date']

    insert_purchase_order_line(purchase_order_id, line_number, base_part_number, quantity, price, ship_date, promised_date)
    flash('Purchase order line added successfully.', 'success')
    return redirect(url_for('purchase_orders.edit_purchase_order', purchase_order_id=purchase_order_id))



@purchase_orders_bp.route('/lines/<int:line_id>/delete', methods=['POST'])
def delete_line(line_id):
    delete_purchase_order_line(line_id)
    flash('Purchase order line deleted successfully.', 'success')
    return redirect(url_for('purchase_orders.edit_purchase_order', purchase_order_id=request.form['purchase_order_id']))

@purchase_orders_bp.route('/<int:purchase_order_id>/lines')
def get_lines(purchase_order_id):
    lines = get_purchase_order_lines(purchase_order_id)
    return jsonify(lines)


@purchase_orders_bp.route('/', methods=['GET', 'POST'])
def list_purchase_orders():
    if request.method == 'POST':
        supplier_id = request.form['supplier_id']
        purchase_order_id = insert_purchase_order(supplier_id)
        flash('Purchase order created successfully.', 'success')
        return redirect(url_for('purchase_orders.edit_purchase_order', purchase_order_id=purchase_order_id))

    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 20  # Show 20 POs per page

    # Get paginated purchase orders and total count
    purchase_orders = get_purchase_orders(limit=per_page, offset=(page - 1) * per_page)
    total_purchase_orders_count = get_purchase_orders_count()
    total_pages = (total_purchase_orders_count + per_page - 1) // per_page

    # Calculate total value of all purchase orders
    total_value = get_purchase_orders_total_value()

    suppliers = get_suppliers()
    return render_template('purchase_orders.html',
                           purchase_orders=purchase_orders,
                           suppliers=suppliers,
                           page=page,
                           total_pages=total_pages,
                           total_value=total_value)


@purchase_orders_bp.route('/edit_line/<int:line_id>', methods=['POST'])
def edit_purchase_order_line(line_id):
    data = request.get_json()  # Get the data sent via AJAX

    field = data.get('field')
    value = data.get('value')

    # Update the field in the database
    update_purchase_order_line_field(line_id, field, value)

    # Return a JSON response indicating success
    return jsonify({"success": True})


@purchase_orders_bp.route('/update_so_ship_date', methods=['POST'])
def update_sales_order_ship_date():
    # Get the data from the request
    data = request.get_json()

    po_line_id = data.get('po_line_id')
    so_line_id = data.get('so_line_id')
    po_ship_date = data.get('po_ship_date')
    supplier_buffer = int(data.get('supplier_buffer'))

    # Convert PO line's ship date to a datetime object
    po_ship_date_obj = datetime.strptime(po_ship_date, '%Y-%m-%d')

    # Calculate the new SO line ship date by adding the supplier buffer
    new_so_ship_date = po_ship_date_obj + datetime.timedelta(days=supplier_buffer)

    # Update the SO line's ship date in the database
    update_sales_order_line_ship_date(so_line_id, new_so_ship_date)

    # Update the SO line's status to 2 (assuming 2 is the status you want to set)
    update_sales_order_line_status(so_line_id, 2)

    # Return a success response
    return jsonify({"success": True})


@purchase_orders_bp.route('/purchase_suggestions', methods=['GET'])
def purchase_suggestions():
    suggestions = get_purchase_suggestions()  # Fetch the suggestions
    return render_template('purchase_suggestions.html', suggestions=suggestions)


@purchase_orders_bp.route('/generate_po', methods=['POST'])
def generate_po():
    selected_line_ids = request.form.getlist('line_ids')  # Get the selected sales order line IDs from the form

    if not selected_line_ids:
        flash('No lines selected to generate PO.', 'error')
        return redirect(url_for('purchase_orders.purchase_suggestions'))

    # Fetch supplier details for the selected lines
    placeholders = ','.join('?' for _ in selected_line_ids)
    query = f'''
        SELECT sol.id as sales_order_line_id, rfq.chosen_supplier, s.name as supplier_name
        FROM sales_order_lines sol
        LEFT JOIN rfq_lines rfq ON sol.rfq_line_id = rfq.id
        LEFT JOIN suppliers s ON rfq.chosen_supplier = s.id
        WHERE sol.id IN ({placeholders})
    '''

    lines_by_supplier = {}
    with db_cursor() as cursor:
        line_rows = _execute_with_cursor(cursor, query, selected_line_ids).fetchall()
        for line in line_rows:
            supplier_id = line['chosen_supplier']
            if supplier_id not in lines_by_supplier:
                lines_by_supplier[supplier_id] = []
            lines_by_supplier[supplier_id].append(line['sales_order_line_id'])

    # For each supplier, create a PO and assign the selected lines
    for supplier_id, line_ids in lines_by_supplier.items():
        po_id = insert_purchase_order(supplier_id)
        for line_id in line_ids:
            insert_purchase_order_line_from_suggestion(po_id, line_id)

    # Redirect to the new PO edit page after creation
    flash('Purchase orders generated successfully!', 'success')
    return redirect(url_for('purchase_orders.edit_purchase_order', purchase_order_id=po_id))


@purchase_orders_bp.route('/import_erp', methods=['GET', 'POST'])
def import_erp_purchase_orders():
    if request.method == 'POST':
        print("=== IMPORT STARTED ===")

        if 'file' not in request.files:
            print("ERROR: No file in request")
            return jsonify(success=False, message='No file uploaded'), 400

        file = request.files['file']
        print(f"File received: {file.filename}")

        if file.filename == '':
            print("ERROR: Empty filename")
            return jsonify(success=False, message='No file selected'), 400

        if file and file.filename.endswith('.csv'):
            try:
                # Read the CSV file
                stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
                csv_reader = csv.DictReader(stream)

                print("CSV file opened successfully")
                print(f"CSV Headers: {csv_reader.fieldnames}")

                # Process the CSV and import
                result = process_erp_csv(csv_reader)

                print(f"=== IMPORT COMPLETED ===")
                print(f"POs created: {result['pos_created']}")
                print(f"Lines created: {result['lines_created']}")

                return jsonify(
                    success=True,
                    message=f'Successfully imported {result["pos_created"]} purchase orders with {result["lines_created"]} lines',
                    results={
                        'purchase_orders': {
                            'processed': result['pos_created'],
                            'created': result['pos_created'],
                            'errors': []
                        },
                        'lines': {
                            'processed': result['lines_created'],
                            'created': result['lines_created'],
                            'errors': []
                        }
                    }
                )

            except Exception as e:
                print(f"=== ERROR OCCURRED ===")
                print(f"Error type: {type(e).__name__}")
                print(f"Error message: {str(e)}")
                print(f"Traceback:")
                traceback.print_exc()
                return jsonify(success=False, message=f'Error importing file: {str(e)}'), 500
        else:
            print("ERROR: File is not CSV")
            return jsonify(success=False, message='Please upload a CSV file'), 400

    # GET request - show the upload form
    return render_template('import_erp_purchase_orders.html')


def process_erp_csv(csv_reader):
    """
    Process the ERP CSV with hardcoded column mappings for your specific ERP format.
    """

    print("\n=== STARTING CSV PROCESSING ===")

    # Track what we've created
    pos_created = 0
    lines_created = 0
    current_po_id = None
    po_refs_processed = set()

    COLUMN_MAPPINGS = {
        'po_number': 'transactionalNumber',
        'supplier_name': 'companyName',
        'date_issued': 'entryDate',
        'line_number': 'itemNumber',
        'part_number': 'partNumber',
        'description': 'description',
        'condition': 'conditionCode',
        'quantity': 'quantityOrdered',
        'unit_cost_base': 'unitCost',
        'vendor_price': 'vendorPrice',
        'base_currency': 'baseCurrency',
        'foreign_currency': 'foreignCurrency',
        'due_date': 'dueDate',
        'status': 'status',
    }

    DEFAULT_PURCHASE_STATUS_ID = 1
    DEFAULT_LINE_STATUS_ID = 1

    try:
        with db_cursor(commit=True) as cursor:
            row_count = 0
            for row in csv_reader:
                row_count += 1
                print(f"\n--- Processing Row {row_count} ---")

                po_ref = row.get(COLUMN_MAPPINGS['po_number'], '').strip()
                print(f"PO Number: {po_ref}")

                if not po_ref:
                    print("SKIPPING: Empty PO number")
                    continue

                existing_po = _execute_with_cursor(
                    cursor,
                    'SELECT id FROM purchase_orders WHERE purchase_order_ref = ?',
                    (po_ref,)
                ).fetchone()

                if existing_po:
                    print(f"Found existing PO: {po_ref} (ID: {existing_po['id']})")
                    current_po_id = existing_po['id']
                elif po_ref not in po_refs_processed:
                    print(f"Creating NEW PO: {po_ref}")
                    supplier_name = row.get(COLUMN_MAPPINGS['supplier_name'], '').strip()
                    date_issued_str = row.get(COLUMN_MAPPINGS['date_issued'], '').strip()
                    status_name = row.get(COLUMN_MAPPINGS['status'], '').strip()
                    base_currency = row.get(COLUMN_MAPPINGS['base_currency'], '').strip()

                    print(f"  Supplier: {supplier_name}")
                    print(f"  Date: {date_issued_str}")
                    print(f"  Status: {status_name}")
                    print(f"  Currency: {base_currency}")

                    supplier_id = find_or_create_supplier(cursor, supplier_name)
                    print(f"  Supplier ID: {supplier_id}")

                    date_issued = parse_date(date_issued_str)
                    print(f"  Parsed date: {date_issued}")

                    currency_id = find_or_create_currency(cursor, base_currency)
                    print(f"  Currency ID: {currency_id}")

                    status_id = find_or_create_po_status(cursor, status_name)
                    print(f"  Status ID: {status_id}")

                    current_po_id = create_purchase_order_from_import(
                        cursor,
                        po_ref,
                        supplier_id,
                        date_issued,
                        None,
                        None,
                        status_id,
                        currency_id
                    )

                    print(f"  Created PO with ID: {current_po_id}")

                    po_refs_processed.add(po_ref)
                    pos_created += 1
                else:
                    print(f"  Warning: Duplicate PO ref in file: {po_ref} (skipping creation)")

                line_number = row.get(COLUMN_MAPPINGS['line_number'], '').strip()
                part_number = row.get(COLUMN_MAPPINGS['part_number'], '').strip()
                quantity_str = row.get(COLUMN_MAPPINGS['quantity'], '0').strip()
                unit_cost_str = row.get(COLUMN_MAPPINGS['unit_cost_base'], '0').strip()
                due_date_str = row.get(COLUMN_MAPPINGS['due_date'], '').strip()

                print(f"  Line: {line_number}, Part: {part_number}, Qty: {quantity_str}, Price: {unit_cost_str}")

                try:
                    line_number_int = int(line_number) if line_number else lines_created + 1
                except ValueError:
                    line_number_int = lines_created + 1
                    print(f"  Could not parse line number, using: {line_number_int}")

                quantity = int(float(quantity_str)) if quantity_str else 0
                unit_price = float(unit_cost_str) if unit_cost_str else 0.0
                due_date = parse_date(due_date_str) if due_date_str and due_date_str != 'null' else None

                print(f"  Parsed - Line#: {line_number_int}, Qty: {quantity}, Price: {unit_price}, Due: {due_date}")

                create_purchase_order_line_from_import(
                    cursor,
                    current_po_id,
                    line_number_int,
                    part_number,
                    quantity,
                    unit_price,
                    due_date,
                    DEFAULT_LINE_STATUS_ID
                )

                lines_created += 1
                print(f"  Line created successfully (Total lines: {lines_created})")

            print(f"\n=== COMMITTING TO DATABASE ===")
            print("Commit successful")

    except Exception as e:
        print(f"\n=== ERROR IN PROCESSING ===")
        print(f"Error: {str(e)}")
        traceback.print_exc()
        raise

    return {
        'pos_created': pos_created,
        'lines_created': lines_created
    }

def find_or_create_supplier(cursor, supplier_name):
    """Find supplier by name, create if doesn't exist."""
    print(f"    Finding/creating supplier: {supplier_name}")

    supplier = _execute_with_cursor(
        cursor,
        'SELECT id FROM suppliers WHERE name = ?',
        (supplier_name,)
    ).fetchone()

    if supplier:
        print(f"    Found existing supplier ID: {supplier['id']}")
        return supplier['id']

    print(f"    Creating new supplier")
    new_id = _insert_returning_id(
        cursor,
        'INSERT INTO suppliers (name) VALUES (?)',
        (supplier_name,)
    )
    print(f"    Created supplier ID: {new_id}")
    return new_id


def find_or_create_currency(cursor, currency_code):
    """Find or create currency by code."""
    print(f"    Finding/creating currency: {currency_code}")

    if not currency_code:
        print(f"    No currency code, using default: 1")
        return 1  # Default to GBP or whatever your default is

    currency = _execute_with_cursor(
        cursor,
        'SELECT id FROM currencies WHERE currency_code = ?',
        (currency_code,)
    ).fetchone()

    if currency:
        print(f"    Found existing currency ID: {currency['id']}")
        return currency['id']

    print(f"    Creating new currency")
    new_id = _insert_returning_id(
        cursor,
        'INSERT INTO currencies (currency_code, symbol, exchange_rate_to_eur) VALUES (?, ?, ?)',
        (currency_code, currency_code, 1.0)
    )
    print(f"    Created currency ID: {new_id}")
    return new_id

def find_or_create_po_status(cursor, status_name):
    """Find or create PO status by name."""
    print(f"    Finding/creating status: {status_name}")

    if not status_name:
        print(f"    No status name, using default: 1")
        return 1  # Default status

    status = _execute_with_cursor(
        cursor,
        'SELECT id FROM purchase_order_statuses WHERE name = ?',
        (status_name,)
    ).fetchone()

    if status:
        print(f"    Found existing status ID: {status['id']}")
        return status['id']

    print(f"    Creating new status")
    new_id = _insert_returning_id(
        cursor,
        'INSERT INTO purchase_order_statuses (name) VALUES (?)',
        (status_name,)
    )
    print(f"    Created status ID: {new_id}")
    return new_id


def parse_date(date_str):
    """Parse date string from various formats."""
    if not date_str or date_str == 'null':
        return datetime.now().date()

    # Try common date formats from your ERP
    date_formats = [
        '%Y-%m-%d %H:%M',  # 2025-11-07 18:47
        '%m/%d/%Y %H:%M:%S',  # 11/10/2025 00:00:00
        '%Y-%m-%d',  # 2024-01-31
        '%d/%m/%Y',  # 31/01/2024
        '%m/%d/%Y',  # 01/31/2024
    ]

    for fmt in date_formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    # If no format matches, return today
    return datetime.now().date()


def create_purchase_order_from_import(cursor, po_ref, supplier_id, date_issued,
                                      incoterms, payment_terms, status_id, currency_id):
    """Create a purchase order from imported data."""
    query = '''
        INSERT INTO purchase_orders 
        (purchase_order_ref, supplier_id, date_issued, incoterms, payment_terms, 
         purchase_status_id, currency_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    '''
    return _insert_returning_id(
        cursor,
        query,
        (po_ref, supplier_id, date_issued, incoterms, payment_terms, status_id, currency_id)
    )


def create_purchase_order_line_from_import(cursor, po_id, line_number, part_number,
                                           quantity, price, ship_date, status_id):
    """Create a purchase order line from imported data."""
    # Clean the part number to create base_part_number
    base_part_number = create_base_part_number(part_number)

    _execute_with_cursor(
        cursor,
        '''
        INSERT INTO purchase_order_lines 
        (purchase_order_id, line_number, base_part_number, quantity, price, 
         ship_date, status_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''',
        (po_id, line_number, base_part_number, quantity, price, ship_date, status_id)
    )


def create_base_part_number(part_number):
    """Clean part number by removing special characters and converting to uppercase."""
    import re
    base_part_number = re.sub(r'[^a-zA-Z0-9]', '', part_number).upper()
    return base_part_number
