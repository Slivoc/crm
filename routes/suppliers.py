from flask import Blueprint, render_template, request, redirect, url_for, jsonify, current_app, session
from models import update_supplier_field, get_suppliers, get_supplier_by_id, \
    update_supplier, update_supplier_fornitore, get_currencies
from utils import generate_breadcrumbs  # Import the helper function
from db import db_cursor, execute as db_execute
import logging
import os


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


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
    return dict(row) if row else None

suppliers_bp = Blueprint('suppliers', __name__)


@suppliers_bp.before_request
def require_login():
    if '_user_id' not in session:
        return redirect(url_for('auth.login'))

# All your routes below now automatically require login
@suppliers_bp.route('/')
def suppliers():
    path = request.path
    currencies = get_currencies()
    breadcrumbs = generate_breadcrumbs(path)
    suppliers = get_suppliers()
    return render_template('suppliers.html', currencies=currencies, suppliers=suppliers, breadcrumbs=breadcrumbs)


@suppliers_bp.route('/search')
def supplier_search():
    query = request.args.get('q', '') or request.args.get('query', '')
    limit = int(request.args.get('limit', 10))

    if not query:
        return jsonify([])

    params = (
        f'%{query}%',
        f'{query}%',
        f'%{query}%',
        limit,
    )
    if _using_postgres():
        sql = '''
            SELECT id, name
            FROM suppliers
            WHERE name ILIKE ?
            ORDER BY
                CASE
                    WHEN name ILIKE ? THEN 1
                    WHEN name ILIKE ? THEN 2
                    ELSE 3
                END,
                name
            LIMIT ?
        '''
    else:
        sql = '''
            SELECT id, name
            FROM suppliers
            WHERE LOWER(name) LIKE LOWER(?)
            ORDER BY
                CASE
                    WHEN LOWER(name) LIKE LOWER(?) THEN 1
                    WHEN LOWER(name) LIKE LOWER(?) THEN 2
                    ELSE 3
                END,
                name
            LIMIT ?
        '''

    suppliers = db_execute(sql, params, fetch='all') or []
    return jsonify([{
        'id': supplier['id'],
        'name': supplier['name'],
    } for supplier in suppliers])
@suppliers_bp.route('/create', methods=['POST'])
def create_supplier():
    if request.method == 'POST':
        if request.is_json:
            data = request.get_json()
            name = data.get('name')
            email = data.get('contact_email')
            first_name = data.get('first_name', '')
            delivery_cost = data.get('delivery_cost', 0)
            currency_id = data.get('currency_id', data.get('currency', 3))  # Default to GBP if not provided
            buffer = data.get('buffer', 1)  # Get buffer from form, default to 1
            contact_phone = data.get('contact_phone', '')  # Get phone from form
            standard_condition = data.get('standard_condition')
            standard_certs = data.get('standard_certs')

            if not name:
                return jsonify({
                    'success': False,
                    'error': 'Supplier name is required'
                }), 400

        try:
            insert_query = _with_returning_clause('''
                INSERT INTO suppliers 
                (name, contact_name, contact_email, contact_phone, buffer, currency, delivery_cost, fornitore, standard_condition, standard_certs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''')
            params = (
                name,
                first_name,
                email,
                contact_phone,
                buffer,
                currency_id,
                delivery_cost if delivery_cost else 0,
                '',
                standard_condition,
                standard_certs
            )
            with db_cursor(commit=True) as cur:
                _execute_with_cursor(cur, insert_query, params)
                supplier_id = _last_inserted_id(cur)

            return jsonify({
                'success': True,
                'supplier_id': supplier_id,
                'supplier_name': name
            })
        except Exception as e:
            current_app.logger.error(f"Error creating supplier: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
        else:
            return jsonify({
                'success': False,
                'error': 'Invalid content type. Expected JSON'
            }), 400

    return jsonify({
        'success': False,
        'error': 'Invalid request method'
    }), 405


@suppliers_bp.route('/<int:supplier_id>/edit')
def edit_supplier(supplier_id):
    path = request.path
    breadcrumbs = generate_breadcrumbs(path)
    supplier = get_supplier_by_id(supplier_id)
    currencies = get_currencies()
    if supplier is None:
        return redirect(url_for('suppliers.suppliers'))
    return render_template('edit_supplier.html', supplier=supplier, currencies=currencies, breadcrumbs=breadcrumbs)


@suppliers_bp.route('/<int:supplier_id>/update', methods=['POST'])
def update_supplier_route(supplier_id):
    try:
        name = request.form['name']
        contact_name = request.form['contact_name']
        contact_email = request.form['contact_email']
        contact_phone = request.form['contact_phone']
        buffer = request.form['buffer']
        currency = request.form['currency']
        delivery_cost = request.form.get('delivery_cost', 0)
        fornitore = request.form['fornitore']
        standard_condition = request.form.get('standard_condition')
        standard_certs = request.form.get('standard_certs')

        logging.info(f"Received form data: {request.form}")

        if not (name and contact_name and contact_email):
            logging.error("Missing required fields")
            return redirect(url_for('suppliers.edit_supplier', supplier_id=supplier_id))

        update_supplier(
            supplier_id,
            name,
            contact_name,
            contact_email,
            contact_phone,
            buffer,
            currency,
            fornitore,
            standard_condition,
            standard_certs
        )
        logging.info(f"Supplier {supplier_id} updated successfully.")
        return redirect(url_for('suppliers.suppliers'))

    except Exception as e:
        logging.error(f"Error updating supplier: {e}")
        return "An error occurred", 500


@suppliers_bp.route('/test_submission', methods=['POST'])
def test_submission():
    print("Form submission received")
    return "Form submitted successfully"


@suppliers_bp.route('/update_fornitore/<int:supplier_id>', methods=['POST'])
def update_fornitore(supplier_id):
    data = request.get_json()
    new_fornitore = data.get('fornitore')

    if not new_fornitore:
        return jsonify({'success': False, 'message': 'Fornitore is required'}), 400

    try:
        update_supplier_fornitore(supplier_id, new_fornitore)
        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Failed to update fornitore for supplier {supplier_id}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@suppliers_bp.route('/update_field/<int:supplier_id>', methods=['POST'])
def update_field(supplier_id):
    data = request.get_json()
    field_name = data.get('field')
    new_value = data.get('value')

    valid_fields = [
        'name', 'contact_name', 'contact_email', 'contact_phone', 'buffer', 'currency',
        'delivery_cost', 'fornitore', 'standard_condition', 'standard_certs'
    ]

    if field_name not in valid_fields:
        return jsonify({'success': False, 'message': 'Invalid field'}), 400

    try:
        update_supplier_field(supplier_id, field_name, new_value)
        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Failed to update {field_name} for supplier {supplier_id}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@suppliers_bp.route('/api/list')
def suppliers_list():
    suppliers = get_suppliers()  # Using your existing function
    return jsonify(suppliers)


@suppliers_bp.route('/api/<int:supplier_id>')
def get_supplier_api(supplier_id):
    """Get a single supplier by ID with currency information"""
    try:
        supplier = db_execute('''
            SELECT s.*, c.id as currency_id, c.currency_code
            FROM suppliers s
            LEFT JOIN currencies c ON s.currency = c.id
            WHERE s.id = ?
        ''', (supplier_id,), fetch='one')

        if supplier:
            return jsonify({
                'success': True,
                'supplier': dict(supplier)
            })
        return jsonify({
            'success': False,
            'error': 'Supplier not found'
        }), 404
    except Exception as e:
        logging.error(f'Error fetching supplier {supplier_id}: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@suppliers_bp.route('/supplier-contacts/add', methods=['POST'])
def add_supplier_contact():
    data = request.get_json()

    # Extract domain from email address
    email = data.get('email')
    domain = email.split('@')[-1] if email and '@' in email else None

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                INSERT INTO supplier_contacts 
                (first_name, second_name, email_address, supplier_id)
                VALUES (?, ?, ?, ?)
            ''', (
                data.get('first_name'),
                data.get('second_name'),
                email,
                data.get('supplier_id')
            ))

            if domain:
                existing_domain = _execute_with_cursor(cur, '''
                    SELECT id FROM supplier_domains
                    WHERE supplier_id = ? AND domain = ?
                ''', (data.get('supplier_id'), domain)).fetchone()

                if not existing_domain:
                    _execute_with_cursor(cur, '''
                        INSERT INTO supplier_domains
                        (supplier_id, domain, created_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                    ''', (
                        data.get('supplier_id'),
                        domain
                    ))

            contact = _execute_with_cursor(cur, '''
                SELECT sc.*, s.name as supplier_name
                FROM supplier_contacts sc
                LEFT JOIN suppliers s ON sc.supplier_id = s.id
                WHERE sc.email_address = ?
            ''', (email,)).fetchone()

        contact_dict = _row_to_dict(contact)
        if not contact_dict:
            raise RuntimeError('Failed to retrieve newly inserted contact')

        return jsonify({
            'success': True,
            'contact': contact_dict
        })

    except Exception as e:
        current_app.logger.error(f"Error adding supplier contact: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to add supplier contact'
        }), 400
