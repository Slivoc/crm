import os

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


@manufacturers_bp.route('/manufacturers', methods=['GET', 'POST'])
def manufacturers():
    if request.method == 'POST':
        name = request.form['name']
        insert_manufacturer(name)
        flash('Manufacturer added successfully!', 'success')
        return redirect(url_for('manufacturers.manufacturers'))

    # Use include_merged=True to show all manufacturers including merged ones
    manufacturers = get_all_manufacturers(include_merged=True)
    return render_template('manufacturers.html', manufacturers=manufacturers)

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
