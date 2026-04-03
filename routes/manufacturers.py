import os
import re
from urllib.parse import urlparse

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


def _tokenize_qpl_manufacturer_name(name):
    if not name:
        return []
    value = re.sub(r'[^a-z0-9 ]+', ' ', str(name).lower())
    stopwords = {
        'ltd', 'limited', 'inc', 'llc', 'plc', 'corp', 'corporation',
        'co', 'company', 'gmbh', 'sa', 'bv', 'ag', 'srl', 'pte', 'group'
    }
    return [token for token in value.split() if token and token not in stopwords]


def _normalize_prefix_report_manufacturer_name(name):
    return ' '.join((str(name or '').strip().lower()).split())


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


def _approval_part_number_prefix_sql():
    if _using_postgres():
        return (
            "UPPER(SUBSTRING(TRIM(COALESCE(NULLIF(ma.manufacturer_part_number_base, ''), "
            "NULLIF(ma.airbus_material_base, ''))) FROM 1 FOR ?))"
        )
    return (
        "UPPER(SUBSTR(TRIM(COALESCE(NULLIF(ma.manufacturer_part_number_base, ''), "
        "NULLIF(ma.airbus_material_base, ''))), 1, ?))"
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


def _qpl_prefix_instruction_table_exists():
    row = db_execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = ?
        LIMIT 1
        """,
        ('qpl_supplier_prefix_instructions',),
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

    suppliers = db_execute('SELECT id, name FROM suppliers ORDER BY name', fetch='all') or []
    suppliers_lookup = {row['id']: row for row in suppliers}
    supplier_candidates = []
    for supplier in suppliers:
        supplier_name = (supplier.get('name') or '').strip()
        normalized_name = _normalize_qpl_manufacturer_name(supplier_name)
        token_set = set(_tokenize_qpl_manufacturer_name(supplier_name))
        if not supplier_name or not normalized_name:
            continue
        supplier_candidates.append({
            'id': supplier.get('id'),
            'name': supplier_name,
            'normalized_name': normalized_name,
            'token_set': token_set,
            'token_count': len(token_set),
            'normalized_length': len(normalized_name),
        })

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
        qpl_tokens = set(_tokenize_qpl_manufacturer_name(qpl_name))
        mapped_supplier_id = mapping_by_normalized_name.get(normalized_name)
        mapped_supplier = suppliers_lookup.get(mapped_supplier_id) if mapped_supplier_id else None
        suggested_supplier = None
        suggested_score = 0

        if normalized_name:
            for supplier in supplier_candidates:
                score = 0
                overlap = len(qpl_tokens & supplier['token_set']) if qpl_tokens and supplier['token_set'] else 0

                if normalized_name == supplier['normalized_name']:
                    score = 1000
                elif normalized_name.startswith(supplier['normalized_name']):
                    score = 800 + supplier['normalized_length']
                elif supplier['normalized_name'] in normalized_name:
                    score = 700 + supplier['normalized_length']
                elif overlap:
                    coverage = overlap / max(supplier['token_count'], 1)
                    score = int(coverage * 100) + overlap * 10
                    if qpl_tokens and overlap == len(qpl_tokens):
                        score += 40

                if score > suggested_score:
                    suggested_score = score
                    suggested_supplier = supplier

        suggested_supplier_id = None
        suggested_supplier_name = None
        if suggested_supplier and suggested_score >= 80 and suggested_supplier.get('id') != mapped_supplier_id:
            suggested_supplier_id = suggested_supplier.get('id')
            suggested_supplier_name = suggested_supplier.get('name')

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
            'suggested_supplier_id': suggested_supplier_id,
            'suggested_supplier_name': suggested_supplier_name,
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


def _redirect_to_manufacturers_return_target():
    return_to = (request.form.get('return_to') or request.args.get('return_to') or '').strip()
    if not return_to:
        return redirect(url_for('manufacturers.manufacturers'))

    parsed = urlparse(return_to)
    if parsed.scheme or parsed.netloc:
        return redirect(url_for('manufacturers.manufacturers'))
    if not return_to.startswith('/manufacturers'):
        return redirect(url_for('manufacturers.manufacturers'))
    return redirect(return_to)


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
    qpl_mapped_only = str(request.args.get('qpl_mapped_only', '0')).lower() not in ('0', 'false', 'off', '')
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
        has_qpl_prefix_instruction_table=_qpl_prefix_instruction_table_exists(),
    )


@manufacturers_bp.route('/qpl-mappings/prefix-report', methods=['GET'])
@manufacturers_bp.route('/manufacturers/qpl-mappings/prefix-report', methods=['GET'])
def qpl_mapped_supplier_prefix_report():
    if not _qpl_mapping_table_exists():
        return jsonify(success=False, message='QPL mapping table is missing.'), 400

    has_instruction_table = _qpl_prefix_instruction_table_exists()
    prefix_length = request.args.get('prefix_length', type=int) or 6
    prefix_length = min(max(prefix_length, 1), 25)

    min_occurrences = request.args.get('min_occurrences', type=int) or 2
    min_occurrences = max(min_occurrences, 1)

    limit = request.args.get('limit', type=int)
    if limit is not None:
        limit = min(max(limit, 1), 5000)

    supplier_id = request.args.get('supplier_id', type=int)

    prefix_sql = _approval_part_number_prefix_sql()
    instruction_join_sql = ''
    supplier_filter_sql = ''
    params = [prefix_length]

    if supplier_id:
        supplier_filter_sql = 'AND ms.supplier_id = ?'
        params.append(supplier_id)

    params.extend([prefix_length, min_occurrences])
    if has_instruction_table:
        instruction_join_sql = """
        LEFT JOIN qpl_supplier_prefix_instructions instr
            ON instr.supplier_id = grouped.supplier_id
           AND instr.manufacturer_name_normalized = grouped.manufacturer_name_normalized
           AND instr.prefix = grouped.prefix
           AND instr.prefix_length = ?
        """
        params.append(prefix_length)

    final_limit_sql = ''
    if limit is not None:
        final_limit_sql = 'LIMIT ?'
        params.append(limit)

    rows = db_execute(
        f"""
        WITH mapped_suppliers AS (
            SELECT DISTINCT
                supplier_id,
                manufacturer_name_normalized
            FROM qpl_manufacturer_supplier_mappings
        ),
        source_parts AS (
            SELECT
                ms.supplier_id,
                s.name AS supplier_name,
                TRIM(ma.manufacturer_name) AS manufacturer_name,
                LOWER(TRIM(ma.manufacturer_name)) AS manufacturer_name_normalized,
                {prefix_sql} AS pn_prefix,
                COALESCE(NULLIF(TRIM(ma.manufacturer_part_number_base), ''), NULLIF(TRIM(ma.airbus_material_base), '')) AS normalized_part_number
            FROM manufacturer_approvals ma
            JOIN mapped_suppliers ms
                ON ms.manufacturer_name_normalized = LOWER(TRIM(ma.manufacturer_name))
            JOIN suppliers s ON s.id = ms.supplier_id
            WHERE TRIM(COALESCE(ma.manufacturer_name, '')) <> ''
              AND TRIM(COALESCE(NULLIF(ma.manufacturer_part_number_base, ''), NULLIF(ma.airbus_material_base, ''))) <> ''
              {supplier_filter_sql}
        ),
        grouped AS (
            SELECT
                supplier_id,
                supplier_name,
                manufacturer_name,
                manufacturer_name_normalized,
                pn_prefix AS prefix,
                COUNT(*) AS approval_count,
                COUNT(DISTINCT normalized_part_number) AS part_count
            FROM source_parts
            WHERE LENGTH(pn_prefix) = ?
            GROUP BY
                supplier_id,
                supplier_name,
                manufacturer_name,
                manufacturer_name_normalized,
                pn_prefix
            HAVING COUNT(*) >= ?
        )
        SELECT
            grouped.supplier_id,
            grouped.supplier_name,
            grouped.manufacturer_name,
            grouped.prefix,
            grouped.approval_count,
            grouped.part_count
            {", COALESCE(instr.instruction_text, '') AS instruction_text" if has_instruction_table else ", '' AS instruction_text"}
        FROM grouped
        {instruction_join_sql}
        ORDER BY grouped.part_count DESC, grouped.approval_count DESC, grouped.supplier_name ASC, grouped.manufacturer_name ASC, grouped.prefix ASC
        {final_limit_sql}
        """,
        tuple(params),
        fetch='all',
    ) or []

    return jsonify(
        success=True,
        has_instruction_table=has_instruction_table,
        prefix_length=prefix_length,
        min_occurrences=min_occurrences,
        supplier_id=supplier_id,
        rows=rows,
    )


@manufacturers_bp.route('/qpl-mappings/prefix-instructions', methods=['POST'])
@manufacturers_bp.route('/manufacturers/qpl-mappings/prefix-instructions', methods=['POST'])
def upsert_qpl_prefix_instruction():
    if not _qpl_prefix_instruction_table_exists():
        return jsonify(
            success=False,
            message='Prefix instruction table is missing. Run migration 20260403_add_qpl_supplier_prefix_instructions.sql.',
        ), 400

    payload = request.get_json(silent=True) or {}
    supplier_id = payload.get('supplier_id')
    prefix = (payload.get('prefix') or '').strip().upper()
    manufacturer_name = (payload.get('manufacturer_name') or '').strip()
    instruction_text = (payload.get('instruction_text') or '').strip()

    try:
        supplier_id = int(supplier_id)
    except (TypeError, ValueError):
        supplier_id = None

    prefix_length = payload.get('prefix_length')
    try:
        prefix_length = min(max(int(prefix_length), 1), 25)
    except (TypeError, ValueError):
        prefix_length = None

    manufacturer_name_normalized = _normalize_prefix_report_manufacturer_name(manufacturer_name)

    if not supplier_id or not prefix or not prefix_length or not manufacturer_name_normalized:
        return jsonify(success=False, message='Supplier, manufacturer, prefix, and prefix length are required.'), 400

    if len(prefix) != prefix_length:
        return jsonify(success=False, message='Prefix length does not match the supplied prefix.'), 400

    try:
        with db_cursor(commit=True) as cur:
            if instruction_text:
                _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO qpl_supplier_prefix_instructions (
                        supplier_id,
                        manufacturer_name,
                        manufacturer_name_normalized,
                        prefix,
                        prefix_length,
                        instruction_text
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (supplier_id, manufacturer_name_normalized, prefix, prefix_length)
                    DO UPDATE SET
                        manufacturer_name = EXCLUDED.manufacturer_name,
                        instruction_text = EXCLUDED.instruction_text,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        supplier_id,
                        manufacturer_name,
                        manufacturer_name_normalized,
                        prefix,
                        prefix_length,
                        instruction_text,
                    ),
                )
            else:
                _execute_with_cursor(
                    cur,
                    """
                    DELETE FROM qpl_supplier_prefix_instructions
                    WHERE supplier_id = ?
                      AND manufacturer_name_normalized = ?
                      AND prefix = ?
                      AND prefix_length = ?
                    """,
                    (supplier_id, manufacturer_name_normalized, prefix, prefix_length),
                )
    except Exception as exc:
        return jsonify(success=False, message=f'Unable to save instruction: {exc}'), 500

    return jsonify(
        success=True,
        supplier_id=supplier_id,
        manufacturer_name=manufacturer_name,
        prefix=prefix,
        prefix_length=prefix_length,
        instruction_text=instruction_text,
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

    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')

    def _json_response(success, message, status_code=200):
        if wants_json:
            return jsonify(
                success=success,
                message=message,
                qpl_name=qpl_name,
                supplier_id=supplier_id,
            ), status_code
        flash(message, 'success' if success else 'error')
        return _redirect_to_manufacturers_return_target()

    if not _qpl_mapping_table_exists():
        message = 'QPL mapping table is missing. Run migration 20260307_add_qpl_manufacturer_supplier_mappings.sql.'
        if wants_json:
            return jsonify(success=False, message=message), 400
        flash(message, 'warning')
        return _redirect_to_manufacturers_return_target()

    if not qpl_name_normalized:
        return _json_response(False, 'QPL manufacturer name is required.', 400)

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
                message = 'QPL manufacturer mapping updated.'
            else:
                _execute_with_cursor(
                    cur,
                    'DELETE FROM qpl_manufacturer_supplier_mappings WHERE manufacturer_name_normalized = ?',
                    (qpl_name_normalized,),
                )
                message = 'QPL manufacturer mapping removed.'
    except Exception as exc:
        return _json_response(False, f'Unable to save QPL mapping: {exc}', 500)

    return _json_response(True, message)


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
