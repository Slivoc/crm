import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
import csv
import io
from datetime import datetime
from db import db_cursor, execute as db_execute
from models import create_base_part_number

vqs_bp = Blueprint('vqs', __name__)


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query: str) -> str:
    if not _using_postgres():
        return query
    return query.replace('?', '%s')


def _execute_with_cursor(cur, query, params=None, fetch=None):
    cur.execute(_prepare_query(query), params or [])
    if fetch == 'one':
        return cur.fetchone()
    if fetch == 'all':
        return cur.fetchall()
    return None


def _get_inserted_id(row, cur):
    if row is None:
        return getattr(cur, 'lastrowid', None)
    if isinstance(row, dict):
        return row.get('id')
    try:
        return row[0]
    except Exception:
        return getattr(cur, 'lastrowid', None)


def _ensure_part_number(cur, base_part_number, part_number):
    """Make sure a part_numbers row exists; base_part_number is the PK in Postgres."""
    if _using_postgres():
        insert_query = '''
            INSERT INTO part_numbers (base_part_number, part_number)
            VALUES (?, ?)
            ON CONFLICT (base_part_number) DO NOTHING
        '''
    else:
        insert_query = '''
            INSERT OR IGNORE INTO part_numbers (base_part_number, part_number)
            VALUES (?, ?)
        '''
    _execute_with_cursor(cur, insert_query, (base_part_number, part_number))


@vqs_bp.route('/', methods=['GET'], endpoint='vqs_list')
def vqs_list():
    """List all VQs"""
    vqs = db_execute('''
        SELECT 
            v.id,
            v.vq_number,
            v.status,
            v.entry_date,
            v.expiration_date,
            s.name as supplier_name,
            c.currency_code,
            COUNT(vl.id) as line_count
        FROM vqs v
        LEFT JOIN suppliers s ON v.supplier_id = s.id
        LEFT JOIN currencies c ON v.currency_id = c.id
        LEFT JOIN vq_lines vl ON v.id = vl.vq_id
        GROUP BY v.id
        ORDER BY v.id DESC
    ''', fetch='all') or []

    return render_template('vqs_list.html', vqs=vqs)


@vqs_bp.route('/<int:vq_id>', methods=['GET'], endpoint='view_vq')
def view_vq(vq_id):
    """View a single VQ with all its lines"""
    vq = db_execute('''
        SELECT v.*, s.name as supplier_name, c.currency_code
        FROM vqs v
        LEFT JOIN suppliers s ON v.supplier_id = s.id
        LEFT JOIN currencies c ON v.currency_id = c.id
        WHERE v.id = ?
    ''', (vq_id,), fetch='one')

    if not vq:
        flash('VQ not found', 'danger')
        return redirect(url_for('vqs.vqs_list'))

    vq_lines = db_execute('''
        SELECT * FROM vq_lines
        WHERE vq_id = ?
        ORDER BY line_number
    ''', (vq_id,), fetch='all') or []

    return render_template('vq_detail.html', vq=vq, vq_lines=vq_lines)


@vqs_bp.route('/import', methods=['GET', 'POST'], endpoint='import_vqs')
def import_vqs():
    """Import VQs from CSV export"""
    if request.method == 'POST':
        print("POST request received")  # Debug

        if 'file' not in request.files:
            print("No file in request.files")  # Debug
            return jsonify(success=False, message='No file uploaded'), 400

        file = request.files['file']
        print(f"File received: {file.filename}")  # Debug

        if file.filename == '':
            return jsonify(success=False, message='No file selected'), 400

        if not file.filename.endswith('.csv'):
            return jsonify(success=False, message='Please upload a CSV file'), 400

        try:
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.DictReader(stream, delimiter=',')

            vqs_processed = {}
            lines_imported = 0
            errors = []

            def safe_int(value, default=0):
                try:
                    if not value or value == '':
                        return default
                    value = str(value).strip().strip('"')
                    return int(float(value))
                except (ValueError, TypeError):
                    return default

            def safe_float(value, default=0.0):
                try:
                    if not value or value == '':
                        return default
                    value = str(value).strip().strip('"')
                    return float(value)
                except (ValueError, TypeError):
                    return default

            with db_cursor(commit=True) as cur:
                for row in csv_reader:
                    if lines_imported == 0:
                        print(f"First row data: {list(row.keys())[:10]}")  # Debug

                    vq_number = row.get('transactionalNumber', '').strip()
                    if not vq_number:
                        print("Could not find VQ number in row")
                        continue

                    if vq_number not in vqs_processed:
                        supplier_name = row.get('companyName', '').strip()
                        if not supplier_name:
                            print(f"No supplier name found, skipping VQ {vq_number}")
                            errors.append(f"VQ {vq_number}: No supplier name found")
                            continue

                        supplier = _execute_with_cursor(
                            cur,
                            'SELECT id FROM suppliers WHERE name = ?',
                            (supplier_name,),
                            fetch='one'
                        )
                        if not supplier:
                            insert_supplier_query = 'INSERT INTO suppliers (name) VALUES (?)'
                            insert_supplier_query_pg = insert_supplier_query + ' RETURNING id' if _using_postgres() else insert_supplier_query
                            supplier_row = _execute_with_cursor(
                                cur,
                                insert_supplier_query_pg,
                                (supplier_name,),
                                fetch='one' if _using_postgres() else None
                            )
                            supplier_id = _get_inserted_id(supplier_row, cur)
                            print(f"Created new supplier: {supplier_name}")
                        else:
                            supplier_id = supplier.get('id') if isinstance(supplier, dict) else supplier[0]

                        currency_code = row.get('foreignCurrency', '').strip()
                        currency = _execute_with_cursor(
                            cur,
                            'SELECT id FROM currencies WHERE currency_code = ?',
                            (currency_code,),
                            fetch='one'
                        )
                        currency_id = currency.get('id') if currency else None

                        entry_date = None
                        entry_date_str = row.get('entryDate', '').strip()
                        if entry_date_str:
                            try:
                                entry_date = datetime.strptime(entry_date_str, '%Y-%m-%d').date()
                            except ValueError:
                                entry_date = datetime.strptime(entry_date_str, '%d/%m/%Y').date()

                        expiration_date = None
                        exp_date_str = row.get('expirationDate', '').strip()
                        if exp_date_str:
                            try:
                                expiration_date = datetime.strptime(exp_date_str, '%Y-%m-%d').date()
                            except ValueError:
                                expiration_date = datetime.strptime(exp_date_str, '%d/%m/%Y').date()

                        status = row.get('status', 'Created').strip()

                        existing_vq = _execute_with_cursor(
                            cur,
                            'SELECT id FROM vqs WHERE vq_number = ?',
                            (vq_number,),
                            fetch='one'
                        )
                        if existing_vq:
                            vq_id = existing_vq.get('id') if isinstance(existing_vq, dict) else existing_vq[0]
                            print(f"VQ {vq_number} already exists with id {vq_id}")
                        else:
                            insert_vq_query = '''
                                INSERT INTO vqs (vq_number, supplier_id, status, entry_date, expiration_date, currency_id)
                                VALUES (?, ?, ?, ?, ?, ?)
                            '''
                            insert_vq_query_pg = insert_vq_query + ' RETURNING id' if _using_postgres() else insert_vq_query
                            insert_row = _execute_with_cursor(
                                cur,
                                insert_vq_query_pg,
                                (vq_number, supplier_id, status, entry_date, expiration_date, currency_id),
                                fetch='one' if _using_postgres() else None
                            )
                            vq_id = _get_inserted_id(insert_row, cur)
                            print(f"Created new VQ {vq_number} with id {vq_id}")

                        vqs_processed[vq_number] = vq_id
                    else:
                        vq_id = vqs_processed[vq_number]

                    part_number = row.get('pnQuoted', '').strip()
                    if not part_number:
                        print(f"No part number found for line, skipping")
                        errors.append(f"VQ {vq_number}: No part number found")
                        continue

                    base_part_number = create_base_part_number(part_number)
                    _ensure_part_number(cur, base_part_number, part_number)

                    existing_line = _execute_with_cursor(
                        cur,
                        '''
                        SELECT id FROM vq_lines 
                        WHERE vq_id = ? AND vendor_response_id = ?
                        ''',
                        (vq_id, row.get('vendorResponseId', '').strip()),
                        fetch='one'
                    )
                    if existing_line:
                        print(f"Line already exists, skipping")
                        continue

                    quoted_date = None
                    quoted_date_str = row.get('quotedDate', '').strip()
                    if quoted_date_str:
                        try:
                            quoted_date = datetime.strptime(quoted_date_str, '%Y-%m-%d').date()
                        except ValueError:
                            quoted_date = datetime.strptime(quoted_date_str, '%d/%m/%Y').date()

                    insert_vq_line_query = '''
                        INSERT INTO vq_lines (
                            vq_id, vendor_response_id, transaction_id, transaction_item_id,
                            base_part_number, part_number, pn_quoted, description,
                            condition_code, quantity_quoted, quantity_requested,
                            unit_of_measure, lead_days, vendor_price, item_total, line_number,
                            foreign_currency, quoted_date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    '''
                    _execute_with_cursor(
                        cur,
                        insert_vq_line_query,
                        (
                            vq_id,
                            row.get('vendorResponseId', '').strip(),
                            row.get('transactionId', '').strip(),
                            row.get('transactionItemId', '').strip(),
                            base_part_number,
                            part_number,
                            part_number,
                            row.get('description', '').strip(),
                            row.get('conditionCode', '').strip(),
                            safe_int(row.get('quantityQuoted', '')),
                            safe_int(row.get('quantityRequested', '')),
                            row.get('unitOfMeasure', 'EA').strip(),
                            safe_int(row.get('leadDays', '')),
                            safe_float(row.get('vendorPrice', '')),
                            safe_float(row.get('itemTotal', '')),
                            safe_int(row.get('itemNumber', '')),
                            row.get('foreignCurrency', '').strip(),
                            quoted_date
                        )
                    )
                    lines_imported += 1

            print(f"Import complete: {len(vqs_processed)} VQs, {lines_imported} lines")  # Debug

            return jsonify(
                success=True,
                message=f'Successfully imported {len(vqs_processed)} VQs with {lines_imported} lines',
                results={
                    'vqs': {
                        'processed': len(vqs_processed),
                        'created': len(vqs_processed),
                        'errors': []
                    },
                    'lines': {
                        'processed': lines_imported,
                        'created': lines_imported,
                        'errors': errors
                    }
                }
            )

        except Exception as e:
            print(f"Error during import: {str(e)}")  # Debug
            import traceback
            traceback.print_exc()
            return jsonify(success=False, message=f'Error importing VQs: {str(e)}'), 500

    # GET request - show the form
    return render_template('vqs_import.html')

@vqs_bp.route('/<int:vq_id>/delete', methods=['POST'], endpoint='delete_vq')
def delete_vq(vq_id):
    """Delete a VQ"""
    db_execute('DELETE FROM vqs WHERE id = ?', (vq_id,), commit=True)

    flash('VQ deleted successfully', 'success')
    return redirect(url_for('vqs.vqs_list'))


@vqs_bp.route('/api/vqs/search', methods=['GET'])
def search_vqs():
    """Search VQs by part number"""
    part_number = request.args.get('part_number', '')
    base_part_number = create_base_part_number(part_number)

    vq_lines = db_execute('''
        SELECT 
            vl.*,
            v.vq_number,
            v.status,
            v.entry_date,
            v.expiration_date,
            s.name as supplier_name,
            c.currency_code
        FROM vq_lines vl
        JOIN vqs v ON vl.vq_id = v.id
        LEFT JOIN suppliers s ON v.supplier_id = s.id
        LEFT JOIN currencies c ON v.currency_id = c.id
        WHERE vl.base_part_number = ?
        ORDER BY v.entry_date DESC
    ''', (base_part_number,), fetch='all') or []

    return jsonify({
        'vq_lines': [dict(line) for line in vq_lines]
    })


def get_vq_by_id(vq_id):
    """Helper function to get VQ by ID"""
    row = db_execute('SELECT * FROM vqs WHERE id = ?', (vq_id,), fetch='one')
    return dict(row) if row else None


def get_vq_by_number(vq_number):
    """Helper function to get VQ by VQ number"""
    row = db_execute('SELECT * FROM vqs WHERE vq_number = ?', (vq_number,), fetch='one')
    return dict(row) if row else None
