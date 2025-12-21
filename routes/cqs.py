from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
import os
import csv
import io
from datetime import datetime
from db import db_cursor, execute as db_execute
from models import create_base_part_number

cqs_bp = Blueprint('cqs', __name__)


def _safe_int(value, default=0):
    try:
        if not value or value == '':
            return default
        return int(float(str(value).strip().strip('"')))
    except (ValueError, TypeError):
        return default


def _safe_float(value, default=0.0):
    try:
        if not value or value == '':
            return default
        return float(str(value).strip().strip('"'))
    except (ValueError, TypeError):
        return default


def _safe_bool(value, default=False):
    try:
        if not value or value == '':
            return default
        normalized = str(value).strip().lower()
        return normalized in ('true', '1', 'yes')
    except (ValueError, TypeError):
        return default


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query: str) -> str:
    return query if not _using_postgres() else query.replace('?', '%s')


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
    """Ensure a part_numbers entry by base part number (the true primary key)."""
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


@cqs_bp.route('/', methods=['GET'], endpoint='cqs_list')
def cqs_list():
    """List all CQs"""
    cqs = db_execute('''
        SELECT 
            c.id,
            c.cq_number,
            c.status,
            c.entry_date,
            c.due_date,
            cust.name as customer_name,
            curr.currency_code,
            COUNT(cl.id) as line_count
        FROM cqs c
        LEFT JOIN customers cust ON c.customer_id = cust.id
        LEFT JOIN currencies curr ON c.currency_id = curr.id
        LEFT JOIN cq_lines cl ON c.id = cl.cq_id
        GROUP BY c.id
        ORDER BY c.id DESC
    ''', fetch='all') or []

    return render_template('cqs_list.html', cqs=cqs)


@cqs_bp.route('/<int:cq_id>', methods=['GET'], endpoint='view_cq')
def view_cq(cq_id):
    """View a single CQ with all its lines"""
    cq = db_execute('''
        SELECT c.*, cust.name as customer_name, curr.currency_code
        FROM cqs c
        LEFT JOIN customers cust ON c.customer_id = cust.id
        LEFT JOIN currencies curr ON c.currency_id = curr.id
        WHERE c.id = ?
    ''', (cq_id,), fetch='one')

    if not cq:
        flash('CQ not found', 'danger')
        return redirect(url_for('cqs.cqs_list'))

    cq_lines = db_execute('''
        SELECT * FROM cq_lines
        WHERE cq_id = ?
        ORDER BY line_number
    ''', (cq_id,), fetch='all') or []

    return render_template('cq_detail.html', cq=cq, cq_lines=cq_lines)


@cqs_bp.route('/import', methods=['GET', 'POST'], endpoint='import_cqs')
def import_cqs():
    """Import CQs from CSV export"""
    if request.method == 'POST':
        if 'file' not in request.files:
            return jsonify(success=False, message='No file uploaded'), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify(success=False, message='No file selected'), 400

        if not file.filename.endswith('.csv'):
            return jsonify(success=False, message='Please upload a CSV file'), 400

        skip_auto_quoted = request.form.get('skip_auto_quoted') == 'on'

        try:
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.DictReader(stream, delimiter=',')

            cqs_processed = {}
            lines_imported = 0
            lines_skipped = 0
            errors = []

            with db_cursor(commit=True) as cur:
                for row in csv_reader:
                    if lines_imported == 0 and csv_reader.fieldnames:
                        print(f"First row data: {list(row.keys())[:10]}")

                    cq_number = row.get('transactionalNumber', '').strip()
                    if not cq_number:
                        continue

                    status = row.get('status', 'Created').strip()
                    if skip_auto_quoted and status == 'Auto-Quoted':
                        lines_skipped += 1
                        continue

                    if cq_number not in cqs_processed:
                        customer_name = row.get('companyName', '').strip()
                        if not customer_name:
                            errors.append(f"CQ {cq_number}: No customer name found")
                            continue

                        customer = _execute_with_cursor(
                            cur,
                            'SELECT id FROM customers WHERE name = ?',
                            (customer_name,),
                            fetch='one'
                        )
                        if customer:
                            customer_id = customer.get('id') if isinstance(customer, dict) else customer[0]
                        else:
                            customer_row = _execute_with_cursor(
                                cur,
                                'INSERT INTO customers (name) VALUES (?) RETURNING id' if _using_postgres() else 'INSERT INTO customers (name) VALUES (?)',
                                (customer_name,),
                                fetch='one' if _using_postgres() else None
                            )
                            customer_id = _get_inserted_id(customer_row, cur)

                        currency_code = row.get('foreignCurrency', '').strip() or row.get('baseCurrency', '').strip()
                        currency = _execute_with_cursor(
                            cur,
                            'SELECT id FROM currencies WHERE currency_code = ?',
                            (currency_code,),
                            fetch='one'
                        )
                        currency_id = currency.get('id') if currency else None

                        def parse_date(value):
                            if not value:
                                return None
                            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y %H:%M:%S'):
                                try:
                                    return datetime.strptime(value, fmt).date()
                                except ValueError:
                                    continue
                            return None

                        entry_date = parse_date(row.get('entryDate', '').strip())
                        due_date = parse_date(row.get('dueDate', '').strip())
                        sales_person = row.get('salesPerson', '').strip()

                        existing_cq = _execute_with_cursor(
                            cur,
                            'SELECT id FROM cqs WHERE cq_number = ?',
                            (cq_number,),
                            fetch='one'
                        )
                        if existing_cq:
                            cq_id = existing_cq.get('id') if isinstance(existing_cq, dict) else existing_cq[0]
                        else:
                            insert_cq_query = '''
                                INSERT INTO cqs (cq_number, customer_id, status, entry_date, due_date, currency_id, sales_person)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            '''
                            insert_cq_query_pg = insert_cq_query + ' RETURNING id' if _using_postgres() else insert_cq_query
                            insert_row = _execute_with_cursor(
                                cur,
                                insert_cq_query_pg,
                                (cq_number, customer_id, status, entry_date, due_date, currency_id, sales_person),
                                fetch='one' if _using_postgres() else None
                            )
                            cq_id = _get_inserted_id(insert_row, cur)

                        cqs_processed[cq_number] = cq_id
                    else:
                        cq_id = cqs_processed[cq_number]

                    part_number = row.get('partNumber', '').strip()
                    if not part_number:
                        errors.append(f"CQ {cq_number}: No part number found")
                        continue

                    base_part_number = create_base_part_number(part_number)
                    _ensure_part_number(cur, base_part_number, part_number)

                    existing_line = _execute_with_cursor(
                        cur,
                        '''
                        SELECT id FROM cq_lines 
                        WHERE cq_id = ? AND transaction_item_id = ?
                        ''',
                        (cq_id, row.get('transactionItemId', '').strip()),
                        fetch='one'
                    )
                    if existing_line:
                        continue

                    _execute_with_cursor(
                        cur,
                        '''
                        INSERT INTO cq_lines (
                            cq_id, transaction_header_id, transaction_item_id,
                            base_part_number, part_number, description,
                            condition_code, quantity_requested, quantity_quoted, quantity_allocated,
                            unit_of_measure, tran_type, base_currency, foreign_currency,
                            unit_cost, unit_price, total_price, total_foreign_price,
                            tax, total_cost, for_price, lead_days,
                            created_by, sales_person, core_charge, traceability,
                            is_no_quote, line_number, serial_number, 
                            tag_or_certificate_number
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            cq_id,
                            row.get('transactionHeaderId', '').strip(),
                            row.get('transactionItemId', '').strip(),
                            base_part_number,
                            part_number,
                            row.get('description', '').strip(),
                            row.get('conditionCode', '').strip(),
                            _safe_int(row.get('quantityRequested', '')),
                            _safe_int(row.get('quantityQuoted', '')),
                            _safe_int(row.get('quantityAllocated', '')),
                            row.get('unitOfMeasure', 'EA').strip(),
                            row.get('tranType', '').strip(),
                            row.get('baseCurrency', '').strip(),
                            row.get('foreignCurrency', '').strip(),
                            _safe_float(row.get('unitCost', '')),
                            _safe_float(row.get('unitPrice', '')),
                            _safe_float(row.get('totalPrice', '')),
                            _safe_float(row.get('totalForeignPrice', '')),
                            _safe_float(row.get('tax', '')),
                            _safe_float(row.get('totalCost', '')),
                            _safe_float(row.get('forPrice', '')),
                            _safe_int(row.get('leadDays', '')),
                            row.get('createdBy', '').strip(),
                            row.get('salesPerson', '').strip(),
                            _safe_float(row.get('coreCharge', '')),
                            row.get('traceability', '').strip(),
                            _safe_bool(row.get('isNoQuote', '')),
                            _safe_int(row.get('itemNumber', '')),
                            row.get('serialNumber', '').strip(),
                            row.get('tagOrCertificateNumber', '').strip()
                        )
                    )
                    lines_imported += 1

            message = f'Successfully imported {len(cqs_processed)} CQs with {lines_imported} lines'
            if lines_skipped > 0:
                message += f' ({lines_skipped} Auto-Quoted lines skipped)'

            return jsonify(
                success=True,
                message=message,
                results={
                    'cqs': {
                        'processed': len(cqs_processed),
                        'created': len(cqs_processed),
                        'errors': []
                    },
                    'lines': {
                        'processed': lines_imported,
                        'created': lines_imported,
                        'skipped': lines_skipped,
                        'errors': errors
                    }
                }
            )
        except Exception as e:
            print(f"Error during import: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify(success=False, message=f'Error importing CQs: {str(e)}'), 500

    return render_template('cqs_import.html')

@cqs_bp.route('/<int:cq_id>/delete', methods=['POST'], endpoint='delete_cq')
def delete_cq(cq_id):
    """Delete a CQ"""
    db_execute('DELETE FROM cqs WHERE id = ?', (cq_id,), commit=True)
    flash('CQ deleted successfully', 'success')
    return redirect(url_for('cqs.cqs_list'))


@cqs_bp.route('/api/cqs/search', methods=['GET'])
def search_cqs():
    """Search CQs by part number"""
    part_number = request.args.get('part_number', '')
    base_part_number = create_base_part_number(part_number)

    cq_lines = db_execute('''
        SELECT 
            cl.*,
            c.cq_number,
            c.status,
            c.entry_date,
            c.due_date,
            cust.name as customer_name,
            curr.currency_code
        FROM cq_lines cl
        JOIN cqs c ON cl.cq_id = c.id
        LEFT JOIN customers cust ON c.customer_id = cust.id
        LEFT JOIN currencies curr ON c.currency_id = curr.id
        WHERE cl.base_part_number = ?
        ORDER BY c.entry_date DESC
    ''', (base_part_number,), fetch='all') or []

    return jsonify({
        'cq_lines': [dict(line) for line in cq_lines]
    })

@cqs_bp.route('/low-conversion', methods=['GET'], endpoint='low_conversion_page')
def low_conversion_page():
    """Serve the low conversion analysis page"""
    min_cq = request.args.get('min_cq', 5, type=int)
    max_so = request.args.get('max_so', 1, type=int)

    parts_query = '''
        SELECT 
            MAX(cl.part_number) as part_number,
            cl.base_part_number,
            COUNT(DISTINCT cl.cq_id) as cq_count,
            (SELECT COUNT(sol.id) FROM sales_order_lines sol WHERE sol.base_part_number = cl.base_part_number) as so_count
        FROM cq_lines cl
        GROUP BY cl.base_part_number
        HAVING cq_count > ? AND so_count <= ?
        ORDER BY cq_count DESC, so_count ASC
    '''
    parts = db_execute(parts_query, (min_cq, max_so), fetch='all') or []

    parts_data = []
    for part_row in parts:
        part_number = part_row['part_number']
        base_part = part_row['base_part_number']
        cq_count = part_row['cq_count']
        so_count = part_row['so_count']

        # Get details of CQs for this part (unique by CQ, with dates, customer names, and quote prices)
        cqs_query = '''
            SELECT 
                c.cq_number,
                c.entry_date,
                cust.name as customer_name,
                MIN(cl.unit_price) as quote_price
            FROM cq_lines cl
            JOIN cqs c ON cl.cq_id = c.id
            LEFT JOIN customers cust ON c.customer_id = cust.id
            WHERE cl.base_part_number = ?
            GROUP BY c.id, c.cq_number, c.entry_date, cust.name
            ORDER BY c.entry_date DESC
        '''
        cqs_list = db_execute(cqs_query, (base_part,), fetch='all') or []
        cqs_data = [dict(cq) for cq in cqs_list]
        avg_cq_price = sum(cq.get('quote_price', 0) for cq in cqs_data) / len(cqs_data) if cqs_data else 0.0

        # Get details of VQs for this part (unique by VQ, with dates, supplier names, and vendor prices)
        vqs_query = '''
            SELECT 
                v.vq_number,
                MIN(vl.quoted_date) as quoted_date,
                s.name as supplier_name,
                MIN(vl.vendor_price) as vq_cost
            FROM vq_lines vl
            JOIN vqs v ON vl.vq_id = v.id
            LEFT JOIN suppliers s ON v.supplier_id = s.id
            WHERE vl.base_part_number = ?
            GROUP BY v.id, v.vq_number, s.name
            ORDER BY MIN(vl.quoted_date) DESC
        '''
        vqs_list = db_execute(vqs_query, (base_part,), fetch='all') or []
        vqs_data = [dict(vq) for vq in vqs_list]
        vq_count = len(vqs_data)
        avg_vq_cost = sum(vq.get('vq_cost', 0) for vq in vqs_data) / vq_count if vq_count else 0.0

        parts_data.append({
            'part_number': part_number,
            'base_part_number': base_part,
            'cq_count': cq_count,
            'so_count': so_count,
            'avg_cq_price': avg_cq_price,
            'vq_count': vq_count,
            'avg_vq_cost': avg_vq_cost,
            'cqs': cqs_data,
            'vqs': vqs_data
        })

    return render_template('low_conversion.html', parts=parts_data, min_cq=min_cq, max_so=max_so)
