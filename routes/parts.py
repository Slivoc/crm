import os
import logging
from math import ceil

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from db import db_cursor, execute as db_execute
from models import (
    get_all_manufacturers, get_part_alternatives, create_part_alternative, get_all_manufacturers_with_association, update_part_number,
    delete_part_number, get_part_numbers, insert_part_number, create_base_part_number,
    get_associated_manufacturers, get_po_lines_by_part_number, get_part_number_by_id,
    get_rfq_lines_by_part_number, get_requisitions_by_part_number, get_sales_order_lines_by_part_number,
    get_global_alternatives, add_global_alternative   # ?Y'^ add these
)

parts_bp = Blueprint('parts', __name__)

def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _extract_single_value(row):
    if not row:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def _with_returning_clause(query, returning='id'):
    if not _using_postgres():
        return query
    trimmed = query.strip().rstrip(';')
    return f"{trimmed} RETURNING {returning}"


def _last_inserted_id(cur, key='id'):
    if _using_postgres():
        row = cur.fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            return row.get(key)
        return row[0]
    return getattr(cur, 'lastrowid', None)


class Part:
    def __init__(self, row_dict):
        self.__dict__.update(row_dict)

    def __getitem__(self, key):
        return getattr(self, key)

@parts_bp.route('/parts', methods=['GET'])
def parts():
    search_query = request.args.get('search', '')
    manufacturer_id = request.args.get('manufacturer', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20

    categories = db_execute('SELECT category_id, category_name FROM part_categories', fetch='all') or []
    categories = [dict(row) for row in categories]

    query = '''
        SELECT DISTINCT pn.base_part_number, pn.part_number, pn.system_part_number, 
               pn.category_id, pc.category_name
        FROM part_numbers pn
        LEFT JOIN part_manufacturers pm ON pn.base_part_number = pm.base_part_number
        LEFT JOIN part_categories pc ON pn.category_id = pc.category_id
    '''
    params = []

    if search_query:
        query += '''
            WHERE pn.base_part_number LIKE ? OR pn.part_number LIKE ? OR pn.system_part_number LIKE ?
        '''
        params.extend([f'%{search_query}%'] * 3)

    if manufacturer_id:
        if 'WHERE' in query:
            query += ' AND '
        else:
            query += ' WHERE '
        query += 'pm.manufacturer_id = ?'
        params.append(manufacturer_id)

    with db_cursor() as cur:
        count_query = f'SELECT COUNT(*) FROM ({query}) as count_table'
        _execute_with_cursor(cur, count_query, params)
        total_results = _extract_single_value(cur.fetchone()) or 0
        total_pages = ceil(total_results / per_page) if total_results else 0

        final_params = params + [per_page, (page - 1) * per_page]
        _execute_with_cursor(cur, query + ' LIMIT ? OFFSET ?', final_params)
        part_numbers = cur.fetchall()

    part_numbers_with_manufacturers = []
    for part in part_numbers:
        part_dict = dict(part)
        part_dict['associated_manufacturers'] = get_associated_manufacturers(part['base_part_number'])
        part_numbers_with_manufacturers.append(Part(part_dict))

    all_manufacturers = get_all_manufacturers()

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Parts', url_for('parts.parts'))
    ]

    return render_template('parts.html',
                           part_numbers=part_numbers_with_manufacturers,
                           categories=categories,
                           all_manufacturers=all_manufacturers,
                           search_query=search_query,
                           selected_manufacturer=manufacturer_id,
                           page=page,
                           total_pages=total_pages,
                           breadcrumbs=breadcrumbs)

@parts_bp.route('/parts/create_part', methods=['POST'])
def create_part():
    # Handle both form data and JSON
    if request.is_json:
        data = request.get_json()
        part_number = data.get('part_number')
        manufacturer = data.get('manufacturer')
        rfq_id = data.get('rfq_id')
        system_part_number = data.get('system_part_number', '')
    else:
        part_number = request.form.get('part_number')
        manufacturer = request.form.get('manufacturer')
        rfq_id = request.form.get('rfq_id')
        system_part_number = request.form.get('system_part_number', '')

    base_part_number = create_base_part_number(part_number)

    logging.info(f'Creating new part number: {part_number}, Manufacturer: {manufacturer}, RFQ ID: {rfq_id}')
    logging.info(f'Base part number: {base_part_number}, System part number: {system_part_number}')

    try:
        insert_part_number(part_number, base_part_number, system_part_number, manufacturer)

        # Return JSON response for AJAX calls, redirect for form submits
        if request.is_json:
            return jsonify(success=True,
                           base_part_number=base_part_number,
                           display_part_number=part_number)
        else:
            flash('Part created successfully!', 'success')
            return redirect(url_for('parts.parts'))

    except Exception as e:
        logging.error(f'Error creating part number: {e}')
        if request.is_json:
            return jsonify(success=False, error=str(e))
        else:
            flash(f'Error creating part: {str(e)}', 'error')
            return redirect(url_for('parts.parts'))

@parts_bp.route('/parts/<base_part_number>/edit', methods=['POST'])
def edit_part_number(base_part_number):
    part_number = request.form['part_number']
    system_part_number = request.form.get('system_part_number')
    manufacturer_ids = request.form.getlist('manufacturers')

    update_part_number(base_part_number, part_number, system_part_number)

    with db_cursor(commit=True) as cur:
        _execute_with_cursor(
            cur,
            'DELETE FROM part_manufacturers WHERE base_part_number = ?',
            (base_part_number,),
        )
        for manufacturer_id in manufacturer_ids:
            _execute_with_cursor(
                cur,
                'INSERT INTO part_manufacturers (base_part_number, manufacturer_id) VALUES (?, ?)',
                (base_part_number, manufacturer_id),
            )

    flash('Part number updated successfully!', 'success')
    return redirect(url_for('parts.parts'))

@parts_bp.route('/delete_part', methods=['POST'])
def delete_part():
    base_part_number = request.form['base_part_number']
    try:
        delete_part_number(base_part_number)
        return jsonify(success=True)
    except Exception as e:
        logging.error(f'Error deleting part number: {e}')
        return jsonify(success=False, message=str(e))

@parts_bp.route('/api/get_manufacturers_by_part', methods=['GET'])
def get_manufacturers_by_part():
    part_number = request.args.get('part_number')
    base_part_number = create_base_part_number(part_number)

    manufacturers = db_execute('''
        SELECT m.id, m.name
        FROM part_manufacturers pm
        JOIN manufacturers m ON pm.manufacturer_id = m.id
        WHERE pm.base_part_number = ?
    ''', (base_part_number,), fetch='all') or []

    all_manufacturers = db_execute('SELECT id, name FROM manufacturers', fetch='all') or []

    return jsonify({
        'associated_manufacturers': [{'id': m['id'], 'name': m['name']} for m in manufacturers],
        'all_manufacturers': [{'id': m['id'], 'name': m['name']} for m in all_manufacturers]
    })

@parts_bp.route('/api/get_part_numbers', methods=['GET'])
def fetch_part_numbers():
    query = request.args.get('query', '')
    base_part_number = create_base_part_number(query)

    rows = db_execute('''
        SELECT pn.part_number, m.name as manufacturer
        FROM part_numbers pn
        LEFT JOIN part_manufacturers pm ON pn.id = pm.part_id
        LEFT JOIN manufacturers m ON pm.manufacturer_id = m.id
        WHERE pn.base_part_number LIKE ?
        LIMIT 10
    ''', (f'%{base_part_number}%',), fetch='all') or []

    part_map = {}
    for row in rows:
        part_num = row['part_number']
        if part_num not in part_map:
            part_map[part_num] = []
        if row.get('manufacturer'):
            part_map[part_num].append(row['manufacturer'])

    result = [{
        'part_number': part,
        'manufacturers': ', '.join(manufacturers)
    } for part, manufacturers in part_map.items()]

    return jsonify(result)

@parts_bp.route('/api/validate_part_number', methods=['GET'])
def validate_part_number():
    part_number = request.args.get('part_number')
    base_part_number = create_base_part_number(part_number)

    part = db_execute(
        'SELECT 1 FROM part_numbers WHERE base_part_number = ?',
        (base_part_number,),
        fetch='one'
    )

    valid = bool(part)
    logging.debug(
        f'Validating base part number: {base_part_number}, Original part number: {part_number}, Query result: {part}, Valid: {valid}')

    return jsonify(valid=valid)


@parts_bp.route('/api/search_parts', methods=['GET'])
def search_parts():
    search_query = request.args.get('search', '')
    manufacturer_id = request.args.get('manufacturer', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = '''
        SELECT DISTINCT pn.base_part_number, pn.part_number, pn.system_part_number
        FROM part_numbers pn
        LEFT JOIN part_manufacturers pm ON pn.base_part_number = pm.base_part_number
    '''
    params = []

    if search_query:
        query += '''
            WHERE pn.base_part_number LIKE ? OR pn.part_number LIKE ? OR pn.system_part_number LIKE ?
        '''
        params.extend([f'%{search_query}%'] * 3)

    if manufacturer_id:
        if 'WHERE' in query:
            query += ' AND '
        else:
            query += ' WHERE '
        query += 'pm.manufacturer_id = ?'
        params.append(manufacturer_id)

    with db_cursor() as cur:
        count_query = f'SELECT COUNT(*) FROM ({query}) as count_table'
        _execute_with_cursor(cur, count_query, params)
        total_results = _extract_single_value(cur.fetchone()) or 0
        total_pages = ceil(total_results / per_page) if total_results else 0

        final_params = params + [per_page, (page - 1) * per_page]
        _execute_with_cursor(cur, query + ' LIMIT ? OFFSET ?', final_params)
        part_numbers_raw = cur.fetchall()

    part_numbers = [{
        'base_part_number': part['base_part_number'],
        'part_number': part['part_number'],
        'system_part_number': part['system_part_number']
    } for part in part_numbers_raw]

    return jsonify({
        'part_numbers': part_numbers,
        'total_pages': total_pages,
        'current_page': page
    })

def get_manufacturer_names(manufacturer_ids):
    if not manufacturer_ids:
        return []

    placeholders = ','.join(['?'] * len(manufacturer_ids))
    query = f'SELECT name FROM manufacturers WHERE id IN ({placeholders})'
    manufacturers = db_execute(query, manufacturer_ids, fetch='all') or []
    return [m['name'] for m in manufacturers]


@parts_bp.route('/update_part', methods=['POST'])
def update_part():
    data = request.json
    base_part_number = data['base_part_number']
    part_number = data['part_number']
    system_part_number = data['system_part_number']
    manufacturer_ids = data['manufacturers']

    try:
        update_part_number(base_part_number, part_number, system_part_number)

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                'DELETE FROM part_manufacturers WHERE base_part_number = ?',
                (base_part_number,),
            )
            for manufacturer_id in manufacturer_ids:
                _execute_with_cursor(
                    cur,
                    'INSERT INTO part_manufacturers (base_part_number, manufacturer_id) VALUES (?, ?)',
                    (base_part_number, manufacturer_id),
                )

        updated_manufacturers = get_manufacturer_names(manufacturer_ids)
        return jsonify(success=True, manufacturers=updated_manufacturers)
    except Exception as e:
        logging.error(f'Error updating part number: {e}')
        return jsonify(success=False, message=str(e))

@parts_bp.route('/add_part', methods=['POST'])
def add_part():
    part_number = request.form['part_number']
    system_part_number = request.form['system_part_number']
    manufacturer_ids = request.form.getlist('manufacturers')
    base_part_number = create_base_part_number(part_number)

    try:
        insert_part_number(part_number, base_part_number, system_part_number, manufacturer_ids)
        new_part = {
            'base_part_number': base_part_number,
            'part_number': part_number,
            'system_part_number': system_part_number,
            'manufacturers': get_manufacturer_names(manufacturer_ids)
        }
        return jsonify(success=True, part=new_part)
    except Exception as e:
        logging.error(f'Error adding part number: {e}')
        return jsonify(success=False, message=str(e))


@parts_bp.route('/parts/<base_part_number>', methods=['GET'])
def view_part_number(base_part_number):
    global_alternatives = get_global_alternatives(base_part_number)

    # Fetch part number details using helper function
    part_number = get_part_number_by_id(base_part_number)

    if not part_number:
        flash(f'Part number {base_part_number} not found.', 'danger')
        return redirect(url_for('parts.parts'))

    # Fetch associated data using helper functions
    rfq_lines = get_rfq_lines_by_part_number(base_part_number)
    po_lines = get_po_lines_by_part_number(base_part_number)
    requisitions = get_requisitions_by_part_number(base_part_number)
    sales_order_lines = get_sales_order_lines_by_part_number(base_part_number)

    industry_query = """
        SELECT it.tag, COUNT(DISTINCT c.id) AS frequency
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        JOIN customers c ON so.customer_id = c.id
        JOIN customer_industry_tags cit ON c.id = cit.customer_id
        JOIN industry_tags it ON cit.tag_id = it.id
        WHERE sol.base_part_number = ?
        GROUP BY it.tag
        ORDER BY frequency DESC
    """

    top_customers_query = """
        SELECT 
            c.name,
            COUNT(DISTINCT so.id) as order_count,
            SUM(sol.quantity) as total_quantity,
            SUM(sol.price * sol.quantity) as total_value
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        JOIN customers c ON so.customer_id = c.id
        WHERE sol.base_part_number = ?
        GROUP BY c.id, c.name
        ORDER BY total_quantity DESC
        LIMIT 5
    """

    metrics_query = """
        SELECT 
            COUNT(DISTINCT so.id) as total_orders,
            COUNT(DISTINCT so.customer_id) as unique_customers,
            SUM(sol.quantity) as total_quantity,
            AVG(sol.price) as avg_price,
            MAX(sol.price) as max_price,
            MIN(sol.price) as min_price
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        WHERE sol.base_part_number = ?
    """

    with db_cursor() as cur:
        _execute_with_cursor(cur, industry_query, (base_part_number,))
        industry_distribution = [dict(row) for row in cur.fetchall()]

        _execute_with_cursor(cur, top_customers_query, (base_part_number,))
        top_customers = [dict(row) for row in cur.fetchall()]

        _execute_with_cursor(cur, metrics_query, (base_part_number,))
        sales_metrics_row = cur.fetchone()
        sales_metrics = dict(sales_metrics_row) if sales_metrics_row else {}

    # Prepare chart data
    chart_data = {
        'labels': [row['tag'] for row in industry_distribution],
        'datasets': [{
            'data': [row['frequency'] for row in industry_distribution],
            'label': 'Industry Distribution'
        }]
    }

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Parts', url_for('parts.parts')),
        (f'Part {base_part_number}', None)
    ]

    return render_template('view_part_number.html',
                           part_number=part_number,
                           rfq_lines=rfq_lines,
                           po_lines=po_lines,
                           requisitions=requisitions,
                           sales_order_lines=sales_order_lines,
                           breadcrumbs=breadcrumbs,
                           chart_data=chart_data,
                           top_customers=top_customers,
                           sales_metrics=sales_metrics,
                           global_alternatives=global_alternatives)

@parts_bp.route('/api/part_number_search', methods=['GET'])
def part_number_search():
    query = request.args.get('query', '').strip()
    base_part_number = create_base_part_number(query)  # Assuming you have this function to strip the part number

    search_query = '''
        SELECT base_part_number, part_number 
        FROM part_numbers 
        WHERE base_part_number LIKE ? OR part_number LIKE ?
        LIMIT 5
    '''
    results = db_execute(search_query, (f'%{base_part_number}%', f'%{query}%'), fetch='all') or []

    return jsonify([{'base_part_number': row['base_part_number'], 'part_number': row['part_number']} for row in results])


@parts_bp.route('/add_part_alternative', methods=['POST'])
def add_part_alternative():
    try:
        rfq_line_id = request.form['rfq_line_id']
        primary_base_part_number = request.form['primary_base_part_number']
        alternative_base_part_number = request.form['alternative_base_part_number']

        success = create_part_alternative(
            rfq_line_id=int(rfq_line_id),
            primary_base_part_number=primary_base_part_number,
            alternative_base_part_number=alternative_base_part_number
        )

        if success:
            return jsonify(success=True)
        else:
            return jsonify(success=False, message="Failed to add part alternative")

    except Exception as e:
        logging.error(f'Error adding part alternative: {e}')
        return jsonify(success=False, message=str(e))


@parts_bp.route('/get_part_alternatives/<base_part_number>', methods=['GET'])
def get_alternatives(base_part_number):
    try:
        alternatives = get_part_alternatives(base_part_number)
        return jsonify(success=True, alternatives=alternatives)
    except Exception as e:
        logging.error(f'Error getting part alternatives: {e}')
        return jsonify(success=False, message=str(e))


# Add a new category
@parts_bp.route('/categories', methods=['POST'])
def create_category():
    data = request.json
    category_name = data.get('category_name')
    description = data.get('description')

    if not category_name:
        return jsonify(success=False, message="Category name is required"), 400

    try:
        insert_query = _with_returning_clause(
            'INSERT INTO part_categories (category_name, description) VALUES (?, ?)',
            returning='category_id'
        )
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, insert_query, (category_name, description))
            category_id = _last_inserted_id(cur, key='category_id')

        return jsonify(
            success=True,
            category={
                'category_id': category_id,
                'category_name': category_name,
                'description': description
            }
        ), 201
    except Exception as e:
        logging.error(f'Error creating category: {e}')
        return jsonify(success=False, message=str(e)), 500


# Get all categories
@parts_bp.route('/categories', methods=['GET'])
def get_categories():
    try:
        categories = db_execute(
            'SELECT category_id, category_name, description, created_at FROM part_categories',
            fetch='all'
        ) or []

        return jsonify(
            success=True,
            categories=[dict(row) for row in categories]
        )
    except Exception as e:
        logging.error(f'Error fetching categories: {e}')
        return jsonify(success=False, message=str(e)), 500


# Update a category
@parts_bp.route('/categories/<int:category_id>', methods=['PUT'])
def update_category(category_id):
    data = request.json
    category_name = data.get('category_name')
    description = data.get('description')

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                'UPDATE part_categories SET category_name = ?, description = ? WHERE category_id = ?',
                (category_name, description, category_id),
            )

        return jsonify(success=True)
    except Exception as e:
        logging.error(f'Error updating category: {e}')
        return jsonify(success=False, message=str(e)), 500


# Delete a category
@parts_bp.route('/categories/<int:category_id>', methods=['DELETE'])
def delete_category(category_id):
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                'DELETE FROM part_categories WHERE category_id = ?',
                (category_id,),
            )

        return jsonify(success=True)
    except Exception as e:
        logging.error(f'Error deleting category: {e}')
        return jsonify(success=False, message=str(e)), 500


# Update part's category
@parts_bp.route('/parts/<base_part_number>/category', methods=['PUT'])
def update_part_category(base_part_number):
    data = request.json
    category_id = data.get('category_id')

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                'UPDATE part_numbers SET category_id = ? WHERE base_part_number = ?',
                (category_id, base_part_number),
            )

        return jsonify(success=True)
    except Exception as e:
        logging.error(f'Error updating part category: {e}')
        return jsonify(success=False, message=str(e)), 500


@parts_bp.route('/bulk_assign_category', methods=['POST'])
def bulk_assign_category():
    data = request.json
    prefix = data.get('prefix')
    category_id = data.get('category_id')

    if not prefix or not category_id:
        return jsonify(success=False, message="Prefix and category are required"), 400

    try:
        query = '''
            UPDATE part_numbers 
            SET category_id = ?
            WHERE part_number LIKE ? 
               OR base_part_number LIKE ? 
               OR system_part_number LIKE ?
        '''
        params = (category_id, f'{prefix}%', f'{prefix}%', f'{prefix}%')

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, query, params)
            affected_rows = cur.rowcount

        return jsonify(success=True, affected_rows=affected_rows)
    except Exception as e:
        logging.error(f'Error in bulk category assignment: {e}')
        return jsonify(success=False, message=str(e)), 500


@parts_bp.route('/timeline/<base_part_number>', methods=['GET'])
def part_timeline(base_part_number):
    """
    Display a timeline view for a specific part number showing incoming and outgoing events
    """
    # Get date range parameters, default to current month +/- 3 months
    from datetime import datetime, timedelta

    today = datetime.now()
    default_start = (today - timedelta(days=90)).strftime('%Y-%m-%d')
    default_end = (today + timedelta(days=90)).strftime('%Y-%m-%d')

    start_date = request.args.get('start_date', default_start)
    end_date = request.args.get('end_date', default_end)

    # Get the part details
    part_details = get_part_number_by_id(base_part_number)
    if not part_details:
        flash(f'Part number {base_part_number} not found', 'danger')
        return redirect(url_for('parts.parts'))

    # Get all incoming purchase orders for this part
    po_query = """
        SELECT 
            pol.id as line_id, 
            pol.purchase_order_id,
            po.purchase_order_ref,
            pol.line_number,
            pol.base_part_number, 
            pol.quantity,
            pol.promised_date as event_date,
            pol.quantity as incoming_quantity,
            s.name as supplier_name,
            'incoming' as event_type
        FROM purchase_order_lines pol
        JOIN purchase_orders po ON pol.purchase_order_id = po.id
        JOIN suppliers s ON po.supplier_id = s.id
        WHERE pol.base_part_number = ?
          AND pol.promised_date BETWEEN ? AND ?
          AND pol.promised_date IS NOT NULL
    """

    # Get all outgoing sales orders for this part
    so_query = """
        SELECT 
            sol.id as line_id,
            sol.sales_order_id, 
            so.sales_order_ref,
            sol.line_number,
            sol.base_part_number,
            sol.quantity,
            sol.ship_date as event_date,
            sol.quantity as outgoing_quantity,
            c.name as customer_name,
            'outgoing' as event_type
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        JOIN customers c ON so.customer_id = c.id
        WHERE sol.base_part_number = ? 
          AND sol.ship_date BETWEEN ? AND ?
          AND sol.ship_date IS NOT NULL
    """

    # Get current stock levels
    stock_query = """
        SELECT 
            SUM(available_quantity) as current_stock
        FROM stock_movements
        WHERE base_part_number = ? 
          AND movement_type = 'IN' 
          AND available_quantity > 0
    """

    # Execute queries
    with db_cursor() as cur:
        _execute_with_cursor(cur, po_query, (base_part_number, start_date, end_date))
        incoming_events = [dict(row) for row in cur.fetchall()]

        _execute_with_cursor(cur, so_query, (base_part_number, start_date, end_date))
        outgoing_events = [dict(row) for row in cur.fetchall()]

        _execute_with_cursor(cur, stock_query, (base_part_number,))
        stock_result = cur.fetchone()

    # Calculate current stock
    current_stock = stock_result['current_stock'] if stock_result and stock_result['current_stock'] else 0

    # Combine events and sort by date
    all_events = []
    for event in incoming_events:
        all_events.append(dict(event))
    for event in outgoing_events:
        all_events.append(dict(event))

    # Sort events by date
    all_events.sort(key=lambda x: x['event_date'])

    # Calculate projected stock levels for each date
    projected_stock = current_stock
    dates = []
    stock_levels = []

    # Create a list of unique dates from all events
    unique_dates = sorted(set(event['event_date'] for event in all_events))

    for date in unique_dates:
        # Calculate stock changes for this date
        day_events = [e for e in all_events if e['event_date'] == date]
        incoming = sum(e.get('incoming_quantity', 0) or 0 for e in day_events if e['event_type'] == 'incoming')
        outgoing = sum(e.get('outgoing_quantity', 0) or 0 for e in day_events if e['event_type'] == 'outgoing')

        # Update projected stock
        projected_stock = projected_stock + incoming - outgoing

        # Add to our data points
        dates.append(date)
        stock_levels.append(projected_stock)

    # Close database connection
    db.close()

    # Prepare chart data
    chart_data = {
        'labels': dates,
        'current_stock': current_stock,
        'projected_stock': stock_levels,
        'events': all_events
    }

    # Generate breadcrumbs for this page
    breadcrumbs = [
        ('Home', url_for('index')),
        ('Parts', url_for('parts.parts')),
        (f'Part {base_part_number}', url_for('parts.view_part_number', base_part_number=base_part_number)),
        ('Timeline', None)
    ]

    return render_template(
        'part_timeline.html',
        part=part_details,
        chart_data=chart_data,
        start_date=start_date,
        end_date=end_date,
        breadcrumbs=breadcrumbs
    )

@parts_bp.route('/parts/<base_part_number>/global_alts', methods=['POST'])
def add_global_alts(base_part_number):
    try:
        data = request.get_json(force=True)
        alt_list = data.get('alternatives', [])

        # tidy + dedupe
        cleaned = []
        for raw in alt_list:
            pn = (raw or '').strip()
            if pn:
                cleaned.append(pn)

        cleaned = list(dict.fromkeys(cleaned))  # keep order, remove duplicates

        for alt in cleaned:
            add_global_alternative(base_part_number, alt)

        updated = get_global_alternatives(base_part_number)
        return jsonify(success=True, alternatives=updated)

    except Exception as e:
        current_app.logger.error(f"Error adding global alts: {e}")
        return jsonify(success=False, message=str(e)), 500


@parts_bp.route('/api/check_alt_groups', methods=['POST'])
def check_alt_groups():
    """Check if any of the provided parts are already in a group"""
    try:
        data = request.get_json()
        part_numbers = data.get('part_numbers', [])

        if not part_numbers:
            return jsonify(success=False, message="No part numbers provided"), 400

        # Normalize part numbers to base format
        base_part_numbers = [create_base_part_number(pn.strip()) for pn in part_numbers if pn.strip()]

        placeholders = ','.join(['?'] * len(base_part_numbers))
        query = f"""
            SELECT 
                m.group_id,
                g.description,
                m2.base_part_number as group_member
            FROM part_alt_group_members m
            JOIN part_alt_groups g ON m.group_id = g.id
            JOIN part_alt_group_members m2 ON m.group_id = m2.group_id
            WHERE m.base_part_number IN ({placeholders})
        """
        rows = db_execute(query, base_part_numbers, fetch='all') or []

        if not rows:
            return jsonify(success=True, has_existing=False)

        groups = {}
        for row in rows:
            group_id = row['group_id']
            if group_id not in groups:
                groups[group_id] = {
                    'group_id': group_id,
                    'description': row['description'],
                    'members': []
                }
            member = row.get('group_member')
            if member and member not in groups[group_id]['members']:
                groups[group_id]['members'].append(member)

        return jsonify(success=True, has_existing=True, groups=list(groups.values()))

    except Exception as e:
        logging.error(f'Error checking alt groups: {e}')
        return jsonify(success=False, message=str(e)), 500


@parts_bp.route('/api/create_alt_group', methods=['POST'])
def create_alt_group():
    """Create a new alternative group or add to existing"""
    try:
        data = request.get_json()
        part_numbers = data.get('part_numbers', [])

        if not part_numbers:
            return jsonify(success=False, message="No part numbers provided"), 400

        # Normalize part numbers
        base_part_numbers = [create_base_part_number(pn.strip()) for pn in part_numbers if pn.strip()]

        if not base_part_numbers:
            return jsonify(success=False, message="No valid part numbers provided"), 400

        # Remove duplicates while preserving order
        seen = set()
        unique_parts = []
        for bp in base_part_numbers:
            if bp not in seen:
                seen.add(bp)
                unique_parts.append(bp)

        if len(unique_parts) < 2:
            return jsonify(success=False, message="Need at least two different parts to create a group"), 400

        # Use add_global_alternative to handle all the logic
        # Start by linking the first part to all others
        primary = unique_parts[0]

        for alt in unique_parts[1:]:
            add_global_alternative(primary, alt)

        # Get the complete group to return
        all_alternatives = get_global_alternatives(primary)

        # Get group info
        group_info = db_execute("""
            SELECT g.id, g.description
            FROM part_alt_groups g
            JOIN part_alt_group_members m ON g.id = m.group_id
            WHERE m.base_part_number = ?
        """, (primary,), fetch='one')

        return jsonify(
            success=True,
            primary=primary,
            group_id=group_info['id'] if group_info else None,
            description=group_info['description'] if group_info else None,
            all_members=[primary] + all_alternatives
        )

    except Exception as e:
        logging.error(f'Error creating alt group: {e}')
        return jsonify(success=False, message=str(e)), 500


@parts_bp.route('/alt_groups', methods=['GET'])
def alt_groups():
    """Page for managing alternative part groups"""
    logging.info("=" * 50)
    logging.info("ALT_GROUPS ROUTE CALLED")
    logging.info(f"Request method: {request.method}")
    logging.info(f"Request path: {request.path}")
    logging.info(f"Request full path: {request.full_path}")
    logging.info(f"Request URL: {request.url}")
    logging.info(f"Blueprint name: {request.blueprint}")

    try:
        breadcrumbs = [
            ('Home', url_for('index')),
            ('Parts', url_for('parts.parts')),
            ('Alternative Groups', None)
        ]
        logging.info(f"Breadcrumbs created: {breadcrumbs}")

        logging.info("About to render template: alt_groups.html")
        result = render_template('alt_groups.html', breadcrumbs=breadcrumbs)
        logging.info("Template rendered successfully")
        logging.info("=" * 50)
        return result

    except Exception as e:
        logging.error(f"ERROR in alt_groups route: {e}")
        logging.error(f"Exception type: {type(e)}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        logging.info("=" * 50)
        raise
