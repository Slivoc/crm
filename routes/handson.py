from flask import Blueprint, render_template, flash, redirect, current_app, jsonify, request
import pandas as pd
from models import get_excess_stock_list_id_by_file, insert_excess_stock_line, get_file_by_id, create_base_part_number, get_part_numbers, insert_part_number, get_excess_stock_list_by_id  # Import your function to get data
import os
import sqlite3
import json
import re
from datetime import datetime
from folder_watcher import start_folder_watcher
import threading
from db import db_cursor, execute as db_execute

# Create a blueprint for the Handsontable-related routes
handson_bp = Blueprint('handson', __name__)


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _resolve_file_path(file_path):
    if not file_path:
        return file_path
    if os.path.isabs(file_path):
        return file_path
    base_path = current_app.root_path if current_app else os.getcwd()
    return os.path.join(base_path, file_path)


def _with_returning_clause(query):
    if not _using_postgres():
        return query
    trimmed = query.strip().rstrip(';')
    return f"{trimmed} RETURNING id"


def _last_inserted_id(cur):
    if _using_postgres():
        row = cur.fetchone()
        if row:
            return row.get('id') if isinstance(row, dict) else row[0]
        return None
    return getattr(cur, 'lastrowid', None)


def _row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def _is_unique_violation(exc):
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    return getattr(exc, 'pgcode', None) == '23505'


# Route to view the Handsontable page for a specific file by file_id
@handson_bp.route('/<int:file_id>', methods=['GET'])
def view_file_in_handson(file_id):
    # Fetch the file details (path) using file_id
    file_details = get_file_by_id(file_id)

    if not file_details:
        return "File not found", 404

    file_path = _resolve_file_path(file_details['filepath'])

    # Ensure the file exists
    if not os.path.exists(file_path):
        return "File path does not exist", 404

    # Change this part in your view_file_in_handson route
    if file_path.endswith('.xls') or file_path.endswith('.xlsx'):
        # Read the Excel file using pandas and specify the header row if necessary
        df = pd.read_excel(file_path, header=0)  # Modify header row index if needed

        # Convert the dataframe to a list of dictionaries for Handsontable
        data = df.to_dict(orient='records')
        columns = df.columns.tolist()  # Get the column names

        # Pass the data and columns to the frontend for rendering in Handsontable
        return render_template('handson.html', file_data=data, columns=columns)

    return "Invalid file format", 400

@handson_bp.route('/process_excess_list', methods=['POST'])
def process_excess_list():
    content = request.get_json()

    # Debug: Print the received data and mapping
    print("Received content:", content)

    if 'mapping' not in content:
        return jsonify(success=False, message="Mapping missing from request"), 400

    mapping = content['mapping']

    # Ensure that 'excess_stock_list_id' is provided in the request
    if 'excess_stock_list_id' not in content:
        return jsonify(success=False, message="'excess_stock_list_id' is missing from the request"), 400

    excess_stock_list_id = content['excess_stock_list_id']
    header_row = int(content.get('header_row', 1) or 1)
    if header_row < 1:
        header_row = 1

    data = content.get('data')
    if data is None:
        file_id = content.get('file_id')
        if not file_id:
            return jsonify(success=False, message="File ID missing from request"), 400

        file_details = get_file_by_id(file_id)
        if not file_details:
            return jsonify(success=False, message="File not found"), 404

        file_path = _resolve_file_path(file_details['filepath'])
        if not os.path.exists(file_path):
            return jsonify(success=False, message="File path does not exist"), 404

        df = pd.read_excel(file_path, header=None)
        df = df.fillna('')
        if header_row < len(df):
            df = df.iloc[header_row:]
        else:
            df = df.iloc[0:0]
        data = df.values.tolist()

    def _find_mapping_index(field_name):
        for key, value in mapping.items():
            if value == field_name:
                return int(key)
        return None

    base_part_index = _find_mapping_index('base_part_number')
    part_number_index = _find_mapping_index('part_number')
    quantity_index = _find_mapping_index('quantity')
    if quantity_index is None or (base_part_index is None and part_number_index is None):
        return jsonify(success=False, message="Mapping error: quantity and part_number or base_part_number are required"), 400

    manufacturer_index = _find_mapping_index('manufacturer')
    date_code_index = _find_mapping_index('date_code')
    unit_price_index = _find_mapping_index('unit_price')
    unit_price_currency_index = _find_mapping_index('unit_price_currency')

    # Fetch all existing base part numbers from the database
    existing_part_numbers = {part['base_part_number'] for part in get_part_numbers()}

    def _parse_unit_price(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        if text == '':
            return None
        text = re.sub(r'[£$€]', '', text)
        text = text.replace(',', '')
        try:
            return float(text)
        except ValueError:
            return None

    def _parse_quantity(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            if abs(value) > 2147483647:
                return None
            return value
        if isinstance(value, float):
            if not value.is_integer():
                return None
            value_int = int(value)
            if abs(value_int) > 2147483647:
                return None
            return value_int
        text = str(value).strip()
        if text == '':
            return None
        text = text.replace(',', '')
        try:
            qty = float(text)
        except ValueError:
            return None
        if not qty.is_integer():
            return None
        qty_int = int(qty)
        if abs(qty_int) > 2147483647:
            return None
        return qty_int

    currency_rows = db_execute(
        "SELECT id, currency_code, symbol FROM currencies",
        fetch='all',
    ) or []
    currency_map = {}
    for row in currency_rows:
        code = (row.get('currency_code') or '').strip().lower()
        symbol = (row.get('symbol') or '').strip().lower()
        if code:
            currency_map[code] = row['id']
        if symbol:
            currency_map[symbol] = row['id']

    def _parse_currency_id(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip().lower()
        if not text:
            return None
        return currency_map.get(text)

    # Process each row based on the user's mapping
    for row_index, row in enumerate(data):
        try:
            if base_part_index is not None and base_part_index >= len(row):
                continue
            if part_number_index is not None and part_number_index >= len(row):
                continue
            if quantity_index >= len(row):
                continue
            if unit_price_index is not None and unit_price_index >= len(row):
                unit_price_index = None
            if unit_price_currency_index is not None and unit_price_currency_index >= len(row):
                unit_price_currency_index = None

            # Clean and standardize base part number
            raw_part_number = row[part_number_index] if part_number_index is not None else row[base_part_index]
            if raw_part_number is None or str(raw_part_number).strip() == '':
                continue
            base_part_number = create_base_part_number(raw_part_number)
            part_number = str(raw_part_number).strip()

            quantity = _parse_quantity(row[quantity_index])
            if quantity is None:
                continue
            manufacturer = row[manufacturer_index] if manufacturer_index is not None else None
            date_code = row[date_code_index] if date_code_index is not None else None
            unit_price = _parse_unit_price(row[unit_price_index]) if unit_price_index is not None else None
            unit_price_currency_id = (
                _parse_currency_id(row[unit_price_currency_index])
                if unit_price_currency_index is not None
                else None
            )

            # Check if the base_part_number exists in the database
            if base_part_number not in existing_part_numbers:
                # If it doesn't exist, insert the part number and base part number into the database
                insert_part_number(part_number=raw_part_number, base_part_number=base_part_number)
                existing_part_numbers.add(base_part_number)  # Update the set with the new base part number

            # Insert into the excess_stock_lines table
            insert_excess_stock_line(excess_stock_list_id=excess_stock_list_id,  # Ensure this ID is passed correctly
                                     base_part_number=base_part_number,
                                     quantity=quantity,
                                     date_code=date_code,
                                     manufacturer=manufacturer,
                                     unit_price=unit_price,
                                     unit_price_currency_id=unit_price_currency_id,
                                     part_number=part_number)
        except Exception as e:
            # Catch all other exceptions
            print(f"Error processing row {row}: {e}")
            return jsonify(success=False, message=f"Error processing row: {str(e)}"), 500

    db_execute(
        '''
        UPDATE excess_stock_lists
        SET mapping = ?, mapping_header_row = ?
        WHERE id = ?
        ''',
        (json.dumps(mapping), header_row, excess_stock_list_id),
        commit=True,
    )

    # If everything is successful, return a JSON response with redirect
    flash('Excess list processed successfully!', 'success')
    return jsonify(success=True, redirect_url=f'/excess/excess_lists/{excess_stock_list_id}/edit')



@handson_bp.route('/excess_list_mapping/<int:file_id>', methods=['GET'])
def excess_list_mapping(file_id):
    # Fetch the file details (path) using file_id
    file_details = get_file_by_id(file_id)

    if not file_details:
        return "File not found", 404

    file_path = _resolve_file_path(file_details['filepath'])

    # Ensure the file exists
    if not os.path.exists(file_path):
        return "File path does not exist", 404

    show_all = str(request.args.get('show_all', '')).lower() in ('1', 'true', 'on', 'yes')

    # Read the Excel file without enforcing headers so we can show raw rows
    if file_path.endswith('.xls') or file_path.endswith('.xlsx'):
        if show_all:
            df = pd.read_excel(file_path, header=None)
        else:
            df = pd.read_excel(file_path, header=None, nrows=20)

        # Convert the dataframe to a list of dictionaries for Handsontable
        df = df.fillna('')
        data = df.values.tolist()
        columns = list(range(df.shape[1]))

        total_rows = None
        if not show_all:
            try:
                total_rows = len(pd.read_excel(file_path, header=None, usecols=[0]))
            except Exception:
                total_rows = None

        # Example: Get the excess_stock_list_id (You could get this from another source, query, etc.)
        excess_stock_list_id = get_excess_stock_list_id_by_file(file_id)  # Define your logic for retrieving this
        excess_list = get_excess_stock_list_by_id(excess_stock_list_id) if excess_stock_list_id else None
        saved_mapping = None
        saved_header_row = 1
        if excess_list:
            saved_header_row = excess_list.get('mapping_header_row') or 1
            raw_mapping = excess_list.get('mapping')
            if raw_mapping:
                try:
                    saved_mapping = json.loads(raw_mapping)
                except json.JSONDecodeError:
                    saved_mapping = None

        # Render the mapping page with the data, column headers, and excess_stock_list_id
        return render_template('excess_list_mapping.html',
                               file_data=data,
                               columns=columns,
                               excess_stock_list_id=excess_stock_list_id,
                               show_all=show_all,
                               total_rows=total_rows,
                               saved_mapping=saved_mapping,
                               saved_header_row=saved_header_row,
                               file_id=file_id)  # Pass it to the template

    return "Invalid file format", 400

# handson_routes.py
@handson_bp.route('/import_mapping/<int:file_id>', methods=['GET'])
def import_mapping(file_id):
    file_details = get_file_by_id(file_id)
    if not file_details:
        return "File not found", 404

    file_path = _resolve_file_path(file_details['filepath'])
    if not os.path.exists(file_path):
        return "File path does not exist", 404

    if file_path.endswith('.xls') or file_path.endswith('.xlsx'):
        # Read the Excel file
        df = pd.read_excel(file_path)

        # Convert all data to strings to avoid serialization issues
        df = df.astype(str)

        # Replace 'nan' strings with empty strings
        df = df.replace('nan', '')
        df = df.replace('NaT', '')

        data = df.to_dict('records')
        columns = df.columns.tolist()

        # Define available fields for each import type
        mapping_fields = get_dynamic_mapping_fields()

        saved_mappings = db_execute("""
            SELECT id, name, import_type, mapping 
            FROM import_column_maps 
            ORDER BY import_type, name
        """, fetch='all') or []

        return render_template('import_mapping.html',
                               file_data=data,
                               columns=columns,
                               mapping_fields=mapping_fields,
                               saved_mappings=[dict(m) for m in saved_mappings],
                               file_id=file_id)

    return "Invalid file format", 400

@handson_bp.route('/save_import_mapping', methods=['POST'])
def save_import_mapping():
    content = request.get_json()

    if 'name' not in content or 'mapping' not in content:
        return jsonify(success=False, message="Name or mapping missing"), 400

    try:
        insert_query = _with_returning_clause("""
            INSERT INTO import_column_maps (name, import_type, mapping)
            VALUES (?, ?, ?)
        """)
        params = (
            content['name'],
            content.get('import_type', 'sales_orders'),
            json.dumps(content['mapping'])
        )
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, insert_query, params)
            mapping_id = _last_inserted_id(cur)
        return jsonify(success=True, id=mapping_id)
    except Exception as e:
        current_app.logger.error(f"Failed to save import mapping: {e}")
        return jsonify(success=False, message=str(e)), 500

@handson_bp.route('/load_import_mapping/<int:mapping_id>', methods=['GET'])
def load_import_mapping(mapping_id):
    """Retrieve a saved mapping by its ID."""
    try:
        mapping = db_execute("""
            SELECT id, name, import_type, mapping 
            FROM import_column_maps 
            WHERE id = ?
        """, (mapping_id,), fetch='one')

        if mapping is None:
            print("No mapping found with ID:", mapping_id)
            return jsonify(success=False, message="Mapping not found"), 404

        mapping_data = {
            'id': mapping['id'],
            'name': mapping['name'],
            'import_type': mapping['import_type'],
            'mapping': json.loads(mapping['mapping'])
        }

        print("Mapping data loaded:", mapping_data)
        return jsonify(success=True, mapping=mapping_data)
    except Exception as e:
        current_app.logger.error(f"Failed to load import mapping: {e}")
        return jsonify(success=False, message=str(e)), 500

@handson_bp.route('/process_import', methods=['POST'])
def process_import():
    content = request.get_json()

    if 'data' not in content or 'mapping' not in content:
        return jsonify(success=False, message="Data or mapping missing"), 400

    data = content['data']
    mapping = content['mapping']

    # Similar to your process_excess_list but for sales orders
    try:
        # Get mapped indices
        sales_order_ref_index = int(next(key for key, value in mapping.items()
                                         if value == 'sales_order_ref'))
        customer_code_index = int(next(key for key, value in mapping.items()
                                       if value == 'customer_system_code'))
        # Add other required fields...

        # Process each row
        for row in data:
            sales_order_ref = row[sales_order_ref_index]
            customer_code = row[customer_code_index]
            # Process other fields...

            # Insert data (you'll need to implement these functions)
            # create_sales_order(...)
            # create_sales_order_lines(...)

        return jsonify(success=True)

    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


# handson_routes.py (adding to existing file)

def create_or_match_part(system_part_number, raw_part_number):
    """
    Looks up part by system_part_number, creates if doesn't exist.
    Returns base_part_number.
    """
    try:
        with db_cursor(commit=True) as cur:
            result = _execute_with_cursor(cur, """
                SELECT base_part_number 
                FROM part_numbers 
                WHERE system_part_number = ?
            """, (system_part_number,)).fetchone()

            if result:
                return result['base_part_number']

            base_part_number = create_base_part_number(raw_part_number)
            insert_query = _with_returning_clause("""
                INSERT INTO part_numbers (base_part_number, part_number, system_part_number)
                VALUES (?, ?, ?)
            """)
            _execute_with_cursor(cur, insert_query, (base_part_number, raw_part_number, system_part_number))

        return base_part_number
    except Exception as e:
        current_app.logger.error(f"create_or_match_part failed: {e}")
        raise


# Modify your import routes to use get_current_import_step
@handson_bp.route('/import/start', methods=['POST'])
def start_import():
    content = request.get_json()
    mapping = content.get('mapping')
    file_id = content.get('file_id')

    print("START IMPORT MAPPING:", mapping)  # Debug line

    if not mapping or not file_id:
        return jsonify(success=False, message="Missing mapping or file_id"), 400

    current_step = get_current_import_step(file_id)
    print("RETURNING MAPPING:", mapping)  # Debug line

    return jsonify(
        success=True,
        next_step=current_step,
        mapping=mapping,
        file_id=file_id
    )


def update_import_status(file_id, import_type, processed, created, updated, skipped, errors, mapping=None):
    """Update the import status with proper error handling"""
    print(f"Updating import status for file_id: {file_id}, import_type: {import_type}")
    print(f"Processed: {processed}, Created: {created}, Updated: {updated}, Skipped: {skipped}, Errors: {errors}")
    print(f"Mapping: {mapping}")

    status = 'failed' if errors else 'completed'
    completed_at = 'CURRENT_TIMESTAMP' if status == 'completed' else 'NULL'

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, """
                UPDATE import_status 
                SET status = 'failed',
                    completed_at = CURRENT_TIMESTAMP
                WHERE file_id = ? 
                AND import_type = ? 
                AND completed_at IS NULL
            """, (file_id, import_type))

            _execute_with_cursor(cur, """
                INSERT INTO import_status 
                (file_id, import_type, processed, created, updated, skipped, errors, mapping, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                file_id,
                import_type,
                processed,
                created,
                updated,
                skipped,
                json.dumps(errors),
                json.dumps(mapping) if mapping else None,
                status
            ))

        print(f"Successfully updated status for {import_type} with status {status}")
    except Exception as e:
        current_app.logger.error(f"update_import_status failed: {e}")
        raise


@handson_bp.route('/import/parts', methods=['POST'])
def import_parts():
    content = request.get_json()
    mapping = content.get('mapping')
    file_id = content.get('file_id')

    print("DEBUG - Parts Import Starting:")
    print("Mapping:", mapping)
    print("File ID:", file_id)

    if not mapping or not file_id:
        return jsonify(success=False, message="Missing mapping or file_id", next_step="customers"), 400

    file_details = get_file_by_id(file_id)
    if not file_details:
        return jsonify(success=False, message="File not found", next_step="customers"), 404

    try:
        df = pd.read_excel(_resolve_file_path(file_details['filepath']))

        results = {
            'processed': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': []
        }

        # Get mapped column indices
        try:
            system_part_number_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'part_numbers.system_part_number'),
                None
            )
            part_number_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'part_numbers.part_number'),
                None
            )
        except StopIteration:
            return jsonify(success=False, message="Required part number mappings not found", next_step="customers"), 400

        if not system_part_number_col and not part_number_col:
            return jsonify(success=False, message="At least one part number field must be mapped", next_step="customers"), 400

        # Process rows in smaller batches
        batch_size = 100
        for batch_start in range(0, len(df), batch_size):
            batch_end = min(batch_start + batch_size, len(df))

            with db_cursor(commit=True) as cur:
                for idx in range(batch_start, batch_end):
                    row = df.iloc[idx]
                    try:
                        system_part_number = (str(row.iloc[int(system_part_number_col)]).strip()
                                              if system_part_number_col is not None
                                              else None)
                        part_number = (str(row.iloc[int(part_number_col)]).strip()
                                       if part_number_col is not None
                                       else None)

                        print(f"DEBUG - Raw values: system_part_number={system_part_number}, part_number={part_number}")

                        if not system_part_number and not part_number:
                            results['skipped'] += 1
                            continue

                        if not part_number:
                            part_number = system_part_number
                        if not system_part_number:
                            system_part_number = part_number

                        existing_row = _execute_with_cursor(cur, """
                            SELECT base_part_number, part_number, system_part_number
                            FROM part_numbers 
                            WHERE system_part_number = ? OR part_number = ?
                        """, (system_part_number, part_number)).fetchone()
                        existing = _row_to_dict(existing_row)

                        if existing:
                            needs_update = (
                                    existing['part_number'] != part_number or
                                    existing['system_part_number'] != system_part_number
                            )
                            if needs_update:
                                _execute_with_cursor(cur, """
                                    UPDATE part_numbers
                                    SET part_number = ?, system_part_number = ?
                                    WHERE base_part_number = ?
                                """, (part_number, system_part_number, existing['base_part_number']))
                                results['updated'] += 1
                            else:
                                results['skipped'] += 1
                        else:
                            try:
                                base_part_number = create_base_part_number(part_number)
                                _execute_with_cursor(cur, """
                                    INSERT INTO part_numbers (base_part_number, part_number, system_part_number)
                                    VALUES (?, ?, ?)
                                """, (base_part_number, part_number, system_part_number))
                                results['created'] += 1
                            except Exception as exc:
                                if _is_unique_violation(exc):
                                    existing_row = _execute_with_cursor(cur, """
                                        SELECT base_part_number
                                        FROM part_numbers 
                                        WHERE base_part_number = ?
                                    """, (base_part_number,)).fetchone()
                                    existing = _row_to_dict(existing_row)

                                    if existing:
                                        _execute_with_cursor(cur, """
                                            UPDATE part_numbers
                                            SET part_number = ?, system_part_number = ?
                                            WHERE base_part_number = ?
                                        """, (part_number, system_part_number, existing['base_part_number']))
                                        results['updated'] += 1
                                    else:
                                        results['errors'].append(
                                            f"Duplicate base part number on row {idx + 1} but couldn't find existing record")
                                        results['skipped'] += 1
                                else:
                                    raise

                        results['processed'] += 1

                    except Exception as e:
                        results['errors'].append(f"Error on row {idx + 1}: {str(e)}")
                        print(f"Error processing row {idx + 1}: {e}")
                        continue

        # Update import status
        try:
            update_import_status(
                file_id=file_id,
                import_type='parts',
                processed=results['processed'],
                created=results['created'],
                updated=results['updated'],
                skipped=results['skipped'],
                errors=results['errors'],
                mapping=mapping
            )
        except Exception as e:
            print(f"Error updating import status: {e}")

        print("DEBUG - Import Finished, Moving to Next Step: customers")  # ✅ Add this
        return jsonify(
            success=True,
            results=results,
            next_step="customers",  # ✅ Ensure this is always included
            mapping=mapping,
            file_id=file_id
        )

    except Exception as e:
        print(f"Error in import_parts: {str(e)}")
        return jsonify(success=False, message=str(e), next_step="customers"), 500

@handson_bp.route('/import/customers', methods=['POST'])
def import_customers():
    content = request.get_json()
    file_id = content.get('file_id')

    query = """
        SELECT mapping 
        FROM import_status 
        WHERE file_id = ? 
        AND import_type = 'parts'
        AND status = 'completed'
        AND mapping IS NOT NULL
        ORDER BY created_at DESC 
        LIMIT 1
    """
    mapping_record = db_execute(query, (file_id,), fetch='one')
    print(f"Found mapping record for file {file_id}:", mapping_record)

    if not mapping_record:
        return jsonify(success=False, message="No mapping found"), 400

    mapping = json.loads(mapping_record['mapping'])
    print("Parsed mapping:", mapping)

    print("Mapping:", mapping)  # Debug print
    print("File ID:", file_id)  # Debug print

    if not mapping or not file_id:
        print("Missing required data - mapping:", bool(mapping), "file_id:", bool(file_id))  # Debug print
        return jsonify(success=False,
                       message=f"Missing mapping or file_id. Got mapping={mapping}, file_id={file_id}"), 400

    file_details = get_file_by_id(file_id)
    if not file_details:
        return jsonify(success=False, message="File not found"), 404

    try:
        df = pd.read_excel(_resolve_file_path(file_details['filepath']))

        results = {
            'processed': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': []
        }

        try:
            system_code_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'customers.system_code'),
                None
            )
            name_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'customers.name'),
                None
            )
        except StopIteration:
            return jsonify(success=False, message="Required customer mappings not found"), 400

        if not system_code_col or not name_col:
            return jsonify(success=False, message="Both system_code and name must be mapped"), 400

        with db_cursor(commit=True) as cur:
            for idx, row in df.iterrows():
                try:
                    system_code = str(row.iloc[int(system_code_col)]).strip()
                    name = str(row.iloc[int(name_col)]).strip()

                    if not system_code or not name:
                        results['skipped'] += 1
                        continue

                    existing_row = _execute_with_cursor(cur, """
                        SELECT id, name 
                        FROM customers 
                        WHERE system_code = ?
                    """, (system_code,)).fetchone()
                    existing = _row_to_dict(existing_row)

                    if existing:
                        if existing['name'] != name:
                            _execute_with_cursor(cur, """
                                UPDATE customers 
                                SET name = ?
                                WHERE system_code = ?
                            """, (name, system_code))
                            results['updated'] += 1
                        else:
                            results['skipped'] += 1
                    else:
                        _execute_with_cursor(cur, """
                            INSERT INTO customers (system_code, name)
                            VALUES (?, ?)
                        """, (system_code, name))
                        results['created'] += 1

                    results['processed'] += 1

                except Exception as e:
                    results['errors'].append(f"Error on row {idx + 1}: {str(e)}")

        try:
            update_import_status(
                file_id=file_id,
                import_type='customers',
                processed=results['processed'],
                created=results['created'],
                updated=results['updated'],
                skipped=results['skipped'],
                errors=results['errors']
            )
        except Exception as e:
            print(f"Error updating import status: {e}")

        return jsonify(
            success=True,
            results=results,
            next_step='sales_orders',
            mapping=mapping,
            file_id=file_id
        )

    except Exception as e:
        print(f"Error in import_customers: {str(e)}")
        return jsonify(success=False, message=str(e)), 500


@handson_bp.route('/import/sales_orders', methods=['POST'])
def import_sales_orders():
    """Process sales orders from the import file"""
    content = request.get_json()
    mapping = content.get('mapping')
    file_id = content.get('file_id')

    print("Starting sales orders import with mapping:", mapping)  # Debug line

    if not mapping or not file_id:
        return jsonify(success=False, message="Missing mapping or file_id"), 400

    file_details = get_file_by_id(file_id)
    if not file_details:
        return jsonify(success=False, message="File not found"), 404

    try:
        df = pd.read_excel(_resolve_file_path(file_details['filepath']))

        results = {
            'processed': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': []
        }

        with db_cursor(commit=True) as cur:
            # Get required mapped column indices
            try:
                sales_order_ref_col = next(
                    col for col, map_val in mapping.items()
                    if map_val == 'sales_orders.sales_order_ref'
                )
                customer_system_code_col = next(
                    col for col, map_val in mapping.items()
                    if map_val == 'customers.system_code'
                )
                print(f"Found columns - SO Ref: {sales_order_ref_col}, Customer: {customer_system_code_col}")

            except StopIteration as e:
                print("Error finding required columns:", str(e))
                return jsonify(success=False,
                               message="Required fields not found in mapping."), 400

            # Optional fields mapping
            total_value_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'sales_orders.total_value'),
                None
            )
            customer_po_ref_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'sales_orders.customer_po_ref'),
                None
            )
            date_entered_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'sales_orders.date_entered'),
                None
            )

            salesperson_ref_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'salespeople.system_ref'),
                None
            )

            # Process each row
            for idx, row in df.iterrows():
                try:
                    row_num = idx + 1
                    print(f"\n--- Processing row {row_num} ---")

                    # Get required fields with detailed debugging
                    try:
                        sales_order_ref_raw = row.iloc[int(sales_order_ref_col)]
                        sales_order_ref = str(sales_order_ref_raw).strip() if pd.notna(sales_order_ref_raw) else ""
                        print(
                            f"Row {row_num}: Sales Order Ref raw='{sales_order_ref_raw}', processed='{sales_order_ref}'")
                    except Exception as e:
                        print(f"Row {row_num}: Error getting sales_order_ref: {e}")
                        sales_order_ref = ""

                    try:
                        customer_system_code_raw = row.iloc[int(customer_system_code_col)]
                        customer_system_code = str(customer_system_code_raw).strip() if pd.notna(
                            customer_system_code_raw) else ""
                        print(
                            f"Row {row_num}: Customer System Code raw='{customer_system_code_raw}', processed='{customer_system_code}'")
                    except Exception as e:
                        print(f"Row {row_num}: Error getting customer_system_code: {e}")
                        customer_system_code = ""

                    # Check for missing required fields with specific reasons
                    skip_reasons = []
                    if not sales_order_ref or sales_order_ref.lower() in ['nan', 'none', '']:
                        skip_reasons.append("Missing/empty sales_order_ref")
                    if not customer_system_code or customer_system_code.lower() in ['nan', 'none', '']:
                        skip_reasons.append("Missing/empty customer_system_code")

                    if skip_reasons:
                        skip_reason = f"Row {row_num}: " + ", ".join(skip_reasons)
                        print(f"SKIPPING: {skip_reason}")
                        results['errors'].append(skip_reason)
                        results['skipped'] += 1
                        continue

                    # Process salesperson with debugging
                    salesperson_id = 1  # Default
                    if salesperson_ref_col is not None:
                        try:
                            salesperson_ref = str(row.iloc[salesperson_ref_col]).strip()
                            if salesperson_ref:
                                salesperson_row = _execute_with_cursor(cur, """
                                    SELECT id FROM salespeople 
                                    WHERE system_ref = ?
                                """, (salesperson_ref,)).fetchone()
                                salesperson = _row_to_dict(salesperson_row)
                                if salesperson:
                                    salesperson_id = salesperson['id']
                        except (ValueError, TypeError):
                            pass

                    # Get optional fields with debugging
                    customer_po_ref = None
                    if customer_po_ref_col is not None:
                        try:
                            customer_po_ref_raw = row.iloc[int(customer_po_ref_col)]
                            customer_po_ref = str(customer_po_ref_raw).strip() if pd.notna(
                                customer_po_ref_raw) else None
                            print(
                                f"Row {row_num}: Customer PO Ref raw='{customer_po_ref_raw}', processed='{customer_po_ref}'")
                        except Exception as e:
                            print(f"Row {row_num}: Error getting customer_po_ref: {e}")

                    date_entered = None
                    if date_entered_col is not None:
                        try:
                            raw_date = row.iloc[int(date_entered_col)]
                            print(f"Row {row_num}: Raw date value: '{raw_date}', type: {type(raw_date)}")
                            date_entered = validate_date(raw_date)
                            print(f"Row {row_num}: Validated date: '{date_entered}'")
                        except ValueError as e:
                            print(f"Row {row_num}: Date validation error: {e}, using current date")
                            date_entered = datetime.now().strftime('%Y-%m-%d')
                        except Exception as e:
                            print(f"Row {row_num}: Unexpected date error: {e}, using current date")
                            date_entered = datetime.now().strftime('%Y-%m-%d')

                    # Get total value if mapped with debugging
                    total_value = None
                    if total_value_col is not None:
                        try:
                            raw_value = row.iloc[int(total_value_col)]
                            print(f"Row {row_num}: Raw total value: '{raw_value}', type: {type(raw_value)}")
                            if pd.notna(raw_value):
                                total_value = float(str(raw_value).replace(',', '').replace('$', '').strip())
                                print(f"Row {row_num}: Processed total value: {total_value}")
                            else:
                                print(f"Row {row_num}: Total value is NaN/None")
                        except (ValueError, TypeError) as e:
                            print(f"Row {row_num}: Value conversion error: {e}")

                    # Get customer details with debugging
                    print(f"Row {row_num}: Looking up customer with system_code: '{customer_system_code}'")
                    customer_row = _execute_with_cursor(cur, """
                        SELECT id, currency_id 
                        FROM customers 
                        WHERE system_code = ?
                    """, (customer_system_code,)).fetchone()
                    customer = _row_to_dict(customer_row)

                    if not customer:
                        skip_reason = f"Row {row_num}: Customer not found for system_code '{customer_system_code}'"
                        print(f"SKIPPING: {skip_reason}")
                        results['errors'].append(skip_reason)
                        results['skipped'] += 1
                        continue

                    print(
                        f"Row {row_num}: Found customer ID {customer['id']} with currency_id {customer['currency_id']}")

                    # Check if order exists
                    print(f"Row {row_num}: Checking if sales order exists with ref: '{sales_order_ref}'")
                    existing_order_row = _execute_with_cursor(cur, """
                        SELECT id, total_value, customer_po_ref, date_entered, salesperson_id
                        FROM sales_orders 
                        WHERE sales_order_ref = ?
                    """, (sales_order_ref,)).fetchone()
                    existing_order = _row_to_dict(existing_order_row)

                    if existing_order:
                        print(f"Row {row_num}: Found existing order ID {existing_order['id']}")
                        # Check if any fields need updating
                        needs_update = False
                        update_fields = []
                        update_values = []

                        if total_value is not None and existing_order['total_value'] != total_value:
                            print(
                                f"Row {row_num}: Total value changed from {existing_order['total_value']} to {total_value}")
                            update_fields.append("total_value = ?")
                            update_values.append(total_value)
                            needs_update = True

                        existing_salesperson_id = existing_order['salesperson_id'] if existing_order[
                                                                                          'salesperson_id'] is not None else 1
                        if salesperson_ref_col is not None and salesperson_id != existing_salesperson_id:
                            print(
                                f"Row {row_num}: Salesperson ID changed from {existing_salesperson_id} to {salesperson_id}")
                            update_fields.append("salesperson_id = ?")
                            update_values.append(salesperson_id)
                            needs_update = True

                        if customer_po_ref and existing_order['customer_po_ref'] != customer_po_ref:
                            print(
                                f"Row {row_num}: Customer PO ref changed from '{existing_order['customer_po_ref']}' to '{customer_po_ref}'")
                            update_fields.append("customer_po_ref = ?")
                            update_values.append(customer_po_ref)
                            needs_update = True

                        if date_entered and existing_order['date_entered'] != date_entered:
                            print(
                                f"Row {row_num}: Date entered changed from '{existing_order['date_entered']}' to '{date_entered}'")
                            update_fields.append("date_entered = ?")
                            update_values.append(date_entered)
                            needs_update = True

                        if needs_update:
                            update_sql = f"""
                                UPDATE sales_orders 
                                SET {', '.join(update_fields)}
                                WHERE id = ?
                            """
                            update_values.append(existing_order['id'])
                            print(f"Row {row_num}: Updating order with SQL: {update_sql}")
                            print(f"Row {row_num}: Update values: {update_values}")
                            _execute_with_cursor(cur, update_sql, update_values)
                            results['updated'] += 1
                            print(f"Row {row_num}: SUCCESS - Updated existing order")
                        else:
                            print(f"Row {row_num}: No changes needed, skipping")
                            results['skipped'] += 1
                    else:
                        # Insert new order
                        print(f"Row {row_num}: Creating new sales order")
                        insert_values = (
                            sales_order_ref,
                            customer['id'],
                            customer_po_ref,
                            date_entered or datetime.now().strftime('%Y-%m-%d'),
                            customer['currency_id'] or 1,
                            1,  # Default sales status
                            salesperson_id,
                            total_value
                        )
                        print(f"Row {row_num}: Insert values: {insert_values}")

                        _execute_with_cursor(cur, """
                            INSERT INTO sales_orders (
                                sales_order_ref,
                                customer_id,
                                customer_po_ref,
                                date_entered,
                                currency_id,
                                sales_status_id,
                                salesperson_id,
                                total_value
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, insert_values)
                        results['created'] += 1
                        print(f"Row {row_num}: SUCCESS - Created new order")

                    results['processed'] += 1

                except Exception as e:
                    error_msg = f"Row {row_num}: Unexpected error - {str(e)}"
                    print(f"ERROR: {error_msg}")
                    print(f"Row {row_num}: Exception type: {type(e).__name__}")
                    import traceback
                    traceback.print_exc()
                    results['errors'].append(error_msg)
                    results['skipped'] += 1


        print(f"\n=== FINAL RESULTS ===")
        print(f"Sales orders import results: {results}")

        try:
            # Explicitly pass 'sales_orders' as import_type
            update_import_status(
                file_id=file_id,
                import_type='sales_orders',  # Make sure this is correct
                processed=results['processed'],
                created=results['created'],
                updated=results['updated'],
                skipped=results['skipped'],
                errors=results['errors']
            )
        except Exception as e:
            print(f"Error updating import status: {e}")

        return jsonify(
            success=True,
            results=results,
            next_step='order_lines',
            mapping=mapping,
            file_id=file_id
        )

    except Exception as e:
        print(f"Error in import_sales_orders: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify(success=False, message=str(e)), 500

@handson_bp.route('/validate_date', methods=['POST'])
def api_validate_date():
    """API endpoint to validate a date string and return standardized format"""
    content = request.get_json()
    date_str = content.get('date')

    try:
        validated_date = validate_date(date_str)
        return jsonify(success=True, valid=True, validated_date=validated_date)
    except ValueError as e:
        return jsonify(success=True, valid=False, error=str(e))
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

def validate_date(date_str):
    """
    Enhanced date validation function that handles multiple formats and Excel dates
    Returns formatted date string (YYYY-MM-DD) if valid, None if empty, raises ValueError if invalid
    """
    if not date_str or pd.isna(date_str):
        return None

    # If it's already a datetime (from pandas)
    if isinstance(date_str, (datetime, pd.Timestamp)):
        return date_str.strftime('%Y-%m-%d')

    # If it's a number (could be Excel date)
    if isinstance(date_str, (int, float)):
        try:
            # Excel dates are days since 1900-01-01 (with some quirks)
            # Pandas has a function to convert Excel dates
            date = pd.to_datetime('1899-12-30') + pd.Timedelta(days=int(date_str))
            return date.strftime('%Y-%m-%d')
        except Exception:
            pass

    # Convert to string if not already
    date_str = str(date_str).strip()

    try:
        # Try parsing with various formats
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%m-%d-%Y', '%Y/%m/%d', '%d.%m.%Y', '%m.%d.%Y'):
            try:
                return datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue

        # Try pandas datetime parsing as a last resort
        return pd.to_datetime(date_str).strftime('%Y-%m-%d')

    except Exception as e:
        raise ValueError(f"Invalid date format: {date_str}. Please use YYYY-MM-DD format.")


@handson_bp.route('/import/status/<int:file_id>', methods=['GET'])
def import_status(file_id):
    """Get current import status and results for a specific file import"""
    with db_cursor() as cur:
        # Get all status records for this file
        results = _execute_with_cursor(cur, """
            SELECT import_type, processed, created, skipped, errors, completed_at, status
            FROM import_status
            WHERE file_id = ?
            ORDER BY created_at DESC
        """, (file_id,)).fetchall()

        # Initialize status dictionary
        status = {
            'parts': None,
            'customers': None,
            'sales_orders': None,
            'order_lines': None
        }

        # Process results
        for result in results:
            step = result['import_type']
            if step not in status or status[step] is None:  # Only take the latest status for each step
                status[step] = {
                    'processed': result['processed'],
                    'created': result['created'],
                    'skipped': result['skipped'],
                    'errors': json.loads(result['errors'] or '[]'),
                    'completed': result['completed_at'] is not None and result['status'] == 'completed',
                    'status': result['status']
                }

        # Get current step
        current_step = get_current_import_step(file_id)

        # Calculate if the whole process is complete
        step_order = ['parts', 'customers', 'sales_orders', 'order_lines']
        completed = all(
            status[step] and status[step]['completed']
            for step in step_order
        )

        print(f"Status check - Current step: {current_step}, Complete: {completed}")

        return jsonify({
            'status': status,
            'current_step': current_step if not completed else 'complete',
            'completed': completed,
            'has_errors': any(
                status[step] and status[step]['errors']
                for step in step_order
                if status[step]
            )
        })

def get_current_import_step(file_id):
    """Get the current import step, taking into account failed steps"""
    with db_cursor() as cur:
        # Define the step order
        step_order = ['parts', 'customers', 'sales_orders', 'order_lines']

        # Get all import status records for this file
        steps = _execute_with_cursor(cur, """
            SELECT import_type, status, completed_at
            FROM import_status
            WHERE file_id = ?
            ORDER BY created_at DESC
        """, (file_id,)).fetchall()

        # Create a dictionary of the latest status for each step
        step_status = {}
        for step in steps:
            if step['import_type'] not in step_status:
                step_status[step['import_type']] = {
                    'status': step['status'],
                    'completed': step['completed_at'] is not None and step['status'] == 'completed'
                }

        # Find the first incomplete or failed step
        for step in step_order:
            if step not in step_status or not step_status[step]['completed']:
                return step

        return 'complete'


def update_imdport_status(file_id, import_type, processed, created, updated, skipped, errors, mapping=None):
    """Update the import status with proper error handling"""
    status = 'failed' if errors else 'completed'

    with db_cursor(commit=True) as cur:
        _execute_with_cursor(cur, """
            INSERT INTO import_status 
            (file_id, import_type, processed, created, updated, skipped, errors, mapping, status, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            file_id,
            import_type,
            processed,
            created,
            updated,
            skipped,
            json.dumps(errors),
            json.dumps(mapping) if mapping else None,
            status
        ))


@handson_bp.route('/import/order_lines', methods=['POST'])
def import_order_lines():
    """Process order lines from the import file with batch processing"""
    content = request.get_json()
    mapping = content.get('mapping')
    file_id = content.get('file_id')
    update_existing = content.get('update_existing', False)

    print(f"Starting order lines import with mapping: {mapping}, update_existing: {update_existing}")

    if not mapping or not file_id:
        return jsonify(success=False, message="Missing mapping or file_id"), 400

    file_details = get_file_by_id(file_id)
    if not file_details:
        return jsonify(success=False, message="File not found"), 404

    try:
        df = pd.read_excel(_resolve_file_path(file_details['filepath']))

        results = {
            'processed': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': []
        }

        # Get required mapped column indices
        try:
            sales_order_ref_col = next(
                col for col, map_val in mapping.items()
                if map_val == 'sales_orders.sales_order_ref'
            )
            system_part_number_col = next(
                col for col, map_val in mapping.items()
                if map_val == 'part_numbers.system_part_number'
            )
            line_number_col = next(
                col for col, map_val in mapping.items()
                if map_val == 'sales_order_lines.line_number'
            )
            quantity_col = next(
                col for col, map_val in mapping.items()
                if map_val == 'sales_order_lines.quantity'
            )
        except StopIteration as e:
            print(f"Mapping error: {e}")
            return jsonify(success=False,
                           message="Required fields not found in mapping. Need sales_order_ref, system_part_number, line_number, and quantity"), 400

        # Optional fields
        price_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_order_lines.price'),
            None
        )

        # GET shipped quantity column mapping
        shipped_quantity_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_order_lines.shipped_quantity'),
            None
        )

        # Date fields mapping
        delivery_date_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_order_lines.delivery_date'),
            None
        )
        requested_date_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_order_lines.requested_date'),
            None
        )
        promise_date_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_order_lines.promise_date'),
            None
        )
        ship_date_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_order_lines.ship_date'),
            None
        )

        note_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_order_lines.note'),
            None
        )

        # Pre-fetch data to reduce database queries
        with db_cursor() as cur:
            # Get all sales orders
            all_sales_orders = {
                row['sales_order_ref']: row['id']
                for row in _execute_with_cursor(cur, "SELECT id, sales_order_ref FROM sales_orders").fetchall()
            }

            # Get all parts
            all_parts = {
                row['system_part_number']: row['base_part_number']
                for row in _execute_with_cursor(cur, "SELECT system_part_number, base_part_number FROM part_numbers").fetchall()
            }

            # Get existing order lines
            existing_lines = {}
            for row in _execute_with_cursor(cur, """
                SELECT sol.id, so.sales_order_ref, sol.line_number 
                FROM sales_order_lines sol
                JOIN sales_orders so ON so.id = sol.sales_order_id
            """).fetchall():
                line_key = f"{row['sales_order_ref']}_{row['line_number']}"
                existing_lines[line_key] = row['id']

        # Process rows in smaller batches
        batch_size = 100
        for batch_start in range(0, len(df), batch_size):
            batch_end = min(batch_start + batch_size, len(df))

            with db_cursor(commit=True) as cur:
                for idx in range(batch_start, batch_end):
                    try:
                        row = df.iloc[idx]

                        # Get required fields
                        sales_order_ref = str(row.iloc[int(sales_order_ref_col)]).strip()
                        system_part_number = str(row.iloc[int(system_part_number_col)]).strip()
                        line_number = int(str(row.iloc[int(line_number_col)]).strip())

                        try:
                            quantity = float(str(row.iloc[int(quantity_col)]).strip())
                        except ValueError:
                            results['errors'].append(f"Row {idx + 1}: Invalid quantity format")
                            results['skipped'] += 1
                            continue

                        # Get shipped quantity if mapped
                        shipped_quantity = None
                        if shipped_quantity_col is not None:
                            try:
                                shipped_qty_value = row.iloc[int(shipped_quantity_col)]
                                if pd.notna(shipped_qty_value):
                                    shipped_quantity = float(str(shipped_qty_value).strip())
                                    # Validate that shipped quantity doesn't exceed total quantity
                                    if shipped_quantity > quantity:
                                        results['errors'].append(
                                            f"Row {idx + 1}: Shipped quantity ({shipped_quantity}) exceeds total quantity ({quantity})")
                                        shipped_quantity = quantity  # Cap at total quantity
                            except (ValueError, TypeError) as e:
                                print(f"Error parsing shipped quantity on row {idx + 1}: {e}")

                        # Skip if any required field is missing
                        if not sales_order_ref or not system_part_number or not quantity:
                            results['errors'].append(f"Row {idx + 1}: Missing required fields")
                            results['skipped'] += 1
                            continue

                        # Get price if mapped
                        price = None
                        if price_col is not None:
                            try:
                                price = float(str(row.iloc[int(price_col)]).strip())
                            except (ValueError, TypeError):
                                pass  # Price will remain None if invalid

                        # Get date fields if mapped
                        delivery_date = None
                        if delivery_date_col is not None:
                            try:
                                raw_date = row.iloc[int(delivery_date_col)]
                                delivery_date = validate_date(raw_date)
                            except (ValueError, TypeError) as e:
                                print(f"Error parsing delivery date on row {idx + 1}: {e}")

                        requested_date = None
                        if requested_date_col is not None:
                            try:
                                raw_date = row.iloc[int(requested_date_col)]
                                requested_date = validate_date(raw_date)
                            except (ValueError, TypeError) as e:
                                print(f"Error parsing requested date on row {idx + 1}: {e}")

                        promise_date = None
                        if promise_date_col is not None:
                            try:
                                raw_date = row.iloc[int(promise_date_col)]
                                promise_date = validate_date(raw_date)
                            except (ValueError, TypeError) as e:
                                print(f"Error parsing promise date on row {idx + 1}: {e}")

                        ship_date = None
                        if ship_date_col is not None:
                            try:
                                raw_date = row.iloc[int(ship_date_col)]
                                ship_date = validate_date(raw_date)
                            except (ValueError, TypeError) as e:
                                print(f"Error parsing ship date on row {idx + 1}: {e}")

                        note = None
                        if note_col is not None:
                            try:
                                note = str(row.iloc[int(note_col)]).strip() or None
                            except (ValueError, TypeError):
                                pass

                        # Check if sales order exists using pre-fetched data
                        sales_order_id = all_sales_orders.get(sales_order_ref)
                        if not sales_order_id:
                            results['errors'].append(
                                f"Row {idx + 1}: Sales order not found: {sales_order_ref}"
                            )
                            results['skipped'] += 1
                            continue

                        # Check if part exists using pre-fetched data
                        base_part_number = all_parts.get(system_part_number)
                        if not base_part_number:
                            results['errors'].append(
                                f"Row {idx + 1}: Part not found: {system_part_number}"
                            )
                            results['skipped'] += 1
                            continue

                        # Check if this line already exists
                        line_key = f"{sales_order_ref}_{line_number}"
                        existing_line_id = existing_lines.get(line_key)

                        if existing_line_id:
                            # If we don't want to update existing lines, skip
                            if not update_existing:
                                results['skipped'] += 1
                                continue

                            # Update the existing line with any changed fields
                            update_fields = []
                            update_values = []

                            # Build the update SQL dynamically based on provided fields
                            if quantity is not None:
                                update_fields.append("quantity = ?")
                                update_values.append(quantity)

                            if price is not None:
                                update_fields.append("price = ?")
                                update_values.append(price)

                            if delivery_date is not None:
                                update_fields.append("delivery_date = ?")
                                update_values.append(delivery_date)

                            if requested_date is not None:
                                update_fields.append("requested_date = ?")
                                update_values.append(requested_date)

                            if promise_date is not None:
                                update_fields.append("promise_date = ?")
                                update_values.append(promise_date)

                            if ship_date is not None:
                                update_fields.append("ship_date = ?")
                                update_values.append(ship_date)

                            # Include shipped_quantity in updates if provided
                            if shipped_quantity is not None:
                                update_fields.append("shipped_quantity = ?")
                                update_values.append(shipped_quantity)

                            if note is not None:
                                update_fields.append("note = ?")
                                update_values.append(note)

                            # Only update if there are fields to update
                            if update_fields:
                                update_fields.append("updated_at = CURRENT_TIMESTAMP")

                                # Build the SQL statement
                                sql = f"""
                                    UPDATE sales_order_lines 
                                    SET {', '.join(update_fields)}
                                    WHERE id = ?
                                """

                                # Add the line ID to the values
                                update_values.append(existing_line_id)

                                _execute_with_cursor(cur, sql, update_values)
                                results['updated'] += 1
                            else:
                                results['skipped'] += 1
                        else:
                            # Insert new line
                            insert_line_sql = _with_returning_clause("""
                                INSERT INTO sales_order_lines (
                                    sales_order_id,
                                    line_number,
                                    base_part_number,
                                    quantity,
                                    price,
                                    delivery_date,
                                    requested_date,
                                    promise_date,
                                    ship_date,
                                    sales_status_id,
                                    note,
                                    shipped_quantity, 
                                    created_at,
                                    updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """)

                            _execute_with_cursor(cur, insert_line_sql, (
                                sales_order_id,
                                line_number,
                                base_part_number,
                                quantity,
                                price,
                                delivery_date,
                                requested_date,
                                promise_date,
                                ship_date,
                                1,  # Default status
                                note,
                                shipped_quantity or 0  # Default to 0 if None
                            ))

                            # Add to existing lines map to prevent duplicates in the same batch
                            new_line_id = _last_inserted_id(cur)
                            existing_lines[line_key] = new_line_id

                            results['created'] += 1

                        results['processed'] += 1

                    except Exception as e:
                        print(f"Error processing row {idx + 1}: {e}")
                        results['errors'].append(f"Row {idx + 1}: {str(e)}")
                        results['skipped'] += 1
                        continue


        # ===== ADD THIS NEW SECTION AFTER THE MAIN IMPORT IS COMPLETE =====
        # Calculate total values for sales orders that don't have them set
        print("\n=== CALCULATING MISSING TOTAL VALUES ===")
        try:
            with db_cursor(commit=True) as cur:
                # Find sales orders that need total value calculation
                # (either NULL or 0, and have order lines with prices)
                orders_needing_totals = _execute_with_cursor(cur, """
                    SELECT DISTINCT so.id, so.sales_order_ref, so.total_value
                    FROM sales_orders so
                    INNER JOIN sales_order_lines sol ON so.id = sol.sales_order_id
                    WHERE (so.total_value IS NULL OR so.total_value = 0)
                    AND sol.price IS NOT NULL
                """).fetchall()

                calculated_count = 0
                for order in orders_needing_totals:
                    try:
                        total_calc = _execute_with_cursor(cur, """
                            SELECT COALESCE(SUM(quantity * COALESCE(price, 0)), 0) as calculated_total
                            FROM sales_order_lines 
                            WHERE sales_order_id = ? AND price IS NOT NULL
                        """, (order['id'],)).fetchone()

                        calculated_total = total_calc['calculated_total'] if total_calc else 0

                        if calculated_total > 0:
                            _execute_with_cursor(cur, """
                                UPDATE sales_orders 
                                SET total_value = ?
                                WHERE id = ?
                            """, (calculated_total, order['id']))

                            calculated_count += 1
                            print(
                                f"Calculated total_value {calculated_total} for order {order['sales_order_ref']} (was {order['total_value']})")
                        else:
                            print(f"Order {order['sales_order_ref']}: No valid order lines with prices found")

                    except Exception as e:
                        error_msg = f"Error calculating total for order {order['sales_order_ref']}: {str(e)}"
                        print(f"ERROR: {error_msg}")
                        results['errors'].append(error_msg)

                if calculated_count > 0:
                    print(f"Automatically calculated total_value for {calculated_count} sales orders")
                    results['total_values_calculated'] = calculated_count
                else:
                    print("No sales orders needed total value calculation")

        except Exception as e:
            print(f"Error in total value calculation: {e}")
            results['errors'].append(f"Total value calculation error: {str(e)}")
        # ===== END OF NEW SECTION =====

        # Update import status
        try:
            update_import_status(
                file_id=file_id,
                import_type='order_lines',
                processed=results['processed'],
                created=results['created'],
                updated=results['updated'],
                skipped=results['skipped'],
                errors=results['errors']
            )
        except Exception as e:
            print(f"Error updating import status: {e}")

        return jsonify(
            success=True,
            results=results,
            next_step='complete',
            mapping=mapping,
            file_id=file_id
        )

    except Exception as e:
        print(f"Error in import_order_lines: {str(e)}")
        return jsonify(success=False, message=str(e)), 500

def get_dynamic_mapping_fields():
    """Return all available fields for mapping, including date fields"""
    return {
        'ignore': [
            {'value': 'ignore', 'label': 'Ignore this column', 'required': False}
        ],
        'sales_orders': [
            {'value': 'sales_order_ref', 'label': 'Sales Order Reference', 'required': True},
            {'value': 'date_entered', 'label': 'Date Entered', 'required': False},
            {'value': 'customer_po_ref', 'label': 'Customer PO Reference', 'required': False},
            {'value': 'total_value', 'label': 'Total Value', 'required': False}
        ],
        'part_numbers': [
            {'value': 'system_part_number', 'label': 'System Part Number', 'required': True},
            {'value': 'part_number', 'label': 'Part Number', 'required': False},
            {'value': 'description', 'label': 'Description', 'required': False},
            {'value': 'manufacturer', 'label': 'Manufacturer', 'required': False}
        ],
        'customers': [
            {'value': 'system_code', 'label': 'Customer System Code', 'required': True},
            {'value': 'name', 'label': 'Customer Name', 'required': True},
            {'value': 'contact_name', 'label': 'Contact Name', 'required': False},
            {'value': 'email', 'label': 'Email', 'required': False},
            {'value': 'phone', 'label': 'Phone', 'required': False}
        ],
        'sales_order_lines': [
            {'value': 'line_number', 'label': 'Line Number', 'required': True},
            {'value': 'quantity', 'label': 'Quantity', 'required': True},
            {'value': 'shipped_quantity', 'label': 'Shipped Quantity', 'required': False},  # Add this line
            {'value': 'price', 'label': 'Price', 'required': False},
            {'value': 'delivery_date', 'label': 'Delivery Date', 'required': False},
            {'value': 'requested_date', 'label': 'Requested Date', 'required': False},
            {'value': 'promise_date', 'label': 'Promise Date', 'required': False},
            {'value': 'ship_date', 'label': 'Ship Date', 'required': False},
            {'value': 'note', 'label': 'Note', 'required': False}
        ]
    }

@handson_bp.route('/import/progress/<int:file_id>', methods=['GET'])
def import_progress(file_id):
    """Show import progress page"""
    file_details = get_file_by_id(file_id)
    if not file_details:
        return "File not found", 404

    # Get current import status
    status = db_execute("""
        SELECT import_type, processed, created, updated, skipped, errors,
               created_at, completed_at, status
        FROM import_status
        WHERE file_id = ?
        ORDER BY created_at DESC
    """, (file_id,), fetch='all') or []

    # Process status into a more usable format
    status_dict = {}
    steps = ['parts', 'customers', 'sales_orders', 'order_lines']

    # Track latest status for each step
    for step in steps:
        # Get the most recent status entry for this step
        step_status = next((s for s in status if s['import_type'] == step), None)

        if step_status:
            # Check explicitly if step is completed (completed_at is not None AND status is 'completed')
            is_completed = (step_status['completed_at'] is not None and
                            step_status['status'] == 'completed')

            status_dict[step] = {
                'processed': step_status['processed'] or 0,
                'created': step_status['created'] or 0,
                'updated': step_status['updated'] or 0,
                'skipped': step_status['skipped'] or 0,
                'errors': json.loads(step_status['errors'] or '[]'),
                'completed': is_completed,
                'total': (step_status['processed'] or 0) + (step_status['skipped'] or 0)
            }
        else:
            status_dict[step] = {
                'processed': 0,
                'created': 0,
                'updated': 0,
                'skipped': 0,
                'errors': [],
                'completed': False,
                'total': 0
            }

        # Find current step (first incomplete step)
        current_step = None
        completed = True
        for step in steps:
            if not status_dict[step]['completed']:
                current_step = step
                completed = False
                break

        # Calculate progress percentage based on completed steps
        completed_steps = sum(1 for step in steps if status_dict[step]['completed'])
        progress_percentage = (completed_steps / len(steps)) * 100

    # Get mapping information
    mapping_query = """
        SELECT mapping 
        FROM import_column_maps 
        WHERE file_id = ? 
        ORDER BY created_at DESC 
        LIMIT 1
    """
    mapping = db_execute(mapping_query, (file_id,), fetch='one')
    mapping_dict = json.loads(mapping['mapping']) if mapping else {}

    return render_template('import_progress.html',
                           file_id=file_id,
                           status=status_dict,
                           current_step=current_step,
                           completed=completed,
                           progress_percentage=progress_percentage,
                           mapping=mapping_dict)


@handson_bp.route('/import/setup', methods=['GET'])
def import_setup():
    """Render the import setup page."""
    # Get the last saved directory (if any)
    directory_record = db_execute("SELECT directory FROM import_settings ORDER BY id DESC LIMIT 1", fetch='one')
    directory = directory_record['directory'] if directory_record else ""

    # Get saved mappings
    mappings = db_execute("""
        SELECT id, name, import_type 
        FROM import_column_maps 
        ORDER BY import_type, name
    """, fetch='all') or []

    return render_template('import_setup.html', directory=directory, mappings=mappings)


@handson_bp.route('/import/extract_directory', methods=['POST'])
def extract_directory():
    """Extracts the correct directory from the selected file."""
    if 'file' not in request.files:
        return jsonify(success=False, message="No file uploaded"), 400

    file = request.files['file']
    file_path = file.filename  # This is just the file name, not the full path

    if not file_path:
        return jsonify(success=False, message="Invalid file path"), 400

    # Ensure correct directory extraction (strip file name)
    directory = os.path.dirname(os.path.abspath(file_path))

    print("Extracted directory:", directory)  # Debugging

    return jsonify(success=True, directory=directory)


@handson_bp.route('/import/save_directory', methods=['POST'])
def save_directory():
    """Saves the directory and mapping ID, then starts watching for new files."""
    content = request.get_json()
    directory = content.get('directory')
    mapping_id = content.get('mapping_id')

    if not directory or not os.path.isdir(directory):
        return jsonify(success=False, message="Invalid directory. Please enter a valid path."), 400
    if not mapping_id:
        return jsonify(success=False, message="Mapping selection is required."), 400

    # Save directory & mapping ID in the database
    db_execute("INSERT INTO import_settings (directory, mapping_id) VALUES (?, ?)",
               (directory, mapping_id), commit=True)

    # Start folder watcher in a background thread
    watcher_thread = threading.Thread(target=start_folder_watcher, args=(directory, mapping_id), daemon=True)
    watcher_thread.start()

    return jsonify(success=True, message="Directory and mapping saved. Watching for new files.")



@handson_bp.route('/import/get_directory', methods=['GET'])
def get_directory():
    """Gets the currently configured watch directory."""
    result = db_execute(
        "SELECT directory FROM import_settings ORDER BY id DESC LIMIT 1",
        fetch='one'
    )

    directory = result['directory'] if result else ""
    return jsonify(success=True, directory=directory)


@handson_bp.route('/import/list_directories', methods=['GET'])
def list_directories():
    """Lists available directories on the server."""
    base_dir = current_app.config.get('BASE_IMPORT_DIR', 'c:/')
    directories = []

    for entry in os.scandir(base_dir):
        if entry.is_dir():
            directories.append({
                'path': entry.path,
                'name': entry.name
            })

    return jsonify(success=True, directories=directories)


@handson_bp.route('/import/start_full', methods=['POST'])
def start_full_import():
    """API endpoint to start the full import process manually."""
    content = request.get_json()
    file_id = content.get('file_id')
    mapping_id = content.get('mapping_id')

    if not file_id or not mapping_id:
        return jsonify(success=False, message="Missing file_id or mapping_id"), 400

    return run_full_import(file_id, mapping_id)

import requests

def run_full_import(file_id, mapping_id):
    """Runs the entire import process sequentially."""
    print(f"Starting full import for file {file_id} using mapping {mapping_id}")

    base_url = "http://127.0.0.1:5000"

    # Load the saved mapping so the import steps can run
    mapping_record = db_execute(
        "SELECT mapping FROM import_column_maps WHERE id = ?",
        (mapping_id,),
        fetch='one'
    )

    if not mapping_record:
        print(f"❌ Error: No mapping found for ID {mapping_id}")
        return {"success": False, "message": "Mapping not found"}

    try:
        mapping = json.loads(mapping_record['mapping'])  # 🔹 Convert JSON string to dictionary
    except json.JSONDecodeError:
        print(f"❌ Error: Invalid JSON format in mapping ID {mapping_id}")
        return {"success": False, "message": "Invalid mapping format"}

    # ✅ Step 1: Import parts
    print("Running parts import...")
    response = requests.post(f"{base_url}/handson/import/parts", json={"file_id": file_id, "mapping": mapping})
    response_json = response.json()
    print("DEBUG - Parts Import Response:", response_json)

    if response.status_code != 200 or not response_json.get("success"):
        print(f"Error in parts import: {response.text}")
        return response.json()

    next_step = response_json.get("next_step")
    if next_step != "customers":
        print("❌ ERROR - Unexpected next_step after parts:", next_step)
        return {"success": False, "message": "Unexpected next step after parts"}

    # ✅ Step 2: Import customers
    print("Running customers import...")
    response = requests.post(f"{base_url}/handson/import/customers", json={"file_id": file_id, "mapping": mapping})
    response_json = response.json()
    print("DEBUG - Customers Import Response:", response_json)

    if response.status_code != 200 or not response_json.get("success"):
        print(f"Error in customers import: {response.text}")
        return response.json()

    next_step = response_json.get("next_step")
    if next_step != "sales_orders":
        print("❌ ERROR - Unexpected next_step after customers:", next_step)
        return {"success": False, "message": "Unexpected next step after customers"}

    # ✅ Step 3: Import sales orders
    print("Running sales orders import...")
    response = requests.post(f"{base_url}/handson/import/sales_orders", json={"file_id": file_id, "mapping": mapping})
    response_json = response.json()
    print("DEBUG - Sales Orders Import Response:", response_json)

    if response.status_code != 200 or not response_json.get("success"):
        print(f"Error in sales orders import: {response.text}")
        return response.json()

    next_step = response_json.get("next_step")
    if next_step != "order_lines":
        print("❌ ERROR - Unexpected next_step after sales_orders:", next_step)
        return {"success": False, "message": "Unexpected next step after sales_orders"}

    # ✅ Step 4: Import order lines
    print("Running order lines import...")
    response = requests.post(f"{base_url}/handson/import/order_lines", json={"file_id": file_id, "mapping": mapping})
    response_json = response.json()
    print("DEBUG - Order Lines Import Response:", response_json)

    if response.status_code != 200 or not response_json.get("success"):
        print(f"Error in order lines import: {response.text}")
        return response.json()

    print("✅ Import process completed successfully!")
    return {"success": True, "message": "Full import completed successfully"}


@handson_bp.route('/part_number_mapping/<int:file_id>', methods=['GET'])
def part_number_mapping(file_id):
    """Render the part number mapping interface for a specific file"""
    file_details = get_file_by_id(file_id)
    if not file_details:
        return "File not found", 404

    file_path = _resolve_file_path(file_details['filepath'])
    if not os.path.exists(file_path):
        return "File path does not exist", 404

    if file_path.endswith('.xls') or file_path.endswith('.xlsx'):
        try:
            # Read the Excel file
            df = pd.read_excel(file_path)

            # If the file has data
            if df.shape[0] > 0:
                # Get the actual column names from the file
                columns = df.columns.tolist()

                # Convert the dataframe to a list of dictionaries
                data = df.fillna('').to_dict('records')

                # Log for debugging
                print(f"Read file: {file_path}")
                print(f"Found columns: {columns}")
                print(f"First row data: {data[0] if data else 'No data'}")

                # Define mapping fields
                mapping_fields = [
                    {'field': 'part_numbers.part_number', 'label': 'Part Number', 'required': False},
                    {'field': 'part_numbers.system_part_number', 'label': 'System Part Number', 'required': False},
                    {'field': 'part_numbers.description', 'label': 'Description', 'required': False},
                    {'field': 'part_numbers.manufacturer', 'label': 'Manufacturer', 'required': False},
                    {'field': 'part_numbers.category', 'label': 'Category', 'required': False}
                ]

                # Get saved mappings
                saved_mappings = db_execute("""
                    SELECT id, name, mapping 
                    FROM import_column_maps 
                    WHERE import_type = 'part_numbers' 
                    ORDER BY name
                """, fetch='all') or []

                return render_template('part_number_mapping.html',
                                       file_data=data,
                                       columns=columns,
                                       mapping_fields=mapping_fields,
                                       saved_mappings=[dict(m) for m in saved_mappings],
                                       file_id=file_id)
            else:
                return "File appears to be empty", 400

        except Exception as e:
            print(f"Error reading file: {e}")
            return f"Error reading Excel file: {str(e)}", 500

    return "Invalid file format", 400

@handson_bp.route('/import/part_numbers', methods=['POST'])
def import_part_numbers():
    """Process part number data from the import file"""
    content = request.get_json()
    mapping = content.get('mapping')
    file_id = content.get('file_id')

    print("Starting part numbers import with mapping:", mapping)  # Debug line

    if not mapping or not file_id:
        return jsonify(success=False, message="Missing mapping or file_id"), 400

    file_details = get_file_by_id(file_id)
    if not file_details:
        return jsonify(success=False, message="File not found"), 404

    try:
        df = pd.read_excel(_resolve_file_path(file_details['filepath']))

        results = {
            'processed': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': []
        }

        # Get required mapped column indices
        try:
            part_number_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'part_numbers.part_number'),
                None
            )
            system_part_number_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'part_numbers.system_part_number'),
                None
            )

            # Either part_number or system_part_number must be mapped
            if part_number_col is None and system_part_number_col is None:
                return jsonify(success=False, message="At least one part number field must be mapped"), 400

        except Exception as e:
            print(f"Mapping error: {e}")
            return jsonify(success=False, message=f"Error in mapping: {str(e)}"), 400

        # Optional fields mapping
        description_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'part_numbers.description'),
            None
        )
        manufacturer_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'part_numbers.manufacturer'),
            None
        )
        category_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'part_numbers.category'),
            None
        )

        # Process rows in smaller batches
        batch_size = 100
        for batch_start in range(0, len(df), batch_size):
            batch_end = min(batch_start + batch_size, len(df))

            with db_cursor(commit=True) as cur:
                for idx in range(batch_start, batch_end):
                    try:
                        row = df.iloc[idx]

                        # Get part numbers
                        part_number = None
                        if part_number_col is not None:
                            part_number = str(row.iloc[int(part_number_col)]).strip()

                        system_part_number = None
                        if system_part_number_col is not None:
                            system_part_number = str(row.iloc[int(system_part_number_col)]).strip()

                        # Skip if both part numbers are missing
                        if not part_number and not system_part_number:
                            results['skipped'] += 1
                            continue

                        # Use one as the other if only one is present
                        if not part_number:
                            part_number = system_part_number
                        if not system_part_number:
                            system_part_number = part_number

                        # Get optional fields
                        description = None
                        if description_col is not None:
                            description = str(row.iloc[int(description_col)]).strip()

                        manufacturer = None
                        if manufacturer_col is not None:
                            manufacturer = str(row.iloc[int(manufacturer_col)]).strip()

                        category = None
                        if category_col is not None:
                            category = str(row.iloc[int(category_col)]).strip()

                        # Create base part number
                        base_part_number = create_base_part_number(part_number)

                        # Check if part exists
                        existing = _execute_with_cursor(cur, """
                            SELECT base_part_number, part_number, system_part_number, description, manufacturer, category
                            FROM part_numbers 
                            WHERE system_part_number = ? OR part_number = ? OR base_part_number = ?
                        """, (system_part_number, part_number, base_part_number)).fetchone()

                        if existing:
                            # Check if any fields need updating
                            update_needed = False
                            update_fields = []
                            update_values = []

                            if existing['part_number'] != part_number:
                                update_fields.append("part_number = ?")
                                update_values.append(part_number)
                                update_needed = True

                            if existing['system_part_number'] != system_part_number:
                                update_fields.append("system_part_number = ?")
                                update_values.append(system_part_number)
                                update_needed = True

                            # Only update optional fields if they are provided and different
                            if description and existing['description'] != description:
                                update_fields.append("description = ?")
                                update_values.append(description)
                                update_needed = True

                            if manufacturer and existing['manufacturer'] != manufacturer:
                                update_fields.append("manufacturer = ?")
                                update_values.append(manufacturer)
                                update_needed = True

                            if category and existing['category'] != category:
                                update_fields.append("category = ?")
                                update_values.append(category)
                                update_needed = True

                            if update_needed:
                                update_sql = f"""
                                    UPDATE part_numbers 
                                    SET {', '.join(update_fields)}
                                    WHERE base_part_number = ?
                                """
                                update_values.append(existing['base_part_number'])
                                _execute_with_cursor(cur, update_sql, update_values)
                                results['updated'] += 1
                            else:
                                results['skipped'] += 1
                        else:
                            # Insert new part
                            _execute_with_cursor(cur, """
                                INSERT INTO part_numbers (
                                    base_part_number, 
                                    part_number, 
                                    system_part_number,
                                    description,
                                    manufacturer,
                                    category
                                ) VALUES (?, ?, ?, ?, ?, ?)
                            """, (
                                base_part_number,
                                part_number,
                                system_part_number,
                                description,
                                manufacturer,
                                category
                            ))
                            results['created'] += 1

                        results['processed'] += 1

                    except Exception as e:
                        print(f"Error processing row {idx + 1}: {e}")
                        results['errors'].append(f"Row {idx + 1}: {str(e)}")
                        results['skipped'] += 1

        # Update import status
        try:
            update_import_status(
                file_id=file_id,
                import_type='part_numbers',
                processed=results['processed'],
                created=results['created'],
                updated=results['updated'],
                skipped=results['skipped'],
                errors=results['errors'],
                mapping=mapping
            )
        except Exception as e:
            print(f"Error updating import status: {e}")

        return jsonify(
            success=True,
            results=results,
            next_step='complete',
            mapping=mapping,
            file_id=file_id
        )

    except Exception as e:
        print(f"Error in import_part_numbers: {str(e)}")
        return jsonify(success=False, message=str(e)), 500


# Add a route to check the status of part number imports
@handson_bp.route('/import/part_numbers/status/<int:file_id>', methods=['GET'])
def part_number_import_status(file_id):
    """Get current import status for part number imports"""
    # Get the most recent status record for this file
    status = db_execute("""
        SELECT processed, created, updated, skipped, errors, completed_at, status
        FROM import_status
        WHERE file_id = ? AND import_type = 'part_numbers'
        ORDER BY created_at DESC
        LIMIT 1
    """, (file_id,), fetch='one')

    if not status:
        return jsonify({
            'status': 'pending',
            'processed': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': [],
            'completed': False
        })

    return jsonify({
        'status': status['status'],
        'processed': status['processed'],
        'created': status['created'],
        'updated': status['updated'],
        'skipped': status['skipped'],
        'errors': json.loads(status['errors'] or '[]'),
        'completed': status['completed_at'] is not None and status['status'] == 'completed'
    })


@handson_bp.route('/purchase_order_mapping/<int:file_id>', methods=['GET'])
def purchase_order_mapping(file_id):
    """Render the purchase order mapping interface for a specific file with limited preview rows"""
    file_details = get_file_by_id(file_id)
    if not file_details:
        return "File not found", 404

    file_path = _resolve_file_path(file_details['filepath'])
    if not os.path.exists(file_path):
        return "File path does not exist", 404

    if file_path.endswith('.xls') or file_path.endswith('.xlsx'):
        try:
            # Read only the first 10 rows of the Excel file for preview
            df = pd.read_excel(file_path, nrows=10)

            # If the first row contains the headers
            if df.shape[0] > 0:
                # Get the actual column names from the file
                columns = df.columns.tolist()

                # Convert the preview dataframe to a list of dictionaries
                data = df.fillna('').to_dict('records')

                # Log for debugging
                print(f"Preview of file: {file_path}")
                print(f"Found columns: {columns}")
                print(f"First row data: {data[0] if data else 'No data'}")

                # Define available fields for purchase order mapping
                mapping_fields = {
                    'header': [
                        {'field': 'purchase_orders.po_number', 'label': 'PO Number', 'required': True},
                        {'field': 'suppliers.fornitore', 'label': 'Supplier Code (Fornitore)', 'required': True},
                        {'field': 'purchase_orders.date_created', 'label': 'Date Created', 'required': False},
                        {'field': 'purchase_orders.expected_delivery', 'label': 'Expected Delivery', 'required': False},
                        {'field': 'purchase_orders.status', 'label': 'Status', 'required': False},
                        {'field': 'purchase_orders.notes', 'label': 'Notes', 'required': False}
                    ],
                    'lines': [
                        {'field': 'purchase_order_lines.po_number', 'label': 'PO Number', 'required': True},
                        {'field': 'purchase_order_lines.line_number', 'label': 'Line Number', 'required': True},
                        {'field': 'part_numbers.system_part_number', 'label': 'System Part Number', 'required': True},
                        {'field': 'purchase_order_lines.quantity', 'label': 'Quantity', 'required': True},
                        {'field': 'purchase_order_lines.price', 'label': 'Price', 'required': False},
                        {'field': 'purchase_order_lines.expected_date', 'label': 'Expected Date', 'required': False},
                        {'field': 'purchase_order_lines.status', 'label': 'Line Status', 'required': False},
                        {'field': 'purchase_order_lines.received_quantity', 'label': 'Received Quantity',
                         'required': False}
                    ]
                }

                # Add information about total rows in file for user information
                try:
                    total_rows_df = pd.read_excel(file_path, nrows=1)
                    total_rows = len(pd.read_excel(file_path, usecols=[0]))
                except Exception as e:
                    print(f"Error counting total rows: {e}")
                    total_rows = "Unknown"

                # Get saved mappings
                saved_mappings = db_execute("""
                    SELECT id, name, import_type, mapping 
                    FROM import_column_maps 
                    WHERE import_type = 'purchase_orders'
                    ORDER BY name
                """, fetch='all') or []

                return render_template('purchase_order_mapping.html',
                                       file_data=data,
                                       columns=columns,
                                       mapping_fields=mapping_fields,
                                       saved_mappings=[dict(m) for m in saved_mappings],
                                       file_id=file_id,
                                       total_rows=total_rows,
                                       preview_note="Showing first 10 rows only")
            else:
                return "File appears to be empty", 400

        except Exception as e:
            print(f"Error reading file: {e}")
            return f"Error reading Excel file: {str(e)}", 500

    return "Invalid file format", 400


@handson_bp.route('/import/purchase_orders', methods=['POST'])
def import_purchase_orders():
    """Process purchase orders from the import file with batched operations"""
    content = request.get_json()
    mapping = content.get('mapping')
    file_id = content.get('file_id')

    print("Starting purchase orders import with mapping:", mapping)

    if not mapping or not file_id:
        return jsonify(success=False, message="Missing mapping or file_id"), 400

    file_details = get_file_by_id(file_id)
    if not file_details:
        return jsonify(success=False, message="File not found"), 404

    try:
        # Initialize results dictionary
        results = {
            'processed': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': [],
            'suppliers_created': 0,
            'skipped_details': []
        }

        # Define chunk size for processing
        CHUNK_SIZE = 1000

        # Get total row count for progress reporting
        try:
            row_count_df = pd.read_excel(_resolve_file_path(file_details['filepath']), nrows=1)
            total_rows = len(pd.read_excel(_resolve_file_path(file_details['filepath']), usecols=[0]))
            print(f"Total rows to process: {total_rows}")
        except Exception as e:
            print(f"Error counting rows: {e}")
            total_rows = "Unknown"

        # Define chunk size for processing
        CHUNK_SIZE = 1000

        # Get total number of rows for progress reporting
        try:
            total_rows = len(pd.read_excel(_resolve_file_path(file_details['filepath']), usecols=[0]))
            print(f"Total rows to process: {total_rows}")
        except Exception as e:
            print(f"Error counting rows: {e}")
            total_rows = 0

        # Process in chunks to avoid memory issues
        processed_rows = 0
        chunk_num = 0

        while processed_rows < total_rows or (total_rows == 0 and chunk_num == 0):
            # Read a chunk of the file
            # Look for this section in your code where you read chunks
            try:
                # If we're at the first chunk, don't skip any rows
                if chunk_num == 0:
                    skiprows = None
                else:
                    # Skip header row and all previously processed rows
                    skiprows = range(1, processed_rows + 1)

                df_chunk = pd.read_excel(_resolve_file_path(file_details['filepath']), skiprows=skiprows, nrows=CHUNK_SIZE)

                # ADD THE DEBUGGING STATEMENTS RIGHT HERE ↓
                print(f"DataFrame columns in chunk {chunk_num}: {df_chunk.columns.tolist()}")
                print(f"Current mapping being used: {mapping}")
                # END OF DEBUGGING STATEMENTS ↑

                # If chunk is empty, we're done
                if df_chunk.empty:
                    break

                chunk_num += 1
                chunk_size = len(df_chunk)
                print(
                    f"Processing chunk {chunk_num} with {chunk_size} rows (rows {processed_rows + 1}-{processed_rows + chunk_size})")
            except Exception as e:
                print(f"Error reading chunk {chunk_num}: {e}")
                break

            # Connect to database for this chunk
            try:
                with db_cursor(commit=True) as cur:
                    # Pre-fetch required mapping columns once per chunk
                    try:
                        # Get column indices from mapping
                        po_ref_col = next(
                            col for col, map_val in mapping.items()
                            if map_val == 'purchase_orders.purchase_order_ref'
                        )
                        # Add this where you extract the supplier code column
                        try:
                            print("Looking for suppliers.fornitore...")
                            supplier_code_col = next(
                                col for col, map_val in mapping.items()
                                if map_val == 'suppliers.fornitore'
                            )
                            print(f"Found suppliers.fornitore at column index {supplier_code_col}")

                            # Verify this column exists in the dataframe
                            if int(supplier_code_col) >= len(df_chunk.columns):
                                print(
                                    f"WARNING: Column index {supplier_code_col} out of range - dataframe only has {len(df_chunk.columns)} columns")
                        except Exception as e:
                            print(f"Error locating supplier code column: {e}")
                        supplier_name_col = next(
                            (col for col, map_val in mapping.items()
                             if map_val == 'suppliers.name'),
                            None
                        )
                        date_issued_col = next(
                            (col for col, map_val in mapping.items()
                             if map_val == 'purchase_orders.date_issued'),
                            None
                        )
                        currency_col = next(
                            (col for col, map_val in mapping.items()
                             if map_val == 'purchase_orders.currency_id'),
                            None
                        )
                        purchase_status_col = next(
                            (col for col, map_val in mapping.items()
                             if map_val == 'purchase_orders.purchase_status_id'),
                            None
                        )
                        # Map optional fields
                        incoterms_col = next(
                            (col for col, map_val in mapping.items()
                             if map_val == 'purchase_orders.incoterms'),
                            None
                        )
                        payment_terms_col = next(
                            (col for col, map_val in mapping.items()
                             if map_val == 'purchase_orders.payment_terms'),
                            None
                        )
                        delivery_address_col = next(
                            (col for col, map_val in mapping.items()
                             if map_val == 'purchase_orders.delivery_address_id'),
                            None
                        )
                        billing_address_col = next(
                            (col for col, map_val in mapping.items()
                             if map_val == 'purchase_orders.billing_address_id'),
                            None
                        )
                        total_value_col = next(
                            (col for col, map_val in mapping.items()
                             if map_val == 'purchase_orders.total_value'),
                            None
                        )
                    except StopIteration as e:
                        print("Error finding required columns:", str(e))
                        return jsonify(success=False, message="Required fields not found in mapping."), 400

                    # Pre-fetch all supplier codes in this chunk to minimize queries
                    supplier_codes = []
                    for idx, row in df_chunk.iterrows():
                        try:
                            supplier_code = str(row.iloc[int(supplier_code_col)]).strip()
                            if supplier_code:
                                supplier_codes.append(supplier_code)
                        except (IndexError, ValueError):
                            pass

                    # Create a unique list of supplier codes
                    unique_supplier_codes = list(set(supplier_codes))

                    # Batch query for existing suppliers
                    supplier_query = f"""
                        SELECT id, fornitore FROM suppliers 
                        WHERE fornitore IN ({','.join(['?'] * len(unique_supplier_codes))})
                    """
                    supplier_mapping = {}
                    if unique_supplier_codes:
                        existing_suppliers = _execute_with_cursor(cur, supplier_query, unique_supplier_codes).fetchall()
                        for supplier in existing_suppliers:
                            supplier_mapping[supplier['fornitore']] = supplier['id']

                    # Fix the currency mapping code
                    currencies = {}
                    currency_rows = _execute_with_cursor(cur, "SELECT id, currency_code, symbol FROM currencies").fetchall()
                    for curr in currency_rows:
                        # Use the correct column names from your table
                        currencies[curr['currency_code'].lower()] = curr['id']  # Map by code
                        currencies[curr['symbol'].lower()] = curr['id']  # Map by symbol

                    # Pre-cache status mapping
                    statuses = {}
                    status_rows = _execute_with_cursor(cur, "SELECT id, name FROM purchase_order_statuses").fetchall()
                    for status in status_rows:
                        statuses[status['name'].lower()] = status['id']

                    # Prepare batch inserts for suppliers and purchase orders
                    new_suppliers = []
                    new_purchase_orders = []
                    purchase_orders_to_update = []

                    # Process each row in the chunk
                    for idx, row in df_chunk.iterrows():
                        try:
                            # Get required fields
                            purchase_order_ref = str(row.iloc[int(po_ref_col)]).strip()
                            supplier_code = str(row.iloc[int(supplier_code_col)]).strip()

                            # Get supplier name if mapped
                            supplier_name = None
                            if supplier_name_col is not None:
                                try:
                                    supplier_name = str(row.iloc[int(supplier_name_col)]).strip() or None
                                except (ValueError, TypeError):
                                    pass

                            # Parse date_issued
                            date_issued = datetime.now().strftime('%Y-%m-%d')  # Default
                            if date_issued_col is not None:
                                try:
                                    raw_date = row.iloc[int(date_issued_col)]
                                    date_issued = validate_date(raw_date)
                                except (ValueError, TypeError):
                                    results['errors'].append(
                                        f"Row {idx + 1}: Invalid date issued format, using current date")

                            # Get status ID
                            purchase_status_id = 1  # Default to status ID 1
                            if purchase_status_col is not None:
                                try:
                                    raw_status = str(row.iloc[int(purchase_status_col)]).strip().lower()
                                    if raw_status:
                                        status_id = statuses.get(raw_status)
                                        if status_id:
                                            purchase_status_id = status_id
                                except (ValueError, TypeError):
                                    pass

                            # Get currency ID
                            currency_id = 1  # Default
                            if currency_col is not None:
                                try:
                                    raw_currency = str(row.iloc[int(currency_col)]).strip().lower()
                                    if raw_currency:
                                        currency_id = currencies.get(raw_currency, 1)
                                except (ValueError, TypeError):
                                    pass

                            if not purchase_order_ref or not supplier_code:
                                skip_reason = f"Row {idx + 1}: Missing required fields"
                                results['errors'].append(skip_reason)
                                results['skipped_details'].append({
                                    'row': idx + 1,
                                    'po_ref': purchase_order_ref or 'N/A',
                                    'supplier_code': supplier_code or 'N/A',
                                    'reason': "Missing required fields"
                                })
                                results['skipped'] += 1
                                continue

                            # Get optional fields
                            incoterms = None
                            if incoterms_col is not None:
                                try:
                                    incoterms = str(row.iloc[int(incoterms_col)]).strip() or None
                                except (ValueError, TypeError):
                                    pass

                            payment_terms = None
                            if payment_terms_col is not None:
                                try:
                                    payment_terms = str(row.iloc[int(payment_terms_col)]).strip() or None
                                except (ValueError, TypeError):
                                    pass

                            delivery_address_id = None
                            if delivery_address_col is not None:
                                try:
                                    delivery_address_id = int(row.iloc[int(delivery_address_col)])
                                except (ValueError, TypeError):
                                    pass

                            billing_address_id = None
                            if billing_address_col is not None:
                                try:
                                    billing_address_id = int(row.iloc[int(billing_address_col)])
                                except (ValueError, TypeError):
                                    pass

                            total_value = None
                            if total_value_col is not None:
                                try:
                                    raw_value = row.iloc[int(total_value_col)]
                                    if pd.notna(raw_value):
                                        total_value = float(str(raw_value).replace(',', '').replace('$', '').strip())
                                except (ValueError, TypeError) as e:
                                    print(f"Value conversion error on row {idx + 1}: {e}")

                            # Check if supplier exists in our cache
                            supplier_id = supplier_mapping.get(supplier_code)

                            if not supplier_id:
                                # Prepare new supplier for batch insert
                                supplier_display_name = supplier_name if supplier_name else f"Supplier {supplier_code}"
                                new_suppliers.append((supplier_code, supplier_display_name))

                                # Query to get ID of the supplier we're about to insert
                                # We still need this query since SQLite doesn't return last inserted IDs for batch inserts
                                _execute_with_cursor(cur, """
                                    INSERT INTO suppliers (fornitore, name) 
                                    VALUES (?, ?)
                                """, (supplier_code, supplier_display_name))

                                new_supplier = _execute_with_cursor(cur, """
                                    SELECT id FROM suppliers WHERE fornitore = ?
                                """, (supplier_code,)).fetchone()

                                supplier_id = new_supplier['id']
                                supplier_mapping[supplier_code] = supplier_id
                                results['suppliers_created'] += 1

                            # Check if PO already exists
                            existing_po = _execute_with_cursor(cur, """
                                SELECT id, supplier_id, date_issued, incoterms, payment_terms, 
                                       purchase_status_id, currency_id, delivery_address_id, 
                                       billing_address_id, total_value
                                FROM purchase_orders 
                                WHERE purchase_order_ref = ?
                            """, (purchase_order_ref,)).fetchone()

                            if existing_po:
                                # Check if any fields need updating
                                needs_update = False
                                update_fields = []
                                update_values = []

                                if existing_po['supplier_id'] != supplier_id:
                                    update_fields.append("supplier_id = ?")
                                    update_values.append(supplier_id)
                                    needs_update = True

                                if date_issued and existing_po['date_issued'] != date_issued:
                                    update_fields.append("date_issued = ?")
                                    update_values.append(date_issued)
                                    needs_update = True

                                if incoterms and existing_po['incoterms'] != incoterms:
                                    update_fields.append("incoterms = ?")
                                    update_values.append(incoterms)
                                    needs_update = True

                                if payment_terms and existing_po['payment_terms'] != payment_terms:
                                    update_fields.append("payment_terms = ?")
                                    update_values.append(payment_terms)
                                    needs_update = True

                                if purchase_status_id and existing_po['purchase_status_id'] != purchase_status_id:
                                    update_fields.append("purchase_status_id = ?")
                                    update_values.append(purchase_status_id)
                                    needs_update = True

                                if currency_id and existing_po['currency_id'] != currency_id:
                                    update_fields.append("currency_id = ?")
                                    update_values.append(currency_id)
                                    needs_update = True

                                if delivery_address_id and existing_po['delivery_address_id'] != delivery_address_id:
                                    update_fields.append("delivery_address_id = ?")
                                    update_values.append(delivery_address_id)
                                    needs_update = True

                                if billing_address_id and existing_po['billing_address_id'] != billing_address_id:
                                    update_fields.append("billing_address_id = ?")
                                    update_values.append(billing_address_id)
                                    needs_update = True

                                if total_value is not None and existing_po['total_value'] != total_value:
                                    update_fields.append("total_value = ?")
                                    update_values.append(total_value)
                                    needs_update = True

                                if needs_update:
                                    update_fields.append("updated_at = CURRENT_TIMESTAMP")
                                    update_sql = f"""
                                        UPDATE purchase_orders 
                                        SET {', '.join(update_fields)}
                                        WHERE id = ?
                                    """
                                    update_values.append(existing_po['id'])
                                    _execute_with_cursor(cur, update_sql, update_values)
                                    results['updated'] += 1
                                else:
                                    results['skipped'] += 1
                                    results['skipped_details'].append({
                                        'row': idx + 1,
                                        'po_ref': purchase_order_ref,
                                        'supplier_code': supplier_code,
                                        'reason': "No changes needed, PO already exists with same values"
                                    })
                            else:
                                # Prepare new PO for batch insert
                                _execute_with_cursor(cur, """
                                    INSERT INTO purchase_orders (
                                        purchase_order_ref,
                                        supplier_id,
                                        date_issued,
                                        incoterms,
                                        payment_terms,
                                        purchase_status_id,
                                        currency_id,
                                        delivery_address_id,
                                        billing_address_id,
                                        total_value,
                                        created_at,
                                        updated_at
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                                """, (
                                    purchase_order_ref,
                                    supplier_id,
                                    date_issued,
                                    incoterms,
                                    payment_terms,
                                    purchase_status_id,
                                    currency_id,
                                    delivery_address_id,
                                    billing_address_id,
                                    total_value
                                ))
                                results['created'] += 1

                            results['processed'] += 1

                        except Exception as e:
                            print(f"Error processing row {idx + 1}: {e}")
                            error_message = f"Row {idx + 1}: {str(e)}"
                            results['errors'].append(error_message)
                            results['skipped_details'].append({
                                'row': idx + 1,
                                'po_ref': purchase_order_ref if 'purchase_order_ref' in locals() else 'N/A',
                                'supplier_code': supplier_code if 'supplier_code' in locals() else 'N/A',
                                'reason': str(e)
                            })
                            results['skipped'] += 1

                    # Transaction is committed automatically by db_cursor
                    print(f"Committed chunk {chunk_num}")

                # Update progress
                processed_rows += chunk_size
            except Exception as e:
                print(f"Error processing chunk {chunk_num}: {e}")
                # Continue to next chunk even if this one failed

        print(f"Purchase orders import results: {results}")

        try:
            # Update import status
            update_import_status(
                file_id=file_id,
                import_type='purchase_orders',
                processed=results['processed'],
                created=results['created'],
                updated=results['updated'],
                skipped=results['skipped'],
                errors=results['errors'],
                mapping=mapping
            )
        except Exception as e:
            print(f"Error updating import status: {e}")

        return jsonify(
            success=True,
            results=results,
            next_step='purchase_order_lines',
            mapping=mapping,
            file_id=file_id
        )

    except Exception as e:
        print(f"Error in import_purchase_orders: {str(e)}")
        return jsonify(success=False, message=str(e)), 500

@handson_bp.route('/import/purchase_order_lines', methods=['POST'])
def import_purchase_order_lines():
    """Process purchase order lines from the import file with chunked processing"""
    content = request.get_json()
    mapping = content.get('mapping')
    file_id = content.get('file_id')

    if not mapping or not file_id:
        return jsonify(success=False, message="Missing mapping or file_id"), 400

    file_details = get_file_by_id(file_id)
    if not file_details:
        return jsonify(success=False, message="File not found"), 404

    try:
        # Initialize results
        results = {
            'processed': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': [],
            'skipped_details': [],
            'updated_details': []
        }

        # Define chunk size for processing
        CHUNK_SIZE = 1000

        # Get total number of rows for progress reporting
        try:
            total_rows = len(pd.read_excel(_resolve_file_path(file_details['filepath']), usecols=[0]))
            print(f"Total rows to process: {total_rows}")
        except Exception as e:
            print(f"Error counting rows: {e}")
            total_rows = 0

        # Process in chunks to avoid memory issues
        processed_rows = 0
        chunk_num = 0

        while processed_rows < total_rows or (total_rows == 0 and chunk_num == 0):
            # Read a chunk of the file
            try:
                # If we're at the first chunk, don't skip any rows
                if chunk_num == 0:
                    skiprows = None
                else:
                    # Skip header row and all previously processed rows
                    skiprows = range(1, processed_rows + 1)

                df_chunk = pd.read_excel(_resolve_file_path(file_details['filepath']), skiprows=skiprows, nrows=CHUNK_SIZE)

                # If chunk is empty, we're done
                if df_chunk.empty:
                    break

                chunk_num += 1
                chunk_size = len(df_chunk)
                print(
                    f"Processing line items chunk {chunk_num} with {chunk_size} rows (rows {processed_rows + 1}-{processed_rows + chunk_size})")
            except Exception as e:
                print(f"Error reading chunk {chunk_num}: {e}")
                break

            # Connect to database for this chunk
            with db_cursor(commit=True) as cur:
                # Get required mapped column indices
                try:
                    line_number_col = next(
                        col for col, map_val in mapping.items()
                        if map_val == 'purchase_order_lines.line_number'
                    )
                    system_part_number_col = next(
                        col for col, map_val in mapping.items()
                        if map_val == 'part_numbers.system_part_number'
                    )
                    quantity_col = next(
                        col for col, map_val in mapping.items()
                        if map_val == 'purchase_order_lines.quantity'
                    )
                    price_col = next(
                        col for col, map_val in mapping.items()
                        if map_val == 'purchase_order_lines.price'
                    )

                    # Status is optional
                    status_col = next(
                        (col for col, map_val in mapping.items()
                         if map_val == 'purchase_order_lines.status_id'),
                        None
                    )
                    received_quantity_col = next(
                        (col for col, map_val in mapping.items()
                         if map_val == 'purchase_order_lines.received_quantity'),
                        None
                    )
                    ship_date_col = next(
                        (col for col, map_val in mapping.items()
                         if map_val == 'purchase_order_lines.ship_date'),
                        None
                    )
                    promised_date_col = next(
                        (col for col, map_val in mapping.items()
                         if map_val == 'purchase_order_lines.promised_date'),
                        None
                    )
                    sales_order_line_col = next(
                        (col for col, map_val in mapping.items()
                         if map_val == 'purchase_order_lines.sales_order_line_id'),
                        None
                    )

                    # Get the PO reference column from the header mapping
                    po_ref_col = next(
                        (col for col, map_val in mapping.items()
                         if map_val == 'purchase_orders.purchase_order_ref'),
                        None
                    )

                except StopIteration as e:
                    print("Mapping error:", e)
                    return jsonify(success=False, message="Required fields not found in mapping"), 400

                if not po_ref_col:
                    return jsonify(success=False, message="PO Reference column not found in mapping"), 400

                # Pre-cache status mapping
                statuses = {}
                status_rows = _execute_with_cursor(cur, "SELECT id, name FROM purchase_order_statuses").fetchall()
                for status in status_rows:
                    statuses[status['name'].lower()] = status['id']

                # Prepare to track unique line identifiers
                line_keys = set()

                # Extract all PO references and part numbers in this chunk
                po_refs = []
                part_numbers = []
                for idx, row in df_chunk.iterrows():
                    try:
                        po_ref = str(row.iloc[int(po_ref_col)]).strip()
                        part_num = str(row.iloc[int(system_part_number_col)]).strip()
                        if po_ref:
                            po_refs.append(po_ref)
                        if part_num:
                            part_numbers.append(part_num)
                    except (IndexError, ValueError):
                        pass

                # Create unique lists
                unique_po_refs = list(set(po_refs))
                unique_part_numbers = list(set(part_numbers))

                # Batch query for PO IDs
                po_mapping = {}
                if unique_po_refs:
                    placeholders = ','.join(['?'] * len(unique_po_refs))
                    po_query = f"SELECT id, purchase_order_ref FROM purchase_orders WHERE purchase_order_ref IN ({placeholders})"
                    po_rows = _execute_with_cursor(cur, po_query, unique_po_refs).fetchall()
                    for po in po_rows:
                        po_mapping[po['purchase_order_ref']] = po['id']

                # Batch query for part numbers
                part_mapping = {}
                if unique_part_numbers:
                    placeholders = ','.join(['?'] * len(unique_part_numbers))
                    part_query = f"SELECT base_part_number, system_part_number FROM part_numbers WHERE system_part_number IN ({placeholders})"
                    part_rows = _execute_with_cursor(cur, part_query, unique_part_numbers).fetchall()
                    for part in part_rows:
                        part_mapping[part['system_part_number']] = part['base_part_number']

                # Process each row in the chunk
                for idx, row in df_chunk.iterrows():
                    try:
                        # Get PO reference from the header mapping
                        po_ref = str(row.iloc[int(po_ref_col)]).strip()

                        # Get required fields
                        line_number = int(float(str(row.iloc[int(line_number_col)]).strip()))
                        system_part_number = str(row.iloc[int(system_part_number_col)]).strip()

                        try:
                            quantity = int(float(str(row.iloc[int(quantity_col)]).strip()))
                            if quantity <= 0:
                                raise ValueError("Quantity must be positive")
                        except ValueError as e:
                            skip_message = f"Row {idx + 1}: Invalid quantity format: {str(e)}"
                            results['errors'].append(skip_message)
                            results['skipped_details'].append({
                                'row': idx + 1,
                                'po_ref': po_ref,
                                'line_number': line_number if 'line_number' in locals() else 'N/A',
                                'part': system_part_number if 'system_part_number' in locals() else 'N/A',
                                'reason': f"Invalid quantity: {str(e)}"
                            })
                            results['skipped'] += 1
                            continue

                        try:
                            price = float(str(row.iloc[int(price_col)]).replace(',', '').replace('$', '').strip())
                        except ValueError:
                            skip_message = f"Row {idx + 1}: Invalid price format"
                            results['errors'].append(skip_message)
                            results['skipped_details'].append({
                                'row': idx + 1,
                                'po_ref': po_ref,
                                'line_number': line_number,
                                'part': system_part_number,
                                'reason': "Invalid price format"
                            })
                            results['skipped'] += 1
                            continue

                        # Get status ID from cache
                        status_id = 1  # Default to status ID 1
                        if status_col is not None:
                            try:
                                raw_status = str(row.iloc[int(status_col)]).strip().lower()
                                if raw_status:
                                    status_id = statuses.get(raw_status, 1)
                            except (ValueError, TypeError):
                                pass

                        received_quantity = None
                        if received_quantity_col is not None:
                            try:
                                raw_quantity = row.iloc[int(received_quantity_col)]
                                if pd.notna(raw_quantity):
                                    received_quantity = int(float(str(raw_quantity).strip()))
                                    # Ensure received quantity is not negative
                                    if received_quantity < 0:
                                        received_quantity = 0
                            except (ValueError, TypeError):
                                pass

                        # Skip if any required field is missing
                        if not po_ref or not system_part_number:
                            skip_message = f"Row {idx + 1}: Missing required fields"
                            results['errors'].append(skip_message)
                            results['skipped_details'].append({
                                'row': idx + 1,
                                'po_ref': po_ref or 'N/A',
                                'line_number': line_number if 'line_number' in locals() else 'N/A',
                                'part': system_part_number or 'N/A',
                                'reason': "Missing required fields"
                            })
                            results['skipped'] += 1
                            continue

                        # Create a unique key for this line to prevent duplicates in the same import
                        line_key = f"{po_ref}_{line_number}"
                        if line_key in line_keys:
                            skip_message = f"Row {idx + 1}: Duplicate line number {line_number} for PO {po_ref}"
                            results['errors'].append(skip_message)
                            results['skipped_details'].append({
                                'row': idx + 1,
                                'po_ref': po_ref,
                                'line_number': line_number,
                                'part': system_part_number,
                                'reason': f"Duplicate line number {line_number} for PO {po_ref}"
                            })
                            results['skipped'] += 1
                            continue

                        line_keys.add(line_key)

                        # Optional fields
                        ship_date = None
                        if ship_date_col is not None:
                            try:
                                raw_date = row.iloc[int(ship_date_col)]
                                ship_date = validate_date(raw_date)
                            except (ValueError, TypeError):
                                pass

                        promised_date = None
                        if promised_date_col is not None:
                            try:
                                raw_date = row.iloc[int(promised_date_col)]
                                promised_date = validate_date(raw_date)
                            except (ValueError, TypeError):
                                pass

                        sales_order_line_id = None
                        if sales_order_line_col is not None:
                            try:
                                sales_order_line_id = int(row.iloc[int(sales_order_line_col)])
                            except (ValueError, TypeError):
                                pass

                        # Get the purchase order ID from our cache
                        po_id = po_mapping.get(po_ref)

                        if not po_id:
                            skip_message = f"Row {idx + 1}: Purchase order not found: {po_ref}"
                            results['errors'].append(skip_message)
                            results['skipped_details'].append({
                                'row': idx + 1,
                                'po_ref': po_ref,
                                'line_number': line_number,
                                'part': system_part_number,
                                'reason': f"Purchase order not found: {po_ref}"
                            })
                            results['skipped'] += 1
                            continue

                        # Get the part base number from our cache
                        base_part_number = part_mapping.get(system_part_number)

                        if not base_part_number:
                            skip_message = f"Row {idx + 1}: Part not found: {system_part_number}"
                            results['errors'].append(skip_message)
                            results['skipped_details'].append({
                                'row': idx + 1,
                                'po_ref': po_ref,
                                'line_number': line_number,
                                'part': system_part_number,
                                'reason': f"Part not found: {system_part_number}"
                            })
                            results['skipped'] += 1
                            continue

                        # Check if this line already exists
                        existing_line = _execute_with_cursor(cur, """
                            SELECT id, quantity, price, ship_date, promised_date, status_id, sales_order_line_id, received_quantity
                            FROM purchase_order_lines
                            WHERE purchase_order_id = ? AND line_number = ?
                        """, (po_id, line_number)).fetchone()

                        if existing_line:
                            # Check if any fields need updating
                            needs_update = False
                            update_fields = []
                            update_values = []

                            if existing_line['quantity'] != quantity:
                                update_fields.append("quantity = ?")
                                update_values.append(quantity)
                                needs_update = True

                            if existing_line['price'] != price:
                                update_fields.append("price = ?")
                                update_values.append(price)
                                needs_update = True

                            if ship_date and existing_line['ship_date'] != ship_date:
                                update_fields.append("ship_date = ?")
                                update_values.append(ship_date)
                                needs_update = True

                            if promised_date and existing_line['promised_date'] != promised_date:
                                update_fields.append("promised_date = ?")
                                update_values.append(promised_date)
                                needs_update = True

                            if status_id and existing_line['status_id'] != status_id:
                                update_fields.append("status_id = ?")
                                update_values.append(status_id)
                                needs_update = True

                            if sales_order_line_id and existing_line['sales_order_line_id'] != sales_order_line_id:
                                update_fields.append("sales_order_line_id = ?")
                                update_values.append(sales_order_line_id)
                                needs_update = True

                            if received_quantity is not None and existing_line[
                                'received_quantity'] != received_quantity:
                                update_fields.append("received_quantity = ?")
                                update_values.append(received_quantity)
                                needs_update = True

                            if needs_update:
                                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                                update_sql = f"""
                                    UPDATE purchase_order_lines 
                                    SET {', '.join(update_fields)}
                                    WHERE id = ?
                                """
                                update_values.append(existing_line['id'])
                                _execute_with_cursor(cur, update_sql, update_values)
                                results['updated'] += 1

                                updated_field_names = []
                                if 'quantity = ?' in update_fields:
                                    updated_field_names.append('quantity')
                                if 'price = ?' in update_fields:
                                    updated_field_names.append('price')
                                if 'ship_date = ?' in update_fields:
                                    updated_field_names.append('ship_date')
                                if 'promised_date = ?' in update_fields:
                                    updated_field_names.append('promised_date')
                                if 'status_id = ?' in update_fields:
                                    updated_field_names.append('status')
                                if 'sales_order_line_id = ?' in update_fields:
                                    updated_field_names.append('sales_order_line')
                                if 'received_quantity = ?' in update_fields:
                                    updated_field_names.append('received_quantity')

                                # Add the details to the results
                                results['updated_details'].append({
                                    'row': idx + 1,
                                    'po_ref': po_ref,
                                    'line_number': line_number,
                                    'part': system_part_number,
                                    'updated_fields': updated_field_names
                                })
                            else:
                                results['skipped'] += 1
                                results['skipped_details'].append({
                                    'row': idx + 1,
                                    'po_ref': po_ref,
                                    'line_number': line_number,
                                    'part': system_part_number,
                                    'reason': "No changes needed, PO line already exists with same values"
                                })
                        else:
                            # Insert new line
                            _execute_with_cursor(cur, """
                                INSERT INTO purchase_order_lines (
                                    purchase_order_id,
                                    line_number,
                                    base_part_number,
                                    quantity,
                                    price,
                                    ship_date,
                                    promised_date,
                                    status_id,
                                    sales_order_line_id,
                                    received_quantity,
                                    created_at,
                                    updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """, (
                                po_id,
                                line_number,
                                base_part_number,
                                quantity,
                                price,
                                ship_date,
                                promised_date,
                                status_id,
                                sales_order_line_id,
                                received_quantity or 0  # Default to 0 if None
                            ))
                            results['created'] += 1

                        results['processed'] += 1

                    except Exception as e:
                        print(f"Error processing row {idx + 1}: {e}")
                        error_message = f"Row {idx + 1}: {str(e)}"
                        results['errors'].append(error_message)

                        # Gather values that are available for error reporting
                        error_details = {
                            'row': idx + 1,
                            'po_ref': po_ref if 'po_ref' in locals() else 'N/A',
                            'line_number': line_number if 'line_number' in locals() else 'N/A',
                            'part': system_part_number if 'system_part_number' in locals() else 'N/A',
                            'reason': str(e)
                        }
                        results['skipped_details'].append(error_details)
                        results['skipped'] += 1

                # Chunk commit is handled by db_cursor
                print(f"Committed lines chunk {chunk_num}")

                # Update processed_rows for next iteration
                processed_rows += len(df_chunk)

        # Update import status
        try:
            update_import_status(
                file_id=file_id,
                import_type='purchase_order_lines',
                processed=results['processed'],
                created=results['created'],
                updated=results['updated'],
                skipped=results['skipped'],
                errors=results['errors']
            )
        except Exception as e:
            print(f"Error updating import status: {e}")

        return jsonify(
            success=True,
            results=results,
            next_step='complete',
            mapping=mapping,
            file_id=file_id
        )

    except Exception as e:
        print(f"Error in import_purchase_order_lines: {str(e)}")
        return jsonify(success=False, message=str(e)), 500

def get_currency_id(currency_value, db=None):
    """Convert a currency code/name to a currency ID"""
    if not currency_value:
        return None

    # First try direct integer conversion
    try:
        currency_id = int(currency_value)
        # Verify the ID exists
        result = db_execute("SELECT id FROM currencies WHERE id = ?", (currency_id,), fetch='one')
        if result:
            return currency_id
    except (ValueError, TypeError):
        pass

    # Try by code (e.g., USD, EUR)
    result = db_execute("SELECT id FROM currencies WHERE currency_code = ?", (currency_value.upper(),), fetch='one')
    if result:
        return result['id']

    # Try case-insensitive code match
    result = db_execute("SELECT id FROM currencies WHERE UPPER(currency_code) = ?", (currency_value.upper(),), fetch='one')
    if result:
        return result['id']

    # Try by name (e.g., US Dollar) - if you have a name column
    # If you don't have a name column, you can remove these queries

    # Try partial match on currency_code
    result = db_execute("SELECT id FROM currencies WHERE currency_code LIKE ?", (f'%{currency_value}%',), fetch='one')
    if result:
        return result['id']

    return None

def get_status_id(status_value, status_type, db=None):
    """
    Convert a status name/code to a status ID
    status_type can be 'purchase_status' or 'line_status'
    """
    # First try direct integer conversion (if it's already an ID)
    try:
        status_id = int(status_value)
        # Verify the ID exists
        result = db_execute(f"SELECT id FROM {status_type} WHERE id = ?", (status_id,), fetch='one')
        if result:
            return status_id
    except (ValueError, TypeError):
        pass

    # Try by name
    result = db_execute(f"SELECT id FROM {status_type} WHERE name = ?", (status_value,), fetch='one')
    if result:
        return result['id']

    # Try by code if applicable
    result = db_execute(f"SELECT id FROM {status_type} WHERE code = ?", (status_value,), fetch='one')
    if result:
        return result['id']

    # If status doesn't exist but we have default mappings, use those
    default_statuses = {
        'purchase_status': {
            'new': 1,
            'draft': 1,
            'pending': 2,
            'approved': 3,
            'in progress': 4,
            'completed': 5,
            'cancelled': 6
        },
        'line_status': {
            'new': 1,
            'pending': 2,
            'confirmed': 3,
            'shipped': 4,
            'received': 5,
            'cancelled': 6
        }
    }

    if status_type in default_statuses:
        lower_status = status_value.lower()
        if lower_status in default_statuses[status_type]:
            return default_statuses[status_type][lower_status]

    return None


# Add these routes to your handson_routes.py file

@handson_bp.route('/quick_import', methods=['GET'])
def quick_import_page():
    """Render the quick import page"""
    saved_mappings = db_execute("""
        SELECT id, name, mapping 
        FROM import_column_maps 
        WHERE import_type = 'sales_orders'
        ORDER BY name
    """, fetch='all') or []

    return render_template('quick_import.html',
                           saved_mappings=[dict(m) for m in saved_mappings])


@handson_bp.route('/quick_import/process', methods=['POST'])
def process_quick_import():
    """Process a quick import from SharePoint or CSV link"""
    content = request.get_json()
    file_url = content.get('file_url')
    file_type = content.get('file_type', 'csv')  # User's selection
    mapping_id = content.get('mapping_id')

    if not file_url or not mapping_id:
        return jsonify(success=False, message="Missing file URL or mapping"), 400

    # Get the mapping
    mapping_record = db_execute(
        "SELECT mapping FROM import_column_maps WHERE id = ?",
        (mapping_id,),
        fetch='one'
    )

    if not mapping_record:
        return jsonify(success=False, message="Mapping not found"), 404

    mapping = json.loads(mapping_record['mapping'])

    print(f"Processing quick import with mapping: {mapping}")
    print(f"User selected file type: {file_type}")

    try:
        # Download the file
        import requests
        import tempfile

        download_url = convert_sharepoint_url(file_url)
        print(f"Downloading from: {download_url}")

        response = requests.get(download_url, timeout=30)
        response.raise_for_status()
        print(f"Downloaded {len(response.content)} bytes")

        # Detect actual file type by checking magic bytes
        actual_file_type = file_type  # Start with user's selection
        if len(response.content) > 4:
            # Check for ZIP/Excel signature (PK\x03\x04)
            if response.content[:2] == b'PK':
                actual_file_type = 'excel'
                print("Detected Excel file format from content (PK signature)")
            # Check for common CSV patterns (starts with printable text)
            elif response.content[:10].decode('latin-1', errors='ignore').isprintable():
                actual_file_type = 'csv'
                print("Detected CSV file format from content")

        suffix = '.csv' if actual_file_type == 'csv' else '.xlsx'
        print(f"Using file extension: {suffix} (actual type: {actual_file_type})")

        # Save to temporary file and create file record
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix=suffix) as temp_file:
            temp_file.write(response.content)
            temp_path = temp_file.name

        print(f"Saved to temp file: {temp_path}")

        # Create a file record for tracking
        insert_query = _with_returning_clause("""
            INSERT INTO files (filepath, filename, upload_date)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """)
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, insert_query, (temp_path, os.path.basename(file_url)))
            file_id = _last_inserted_id(cur)

        # Read the file based on actual detected type
        try:
            if actual_file_type == 'csv':
                # Try UTF-8 first, then fall back to other encodings
                encodings_to_try = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252', 'iso-8859-1']
                df = None

                for encoding in encodings_to_try:
                    try:
                        df = pd.read_csv(
                            temp_path,
                            encoding=encoding,
                            on_bad_lines='skip',
                            engine='python',
                            sep=None,  # Auto-detect delimiter
                            skipinitialspace=True
                        )
                        print(
                            f"Successfully read CSV with {encoding} encoding: {len(df)} rows and {len(df.columns)} columns")
                        break
                    except (UnicodeDecodeError, UnicodeError):
                        continue
                    except Exception as e:
                        print(f"Failed with {encoding}: {e}")
                        continue

                if df is None:
                    raise ValueError("Could not read CSV with any supported encoding")
            else:
                df = pd.read_excel(temp_path)
                print(f"Read Excel with {len(df)} rows and {len(df.columns)} columns")

            print(f"Column names: {df.columns.tolist()}")

        except Exception as e:
            print(f"Error reading file: {e}")
            import traceback
            traceback.print_exc()
            os.unlink(temp_path)
            return jsonify(success=False, message=f"Error reading file: {str(e)}"), 500

        # ... rest of the function stays the same ...

        # Process the data following the regular import steps
        results = {
            'parts': {'processed': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': []},
            'customers': {'processed': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': []},
            'sales_orders': {'processed': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': []},
            'order_lines': {'processed': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': []}
        }

        # Step 1: Process parts (like regular import)
        print("Step 1: Processing parts...")
        process_parts_from_df(df, mapping, results['parts'])

        # Step 2: Process customers
        print("Step 2: Processing customers...")
        process_customers_from_df(df, mapping, results['customers'])

        # Step 3: Process sales orders
        print("Step 3: Processing sales orders...")
        process_sales_orders_from_df(df, mapping, results['sales_orders'])

        # Step 4: Process order lines
        print("Step 4: Processing order lines...")
        process_order_lines_from_df(df, mapping, results['order_lines'])

        # Step 5: Calculate missing total values
        print("Step 5: Calculating missing total values...")
        calculate_missing_totals(results)

        # Clean up temp file
        os.unlink(temp_path)

        # Record import status for each step
        for import_type, step_results in results.items():
            try:
                update_import_status(
                    file_id=file_id,
                    import_type=import_type,
                    processed=step_results['processed'],
                    created=step_results['created'],
                    updated=step_results['updated'],
                    skipped=step_results['skipped'],
                    errors=step_results['errors'],
                    mapping=mapping
                )
            except Exception as e:
                print(f"Error updating import status for {import_type}: {e}")

        return jsonify(success=True, results=results, file_id=file_id)

    except requests.RequestException as e:
        print(f"Request error: {e}")
        return jsonify(success=False, message=f"Error downloading file: {str(e)}"), 500
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify(success=False, message=f"Error processing file: {str(e)}"), 500


def create_part_on_demand(cur, system_part_number, part_number=None):
    """Create a part on-demand if it doesn't exist, return base_part_number"""
    if not part_number:
        part_number = system_part_number

    base_part_number = create_base_part_number(part_number)

    try:
        _execute_with_cursor(cur, """
            INSERT INTO part_numbers (base_part_number, part_number, system_part_number)
            VALUES (?, ?, ?)
        """, (base_part_number, part_number, system_part_number))
        print(f"Created missing part on-demand: {system_part_number}")
        return base_part_number
    except Exception as exc:
        if not _is_unique_violation(exc):
            raise
        e = exc
        # Check if it's a base_part_number collision
        if 'base_part_number' in str(e):
            # Part exists with same base but different system number - update it
            existing = _execute_with_cursor(cur, """
                SELECT base_part_number, system_part_number, part_number 
                FROM part_numbers 
                WHERE base_part_number = ?
            """, (base_part_number,)).fetchone()

            if existing:
                print(
                    f"Part with base {base_part_number} exists with system_part_number='{existing['system_part_number']}'")
                print(f"Updating to use system_part_number='{system_part_number}' instead")

                # Update to the new system_part_number
                _execute_with_cursor(cur, """
                    UPDATE part_numbers 
                    SET system_part_number = ?,
                        part_number = ?
                    WHERE base_part_number = ?
                """, (system_part_number, part_number, base_part_number))

                return base_part_number

        # Check if system_part_number already exists
        result = _execute_with_cursor(cur, """
            SELECT base_part_number FROM part_numbers 
            WHERE system_part_number = ?
        """, (system_part_number,)).fetchone()

        if result:
            print(f"Part {system_part_number} already exists, returning existing base_part_number")
            return result['base_part_number']

        # If we get here, something else went wrong
        raise

def process_parts_from_df(df, mapping, results):
    """Process parts from dataframe (Step 1)"""
    with db_cursor(commit=True) as cur:
        try:
            # Get mapped columns
            system_part_number_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'part_numbers.system_part_number'),
                None
            )
            part_number_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'part_numbers.part_number'),
                None
            )

            if not system_part_number_col and not part_number_col:
                print("No part number columns mapped, skipping part creation")
                return

            # Convert to int
            if system_part_number_col is not None:
                system_part_number_col = int(system_part_number_col)
            if part_number_col is not None:
                part_number_col = int(part_number_col)

            print(f"Part columns: system={system_part_number_col}, part={part_number_col}")
            print(f"DataFrame has {len(df)} rows and {len(df.columns)} columns")

            # Get existing parts - index by BOTH system_part_number AND base_part_number
            existing_by_system = {}
            existing_by_base = {}

            existing_rows = _execute_with_cursor(
                cur,
                "SELECT system_part_number, base_part_number, part_number FROM part_numbers"
            ).fetchall()
            for row in existing_rows:
                existing_by_system[row['system_part_number']] = {
                    'base': row['base_part_number'],
                    'part': row['part_number']
                }
                existing_by_base[row['base_part_number']] = {
                    'system': row['system_part_number'],
                    'part': row['part_number']
                }

            print(f"Found {len(existing_by_system)} existing parts in database")
            print(f"Sample existing parts: {list(existing_by_system.keys())[:3]}")

            # Get unique parts from dataframe with full details
            unique_parts = {}
            empty_parts = 0

            for idx, row in df.iterrows():
                try:
                    system_part_number = None
                    part_number = None

                    if system_part_number_col is not None:
                        val = row.iloc[system_part_number_col]
                        if pd.notna(val) and str(val).strip():
                            system_part_number = str(val).strip()

                    if part_number_col is not None:
                        val = row.iloc[part_number_col]
                        if pd.notna(val) and str(val).strip():
                            part_number = str(val).strip()

                    if system_part_number or part_number:
                        # Use one as the other if only one is present
                        if not part_number:
                            part_number = system_part_number
                        if not system_part_number:
                            system_part_number = part_number

                        # Calculate what the base part number would be
                        base_part_number = create_base_part_number(part_number)

                        if system_part_number not in unique_parts:
                            unique_parts[system_part_number] = {
                                'part_number': part_number,
                                'base_part_number': base_part_number,
                                'first_row': idx + 1
                            }
                    else:
                        empty_parts += 1

                except (ValueError, TypeError, IndexError) as e:
                    print(f"Error reading part from row {idx + 1}: {e}")
                    continue

            print(f"\nExtracted {len(unique_parts)} unique parts from file")
            print(f"Empty/skipped rows: {empty_parts}")
            print(f"Sample parts from file:")
            for sys_part, info in list(unique_parts.items())[:3]:
                print(f"  System: {sys_part}")
                print(f"  Part: {info['part_number']}")
                print(f"  Base: {info['base_part_number']}")
                print(f"  First row: {info['first_row']}")
                print()

            # Process each unique part
            for system_part_number, part_info in unique_parts.items():
                part_number = part_info['part_number']
                base_part_number = part_info['base_part_number']
                first_row = part_info['first_row']

                try:
                    # Check if system_part_number already exists
                    if system_part_number in existing_by_system:
                        print(f"Part {system_part_number} already exists (row {first_row}), skipping")
                        results['skipped'] += 1
                        results['processed'] += 1
                        continue

                    # Check if base_part_number already exists (collision with different system number)
                    if base_part_number in existing_by_base:
                        existing_info = existing_by_base[base_part_number]
                        print(f"WARNING: Base part {base_part_number} exists with different system number!")
                        print(f"  Existing: system={existing_info['system']}, part={existing_info['part']}")
                        print(f"  New (row {first_row}): system={system_part_number}, part={part_number}")

                        # Update the existing record to add this system_part_number
                        _execute_with_cursor(cur, """
                            UPDATE part_numbers 
                            SET system_part_number = ?
                            WHERE base_part_number = ?
                        """, (system_part_number, base_part_number))

                        results['updated'] += 1
                        results['processed'] += 1
                        existing_by_system[system_part_number] = {
                            'base': base_part_number,
                            'part': part_number
                        }
                        continue

                    # Create new part
                    print(f"Creating part: system={system_part_number}, base={base_part_number} (row {first_row})")
                    _execute_with_cursor(cur, """
                        INSERT INTO part_numbers (base_part_number, part_number, system_part_number)
                        VALUES (?, ?, ?)
                    """, (base_part_number, part_number, system_part_number))

                    existing_by_system[system_part_number] = {
                        'base': base_part_number,
                        'part': part_number
                    }
                    existing_by_base[base_part_number] = {
                        'system': system_part_number,
                        'part': part_number
                    }

                    results['created'] += 1
                    results['processed'] += 1

                except Exception as exc:
                    if _is_unique_violation(exc):
                        error_msg = f"IntegrityError for part {system_part_number} (row {first_row}): {exc}"
                    else:
                        error_msg = f"Part {system_part_number} (row {first_row}): {exc}"
                    print(error_msg)
                    results['errors'].append(error_msg)
                    results['skipped'] += 1
                    results['processed'] += 1

            print(f"\n{'=' * 60}")
            print(f"Parts Processing Complete:")
            print(f"  Processed: {results['processed']}")
            print(f"  Created: {results['created']}")
            print(f"  Updated: {results['updated']}")
            print(f"  Skipped: {results['skipped']}")
            print(f"  Errors: {len(results['errors'])}")
            print(f"{'=' * 60}\n")

        except Exception as e:
            error_msg = f"Part processing error: {str(e)}"
            results['errors'].append(error_msg)
            print(error_msg)
            import traceback
            traceback.print_exc()


def process_customers_from_df(df, mapping, results):
    """Process customers from dataframe (Step 2)"""
    with db_cursor(commit=True) as cur:
        try:
            from flask_login import current_user
            # Get the logged-in user's ID as default salesperson
            default_salesperson_id = current_user.id if current_user.is_authenticated else 1

            customer_system_code_col = int(next(
                col for col, map_val in mapping.items()
                if map_val == 'customers.system_code'
            ))
            customer_name_col = next(
                (col for col, map_val in mapping.items()
                 if map_val == 'customers.name'),
                None
            )
            if customer_name_col is not None:
                customer_name_col = int(customer_name_col)

        except StopIteration:
            results['errors'].append("Customer system_code mapping not found")
            return

        # Get unique customers
        unique_customers = df.drop_duplicates(subset=[df.columns[customer_system_code_col]])

        for idx, row in unique_customers.iterrows():
            try:
                customer_system_code = str(row.iloc[customer_system_code_col]).strip()

                if not customer_system_code or customer_system_code == 'nan':
                    results['skipped'] += 1
                    continue

                # Get customer name
                customer_name = customer_system_code
                if customer_name_col is not None:
                    try:
                        name_val = row.iloc[customer_name_col]
                        if pd.notna(name_val):
                            customer_name = str(name_val).strip()
                    except:
                        pass

                # Check if exists
                existing = _execute_with_cursor(cur, """
                    SELECT id, name FROM customers WHERE system_code = ?
                """, (customer_system_code,)).fetchone()

                if existing:
                    if existing['name'] != customer_name:
                        _execute_with_cursor(cur, """
                            UPDATE customers SET name = ? WHERE system_code = ?
                        """, (customer_name, customer_system_code))
                        results['updated'] += 1
                    else:
                        results['skipped'] += 1
                else:
                    # Create new customer with default salesperson and status_id = 3
                    # The salesperson will be updated later from the sales order
                    _execute_with_cursor(cur, """
                        INSERT INTO customers (system_code, name, salesperson_id, status_id)
                        VALUES (?, ?, ?, 3)
                    """, (customer_system_code, customer_name, default_salesperson_id))
                    results['created'] += 1
                    print(
                        f"Created customer {customer_system_code} with temporary salesperson {default_salesperson_id}")

                results['processed'] += 1

            except Exception as e:
                results['errors'].append(f"Row {idx + 1}: {str(e)}")
                results['skipped'] += 1

        print(f"Customers processed: {results}")


def calculate_missing_totals(results):
    """Calculate total_value for sales orders that don't have it set (Step 5)"""
    try:
        with db_cursor(commit=True) as cur:
            orders_needing_totals = _execute_with_cursor(cur, """
                SELECT DISTINCT so.id, so.sales_order_ref
                FROM sales_orders so
                INNER JOIN sales_order_lines sol ON so.id = sol.sales_order_id
                WHERE (so.total_value IS NULL OR so.total_value = 0)
                AND sol.price IS NOT NULL
            """).fetchall()

            calculated_count = 0
            for order in orders_needing_totals:
                try:
                    total_calc = _execute_with_cursor(cur, """
                        SELECT COALESCE(SUM(quantity * COALESCE(price, 0)), 0) as calculated_total
                        FROM sales_order_lines 
                        WHERE sales_order_id = ? AND price IS NOT NULL
                    """, (order['id'],)).fetchone()

                    calculated_total = total_calc['calculated_total'] if total_calc else 0

                    if calculated_total > 0:
                        _execute_with_cursor(cur, """
                            UPDATE sales_orders 
                            SET total_value = ?
                            WHERE id = ?
                        """, (calculated_total, order['id']))
                        calculated_count += 1

                except Exception as e:
                    print(f"Error calculating total for order {order['sales_order_ref']}: {e}")

            if calculated_count > 0:
                print(f"Calculated total_value for {calculated_count} sales orders")
                results['sales_orders']['total_values_calculated'] = calculated_count

    except Exception as e:
        print(f"Error in calculate_missing_totals: {e}")


def process_sales_orders_from_df(df, mapping, results):
    """Process sales orders from dataframe (Step 3)"""
    with db_cursor(commit=True) as cur:
        # Track customer-salesperson conflicts
        customer_salesperson_map = {}  # {customer_system_code: salesperson_id}
        conflicts = []  # List of customers with multiple salespeople

        # Get required column indices
        try:
            sales_order_ref_col = next(
                col for col, map_val in mapping.items()
                if map_val == 'sales_orders.sales_order_ref'
            )
            customer_system_code_col = next(
                col for col, map_val in mapping.items()
                if map_val == 'customers.system_code'
            )

            # Convert to integers
            sales_order_ref_col = int(sales_order_ref_col)
            customer_system_code_col = int(customer_system_code_col)

            # Get actual column names from dataframe
            sales_order_ref_colname = df.columns[sales_order_ref_col]
            customer_system_code_colname = df.columns[customer_system_code_col]

            print(
                f"Using columns: SO ref={sales_order_ref_col} ({sales_order_ref_colname}), Customer={customer_system_code_col} ({customer_system_code_colname})")
        except StopIteration:
            error_msg = "Required mappings not found (need sales_order_ref and customer system_code)"
            print(f"ERROR: {error_msg}")
            results['errors'].append(error_msg)
            return False
        except (ValueError, TypeError, IndexError) as e:
            error_msg = f"Invalid column indices in mapping: {e}"
            print(f"ERROR: {error_msg}")
            results['errors'].append(error_msg)
            return False

        # Get customer name column if mapped
        customer_name_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'customers.name'), None
        )
        if customer_name_col is not None:
            customer_name_col = int(customer_name_col)

        # Optional columns
        total_value_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_orders.total_value'), None
        )
        customer_po_ref_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_orders.customer_po_ref'), None
        )
        date_entered_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_orders.date_entered'), None
        )
        salesperson_ref_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'salespeople.system_ref'), None
        )

        # Convert optional columns to integers if they exist
        if total_value_col is not None:
            total_value_col = int(total_value_col)
        if customer_po_ref_col is not None:
            customer_po_ref_col = int(customer_po_ref_col)
        if date_entered_col is not None:
            date_entered_col = int(date_entered_col)
        if salesperson_ref_col is not None:
            salesperson_ref_col = int(salesperson_ref_col)

        # Get unique sales orders using column name
        unique_orders = df.drop_duplicates(subset=[sales_order_ref_colname])

        for idx, row in unique_orders.iterrows():
            try:
                sales_order_ref = str(row.iloc[sales_order_ref_col]).strip()
                customer_system_code = str(row.iloc[customer_system_code_col]).strip()

                if not sales_order_ref or not customer_system_code or sales_order_ref == 'nan' or customer_system_code == 'nan':
                    results['skipped'] += 1
                    continue

                # Get customer name
                customer_name = customer_system_code  # Default to system code
                if customer_name_col is not None:
                    try:
                        name_val = row.iloc[customer_name_col]
                        if pd.notna(name_val):
                            customer_name = str(name_val).strip()
                    except:
                        pass

                # Get or create customer
                customer = _execute_with_cursor(cur, """
                    SELECT id, currency_id 
                    FROM customers 
                    WHERE system_code = ?
                """, (customer_system_code,)).fetchone()

                if not customer:
                    # Create customer (this is a fallback, should have been created in Step 2)
                    print(f"Creating customer: {customer_system_code} - {customer_name}")
                    _execute_with_cursor(cur, """
                        INSERT INTO customers (system_code, name, status_id)
                        VALUES (?, ?, 3)
                    """, (customer_system_code, customer_name))
                    customer = _execute_with_cursor(cur, """
                        SELECT id, currency_id 
                        FROM customers 
                        WHERE system_code = ?
                    """, (customer_system_code,)).fetchone()

                # Get salesperson ID
                salesperson_id = 1  # Default
                if salesperson_ref_col is not None:
                    try:
                        salesperson_ref_raw = row.iloc[salesperson_ref_col]
                        if pd.notna(salesperson_ref_raw):
                            salesperson_ref = str(salesperson_ref_raw).strip()
                            if salesperson_ref:
                                salesperson = _execute_with_cursor(cur, """
                                    SELECT id FROM salespeople 
                                    WHERE system_ref = ?
                                """, (salesperson_ref,)).fetchone()
                                if salesperson:
                                    salesperson_id = salesperson['id']
                                    print(f"Found salesperson ID {salesperson_id} for ref '{salesperson_ref}'")
                                else:
                                    print(
                                        f"WARNING: Salesperson not found for ref '{salesperson_ref}', using default ID 1")
                    except (ValueError, TypeError) as e:
                        print(f"Error processing salesperson: {e}, using default ID 1")

                # Track and update customer salesperson
                if customer_system_code in customer_salesperson_map:
                    # Check for conflicts
                    if customer_salesperson_map[customer_system_code] != salesperson_id:
                        conflict_msg = f"Customer '{customer_system_code}' has multiple salespeople: {customer_salesperson_map[customer_system_code]} and {salesperson_id}"
                        conflicts.append(conflict_msg)
                        print(f"WARNING: {conflict_msg}")
                else:
                    # First time seeing this customer, update their salesperson
                    customer_salesperson_map[customer_system_code] = salesperson_id
                    _execute_with_cursor(cur, """
                        UPDATE customers 
                        SET salesperson_id = ? 
                        WHERE system_code = ?
                    """, (salesperson_id, customer_system_code))
                    print(f"Updated customer {customer_system_code} with salesperson {salesperson_id}")

                # Get optional fields
                total_value = None
                if total_value_col is not None:
                    try:
                        raw_val = row.iloc[total_value_col]
                        if pd.notna(raw_val):
                            total_value = float(str(raw_val).replace(',', '').replace('$', '').strip())
                    except (ValueError, TypeError):
                        pass

                customer_po_ref = None
                if customer_po_ref_col is not None:
                    try:
                        raw_val = row.iloc[customer_po_ref_col]
                        if pd.notna(raw_val):
                            customer_po_ref = str(raw_val).strip() or None
                    except (ValueError, TypeError):
                        pass

                date_entered = None
                if date_entered_col is not None:
                    try:
                        raw_date = row.iloc[date_entered_col]
                        date_entered = validate_date(raw_date)
                    except (ValueError, TypeError):
                        date_entered = datetime.now().strftime('%Y-%m-%d')

                # Check if order exists
                existing = _execute_with_cursor(cur, """
                    SELECT id FROM sales_orders WHERE sales_order_ref = ?
                """, (sales_order_ref,)).fetchone()

                if existing:
                    results['skipped'] += 1
                else:
                    _execute_with_cursor(cur, """
                        INSERT INTO sales_orders (
                            sales_order_ref, customer_id, customer_po_ref,
                            date_entered, currency_id, sales_status_id,
                            salesperson_id, total_value
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        sales_order_ref, customer['id'], customer_po_ref,
                        date_entered or datetime.now().strftime('%Y-%m-%d'),
                        customer['currency_id'] or 1, 1, salesperson_id, total_value
                    ))
                    results['created'] += 1
                    print(f"Created order {sales_order_ref} with salesperson_id {salesperson_id}")

                results['processed'] += 1

            except Exception as e:
                results['errors'].append(f"Row {idx + 1}: {str(e)}")
                results['skipped'] += 1
                import traceback
                traceback.print_exc()

        # Log any conflicts found
        if conflicts:
            print(f"\n{'=' * 60}")
            print(f"SALESPERSON CONFLICTS DETECTED ({len(conflicts)}):")
            for conflict in conflicts:
                print(f"  - {conflict}")
            print(f"{'=' * 60}\n")
            # Add to results for visibility
            results['errors'].extend(conflicts)

        print(f"Sales orders processed: {results}")

def convert_sharepoint_url(url):
    """Convert SharePoint sharing link to direct download link"""
    if 'sharepoint.com' not in url.lower():
        return url

    # If it's already a direct download link, return as is
    if 'download=1' in url:
        return url

    # Convert sharing link to download link
    if '/_layouts/15/' in url or '/personal/' in url:
        # Add download parameter
        separator = '&' if '?' in url else '?'
        return f"{url}{separator}download=1"

    return url


def process_order_lines_from_df(df, mapping, results):
    """Process order lines from dataframe"""
    with db_cursor(commit=True) as cur:
        # Get required column indices
        try:
            sales_order_ref_col = int(next(
                col for col, map_val in mapping.items()
                if map_val == 'sales_orders.sales_order_ref'
            ))
            system_part_number_col = int(next(
                col for col, map_val in mapping.items()
                if map_val == 'part_numbers.system_part_number'
            ))
            line_number_col = int(next(
                col for col, map_val in mapping.items()
                if map_val == 'sales_order_lines.line_number'
            ))
            quantity_col = int(next(
                col for col, map_val in mapping.items()
                if map_val == 'sales_order_lines.quantity'
            ))

            print(
                f"Using columns: SO ref={sales_order_ref_col}, Part={system_part_number_col}, Line={line_number_col}, Qty={quantity_col}")
        except StopIteration:
            error_msg = "Required line mappings not found"
            print(f"ERROR: {error_msg}")
            results['errors'].append(error_msg)
            return
        except (ValueError, TypeError) as e:
            error_msg = f"Invalid column indices in line mapping: {e}"
            print(f"ERROR: {error_msg}")
            results['errors'].append(error_msg)
            return

        # Optional columns
        price_col = next(
            (col for col, map_val in mapping.items()
             if map_val == 'sales_order_lines.price'), None
        )

        # Pre-fetch sales orders and parts
        def refresh_lookups():
                """Refresh lookup dictionaries from database"""
                orders = {
                    row['sales_order_ref']: row['id']
                    for row in _execute_with_cursor(cur, "SELECT id, sales_order_ref FROM sales_orders").fetchall()
                }
                parts = {
                    row['system_part_number']: row['base_part_number']
                    for row in _execute_with_cursor(cur, "SELECT system_part_number, base_part_number FROM part_numbers").fetchall()
                }
                print(f"Refreshed lookups: {len(orders)} orders, {len(parts)} parts")
                return orders, parts

        all_orders, all_parts = refresh_lookups()

        # Track missing items and auto-created parts
        missing_orders = set()
        missing_parts = set()
        auto_created_parts = 0

        for idx, row in df.iterrows():
            try:
                sales_order_ref = str(row.iloc[int(sales_order_ref_col)]).strip()
                system_part_number = str(row.iloc[int(system_part_number_col)]).strip()
                line_number_str = str(row.iloc[int(line_number_col)]).strip()
                quantity_str = str(row.iloc[int(quantity_col)]).strip()

                # Better validation
                if not sales_order_ref or sales_order_ref.lower() in ['nan', 'none', '']:
                    results['errors'].append(f"Row {idx + 1}: Missing or invalid sales order reference")
                    results['skipped'] += 1
                    continue

                if not system_part_number or system_part_number.lower() in ['nan', 'none', '']:
                    results['errors'].append(f"Row {idx + 1}: Missing or invalid part number")
                    results['skipped'] += 1
                    continue

                try:
                    line_number = int(float(line_number_str))
                except (ValueError, TypeError):
                    results['errors'].append(f"Row {idx + 1}: Invalid line number '{line_number_str}'")
                    results['skipped'] += 1
                    continue

                try:
                    quantity = float(quantity_str)
                except (ValueError, TypeError):
                    results['errors'].append(f"Row {idx + 1}: Invalid quantity '{quantity_str}'")
                    results['skipped'] += 1
                    continue

                sales_order_id = all_orders.get(sales_order_ref)
                base_part_number = all_parts.get(system_part_number)

                # Enhanced error reporting for orders
                if not sales_order_id:
                    if sales_order_ref not in missing_orders:
                        missing_orders.add(sales_order_ref)
                        error_msg = f"Row {idx + 1}: Sales order '{sales_order_ref}' not found in database"
                        results['errors'].append(error_msg)
                        print(f"ERROR: {error_msg}")
                        similar = _execute_with_cursor(cur, """
                                           SELECT sales_order_ref FROM sales_orders 
                                           WHERE sales_order_ref LIKE ? LIMIT 3
                                       """, (f'%{sales_order_ref[:5]}%',)).fetchall()
                        if similar:
                            print(f"  Similar orders found: {[s['sales_order_ref'] for s in similar]}")
                    results['skipped'] += 1
                    continue

                # Try to create part on-demand if it doesn't exist
                if not base_part_number:
                    if system_part_number not in missing_parts:
                        print(f"Row {idx + 1}: Part '{system_part_number}' not found, attempting to create...")

                        # Try to get part_number from column 9 if available
                        part_number = system_part_number
                        part_number_col_idx = next(
                            (int(col) for col, map_val in mapping.items()
                             if map_val == 'part_numbers.part_number'), None
                        )
                        if part_number_col_idx is not None:
                            try:
                                part_val = row.iloc[part_number_col_idx]
                                if pd.notna(part_val) and str(part_val).strip():
                                    part_number = str(part_val).strip()
                            except:
                                pass

                        try:
                            base_part_number = create_part_on_demand(cur, system_part_number, part_number)
                            all_parts[system_part_number] = base_part_number  # Update cache
                            auto_created_parts += 1
                            print(f"  Successfully created part: {system_part_number}")
                        except Exception as e:
                            missing_parts.add(system_part_number)
                            error_msg = f"Row {idx + 1}: Part '{system_part_number}' not found in database"
                            results['errors'].append(error_msg)
                            print(f"ERROR: {error_msg}")
                            print(f"  Failed to auto-create: {e}")
                            similar = _execute_with_cursor(cur, """
                                               SELECT system_part_number FROM part_numbers 
                                               WHERE system_part_number LIKE ? LIMIT 3
                                           """, (f'%{system_part_number[:5]}%',)).fetchall()
                            if similar:
                                print(f"  Similar parts found: {[s['system_part_number'] for s in similar]}")
                            results['skipped'] += 1
                            continue

                price = None
                if price_col is not None:
                    try:
                        price_val = row.iloc[int(price_col)]
                        if pd.notna(price_val):
                            price = float(str(price_val).strip())
                    except (ValueError, TypeError):
                        pass

                # Check if line exists
                        existing = _execute_with_cursor(cur, """
                            SELECT id FROM sales_order_lines 
                            WHERE sales_order_id = ? AND line_number = ?
                        """, (sales_order_id, line_number)).fetchone()

                if existing:
                    results['skipped'] += 1
                else:
                    _execute_with_cursor(cur, """
                        INSERT INTO sales_order_lines (
                            sales_order_id, line_number, base_part_number,
                            quantity, price, sales_status_id,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """, (sales_order_id, line_number, base_part_number, quantity, price, 1))
                    results['created'] += 1

                results['processed'] += 1

            except Exception as e:
                error_msg = f"Row {idx + 1}: Unexpected error - {str(e)}"
                results['errors'].append(error_msg)
                print(f"ERROR: {error_msg}")
                import traceback
                traceback.print_exc()
                results['skipped'] += 1

        # Final summary
        if auto_created_parts > 0:
            print(f"\nAuto-created {auto_created_parts} parts that were missing during parts step")
        if missing_orders:
            print(f"\nSummary: {len(missing_orders)} unique sales orders were not found")
            print(f"Missing orders: {list(missing_orders)[:10]}{'...' if len(missing_orders) > 10 else ''}")
        if missing_parts:
            print(f"\nSummary: {len(missing_parts)} unique parts could not be created")
            print(f"Missing parts: {list(missing_parts)[:10]}{'...' if len(missing_parts) > 10 else ''}")
