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
    value = (name or '').strip().lower()
    value = re.sub(r'\s+', ' ', value)
    return value


def _qpl_mapping_table_exists():
    row = db_execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = ?
        LIMIT 1
        """,
        ('qpl_manufacturer_mappings',),
        fetch='one',
    )
    return row is not None


def _load_qpl_manufacturer_rows():
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

    qpl_mapping_rows = []
    if _qpl_mapping_table_exists():
        qpl_mapping_rows = db_execute(
            """
            SELECT qpl_manufacturer_name_normalized, manufacturer_id
            FROM qpl_manufacturer_mappings
            """,
            fetch='all',
        ) or []

    mapping_by_normalized_name = {
        (row.get('qpl_manufacturer_name_normalized') or '').strip(): row.get('manufacturer_id')
        for row in qpl_mapping_rows
        if (row.get('qpl_manufacturer_name_normalized') or '').strip()
    }

    manufacturers_lookup = {
        row['id']: row
        for row in (get_all_manufacturers(include_merged=False) or [])
    }

    results = []
    for row in qpl_rows:
        qpl_name = (row.get('manufacturer_name') or '').strip()
        if not qpl_name:
            continue
        normalized_name = _normalize_qpl_manufacturer_name(qpl_name)
        mapped_manufacturer_id = mapping_by_normalized_name.get(normalized_name)
        mapped_manufacturer = manufacturers_lookup.get(mapped_manufacturer_id) if mapped_manufacturer_id else None
        results.append({
            'qpl_name': qpl_name,
            'qpl_name_normalized': normalized_name,
            'approvals_count': row.get('approvals_count', 0),
            'list_type_count': row.get('list_type_count', 0),
            'mapped_manufacturer_id': mapped_manufacturer_id,
            'mapped_manufacturer_name': mapped_manufacturer.get('name') if mapped_manufacturer else None,
        })

    return results


@manufacturers_bp.route('/manufacturers', methods=['GET', 'POST'])
def manufacturers():
    if request.method == 'POST':
        name = request.form['name']
        insert_manufacturer(name)
        flash('Manufacturer added successfully!', 'success')
        return redirect(url_for('manufacturers.manufacturers'))

    # Use include_merged=True to show all manufacturers including merged ones
    manufacturers = get_all_manufacturers(include_merged=True)
    active_manufacturers = [row for row in manufacturers if not row.get('merged_into')]
    qpl_manufacturers = _load_qpl_manufacturer_rows()
    return render_template(
        'manufacturers.html',
        manufacturers=manufacturers,
        active_manufacturers=active_manufacturers,
        qpl_manufacturers=qpl_manufacturers,
        has_qpl_mapping_table=_qpl_mapping_table_exists(),
    )

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


@manufacturers_bp.route('/manufacturers/qpl-mappings', methods=['POST'])
def upsert_qpl_mapping():
    qpl_name = (request.form.get('qpl_name') or '').strip()
    qpl_name_normalized = _normalize_qpl_manufacturer_name(qpl_name)
    manufacturer_id = request.form.get('manufacturer_id', type=int)

    if not _qpl_mapping_table_exists():
        flash('QPL mapping table is missing. Run migration 20260402_add_qpl_manufacturer_mappings.sql.', 'warning')
        return redirect(url_for('manufacturers.manufacturers'))

    if not qpl_name_normalized:
        flash('QPL manufacturer name is required.', 'error')
        return redirect(url_for('manufacturers.manufacturers'))

    try:
        with db_cursor(commit=True) as cur:
            if manufacturer_id:
                _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO qpl_manufacturer_mappings (
                        qpl_manufacturer_name,
                        qpl_manufacturer_name_normalized,
                        manufacturer_id
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT (qpl_manufacturer_name_normalized)
                    DO UPDATE SET
                        qpl_manufacturer_name = EXCLUDED.qpl_manufacturer_name,
                        manufacturer_id = EXCLUDED.manufacturer_id,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (qpl_name, qpl_name_normalized, manufacturer_id),
                )
                flash('QPL manufacturer mapping updated.', 'success')
            else:
                _execute_with_cursor(
                    cur,
                    'DELETE FROM qpl_manufacturer_mappings WHERE qpl_manufacturer_name_normalized = ?',
                    (qpl_name_normalized,),
                )
                flash('QPL manufacturer mapping removed.', 'success')
    except Exception as exc:
        flash(f'Unable to save QPL mapping: {exc}', 'error')

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
