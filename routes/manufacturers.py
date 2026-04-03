import os
import re

from flask import jsonify, Blueprint, render_template, request, redirect, url_for, flash
from db import db_cursor, execute as db_execute
from models import (
    get_all_manufacturers,
    create_base_part_number,
    get_associated_manufacturers,
    insert_manufacturer,
    delete_manufacturer,
)

manufacturers_bp = Blueprint('manufacturers', __name__)


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _normalize_qpl_manufacturer_name(name):
    if not name:
        return ''
    value = re.sub(r'[^a-z0-9 ]+', ' ', str(name).lower())
    tokens = [
        token for token in value.split()
        if token not in {
            'ltd', 'limited', 'inc', 'llc', 'plc', 'corp', 'corporation',
            'co', 'company', 'gmbh', 'sa', 'bv', 'ag', 'srl', 'pte', 'group'
        }
    ]
    return ''.join(tokens)


def _part_number_prefix_sql():
    if _using_postgres():
        return (
            "UPPER(SUBSTRING(TRIM(COALESCE(NULLIF(sql.quoted_part_number, ''), "
            "NULLIF(pll.customer_part_number, ''), NULLIF(pll.base_part_number, ''))) FROM 1 FOR ?))"
        )
    return (
        "UPPER(SUBSTR(TRIM(COALESCE(NULLIF(sql.quoted_part_number, ''), "
        "NULLIF(pll.customer_part_number, ''), NULLIF(pll.base_part_number, ''))), 1, ?))"
    )


def _qpl_mapping_table_exists():
    row = db_execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = ?
        LIMIT 1
        """,
        ('qpl_manufacturer_supplier_mappings',),
        fetch='one',
    )
    return row is not None


def _load_qpl_manufacturer_rows(search_term='', limit=10, mapped_only=True):
    limit = min(max(int(limit or 10), 1), 100)
    search_term = (search_term or '').strip()

    mapping_rows = []
    if _qpl_mapping_table_exists():
        mapping_rows = db_execute(
            """
            SELECT manufacturer_name_normalized, supplier_id
            FROM qpl_manufacturer_supplier_mappings
            """,
            fetch='all',
        ) or []

    mapping_by_normalized_name = {
        (row.get('manufacturer_name_normalized') or '').strip(): row.get('supplier_id')
        for row in mapping_rows
        if (row.get('manufacturer_name_normalized') or '').strip()
    }

    suppliers_lookup = {
        row['id']: row
        for row in (db_execute('SELECT id, name FROM suppliers ORDER BY name', fetch='all') or [])
    }

    qpl_rows = db_execute(
        """
        SELECT
            manufacturer_name,
            COUNT(*) AS approvals_count,
            COUNT(DISTINCT approval_list_type) AS list_type_count
        FROM manufacturer_approvals
        WHERE manufacturer_name IS NOT NULL
          AND TRIM(manufacturer_name) <> ''
        GROUP BY manufacturer_name
        ORDER BY COUNT(*) DESC, manufacturer_name
        """,
        fetch='all',
    ) or []

    filtered_results = []
    for row in qpl_rows:
        qpl_name = (row.get('manufacturer_name') or '').strip()
        if not qpl_name:
            continue

        normalized_name = _normalize_qpl_manufacturer_name(qpl_name)
        mapped_supplier_id = mapping_by_normalized_name.get(normalized_name)
        mapped_supplier = suppliers_lookup.get(mapped_supplier_id) if mapped_supplier_id else None

        if mapped_only and not mapped_supplier_id:
            continue
        if search_term and search_term.lower() not in qpl_name.lower():
            continue

        filtered_results.append({
            'qpl_name': qpl_name,
            'qpl_name_normalized': normalized_name,
            'approvals_count': row.get('approvals_count', 0),
            'list_type_count': row.get('list_type_count', 0),
            'mapped_supplier_id': mapped_supplier_id,
            'mapped_supplier_name': mapped_supplier.get('name') if mapped_supplier else None,
        })

    filtered_results.sort(key=lambda row: (-int(row.get('approvals_count', 0) or 0), row.get('qpl_name', '').lower()))
    return filtered_results[:limit], len(filtered_results)


def _load_qpl_mapped_suppliers():
    if not _qpl_mapping_table_exists():
        return []

    return db_execute(
        """
        SELECT DISTINCT s.id, s.name
        FROM qpl_manufacturer_supplier_mappings m
        JOIN suppliers s ON s.id = m.supplier_id
        ORDER BY s.name
        """,
        fetch='all',
    ) or []


def _load_manufacturers_page(search_term='', limit=10):
    limit = min(max(int(limit or 10), 1), 100)
    search_term = (search_term or '').strip()
    params = []
    where_clause = ''

    if search_term:
        where_clause = 'WHERE LOWER(m.name) LIKE LOWER(?)'
        params.append(f'%{search_term}%')

    rows = db_execute(
        f"""
        SELECT m.id, m.name, m.merged_into, m2.name AS merged_into_name
        FROM manufacturers m
        LEFT JOIN manufacturers m2 ON m.merged_into = m2.id
        {where_clause}
        ORDER BY m.name
        LIMIT ?
        """,
        tuple(params + [limit]),
        fetch='all',
    ) or []

    total_row = db_execute(
        f"""
        SELECT COUNT(*) AS total_count
        FROM manufacturers m
        {where_clause}
        """,
        tuple(params),
        fetch='one',
    ) or {}

    return [dict(row) for row in rows], int(total_row.get('total_count', 0) or 0)


@manufacturers_bp.route('', methods=['GET', 'POST'])
@manufacturers_bp.route('/', methods=['GET', 'POST'])
@manufacturers_bp.route('/manufacturers', methods=['GET', 'POST'])
def manufacturers():
    if request.method == 'POST':
        name = request.form['name']
        insert_manufacturer(name)
        flash('Manufacturer added successfully!', 'success')
        return redirect(url_for('manufacturers.manufacturers'))

    search = (request.args.get('search') or '').strip()
    limit = request.args.get('limit', type=int) or 10
    qpl_search = (request.args.get('qpl_search') or '').strip()
    qpl_limit = request.args.get('qpl_limit', type=int) or 10
    qpl_mapped_only = str(request.args.get('qpl_mapped_only', '1')).lower() not in ('0', 'false', 'off', '')
    manufacturers, manufacturer_total = _load_manufacturers_page(search, limit)
    active_manufacturers = get_all_manufacturers(include_merged=False)
    suppliers = db_execute('SELECT id, name FROM suppliers ORDER BY name', fetch='all') or []
    qpl_manufacturers, qpl_manufacturer_total = _load_qpl_manufacturer_rows(qpl_search, qpl_limit, qpl_mapped_only)
    qpl_mapped_suppliers = _load_qpl_mapped_suppliers()
    return render_template(
        'manufacturers.html',
        manufacturers=manufacturers,
        manufacturer_total=manufacturer_total,
        manufacturer_search=search,
        manufacturer_limit=min(max(limit, 1), 100),
        active_manufacturers=active_manufacturers,
        suppliers=suppliers,
        qpl_mapped_suppliers=qpl_mapped_suppliers,
        qpl_manufacturers=qpl_manufacturers,
        qpl_manufacturer_total=qpl_manufacturer_total,
        qpl_search=qpl_search,
        qpl_limit=min(max(qpl_limit, 1), 100),
        qpl_mapped_only=qpl_mapped_only,
        has_qpl_mapping_table=_qpl_mapping_table_exists(),
    )


@manufacturers_bp.route('/qpl-mappings/prefix-report', methods=['GET'])
@manufacturers_bp.route('/manufacturers/qpl-mappings/prefix-report', methods=['GET'])
def qpl_mapped_supplier_prefix_report():
    if not _qpl_mapping_table_exists():
        return jsonify(success=False, message='QPL mapping table is missing.'), 400

    prefix_length = request.args.get('prefix_length', type=int) or 6
    prefix_length = min(max(prefix_length, 1), 25)

    min_occurrences = request.args.get('min_occurrences', type=int) or 2
    min_occurrences = max(min_occurrences, 1)

    limit = request.args.get('limit', type=int) or 100
    limit = min(max(limit, 1), 500)

    supplier_id = request.args.get('supplier_id', type=int)

    prefix_sql = _part_number_prefix_sql()
    supplier_filter_sql = ''
    params = [prefix_length]

    if supplier_id:
        supplier_filter_sql = 'AND sq.supplier_id = ?'
        params.append(supplier_id)

    params.extend([prefix_length, min_occurrences, limit])

    rows = db_execute(
        f"""
        WITH mapped_suppliers AS (
            SELECT DISTINCT supplier_id
            FROM qpl_manufacturer_supplier_mappings
        ),
        source_lines AS (
            SELECT
                sq.id AS supplier_quote_id,
                sq.supplier_id,
                {prefix_sql} AS pn_prefix
            FROM parts_list_supplier_quote_lines sql
            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
            LEFT JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
            JOIN mapped_suppliers ms ON ms.supplier_id = sq.supplier_id
            WHERE TRIM(COALESCE(NULLIF(sql.quoted_part_number, ''), NULLIF(pll.customer_part_number, ''), NULLIF(pll.base_part_number, ''))) <> ''
              {supplier_filter_sql}
        )
        SELECT
            pn_prefix AS prefix,
            COUNT(*) AS line_count,
            COUNT(DISTINCT supplier_quote_id) AS quote_count,
            COUNT(DISTINCT supplier_id) AS supplier_count
        FROM source_lines
        WHERE LENGTH(pn_prefix) = ?
        GROUP BY pn_prefix
        HAVING COUNT(*) >= ?
        ORDER BY line_count DESC, prefix ASC
        LIMIT ?
        """,
        tuple(params),
        fetch='all',
    ) or []

    return jsonify(
        success=True,
        prefix_length=prefix_length,
        min_occurrences=min_occurrences,
        supplier_id=supplier_id,
        rows=rows,
    )


@manufacturers_bp.route('/<int:manufacturer_id>/delete', methods=['POST'])
@manufacturers_bp.route('/manufacturers/<int:manufacturer_id>/delete', methods=['POST'])
def delete_manufacturer_route(manufacturer_id):
    delete_manufacturer(manufacturer_id)
    flash('Manufacturer deleted successfully!', 'success')
    return redirect(url_for('manufacturers.manufacturers'))

@manufacturers_bp.route('/api/get_manufacturers', methods=['GET'])
def get_manufacturers():
    part_number = request.args.get('part_number')
    base_part_number = create_base_part_number(part_number)

    part_exists = db_execute(
        'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
        (base_part_number,),
        fetch='one',
    )

    if not part_exists:
        return jsonify(manufacturers=[]), 200

    manufacturers = get_associated_manufacturers(base_part_number)
    return jsonify(manufacturers=manufacturers), 200

@manufacturers_bp.route('/api/get_all_manufacturers', methods=['GET'])
def fetch_all_manufacturers():
    # Default to only active manufacturers for API calls
    include_merged = request.args.get('include_merged', '').lower() == 'true'
    manufacturers = get_all_manufacturers(include_merged=include_merged)
    return jsonify(manufacturers)


@manufacturers_bp.route('/merge', methods=['POST'])
@manufacturers_bp.route('/manufacturers/merge', methods=['POST'])
def merge_manufacturers():
    source_id = request.form.get('source_id', type=int)
    target_id = request.form.get('target_id', type=int)

    if not source_id or not target_id:
        flash('Both source and target manufacturers must be specified', 'error')
        return redirect(url_for('manufacturers.manufacturers'))

    if source_id == target_id:
        flash('Cannot merge a manufacturer into itself', 'error')
        return redirect(url_for('manufacturers.manufacturers'))

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                'UPDATE manufacturers SET merged_into = ? WHERE id = ?',
                (target_id, source_id),
            )
            _execute_with_cursor(
                cur,
                'UPDATE rfq_lines SET manufacturer_id = ? WHERE manufacturer_id = ?',
                (target_id, source_id),
            )

        flash('Manufacturers merged successfully!', 'success')
    except Exception as exc:
        flash(f'Error merging manufacturers: {str(exc)}', 'error')

    return redirect(url_for('manufacturers.manufacturers'))


@manufacturers_bp.route('/qpl-mappings', methods=['POST'])
@manufacturers_bp.route('/manufacturers/qpl-mappings', methods=['POST'])
def upsert_qpl_mapping():
    qpl_name = (request.form.get('qpl_name') or '').strip()
    qpl_name_normalized = _normalize_qpl_manufacturer_name(qpl_name)
    supplier_id = request.form.get('supplier_id', type=int)

    if not _qpl_mapping_table_exists():
        flash('QPL mapping table is missing. Run migration 20260307_add_qpl_manufacturer_supplier_mappings.sql.', 'warning')
        return redirect(url_for('manufacturers.manufacturers'))

    if not qpl_name_normalized:
        flash('QPL manufacturer name is required.', 'error')
        return redirect(url_for('manufacturers.manufacturers'))

    try:
        with db_cursor(commit=True) as cur:
            if supplier_id:
                _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO qpl_manufacturer_supplier_mappings (
                        manufacturer_name,
                        manufacturer_name_normalized,
                        supplier_id
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT (manufacturer_name_normalized)
                    DO UPDATE SET
                        manufacturer_name = EXCLUDED.manufacturer_name,
                        supplier_id = EXCLUDED.supplier_id,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (qpl_name, qpl_name_normalized, supplier_id),
                )
                flash('QPL manufacturer mapping updated.', 'success')
            else:
                _execute_with_cursor(
                    cur,
                    'DELETE FROM qpl_manufacturer_supplier_mappings WHERE manufacturer_name_normalized = ?',
                    (qpl_name_normalized,),
                )
                flash('QPL manufacturer mapping removed.', 'success')
    except Exception as exc:
        flash(f'Unable to save QPL mapping: {exc}', 'error')

    return redirect(url_for('manufacturers.manufacturers'))


@manufacturers_bp.route('/import-qpl-manufacturers', methods=['POST'])
@manufacturers_bp.route('/manufacturers/import-qpl-manufacturers', methods=['POST'])
def import_qpl_manufacturers():
    qpl_rows = db_execute(
        """
        SELECT DISTINCT TRIM(manufacturer_name) AS manufacturer_name
        FROM manufacturer_approvals
        WHERE manufacturer_name IS NOT NULL
          AND TRIM(manufacturer_name) <> ''
        ORDER BY TRIM(manufacturer_name)
        """,
        fetch='all',
    ) or []

    if not qpl_rows:
        flash('No QPL manufacturers found to import.', 'warning')
        return redirect(url_for('manufacturers.manufacturers'))

    existing_rows = db_execute(
        """
        SELECT LOWER(TRIM(name)) AS manufacturer_key
        FROM manufacturers
        WHERE name IS NOT NULL
          AND TRIM(name) <> ''
        """,
        fetch='all',
    ) or []
    existing_keys = {
        (row.get('manufacturer_key') or '').strip()
        for row in existing_rows
        if (row.get('manufacturer_key') or '').strip()
    }

    names_to_insert = []
    seen_new = set()
    for row in qpl_rows:
        manufacturer_name = (row.get('manufacturer_name') or '').strip()
        manufacturer_key = manufacturer_name.lower()
        if not manufacturer_name or manufacturer_key in existing_keys or manufacturer_key in seen_new:
            continue
        names_to_insert.append(manufacturer_name)
        seen_new.add(manufacturer_key)

    inserted_count = 0
    if names_to_insert:
        with db_cursor(commit=True) as cur:
            for manufacturer_name in names_to_insert:
                _execute_with_cursor(
                    cur,
                    "INSERT INTO manufacturers (name) VALUES (?)",
                    (manufacturer_name,),
                )
                inserted_count += 1

    skipped_count = len(qpl_rows) - inserted_count
    flash(
        f'Imported {inserted_count} QPL manufacturer(s) into the manufacturers table. '
        f'Skipped {skipped_count} existing name(s).',
        'success' if inserted_count else 'info',
    )
    return redirect(url_for('manufacturers.manufacturers'))


@manufacturers_bp.route('/get_or_create', methods=['POST'])
def get_or_create_manufacturer():
    """Get existing manufacturer or create new one. Handles empty/null values."""
    name = request.json.get('name')
    if not name or name.strip() == '':
        return jsonify({
            'id': None,
            'name': '',
            'was_merged': False,
            'original_name': ''
        })

    existing = db_execute('''
        SELECT m1.id, m1.name, m1.merged_into,
               m2.id as canonical_id, m2.name as canonical_name
        FROM manufacturers m1
        LEFT JOIN manufacturers m2 ON m1.merged_into = m2.id
        WHERE LOWER(m1.name) = LOWER(?)
    ''', (name,), fetch='one')

    if existing:
        if existing['merged_into']:
            return jsonify({
                'id': existing['canonical_id'],
                'name': existing['canonical_name'],
                'was_merged': True,
                'original_name': name
            })
        return jsonify({
            'id': existing['id'],
            'name': existing['name'],
            'was_merged': False,
            'original_name': name
        })

    with db_cursor(commit=True) as cur:
        _execute_with_cursor(
            cur,
            '''
            INSERT INTO manufacturers (name) VALUES (?)
            RETURNING id, name
            ''',
            (name,),
        )
        new_manufacturer = cur.fetchone()

    return jsonify({
        'id': new_manufacturer['id'],
        'name': new_manufacturer['name'],
        'was_merged': False,
        'original_name': name
    })


@manufacturers_bp.route('/lookup', methods=['GET'])
def lookup_manufacturers():
    """API endpoint for manufacturer name autocomplete."""
    search_term = request.args.get('term', '')
    if not search_term:
        return jsonify([])

    rows = db_execute('''
        WITH canonical_names AS (
            SELECT 
                COALESCE(m2.id, m1.id) as final_id,
                COALESCE(m2.name, m1.name) as final_name
            FROM manufacturers m1
            LEFT JOIN manufacturers m2 ON m1.merged_into = m2.id
            WHERE m1.name LIKE ?
        )
        SELECT DISTINCT final_id as id, final_name as name
        FROM canonical_names
        ORDER BY final_name
    ''', (f'%{search_term}%',), fetch='all') or []

    results = [{
        'value': row['name'],
        'label': row['name'],
        'id': row['id'],
        'name': row['name'],
    } for row in rows]

    return jsonify(results)
