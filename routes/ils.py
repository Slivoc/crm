from flask import Blueprint, request, jsonify, render_template, url_for
from db import execute as db_execute, db_cursor
from models import create_base_part_number
import csv
import io
import logging
import os
from datetime import datetime
from difflib import SequenceMatcher  # For simple fuzzy similarity scoring

ils_bp = Blueprint('ils', __name__)


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _similarity(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _calculate_fuzzy_suggestions(ils_company, ils_cage, limit=5):
    rows = db_execute('''
        SELECT id, name
        FROM suppliers
        ORDER BY name
    ''', fetch='all') or []

    suggestions = []
    for row in rows:
        score = 0.0
        if ils_company:
            score = max(score, _similarity(ils_company, row['name']))
        if ils_cage and row['name'].lower().endswith(ils_cage.lower()):
            score = max(score, 0.8)

        if score > 0.6:
            match_type = 'company' if ils_company and score > 0.6 else 'cage' if ils_cage else 'unknown'
            suggestions.append({
                'id': row['id'],
                'name': row['name'],
                'similarity_score': round(score, 3),
                'match_type': match_type
            })

    suggestions.sort(key=lambda x: -x['similarity_score'])
    return suggestions[:limit]


def _scalar(query, params=None, key=None, default=None):
    row = db_execute(query, params or [], fetch='one')
    if not row:
        return default
    if key and hasattr(row, 'get'):
        return row.get(key, default)
    if hasattr(row, 'keys'):
        return next(iter(row.values()))
    return row[0]


@ils_bp.route('/supplier-mapping', methods=['GET'])
def supplier_mapping_page():
    """
    Display the supplier mapping interface
    """
    breadcrumbs = [
        ('Home', url_for('index')),
        ('ILS Supplier Mapping', None)
    ]
    return render_template('ils_supplier_mapping.html', breadcrumbs=breadcrumbs)


@ils_bp.route('/suppliers/all', methods=['GET'])
def get_all_suppliers():
    """
    Get all suppliers for dropdown in mapping interface
    """
    try:
        suppliers = db_execute('''
            SELECT s.id, s.name, c.currency_code 
            FROM suppliers s
            LEFT JOIN currencies c ON s.currency = c.id
            ORDER BY s.name
        ''', fetch='all') or []

        return jsonify({
            'success': True,
            'suppliers': [dict(s) for s in suppliers]
        })

    except Exception as e:
        logging.error(f'Error getting all suppliers: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@ils_bp.route('/suppliers/search', methods=['GET'])
def search_suppliers():
    """
    Search internal suppliers with fuzzy matching for ILS company names
    Enhanced with similarity scoring for better suggestions.
    """
    try:
        query = request.args.get('q', '').strip()
        limit = request.args.get('limit', 20, type=int)

        if not query or len(query) < 2:
            return jsonify({'success': True, 'suppliers': []})

        search_term = f'%{query}%'

        suppliers = db_execute('''
            SELECT DISTINCT s.id, s.name, c.id as currency_id, c.currency_code
            FROM suppliers s
            LEFT JOIN currencies c ON s.currency = c.id
            WHERE s.name LIKE ? COLLATE NOCASE
            ORDER BY 
                CASE 
                    WHEN s.name LIKE ? COLLATE NOCASE THEN 1
                    WHEN s.name LIKE ? COLLATE NOCASE THEN 2
                    ELSE 3
                END,
                s.name
            LIMIT ?
        ''', (search_term, f'{query}%', f'%{query}%', limit * 2), fetch='all') or []

        scored_suppliers = []
        for supp in suppliers:
            score = _similarity(query, supp['name'])
            if score > 0.3:
                scored_suppliers.append({
                    **dict(supp),
                    'similarity_score': round(score, 3)
                })

        scored_suppliers.sort(key=lambda x: (-x['similarity_score'], x['name']))
        top_suppliers = scored_suppliers[:limit]

        return jsonify({
            'success': True,
            'suppliers': top_suppliers
        })

    except Exception as e:
        logging.error(f'Error searching suppliers: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@ils_bp.route('/suppliers/fuzzy-suggest', methods=['GET'])
def fuzzy_supplier_suggestions():
    """
    NEW: Get fuzzy suggestions for a specific ILS company name or CAGE code.
    Used for auto-suggestions when viewing/editing an unmapped supplier.
    """
    try:
        ils_company = request.args.get('company', '').strip()
        ils_cage = request.args.get('cage', '').strip()
        limit = request.args.get('limit', 5, type=int)

        if not ils_company and not ils_cage:
            return jsonify({'success': True, 'suggestions': []})

        top_suggestions = _calculate_fuzzy_suggestions(ils_company, ils_cage, limit)

        return jsonify({
            'success': True,
            'suggestions': top_suggestions
        })

    except Exception as e:
        logging.error(f'Error getting fuzzy suggestions: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


def parse_ils_csv(file_content):
    """
    Parse ILS CSV export and extract part availability data.
    Hard-coded based on ILS PartsAvailability format.
    """
    # Skip first 3 header lines, actual headers are on line 4
    lines = file_content.strip().split('\n')

    # Find the header line (contains "ListCode","Item","Company",etc)
    header_line_idx = None
    for idx, line in enumerate(lines):
        if 'ListCode' in line and 'Company' in line and 'PartNo' in line:
            header_line_idx = idx
            break

    if header_line_idx is None:
        raise ValueError("Could not find header row in ILS CSV")

    # Parse CSV starting from header line
    csv_data = '\n'.join(lines[header_line_idx:])
    csv_reader = csv.DictReader(io.StringIO(csv_data))

    results = []
    for row in csv_reader:
        # Helper function to safely get and strip values
        def safe_get(key):
            value = row.get(key)
            if value is None:
                return ''
            return str(value).strip()

        # Extract and clean data
        part_number = safe_get('PartNo')
        if not part_number:
            continue

        result = {
            'item': safe_get('Item'),
            'company': safe_get('Company'),
            'cage_code': safe_get('CAGE'),
            'part_number': part_number,
            'alt_part_number': safe_get('AltPartNo'),
            'quantity': safe_get('QTY'),
            'condition': safe_get('Cond'),
            'serial_number': safe_get('SerialNo'),
            'description': safe_get('Description'),
            'price': safe_get('Price'),
            'phone': safe_get('Phone'),
            'fax': safe_get('Fax'),
            'email': safe_get('Email'),
            'distance': safe_get('Distance'),
            'supplier_comment': safe_get('SupplierComment'),
            'exchange': safe_get('EXCH'),
        }
        results.append(result)

    return results


def get_or_create_supplier_mapping(cur, ils_company_name, ils_cage_code):
    """
    Get supplier_id from mapping table by exact ils_company_name match, or create if none exists.
    Prioritizes rows with non-NULL cage_code or latest created_date if multiples found.
    """
    if not ils_company_name or not ils_company_name.strip():
        return None  # Skip empty names

    _execute_with_cursor(cur, '''
        SELECT id, supplier_id, ils_cage_code, created_date
        FROM ils_supplier_mappings 
        WHERE ils_company_name = ?
        ORDER BY 
            CASE WHEN ils_cage_code IS NOT NULL THEN 0 ELSE 1 END,
            created_date DESC
        LIMIT 1
    ''', (ils_company_name.strip(),))

    existing = cur.fetchone()
    if existing and existing.get('id'):
        return existing.get('supplier_id')

    _execute_with_cursor(cur, '''
        INSERT INTO ils_supplier_mappings (ils_company_name, ils_cage_code, supplier_id)
        VALUES (?, ?, NULL)
    ''', (ils_company_name.strip(), ils_cage_code if ils_cage_code else None))

    return None

def save_ils_results(parsed_results, search_date=None):
    """
    Save parsed ILS results to database.
    """
    if search_date is None:
        search_date = datetime.now()

    saved_count = 0
    with db_cursor(commit=True) as cur:
        for result in parsed_results:
            part_number = result['part_number']
            base_part_number = create_base_part_number(part_number)

            supplier_id = get_or_create_supplier_mapping(
                cur,
                result['company'],
                result['cage_code']
            )

            _execute_with_cursor(cur, '''
                INSERT INTO ils_search_results (
                    search_date, base_part_number, part_number, ils_company_name,
                    ils_cage_code, supplier_id, quantity, condition_code,
                    description, price, phone, email, distance, supplier_comment,
                    alt_part_number, exchange, serial_number, fax
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                search_date, base_part_number, part_number, result['company'],
                result['cage_code'] if result['cage_code'] else None,
                supplier_id, result['quantity'], result['condition'],
                result['description'], result['price'], result['phone'],
                result['email'], result['distance'], result['supplier_comment'],
                result['alt_part_number'], result['exchange'],
                result['serial_number'], result['fax']
            ))
            saved_count += 1

    return saved_count


@ils_bp.route('/upload', methods=['POST'])
def upload_ils_csv():
    """
    Upload and parse ILS CSV file.
    """
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400

        # Read file content
        file_content = file.read().decode('utf-8')

        # Parse CSV
        parsed_results = parse_ils_csv(file_content)

        if not parsed_results:
            return jsonify({'success': False, 'error': 'No parts found in CSV'}), 400

        # Save to database
        saved_count = save_ils_results(parsed_results)

        # Get summary stats
        unique_parts = len(set(r['part_number'] for r in parsed_results))
        unique_suppliers = len(set(r['company'] for r in parsed_results))

        return jsonify({
            'success': True,
            'message': f'Successfully imported {saved_count} ILS records',
            'stats': {
                'total_records': saved_count,
                'unique_parts': unique_parts,
                'unique_suppliers': unique_suppliers
            }
        })

    except Exception as e:
        logging.error(f'Error uploading ILS CSV: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@ils_bp.route('/suppliers/mappings', methods=['GET'])
def get_supplier_mappings():
    """
    Get list of all ILS supplier mappings (both mapped and unmapped).
    Enhanced: Includes fuzzy suggestions for unmapped ones.
    """
    try:
        mappings = db_execute('''
            SELECT 
                m.id,
                m.ils_company_name,
                m.ils_cage_code,
                m.supplier_id,
                m.created_date,
                m.notes,
                s.name as supplier_name,
                (SELECT COUNT(*) FROM ils_search_results 
                 WHERE ils_company_name = m.ils_company_name) as result_count
            FROM ils_supplier_mappings m
            LEFT JOIN suppliers s ON m.supplier_id = s.id
            ORDER BY result_count DESC, m.ils_company_name
        ''', fetch='all') or []

        result_rows = []
        for mapping in mappings:
            mapping_dict = dict(mapping)
            if not mapping_dict.get('supplier_id'):
                mapping_dict['suggestions'] = _calculate_fuzzy_suggestions(
                    mapping_dict['ils_company_name'],
                    mapping_dict['ils_cage_code'],
                    limit=3
                )
            result_rows.append(mapping_dict)

        return jsonify({
            'success': True,
            'suppliers': result_rows
        })

    except Exception as e:
        logging.error(f'Error getting supplier mappings: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@ils_bp.route('/suppliers/unmapped', methods=['GET'])
def get_unmapped_suppliers():
    """
    Get list of ILS suppliers that haven't been mapped to internal suppliers.
    Enhanced: Includes fuzzy suggestions for each.
    """
    try:
        unmapped = db_execute('''
            SELECT 
                id,
                ils_company_name,
                ils_cage_code,
                supplier_id,
                created_date,
                notes,
                (SELECT COUNT(*) FROM ils_search_results 
                 WHERE ils_company_name = ils_supplier_mappings.ils_company_name) as result_count
            FROM ils_supplier_mappings
            WHERE supplier_id IS NULL
            ORDER BY result_count DESC, ils_company_name
        ''', fetch='all') or []

        result_rows = []
        for mapping in unmapped:
            mapping_dict = dict(mapping)
            mapping_dict['suggestions'] = _calculate_fuzzy_suggestions(
                mapping_dict['ils_company_name'],
                mapping_dict['ils_cage_code'],
                limit=3
            )
            result_rows.append(mapping_dict)

        return jsonify({
            'success': True,
            'suppliers': result_rows
        })

    except Exception as e:
        logging.error(f'Error getting unmapped suppliers: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@ils_bp.route('/suppliers/map', methods=['POST'])
def map_supplier():
    """
    Map an ILS supplier to an internal supplier.
    """
    try:
        data = request.get_json()
        mapping_id = data.get('mapping_id')
        supplier_id = data.get('supplier_id')

        if not mapping_id:
            return jsonify({'success': False, 'error': 'mapping_id required'}), 400

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, '''
                UPDATE ils_supplier_mappings
                SET supplier_id = ?
                WHERE id = ?
            ''', (supplier_id, mapping_id))

            _execute_with_cursor(cursor, '''
                UPDATE ils_search_results
                SET supplier_id = ?
                WHERE ils_company_name = (
                    SELECT ils_company_name FROM ils_supplier_mappings WHERE id = ?
                )
            ''', (supplier_id, mapping_id))

        return jsonify({
            'success': True,
            'message': 'Supplier mapping updated'
        })

    except Exception as e:
        logging.error(f'Error mapping supplier: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@ils_bp.route('/results/<base_part_number>', methods=['GET'])
def get_ils_results_for_part(base_part_number):
    """
    Get ILS results for a specific part number.
    """
    try:
        # Optional: filter by preferred suppliers only
        preferred_only = request.args.get('preferred_only', 'false').lower() == 'true'
        # Optional: limit results
        limit = request.args.get('limit', 50, type=int)

        if preferred_only:
            query = '''
                SELECT 
                    r.*,
                    s.name as supplier_name,
                    s.id as internal_supplier_id
                FROM ils_search_results r
                LEFT JOIN suppliers s ON r.supplier_id = s.id
                WHERE r.base_part_number = ? AND r.supplier_id IS NOT NULL
                ORDER BY r.search_date DESC, r.ils_company_name
                LIMIT ?
            '''
        else:
            query = '''
                SELECT 
                    r.*,
                    s.name as supplier_name,
                    s.id as internal_supplier_id
                FROM ils_search_results r
                LEFT JOIN suppliers s ON r.supplier_id = s.id
                WHERE r.base_part_number = ?
                ORDER BY r.search_date DESC, r.ils_company_name
                LIMIT ?
            '''

        results = db_execute(query, (base_part_number, limit), fetch='all') or []

        return jsonify({
            'success': True,
            'results': [dict(row) for row in results]
        })

    except Exception as e:
        logging.error(f'Error getting ILS results: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@ils_bp.route('/stats', methods=['GET'])
def get_ils_stats():
    """
    Get overall ILS statistics.
    """
    try:
        stats = {
            'total_results': _scalar('SELECT COUNT(*) as count FROM ils_search_results', key='count', default=0),
            'unique_parts': _scalar(
                'SELECT COUNT(DISTINCT base_part_number) as count FROM ils_search_results', key='count', default=0),
            'unique_suppliers': _scalar(
                'SELECT COUNT(DISTINCT ils_company_name) as count FROM ils_search_results', key='count', default=0),
            'mapped_suppliers': _scalar(
                'SELECT COUNT(*) as count FROM ils_supplier_mappings WHERE supplier_id IS NOT NULL', key='count', default=0),
            'unmapped_suppliers': _scalar(
                'SELECT COUNT(*) as count FROM ils_supplier_mappings WHERE supplier_id IS NULL', key='count', default=0),
            'latest_search_date': _scalar(
                'SELECT MAX(search_date) as date FROM ils_search_results', key='date')
        }

        return jsonify({
            'success': True,
            'stats': stats
        })

    except Exception as e:
        logging.error(f'Error getting ILS stats: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
