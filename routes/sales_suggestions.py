from flask import Blueprint, render_template, request, jsonify, session
from math import ceil
import json
import os

from db import db_cursor, execute as db_execute

sales_suggestions_bp = Blueprint('sales_suggestions', __name__, url_prefix='/sales-suggestions')


def _using_postgres() -> bool:
    return bool(os.getenv('DATABASE_URL') and os.getenv('DATABASE_URL').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query: str) -> str:
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query: str, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


@sales_suggestions_bp.route('/upload-stock', methods=['POST'])
def upload_stock():
    """Store uploaded stock data temporarily in session"""
    try:
        print("DEBUG: upload_stock route called")
        data = request.get_json()
        print(f"DEBUG: Received data keys: {data.keys() if data else 'None'}")

        stock_data = data.get('stock_data', [])
        mapping = data.get('mapping', {})

        print(f"DEBUG: Stock data rows: {len(stock_data)}")
        print(f"DEBUG: Mapping: {mapping}")

        # Process and store the mapped stock data
        processed_stock = {}

        for i, row in enumerate(stock_data):
            # Get the part number based on mapping
            part_col = mapping.get('part_number')
            qty_col = mapping.get('quantity')
            price_col = mapping.get('unit_price')  # Optional unit price column

            if part_col is not None and qty_col is not None:
                try:
                    part_number = row[int(part_col)]
                    quantity = row[int(qty_col)]

                    # Get unit price if provided (optional)
                    unit_price = None
                    if price_col is not None:
                        try:
                            unit_price = float(row[int(price_col)])
                        except (ValueError, TypeError, IndexError):
                            unit_price = None

                    if part_number and quantity:
                        # Store with part number as key
                        part_key = str(part_number).strip()
                        qty_value = float(quantity)

                        if part_key in processed_stock:
                            # If part already exists, sum quantities and average prices
                            existing_qty = processed_stock[part_key]['quantity']
                            existing_price = processed_stock[part_key].get('unit_price')

                            new_qty = existing_qty + qty_value

                            # Calculate weighted average price if both have prices
                            # Guard against division by zero
                            if new_qty > 0 and existing_price is not None and unit_price is not None:
                                new_price = ((existing_price * existing_qty) + (unit_price * qty_value)) / new_qty
                            elif unit_price is not None:
                                new_price = unit_price
                            else:
                                new_price = existing_price

                            processed_stock[part_key] = {
                                'quantity': new_qty,
                                'unit_price': new_price
                            }
                        else:
                            processed_stock[part_key] = {
                                'quantity': qty_value,
                                'unit_price': unit_price
                            }
                except (ValueError, TypeError, IndexError) as e:
                    if i < 5:  # Only print first 5 errors
                        print(f"DEBUG: Error processing row {i}: {e}")
                    continue

        print(f"DEBUG: Processed {len(processed_stock)} parts")

        # Store in session
        session['uploaded_stock'] = processed_stock
        session.modified = True

        print(f"DEBUG: Session updated with {len(session['uploaded_stock'])} parts")

        return jsonify({
            'success': True,
            'parts_loaded': len(processed_stock)
        })

    except Exception as e:
        print(f"ERROR in upload_stock: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@sales_suggestions_bp.route('/clear-stock', methods=['POST'])
def clear_stock():
    """Clear uploaded stock data from session"""
    if 'uploaded_stock' in session:
        del session['uploaded_stock']
        session.modified = True
    return jsonify({'success': True})


@sales_suggestions_bp.route('/debug/part')
def debug_part():
    """Debug endpoint to check if a specific part exists in uploaded stock"""
    part_to_check = request.args.get('part', '')

    if not part_to_check:
        return jsonify({'success': False, 'message': 'Please provide a part number via ?part=XXX'}), 400

    uploaded_stock = session.get('uploaded_stock', {})

    if not uploaded_stock:
        return jsonify({'success': False, 'message': 'No stock data uploaded'}), 400

    # Try various formats
    results = {
        'searched_for': part_to_check,
        'total_parts_in_stock': len(uploaded_stock),
        'exact_match': part_to_check in uploaded_stock,
        'exact_match_data': uploaded_stock.get(part_to_check, 'N/A'),
        'stripped_match': part_to_check.strip() in uploaded_stock,
        'stripped_match_data': uploaded_stock.get(part_to_check.strip(), 'N/A'),
        'similar_keys': []
    }

    # Find similar keys
    search_lower = part_to_check.lower().strip()
    for key in uploaded_stock.keys():
        if search_lower in key.lower() or key.lower() in search_lower:
            results['similar_keys'].append({
                'key': key,
                'data': uploaded_stock[key]
            })

    return jsonify({'success': True, 'debug_info': results})


@sales_suggestions_bp.route('/', methods=['GET'])
def sales_suggestions():
    """Main page for sales suggestions"""
    try:
        view_by = request.args.get('view_by', 'part')
        search_query = request.args.get('search', '')
        bom_filter = request.args.get('bom_filter', '')
        page = request.args.get('page', 1, type=int)
        per_page = 50

        # Get sort parameters (SINGLE TIME)
        sort_by = request.args.get('sort', 'last_sale_date')
        sort_order = request.args.get('order', 'desc').lower()

        # Validate sort order
        if sort_order not in ['asc', 'desc']:
            sort_order = 'desc'

        # NOTE: This handler uses dynamic SQL (ORDER BY) and aggregation.
        # We open a cursor via db_cursor so placeholder translation works in Postgres mode.
        with db_cursor() as cursor:
            # Check if we're using uploaded stock
            use_uploaded_stock = 'uploaded_stock' in session
            uploaded_stock = session.get('uploaded_stock', {})

            if view_by == 'part':
                if use_uploaded_stock:
                    # Query WITHOUT initial sorting - we'll sort after filtering
                    query = '''
                    SELECT 
                        pn.base_part_number,
                        pn.part_number,
                        pn.system_part_number,
                        COUNT(DISTINCT c.id) as customer_count,
                        MAX(so.date_entered) as last_sale_date,
                        SUM(sol.quantity) as total_quantity_sold,
                        AVG(sol.price) as avg_price,
                        GROUP_CONCAT(DISTINCT bh.name) as bom_names,
                        AVG(bl.guide_price) as avg_guide_price
                    FROM part_numbers pn
                    LEFT JOIN bom_lines bl ON pn.base_part_number = bl.base_part_number
                    LEFT JOIN bom_headers bh ON bl.bom_header_id = bh.id
                    INNER JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                    INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                    INNER JOIN customers c ON so.customer_id = c.id
                    WHERE 1=1
                    '''

                    params = []
                    if search_query:
                        query += ' AND (pn.part_number LIKE ? OR pn.system_part_number LIKE ? OR pn.base_part_number LIKE ?)'
                        params.extend([f'%{search_query}%'] * 3)

                    if bom_filter:
                        query += ' AND bh.name LIKE ?'
                        params.append(f'%{bom_filter}%')

                    query += ' GROUP BY pn.base_part_number HAVING customer_count > 0'

                    results = cursor.execute(query, params).fetchall()

                    # Filter to only include parts in uploaded stock
                    filtered_results = []
                    for row in results:
                        row_dict = dict(row)
                        part_identifiers = [
                            row_dict.get('part_number'),
                            row_dict.get('system_part_number'),
                            row_dict.get('base_part_number')
                        ]

                        stock_info = None
                        for identifier in part_identifiers:
                            if identifier and str(identifier).strip() in uploaded_stock:
                                stock_info = uploaded_stock[str(identifier).strip()]
                                break

                        if stock_info:
                            if isinstance(stock_info, dict):
                                stock_qty = stock_info.get('quantity', 0)
                                stock_price = stock_info.get('unit_price')
                            else:
                                stock_qty = stock_info
                                stock_price = None

                            if stock_qty > 0:
                                row_dict['stock_quantity'] = stock_qty
                                row_dict['stock_unit_price'] = stock_price
                                filtered_results.append(row_dict)

                    sort_key_map = {
                        'part_number': lambda x: (x.get('part_number') or x.get('base_part_number') or '').lower(),
                        'stock_quantity': lambda x: x.get('stock_quantity', 0),
                        'customer_count': lambda x: x.get('customer_count', 0),
                        'total_quantity_sold': lambda x: x.get('total_quantity_sold', 0),
                        'avg_price': lambda x: x.get('avg_price', 0) or 0,
                        'last_sale_date': lambda x: x.get('last_sale_date') or '',
                        'bom_names': lambda x: (x.get('bom_names') or '').lower()
                    }

                    sort_key = sort_key_map.get(sort_by, sort_key_map['last_sale_date'])
                    filtered_results.sort(key=sort_key, reverse=(sort_order == 'desc'))

                    # Paginate
                    total_results = len(filtered_results)
                    total_pages = ceil(total_results / per_page)
                    start_idx = (page - 1) * per_page
                    end_idx = start_idx + per_page
                    paginated_results = filtered_results[start_idx:end_idx]

                    parts_list = []
                    for row in paginated_results:
                        stock_price = row.get('stock_unit_price')
                        parts_list.append({
                            'part_number': row.get('part_number') or row.get('base_part_number'),
                            'system_part_number': row.get('system_part_number', ''),
                            'customer_count': row.get('customer_count', 0),
                            'last_sale_date': row.get('last_sale_date', ''),
                            'total_quantity_sold': row.get('total_quantity_sold', 0),
                            'avg_price': round(row.get('avg_price', 0) or 0, 2),
                            'bom_names': row.get('bom_names', ''),
                            'stock_quantity': row.get('stock_quantity', 0),
                            'stock_unit_price': round(stock_price, 2) if stock_price is not None else None,
                            'avg_guide_price': round(row.get('avg_guide_price', 0) or 0, 2)
                        })

                else:
                    # Using system stock - sort in SQL
                    valid_sql_sorts = {
                        'part_number': 'pn.part_number',
                        'stock_quantity': 'stock_quantity',
                        'customer_count': 'customer_count',
                        'total_quantity_sold': 'total_quantity_sold',
                        'avg_price': 'avg_price',
                        'last_sale_date': 'last_sale_date',
                        'bom_names': 'bom_names'
                    }

                    sort_col = valid_sql_sorts.get(sort_by, 'last_sale_date')
                    sort_order_sql = sort_order.upper()

                    query = '''
                        SELECT 
                            pn.base_part_number,
                            pn.part_number,
                            pn.system_part_number,
                            COUNT(DISTINCT c.id) as customer_count,
                            MAX(so.date_entered) as last_sale_date,
                            SUM(sol.quantity) as total_quantity_sold,
                            AVG(sol.price) as avg_price,
                            GROUP_CONCAT(DISTINCT bh.name) as bom_names,
                            AVG(bl.guide_price) as avg_guide_price,
                            COALESCE(SUM(sm.available_quantity), 0) as stock_quantity
                        FROM part_numbers pn
                        LEFT JOIN bom_lines bl ON pn.base_part_number = bl.base_part_number
                        LEFT JOIN bom_headers bh ON bl.bom_header_id = bh.id
                        INNER JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                        INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                        INNER JOIN customers c ON so.customer_id = c.id
                        LEFT JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number 
                            AND sm.movement_type = "IN" 
                            AND sm.available_quantity > 0
                        WHERE 1=1
                    '''

                    params = []
                    if search_query:
                        query += ' AND (pn.part_number LIKE ? OR pn.system_part_number LIKE ? OR pn.base_part_number LIKE ?)'
                        params.extend([f'%{search_query}%'] * 3)

                    if bom_filter:
                        query += ' AND bh.name LIKE ?'
                        params.append(f'%{bom_filter}%')

                    query += f'''
                        GROUP BY pn.base_part_number
                        HAVING customer_count > 0
                        ORDER BY {sort_col} {sort_order_sql}
                    '''

                    count_query = f'SELECT COUNT(*) as total FROM ({query})'
                    total_results = cursor.execute(count_query, params).fetchone()['total']
                    total_pages = ceil(total_results / per_page)

                    query += ' LIMIT ? OFFSET ?'
                    params.extend([per_page, (page - 1) * per_page])

                    results = cursor.execute(query, params).fetchall()

                    parts_list = [{
                        'part_number': row['part_number'] or row['base_part_number'],
                        'system_part_number': row['system_part_number'] or '',
                        'customer_count': row['customer_count'],
                        'last_sale_date': row['last_sale_date'] or '',
                        'total_quantity_sold': row['total_quantity_sold'],
                        'avg_price': round(row['avg_price'] or 0, 2),
                        'bom_names': row['bom_names'] or '',
                        'stock_quantity': row['stock_quantity'],
                        'stock_unit_price': None,
                        'avg_guide_price': round(row['avg_guide_price'] or 0, 2)
                    } for row in results]

            elif view_by == 'customer':
                if use_uploaded_stock:
                    # Query customers and check stock for their parts
                    query = '''
                    SELECT 
                        c.id as customer_id,
                        c.name as customer_name,
                        COUNT(DISTINCT pn.base_part_number) as unique_parts,
                        COUNT(DISTINCT so.id) as order_count,
                        SUM(sol.quantity) as total_quantity,
                        AVG(sol.price) as avg_price,
                        MAX(so.date_entered) as last_purchase_date
                    FROM customers c
                    INNER JOIN sales_orders so ON c.id = so.customer_id
                    INNER JOIN sales_order_lines sol ON so.id = sol.sales_order_id
                    INNER JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                    WHERE 1=1
                    '''

                    params = []
                    if search_query:
                        query += ' AND c.name LIKE ?'
                        params.append(f'%{search_query}%')

                    query += '''
                        GROUP BY c.id
                        ORDER BY last_purchase_date DESC
                    '''

                    results = cursor.execute(query, params).fetchall()

                    # For each customer, check if they have parts in stock
                    customers_list = []
                    for row in results:
                        row_dict = dict(row)
                        customer_id = row_dict['customer_id']

                        # Get parts bought by this customer
                        parts_query = '''
                            SELECT DISTINCT pn.part_number, pn.system_part_number, pn.base_part_number
                            FROM sales_order_lines sol
                            INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                            INNER JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                            WHERE so.customer_id = ?
                        '''
                        customer_parts = cursor.execute(parts_query, [customer_id]).fetchall()

                        # Count how many of their parts are in stock
                        parts_in_stock = 0
                        stock_value = 0
                        for part_row in customer_parts:
                            part_identifiers = [
                                part_row['part_number'],
                                part_row['system_part_number'],
                                part_row['base_part_number']
                            ]

                            for identifier in part_identifiers:
                                if identifier and str(identifier).strip() in uploaded_stock:
                                    stock_info = uploaded_stock[str(identifier).strip()]

                                    if isinstance(stock_info, dict):
                                        qty = stock_info.get('quantity', 0)
                                        price = stock_info.get('unit_price')
                                    else:
                                        qty = stock_info
                                        price = None

                                    if qty > 0:
                                        parts_in_stock += 1
                                        if price is not None:
                                            stock_value += qty * price
                                    break

                        if parts_in_stock > 0:
                            customers_list.append({
                                'customer_id': customer_id,
                                'customer_name': row_dict['customer_name'],
                                'unique_parts': row_dict['unique_parts'],
                                'parts_in_stock': parts_in_stock,
                                'stock_value': round(stock_value, 2) if stock_value > 0 else None,
                                'order_count': row_dict['order_count'],
                                'total_quantity': row_dict['total_quantity'],
                                'avg_price': round(row_dict['avg_price'] or 0, 2),
                                'last_purchase_date': row_dict['last_purchase_date']
                            })

                    # Pagination
                    total_results = len(customers_list)
                    total_pages = ceil(total_results / per_page)
                    start_idx = (page - 1) * per_page
                    end_idx = start_idx + per_page
                    customers_list = customers_list[start_idx:end_idx]

                else:
                    # Using system stock
                    query = '''
                    SELECT 
                        c.id as customer_id,
                        c.name as customer_name,
                        COUNT(DISTINCT pn.base_part_number) as unique_parts,
                        COUNT(DISTINCT so.id) as order_count,
                        SUM(sol.quantity) as total_quantity,
                        AVG(sol.price) as avg_price,
                        MAX(so.date_entered) as last_purchase_date
                    FROM customers c
                    INNER JOIN sales_orders so ON c.id = so.customer_id
                    INNER JOIN sales_order_lines sol ON so.id = sol.sales_order_id
                    INNER JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                    INNER JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number
                    WHERE sm.movement_type = "IN" 
                      AND sm.available_quantity > 0
                '''

                params = []
                if search_query:
                    query += ' AND c.name LIKE ?'
                    params.append(f'%{search_query}%')

                query += '''
                    GROUP BY c.id
                    HAVING COUNT(DISTINCT pn.base_part_number) > 0
                    ORDER BY last_purchase_date DESC
                '''

                # Get total count
                count_query = f'SELECT COUNT(*) as total FROM ({query})'
                total_results = cursor.execute(count_query, params).fetchone()['total']
                total_pages = ceil(total_results / per_page)

                # Add pagination
                query += ' LIMIT ? OFFSET ?'
                params.extend([per_page, (page - 1) * per_page])

                results = cursor.execute(query, params).fetchall()

                customers_list = [{
                    'customer_id': row['customer_id'],
                    'customer_name': row['customer_name'],
                    'unique_parts': row['unique_parts'],
                    'parts_in_stock': row['unique_parts'],
                    'stock_value': None,
                    'order_count': row['order_count'],
                    'total_quantity': row['total_quantity'],
                    'avg_price': round(row['avg_price'] or 0, 2),
                    'last_purchase_date': row['last_purchase_date'] or ''
                } for row in results]

            elif view_by == 'bom':
                if use_uploaded_stock:
                    # Get all BOMs
                    query = '''
                    SELECT 
                        bh.id,
                        bh.name,
                        bh.description,
                        COUNT(DISTINCT bl.base_part_number) as total_parts,
                        AVG(bl.guide_price) as avg_guide_price
                    FROM bom_headers bh
                    LEFT JOIN bom_lines bl ON bh.id = bl.bom_header_id
                    WHERE 1=1
                '''

                params = []
                if search_query:
                    query += ' AND bh.name LIKE ?'
                    params.append(f'%{search_query}%')

                query += '''
                    GROUP BY bh.id
                    ORDER BY bh.name
                '''

                results = cursor.execute(query, params).fetchall()

                # For each BOM, check how many parts are in stock
                boms_list = []
                for row in results:
                    row_dict = dict(row)
                    bom_id = row_dict['id']

                    # Get parts for this BOM
                    parts_query = '''
                        SELECT pn.part_number, pn.system_part_number, pn.base_part_number, bl.quantity as bom_quantity
                        FROM bom_lines bl
                        INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                        WHERE bl.bom_header_id = ?
                    '''
                    bom_parts = cursor.execute(parts_query, [bom_id]).fetchall()

                    parts_in_stock = 0
                    stock_value = 0
                    for part_row in bom_parts:
                        part_identifiers = [
                            part_row['part_number'],
                            part_row['system_part_number'],
                            part_row['base_part_number']
                        ]

                        for identifier in part_identifiers:
                            if identifier and str(identifier).strip() in uploaded_stock:
                                stock_info = uploaded_stock[str(identifier).strip()]

                                if isinstance(stock_info, dict):
                                    qty = stock_info.get('quantity', 0)
                                    price = stock_info.get('unit_price')
                                else:
                                    qty = stock_info
                                    price = None

                                if qty > 0:
                                    parts_in_stock += 1
                                    if price is not None:
                                        bom_qty = part_row['bom_quantity'] or 1
                                        stock_value += bom_qty * price
                                break

                    if parts_in_stock > 0:
                        boms_list.append({
                            'bom_id': bom_id,
                            'bom_name': row_dict['name'],
                            'description': row_dict['description'],
                            'total_parts': row_dict['total_parts'],
                            'parts_in_stock': parts_in_stock,
                            'stock_value': round(stock_value, 2) if stock_value > 0 else None,
                            'avg_guide_price': round(row_dict['avg_guide_price'] or 0, 2)
                        })

                # Pagination
                total_results = len(boms_list)
                total_pages = ceil(total_results / per_page)
                start_idx = (page - 1) * per_page
                end_idx = start_idx + per_page
                boms_list = boms_list[start_idx:end_idx]

            else:
                # Using system stock
                query = '''
                    SELECT 
                        bh.id as bom_id,
                        bh.name as bom_name,
                        bh.description,
                        COUNT(DISTINCT bl.base_part_number) as total_parts,
                        AVG(bl.guide_price) as avg_guide_price
                    FROM bom_headers bh
                    INNER JOIN bom_lines bl ON bh.id = bl.bom_header_id
                    INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                    INNER JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number
                    WHERE sm.movement_type = "IN" 
                      AND sm.available_quantity > 0
                '''

                params = []
                if search_query:
                    query += ' AND bh.name LIKE ?'
                    params.append(f'%{search_query}%')

                query += '''
                    GROUP BY bh.id
                    HAVING COUNT(DISTINCT bl.base_part_number) > 0
                    ORDER BY bh.name
                '''

                # Get total count
                count_query = f'SELECT COUNT(*) as total FROM ({query})'
                total_results = cursor.execute(count_query, params).fetchone()['total']
                total_pages = ceil(total_results / per_page)

                # Add pagination
                query += ' LIMIT ? OFFSET ?'
                params.extend([per_page, (page - 1) * per_page])

                results = cursor.execute(query, params).fetchall()

                boms_list = [{
                    'bom_id': row['bom_id'],
                    'bom_name': row['bom_name'],
                    'description': row['description'] or '',
                    'total_parts': row['total_parts'],
                    'parts_in_stock': row['total_parts'],
                    'stock_value': None,
                    'avg_guide_price': round(row['avg_guide_price'] or 0, 2)
                } for row in results]

        # db_cursor context handles cleanup

        if view_by == 'part':
            response_data = parts_list
        elif view_by == 'customer':
            response_data = customers_list
        elif view_by == 'bom':
            response_data = boms_list
        else:
            response_data = []

        return render_template('sales_suggestions.html',
                               view_by=view_by,
                               data=response_data,
                               search_query=search_query,
                               bom_filter=bom_filter,
                               page=page,
                               total_pages=total_pages,
                               use_uploaded_stock=use_uploaded_stock,
                               parts_in_upload=len(uploaded_stock),
                               sort=sort_by,
                               order=sort_order)

    except Exception as e:
        print(f"Error in sales_suggestions: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"An error occurred: {str(e)}", 500


@sales_suggestions_bp.route('/api/customer-parts/<int:customer_id>')
def get_customer_parts(customer_id):
    """Get parts in stock that this customer has purchased"""
    try:
        # Use shared helper (works for SQLite + Postgres)

        # Check if using uploaded stock
        use_uploaded_stock = 'uploaded_stock' in session
        uploaded_stock = session.get('uploaded_stock', {})

        # Get customer info
        customer = db_execute(
            '''
            SELECT id, name
            FROM customers
            WHERE id = ?
            ''',
            (customer_id,),
            fetch='one',
        )

        if not customer:
            return jsonify({'error': 'Customer not found'}), 404

        if use_uploaded_stock:
            # Get parts this customer has bought
            query = '''
                SELECT 
                    pn.part_number,
                    pn.system_part_number,
                    pn.base_part_number,
                    COUNT(DISTINCT so.id) as times_purchased,
                    SUM(sol.quantity) as total_quantity_purchased,
                    AVG(sol.price) as avg_purchase_price,
                    MAX(so.date_entered) as last_purchase_date,
                    AVG(bl.guide_price) as avg_guide_price
                FROM sales_order_lines sol
                INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                INNER JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                LEFT JOIN bom_lines bl ON pn.base_part_number = bl.base_part_number
                WHERE so.customer_id = ?
                GROUP BY pn.base_part_number
                ORDER BY last_purchase_date DESC
            '''

            results = db_execute(query, (customer_id,), fetch='all') or []

            # Filter for parts in stock
            parts_list = []
            for row in results:
                row_dict = dict(row)
                part_identifiers = [
                    row_dict.get('part_number'),
                    row_dict.get('system_part_number'),
                    row_dict.get('base_part_number')
                ]

                stock_info = None
                for identifier in part_identifiers:
                    if identifier and str(identifier).strip() in uploaded_stock:
                        stock_info = uploaded_stock[str(identifier).strip()]
                        break

                if stock_info:
                    # Handle both formats
                    if isinstance(stock_info, dict):
                        stock_qty = stock_info.get('quantity', 0)
                        stock_price = stock_info.get('unit_price')
                    else:
                        stock_qty = stock_info
                        stock_price = None

                    if stock_qty > 0:
                        parts_list.append({
                            'part_number': row_dict.get('part_number') or row_dict.get('base_part_number'),
                            'system_part_number': row_dict.get('system_part_number', ''),
                            'stock_quantity': stock_qty,
                            'stock_unit_price': round(stock_price, 2) if stock_price is not None else None,
                            'times_purchased': row_dict.get('times_purchased', 0),
                            'total_quantity_purchased': row_dict.get('total_quantity_purchased', 0),
                            'avg_purchase_price': round(row_dict.get('avg_purchase_price', 0) or 0, 2),
                            'avg_guide_price': round(row_dict.get('avg_guide_price', 0) or 0, 2),
                            'last_purchase_date': row_dict.get('last_purchase_date', '')
                        })

        else:
            # Using system stock
            query = '''
                SELECT 
                    pn.part_number,
                    pn.system_part_number,
                    pn.base_part_number,
                    COALESCE(SUM(sm.available_quantity), 0) as stock_quantity,
                    COUNT(DISTINCT so.id) as times_purchased,
                    SUM(sol.quantity) as total_quantity_purchased,
                    AVG(sol.price) as avg_purchase_price,
                    MAX(so.date_entered) as last_purchase_date,
                    AVG(bl.guide_price) as avg_guide_price
                FROM sales_order_lines sol
                INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                INNER JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                LEFT JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number 
                    AND sm.movement_type = "IN" 
                    AND sm.available_quantity > 0
                LEFT JOIN bom_lines bl ON pn.base_part_number = bl.base_part_number
                WHERE so.customer_id = ?
                GROUP BY pn.base_part_number
                HAVING stock_quantity > 0
                ORDER BY last_purchase_date DESC
            '''

            results = db_execute(query, (customer_id,), fetch='all') or []
            parts_list = [{
                'part_number': row['part_number'] or row['base_part_number'],
                'system_part_number': row['system_part_number'] or '',
                'stock_quantity': row['stock_quantity'],
                'stock_unit_price': None,  # System stock doesn't have unit prices
                'times_purchased': row['times_purchased'],
                'total_quantity_purchased': row['total_quantity_purchased'],
                'avg_purchase_price': round(row['avg_purchase_price'] or 0, 2),
                'avg_guide_price': round(row['avg_guide_price'] or 0, 2),
                'last_purchase_date': row['last_purchase_date'] or ''
            } for row in results]

        # db_execute handles cleanup per call

        return jsonify({
            'customer': {
                'id': customer['id'],
                'name': customer['name']
            },
            'parts': parts_list,
            'total_parts': len(parts_list)
        })

    except Exception as e:
        print(f"Error getting customer parts: {str(e)}")
        return jsonify({'error': str(e)}), 500


@sales_suggestions_bp.route('/api/part-details/<path:part_number>')
def get_part_details(part_number):
    """Get details for a specific part including customer breakdown"""
    try:
        # Use shared helper (works for SQLite + Postgres)

        # Check if using uploaded stock
        use_uploaded_stock = 'uploaded_stock' in session
        uploaded_stock = session.get('uploaded_stock', {})

        # Get part info
        part = db_execute(
            '''
            SELECT pn.part_number, pn.system_part_number, pn.base_part_number
            FROM part_numbers pn
            WHERE pn.part_number = ?
               OR pn.system_part_number = ?
               OR pn.base_part_number = ?
            LIMIT 1
            ''',
            (part_number, part_number, part_number),
            fetch='one',
        )

        if not part:
            return jsonify({'error': 'Part not found'}), 404

        base_part_number = part['base_part_number']

        # Check stock
        stock_quantity = 0
        stock_unit_price = None

        if use_uploaded_stock:
            part_identifiers = [
                part['part_number'],
                part['system_part_number'],
                part['base_part_number']
            ]

            for identifier in part_identifiers:
                if identifier and str(identifier).strip() in uploaded_stock:
                    stock_info = uploaded_stock[str(identifier).strip()]
                    if isinstance(stock_info, dict):
                        stock_quantity = stock_info.get('quantity', 0)
                        stock_unit_price = stock_info.get('unit_price')
                    else:
                        stock_quantity = stock_info
                    break
        else:
            # System stock
            stock_result = db_execute(
                '''
                SELECT COALESCE(SUM(sm.available_quantity), 0) as stock_quantity
                FROM stock_movements sm
                WHERE sm.base_part_number = ?
                  AND sm.movement_type = "IN"
                  AND sm.available_quantity > 0
                ''',
                (base_part_number,),
                fetch='one',
            )
            stock_quantity = stock_result['stock_quantity'] if stock_result else 0

        # Get customer purchase history
        customers = db_execute(
            '''
            SELECT
                c.id as customer_id,
                c.name as customer_name,
                COUNT(DISTINCT so.id) as times_purchased,
                SUM(sol.quantity) as total_quantity_purchased,
                AVG(sol.price) as avg_purchase_price,
                MAX(so.date_entered) as last_purchase_date,
                AVG(bl.guide_price) as avg_guide_price
            FROM sales_order_lines sol
            INNER JOIN sales_orders so ON sol.sales_order_id = so.id
            INNER JOIN customers c ON so.customer_id = c.id
            LEFT JOIN bom_lines bl ON sol.base_part_number = bl.base_part_number
            WHERE sol.base_part_number = ?
            GROUP BY c.id
            ORDER BY times_purchased DESC, last_purchase_date DESC
            ''',
            (base_part_number,),
            fetch='all',
        ) or []

        # Calculate avg sale price
        avg_sale_price_result = db_execute(
            '''
            SELECT AVG(sol.price) as avg_sale_price
            FROM sales_order_lines sol
            WHERE sol.base_part_number = ?
            ''',
            (base_part_number,),
            fetch='one',
        )

        avg_sale_price = avg_sale_price_result['avg_sale_price'] if avg_sale_price_result else 0

        customers_list = [{
            'customer_id': row['customer_id'],
            'customer_name': row['customer_name'],
            'times_purchased': row['times_purchased'],
            'total_quantity_purchased': row['total_quantity_purchased'],
            'avg_purchase_price': round(row['avg_purchase_price'] or 0, 2),
            'last_purchase_date': row['last_purchase_date'] or '',
            'avg_guide_price': round(row['avg_guide_price'] or 0, 2)
        } for row in customers]

        # db_execute handles cleanup per call

        return jsonify({
            'part': {
                'part_number': part['part_number'] or part['base_part_number'],
                'base_part_number': base_part_number,
                'stock_quantity': stock_quantity,
                'stock_unit_price': round(stock_unit_price, 2) if stock_unit_price is not None else None,
                # Fixed: None (not none)
                'avg_sale_price': round(avg_sale_price or 0, 2)
            },
            'customers': customers_list
        })

    except Exception as e:
        print(f"Error getting part details: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@sales_suggestions_bp.route('/api/out-of-stock')
def get_out_of_stock():
    """Get parts that are out of stock but have sales history"""
    try:
        # Use shared helper (works for SQLite + Postgres)

        # Check if using uploaded stock
        use_uploaded_stock = 'uploaded_stock' in session
        uploaded_stock = session.get('uploaded_stock', {})

        if use_uploaded_stock:
            # Get all parts with sales history
            query = '''
                SELECT 
                    pn.base_part_number,
                    pn.part_number,
                    pn.system_part_number,
                    COUNT(DISTINCT so.id) as order_count,
                    COUNT(DISTINCT c.id) as customer_count,
                    SUM(sol.quantity) as total_quantity,
                    AVG(sol.price) as avg_price,
                    MAX(so.date_entered) as last_purchase_date
                FROM part_numbers pn
                INNER JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                INNER JOIN customers c ON so.customer_id = c.id
                GROUP BY pn.base_part_number
                ORDER BY order_count DESC, customer_count DESC
                LIMIT 200
            '''

            results = db_execute(query, fetch='all') or []

            # Filter to only parts NOT in stock
            out_of_stock = []
            for row in results:
                row_dict = dict(row)
                part_identifiers = [
                    row_dict.get('part_number'),
                    row_dict.get('system_part_number'),
                    row_dict.get('base_part_number')
                ]

                # Check if part is NOT in stock
                in_stock = False
                for identifier in part_identifiers:
                    if identifier and str(identifier).strip() in uploaded_stock:
                        stock_info = uploaded_stock[str(identifier).strip()]

                        # Handle both formats
                        if isinstance(stock_info, dict):
                            qty = stock_info.get('quantity', 0)
                        else:
                            qty = stock_info

                        if qty > 0:
                            in_stock = True
                            break

                if not in_stock:
                    out_of_stock.append(row_dict)

        else:
            # Using system stock - find parts with no stock
            query = '''
                SELECT 
                    pn.base_part_number,
                    pn.part_number,
                    pn.system_part_number,
                    COUNT(DISTINCT so.id) as order_count,
                    COUNT(DISTINCT c.id) as customer_count,
                    SUM(sol.quantity) as total_quantity,
                    AVG(sol.price) as avg_price,
                    MAX(so.date_entered) as last_purchase_date
                FROM part_numbers pn
                INNER JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                INNER JOIN customers c ON so.customer_id = c.id
                LEFT JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number 
                    AND sm.movement_type = "IN" 
                    AND sm.available_quantity > 0
                WHERE sm.id IS NULL
                GROUP BY pn.base_part_number
                ORDER BY order_count DESC, customer_count DESC
                LIMIT 100
            '''

            out_of_stock = [dict(row) for row in (db_execute(query, fetch='all') or [])]

        # db_execute handles cleanup per call

        return jsonify({
            'success': True,
            'parts': out_of_stock
        })

    except Exception as e:
        print(f"Error getting out of stock parts: {str(e)}")
        return jsonify({'error': str(e)}), 500


@sales_suggestions_bp.route('/api/bom-parts/<int:bom_id>')
def get_bom_parts(bom_id):
    """Get parts in stock for a specific BOM"""
    try:
        # Use shared helper (works for SQLite + Postgres)

        # Check if using uploaded stock
        use_uploaded_stock = 'uploaded_stock' in session
        uploaded_stock = session.get('uploaded_stock', {})

        # Get BOM info
        bom = db_execute(
            '''
            SELECT id, name, description
            FROM bom_headers
            WHERE id = ?
            ''',
            (bom_id,),
            fetch='one',
        )

        if not bom:
            return jsonify({'error': 'BOM not found'}), 404

        if use_uploaded_stock:
            # Get base part info first (without expensive joins)
            query = '''
                SELECT 
                    pn.part_number,
                    pn.system_part_number,
                    pn.base_part_number,
                    bl.quantity as bom_quantity,
                    bl.guide_price
                FROM bom_lines bl
                INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                WHERE bl.bom_header_id = ?
                ORDER BY pn.part_number
            '''

            results = db_execute(query, (bom_id,), fetch='all') or []

            # Filter for parts in stock and get sales data only for those
            parts_list = []
            for row in results:
                row_dict = dict(row)

                # Check if part is in uploaded stock
                part_identifiers = [
                    row_dict.get('part_number'),
                    row_dict.get('system_part_number'),
                    row_dict.get('base_part_number')
                ]

                stock_info = None
                for identifier in part_identifiers:
                    if identifier and str(identifier).strip() in uploaded_stock:
                        stock_info = uploaded_stock[str(identifier).strip()]
                        break

                if stock_info:
                    # Handle both formats
                    if isinstance(stock_info, dict):
                        stock_qty = stock_info.get('quantity', 0)
                        stock_price = stock_info.get('unit_price')
                    else:
                        stock_qty = stock_info
                        stock_price = None

                    if stock_qty > 0:
                        # Now get sales data only for this specific part
                        sales_query = '''
                            SELECT 
                                AVG(sol.price) as avg_sale_price,
                                COUNT(DISTINCT so.customer_id) as times_sold,
                                MAX(so.date_entered) as last_sale_date,
                                GROUP_CONCAT(DISTINCT c.name) as customers
                            FROM sales_order_lines sol
                            INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                            LEFT JOIN customers c ON so.customer_id = c.id
                            WHERE sol.base_part_number = ?
                        '''
                        # SQLite uses GROUP_CONCAT; Postgres uses STRING_AGG
                        sales_query_xdb = (
                            sales_query.replace(
                                'GROUP_CONCAT(DISTINCT c.name)',
                                'STRING_AGG(DISTINCT c.name, \",\")',
                            )
                            if _using_postgres()
                            else sales_query
                        )
                        sales_data = db_execute(
                            sales_query_xdb,
                            (row_dict.get('base_part_number'),),
                            fetch='one',
                        )

                        # Calculate delta as percentage (negative = cheaper than guide = good)
                        guide_price = round(row_dict.get('guide_price', 0) or 0, 2)
                        delta_percent = None
                        if stock_price is not None and guide_price > 0:
                            delta_percent = round(((stock_price - guide_price) / guide_price) * 100, 1)

                        parts_list.append({
                            'part_number': row_dict.get('part_number') or row_dict.get('base_part_number'),
                            'system_part_number': row_dict.get('system_part_number', ''),
                            'bom_quantity': row_dict.get('bom_quantity', 0),
                            'stock_quantity': stock_qty,
                            'stock_unit_price': round(stock_price, 2) if stock_price is not None else None,
                            'guide_price': guide_price,
                            'delta_percent': delta_percent,
                            'avg_sale_price': round(sales_data['avg_sale_price'] or 0, 2) if sales_data else 0,
                            'times_sold': sales_data['times_sold'] if sales_data else 0,
                            'last_sale_date': sales_data['last_sale_date'] if sales_data else '',
                            'customers': sales_data['customers'] if sales_data else ''
                        })

        else:
            # Using system stock
            query = '''
                SELECT 
                    pn.part_number,
                    pn.system_part_number,
                    pn.base_part_number,
                    bl.quantity as bom_quantity,
                    COALESCE(SUM(sm.available_quantity), 0) as stock_quantity,
                    bl.guide_price,
                    AVG(sol.price) as avg_sale_price,
                    COUNT(DISTINCT so.customer_id) as times_sold,
                    MAX(so.date_entered) as last_sale_date,
                    GROUP_CONCAT(DISTINCT c.name) as customers
                FROM bom_lines bl
                INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                INNER JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number
                LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
                LEFT JOIN customers c ON so.customer_id = c.id
                WHERE bl.bom_header_id = ?
                  AND sm.movement_type = "IN"
                  AND sm.available_quantity > 0
                GROUP BY pn.base_part_number
                HAVING stock_quantity > 0
                ORDER BY pn.part_number
            '''

            query_xdb = (
                query.replace('GROUP_CONCAT(DISTINCT c.name)', 'STRING_AGG(DISTINCT c.name, \",\")')
                if _using_postgres()
                else query
            )
            results = db_execute(query_xdb, (bom_id,), fetch='all') or []
            parts_list = [{
                'part_number': row['part_number'] or row['base_part_number'],
                'system_part_number': row['system_part_number'] or '',
                'bom_quantity': row['bom_quantity'],
                'stock_quantity': row['stock_quantity'],
                'stock_unit_price': None,  # System stock doesn't have unit prices
                'guide_price': round(row['guide_price'] or 0, 2),
                'delta_percent': None,  # No delta for system stock (no unit prices)
                'avg_sale_price': round(row['avg_sale_price'] or 0, 2),
                'times_sold': row['times_sold'],
                'last_sale_date': row['last_sale_date'] or '',
                'customers': row['customers'] or ''
            } for row in results]

        # db_execute handles cleanup per call

        return jsonify({
            'bom': {
                'id': bom['id'],
                'name': bom['name'],
                'description': bom['description']
            },
            'parts': parts_list,
            'total_parts': len(parts_list)
        })

    except Exception as e:
        print(f"Error getting BOM parts: {str(e)}")
        return jsonify({'error': str(e)}), 500
