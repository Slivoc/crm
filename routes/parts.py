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
    get_global_alternatives, add_global_alternative,
    get_parts_list_lines_by_part_number, get_supplier_quotes_by_part_number,
    get_bom_lines_by_part_number, get_excess_lines_by_part_number, get_manufacturer_approvals_by_part_number
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


def _build_in_clause(values):
    cleaned = [value for value in (values or []) if value is not None]
    if not cleaned:
        return '', []
    placeholders = ','.join(['?'] * len(cleaned))
    return placeholders, cleaned


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


def _normalize_base_part_numbers(part_numbers):
    normalized = []
    seen = set()

    for raw_part in part_numbers or []:
        cleaned = create_base_part_number((raw_part or '').strip())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)

    return normalized


def _fetch_alt_group_members(cur, group_id):
    _execute_with_cursor(
        cur,
        '''
        SELECT base_part_number
        FROM part_alt_group_members
        WHERE group_id = ?
        ORDER BY base_part_number
        ''',
        (group_id,),
    )
    return [row['base_part_number'] for row in cur.fetchall()]


def _cleanup_alt_group_if_needed(cur, group_id):
    members = _fetch_alt_group_members(cur, group_id)

    if len(members) >= 2:
        return members

    _execute_with_cursor(
        cur,
        'DELETE FROM part_alt_group_members WHERE group_id = ?',
        (group_id,),
    )
    _execute_with_cursor(
        cur,
        'DELETE FROM part_alt_groups WHERE id = ?',
        (group_id,),
    )
    return []


def _get_alt_groups(search_query=''):
    search_query = (search_query or '').strip()
    if not search_query:
        return []

    like_term = f'%{search_query.lower()}%'

    summary_query = '''
        WITH matched_groups AS (
            SELECT DISTINCT m.group_id
            FROM part_alt_group_members m
            WHERE LOWER(m.base_part_number) LIKE ?
        )
        SELECT
            mg.group_id AS group_id,
            COALESCE(g.description, '') AS description,
            COUNT(m.base_part_number) AS member_count
        FROM matched_groups mg
        LEFT JOIN part_alt_groups g ON g.id = mg.group_id
        JOIN part_alt_group_members m ON m.group_id = mg.group_id
        GROUP BY mg.group_id, g.description
        ORDER BY COUNT(m.base_part_number) DESC, mg.group_id DESC
    '''

    rows = db_execute(summary_query, (like_term,), fetch='all') or []
    if not rows:
        return []

    group_ids = [row['group_id'] for row in rows]
    placeholders = ', '.join(['?'] * len(group_ids))
    member_rows = db_execute(
        f'''
        SELECT group_id, base_part_number
        FROM part_alt_group_members
        WHERE group_id IN ({placeholders})
        ORDER BY group_id DESC, base_part_number
        ''',
        group_ids,
        fetch='all',
    ) or []

    members_by_group = {}
    for member_row in member_rows:
        members_by_group.setdefault(member_row['group_id'], []).append(member_row['base_part_number'])

    groups = []
    for row in rows:
        groups.append({
            'group_id': row['group_id'],
            'description': row.get('description') or '',
            'member_count': row.get('member_count', 0),
            'members': members_by_group.get(row['group_id'], []),
        })

    return groups


def _get_largest_alt_groups(limit=25, min_size=5):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 25

    try:
        min_size = int(min_size)
    except (TypeError, ValueError):
        min_size = 5

    limit = max(1, min(limit, 100))
    min_size = max(2, min(min_size, 1000))

    rows = db_execute(
        '''
        SELECT
            m.group_id AS group_id,
            COALESCE(g.description, '') AS description,
            COUNT(m.base_part_number) AS member_count
        FROM part_alt_group_members m
        LEFT JOIN part_alt_groups g ON g.id = m.group_id
        GROUP BY m.group_id, g.description
        HAVING COUNT(m.base_part_number) >= ?
        ORDER BY COUNT(m.base_part_number) DESC, m.group_id DESC
        LIMIT ?
        ''',
        (min_size, limit),
        fetch='all',
    ) or []

    if not rows:
        return []

    group_ids = [row['group_id'] for row in rows]
    placeholders = ', '.join(['?'] * len(group_ids))
    member_rows = db_execute(
        f'''
        SELECT group_id, base_part_number
        FROM part_alt_group_members
        WHERE group_id IN ({placeholders})
        ORDER BY group_id DESC, base_part_number
        ''',
        group_ids,
        fetch='all',
    ) or []

    members_by_group = {}
    for member_row in member_rows:
        members_by_group.setdefault(member_row['group_id'], []).append(member_row['base_part_number'])

    groups = []
    for row in rows:
        groups.append({
            'group_id': row['group_id'],
            'description': row.get('description') or '',
            'member_count': row.get('member_count', 0),
            'members': members_by_group.get(row['group_id'], []),
        })

    return groups


def _alt_groups_redirect_args(form_data):
    view_mode = (form_data.get('view') or '').strip().lower()

    if view_mode == 'largest':
        limit = form_data.get('limit', 25)
        min_size = form_data.get('min_size', 5)
        return {
            'view': 'largest',
            'limit': int(limit) if str(limit).isdigit() else 25,
            'min_size': int(min_size) if str(min_size).isdigit() else 5,
        }

    return {
        'q': (form_data.get('q') or '').strip(),
    }


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


@parts_bp.route('/stock-building', methods=['GET'])
def stock_building():
    selected_customer_ids = [cid for cid in request.args.getlist('customer_ids', type=int) if cid]
    selected_bom_ids = [bid for bid in request.args.getlist('bom_ids', type=int) if bid]
    selected_qpl_manufacturers = [name.strip() for name in request.args.getlist('qpl_manufacturers') if (name or '').strip()]
    exclude_in_stock = str(request.args.get('exclude_in_stock', '')).lower() in ('1', 'true', 'yes', 'on')

    customers = db_execute(
        '''
        SELECT id, name
        FROM customers
        ORDER BY name
        ''',
        fetch='all',
    ) or []
    boms = db_execute(
        '''
        SELECT id, name, description
        FROM bom_headers
        WHERE type = 'kit'
        ORDER BY name
        ''',
        fetch='all',
    ) or []

    customer_clause, customer_params = _build_in_clause(selected_customer_ids)
    bom_clause, bom_params = _build_in_clause(selected_bom_ids)

    parts_list_query = '''
        SELECT
            pll.base_part_number,
            COUNT(*) AS parts_list_frequency,
            COUNT(DISTINCT pl.customer_id) AS parts_list_customer_count
        FROM parts_list_lines pll
        JOIN parts_lists pl ON pl.id = pll.parts_list_id
        WHERE pll.base_part_number IS NOT NULL
          AND TRIM(pll.base_part_number) <> ''
    '''
    parts_list_params = []
    if customer_clause:
        parts_list_query += f' AND pl.customer_id IN ({customer_clause})'
        parts_list_params.extend(customer_params)
    parts_list_query += ' GROUP BY pll.base_part_number'
    parts_list_rows = db_execute(parts_list_query, tuple(parts_list_params), fetch='all') or []

    sales_query = '''
        SELECT
            sol.base_part_number,
            COUNT(*) AS sales_frequency,
            COUNT(DISTINCT so.customer_id) AS sales_customer_count
        FROM sales_order_lines sol
        JOIN sales_orders so ON so.id = sol.sales_order_id
        WHERE sol.base_part_number IS NOT NULL
          AND TRIM(sol.base_part_number) <> ''
    '''
    sales_params = []
    if customer_clause:
        sales_query += f' AND so.customer_id IN ({customer_clause})'
        sales_params.extend(customer_params)
    sales_query += ' GROUP BY sol.base_part_number'
    sales_rows = db_execute(sales_query, tuple(sales_params), fetch='all') or []

    stock_rows = db_execute(
        '''
        SELECT
            base_part_number,
            COALESCE(SUM(available_quantity), 0) AS stock_quantity
        FROM stock_movements
        WHERE movement_type = 'IN'
          AND available_quantity > 0
          AND base_part_number IS NOT NULL
          AND TRIM(base_part_number) <> ''
        GROUP BY base_part_number
        ''',
        fetch='all',
    ) or []

    try:
        mapped_qpl_rows = db_execute(
            '''
            SELECT
                ma.base_part_number,
                ma.manufacturer_name,
                map.supplier_id,
                s.name AS supplier_name
            FROM manufacturer_approvals ma
            JOIN qpl_manufacturer_supplier_mappings map
              ON LOWER(TRIM(ma.manufacturer_name)) = map.manufacturer_name_normalized
            LEFT JOIN suppliers s ON s.id = map.supplier_id
            WHERE ma.base_part_number IS NOT NULL
              AND TRIM(ma.base_part_number) <> ''
              AND ma.manufacturer_name IS NOT NULL
              AND TRIM(ma.manufacturer_name) <> ''
            ''',
            fetch='all',
        ) or []
    except Exception:
        current_app.logger.warning('QPL mapping table not available for stock building consolidated report.')
        mapped_qpl_rows = []

    bom_membership_query = '''
        SELECT DISTINCT
            bl.base_part_number,
            bh.id AS bom_id,
            bh.name AS bom_name
        FROM bom_lines bl
        JOIN bom_headers bh ON bh.id = bl.bom_header_id
        WHERE bh.type = 'kit'
          AND bl.base_part_number IS NOT NULL
          AND TRIM(bl.base_part_number) <> ''
    '''
    bom_membership_params = []
    if bom_clause:
        bom_membership_query += f' AND bh.id IN ({bom_clause})'
        bom_membership_params.extend(bom_params)
    bom_membership_rows = db_execute(bom_membership_query, tuple(bom_membership_params), fetch='all') or []

    part_rows = {}
    for row in parts_list_rows:
        base_part = (row.get('base_part_number') or '').strip().upper()
        if not base_part:
            continue
        part_rows.setdefault(base_part, {'base_part_number': base_part})
        part_rows[base_part]['parts_list_frequency'] = int(row.get('parts_list_frequency') or 0)
        part_rows[base_part]['parts_list_customer_count'] = int(row.get('parts_list_customer_count') or 0)

    for row in sales_rows:
        base_part = (row.get('base_part_number') or '').strip().upper()
        if not base_part:
            continue
        part_rows.setdefault(base_part, {'base_part_number': base_part})
        part_rows[base_part]['sales_frequency'] = int(row.get('sales_frequency') or 0)
        part_rows[base_part]['sales_customer_count'] = int(row.get('sales_customer_count') or 0)

    for row in stock_rows:
        base_part = (row.get('base_part_number') or '').strip().upper()
        if not base_part:
            continue
        part_rows.setdefault(base_part, {'base_part_number': base_part})
        part_rows[base_part]['stock_quantity'] = float(row.get('stock_quantity') or 0)

    for row in bom_membership_rows:
        base_part = (row.get('base_part_number') or '').strip().upper()
        if not base_part:
            continue
        part_rows.setdefault(base_part, {'base_part_number': base_part})
        bom_map = part_rows[base_part].setdefault('bom_map', {})
        bom_id = int(row.get('bom_id'))
        bom_map[bom_id] = row.get('bom_name') or f'BOM {bom_id}'

    for row in mapped_qpl_rows:
        base_part = (row.get('base_part_number') or '').strip().upper()
        if not base_part:
            continue
        part_rows.setdefault(base_part, {'base_part_number': base_part})
        qpl_mappings = part_rows[base_part].setdefault('qpl_mappings', [])
        qpl_mappings.append({
            'manufacturer_name': row.get('manufacturer_name'),
            'supplier_name': row.get('supplier_name'),
            'supplier_id': row.get('supplier_id'),
        })

    all_bom_ids = sorted({
        int(row.get('bom_id'))
        for row in bom_membership_rows
        if row.get('bom_id') is not None
    })
    bom_name_by_id = {
        int(row.get('bom_id')): (row.get('bom_name') or f"BOM {row.get('bom_id')}")
        for row in bom_membership_rows
        if row.get('bom_id') is not None
    }

    base_parts = list(part_rows.keys())
    if base_parts:
        in_clause, pn_params = _build_in_clause(base_parts)
        part_number_rows = db_execute(
            f'''
            SELECT base_part_number, MAX(part_number) AS part_number
            FROM part_numbers
            WHERE base_part_number IN ({in_clause})
            GROUP BY base_part_number
            ''',
            tuple(pn_params),
            fetch='all',
        ) or []
    else:
        part_number_rows = []

    part_number_map = {
        (row.get('base_part_number') or '').strip().upper(): (row.get('part_number') or row.get('base_part_number'))
        for row in part_number_rows
    }

    qpl_manufacturer_option_set = set()
    consolidated_rows = []
    selected_qpl_keys = {name.lower() for name in selected_qpl_manufacturers}
    selected_bom_id_set = set(selected_bom_ids)

    for base_part, data in part_rows.items():
        qpl_mappings = data.get('qpl_mappings') or []
        mapped_manufacturers = sorted({
            (mapping.get('manufacturer_name') or '').strip()
            for mapping in qpl_mappings
            if (mapping.get('manufacturer_name') or '').strip()
        }, key=lambda name: name.lower())
        qpl_manufacturer_option_set.update(mapped_manufacturers)

        if selected_qpl_keys:
            mapped_keys = {(name or '').lower() for name in mapped_manufacturers}
            if not (mapped_keys & selected_qpl_keys):
                continue

        bom_map = data.get('bom_map') or {}
        if selected_bom_id_set and not (set(bom_map.keys()) & selected_bom_id_set):
            continue

        stock_quantity = float(data.get('stock_quantity') or 0)
        if exclude_in_stock and stock_quantity > 0:
            continue

        consolidated_rows.append({
            'base_part_number': base_part,
            'part_number': part_number_map.get(base_part) or base_part,
            'sales_frequency': int(data.get('sales_frequency') or 0),
            'sales_customer_count': int(data.get('sales_customer_count') or 0),
            'parts_list_frequency': int(data.get('parts_list_frequency') or 0),
            'parts_list_customer_count': int(data.get('parts_list_customer_count') or 0),
            'stock_quantity': stock_quantity,
            'qpl_mappings': qpl_mappings,
            'qpl_manufacturers': mapped_manufacturers,
            'bom_map': bom_map,
            'bom_count': len(bom_map),
        })

    consolidated_rows.sort(
        key=lambda row: (
            -len(row.get('qpl_manufacturers') or []),
            -(row.get('sales_frequency') or 0),
            -(row.get('parts_list_frequency') or 0),
            -(row.get('bom_count') or 0),
            row.get('part_number') or row.get('base_part_number'),
        )
    )

    qpl_manufacturer_options = sorted(qpl_manufacturer_option_set, key=lambda name: name.lower())

    return render_template(
        'stock_building.html',
        rows=consolidated_rows,
        customers=[dict(row) for row in customers],
        boms=[dict(row) for row in boms],
        bom_column_ids=all_bom_ids,
        bom_name_by_id=bom_name_by_id,
        qpl_manufacturer_options=qpl_manufacturer_options,
        selected_customer_ids=selected_customer_ids,
        selected_bom_ids=selected_bom_ids,
        selected_qpl_manufacturers=selected_qpl_manufacturers,
        exclude_in_stock=exclude_in_stock,
    )

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

@parts_bp.route('/parts/<base_part_number>/pieces_per_pound', methods=['POST'])
def update_pieces_per_pound(base_part_number):
    """Update the pieces_per_pound value for a part number."""
    data = request.json
    pieces_per_pound = data.get('pieces_per_pound')

    # Allow None/null to clear the value
    if pieces_per_pound is not None:
        try:
            pieces_per_pound = float(pieces_per_pound)
            if pieces_per_pound <= 0:
                return jsonify(success=False, message='Pieces per pound must be a positive number'), 400
        except (ValueError, TypeError):
            return jsonify(success=False, message='Invalid pieces per pound value'), 400

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                'UPDATE part_numbers SET pieces_per_pound = ? WHERE base_part_number = ?',
                (pieces_per_pound, base_part_number),
            )
        return jsonify(success=True, pieces_per_pound=pieces_per_pound)
    except Exception as e:
        logging.error(f'Error updating pieces_per_pound: {e}')
        return jsonify(success=False, message=str(e)), 500


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

    # Fetch new data sources
    parts_list_lines = get_parts_list_lines_by_part_number(base_part_number)
    supplier_quotes = get_supplier_quotes_by_part_number(base_part_number)
    bom_lines = get_bom_lines_by_part_number(base_part_number)
    excess_lines = get_excess_lines_by_part_number(base_part_number)
    manufacturer_approvals = get_manufacturer_approvals_by_part_number(base_part_number)

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
                           global_alternatives=global_alternatives,
                           parts_list_lines=parts_list_lines,
                           supplier_quotes=supplier_quotes,
                           bom_lines=bom_lines,
                           excess_lines=excess_lines,
                           manufacturer_approvals=manufacturer_approvals)

@parts_bp.route('/api/part_number_search', methods=['GET'])
def part_number_search():
    query = request.args.get('query', '').strip()
    base_part_number = create_base_part_number(query)  # Assuming you have this function to strip the part number

    search_query = '''
        SELECT
            pn.base_part_number,
            pn.part_number,
            pn.system_part_number,
            pn.stock,
            pc.category_name,
            COALESCE(pm.manufacturers, '') AS manufacturers,
            COALESCE(ma.approvals_count, 0) AS approvals_count
        FROM part_numbers pn
        LEFT JOIN part_categories pc ON pn.category_id = pc.category_id
        LEFT JOIN (
            SELECT
                pm.base_part_number,
                STRING_AGG(DISTINCT m.name, ', ' ORDER BY m.name) AS manufacturers
            FROM part_manufacturers pm
            JOIN manufacturers m ON m.id = pm.manufacturer_id
            GROUP BY pm.base_part_number
        ) pm ON pm.base_part_number = pn.base_part_number
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS approvals_count
            FROM manufacturer_approvals ma
            WHERE ma.airbus_material_base = pn.base_part_number
               OR ma.manufacturer_part_number_base = pn.base_part_number
        ) ma ON TRUE
        WHERE pn.base_part_number LIKE ? OR pn.part_number LIKE ?
        LIMIT 5
    '''
    results = db_execute(search_query, (f'%{base_part_number}%', f'%{query}%'), fetch='all') or []

    return jsonify([
        {
            'base_part_number': row['base_part_number'],
            'part_number': row['part_number'],
            'system_part_number': row.get('system_part_number'),
            'stock': row.get('stock'),
            'category_name': row.get('category_name'),
            'manufacturers': row.get('manufacturers', ''),
            'approvals_count': row.get('approvals_count', 0),
        }
        for row in results
    ])


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
            LEFT JOIN part_alt_groups g ON m.group_id = g.id
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
        base_part_numbers = _normalize_base_part_numbers(part_numbers)

        if not base_part_numbers:
            return jsonify(success=False, message="No valid part numbers provided"), 400

        if len(base_part_numbers) < 2:
            return jsonify(success=False, message="Need at least two different parts to create a group"), 400

        # Use add_global_alternative to handle all the logic
        # Start by linking the first part to all others
        primary = base_part_numbers[0]

        for alt in base_part_numbers[1:]:
            add_global_alternative(primary, alt)

        # Get the complete group to return
        all_alternatives = get_global_alternatives(primary)

        # Get group info
        group_info = db_execute("""
            SELECT m.group_id AS id, g.description
            FROM part_alt_group_members m
            LEFT JOIN part_alt_groups g ON g.id = m.group_id
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
    try:
        search_query = request.args.get('q', '').strip()
        view_mode = request.args.get('view', '').strip().lower()
        top_limit = request.args.get('limit', 25)
        min_size = request.args.get('min_size', 5)

        if view_mode == 'largest':
            groups = _get_largest_alt_groups(limit=top_limit, min_size=min_size)
        else:
            view_mode = 'search'
            groups = _get_alt_groups(search_query) if search_query else []

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Parts', url_for('parts.parts')),
            ('Alternative Groups', None)
        ]
        return render_template(
            'alt_groups.html',
            breadcrumbs=breadcrumbs,
            groups=groups,
            search_query=search_query,
            view_mode=view_mode,
            top_limit=int(top_limit) if str(top_limit).isdigit() else 25,
            min_size=int(min_size) if str(min_size).isdigit() else 5,
        )

    except Exception as e:
        logging.error(f"ERROR in alt_groups route: {e}")
        logging.error(f"Exception type: {type(e)}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        flash('Unable to load alternative groups.', 'danger')
        return redirect(url_for('parts.parts'))


@parts_bp.route('/alt_groups/<int:group_id>/remove', methods=['POST'])
def remove_alt_group_members(group_id):
    selected_parts = _normalize_base_part_numbers(request.form.getlist('selected_part_numbers'))
    redirect_args = _alt_groups_redirect_args(request.form)

    if not selected_parts:
        flash('Select at least one part to remove from the group.', 'warning')
        return redirect(url_for('parts.alt_groups', **redirect_args))

    try:
        with db_cursor(commit=True) as cur:
            current_members = _fetch_alt_group_members(cur, group_id)
            current_member_set = set(current_members)
            removable_parts = [part for part in selected_parts if part in current_member_set]

            if not removable_parts:
                flash('None of the selected parts are still in that group.', 'warning')
                return redirect(url_for('parts.alt_groups', **redirect_args))

            placeholders = ', '.join(['?'] * len(removable_parts))
            _execute_with_cursor(
                cur,
                f'''
                DELETE FROM part_alt_group_members
                WHERE group_id = ?
                  AND base_part_number IN ({placeholders})
                ''',
                [group_id, *removable_parts],
            )
            remaining_members = _cleanup_alt_group_if_needed(cur, group_id)

        if remaining_members:
            flash(
                f"Removed {len(removable_parts)} part(s) from group {group_id}. "
                f"{len(remaining_members)} part(s) remain grouped.",
                'success',
            )
        else:
            flash(
                f"Removed {len(removable_parts)} part(s). Group {group_id} was dissolved because fewer than two parts remained.",
                'success',
            )
    except Exception as e:
        logging.error(f'Error removing members from alt group {group_id}: {e}')
        flash(f'Could not remove the selected parts: {e}', 'danger')

    return redirect(url_for('parts.alt_groups', **redirect_args))


@parts_bp.route('/alt_groups/<int:group_id>/split', methods=['POST'])
def split_alt_group(group_id):
    selected_parts = _normalize_base_part_numbers(request.form.getlist('selected_part_numbers'))
    redirect_args = _alt_groups_redirect_args(request.form)

    if len(selected_parts) < 2:
        flash('Select at least two parts to create a new group.', 'warning')
        return redirect(url_for('parts.alt_groups', **redirect_args))

    try:
        with db_cursor(commit=True) as cur:
            current_members = _fetch_alt_group_members(cur, group_id)
            current_member_set = set(current_members)
            split_members = [part for part in selected_parts if part in current_member_set]

            if len(split_members) < 2:
                flash('At least two selected parts must belong to the same current group.', 'warning')
                return redirect(url_for('parts.alt_groups', **redirect_args))

            if len(split_members) == len(current_members):
                flash('Select only part of the group when splitting. Creating a new group for every member would not change anything.', 'warning')
                return redirect(url_for('parts.alt_groups', **redirect_args))

            description = f"Split from group {group_id}: {' / '.join(split_members[:3])}"
            insert_query = _with_returning_clause(
                'INSERT INTO part_alt_groups (description) VALUES (?)',
                returning='id',
            )
            _execute_with_cursor(cur, insert_query, (description,))
            new_group_id = _last_inserted_id(cur)

            for part_number in split_members:
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO part_alt_group_members (group_id, base_part_number)
                    VALUES (?, ?)
                    ON CONFLICT DO NOTHING
                    ''',
                    (new_group_id, part_number),
                )

            placeholders = ', '.join(['?'] * len(split_members))
            _execute_with_cursor(
                cur,
                f'''
                DELETE FROM part_alt_group_members
                WHERE group_id = ?
                  AND base_part_number IN ({placeholders})
                ''',
                [group_id, *split_members],
            )

            _cleanup_alt_group_if_needed(cur, group_id)

        flash(
            f"Created new group {new_group_id} with {len(split_members)} selected part(s) from group {group_id}.",
            'success',
        )
    except Exception as e:
        logging.error(f'Error splitting alt group {group_id}: {e}')
        flash(f'Could not split the selected parts into a new group: {e}', 'danger')

    return redirect(url_for('parts.alt_groups', **redirect_args))
