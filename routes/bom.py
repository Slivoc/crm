from flask import Blueprint, render_template, jsonify, request, redirect, url_for
from db import db_cursor, execute as db_execute
from models import create_base_part_number
import logging
import pandas as pd
from werkzeug.utils import secure_filename
import os


def _using_postgres() -> bool:
    return os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://'))


def _prepare_query(query: str) -> str:
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _with_returning_clause(query: str) -> str:
    trimmed = query.strip().rstrip(';')
    if not _using_postgres():
        return query
    return f"{trimmed} RETURNING id"


def _fetch_inserted_id(cur):
    if _using_postgres():
        row = cur.fetchone()
        if row:
            return row.get('id') if isinstance(row, dict) else row[0]
        return None
    return getattr(cur, 'lastrowid', None)

bom_bp = Blueprint('bom', __name__, url_prefix='/bom')


@bom_bp.route('/')
def boms():
    # Get all BOMs with their details
    customer_names_expr = (
        "STRING_AGG(DISTINCT c.name, ', ')" if _using_postgres() else "GROUP_CONCAT(DISTINCT c.name)"
    )
    boms = db_execute(f'''
        SELECT bh.*,
               COUNT(DISTINCT bl.id) as components_count,
               COUNT(DISTINCT cb.customer_id) as customers_count,
               {customer_names_expr} as customer_names
        FROM bom_headers bh
        LEFT JOIN bom_lines bl ON bh.id = bl.bom_header_id
        LEFT JOIN customer_boms cb ON bh.id = cb.bom_header_id
        LEFT JOIN customers c ON cb.customer_id = c.id
        WHERE bh.type = 'kit'
        GROUP BY bh.id
        ORDER BY bh.created_at DESC
    ''', fetch='all')

    return render_template('bom/boms.html', boms=boms)


@bom_bp.route('/create', methods=['POST'])
def create_bom():
    try:
        with db_cursor(commit=True) as cur:
            insert_query = _with_returning_clause('''
                INSERT INTO bom_headers (name, description, type)
                VALUES (?, ?, 'kit')
            ''')
            _execute_with_cursor(cur, insert_query, [
                request.form['name'],
                request.form.get('description')
            ])
            bom_id = _fetch_inserted_id(cur)

            if not bom_id:
                raise RuntimeError("Failed to create BOM header")

            if 'file' in request.files:
                file = request.files['file']
                if file.filename:
                    filename = secure_filename(file.filename)

                    if filename.endswith('.csv'):
                        df = pd.read_csv(file)
                    elif filename.endswith(('.xlsx', '.xls')):
                        df = pd.read_excel(file)
                    else:
                        raise ValueError("Unsupported file format")

                    for idx, row in df.iterrows():
                        raw_part_number = str(row.get('part_number', '')).strip()
                        if not raw_part_number:
                            continue

                        base_part_number = create_base_part_number(raw_part_number)
                        quantity = int(row.get('quantity', 1))
                        position = int(row.get('position', (idx + 1) * 10))
                        raw_guide_price = row.get('guide_price')
                        logging.info(f"Row {idx} - Part: {raw_part_number}")
                        logging.info(f"  Raw guide_price value: {raw_guide_price}")
                        logging.info(f"  Type: {type(raw_guide_price)}")
                        logging.info(f"  pd.notna(): {pd.notna(raw_guide_price)}")
                        guide_price = float(raw_guide_price) if pd.notna(raw_guide_price) else None
                        logging.info(f"  Final guide_price: {guide_price}")

                        part = _execute_with_cursor(cur,
                            'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
                            [base_part_number]
                        ).fetchone()

                        if not part:
                            _execute_with_cursor(cur,
                                'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                                [raw_part_number, base_part_number]
                            )

                        _execute_with_cursor(cur, '''
                            INSERT INTO bom_lines (
                                bom_header_id, base_part_number, quantity,
                                reference_designator, notes, position, guide_price
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', [
                            bom_id,
                            base_part_number,
                            quantity,
                            row.get('reference_designator'),
                            row.get('notes'),
                            position,
                            guide_price
                        ])
                        logging.info(f"  Inserted with guide_price: {guide_price}")

        return redirect(url_for('bom.view_bom', bom_id=bom_id))

    except Exception as e:
        logging.error(f"Error creating BOM: {str(e)}", exc_info=True)
        return str(e), 400


@bom_bp.route('/view/<int:bom_id>')
def view_bom(bom_id):
    logging.debug(f"Viewing BOM {bom_id}")
    try:
        bom_row = db_execute('''
            SELECT bh.*,
                   COALESCE(COUNT(DISTINCT bl.id), 0) as components_count,
                   COALESCE(COUNT(DISTINCT cb.customer_id), 0) as customers_count
            FROM bom_headers bh
            LEFT JOIN bom_lines bl ON bh.id = bl.bom_header_id
            LEFT JOIN customer_boms cb ON bh.id = cb.bom_header_id
            WHERE bh.id = ?
            GROUP BY bh.id
        ''', (bom_id,), fetch='one')

        if not bom_row:
            logging.warning(f"BOM {bom_id} not found")
            return "BOM not found", 404

        bom = {k: (v if v is not None else '') for k, v in dict(bom_row).items()}
        logging.debug(f"BOM details: {bom}")

        lines_rows = db_execute('''
            SELECT 
                bl.id,
                bl.base_part_number,
                COALESCE(bl.quantity, 0) as quantity,
                COALESCE(bl.position, 0) as position,
                COALESCE(bl.guide_price, 0) as guide_price,
                pn.part_number,
                bp.offer_line_id,
                COALESCE(ol.price, 0) as current_price,
                ol.lead_time as current_lead_time,
                s.name as supplier_name,
                c.currency_code
            FROM bom_lines bl
            LEFT JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
            LEFT JOIN bom_pricing bp ON bl.id = bp.bom_line_id
            LEFT JOIN offer_lines ol ON bp.offer_line_id = ol.id
            LEFT JOIN offers o ON ol.offer_id = o.id
            LEFT JOIN suppliers s ON o.supplier_id = s.id
            LEFT JOIN currencies c ON o.currency_id = c.id
            WHERE bl.bom_header_id = ?
            ORDER BY bl.position, bl.id
        ''', (bom_id,), fetch='all')

        components = []
        for row in lines_rows:
            row_dict = dict(row)
            components.append({
                'base_part_number': row_dict.get('part_number') or row_dict.get('base_part_number') or '',
                'quantity': int(row_dict.get('quantity', 0) or 0),
                'position': int(row_dict.get('position', 0) or 0),
                'guide_price': float(row_dict.get('guide_price', 0) or 0),
                'current_price': float(row_dict.get('current_price', 0) or 0),
                'supplier_name': row_dict.get('supplier_name', ''),
                'currency_code': row_dict.get('currency_code', ''),
                'lead_time': row_dict.get('current_lead_time', '')
            })

        logging.debug(f"Processed {len(components)} components")

        customer_rows = db_execute('''
            SELECT c.*, cb.reference
            FROM customers c
            JOIN customer_boms cb ON c.id = cb.customer_id
            WHERE cb.bom_header_id = ?
        ''', (bom_id,), fetch='all')

        customers = [{k: (v if v is not None else '') for k, v in dict(row).items()}
                     for row in customer_rows]

        logging.debug(f"Found {len(customers)} customers")

        return render_template('bom/view_bom.html',
                               bom=bom,
                               components=components,
                               customers=customers)

    except Exception as e:
        logging.error(f"Error viewing BOM {bom_id}: {str(e)}", exc_info=True)
        return f"Error loading BOM: {str(e)}", 500


@bom_bp.route('/import_components/<int:bom_id>', methods=['POST'])
def import_components(bom_id):
    """Handle file upload for importing components into existing BOM"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    filename = secure_filename(file.filename)
    logging.info(f"Starting import for BOM {bom_id} from file: {filename}")

    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file)
        else:
            return jsonify({'error': 'Unsupported file format. Use CSV or Excel'}), 400
    except Exception as exc:
        logging.error(f"Failed to read file for BOM {bom_id}: {exc}")
        return jsonify({'error': f"Failed to read file: {exc}"}), 400

    logging.info(f"Loaded dataframe with {len(df)} rows")
    logging.info(f"Columns in file: {list(df.columns)}")

    imported_count = 0
    skipped_count = 0

    with db_cursor(commit=True) as cur:
        max_position_row = _execute_with_cursor(cur, '''
            SELECT COALESCE(MAX(position), 0) as max_pos
            FROM bom_lines
            WHERE bom_header_id = ?
        ''', (bom_id,)).fetchone()
        max_position = max_position_row['max_pos'] if max_position_row else 0
        logging.info(f"Current max position: {max_position}")

        for idx, row in df.iterrows():
            logging.info(f"\n--- Processing Row {idx} ---")
            raw_part_number = str(row.get('part_number', '')).strip()
            logging.info(f"Raw part_number: '{raw_part_number}'")

            if not raw_part_number:
                logging.warning(f"Row {idx}: Skipping - empty part_number")
                skipped_count += 1
                continue

            try:
                base_part_number = create_base_part_number(raw_part_number)
                logging.info(f"Base part_number: '{base_part_number}'")

                raw_quantity = row.get('quantity', 1)
                quantity = int(raw_quantity)
                logging.info(f"Quantity: {quantity} (from {raw_quantity})")

                position = max_position + ((idx + 1) * 10)
                logging.info(f"Position: {position}")

                raw_guide_price = row.get('guide_price')
                logging.info(f"Raw guide_price value: {raw_guide_price}")
                logging.info(f"Raw guide_price type: {type(raw_guide_price)}")
                logging.info(f"pd.notna(guide_price): {pd.notna(raw_guide_price)}")
                logging.info(f"pd.isna(guide_price): {pd.isna(raw_guide_price)}")

                guide_price = float(raw_guide_price) if pd.notna(raw_guide_price) else None
                logging.info(f"Final guide_price for INSERT: {guide_price}")

                part = _execute_with_cursor(cur,
                    'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
                    (base_part_number,)
                ).fetchone()

                if not part:
                    logging.info(f"Part {base_part_number} not found, creating new part")
                    _execute_with_cursor(cur,
                        'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                        (raw_part_number, base_part_number)
                    )
                else:
                    logging.info(f"Part {base_part_number} already exists")

                ref_des = row.get('reference_designator')
                notes = row.get('notes')
                logging.info(f"Reference designator: {ref_des}")
                logging.info(f"Notes: {notes}")
                logging.info(f"  guide_price: {guide_price}")

                insert_line = _with_returning_clause('''
                    INSERT INTO bom_lines (
                        bom_header_id, base_part_number, quantity,
                        reference_designator, notes, position, guide_price
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''')
                _execute_with_cursor(cur, insert_line, (
                    bom_id,
                    base_part_number,
                    quantity,
                    ref_des,
                    notes,
                    position,
                    guide_price
                ))
                new_line_id = _fetch_inserted_id(cur)

                verify = _execute_with_cursor(cur,
                    'SELECT guide_price FROM bom_lines WHERE id = ?',
                    (new_line_id,)
                ).fetchone()
                logging.info(f"Verification - guide_price in DB: {verify['guide_price'] if verify else 'NOT FOUND'}")

                imported_count += 1

            except Exception as exc:
                logging.error(f"Failed to import row {idx}: {exc}", exc_info=True)
                skipped_count += 1

    logging.info(f"Successfully imported: {imported_count} components")
    logging.info(f"Skipped (empty part_number): {skipped_count} rows")

    return jsonify({
        'status': 'success',
        'message': f"Successfully imported {imported_count} components (skipped {skipped_count})"
    }), 200


@bom_bp.route('/api/customers/search')
def search_customers():
    search = request.args.get('q', '')
    customers = db_execute('''
        SELECT id, name 
        FROM customers 
        WHERE name LIKE ? 
        ORDER BY name 
        LIMIT 10
    ''', ('%' + search + '%',), fetch='all')

    return jsonify({
        'results': [{'id': c['id'], 'text': c['name']} for c in customers]
    })


@bom_bp.route('/update/<int:bom_id>', methods=['POST'])
def update_bom(bom_id):
    data = request.json or {}

    try:
        with db_cursor(commit=True) as cur:
            existing_lines = _execute_with_cursor(cur, '''
                SELECT id, base_part_number, position 
                FROM bom_lines 
                WHERE bom_header_id = ?
            ''', (bom_id,)).fetchall()

            existing_lines_dict = {
                (line['base_part_number'], line['position']): line['id']
                for line in existing_lines
            }

            for component in data.get('components') or []:
                raw_part_number = (component.get('base_part_number') or '').strip()
                base_part_number = create_base_part_number(raw_part_number) if raw_part_number else ''
                position = component.get('position', 0)
                quantity = component.get('quantity', 0)
                guide_price = component.get('guide_price')

                print(f"Processing part: {raw_part_number} -> {base_part_number}")
                print(f"  guide_price from request: {guide_price} (type: {type(guide_price)})")

                if base_part_number:
                    part = _execute_with_cursor(cur, '''
                        SELECT base_part_number 
                        FROM part_numbers 
                        WHERE base_part_number = ?
                    ''', (base_part_number,)).fetchone()
                    if not part:
                        _execute_with_cursor(cur, '''
                            INSERT INTO part_numbers (part_number, base_part_number) 
                            VALUES (?, ?)
                        ''', (raw_part_number, base_part_number))

                line_key = (base_part_number, position)
                if line_key in existing_lines_dict:
                    print(f"  Updating existing line {existing_lines_dict[line_key]}")
                    _execute_with_cursor(cur, '''
                        UPDATE bom_lines 
                        SET quantity = ?,
                            position = ?,
                            base_part_number = ?,
                            guide_price = ?
                        WHERE id = ?
                    ''', (
                        quantity,
                        position,
                        base_part_number,
                        guide_price,
                        existing_lines_dict[line_key]
                    ))
                else:
                    print(f"  Inserting new line")
                    _execute_with_cursor(cur, '''
                        INSERT INTO bom_lines (
                            bom_header_id, 
                            base_part_number, 
                            quantity, 
                            position,
                            guide_price
                        ) VALUES (?, ?, ?, ?, ?)
                    ''', (
                        bom_id,
                        base_part_number,
                        quantity,
                        position,
                        guide_price
                    ))

        return jsonify({
            'status': 'success',
            'message': 'BOM updated successfully'
        })

    except Exception as exc:
        logging.error(f"Error updating BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400


@bom_bp.route('/customers/remove/<int:bom_id>', methods=['POST'])
def remove_customer(bom_id):
    data = request.json or {}

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                DELETE FROM customer_boms 
                WHERE bom_header_id = ? AND customer_id = ?
            ''', (bom_id, data.get('customer_id')))

        return jsonify({
            'status': 'success',
            'message': 'Customer removed from BOM'
        })

    except Exception as exc:
        logging.error(f"Error removing customer from BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400


@bom_bp.route('/customers/update_ref/<int:bom_id>', methods=['POST'])
def update_customer_ref(bom_id):
    data = request.json or {}

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                UPDATE customer_boms 
                SET reference = ?
                WHERE bom_header_id = ? AND customer_id = ?
            ''', (
                data.get('reference', ''),
                bom_id,
                data.get('customer_id')
            ))

        return jsonify({
            'status': 'success',
            'message': 'Customer reference updated'
        })

    except Exception as exc:
        logging.error(f"Error updating customer reference for BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400


@bom_bp.route('/components/add/<int:bom_id>', methods=['POST'])
def add_component(bom_id):
    data = request.json or {}

    try:
        with db_cursor(commit=True) as cur:
            bom = _execute_with_cursor(cur, 'SELECT id FROM bom_headers WHERE id = ?', (bom_id,)).fetchone()
            if not bom:
                return jsonify({
                    'status': 'error',
                    'message': 'BOM not found'
                }), 404

            raw_part_number = (data.get('base_part_number') or '').strip()
            logging.info(f"Raw part number before base conversion: '{raw_part_number}'")

            base_part_number = create_base_part_number(raw_part_number) if raw_part_number else ''
            logging.info(f"Base part number after conversion: '{base_part_number}'")

            if base_part_number:
                part = _execute_with_cursor(cur,
                    'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
                    (base_part_number,)
                ).fetchone()
                if not part:
                    _execute_with_cursor(cur,
                        'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                        (raw_part_number, base_part_number)
                    )

            max_position_row = _execute_with_cursor(cur, '''
                SELECT COALESCE(MAX(position), 0) as max_pos
                FROM bom_lines
                WHERE bom_header_id = ?
            ''', (bom_id,)).fetchone()
            max_position = max_position_row['max_pos'] if max_position_row else 0

            guide_price_value = data.get('guide_price')
            logging.info(f"guide_price from add_component: {guide_price_value} (type: {type(guide_price_value)})")

            insert_line = _with_returning_clause('''
                INSERT INTO bom_lines (
                    bom_header_id,
                    base_part_number,
                    quantity,
                    position,
                    guide_price
                ) VALUES (?, ?, ?, ?, ?)
            ''')
            _execute_with_cursor(cur, insert_line, (
                bom_id,
                base_part_number,
                data.get('quantity', 1),
                max_position + 10,
                guide_price_value
            ))
            new_line_id = _fetch_inserted_id(cur)
            if not new_line_id:
                raise RuntimeError("Failed to insert BOM line")

            new_component = _execute_with_cursor(cur, '''
                SELECT bl.*,
                       pn.part_number,
                       bp.offer_line_id,
                       ol.price as current_price,
                       ol.lead_time as current_lead_time,
                       s.name as supplier_name,
                       c.currency_code,
                       bl.guide_price
                FROM bom_lines bl
                LEFT JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                LEFT JOIN bom_pricing bp ON bl.id = bp.bom_line_id
                LEFT JOIN offer_lines ol ON bp.offer_line_id = ol.id
                LEFT JOIN offers o ON ol.offer_id = o.id
                LEFT JOIN suppliers s ON o.supplier_id = s.id
                LEFT JOIN currencies c ON o.currency_id = c.id
                WHERE bl.id = ?
            ''', (new_line_id,)).fetchone()

            if new_component is None:
                raise RuntimeError("Failed to retrieve newly created component")

        component_data = {
            'base_part_number': new_component['part_number'] or new_component['base_part_number'] or '',
            'quantity': new_component['quantity'] or 0,
            'position': new_component['position'] or 0,
            'guide_price': new_component['guide_price'] or 0.0,
            'current_price': new_component.get('current_price') or 0.0,
            'supplier_name': new_component.get('supplier_name') or '',
            'currency_code': new_component.get('currency_code') or '',
            'lead_time': new_component.get('current_lead_time') or ''
        }

        return jsonify({
            'status': 'success',
            'component': component_data
        })

    except Exception as exc:
        logging.error(f"Error adding component to BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400


@bom_bp.route('/customers/add/<int:bom_id>', methods=['POST'])
def add_customer(bom_id):
    data = request.json or {}

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                INSERT INTO customer_boms (bom_header_id, customer_id, reference)
                VALUES (?, ?, ?)
            ''', (
                bom_id,
                data['customer_id'],
                data.get('reference', '')
            ))

        return jsonify({
            'status': 'success',
            'message': 'Customer added to BOM'
        })

    except Exception as exc:
        logging.error(f"Error adding customer to BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400
