from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from models import  calculate_ship_dates_for_open_orders, get_sales_orders_paginated, get_orders_for_calendar, check_order_stock_availability, update_all_sales_order_lines_status, is_line_in_sales_order, update_sales_order_line_status, update_multiple_sales_order_lines_status, get_sales_order_lines_with_status, get_sales_order_lines_with_status_and_po, get_sales_order_lines_with_po, get_max_line_number, update_sales_order_line, get_sales_orders, insert_sales_order_line, get_sales_order_by_id, insert_sales_order, get_customers, get_salespeople, update_sales_order, get_sales_order_lines, get_sales_statuses
import datetime
import os
from datetime import datetime, timedelta

from db import db_cursor, execute as db_execute


sales_orders_bp = Blueprint('sales_orders', __name__)


def _using_postgres() -> bool:
    return bool(os.getenv('DATABASE_URL') and os.getenv('DATABASE_URL').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query: str) -> str:
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query: str, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur

def generate_breadcrumbs(*crumbs):
    breadcrumbs = []
    for crumb, path in crumbs:
        breadcrumbs.append((crumb, path))
    return breadcrumbs

@sales_orders_bp.route('/', methods=['GET', 'POST'])
def list_sales_orders():
    if request.method == 'POST':
        customer_id = request.form['customer_id']
        customer_po_ref = request.form['customer_po_ref']
        insert_sales_order(customer_id, customer_po_ref)
        return redirect(url_for('sales_orders.list_sales_orders'))

    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)

    # Filter parameters
    customer_id = request.args.get('customer_id', type=int)
    salesperson_id = request.args.get('salesperson_id', type=int)
    status_id = request.args.get('status_id', type=int)
    search = request.args.get('search', '').strip()
    show_mismatches_only = request.args.get('show_mismatches', 'false').lower() == 'true'
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    # Sorting parameters - default to most recent first when showing mismatches
    sort_by = request.args.get('sort_by', 'date_entered' if show_mismatches_only else 'id')
    sort_order = request.args.get('sort_order', 'desc')

    # Get data
    customers = get_customers()
    customers = sorted(customers, key=lambda x: x['name'].lower())  # Sort alphabetically
    salespeople = get_salespeople()
    statuses = get_sales_statuses()

    # Get filtered and paginated sales orders
    result = get_sales_orders_paginated(
        page=page,
        per_page=per_page,
        customer_id=customer_id,
        salesperson_id=salesperson_id,
        status_id=status_id,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        show_mismatches_only=show_mismatches_only,
        date_from=date_from,
        date_to=date_to
    )

    # Group orders by customer for bulk assignment
    orders_by_customer = {}
    for order in result['orders']:
        cust_id = order['customer_id']
        if cust_id not in orders_by_customer:
            orders_by_customer[cust_id] = {
                'customer_name': order['customer_name'],
                'customer_salesperson_id': order['customer_salesperson_id'],
                'customer_salesperson_name': order['customer_salesperson_name'],
                'order_count': 0,
                'mismatch_count': 0,
                'order_ids': []
            }

        orders_by_customer[cust_id]['order_count'] += 1
        orders_by_customer[cust_id]['order_ids'].append(order['id'])

        # Count mismatches
        if order['customer_salesperson_id'] and order['salesperson_id'] != order['customer_salesperson_id']:
            orders_by_customer[cust_id]['mismatch_count'] += 1

    breadcrumbs = generate_breadcrumbs(
        ('Home', url_for('index')),
        ('Sales Orders', url_for('sales_orders.list_sales_orders'))
    )

    return render_template('sales_orders.html',
                           customers=customers,
                           sales_orders=result['orders'],
                           salespeople=salespeople,
                           statuses=statuses,
                           breadcrumbs=breadcrumbs,
                           pagination=result['pagination'],
                           orders_by_customer=orders_by_customer,
                           filters={
                               'customer_id': customer_id,
                               'salesperson_id': salesperson_id,
                               'status_id': status_id,
                               'search': search,
                               'sort_by': sort_by,
                               'sort_order': sort_order,
                               'per_page': per_page,
                               'show_mismatches': show_mismatches_only,
                               'date_from': date_from,
                               'date_to': date_to
                           })

@sales_orders_bp.route('/bulk_update_salesperson', methods=['POST'])
def bulk_update_salesperson():
    """Bulk update salesperson for multiple orders"""
    data = request.get_json()
    order_ids = data.get('order_ids', [])
    salesperson_id = data.get('salesperson_id')

    if not order_ids:
        return jsonify({'error': 'No orders specified'}), 400

    try:
        placeholders = ','.join(['?'] * len(order_ids))
        query = f"""
            UPDATE sales_orders
            SET salesperson_id = ?
            WHERE id IN ({placeholders})
        """
        params = [salesperson_id if salesperson_id else None] + order_ids
        db_execute(query, params, commit=True)

        return jsonify({'success': True, 'updated_count': len(order_ids)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sales_orders_bp.route('/new', methods=['GET', 'POST'])
def create_sales_order():
    if request.method == 'POST':
        # Capture form data
        customer_id = request.form['customer_id']
        customer_po_ref = request.form['customer_po_ref']
        salesperson_id = request.form.get('salesperson_id')
        contact_name = request.form['contact_name']
        incoterms = request.form['incoterms']
        payment_terms = request.form['payment_terms']

        # Insert sales order into the database
        insert_sales_order(customer_id, customer_po_ref, salesperson_id, contact_name, incoterms, payment_terms)

        return redirect(url_for('sales_orders.list_sales_orders'))

    # Fetch customers and salespeople for the form
    customers = get_customers()
    salespeople = get_salespeople()

    breadcrumbs = generate_breadcrumbs(('Home', url_for('index')), ('Sales Orders', url_for('sales_orders.list_sales_orders')), ('New Sales Order', url_for('sales_orders.create_sales_order')))
    return render_template('new_sales_order.html', customers=customers, salespeople=salespeople, breadcrumbs=breadcrumbs)


@sales_orders_bp.route('/<int:sales_order_id>/edit', methods=['GET', 'POST'])
def edit_sales_order(sales_order_id):
    if request.method == 'POST':
        # Update sales order details
        customer_id = request.form['customer_id']
        customer_po_ref = request.form['customer_po_ref']
        sales_status_id = request.form['sales_status_id']
        update_sales_order(sales_order_id, customer_id, customer_po_ref, sales_status_id)

        # Update sales order lines
        sales_order_lines = get_sales_order_lines(sales_order_id)

        for line in sales_order_lines:
            line_id = line['id']
            quantity = request.form.get(f'quantity_{line_id}')
            price = request.form.get(f'price_{line_id}')
            promise_date = request.form.get(f'promise_date_{line_id}')
            ship_date = request.form.get(f'ship_date_{line_id}')
            requested_date = request.form.get(f'requested_date_{line_id}')
            shipped_quantity = request.form.get(f'shipped_quantity_{line_id}')

            update_sales_order_line(
                line_id,
                quantity,
                price,
                promise_date,
                ship_date,
                requested_date,
                shipped_quantity
            )

        return redirect(url_for('sales_orders.edit_sales_order', sales_order_id=sales_order_id))

    # Fetch sales order details and related data for the GET request
    print(f"Sales Order ID: {sales_order_id}")
    sales_order = get_sales_order_by_id(sales_order_id)

    if sales_order is None:
        print(f"Sales Order with ID {sales_order_id} not found.")
        return "Sales order not found", 404
    else:
        print(f"Sales Order Retrieved: {sales_order}")

    # Fetch sales order lines and their status
    sales_order_lines_with_status = get_sales_order_lines_with_status(sales_order_id)
    print(f"Sales Order Lines with Status: {sales_order_lines_with_status}")

    sales_order_lines_with_po = get_sales_order_lines_with_po(sales_order_id)
    print(f"Sales Order Lines with PO: {sales_order_lines_with_po}")

    combined_sales_order_lines = []
    for line in sales_order_lines_with_status:
        # Find matching line in the PO result set
        matching_po_line = next((po_line for po_line in sales_order_lines_with_po if po_line['id'] == line['id']), None)
        if matching_po_line:
            # Add PO information to the status line
            line['purchase_order_id'] = matching_po_line.get('purchase_order_id')
            line['supplier_name'] = matching_po_line.get('supplier_name')

        combined_sales_order_lines.append(line)

    print(f"Combined Sales Order Lines: {combined_sales_order_lines}")

    customers = get_customers()
    statuses = get_sales_statuses()

    # Fetch max line number for the sales order
    max_line_number = get_max_line_number(sales_order_id)

    # Render the template and pass the combined data
    return render_template('sales_order_edit.html',
                           sales_order=sales_order,
                           sales_order_lines=combined_sales_order_lines,
                           customers=customers,
                           statuses=statuses,
                           max_line_number=max_line_number)


@sales_orders_bp.route('/<int:sales_order_id>/lines/add', methods=['POST'])
def add_sales_order_line(sales_order_id):
    # Get data from the form or request
    line_number = request.form.get('line_number')
    part_number = request.form.get('part_number')
    quantity = request.form.get('quantity')
    price = request.form.get('price')
    delivery_date = request.form.get('delivery_date')

    # Insert the sales order line into the database
    insert_sales_order_line(sales_order_id, line_number, part_number, quantity, price, delivery_date)

    # Redirect to the sales order page
    return redirect(url_for('sales_orders.edit_sales_order', sales_order_id=sales_order_id))


    return redirect(url_for('sales_orders.edit_sales_order', sales_order_id=sales_order_id))

def generate_sales_order_ref():
    last_order = db_execute(
        'SELECT sales_order_ref FROM sales_orders ORDER BY id DESC LIMIT 1',
        fetch='one'
    )

    if last_order:
        last_number = int(last_order['sales_order_ref'].split('-')[-1])
        new_order_number = last_number + 1
    else:
        new_order_number = 1

    year = datetime.now().year
    return f"SO{year}-{new_order_number:03d}"

@sales_orders_bp.route('/<int:line_id>/update', methods=['POST'])
def update_sales_order_line_api(line_id):
    data = request.get_json()

    # Extract the values from the JSON request
    quantity = data.get('quantity')
    price = data.get('price')
    promise_date = data.get('promise_date')
    ship_date = data.get('ship_date')
    requested_date = data.get('requested_date')
    shipped_quantity = data.get('shipped_quantity')

    update_sales_order_line(
        line_id,
        quantity,
        price,
        promise_date,
        ship_date,
        requested_date,
        shipped_quantity
    )

    return jsonify({"success": True})


@sales_orders_bp.route('/<int:line_id>/toggle_shipped', methods=['POST'])
def toggle_line_shipped(line_id):
    data = request.get_json()
    shipped = data.get('shipped', False)

    # Update only the shipped status
    db_execute(
        'UPDATE sales_order_lines SET shipped = ? WHERE id = ?',
        (shipped, line_id),
        commit=True,
    )

    # If shipped and we have a ship date, use it, otherwise use current date
    if shipped and not data.get('has_ship_date', False):
        ship_date = datetime.now().strftime('%Y-%m-%d')
        db_execute(
            'UPDATE sales_order_lines SET ship_date = ? WHERE id = ? AND ship_date IS NULL',
            (ship_date, line_id),
            commit=True,
        )

    # no explicit close needed

    return jsonify({"success": True})

@sales_orders_bp.route('/<int:sales_order_id>/order_health', methods=['GET'])
def get_order_health(sales_order_id):
    # uses db_execute so we don't manage connections manually

    # The acknowledged status is 2, so we check against that.
    acknowledged_status_id = 2

    # Query sales order lines and related data for health status
    query = '''
        SELECT sol.line_number,
               sol.base_part_number,  -- Include the part number
               (sol.sales_status_id = ?) AS acknowledged_to_customer,  -- Compare to status 2 for acknowledgment
               (pol.id IS NOT NULL) AS po_line_placed,  -- Check if PO line is placed
               (pol.status_id = ?) AS po_line_acknowledged,  -- Compare to status 2 for PO acknowledgment
               (pol.promised_date <= sol.promise_date) AS po_delivery_on_time
        FROM sales_order_lines sol
        LEFT JOIN purchase_order_lines pol ON pol.sales_order_line_id = sol.id
        LEFT JOIN purchase_orders po ON pol.purchase_order_id = po.id
        LEFT JOIN sales_statuses ss ON sol.sales_status_id = ss.id
        WHERE sol.sales_order_id = ?
    '''

    # Execute the query and fetch all the lines
    rows = db_execute(query, (acknowledged_status_id, acknowledged_status_id, sales_order_id), fetch='all') or []
    health_statuses = [dict(line) for line in rows]

    # Return the data as JSON
    return jsonify(health_statuses)

@sales_orders_bp.route('/<int:sales_order_id>/reference', methods=['GET'])
def get_sales_order_reference(sales_order_id):
    query = '''SELECT sales_order_ref FROM sales_orders WHERE id = ?'''
    result = db_execute(query, (sales_order_id,), fetch='one')

    # Check if the sales order exists
    if result:
        return result['sales_order_ref']  # Return the sales order reference as plain text
    else:
        return "Sales order not found", 404

@sales_orders_bp.route('/<int:sales_order_id>/update_lines_status', methods=['POST'])
def update_lines_status(sales_order_id):
    """Update sales order lines with shipped quantity"""
    if not request.is_json:
        return jsonify({"success": False, "message": "Expected JSON data"})

    data = request.json
    mode = data.get('mode')
    ship_date = data.get('ship_date')
    shipped_status_id = 3  # Status ID for "shipped"

    with db_cursor(commit=True) as cur:
        try:
            # First, get the current line data
            _execute_with_cursor(
                cur,
                """
                SELECT id, line_number, quantity, price, promise_date, requested_date
                FROM sales_order_lines
                WHERE sales_order_id = ?
                """,
                (sales_order_id,),
            )
            lines_data = cur.fetchall() or []

            lines_dict = {
                (row['id'] if isinstance(row, dict) else row[0]): {
                    'line_number': row['line_number'] if isinstance(row, dict) else row[1],
                    'quantity': row['quantity'] if isinstance(row, dict) else row[2],
                    'price': row['price'] if isinstance(row, dict) else row[3],
                    'promise_date': row['promise_date'] if isinstance(row, dict) else row[4],
                    'requested_date': row['requested_date'] if isinstance(row, dict) else row[5],
                }
                for row in lines_data
            }

            updated_count = 0

            if mode == 'all':
                target_line_ids = list(lines_dict.keys())
            elif mode == 'selected':
                target_line_ids = [int(x) for x in (data.get('line_ids', []) or [])]
                if not target_line_ids:
                    return jsonify({"success": False, "message": "No lines selected"})
            else:
                return jsonify({"success": False, "message": "Invalid mode"})

            for line_id in target_line_ids:
                if line_id not in lines_dict:
                    continue
                line_data = lines_dict[line_id]
                _execute_with_cursor(
                    cur,
                    """
                    UPDATE sales_order_lines
                    SET sales_status_id = ?, ship_date = ?, shipped_quantity = quantity,
                        line_number = ?, quantity = ?, price = ?,
                        promise_date = ?, requested_date = ?
                    WHERE id = ?
                    """,
                    (
                        shipped_status_id,
                        ship_date,
                        line_data['line_number'],
                        line_data['quantity'],
                        line_data['price'],
                        line_data['promise_date'],
                        line_data['requested_date'],
                        line_id,
                    ),
                )
                updated_count += 1

            message = (
                f"Updated {updated_count} lines with shipped quantities."
                if mode == 'all'
                else f"Updated {updated_count} selected lines with shipped quantities."
            )

            _execute_with_cursor(
                cur,
                """
                SELECT COUNT(*) AS non_shipped_count
                FROM sales_order_lines
                WHERE sales_order_id = ? AND (shipped_quantity < quantity OR shipped_quantity IS NULL)
                """,
                (sales_order_id,),
            )
            row = cur.fetchone()
            non_shipped_count = (
                row.get('non_shipped_count') if isinstance(row, dict)
                else (row[0] if row else 0)
            )

            if non_shipped_count == 0:
                _execute_with_cursor(
                    cur,
                    "UPDATE sales_orders SET sales_status_id = ? WHERE id = ?",
                    (shipped_status_id, sales_order_id),
                )
                message += " All lines fully shipped - updated sales order status to shipped."

            return jsonify({"success": True, "message": message, "updated_count": updated_count})

        except Exception as e:
            print(f"Error in update_lines_status: {str(e)}")
            return jsonify({"success": False, "message": str(e)})

# NOTE: helper functions below were previously annotated "Add this helper function to models.py".
# They remain here for backward compatibility, but now use the shared db helpers so they're
# Postgres/SQLite dual-mode.

def validate_line_ids_for_sales_order(line_ids, sales_order_id):
    """Validate that all line IDs belong to the specified sales order"""
    if not line_ids:
        return True

    try:
        placeholders = ','.join(['?'] * len(line_ids))
        row = db_execute(
            f"""
            SELECT COUNT(*) AS valid_count
            FROM sales_order_lines
            WHERE id IN ({placeholders}) AND sales_order_id = ?
            """,
            tuple(line_ids) + (sales_order_id,),
            fetch='one',
        )
        valid_count = row.get('valid_count', 0) if isinstance(row, dict) else (row[0] if row else 0)
        return valid_count == len(line_ids)
    except Exception as e:
        print(f"Error validating line IDs: {e}")
        return False


def count_non_shipped_lines(sales_order_id):
    """Count how many lines in the sales order are not shipped"""
    try:
        row = db_execute(
            """
            SELECT COUNT(*) AS non_shipped_count
            FROM sales_order_lines
            WHERE sales_order_id = ? AND sales_status_id != 3
            """,
            (sales_order_id,),
            fetch='one',
        )
        return row.get('non_shipped_count', 0) if isinstance(row, dict) else (row[0] if row else 0)
    except Exception as e:
        print(f"Error counting non-shipped lines: {e}")
        return -1


@sales_orders_bp.route('/calendar', methods=['GET'])
def calendar_view():
    # Get query parameters for filtering
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)
    customer_id = request.args.get('customer_id')
    date_type = request.args.get('date_type', 'ship_date')
    show_stock_alerts = request.args.get('show_stock_alerts', 'true') == 'true'
    show_pending_only = request.args.get('show_pending_only', 'false') == 'true'

    # If month/year not provided, use current month/year
    today = datetime.now()
    if not month or not year:
        month = today.month
        year = today.year

    # Create datetime object for the first day of selected month
    first_day = datetime(year, month, 1)

    # Calculate previous and next month
    if month == 1:
        prev_month = datetime(year - 1, 12, 1)
    else:
        prev_month = datetime(year, month - 1, 1)

    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)

    # Get the last day of the current month
    if month == 12:
        last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1) - timedelta(days=1)

    # Calculate the day of week (0 is Monday in Python's calendar module, but we want 0 to be Sunday)
    first_weekday = first_day.weekday() + 1  # +1 to shift from 0-6 (Mon-Sun) to 1-7
    if first_weekday == 7:  # If it's already Sunday
        first_weekday = 0

    # Generate calendar data structure
    calendar_data = []
    day = 1
    for week in range(6):  # Maximum of 6 weeks in a month
        week_data = []
        for weekday in range(7):  # 7 days in a week
            # Fill in leading/trailing days from previous/next month
            if week == 0 and weekday < first_weekday:
                # Calculate the day from previous month
                prev_month_last_day = (datetime(prev_month.year, prev_month.month + 1, 1) if prev_month.month < 12
                                       else datetime(prev_month.year + 1, 1, 1)) - timedelta(days=1)
                prev_day = prev_month_last_day.day - (first_weekday - weekday - 1)
                date_obj = datetime(prev_month.year, prev_month.month, prev_day)
                week_data.append({"date": date_obj, "orders": []})
            elif day > last_day.day:
                # Calculate the day from next month
                next_day = day - last_day.day
                date_obj = datetime(next_month.year, next_month.month, next_day)
                week_data.append({"date": date_obj, "orders": []})
                day += 1
            else:
                # Current month's day
                date_obj = datetime(year, month, day)
                week_data.append({"date": date_obj, "orders": []})
                day += 1

        calendar_data.append(week_data)

        # Stop if we've gone past the end of the month
        if day > last_day.day and week_data[-1]["date"].day >= 7:
            break

    # Fetch all sales order lines for the displayed calendar period
    # For proper calendar display, we need to include orders from previous/next month that appear in the view
    start_date = calendar_data[0][0]["date"]
    end_date = calendar_data[-1][-1]["date"]

    # Get sales orders with stock availability information
    db = None
    orders = get_orders_for_calendar(start_date, end_date, date_type, customer_id)

    # Add stock status information if enabled
    if show_stock_alerts:
        for order in orders:
            try:
                order.stock_status = check_order_stock_availability(db, order)
            except Exception as e:
                print(f"Error checking stock for order {getattr(order, 'id', 'unknown')}: {e}")
                # Set a default error status
                order.stock_status = {
                    "at_risk": False,
                    "status": "error",
                    "available_quantity": 0,
                    "shortage": 0,
                    "next_delivery_date": None,
                    "next_delivery_quantity": 0,
                    "details": "Error checking stock"
                }

    # Filter orders based on pending shipments if requested
    if show_pending_only:
        filtered_orders = []

        for order in orders:
            try:
                # Use the correct ID field - use sales_order_id if it exists, otherwise use id
                order_id = getattr(order, 'sales_order_id', getattr(order, 'id', None))

                if order_id:
                    non_shipped_count = count_non_fully_shipped_lines_with_db(db, order_id)
                    order.non_shipped_lines_count = non_shipped_count
                    order.has_pending_shipments = order.non_shipped_lines_count > 0

                    # Only include orders with pending shipments
                    if order.has_pending_shipments:
                        filtered_orders.append(order)
                    # If no pending shipments but we're debugging, add for visualization
                    elif non_shipped_count < 0:  # Error case
                        filtered_orders.append(order)
                else:
                    # Include the order by default if there's no valid ID
                    filtered_orders.append(order)
            except Exception as e:
                print(f"ERROR: Problem processing order: {e}")
                # Include the order by default if there's an error
                filtered_orders.append(order)

        orders = filtered_orders

    # Group orders by sales order reference before populating the calendar
    # This will make it easier for the template to display grouped orders
    orders_by_ref = {}
    for order in orders:
        # Make sure each order has a sales_order_ref attribute
        if not hasattr(order, 'sales_order_ref') or not order.sales_order_ref:
            # If no sales_order_ref, use the ID as a fallback
            order_ref = f"SO-{getattr(order, 'sales_order_id', getattr(order, 'id', 'unknown'))}"
            order.sales_order_ref = order_ref

    # Populate calendar with orders
    for order in orders:
        order_date = getattr(order, date_type)
        if not order_date:
            continue  # Skip if the date type is not set

        # Convert string date to datetime object if it's a string
        if isinstance(order_date, str):
            try:
                order_date = datetime.strptime(order_date, '%Y-%m-%d')
            except ValueError:
                # If parsing fails, skip this order
                continue

        # Find the correct day in our calendar data
        for week in calendar_data:
            for day in week:
                # Compare dates - convert datetime to date for comparison
                if day["date"].date() == order_date.date():
                    day["orders"].append(order)

    # Sort orders within each day by customer name and sales order reference
    for week in calendar_data:
        for day in week:
            day["orders"].sort(key=lambda o: (getattr(o, 'customer_name', ''), getattr(o, 'sales_order_ref', '')))

    # Get customers for filter dropdown
    customers = get_customers()

    # Generate breadcrumbs for this page
    breadcrumbs = generate_breadcrumbs(
        ('Home', url_for('index')),
        ('Sales Orders', url_for('sales_orders.list_sales_orders')),
        ('Calendar View', url_for('sales_orders.calendar_view'))
    )

    if db is not None:
        db.close()

    # Return the template with calendar data
    return render_template(
        'sales_orders_calendar.html',
        calendar_data=calendar_data,
        month=month,
        year=year,
        month_name=first_day.strftime('%B'),
        prev_month=prev_month,
        next_month=next_month,
        today=today.date(),
        customers=customers,
        customer_id=customer_id,
        date_type=date_type,
        breadcrumbs=breadcrumbs,
        show_stock_alerts=show_stock_alerts,
        show_pending_only=show_pending_only
    )


def count_non_fully_shipped_lines_with_db(db, sales_order_id):
    """Count how many lines in the sales order have not been fully shipped"""
    try:
        cursor = db.cursor()
        query = """
            SELECT COUNT(*) FROM sales_order_lines 
            WHERE sales_order_id = ? AND (quantity > shipped_quantity OR shipped_quantity IS NULL)
        """
        print(f"DEBUG: Executing query: {query} with sales_order_id={sales_order_id}")
        cursor.execute(query, (sales_order_id,))

        result = cursor.fetchone()[0]
        print(f"DEBUG: Query result: {result}")
        return result
    except Exception as e:
        print(f"ERROR: Error counting non-fully shipped lines: {e}")
        # Let's also check the structure of the sales_order_lines table
        # Avoid SQLite-only debug-only schema checks in dual-mode (left as comment).

        return -1  # Return -1 to indicate an error

@sales_orders_bp.route('/<int:line_id>/update_shipped_quantity', methods=['POST'])
def update_shipped_quantity(line_id):
    data = request.get_json()
    shipped_quantity = data.get('shipped_quantity', 0)

    line = db_execute('SELECT quantity FROM sales_order_lines WHERE id = ?', (line_id,), fetch='one')

    if not line:
        return jsonify({"success": False, "message": "Line not found"})

    # Validate shipped quantity
    total_quantity = line['quantity']
    if shipped_quantity > total_quantity:
        return jsonify(
            {"success": False, "message": f"Shipped quantity cannot exceed total quantity ({total_quantity})"})

    db_execute('UPDATE sales_order_lines SET shipped_quantity = ? WHERE id = ?', (shipped_quantity, line_id), commit=True)

    # If we're marking shipped and ship_date is empty, set it to today
    if shipped_quantity > 0 and data.get('update_ship_date', False):
        today = datetime.now().strftime('%Y-%m-%d')
        db_execute(
            'UPDATE sales_order_lines SET ship_date = ? WHERE id = ? AND ship_date IS NULL',
            (today, line_id),
            commit=True,
        )

    return jsonify({"success": True})


@sales_orders_bp.route('/calculate_ship_dates', methods=['POST'])
def calculate_ship_dates():
    try:
        # Get parameters from the request with fallback to query parameters
        # First try to get JSON data, but don't fail if no JSON is provided
        try:
            request_data = request.get_json(silent=True) or {}
        except:
            request_data = {}

        # Allow parameters to be passed in query string as well
        debug_mode = request_data.get('debug', request.args.get('debug', 'false').lower() == 'true')

        # Always avoid weekends
        avoid_weekends = True

        # Add debug mode parameter
        debug_info = {}
        result = calculate_ship_dates_for_open_orders(
            debug=debug_mode,
            debug_info=debug_info,
            avoid_weekends=avoid_weekends
        )

        # Include debug info in response if no orders were updated or in debug mode
        if debug_mode or result.get("updated_count", 0) == 0:
            result["debug_info"] = debug_info

        return jsonify(result)
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        return jsonify({
            "status": "error",
            "message": str(e),
            "details": error_details
        }), 500


def count_non_fully_shipped_lines(sales_order_id):
    """Count how many lines in the sales order have not been fully shipped"""
    try:
        row = db_execute(
            """
            SELECT COUNT(*) AS non_shipped_count
            FROM sales_order_lines
            WHERE sales_order_id = ? AND (quantity > shipped_quantity OR shipped_quantity IS NULL)
            """,
            (sales_order_id,),
            fetch='one',
        )
        if not row:
            return 0
        return row.get('non_shipped_count', 0) if isinstance(row, dict) else row[0]
    except Exception as e:
        print(f"Error counting non-fully shipped lines: {e}")
        return -1


@sales_orders_bp.route('/<int:sales_order_id>/update_salesperson', methods=['POST'])
def update_salesperson(sales_order_id):
    """Update the salesperson for a sales order."""
    try:
        data = request.get_json()
        salesperson_id = data.get('salesperson_id')

        db_execute(
            'UPDATE sales_orders SET salesperson_id = ? WHERE id = ?',
            (salesperson_id, sales_order_id),
            commit=True,
        )

        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
