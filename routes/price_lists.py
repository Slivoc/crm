from flask import Blueprint, request, jsonify, current_app, render_template
from werkzeug.utils import secure_filename
import os
import csv
import datetime
from typing import Optional, Dict, Any
from db import db_cursor, execute as db_execute


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _get_inserted_id(row, cur):
    if row is None:
        return getattr(cur, 'lastrowid', None)
    if isinstance(row, dict):
        return row.get('id')
    try:
        return row[0]
    except Exception:
        return getattr(cur, 'lastrowid', None)

price_lists_bp = Blueprint('price_lists', __name__)


def _row_to_dict(row):
    return dict(row) if row else None


def get_price_list_price(base_part_number: str, quantity: int) -> Optional[Dict[str, Any]]:
    query = '''
        SELECT pli.id, pli.price_list_id, pli.lead_time, 
               pb.quantity, pb.price,
               pl.supplier_id, s.name as supplier_name
        FROM price_list_items pli
        JOIN price_breaks pb ON pli.id = pb.price_list_item_id
        JOIN price_lists pl ON pli.price_list_id = pl.id
        JOIN suppliers s ON pl.supplier_id = s.id
        WHERE pli.base_part_number = ?
        ORDER BY pb.quantity ASC
    '''

    price_breaks = db_execute(query, (base_part_number,), fetch='all') or []
    price_breaks = [dict(row) for row in price_breaks]

    if not price_breaks:
        return None

    selected_price_break = None
    for price_break in price_breaks:
        if price_break['quantity'] > quantity:
            break
        selected_price_break = price_break

    if not selected_price_break:
        return None

    next_break_quantity = None
    if selected_price_break != price_breaks[-1]:
        next_index = price_breaks.index(selected_price_break) + 1
        next_break_quantity = price_breaks[next_index]['quantity']

    return {
        'price': selected_price_break['price'],
        'quantity': selected_price_break['quantity'],
        'lead_time': selected_price_break['lead_time'],
        'supplier_id': selected_price_break['supplier_id'],
        'supplier_name': selected_price_break['supplier_name'],
        'price_list_item_id': selected_price_break['id'],
        'price_list_id': selected_price_break['price_list_id'],
        'next_break_quantity': next_break_quantity
    }


@price_lists_bp.route('/', methods=['GET'])
def get_all_price_lists():
    """Get all price lists"""
    try:
        price_lists = db_execute(
            '''
            SELECT pl.*, s.name as supplier_name
            FROM price_lists pl
            JOIN suppliers s ON pl.supplier_id = s.id
            ''',
            fetch='all'
        ) or []

        price_lists = [dict(row) for row in price_lists]

        for price_list in price_lists:
            price_list.setdefault('currency_code', str(price_list.get('currency_id', 'N/A')))
            count_row = db_execute(
                '''
                SELECT COUNT(*) as count
                FROM price_list_items
                WHERE price_list_id = ?
                ''',
                (price_list['id'],),
                fetch='one'
            )
            price_list['item_count'] = count_row['count'] if count_row else 0

        suppliers = db_execute('SELECT id, name FROM suppliers', fetch='all') or []
        currencies_rows = db_execute('SELECT id, currency_code, symbol FROM currencies', fetch='all') or []
        currencies = [{
            'id': row['id'],
            'code': row.get('currency_code') or str(row['id']),
            'name': row.get('symbol', '')
        } for row in currencies_rows]

        return render_template('price_lists.html',
                               price_lists=price_lists,
                               suppliers=[dict(row) for row in suppliers],
                               currencies=currencies)
    except Exception as e:
        current_app.logger.error(f"Error getting price lists: {str(e)}")
        return f"""
        <html>
            <head><title>Error</title></head>
            <body>
                <h1>Error</h1>
                <p>Failed to retrieve price lists: {str(e)}</p>
                <p><a href="/">Back to home</a></p>
            </body>
        </html>
        """, 500


@price_lists_bp.route('/<int:price_list_id>', methods=['GET'])
def get_price_list(price_list_id):
    try:
        price_list_row = db_execute(
            '''
            SELECT pl.*, s.name as supplier_name, c.currency_code
            FROM price_lists pl
            JOIN suppliers s ON pl.supplier_id = s.id
            LEFT JOIN currencies c ON pl.currency_id = c.id
            WHERE pl.id = ?
            ''',
            (price_list_id,),
            fetch='one'
        )
        if not price_list_row:
            return jsonify({"status": "error", "message": "Price list not found"}), 404

        price_list = dict(price_list_row)
        for date_field in ['valid_from', 'valid_to']:
            if price_list.get(date_field) and hasattr(price_list[date_field], 'strftime'):
                price_list[date_field] = price_list[date_field].strftime('%Y-%m-%d')

        items = db_execute('SELECT * FROM price_list_items WHERE price_list_id = ?', (price_list_id,), fetch='all') or []
        items = [dict(row) for row in items]
        for item in items:
            price_breaks = db_execute(
                'SELECT * FROM price_breaks WHERE price_list_item_id = ? ORDER BY quantity',
                (item['id'],),
                fetch='all'
            ) or []
            item['price_breaks'] = [dict(pb) for pb in price_breaks]

        price_list['price_list_items'] = items

        suppliers = db_execute('SELECT id, name FROM suppliers ORDER BY name', fetch='all') or []
        currencies = db_execute('SELECT id, currency_code FROM currencies ORDER BY currency_code', fetch='all') or []

        return render_template('price_list_edit.html',
                               price_list=price_list,
                               suppliers=[dict(row) for row in suppliers],
                               currencies=[dict(row) for row in currencies])
    except Exception as e:
        current_app.logger.error(f"Error getting price list {price_list_id}: {str(e)}")
        return jsonify({"status": "error", "message": f"Failed to retrieve price list: {str(e)}"}), 500

@price_lists_bp.route('/', methods=['POST'])
def create_price_list():
    """Create a new price list"""
    # Get form data instead of JSON
    data = request.form
    required_fields = ['supplier_id', 'name_reference', 'currency_id']

    # Rest of the function stays the same
    for field in required_fields:
        if field not in data:
            return jsonify({
                'status': 'error',
                'message': f'Missing required field: {field}'
            }), 400

    valid_from = data.get('valid_from')
    valid_to = data.get('valid_to')

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                '''
                INSERT INTO price_lists (supplier_id, valid_from, valid_to, name_reference, currency_id)
                VALUES (?, ?, ?, ?, ?)
                RETURNING id
                ''',
                (
                    data['supplier_id'],
                    valid_from,
                    valid_to,
                    data['name_reference'],
                    data['currency_id']
                )
            )
            row = cur.fetchone()
            price_list_id = _get_inserted_id(row, cur)

        return jsonify({
            'status': 'success',
            'message': 'Price list created successfully',
            'data': {'id': price_list_id}
        }), 201
    except Exception as e:
        current_app.logger.error(f"Error creating price list: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to create price list'
        }), 500


@price_lists_bp.route('/<int:price_list_id>', methods=['PUT'])
def update_price_list(price_list_id):
    """Update an existing price list"""
    data = request.json

    try:
        existing = db_execute('SELECT id FROM price_lists WHERE id = ?', (price_list_id,), fetch='one')
        if not existing:
            return jsonify({
                'status': 'error',
                'message': 'Price list not found'
            }), 404

        update_fields = []
        params = []
        for field in ['supplier_id', 'valid_from', 'valid_to', 'name_reference', 'currency_id']:
            if field in data:
                update_fields.append(f'{field} = ?')
                params.append(data[field])

        if not update_fields:
            return jsonify({
                'status': 'error',
                'message': 'No fields to update'
            }), 400

        params.append(price_list_id)
        query = f'''
            UPDATE price_lists
            SET {', '.join(update_fields)}
            WHERE id = ?
        '''
        db_execute(query, tuple(params), commit=True)

        return jsonify({
            'status': 'success',
            'message': 'Price list updated successfully'
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error updating price list {price_list_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to update price list'
        }), 500


@price_lists_bp.route('/<int:price_list_id>', methods=['DELETE'])
def delete_price_list(price_list_id):
    """Delete a price list and all associated items and price breaks"""
    try:
        existing = db_execute('SELECT id FROM price_lists WHERE id = ?', (price_list_id,), fetch='one')
        if not existing:
            return jsonify({
                'status': 'error',
                'message': 'Price list not found'
            }), 404

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                '''
                DELETE FROM price_breaks
                WHERE price_list_item_id IN (
                    SELECT id FROM price_list_items WHERE price_list_id = ?
                )
                ''',
                (price_list_id,)
            )
            _execute_with_cursor(cur, 'DELETE FROM price_list_items WHERE price_list_id = ?', (price_list_id,))
            _execute_with_cursor(cur, 'DELETE FROM price_lists WHERE id = ?', (price_list_id,))

        return jsonify({
            'status': 'success',
            'message': 'Price list deleted successfully'
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error deleting price list {price_list_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to delete price list'
        }), 500


@price_lists_bp.route('/<int:price_list_id>/items', methods=['GET'])
def get_price_list_items(price_list_id):
    """Get all items for a specific price list"""
    try:
        exists = db_execute('SELECT id FROM price_lists WHERE id = ?', (price_list_id,), fetch='one')
        if not exists:
            return jsonify({
                'status': 'error',
                'message': 'Price list not found'
            }), 404

        items = db_execute('''
            SELECT pli.id, pli.part_number, pli.base_part_number, pli.lead_time
            FROM price_list_items pli
            WHERE pli.price_list_id = ?
            ORDER BY pli.part_number
        ''', (price_list_id,), fetch='all') or []
        items = [dict(item) for item in items]

        for item in items:
            price_breaks = db_execute('''
                SELECT pb.id, pb.quantity, pb.price
                FROM price_breaks pb
                WHERE pb.price_list_item_id = ?
                ORDER BY pb.quantity ASC
            ''', (item['id'],), fetch='all') or []
            item['price_breaks'] = [dict(row) for row in price_breaks]

        return jsonify({
            'status': 'success',
            'data': items
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error getting items for price list {price_list_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to retrieve price list items'
        }), 500


@price_lists_bp.route('/<int:price_list_id>/items', methods=['POST'])
def add_price_list_item(price_list_id):
    """Add a new item to a price list with its price breaks"""
    data = request.json
    required_fields = ['part_number', 'base_part_number', 'lead_time', 'price_breaks']

    # Validate required fields
    for field in required_fields:
        if field not in data:
            return jsonify({
                'status': 'error',
                'message': f'Missing required field: {field}'
            }), 400

    # Validate price breaks
    if not isinstance(data['price_breaks'], list) or not data['price_breaks']:
        return jsonify({
            'status': 'error',
            'message': 'Price breaks must be a non-empty list'
        }), 400

    for price_break in data['price_breaks']:
        if 'quantity' not in price_break or 'price' not in price_break:
            return jsonify({
                'status': 'error',
                'message': 'Each price break must have quantity and price'
            }), 400

    existing = db_execute('SELECT id FROM price_lists WHERE id = ?', (price_list_id,), fetch='one')
    if not existing:
        return jsonify({
            'status': 'error',
            'message': 'Price list not found'
        }), 404

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                '''
                INSERT INTO price_list_items (price_list_id, part_number, base_part_number, lead_time)
                VALUES (?, ?, ?, ?)
                RETURNING id
                ''',
                (
                    price_list_id,
                    data['part_number'],
                    data['base_part_number'],
                    data['lead_time']
                )
            )
            row = cur.fetchone()
            item_id = _get_inserted_id(row, cur)

            for price_break in data['price_breaks']:
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO price_breaks (price_list_item_id, quantity, price)
                    VALUES (?, ?, ?)
                    ''',
                    (item_id, price_break['quantity'], price_break['price'])
                )

        return jsonify({
            'status': 'success',
            'message': 'Price list item added successfully',
            'data': {'id': item_id}
        }), 201
    except Exception as e:
        current_app.logger.error(f"Error adding item to price list {price_list_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to add price list item'
        }), 500


@price_lists_bp.route('/<int:price_list_id>/items/<int:item_id>', methods=['PUT'])
def update_price_list_item(price_list_id, item_id):
    """Update a price list item and its price breaks"""
    data = request.json

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                '''
                SELECT id FROM price_list_items
                WHERE id = ? AND price_list_id = ?
                ''',
                (item_id, price_list_id)
            )
            if not cur.fetchone():
                return jsonify({
                    'status': 'error',
                    'message': 'Price list item not found or does not belong to the specified price list'
                }), 404

            update_fields = []
            params = []
            if 'part_number' in data:
                update_fields.append('part_number = ?')
                params.append(data['part_number'])

            if 'base_part_number' in data:
                update_fields.append('base_part_number = ?')
                params.append(data['base_part_number'])

            if 'lead_time' in data:
                update_fields.append('lead_time = ?')
                params.append(data['lead_time'])

            if update_fields:
                params.append(item_id)
                _execute_with_cursor(
                    cur,
                    f'''
                    UPDATE price_list_items
                    SET {', '.join(update_fields)}
                    WHERE id = ?
                    ''',
                    tuple(params)
                )

            if 'price_breaks' in data and isinstance(data['price_breaks'], list):
                _execute_with_cursor(
                    cur,
                    'DELETE FROM price_breaks WHERE price_list_item_id = ?',
                    (item_id,)
                )
                for price_break in data['price_breaks']:
                    if 'quantity' in price_break and 'price' in price_break:
                        _execute_with_cursor(
                            cur,
                            '''
                            INSERT INTO price_breaks (price_list_item_id, quantity, price)
                            VALUES (?, ?, ?)
                            ''',
                            (
                                item_id,
                                price_break['quantity'],
                                price_break['price']
                            )
                        )

        return jsonify({
            'status': 'success',
            'message': 'Price list item updated successfully'
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error updating price list item {item_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to update price list item'
        }), 500


@price_lists_bp.route('/<int:price_list_id>/items/<int:item_id>', methods=['DELETE'])
def delete_price_list_item(price_list_id, item_id):
    """Delete a price list item and its price breaks"""
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                '''
                SELECT id FROM price_list_items
                WHERE id = ? AND price_list_id = ?
                ''',
                (item_id, price_list_id)
            )
            if not cur.fetchone():
                return jsonify({
                    'status': 'error',
                    'message': 'Price list item not found or does not belong to the specified price list'
                }), 404

            _execute_with_cursor(
                cur,
                'DELETE FROM price_breaks WHERE price_list_item_id = ?',
                (item_id,)
            )
            _execute_with_cursor(
                cur,
                'DELETE FROM price_list_items WHERE id = ?',
                (item_id,)
            )

        return jsonify({
            'status': 'success',
            'message': 'Price list item deleted successfully'
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error deleting price list item {item_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to delete price list item'
        }), 500


@price_lists_bp.route('/lookup', methods=['POST'])
def lookup_price():
    """Lookup the price for a given part number and quantity"""
    data = request.json

    if not data or 'base_part_number' not in data or 'quantity' not in data:
        return jsonify({
            'status': 'error',
            'message': 'Missing required fields: base_part_number and quantity'
        }), 400

    base_part_number = data['base_part_number']
    quantity = data['quantity']

    # Validate quantity
    try:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError("Quantity must be positive")
    except ValueError:
        return jsonify({
            'status': 'error',
            'message': 'Quantity must be a positive integer'
        }), 400

    price_data = get_price_list_price(base_part_number, quantity)

    if not price_data:
        return jsonify({
            'status': 'error',
            'message': 'No price information found for the specified part and quantity'
        }), 404

    return jsonify({
        'status': 'success',
        'data': price_data
    }), 200


@price_lists_bp.route('/upload', methods=['POST'])
def upload_price_list():
    """Upload a CSV file to create a new price list or update an existing one"""
    if 'file' not in request.files:
        return jsonify({
            'status': 'error',
            'message': 'No file provided'
        }), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({
            'status': 'error',
            'message': 'No file selected'
        }), 400

    if not file.filename.endswith('.csv'):
        return jsonify({
            'status': 'error',
            'message': 'Only CSV files are supported'
        }), 400

    # Get form data
    supplier_id = request.form.get('supplier_id')
    currency_id = request.form.get('currency_id')
    name_reference = request.form.get('name_reference')
    valid_from = request.form.get('valid_from')
    valid_to = request.form.get('valid_to')

    if not supplier_id or not currency_id or not name_reference:
        return jsonify({
            'status': 'error',
            'message': 'Missing required fields: supplier_id, currency_id, and name_reference'
        }), 400

    try:
        supplier_id = int(supplier_id)
        currency_id = int(currency_id)
    except ValueError:
        return jsonify({
            'status': 'error',
            'message': 'supplier_id and currency_id must be integers'
        }), 400

    # Save file temporarily
    filename = secure_filename(file.filename)
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)

    expected_headers = ['part_number', 'base_part_number', 'lead_time']
    processed_items = 0

    try:
        with open(file_path, 'r', newline='') as csvfile:
            csv_reader = csv.DictReader(csvfile)
            headers = csv_reader.fieldnames
            if not headers or not all(header in headers for header in expected_headers):
                return jsonify({
                    'status': 'error',
                    'message': 'CSV file must contain part_number, base_part_number, and lead_time columns'
                }), 400

            qty_columns = [col for col in headers if col.startswith('qty_')]
            price_columns = [col for col in headers if col.startswith('price_')]
            if not qty_columns or not price_columns or len(qty_columns) != len(price_columns):
                return jsonify({
                    'status': 'error',
                    'message': 'CSV file must contain matching qty_X and price_X columns'
                }), 400

            qty_columns.sort()
            price_columns.sort()

            with db_cursor(commit=True) as cur:
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO price_lists (supplier_id, valid_from, valid_to, name_reference, currency_id)
                    VALUES (?, ?, ?, ?, ?)
                    RETURNING id
                    ''',
                    (supplier_id, valid_from, valid_to, name_reference, currency_id)
                )
                row = cur.fetchone()
                price_list_id = _get_inserted_id(row, cur)

                for row in csv_reader:
                    if not row['part_number'] or not row['base_part_number']:
                        continue

                    try:
                        lead_time = int(row['lead_time']) if row['lead_time'] else 0
                    except ValueError:
                        lead_time = 0

                    _execute_with_cursor(
                        cur,
                        '''
                        INSERT INTO price_list_items (price_list_id, part_number, base_part_number, lead_time)
                        VALUES (?, ?, ?, ?)
                        RETURNING id
                        ''',
                        (price_list_id, row['part_number'], row['base_part_number'], lead_time)
                    )
                    item_row = cur.fetchone()
                    item_id = _get_inserted_id(item_row, cur)

                    for qty_col, price_col in zip(qty_columns, price_columns):
                        qty_value = row[qty_col]
                        price_value = row[price_col]

                        if not qty_value or not price_value:
                            continue

                        try:
                            qty = int(qty_value)
                            price = float(price_value)
                            _execute_with_cursor(
                                cur,
                                '''
                                INSERT INTO price_breaks (price_list_item_id, quantity, price)
                                VALUES (?, ?, ?)
                                ''',
                                (item_id, qty, price)
                            )
                        except ValueError:
                            current_app.logger.warning(f"Skipped invalid price break: {qty_value}, {price_value}")

                    processed_items += 1

        return jsonify({
            'status': 'success',
            'message': f'Price list uploaded successfully with {processed_items} items',
            'data': {'price_list_id': price_list_id}
        }), 201
    except Exception as e:
        current_app.logger.error(f"Error uploading price list: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to upload price list'
        }), 500
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


@price_lists_bp.route('/search', methods=['GET'])
def search_price_lists():
    """Search price lists based on query parameters"""
    supplier_id = request.args.get('supplier_id')
    part_number = request.args.get('part_number')
    base_part_number = request.args.get('base_part_number')

    query = '''
        SELECT DISTINCT pl.id, pl.name_reference, pl.valid_from, pl.valid_to,
               s.name as supplier_name, c.code as currency_code
        FROM price_lists pl
        JOIN suppliers s ON pl.supplier_id = s.id
        JOIN currencies c ON pl.currency_id = c.id
    '''

    params: list = []
    where_clauses: list = []

    if supplier_id:
        where_clauses.append('pl.supplier_id = ?')
        params.append(supplier_id)

    if part_number or base_part_number:
        query += ' JOIN price_list_items pli ON pl.id = pli.price_list_id'

        if part_number:
            where_clauses.append('pli.part_number LIKE ?')
            params.append(f'%{part_number}%')

        if base_part_number:
            where_clauses.append('pli.base_part_number LIKE ?')
            params.append(f'%{base_part_number}%')

    if where_clauses:
        query += ' WHERE ' + ' AND '.join(where_clauses)

    query += ' ORDER BY pl.valid_from DESC'

    try:
        price_lists = db_execute(query, tuple(params), fetch='all') or []
        price_lists = [dict(row) for row in price_lists]
        for price_list in price_lists:
            if price_list['valid_from']:
                price_list['valid_from'] = price_list['valid_from'].strftime('%Y-%m-%d')
            if price_list['valid_to']:
                price_list['valid_to'] = price_list['valid_to'].strftime('%Y-%m-%d')

        return jsonify({
            'status': 'success',
            'data': price_lists
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error searching price lists: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to search price lists'
        }), 500


@price_lists_bp.route('/bulk-upload', methods=['POST'])
def bulk_upload_price_breaks():
    """Bulk upload price breaks from a CSV file for an existing price list"""
    if 'file' not in request.files:
        return jsonify({
            'status': 'error',
            'message': 'No file provided'
        }), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({
            'status': 'error',
            'message': 'No file selected'
        }), 400

    if not file.filename.endswith('.csv'):
        return jsonify({
            'status': 'error',
            'message': 'Only CSV files are supported'
        }), 400

    # Get price list ID
    price_list_id = request.form.get('price_list_id')
    if not price_list_id:
        return jsonify({
            'status': 'error',
            'message': 'Missing required field: price_list_id'
        }), 400

    try:
        price_list_id = int(price_list_id)
    except ValueError:
        return jsonify({
            'status': 'error',
            'message': 'price_list_id must be an integer'
        }), 400

    # Save file temporarily
    filename = secure_filename(file.filename)
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)

    exists = db_execute('SELECT id FROM price_lists WHERE id = ?', (price_list_id,), fetch='one')
    if not exists:
        if os.path.exists(file_path):
            os.remove(file_path)
        return jsonify({
            'status': 'error',
            'message': 'Price list not found'
        }), 404

    processed_items = 0
    skipped_items = 0

    try:
        with open(file_path, 'r', newline='') as csvfile:
            csv_reader = csv.DictReader(csvfile)
            headers = csv_reader.fieldnames or []
            if not (
                    ('part_number' in headers) or ('base_part_number' in headers)) or 'quantity' not in headers or 'price' not in headers:
                return jsonify({
                    'status': 'error',
                    'message': 'CSV file must contain part_number or base_part_number, quantity, and price columns'
                }), 400

            with db_cursor(commit=True) as cur:
                for row in csv_reader:
                    part_identifier = row.get('part_number') or row.get('base_part_number')
                    quantity = row.get('quantity')
                    price = row.get('price')

                    if not part_identifier or not quantity or not price:
                        skipped_items += 1
                        continue

                    try:
                        quantity = int(quantity)
                        price = float(price)
                    except ValueError:
                        skipped_items += 1
                        continue

                    if row.get('part_number'):
                        identifier_query = '''
                            SELECT id FROM price_list_items
                            WHERE price_list_id = ? AND part_number = ?
                        '''
                    else:
                        identifier_query = '''
                            SELECT id FROM price_list_items
                            WHERE price_list_id = ? AND base_part_number = ?
                        '''

                    _execute_with_cursor(cur, identifier_query, (price_list_id, part_identifier))
                    item = cur.fetchone()
                    if not item:
                        skipped_items += 1
                        continue

                    item_id = item['id']

                    _execute_with_cursor(
                        cur,
                        '''
                        SELECT id FROM price_breaks
                        WHERE price_list_item_id = ? AND quantity = ?
                        ''',
                        (item_id, quantity)
                    )
                    existing = cur.fetchone()

                    if existing:
                        _execute_with_cursor(
                            cur,
                            '''
                            UPDATE price_breaks
                            SET price = ?
                            WHERE id = ?
                            ''',
                            (price, existing['id'])
                        )
                    else:
                        _execute_with_cursor(
                            cur,
                            '''
                            INSERT INTO price_breaks (price_list_item_id, quantity, price)
                            VALUES (?, ?, ?)
                            ''',
                            (item_id, quantity, price)
                        )

                    processed_items += 1

        return jsonify({
            'status': 'success',
            'message': f'Price breaks uploaded: {processed_items} processed, {skipped_items} skipped'
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error uploading price breaks: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to upload price breaks'
        }), 500
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


@price_lists_bp.route('/statistics', methods=['GET'])
def get_price_list_statistics():
    """Get statistics about price lists"""
    today = datetime.date.today().strftime('%Y-%m-%d')
    try:
        with db_cursor() as cur:
            _execute_with_cursor(cur, 'SELECT COUNT(*) as total_price_lists FROM price_lists')
            total_price_lists = cur.fetchone()['total_price_lists']

            _execute_with_cursor(cur, 'SELECT COUNT(*) as total_items FROM price_list_items')
            total_items = cur.fetchone()['total_items']

            _execute_with_cursor(cur, 'SELECT COUNT(*) as total_price_breaks FROM price_breaks')
            total_price_breaks = cur.fetchone()['total_price_breaks']

            _execute_with_cursor(
                cur,
                '''
                SELECT s.name as supplier_name, COUNT(pl.id) as price_list_count
                FROM suppliers s
                LEFT JOIN price_lists pl ON s.id = pl.supplier_id
                GROUP BY s.id
                ORDER BY price_list_count DESC
                '''
            )
            price_lists_by_supplier = [dict(row) for row in cur.fetchall()]

            _execute_with_cursor(
                cur,
                '''
                SELECT COUNT(*) as active_price_lists
                FROM price_lists
                WHERE (valid_from IS NULL OR valid_from <= ?)
                  AND (valid_to IS NULL OR valid_to >= ?)
                ''',
                (today, today)
            )
            active_price_lists = cur.fetchone()['active_price_lists']

            _execute_with_cursor(
                cur,
                '''
                SELECT pli.part_number, pli.base_part_number, 
                       COUNT(pb.id) as price_break_count,
                       s.name as supplier_name
                FROM price_list_items pli
                JOIN price_lists pl ON pli.price_list_id = pl.id
                JOIN suppliers s ON pl.supplier_id = s.id
                JOIN price_breaks pb ON pli.id = pb.price_list_item_id
                GROUP BY pli.id
                ORDER BY price_break_count DESC
                LIMIT 10
                '''
            )
            top_parts = [dict(row) for row in cur.fetchall()]

            _execute_with_cursor(
                cur,
                '''
                SELECT 
                    MIN(pb.price) as min_price,
                    MAX(pb.price) as max_price,
                    AVG(pb.price) as avg_price,
                    COUNT(DISTINCT pl.currency_id) as currency_count
                FROM price_breaks pb
                JOIN price_list_items pli ON pb.price_list_item_id = pli.id
                JOIN price_lists pl ON pli.price_list_id = pl.id
                '''
            )
            price_stats_row = cur.fetchone()
            price_stats = dict(price_stats_row) if price_stats_row else {}

            _execute_with_cursor(
                cur,
                '''
                SELECT pl.id, pl.name_reference, s.name as supplier_name,
                       pl.valid_from, pl.valid_to
                FROM price_lists pl
                JOIN suppliers s ON pl.supplier_id = s.id
                ORDER BY pl.id DESC
                LIMIT 5
                '''
            )
            recent_price_lists = [dict(row) for row in cur.fetchall()]

            for price_list in recent_price_lists:
                if price_list['valid_from'] and hasattr(price_list['valid_from'], 'strftime'):
                    price_list['valid_from'] = price_list['valid_from'].strftime('%Y-%m-%d')
                if price_list['valid_to'] and hasattr(price_list['valid_to'], 'strftime'):
                    price_list['valid_to'] = price_list['valid_to'].strftime('%Y-%m-%d')

            expiring_count = get_expiring_price_lists_count(cur, today)

        return render_template('price_list_statistics.html',
                               total_price_lists=total_price_lists,
                               total_items=total_items,
                               total_price_breaks=total_price_breaks,
                               active_price_lists=active_price_lists,
                               price_lists_by_supplier=price_lists_by_supplier,
                               top_parts=top_parts,
                               price_stats=price_stats,
                               recent_price_lists=recent_price_lists,
                               expiring_soon_count=expiring_count)
    except Exception as e:
        current_app.logger.error(f"Error getting price list statistics: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Failed to retrieve price list statistics: {str(e)}'
        }), 500


def get_expiring_price_lists_count(cursor, today):
    """Get count of price lists expiring in the next 30 days"""
    thirty_days_later = (datetime.datetime.strptime(today, '%Y-%m-%d') +
                         datetime.timedelta(days=30)).strftime('%Y-%m-%d')

    _execute_with_cursor(
        cursor,
        '''
        SELECT COUNT(*) as expiring_count
        FROM price_lists
        WHERE valid_to BETWEEN ? AND ?
        ''',
        (today, thirty_days_later)
    )
    row = cursor.fetchone()
    return row['expiring_count'] if row else 0

@price_lists_bp.route('/<int:price_list_id>/handsontable', methods=['GET'])
def handsontable_editor(price_list_id):
    """Render the Handsontable editor for a price list"""
    try:
        price_list = db_execute(
            '''
            SELECT pl.*, s.name as supplier_name, 
                   c.currency_code as currency_code
            FROM price_lists pl
            JOIN suppliers s ON pl.supplier_id = s.id
            LEFT JOIN currencies c ON pl.currency_id = c.id
            WHERE pl.id = ?
            ''',
            (price_list_id,),
            fetch='one'
        )
        if not price_list:
            return jsonify({"status": "error", "message": "Price list not found"}), 404

        for date_field in ['valid_from', 'valid_to']:
            if price_list.get(date_field) and hasattr(price_list[date_field], 'strftime'):
                price_list[date_field] = price_list[date_field].strftime('%Y-%m-%d')

        return render_template('price_list_handsontable.html', price_list=price_list)
    except Exception as e:
        current_app.logger.error(f"Error getting price list {price_list_id}: {str(e)}")
        return jsonify({"status": "error", "message": f"Failed to retrieve price list: {str(e)}"}), 500


@price_lists_bp.route('/<int:price_list_id>/api/items_with_price_breaks', methods=['GET'])
def get_items_with_price_breaks(price_list_id):
    """API endpoint to get all items with their price breaks for a specific price list"""
    try:
        exists = db_execute('SELECT id FROM price_lists WHERE id = ?', (price_list_id,), fetch='one')
        if not exists:
            return jsonify({
                'status': 'error',
                'message': 'Price list not found'
            }), 404

        items = db_execute('''
            SELECT id, part_number, base_part_number, lead_time
            FROM price_list_items
            WHERE price_list_id = ?
            ORDER BY part_number
        ''', (price_list_id,), fetch='all') or []

        formatted_items = []
        for item in items:
            price_breaks = db_execute('''
                SELECT id, quantity, price
                FROM price_breaks
                WHERE price_list_item_id = ?
                ORDER BY quantity ASC
            ''', (item['id'],), fetch='all') or []
            item['price_breaks'] = [dict(row) for row in price_breaks]
            formatted_items.append(dict(item))

        return jsonify({
            'status': 'success',
            'data': formatted_items
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error getting items for price list {price_list_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Failed to retrieve price list items: {str(e)}'
        }), 500


@price_lists_bp.route('/<int:price_list_id>/api/batch_update_items', methods=['POST'])
def batch_update_items(price_list_id):
    """API endpoint to batch update items and their price breaks"""
    data = request.json
    if not data or 'items' not in data or not isinstance(data['items'], list):
        return jsonify({
            'status': 'error',
            'message': 'Invalid request data'
        }), 400

    exists = db_execute('SELECT id FROM price_lists WHERE id = ?', (price_list_id,), fetch='one')
    if not exists:
        return jsonify({
            'status': 'error',
            'message': 'Price list not found'
        }), 404

    updated_items = []
    created_items = []

    try:
        with db_cursor(commit=True) as cur:
            for item_data in data['items']:
                if 'part_number' not in item_data or 'base_part_number' not in item_data:
                    continue

                lead_time = item_data.get('lead_time', 0)
                if lead_time is None or lead_time == '':
                    lead_time = 0

                try:
                    lead_time = int(lead_time)
                except (ValueError, TypeError):
                    lead_time = 0

                if 'id' in item_data and item_data['id']:
                    item_id = item_data['id']
                    _execute_with_cursor(
                        cur,
                        '''
                        SELECT id FROM price_list_items
                        WHERE id = ? AND price_list_id = ?
                        ''',
                        (item_id, price_list_id)
                    )
                    if not cur.fetchone():
                        continue

                    _execute_with_cursor(
                        cur,
                        '''
                        UPDATE price_list_items
                        SET part_number = ?, base_part_number = ?, lead_time = ?
                        WHERE id = ?
                        ''',
                        (
                            item_data['part_number'],
                            item_data['base_part_number'],
                            lead_time,
                            item_id
                        )
                    )
                    updated_items.append(item_id)
                else:
                    _execute_with_cursor(
                        cur,
                        '''
                        INSERT INTO price_list_items (price_list_id, part_number, base_part_number, lead_time)
                        VALUES (?, ?, ?, ?)
                        RETURNING id
                        ''',
                        (
                            price_list_id,
                            item_data['part_number'],
                            item_data['base_part_number'],
                            lead_time
                        )
                    )
                    row = cur.fetchone()
                    item_id = _get_inserted_id(row, cur)
                    created_items.append(item_id)

                if 'price_breaks' in item_data and isinstance(item_data['price_breaks'], list):
                    _execute_with_cursor(
                        cur,
                        'DELETE FROM price_breaks WHERE price_list_item_id = ?',
                        (item_id,)
                    )

                    for price_break in item_data['price_breaks']:
                        if 'quantity' in price_break and 'price' in price_break:
                            try:
                                quantity = int(price_break['quantity'])
                                price = float(price_break['price'])

                                _execute_with_cursor(
                                    cur,
                                    '''
                                    INSERT INTO price_breaks (price_list_item_id, quantity, price)
                                    VALUES (?, ?, ?)
                                    ''',
                                    (item_id, quantity, price)
                                )
                            except (ValueError, TypeError):
                                continue

        return jsonify({
            'status': 'success',
            'message': f'Updated {len(updated_items)} items, created {len(created_items)} items'
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error updating items for price list {price_list_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Failed to update items: {str(e)}'
        }), 500


@price_lists_bp.route('/<int:price_list_id>/api/items/<int:item_id>', methods=['DELETE'])
def delete_item_api(price_list_id, item_id):
    """API endpoint to delete a price list item"""
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                '''
                SELECT id FROM price_list_items
                WHERE id = ? AND price_list_id = ?
                ''',
                (item_id, price_list_id)
            )
            if not cur.fetchone():
                return jsonify({
                    'status': 'error',
                    'message': 'Item not found or does not belong to this price list'
                }), 404

            _execute_with_cursor(
                cur,
                'DELETE FROM price_breaks WHERE price_list_item_id = ?',
                (item_id,)
            )
            _execute_with_cursor(
                cur,
                'DELETE FROM price_list_items WHERE id = ?',
                (item_id,)
            )

        return jsonify({
            'status': 'success',
            'message': 'Item deleted successfully'
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error deleting item {item_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Failed to delete item: {str(e)}'
        }), 500
