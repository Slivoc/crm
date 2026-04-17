from flask import Blueprint, render_template, jsonify, request, redirect, url_for, Response
from db import db_cursor, execute as db_execute
from models import create_base_part_number
import logging
import pandas as pd
from werkzeug.utils import secure_filename
import os
import csv
import io
from datetime import datetime, timedelta


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


def _build_in_clause(values):
    if not values:
        return None, []
    return ', '.join(['?'] * len(values)), values


def _parse_positive_int(value, default=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _parse_percentage(value, default=50):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return 0
    if parsed > 100:
        return 100
    return parsed


def _get_all_kit_boms():
    return db_execute('''
        SELECT id, name, description
        FROM bom_headers
        WHERE type = 'kit'
        ORDER BY name
    ''', fetch='all') or []


def _get_recent_offer_cutoff(recent_offer_days):
    if not recent_offer_days:
        return None
    return (datetime.utcnow() - timedelta(days=recent_offer_days)).strftime('%Y-%m-%d %H:%M:%S')


def _get_bom_stock_report_data(selected_bom_ids, include_recent_offers=False, recent_offer_days=None):
    if not selected_bom_ids:
        return [], []

    in_clause, params = _build_in_clause(selected_bom_ids)
    if not in_clause:
        return [], []

    selected_boms = db_execute(f'''
        SELECT id, name
        FROM bom_headers
        WHERE id IN ({in_clause})
        ORDER BY name
    ''', params, fetch='all') or []

    if not selected_boms:
        return [], []

    selected_bom_ids = [int(row['id']) for row in selected_boms]
    in_clause, params = _build_in_clause(selected_bom_ids)

    recent_offer_clause = ''
    query_params = list(params)
    if include_recent_offers and recent_offer_days:
        recent_offer_cutoff = _get_recent_offer_cutoff(recent_offer_days)
        recent_offer_clause = '''
            , recent_offer_parts AS (
                SELECT
                    pll.base_part_number,
                    COUNT(*) AS recent_offer_count,
                    MAX(COALESCE(sq.quote_date, sq.date_created)) AS latest_offer_date
                FROM parts_list_supplier_quote_lines sql
                JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                WHERE COALESCE(sq.quote_date, sq.date_created) >= ?
                GROUP BY pll.base_part_number
            )
        '''
        query_params.append(recent_offer_cutoff)

    rows = db_execute(f'''
        WITH selected_parts AS (
            SELECT DISTINCT bl.base_part_number
            FROM bom_lines bl
            WHERE bl.bom_header_id IN ({in_clause})
        ),
        stock_totals AS (
            SELECT
                sm.base_part_number,
                SUM(sm.available_quantity) AS amount_in_stock
            FROM stock_movements sm
            WHERE sm.movement_type = 'IN'
              AND sm.available_quantity > 0
            GROUP BY sm.base_part_number
        )
        {recent_offer_clause}
        SELECT
            sp.base_part_number,
            COALESCE(MAX(pn.part_number), sp.base_part_number) AS part_number,
            COALESCE(st.amount_in_stock, 0) AS amount_in_stock
            {", COALESCE(rop.recent_offer_count, 0) AS recent_offer_count, rop.latest_offer_date" if recent_offer_clause else ""}
        FROM selected_parts sp
        LEFT JOIN stock_totals st ON st.base_part_number = sp.base_part_number
        LEFT JOIN part_numbers pn ON pn.base_part_number = sp.base_part_number
        {"LEFT JOIN recent_offer_parts rop ON rop.base_part_number = sp.base_part_number" if recent_offer_clause else ""}
        WHERE COALESCE(st.amount_in_stock, 0) > 0
            {"OR COALESCE(rop.recent_offer_count, 0) > 0" if recent_offer_clause else ""}
        GROUP BY
            sp.base_part_number,
            st.amount_in_stock
            {", rop.recent_offer_count, rop.latest_offer_date" if recent_offer_clause else ""}
        ORDER BY COALESCE(MAX(pn.part_number), sp.base_part_number)
    ''', query_params, fetch='all') or []

    memberships = db_execute(f'''
        SELECT DISTINCT
            bl.base_part_number,
            bl.bom_header_id
        FROM bom_lines bl
        WHERE bl.bom_header_id IN ({in_clause})
    ''', params, fetch='all') or []

    membership_map = {}
    for membership in memberships:
        base_part_number = membership['base_part_number']
        bom_id = int(membership['bom_header_id'])
        membership_map.setdefault(base_part_number, set()).add(bom_id)

    matrix_rows = []
    for row in rows:
        row_data = dict(row)
        base_part_number = row['base_part_number']
        bom_flags = {
            bom['id']: ('X' if bom['id'] in membership_map.get(base_part_number, set()) else '')
            for bom in selected_boms
        }
        matrix_rows.append({
            'part_number': row_data.get('part_number') or base_part_number,
            'amount_in_stock': row_data.get('amount_in_stock') or 0,
            'recent_offer_count': row_data.get('recent_offer_count', 0),
            'latest_offer_date': row_data.get('latest_offer_date'),
            'bom_flags': bom_flags
        })

    return selected_boms, matrix_rows


def _get_bom_commonality_report_data(selected_bom_ids, recent_offer_days=None, coverage_threshold_pct=50):
    if not selected_bom_ids:
        return [], []

    in_clause, params = _build_in_clause(selected_bom_ids)
    if not in_clause:
        return [], []

    selected_boms = db_execute(f'''
        SELECT id, name
        FROM bom_headers
        WHERE id IN ({in_clause})
        ORDER BY name
    ''', params, fetch='all') or []

    if not selected_boms:
        return [], []

    selected_bom_ids = [int(row['id']) for row in selected_boms]
    in_clause, params = _build_in_clause(selected_bom_ids)
    recent_offer_cutoff = _get_recent_offer_cutoff(recent_offer_days)
    query_params = list(params)
    if recent_offer_cutoff:
        query_params.append(recent_offer_cutoff)

    rows = db_execute(f'''
        WITH selected_lines AS (
            SELECT DISTINCT
                bl.bom_header_id,
                bl.base_part_number
            FROM bom_lines bl
            WHERE bl.bom_header_id IN ({in_clause})
        ),
        common_parts AS (
            SELECT
                sl.base_part_number,
                COUNT(*) AS bom_count
            FROM selected_lines sl
            GROUP BY sl.base_part_number
        ),
        stock_totals AS (
            SELECT
                sm.base_part_number,
                SUM(sm.available_quantity) AS amount_in_stock
            FROM stock_movements sm
            WHERE sm.movement_type = 'IN'
              AND sm.available_quantity > 0
            GROUP BY sm.base_part_number
        ),
        recent_offer_parts AS (
            SELECT
                pll.base_part_number,
                COUNT(*) AS recent_offer_count,
                MAX(COALESCE(sq.quote_date, sq.date_created)) AS latest_offer_date
            FROM parts_list_supplier_quote_lines sql
            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
            JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
            WHERE COALESCE(sq.quote_date, sq.date_created) >= ?
            GROUP BY pll.base_part_number
        )
        SELECT
            cp.base_part_number,
            COALESCE(MAX(pn.part_number), cp.base_part_number) AS part_number,
            cp.bom_count,
            COALESCE(st.amount_in_stock, 0) AS amount_in_stock,
            COALESCE(rop.recent_offer_count, 0) AS recent_offer_count,
            rop.latest_offer_date
        FROM common_parts cp
        LEFT JOIN stock_totals st ON st.base_part_number = cp.base_part_number
        LEFT JOIN recent_offer_parts rop ON rop.base_part_number = cp.base_part_number
        LEFT JOIN part_numbers pn ON pn.base_part_number = cp.base_part_number
        GROUP BY
            cp.base_part_number,
            cp.bom_count,
            st.amount_in_stock,
            rop.recent_offer_count,
            rop.latest_offer_date
        ORDER BY
            cp.bom_count DESC,
            COALESCE(st.amount_in_stock, 0) DESC,
            COALESCE(MAX(pn.part_number), cp.base_part_number)
    ''', query_params, fetch='all') or []

    memberships = db_execute(f'''
        SELECT DISTINCT
            bl.base_part_number,
            bl.bom_header_id
        FROM bom_lines bl
        WHERE bl.bom_header_id IN ({in_clause})
    ''', params, fetch='all') or []

    membership_map = {}
    for membership in memberships:
        base_part_number = membership['base_part_number']
        bom_id = int(membership['bom_header_id'])
        membership_map.setdefault(base_part_number, set()).add(bom_id)

    total_selected_boms = len(selected_boms)
    minimum_bom_count = 0
    if total_selected_boms:
        minimum_bom_count = max(1, int((coverage_threshold_pct / 100) * total_selected_boms + 0.999999))
    report_rows = []
    for row in rows:
        row_data = dict(row)
        base_part_number = row_data['base_part_number']
        bom_count = row_data.get('bom_count') or 0
        if bom_count < minimum_bom_count:
            continue
        bom_ids = membership_map.get(base_part_number, set())
        bom_flags = {
            bom['id']: ('X' if bom['id'] in bom_ids else '')
            for bom in selected_boms
        }
        report_rows.append({
            'part_number': row_data.get('part_number') or base_part_number,
            'amount_in_stock': row_data.get('amount_in_stock') or 0,
            'recent_offer_count': row_data.get('recent_offer_count', 0),
            'latest_offer_date': row_data.get('latest_offer_date'),
            'bom_count': bom_count,
            'bom_coverage_pct': (bom_count / total_selected_boms * 100) if total_selected_boms else 0,
            'bom_flags': bom_flags,
        })

    return selected_boms, report_rows

def _load_bom_dataframe(file, filename):
    if filename.endswith('.csv'):
        return pd.read_csv(file)
    if filename.endswith(('.xlsx', '.xls')):
        return pd.read_excel(file)
    raise ValueError("Unsupported file format. Use CSV or Excel")


def _import_bom_dataframe(cur, bom_id, df, start_position=0):
    imported_count = 0
    skipped_count = 0

    for idx, row in df.iterrows():
        try:
            raw_part_number = str(row.get('part_number', '')).strip()
            if not raw_part_number:
                skipped_count += 1
                continue

            base_part_number = create_base_part_number(raw_part_number)
            quantity = int(row.get('quantity', 1))
            position = start_position + ((idx + 1) * 10)
            raw_guide_price = row.get('guide_price')
            guide_price = float(raw_guide_price) if pd.notna(raw_guide_price) else None

            part = _execute_with_cursor(cur,
                'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
                (base_part_number,)
            ).fetchone()

            if not part:
                _execute_with_cursor(cur,
                    'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                    (raw_part_number, base_part_number)
                )

            _execute_with_cursor(cur, '''
                INSERT INTO bom_lines (
                    bom_header_id, base_part_number, quantity,
                    reference_designator, notes, position, guide_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                bom_id,
                base_part_number,
                quantity,
                row.get('reference_designator'),
                row.get('notes'),
                position,
                guide_price
            ))

            imported_count += 1
        except Exception as exc:
            logging.error(f"Failed to import row {idx}: {exc}", exc_info=True)
            skipped_count += 1

    return imported_count, skipped_count

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


@bom_bp.route('/common-parts-report')
def common_parts_report():
    selected_bom_ids = request.args.getlist('bom_ids', type=int)
    recent_offer_days = _parse_positive_int(request.args.get('recent_offer_days'), default=30)
    coverage_threshold_pct = _parse_percentage(request.args.get('coverage_threshold_pct'), default=50)
    all_boms = _get_all_kit_boms()
    selected_boms, report_rows = _get_bom_commonality_report_data(
        selected_bom_ids,
        recent_offer_days=recent_offer_days,
        coverage_threshold_pct=coverage_threshold_pct,
    )

    return render_template(
        'bom/common_parts_report.html',
        boms=all_boms,
        selected_bom_ids=selected_bom_ids,
        selected_boms=selected_boms,
        report_rows=report_rows,
        recent_offer_days=recent_offer_days,
        coverage_threshold_pct=coverage_threshold_pct,
    )


@bom_bp.route('/common-parts-report.csv')
def common_parts_report_csv():
    selected_bom_ids = request.args.getlist('bom_ids', type=int)
    recent_offer_days = _parse_positive_int(request.args.get('recent_offer_days'), default=30)
    coverage_threshold_pct = _parse_percentage(request.args.get('coverage_threshold_pct'), default=50)
    selected_boms, report_rows = _get_bom_commonality_report_data(
        selected_bom_ids,
        recent_offer_days=recent_offer_days,
        coverage_threshold_pct=coverage_threshold_pct,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    header = [
        'Part Number',
        'BOM Count',
        'BOM Coverage %',
        'Coverage Threshold %',
        'Amount In Stock',
        f'Recent Supplier Offers ({recent_offer_days} days)',
        'Latest Supplier Offer Date',
    ] + [bom['name'] for bom in selected_boms]
    writer.writerow(header)

    for row in report_rows:
        csv_row = [
            row['part_number'],
            row['bom_count'],
            round(float(row['bom_coverage_pct']), 2),
            coverage_threshold_pct,
            row['amount_in_stock'],
            row.get('recent_offer_count', 0),
            row.get('latest_offer_date') or '',
        ]
        for bom in selected_boms:
            csv_row.append(row['bom_flags'].get(bom['id'], ''))
        writer.writerow(csv_row)

    csv_data = output.getvalue()
    output.close()

    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=bom_common_parts_report_{recent_offer_days}d.csv'}
    )


@bom_bp.route('/stock-report')
def stock_report():
    selected_bom_ids = request.args.getlist('bom_ids', type=int)
    include_recent_offers = str(request.args.get('include_recent_offers', '')).lower() in ('1', 'true', 'yes', 'on')
    recent_offer_days = _parse_positive_int(request.args.get('recent_offer_days'), default=30)

    all_boms = _get_all_kit_boms()

    selected_boms, matrix_rows = _get_bom_stock_report_data(
        selected_bom_ids,
        include_recent_offers=include_recent_offers,
        recent_offer_days=recent_offer_days,
    )

    return render_template(
        'bom/stock_report.html',
        boms=all_boms,
        selected_bom_ids=selected_bom_ids,
        selected_boms=selected_boms,
        matrix_rows=matrix_rows,
        include_recent_offers=include_recent_offers,
        recent_offer_days=recent_offer_days,
    )


@bom_bp.route('/stock-report.csv')
def stock_report_csv():
    selected_bom_ids = request.args.getlist('bom_ids', type=int)
    include_recent_offers = str(request.args.get('include_recent_offers', '')).lower() in ('1', 'true', 'yes', 'on')
    recent_offer_days = _parse_positive_int(request.args.get('recent_offer_days'), default=30)
    selected_boms, matrix_rows = _get_bom_stock_report_data(
        selected_bom_ids,
        include_recent_offers=include_recent_offers,
        recent_offer_days=recent_offer_days,
    )

    output = io.StringIO()
    writer = csv.writer(output)

    header = ['Part Number', 'Amount In Stock', 'Recent Supplier Offers', 'Latest Supplier Offer Date'] + [bom['name'] for bom in selected_boms]
    writer.writerow(header)

    for row in matrix_rows:
        csv_row = [
            row['part_number'],
            row['amount_in_stock'],
            row.get('recent_offer_count', 0),
            row.get('latest_offer_date') or '',
        ]
        for bom in selected_boms:
            csv_row.append(row['bom_flags'].get(bom['id'], ''))
        writer.writerow(csv_row)

    csv_data = output.getvalue()
    output.close()

    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=bom_stock_report.csv'}
    )


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
                    df = _load_bom_dataframe(file, filename)
                    _import_bom_dataframe(cur, bom_id, df)

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
        df = _load_bom_dataframe(file, filename)
    except Exception as exc:
        logging.error(f"Failed to read file for BOM {bom_id}: {exc}")
        return jsonify({'error': f"Failed to read file: {exc}"}), 400

    logging.info(f"Loaded dataframe with {len(df)} rows")
    logging.info(f"Columns in file: {list(df.columns)}")

    with db_cursor(commit=True) as cur:
        max_position_row = _execute_with_cursor(cur, '''
            SELECT COALESCE(MAX(position), 0) as max_pos
            FROM bom_lines
            WHERE bom_header_id = ?
        ''', (bom_id,)).fetchone()
        max_position = max_position_row['max_pos'] if max_position_row else 0
        logging.info(f"Current max position: {max_position}")
        imported_count, skipped_count = _import_bom_dataframe(cur, bom_id, df, start_position=max_position)

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


@bom_bp.route('/delete/<int:bom_id>', methods=['POST'])
def delete_bom(bom_id):
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                DELETE FROM bom_pricing
                WHERE bom_line_id IN (
                    SELECT id FROM bom_lines WHERE bom_header_id = ?
                )
            ''', (bom_id,))
            _execute_with_cursor(cur, 'DELETE FROM bom_lines WHERE bom_header_id = ?', (bom_id,))
            _execute_with_cursor(cur, 'DELETE FROM bom_files WHERE bom_header_id = ?', (bom_id,))
            _execute_with_cursor(cur, 'DELETE FROM customer_boms WHERE bom_header_id = ?', (bom_id,))
            _execute_with_cursor(cur, 'DELETE FROM bom_revisions WHERE bom_header_id = ?', (bom_id,))
            _execute_with_cursor(cur, 'DELETE FROM bom_headers WHERE id = ?', (bom_id,))

        return jsonify({
            'status': 'success',
            'message': 'BOM deleted successfully'
        })
    except Exception as exc:
        logging.error(f"Error deleting BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400
