from flask import Blueprint, request, jsonify, render_template, redirect, url_for
import pandas as pd
import os
from datetime import datetime
from werkzeug.utils import secure_filename
import numpy as np

from db import db_cursor, execute as db_execute, get_db_connection

stock_movements_bp = Blueprint('stock_movements', __name__, url_prefix='/stock')


def _using_postgres() -> bool:
    return bool(os.getenv('DATABASE_URL') and os.getenv('DATABASE_URL').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query: str) -> str:
    """Compatibility layer for endpoints still using raw cursor.execute.

    db_execute/db_cursor translate '?' to '%s' in Postgres mode. We replicate that here
    for direct cursor.execute calls.
    """
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query: str, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _get_inserted_id(row, cur) -> int | None:
    if row is None:
        return getattr(cur, 'lastrowid', None)
    if isinstance(row, dict):
        return row.get('id')
    try:
        return row['id']
    except Exception:
        try:
            return row[0]
        except Exception:
            return getattr(cur, 'lastrowid', None)


def update_part_stock(base_part_number, quantity, movement_type):
    operator = '+' if movement_type == 'IN' else '-'
    query = f"""
        UPDATE part_numbers
        SET stock = COALESCE(stock, 0) {operator} ?
        WHERE base_part_number = ?
    """
    db_execute(query, (quantity, base_part_number), commit=True)


@stock_movements_bp.route('/', methods=['GET'])
def stock_movements_page():
    try:
        parts_rows = db_execute(
            """
            SELECT base_part_number, part_number
            FROM part_numbers
            ORDER BY base_part_number
            """,
            fetch='all'
        ) or []
        parts = [
            {
                'base_part_number': r['base_part_number'] if isinstance(r, dict) else r[0],
                'part_number': r['part_number'] if isinstance(r, dict) else r[1],
            }
            for r in parts_rows
        ]

        recent_rows = db_execute(
            """
            SELECT sm.movement_id, sm.base_part_number, pn.part_number,
                   sm.movement_type, sm.quantity, sm.datecode,
                   sm.cost_per_unit, sm.movement_date
            FROM stock_movements sm
            LEFT JOIN part_numbers pn ON sm.base_part_number = pn.base_part_number
            ORDER BY sm.movement_date DESC
            LIMIT 10
            """,
            fetch='all'
        ) or []

        recent_movements = [
            {
                'id': r['movement_id'] if isinstance(r, dict) else r[0],
                'base_part_number': r['base_part_number'] if isinstance(r, dict) else r[1],
                'part_number': r['part_number'] if isinstance(r, dict) else r[2],
                'type': r['movement_type'] if isinstance(r, dict) else r[3],
                'quantity': r['quantity'] if isinstance(r, dict) else r[4],
                'datecode': r['datecode'] if isinstance(r, dict) else r[5],
                'cost_per_unit': r['cost_per_unit'] if isinstance(r, dict) else r[6],
                'date': r['movement_date'] if isinstance(r, dict) else r[7],
            }
            for r in recent_rows
        ]

        return render_template('stock_movements.html', parts=parts, recent_movements=recent_movements)

    except Exception as e:
        return f"Database error: {str(e)}"


@stock_movements_bp.route('/add', methods=['POST'])
def add_stock():
    data = request.get_json()

    base_part_number = data.get('base_part_number')
    quantity = data.get('quantity')

    datecode = data.get('datecode', '')
    cost_per_unit = data.get('cost_per_unit')
    reference = data.get('reference', '')
    notes = data.get('notes', '')

    if not base_part_number or not quantity:
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                INSERT INTO stock_movements
                (base_part_number, movement_type, quantity, available_quantity, datecode,
                 cost_per_unit, reference, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (base_part_number, 'IN', quantity, quantity, datecode, cost_per_unit, reference, notes),
            )

        update_part_stock(base_part_number, quantity, 'IN')
        return jsonify({'success': True, 'message': f'Added {quantity} units to {base_part_number}'}), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@stock_movements_bp.route('/remove', methods=['POST'])
def remove_stock():
    data = request.get_json()

    # Required fields
    base_part_number = data.get('base_part_number')
    quantity = data.get('quantity')

    # Optional fields
    reference = data.get('reference', '')
    notes = data.get('notes', '')

    if not base_part_number or not quantity:
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        with db_cursor(commit=True) as cur:
            # Check current stock level
            _execute_with_cursor(
                cur,
                "SELECT stock FROM part_numbers WHERE base_part_number = ?",
                (base_part_number,),
            )
            stock_row = cur.fetchone()
            current_stock = stock_row['stock'] if isinstance(stock_row, dict) else stock_row[0]

            if current_stock is None or current_stock < quantity:
                return jsonify({'error': 'Insufficient stock'}), 400

            # Find available stock using FIFO
            _execute_with_cursor(
                cur,
                """
                SELECT movement_id, available_quantity
                FROM stock_movements
                WHERE base_part_number = ? AND movement_type = 'IN' AND available_quantity > 0
                ORDER BY movement_date ASC
                """,
                (base_part_number,),
            )
            available_stock = cur.fetchall()

            remaining_quantity = quantity
            allocations = []

            for movement in available_stock:
                movement_id = movement['movement_id'] if isinstance(movement, dict) else movement[0]
                available = movement['available_quantity'] if isinstance(movement, dict) else movement[1]

                if remaining_quantity <= 0:
                    break

                allocated = min(remaining_quantity, available)
                allocations.append((movement_id, allocated))

                _execute_with_cursor(
                    cur,
                    "UPDATE stock_movements SET available_quantity = available_quantity - ? WHERE movement_id = ?",
                    (allocated, movement_id),
                )

                remaining_quantity -= allocated

            if remaining_quantity > 0:
                return jsonify({'error': 'Insufficient stock available'}), 400

            for movement_id, allocated in allocations:
                _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO stock_movements
                    (base_part_number, movement_type, quantity, parent_movement_id, reference, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (base_part_number, 'OUT', allocated, movement_id, reference, notes),
                )

        update_part_stock(base_part_number, quantity, 'OUT')
        return jsonify({'success': True, 'message': f'Removed {quantity} units from {base_part_number}'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@stock_movements_bp.route('/inventory', methods=['GET'])
def get_inventory():
    try:
        rows = db_execute(
            """
            SELECT pn.base_part_number, pn.part_number, pn.stock, pn.datecode,
                   (SELECT COALESCE(AVG(cost_per_unit), 0)
                    FROM stock_movements sm
                    WHERE sm.base_part_number = pn.base_part_number AND sm.movement_type = 'IN'
                   ) as avg_cost
            FROM part_numbers pn
            WHERE pn.stock > 0
            ORDER BY pn.base_part_number
            """,
            fetch='all'
        ) or []

        inventory = [
            {
                'base_part_number': r['base_part_number'] if isinstance(r, dict) else r[0],
                'part_number': r['part_number'] if isinstance(r, dict) else r[1],
                'stock': r['stock'] if isinstance(r, dict) else r[2],
                'datecode': r['datecode'] if isinstance(r, dict) else r[3],
                'avg_cost': round((r['avg_cost'] if isinstance(r, dict) else r[4]) or 0, 2),
            }
            for r in rows
        ]

        return jsonify(inventory), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@stock_movements_bp.route('/balances/<string:base_part_number>', methods=['GET'])
def get_stock_balances(base_part_number, jsonify=jsonify):
    try:
        rows = db_execute(
            """
            SELECT
                sm.movement_id,
                sm.base_part_number,
                pn.part_number,
                sm.datecode,
                sm.movement_date,
                sm.cost_per_unit,
                sm.quantity,
                sm.available_quantity,
                sm.reference
            FROM stock_movements sm
            JOIN part_numbers pn ON sm.base_part_number = pn.base_part_number
            WHERE sm.base_part_number = ?
              AND sm.movement_type = 'IN'
              AND sm.available_quantity > 0
            ORDER BY sm.movement_date
            """,
            (base_part_number,),
            fetch='all'
        ) or []

        balances = [
            {
                'movement_id': r['movement_id'] if isinstance(r, dict) else r[0],
                'base_part_number': r['base_part_number'] if isinstance(r, dict) else r[1],
                'part_number': r['part_number'] if isinstance(r, dict) else r[2],
                'datecode': r['datecode'] if isinstance(r, dict) else r[3],
                'receipt_date': r['movement_date'] if isinstance(r, dict) else r[4],
                'cost_per_unit': r['cost_per_unit'] if isinstance(r, dict) else r[5],
                'original_quantity': r['quantity'] if isinstance(r, dict) else r[6],
                'available_quantity': r['available_quantity'] if isinstance(r, dict) else r[7],
                'reference': r['reference'] if isinstance(r, dict) else r[8],
            }
            for r in rows
        ]

        return jsonify(balances), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@stock_movements_bp.route('/remove-specific', methods=['POST'])
def remove_specific_stock():
    data = request.get_json()

    movement_id = data.get('movement_id')
    quantity = data.get('quantity')

    reference = data.get('reference', '')
    notes = data.get('notes', '')

    if not movement_id or not quantity:
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                SELECT sm.movement_id, sm.base_part_number, sm.available_quantity
                FROM stock_movements sm
                WHERE sm.movement_id = ? AND sm.movement_type = 'IN'
                """,
                (movement_id,),
            )
            movement = cur.fetchone()
            if not movement:
                return jsonify({'error': 'Invalid movement ID'}), 400

            base_part_number = movement['base_part_number'] if isinstance(movement, dict) else movement[1]
            available = movement['available_quantity'] if isinstance(movement, dict) else movement[2]

            if quantity > available:
                return jsonify({'error': f'Insufficient stock available (only {available} units left)'}), 400

            _execute_with_cursor(
                cur,
                "UPDATE stock_movements SET available_quantity = available_quantity - ? WHERE movement_id = ?",
                (quantity, movement_id),
            )

            _execute_with_cursor(
                cur,
                """
                INSERT INTO stock_movements
                (base_part_number, movement_type, quantity, parent_movement_id, reference, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (base_part_number, 'OUT', quantity, movement_id, reference, notes),
            )

        update_part_stock(base_part_number, quantity, 'OUT')
        return jsonify({'success': True, 'message': f'Removed {quantity} units from specific batch'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# New functionality for stock movement imports
def create_base_part_number(part_number):
    """Create a normalized base part number from a part number"""
    # Remove common prefixes, suffixes, and normalize
    base = part_number.upper().strip()
    # Remove common manufacturer prefixes
    for prefix in ['MS', 'NAS', 'AN', 'AS']:
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    # Remove dashes and spaces
    base = base.replace('-', '').replace(' ', '')
    return base if base else part_number.upper()


def create_part_on_demand(cur, system_part_number, part_number=None):
    """Create a part on-demand if it doesn't exist, return base_part_number"""
    if not part_number:
        part_number = system_part_number

    base_part_number = create_base_part_number(part_number)

    try:
        _execute_with_cursor(
            cur,
            """
            INSERT INTO part_numbers (base_part_number, part_number, system_part_number)
            VALUES (?, ?, ?)
            """,
            (base_part_number, part_number, system_part_number),
        )
        print(f"Created missing part on-demand: {system_part_number}")
        return base_part_number
    except Exception as e:
        # Check if it's a base_part_number collision
        if 'base_part_number' in str(e) or 'UNIQUE' in str(e).upper() or 'duplicate' in str(e).lower():
            # Part exists with same base but different system number - update it
            _execute_with_cursor(
                cur,
                """
                SELECT base_part_number, system_part_number, part_number 
                FROM part_numbers 
                WHERE base_part_number = ?
                """,
                (base_part_number,),
            )
            existing = cur.fetchone()

            if existing:
                existing_bpn = existing['base_part_number'] if isinstance(existing, dict) else existing[0]
                print(f"Part with base {base_part_number} exists with system_part_number='{existing_bpn}'")
                print(f"Updating to use system_part_number='{system_part_number}' instead")

                # Update to the new system_part_number
                _execute_with_cursor(
                    cur,
                    """
                    UPDATE part_numbers 
                    SET system_part_number = ?,
                        part_number = ?
                    WHERE base_part_number = ?
                    """,
                    (system_part_number, part_number, base_part_number),
                )

                return base_part_number

        # Check if system_part_number already exists
        _execute_with_cursor(
            cur,
            """
            SELECT base_part_number FROM part_numbers 
            WHERE system_part_number = ?
            """,
            (system_part_number,),
        )
        result = cur.fetchone()

        if result:
            result_bpn = result['base_part_number'] if isinstance(result, dict) else result[0]
            print(f"Part {system_part_number} already exists, returning existing base_part_number")
            return result_bpn

        # If we get here, something else went wrong
        raise


@stock_movements_bp.route('/import', methods=['GET'])
def import_stock_movements_page():
    """Show stock movement import page and list existing files"""
    try:
        rows = db_execute(
            """
            SELECT f.id, f.filename, f.upload_date,
                   EXISTS(
                        SELECT 1
                        FROM import_status
                        WHERE file_id = f.id AND import_type = 'stock_movements'
                   ) as import_status
            FROM files f
            ORDER BY f.upload_date DESC
            """,
            fetch='all'
        ) or []

        files = [
            {
                'id': r['id'] if isinstance(r, dict) else r[0],
                'filename': r['filename'] if isinstance(r, dict) else r[1],
                'upload_date': r['upload_date'] if isinstance(r, dict) else r[2],
                'import_status': r['import_status'] if isinstance(r, dict) else r[3],
            }
            for r in rows
        ]

        return render_template('stock_movement_imports.html', files=files)

    except Exception as e:
        return f"Database error: {str(e)}"
@stock_movements_bp.route('/import/upload', methods=['POST'])
def upload_stock_movement_file():
    """Handle file upload for stock movements"""
    if 'file' not in request.files:
        return jsonify(success=False, message="No file provided"), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify(success=False, message="No file selected"), 400

    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        return jsonify(success=False, message="Only Excel (.xlsx, .xls) and CSV (.csv) files are allowed"), 400

    try:
        # Secure the filename
        filename = secure_filename(file.filename)

        # Generate the file path (ensure the uploads directory exists)
        upload_dir = os.path.join('uploads')
        os.makedirs(upload_dir, exist_ok=True)

        # Generate unique filename if needed
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(os.path.join(upload_dir, filename)):
            filename = f"{base}_{counter}{ext}"
            counter += 1

        # Save the file
        filepath = os.path.join(upload_dir, filename)
        file.save(filepath)

        with db_cursor(commit=True) as cur:
            insert_sql = """
                INSERT INTO files (filename, filepath, upload_date)
                VALUES (?, ?, ?)
            """
            if _using_postgres():
                insert_sql = insert_sql.strip() + " RETURNING id"

            _execute_with_cursor(cur, insert_sql, (filename, filepath, datetime.now()))
            row = cur.fetchone() if _using_postgres() else None
            file_id = _get_inserted_id(row, cur)

        return jsonify(success=True, file_id=file_id)

    except Exception as e:
        print(f"File upload error: {str(e)}")
        return jsonify(success=False, message="Error uploading file"), 500


@stock_movements_bp.route('/import/mapping/<int:file_id>', methods=['GET'])
def stock_movement_mapping(file_id):
    """Render the stock movement mapping interface for a specific file"""
    try:
        file_row = db_execute(
            "SELECT id, filepath FROM files WHERE id = ?",
            (file_id,),
            fetch='one',
        )
        if not file_row:
            return "File not found", 404

        filepath = file_row['filepath'] if isinstance(file_row, dict) else file_row[1]

        # Ensure the file exists
        if not os.path.exists(filepath):
            return "File path does not exist", 404

        # Read the Excel file with explicit header specification
        if filepath.endswith('.xls') or filepath.endswith('.xlsx'):
            # Read with header=0 to explicitly tell pandas that the first row is headers
            df = pd.read_excel(filepath, header=0)

            # Clean column names
            df.columns = [str(col).strip() for col in df.columns]

            # Reset the index to ensure we don't have confusion with row numbers
            df = df.reset_index(drop=True)

            # Create a row index starting from 1 instead of 0 for display purposes
            df.index = df.index + 1

            # If there are rows to process
            if df.shape[0] > 0:
                # Get the column names
                columns = df.columns.tolist()

                # Convert datetime columns to strings to avoid NaT serialization issues
                df_with_index = df.copy()
                for col in df_with_index.columns:
                    if pd.api.types.is_datetime64_any_dtype(df_with_index[col]):
                        df_with_index[col] = df_with_index[col].astype(str).replace('NaT', '')

                df_with_index['row_index'] = df.index
                data = df_with_index.fillna('').to_dict('records')

                # Log for debugging
                print(f"Read file: {filepath}")
                print(f"Found columns: {columns}")
                print(f"First row data: {data[0] if data else 'No data'}")

                mapping_rows = db_execute(
                    """
                    SELECT id, name, mapping
                    FROM import_column_maps
                    WHERE import_type = 'stock_movements'
                    ORDER BY name
                    """,
                    fetch='all',
                ) or []

                saved_mappings = [
                    {
                        'id': r['id'] if isinstance(r, dict) else r[0],
                        'name': r['name'] if isinstance(r, dict) else r[1],
                        'mapping': r['mapping'] if isinstance(r, dict) else r[2],
                    }
                    for r in mapping_rows
                ]

                # Define fields for mapping
                mapping_fields = [
                    {'field': 'part_numbers.system_part_number', 'label': 'System Part Number', 'required': True},
                    {'field': 'part_numbers.part_number', 'label': 'Part Number', 'required': False},
                    {'field': 'stock_movements.movement_type', 'label': 'Movement Type', 'required': True},
                    {'field': 'stock_movements.quantity', 'label': 'Quantity', 'required': True},
                    {'field': 'stock_movements.datecode', 'label': 'Date Code', 'required': False},
                    {'field': 'stock_movements.cost_per_unit', 'label': 'Cost Per Unit', 'required': False},
                    {'field': 'stock_movements.reference', 'label': 'Reference', 'required': False},
                    {'field': 'stock_movements.notes', 'label': 'Notes', 'required': False}
                ]

                print(f"Column datatypes: {df.dtypes}")
                print(f"First few rows:\n{df.head()}")
                return render_template('stock_movement_mapping.html',
                                       file_data=data,
                                       columns=columns,
                                       mapping_fields=mapping_fields,
                                       saved_mappings=saved_mappings,
                                       file_id=file_id,
                                       enumerate=enumerate)
            else:
                return "File appears to be empty", 400

        return "Invalid file format", 400

    except Exception as e:
        print(f"Error in stock_movement_mapping: {str(e)}")
        return f"Error: {str(e)}", 500

@stock_movements_bp.route('/import/process', methods=['POST'])
def process_stock_movement_import():
    """Process stock movement data from the import file"""
    data = request.get_json()
    mapping = data.get('mapping')
    file_id = data.get('file_id')
    clear_existing = data.get('clear_existing', False)

    if not mapping or not file_id:
        return jsonify(success=False, message="Missing mapping or file_id"), 400

    try:
        with db_cursor(commit=True) as cur:
            # Get file details
            _execute_with_cursor(cur, "SELECT filepath FROM files WHERE id = ?", (file_id,))
            file_details = cur.fetchone()
            if not file_details:
                return jsonify(success=False, message="File not found"), 404

            filepath = file_details['filepath'] if isinstance(file_details, dict) else file_details[0]

        # Read Excel file
        df = pd.read_excel(filepath)

        results = {
            'processed': 0,
            'created': 0,
            'skipped': 0,
            'cleared': 0,
            'errors': []
        }

        # If clear existing is requested
        if clear_existing:
            try:
                with db_cursor(commit=True) as cur:
                    _execute_with_cursor(
                        cur,
                        """
                        DELETE FROM stock_movements
                        WHERE reference LIKE 'IMPORT-%'
                        """,
                    )
                    results['cleared'] = getattr(cur, 'rowcount', 0) or 0
                print(f"Cleared {results['cleared']} existing stock movements")
            except Exception as e:
                return jsonify(success=False, message=f"Error clearing existing movements: {str(e)}"), 500

        # Get required mapped column indices
        try:
            # At least one part number field is required
            part_number_col = next(
                (int(col) for col, field in mapping.items()
                 if field == 'part_numbers.part_number'),
                None
            )

            system_part_number_col = next(
                (int(col) for col, field in mapping.items()
                 if field == 'part_numbers.system_part_number'),
                None
            )

            if part_number_col is None and system_part_number_col is None:
                return jsonify(success=False, message="Either Part Number or System Part Number must be mapped"), 400

            movement_type_col = next(
                int(col) for col, field in mapping.items()
                if field == 'stock_movements.movement_type'
            )

            quantity_col = next(
                int(col) for col, field in mapping.items()
                if field == 'stock_movements.quantity'
            )

            # Optional fields
            datecode_col = next(
                (int(col) for col, field in mapping.items()
                 if field == 'stock_movements.datecode'),
                None
            )

            cost_per_unit_col = next(
                (int(col) for col, field in mapping.items()
                 if field == 'stock_movements.cost_per_unit'),
                None
            )

            reference_col = next(
                (int(col) for col, field in mapping.items()
                 if field == 'stock_movements.reference'),
                None
            )

            notes_col = next(
                (int(col) for col, field in mapping.items()
                 if field == 'stock_movements.notes'),
                None
            )

        except StopIteration:
            return jsonify(success=False, message="Required fields not properly mapped"), 400
        except ValueError as e:
            return jsonify(success=False, message=f"Invalid column index: {str(e)}"), 400

        # Store pending stock updates to apply in a single batch at the end
        stock_updates = {}

        # Process each row in the file
        for idx, row in df.iterrows():
            try:
                # Get part number (either system or regular)
                base_part_number = None

                if system_part_number_col is not None:
                    system_part_number = str(row.iloc[system_part_number_col]).strip()

                    # Look up base part number from system part number
                    _execute_with_cursor(
                        cur,
                        "SELECT base_part_number FROM part_numbers WHERE system_part_number = ?",
                        (system_part_number,),
                    )
                    result = cur.fetchone()

                    if result:
                        base_part_number = result[0]

                if base_part_number is None and part_number_col is not None:
                    part_number = str(row.iloc[part_number_col]).strip()

                    # Look up base part number from part number
                    _execute_with_cursor(
                        cur,
                        "SELECT base_part_number FROM part_numbers WHERE part_number = ?",
                        (part_number,),
                    )
                    result = cur.fetchone()

                    if result:
                        base_part_number = result[0]

                if base_part_number is None:
                    results['errors'].append(f"Row {idx + 1}: Part not found")
                    results['skipped'] += 1
                    continue

                # Get movement type
                movement_type = str(row.iloc[movement_type_col]).strip().upper()

                # Standardize movement type
                if movement_type not in ['IN', 'OUT']:
                    if movement_type in ['INBOUND', 'RECEIPT', 'RECEIVE', 'ADD', 'STOCK IN']:
                        movement_type = 'IN'
                    elif movement_type in ['OUTBOUND', 'ISSUE', 'SHIP', 'REMOVE', 'STOCK OUT']:
                        movement_type = 'OUT'
                    else:
                        results['errors'].append(f"Row {idx + 1}: Invalid movement type: {movement_type}")
                        results['skipped'] += 1
                        continue

                # Get quantity
                try:
                    quantity = float(str(row.iloc[quantity_col]).strip())
                except (ValueError, TypeError):
                    results['errors'].append(f"Row {idx + 1}: Invalid quantity format")
                    results['skipped'] += 1
                    continue

                if quantity <= 0:
                    results['errors'].append(f"Row {idx + 1}: Quantity must be positive")
                    results['skipped'] += 1
                    continue

                # Get optional fields
                datecode = None
                if datecode_col is not None:
                    datecode = str(row.iloc[datecode_col]).strip()

                cost_per_unit = None
                if cost_per_unit_col is not None:
                    try:
                        cost_str = str(row.iloc[cost_per_unit_col]).strip()
                        if cost_str:
                            # Handle currency symbols and commas
                            cost_str = cost_str.replace('$', '').replace(',', '')
                            cost_per_unit = float(cost_str)
                    except (ValueError, TypeError):
                        # Not critical, so just log but continue
                        print(f"Warning: Invalid cost format in row {idx + 1}")

                reference = f"IMPORT-{file_id}-{datetime.now().strftime('%Y%m%d')}"
                if reference_col is not None:
                    ref = str(row.iloc[reference_col]).strip()
                    if ref:
                        reference = ref

                notes = None
                if notes_col is not None:
                    notes = str(row.iloc[notes_col]).strip()

                # Process based on movement type
                if movement_type == 'IN':
                    # Add stock - insert movement record
                    _execute_with_cursor(
                        cur,
                        """
                        INSERT INTO stock_movements
                        (base_part_number, movement_type, quantity, available_quantity, datecode,
                         cost_per_unit, reference, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (base_part_number, 'IN', quantity, quantity, datecode,
                         cost_per_unit, reference, notes),
                    )

                    # Track stock update instead of immediately updating
                    if base_part_number in stock_updates:
                        stock_updates[base_part_number] += quantity
                    else:
                        stock_updates[base_part_number] = quantity

                elif movement_type == 'OUT':
                    # Check if there's enough stock
                    _execute_with_cursor(
                        cur,
                        "SELECT stock FROM part_numbers WHERE base_part_number = ?",
                        (base_part_number,),
                    )
                    result = cur.fetchone()

                    current_stock = result[0] if result and result[0] is not None else 0

                    # Account for pending updates to this part
                    if base_part_number in stock_updates:
                        current_stock += stock_updates[base_part_number]

                    if current_stock < quantity:
                        results['errors'].append(f"Row {idx + 1}: Insufficient stock for {base_part_number}")
                        results['skipped'] += 1
                        continue

                    # Find available stock using FIFO
                    _execute_with_cursor(
                        cur,
                        """
                        SELECT movement_id, available_quantity
                        FROM stock_movements
                        WHERE base_part_number = ? AND movement_type = 'IN' AND available_quantity > 0
                        ORDER BY movement_date ASC
                        """,
                        (base_part_number,),
                    )

                    available_stock = cur.fetchall()
                    remaining_quantity = quantity
                    allocations = []

                    # Allocate stock from oldest movements first (FIFO)
                    for movement in available_stock:
                        movement_id = movement[0]
                        available = movement[1]

                        if remaining_quantity <= 0:
                            break

                        allocated = min(remaining_quantity, available)
                        allocations.append({
                            "movement_id": movement_id,
                            "quantity": allocated
                        })

                        # Update available quantity
                        _execute_with_cursor(
                            cur,
                            "UPDATE stock_movements SET available_quantity = available_quantity - ? WHERE movement_id = ?",
                            (allocated, movement_id),
                        )

                        remaining_quantity -= allocated

                    if remaining_quantity > 0:
                        # Rollback changes and skip this row
                        results['errors'].append(f"Row {idx + 1}: Insufficient available stock for {base_part_number}")
                        results['skipped'] += 1
                        continue

                    # Insert OUT movement records for each allocation
                    for allocation in allocations:
                        _execute_with_cursor(
                            cur,
                            """
                            INSERT INTO stock_movements
                            (base_part_number, movement_type, quantity, parent_movement_id, reference, notes)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (base_part_number, 'OUT', allocation["quantity"], allocation["movement_id"], reference,
                             notes),
                        )

                    # Track stock update instead of immediately updating
                    if base_part_number in stock_updates:
                        stock_updates[base_part_number] -= quantity
                    else:
                        stock_updates[base_part_number] = -quantity

                results['processed'] += 1
                results['created'] += 1

            except Exception as e:
                print(f"Error processing row {idx + 1}: {e}")
                results['errors'].append(f"Row {idx + 1}: {str(e)}")
                results['skipped'] += 1
                continue

            # Now apply all stock updates in a single batch
            for base_part_number, quantity_change in stock_updates.items():
                if quantity_change > 0:
                    _execute_with_cursor(
                        cur,
                        "UPDATE part_numbers SET stock = stock + ? WHERE base_part_number = ?",
                        (quantity_change, base_part_number),
                    )
                elif quantity_change < 0:
                    _execute_with_cursor(
                        cur,
                        "UPDATE part_numbers SET stock = stock - ? WHERE base_part_number = ?",
                        (abs(quantity_change), base_part_number),
                    )

            # Update import status
            _execute_with_cursor(
                cur,
                """
                INSERT INTO import_status
                (file_id, import_type, processed, created, updated, skipped, errors, status, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    file_id,
                    'stock_movements',
                    results['processed'],
                    results['created'],
                    0,  # No updates, only creations
                    results['skipped'],
                    str(results['errors']),
                    'completed',
                ),
            )

            return jsonify(success=True, results=results)

    except Exception as e:
        print(f"Error in process_stock_movement_import: {str(e)}")
        return jsonify(success=False, message=str(e)), 500


@stock_movements_bp.route('/upload-stock-levels', methods=['GET'])
def upload_stock_levels_page():
    """Show stock levels upload page"""
    try:
        rows = db_execute(
            """
            SELECT f.id, f.filename, f.upload_date,
                   EXISTS(
                     SELECT 1 FROM import_status
                     WHERE file_id = f.id AND import_type = 'stock_levels'
                   ) as import_status
            FROM files f
            ORDER BY f.upload_date DESC
            """,
            fetch='all',
        ) or []

        files = [
            {
                'id': r['id'] if isinstance(r, dict) else r[0],
                'filename': r['filename'] if isinstance(r, dict) else r[1],
                'upload_date': r['upload_date'] if isinstance(r, dict) else r[2],
                'import_status': r['import_status'] if isinstance(r, dict) else r[3],
            }
            for r in rows
        ]

        return render_template('stock_levels_upload.html', files=files)

    except Exception as e:
        return f"Database error: {str(e)}"


@stock_movements_bp.route('/upload-stock-levels/mapping/<int:file_id>', methods=['GET'])
def stock_levels_mapping(file_id):
    """Render the stock levels mapping interface - now processes CSV directly"""
    try:
        file_row = db_execute(
            "SELECT id, filepath FROM files WHERE id = ?",
            (file_id,),
            fetch='one',
        )
        if not file_row:
            return "File not found", 404

        filepath = file_row['filepath'] if isinstance(file_row, dict) else file_row[1]

        if not os.path.exists(filepath):
            return "File path does not exist", 404

        # Read CSV file
        df = pd.read_csv(filepath)
        df.columns = [str(col).strip() for col in df.columns]
        df = df.reset_index(drop=True)
        df.index = df.index + 1

        if df.shape[0] > 0:
            columns = df.columns.tolist()

            df_with_index = df.copy()
            for col in df_with_index.columns:
                if pd.api.types.is_datetime64_any_dtype(df_with_index[col]):
                    df_with_index[col] = df_with_index[col].astype(str).replace('NaT', '')

            df_with_index['row_index'] = df.index
            data = df_with_index.fillna('').to_dict('records')

            # Define simpler fields for stock levels
            mapping_fields = [
                {'field': 'part_number', 'label': 'Part Number', 'required': True},
                {'field': 'stock_quantity', 'label': 'Stock Quantity', 'required': True},
                {'field': 'unit_price', 'label': 'Unit Price (optional)', 'required': False}
            ]

            return render_template(
                'stock_levels_mapping.html',
                file_data=data,
                columns=columns,
                mapping_fields=mapping_fields,
                file_id=file_id,
                enumerate=enumerate,
            )
        else:
            return "File appears to be empty", 400

    except Exception as e:
        print(f"Error in stock_levels_mapping: {str(e)}")
        return f"Error: {str(e)}", 500


@stock_movements_bp.route('/upload-stock-levels/process', methods=['POST'])
def process_stock_levels_upload():
    """Process stock levels upload - NUKES EVERYTHING and rebuilds from CSV"""
    data = request.get_json()
    file_id = data.get('file_id')

    if not file_id:
        return jsonify(success=False, message="Missing file_id"), 400

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, "SELECT filepath FROM files WHERE id = ?", (file_id,))
            file_details = cur.fetchone()
            if not file_details:
                return jsonify(success=False, message="File not found"), 404

            filepath = file_details['filepath'] if isinstance(file_details, dict) else file_details[0]

        # Read CSV file
        df = pd.read_csv(filepath)

        results = {
            'processed': 0,
            'created': 0,
            'deleted': 0,
            'errors': []
        }

        # Hard-coded column names from ERP
        ERP_ID_COL = 'id'  # Unique identifier from ERP
        PART_NUMBER_COL = 'partNumber'
        PART_ID_COL = 'partId'  # System part number
        QUANTITY_COL = 'remainingQty'
        COST_COL = 'unitCost'

        # Verify required columns exist
        required_cols = [ERP_ID_COL, PART_NUMBER_COL, PART_ID_COL, QUANTITY_COL]
        for col in required_cols:
            if col not in df.columns:
                return jsonify(success=False, message=f"Required column '{col}' not found in CSV"), 400

        # Run destructive reset inside a transaction
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # NUKE EVERYTHING - Delete ALL stock movements (no conditions)
            _execute_with_cursor(cur, "SELECT COUNT(*) FROM stock_movements")
            count_row = cur.fetchone()
            results['deleted'] = (
                count_row.get('count') if isinstance(count_row, dict) and 'count' in count_row
                else (count_row[0] if count_row else 0)
            )

            _execute_with_cursor(cur, "DELETE FROM stock_movements")
            print(f"NUKED ALL {results['deleted']} stock movements")

            # Reset all stock levels to 0
            _execute_with_cursor(cur, "UPDATE part_numbers SET stock = 0")
            print("Reset all stock levels to 0")
            conn.commit()

            # Process each row, committing as we go so a single failure doesn't abort the entire import
            for idx, row in df.iterrows():
                try:
                    # Get ERP ID
                    erp_id = str(row[ERP_ID_COL]).strip()
                    if not erp_id or erp_id.lower() in ['nan', 'none', '']:
                        results['errors'].append(f"Row {idx + 1}: Missing ERP ID")
                        continue

                    # Get part number
                    part_number = str(row[PART_NUMBER_COL]).strip()

                    # Get system part number (partId)
                    system_part_number = str(row[PART_ID_COL]).strip()

                    if not part_number or part_number.lower() in ['nan', 'none', '']:
                        continue

                    if not system_part_number or system_part_number.lower() in ['nan', 'none', '']:
                        results['errors'].append(f"Row {idx + 1}: Missing partId (system_part_number)")
                        continue

                    # Get quantity
                    try:
                        quantity = float(str(row[QUANTITY_COL]).strip())
                    except (ValueError, TypeError):
                        results['errors'].append(f"Row {idx + 1}: Invalid quantity format")
                        continue

                    if quantity < 0:
                        results['errors'].append(f"Row {idx + 1}: Quantity cannot be negative")
                        continue

                    # Get optional cost
                    unit_price = None
                    if COST_COL in df.columns:
                        try:
                            price_str = str(row[COST_COL]).strip()
                            if price_str and price_str.lower() not in ['nan', 'none', '']:
                                price_str = price_str.replace('$', '').replace(',', '')
                                unit_price = float(price_str)
                        except (ValueError, TypeError):
                            pass

                    row_created = False

                    # Look up base_part_number
                    _execute_with_cursor(
                        cur,
                        "SELECT base_part_number FROM part_numbers WHERE part_number = ? OR system_part_number = ?",
                        (part_number, system_part_number),
                    )
                    result = cur.fetchone()

                    if not result:
                        # Part doesn't exist - create it on-demand
                        base_part_number = create_part_on_demand(cur, system_part_number, part_number)
                        results['errors'].append(f"Row {idx + 1}: Created new part {system_part_number}")
                    else:
                        base_part_number = (
                            result['base_part_number'] if isinstance(result, dict)
                            else result[0]
                        )

                    # Create new stock movement with ERP ID as reference
                    if quantity > 0:
                        _execute_with_cursor(
                            cur,
                            """
                            INSERT INTO stock_movements
                            (base_part_number, movement_type, quantity, available_quantity,
                             cost_per_unit, reference, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                base_part_number,
                                'IN',
                                quantity,
                                quantity,
                                unit_price,
                                f"ERP-{erp_id}",
                                f"Stock from ERP import",
                            ),
                        )

                        # Update part_numbers stock total
                        _execute_with_cursor(
                            cur,
                            "UPDATE part_numbers SET stock = stock + ? WHERE base_part_number = ?",
                            (quantity, base_part_number),
                        )

                        row_created = True

                    if row_created:
                        results['created'] += 1

                    results['processed'] += 1

                    conn.commit()

                except Exception as e:
                    print(f"Error processing row {idx + 1}: {e}")
                    results['errors'].append(f"Row {idx + 1}: {str(e)}")
                    conn.rollback()
                    try:
                        cur.close()
                    except Exception:
                        pass
                    cur = conn.cursor()
                    continue
            # Record the import
            _execute_with_cursor(
                cur,
                """
                INSERT INTO import_status
                (file_id, import_type, processed, created, updated, skipped, errors, status, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    file_id,
                    'stock_levels',
                    results['processed'],
                    results['created'],
                    0,
                    0,
                    str(results['errors']),
                    'completed',
                ),
            )

            conn.commit()

        finally:
            try:
                cur.close()
            except Exception:
                pass
            conn.close()

        return jsonify(success=True, results=results)

    except Exception as e:
        print(f"Error in process_stock_levels_upload: {str(e)}")
        return jsonify(success=False, message=str(e)), 500
