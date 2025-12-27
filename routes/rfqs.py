import logging
import json
import re
import math
import chardet
import requests
import extract_msg
from sqlalchemy.orm import sessionmaker
from bs4 import BeautifulSoup
import bleach
import os
from email.parser import BytesParser
from email import policy
from flask import Flask, Blueprint, render_template, request, redirect, url_for, jsonify, flash, current_app
from utils import generate_breadcrumbs
from werkzeug.utils import secure_filename
from collections import defaultdict
import traceback
import time
from collections import Counter
from models import (
    get_currency_symbol,
    get_offer_by_id,
    update_rfq_line_db,
    calculate_base_cost,
    get_exchange_rate,
    get_rfq_line_currency,
    get_price_list_price,
    get_currencies,
    engine,
    get_all_rfqs,
    clean_rfq_lines_base_part_numbers,
    get_part_number_by_base,
    get_all_rfq_lines,
    get_all_statuses,
    get_customer_id_by_rfq,
    update_rfq_line_base_cost,
    get_rfq_lines_with_offers,
    insert_requisition,
    get_requisitions,
    save_email_file_and_create_entries,
    get_db,
    get_rfqs,
    insert_rfq,
    get_customers,
    get_contacts,
    get_contacts_by_customer,
    get_rfq_lines,
    insert_rfq_line,
    get_suppliers,
    update_rfq_line,
    get_customer_by_id,
    get_rfq_by_id,
    update_rfq,
    get_supplier_by_id,
    get_rfq_line_by_id,
    get_salesperson_by_id,
    get_files_by_rfq,
    insert_file,
    get_salespeople,
    get_part_numbers,
    get_all_part_numbers_with_manufacturers,
    get_all_manufacturers,
    create_base_part_number,
    insert_part_number,
    get_rfq_updates,
    add_rfq_update,
    get_latest_rfq_update,
    get_update_types
)
from routes.auth import login_required, current_user

from datetime import date, datetime, timedelta  # Correct import
from ai_helper import extract_part_numbers_and_quantities

from models import (
    create_base_part_number, PartNumber, RFQLine, engine
)

from db import db_cursor, execute as db_execute


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


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


def _row_value(row, key=None):
    if row is None:
        return None
    if isinstance(row, dict):
        if key:
            return row.get(key)
        return next(iter(row.values()), None)
    try:
        if key is not None:
            return row[key]
    except Exception:
        pass
    try:
        return row[0]
    except Exception:
        return None

# Set up logging
app = Flask(__name__)
logger = logging.getLogger(__name__)

rfqs_bp = Blueprint('rfqs', __name__)
Session = sessionmaker(bind=engine)
session = Session()

def row_to_dict(row):
    return {key: row[key] for key in row.keys()}


@rfqs_bp.route('/rfqs', methods=['GET', 'POST'])
@login_required
def rfqs():
    print(f"Current user type: {current_user.user_type}")  # Debug print

    filter_customer = request.args.get('filter_customer', '').strip()
    filter_salesperson = request.args.get('filter_salesperson', '').strip()
    part_number = request.args.get('part_number', '').strip()
    base_part_number = create_base_part_number(part_number) if part_number else None

    print("DEBUG ----")
    print(f"User ID: {current_user.id}")
    print(f"Username: {current_user.username}")
    print(f"User Type: {current_user.user_type}")
    print(f"User Type Lower: {current_user.user_type.lower()}")  # Add this debug line
    print("----")

    all_rfqs = get_rfqs()
    print(f"Total RFQs before filtering: {len(all_rfqs)}")

    if current_user.user_type.lower() == 'admin':  # Make comparison case-insensitive
        print("ADMIN PATH TAKEN")
        rfqs = all_rfqs
    else:
        print("NON-ADMIN PATH TAKEN")
        user_salesperson_id = current_user.get_salesperson_id()
        rfqs = [rfq for rfq in all_rfqs if str(rfq['salesperson_id']) == str(user_salesperson_id)]

    # Apply additional filters
    if filter_customer:
        rfqs = [rfq for rfq in rfqs if rfq['customer_id'] == int(filter_customer)]

    if filter_salesperson:
        rfqs = [rfq for rfq in rfqs if rfq.get('salesperson_id') == int(filter_salesperson)]


    if base_part_number:
        rfqs = [rfq for rfq in rfqs if
                any(base_part_number in line['base_part_number'] for line in get_rfq_lines(rfq['id']))]

    # Get filtered lists based on user type
    if current_user.user_type == 'admin':
        customers = get_customers()
        salespeople = get_salespeople()  # Only admins see salesperson filter
    else:
        # Normal users only see their customers
        customers = [c for c in get_customers() if any(rfq['customer_id'] == c['id'] for rfq in rfqs)]
        salespeople = []  # Normal users don't need salesperson list

    # Rest of your existing code for processing RFQs
    for rfq in rfqs:
        customer = get_customer_by_id(rfq['customer_id'])
        salesperson_id = rfq.get('salesperson_id')
        salesperson = get_salesperson_by_id(salesperson_id) if salesperson_id else None
        rfq['customer_name'] = customer['name']
        rfq['salesperson_name'] = salesperson['name'] if salesperson else 'No salesperson'

        rfq_lines = get_rfq_lines_with_offers(rfq['id'])
        rfq['total_lines'] = len(rfq_lines)
        rfq['lines_with_offers'] = sum(1 for line in rfq_lines if line['offers'])
        total_value = sum(float(line.get('line_value', 0) or 0) for line in rfq_lines)
        rfq['total_value'] = total_value

    for rfq in rfqs:
        latest_update = get_latest_rfq_update(rfq['id'])
        if latest_update:
            rfq['latest_update'] = latest_update[0]
        else:
            rfq['latest_update'] = None

    # Group RFQs as before
    grouped_rfqs = {}
    for rfq in rfqs:
        if rfq['status'] == 'deleted':
            continue
        status = rfq['status']
        if status not in grouped_rfqs:
            grouped_rfqs[status] = []
        grouped_rfqs[status].append(rfq)

    today_date = date.today().isoformat()

    breadcrumbs = [
        ('Home', '/'),
        ('RFQs', url_for('rfqs.rfqs'))
    ]

    # Pass user type to template for UI adjustments
    return render_template('rfqs.html',
                         customers=customers,
                         salespeople=salespeople,
                         grouped_rfqs=grouped_rfqs,
                         today_date=today_date,
                         breadcrumbs=breadcrumbs,
                         part_number=part_number,
                         update_types=get_update_types(),
                         user_type=current_user.user_type)  # Add user type for template


@rfqs_bp.route('/create_rfq', methods=['POST'])
def create_rfq():
    logging.debug("Entered create_rfq route")
    if request.method == 'POST':
        logging.debug('Form data received: %s', request.form)
        try:
            entered_date = request.form['entered_date']
            customer_id = request.form['customer_id']
            customer_ref = request.form['customer_ref']
            project_id = request.form.get('project_id')  # Get project_id if it exists
            status = 'new'  # Default status for new RFQs
            salesperson_id = request.form.get('salesperson_id')  # Get the salesperson_id from the form

            with db_cursor(commit=True) as cur:
                customer_currency = _execute_with_cursor(
                    cur,
                    'SELECT currency_id FROM customers WHERE id = ?',
                    (customer_id,),
                    fetch='one'
                )

                if customer_currency:
                    currency_id = customer_currency.get('currency_id') if isinstance(customer_currency, dict) else customer_currency[0]
                else:
                    flash('Customer currency not found', 'error')
                    return redirect(url_for('rfqs.create_rfq'))

                logging.debug(
                    f"Attempting to insert RFQ with data: {entered_date}, {customer_id}, {customer_ref}, {status}, {currency_id}, {salesperson_id}")

                inserted_row = _execute_with_cursor(
                    cur,
                    'INSERT INTO rfqs (entered_date, customer_id, customer_ref, status, currency, salesperson_id) VALUES (?, ?, ?, ?, ?, ?) RETURNING id',
                    (entered_date, customer_id, customer_ref, status, currency_id, salesperson_id),
                    fetch='one'
                )
                new_rfq_id = _get_inserted_id(inserted_row, cur)

                if project_id:
                    _execute_with_cursor(
                        cur,
                        'INSERT INTO project_rfqs (project_id, rfq_id) VALUES (?, ?)',
                        (project_id, new_rfq_id)
                    )

            flash('RFQ created successfully!', 'success')
            logging.info(f'RFQ created successfully with ID {new_rfq_id}')

            return redirect(url_for('rfqs.edit_rfq', rfq_id=new_rfq_id))
        except Exception as e:
            logging.error(f'Database error creating RFQ: {e}')
            flash(f'Database error creating RFQ: {e}', 'error')
            return redirect(url_for('rfqs.create_rfq'))

    logging.debug("Rendering create_rfq template")
    customers = get_customers()
    today_date = date.today().isoformat()  # Set the default date to today
    return render_template('rfqs.html', customers=customers, today_date=today_date)


@rfqs_bp.route('/open_rfq', methods=['POST'])
def open_rfq():
    rfq_id = request.form.get('rfq_id')
    if rfq_id:
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))
    else:
        flash('Please enter a valid RFQ ID', 'error')
        return redirect(url_for('rfqs.rfqs'))


@rfqs_bp.route('/<int:rfq_id>/edit', methods=['GET', 'POST'])
def edit_rfq(rfq_id):
    currencies = get_currencies()
    rfq = get_rfq_by_id(rfq_id)
    rfq_updates = get_rfq_updates(rfq_id)

    # Debugging: Print the email content to check what's being passed
    if not rfq:
        flash('RFQ not found!', 'error')
        return redirect(url_for('rfqs.rfqs'))

    if request.method == 'POST':
        try:
            entered_date = request.form['entered_date']
            customer_id = request.form['customer_id']
            contact_id = request.form['contact_id']
            customer_ref = request.form['customer_ref']
            status = request.form['status']
            currency = request.form['currency']
            salesperson_id = request.form.get('salesperson_id')

            update_rfq(rfq_id, entered_date, customer_id, contact_id, customer_ref, status, currency, salesperson_id)
            flash('RFQ updated successfully!', 'success')
        except Exception as e:
            flash(f'Error updating RFQ: {e}', 'error')
            return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

    clean_rfq_lines_base_part_numbers()

    rfq_lines = get_rfq_lines_with_offers(rfq_id)
    print(f"RFQ lines with offers: {rfq_lines}")

    for line in rfq_lines:
        price_list_info = get_price_list_price(line['base_part_number'], line['quantity'])
        if price_list_info:
            line['price_list_info'] = price_list_info

    with db_cursor() as cur:
        for line in rfq_lines:
            if line['offer_id']:
                result = _execute_with_cursor(
                    cur,
                    '''
                    SELECT s.name as supplier_name
                    FROM offers o
                    JOIN suppliers s ON o.supplier_id = s.id
                    WHERE o.id = ?
                    ''',
                    (line['offer_id'],),
                    fetch='one'
                )
                supplier_name = _row_value(result, 'supplier_name')
                if supplier_name:
                    line['chosen_supplier_name'] = supplier_name

            part_number_row = _execute_with_cursor(
                cur,
                'SELECT part_number FROM part_numbers WHERE base_part_number = ?',
                (line['base_part_number'],),
                fetch='one'
            )
            part_number_value = _row_value(part_number_row, 'part_number')
            line['part_number'] = part_number_value if part_number_value else 'Unknown'

            chosen_supplier_row = _execute_with_cursor(
                cur,
                'SELECT name FROM suppliers WHERE id = ?',
                (line['chosen_supplier'],),
                fetch='one'
            )
            chosen_supplier_name = _row_value(chosen_supplier_row, 'name')
            line['chosen_supplier_name'] = chosen_supplier_name if chosen_supplier_name else line.get('chosen_supplier_name')

        for line in rfq_lines:
            base_part_number = line['base_part_number']

            previous_offers_row = _execute_with_cursor(
                cur,
                '''
                SELECT COUNT(*) as offer_history_count
                FROM offer_lines
                WHERE base_part_number = ? AND offer_id != ?
                ''',
                (base_part_number, line['offer_id']),
                fetch='one'
            )
            previous_offers = _row_value(previous_offers_row, 'offer_history_count') or 0
            line['has_offer_history'] = previous_offers > 0

            previous_rfq_lines_row = _execute_with_cursor(
                cur,
                '''
                SELECT COUNT(*) as rfq_history_count
                FROM rfq_lines
                WHERE base_part_number = ? AND id != ?
                ''',
                (base_part_number, line['id']),
                fetch='one'
            )
            previous_rfq_lines = _row_value(previous_rfq_lines_row, 'rfq_history_count') or 0
            line['has_rfq_history'] = previous_rfq_lines > 0

            line['has_history'] = line['has_offer_history'] or line['has_rfq_history']

            previous_sales_order_lines_row = _execute_with_cursor(
                cur,
                '''
                SELECT COUNT(*) as sales_order_lines_count
                FROM sales_order_lines
                WHERE base_part_number = ?
                ''',
                (base_part_number,),
                fetch='one'
            )
            previous_sales_order_lines = _row_value(previous_sales_order_lines_row, 'sales_order_lines_count') or 0
            line['has_sales_order_lines'] = previous_sales_order_lines > 0

        max_line_number_row = _execute_with_cursor(
            cur,
            'SELECT MAX(line_number) as max_line_number FROM rfq_lines WHERE rfq_id = ?',
            (rfq_id,),
            fetch='one'
        )
        max_line_number_str = _row_value(max_line_number_row, 'max_line_number')

    if max_line_number_str:
        try:
            max_line_number_float = float(max_line_number_str)
            max_line_number = math.ceil(max_line_number_float)
        except ValueError:
            logging.error(f"Unable to convert max line number: {max_line_number_str}")
            max_line_number = 0
    else:
        max_line_number = 0

    contacts = get_contacts_by_customer(rfq['customer_id'])
    customers = get_customers()
    salespeople = get_salespeople()
    files = get_files_by_rfq(rfq_id)
    all_part_numbers = get_all_part_numbers_with_manufacturers()
    all_manufacturers = get_all_manufacturers()
    past_requisitions = get_requisitions()
    suppliers = [dict(supplier) for supplier in get_suppliers()]
    statuses = get_statuses()

    # Further processing of rfq_lines can happen here, if needed

    return render_template(
        'rfq_edit.html',
        rfq=rfq,
        rfq_lines = [line for line in rfq_lines if line.get('status_id') != 8],
        contacts=contacts,
        customers=customers,
        salespeople=salespeople,
        files=files,
        all_part_numbers=all_part_numbers,
        all_manufacturers=all_manufacturers,
        max_line_number=max_line_number,
        suppliers=suppliers,
        get_currency_symbol=get_currency_symbol,
        currencies=currencies,
        rfq_updates=rfq_updates,
        statuses=statuses,
        breadcrumbs = [
        ('Home', url_for('index')),
        ('RFQs', url_for('rfqs.rfqs')),
        (f'Edit RFQ #{rfq_id}', url_for('rfqs.edit_rfq', rfq_id=rfq_id))
    ]
    )


# Define a function to get all statuses
def get_statuses():
    statuses = db_execute('SELECT id, status FROM statuses', fetch='all')
    return [dict(status) for status in (statuses or [])]
@rfqs_bp.route('/<int:rfq_id>/add_rfq_line', methods=['POST'])
def add_rfq_line(rfq_id):
    line_number = request.form.get('line_number')
    part_number = request.form.get('part_number')
    quantity = request.form.get('quantity')
    manufacturer_name = request.form.get('manufacturer_name', '').strip()
    customer_part_number = request.form.get('customer_part_number')

    base_part_number = create_base_part_number(part_number)

    try:
        with db_cursor(commit=True) as cur:
            existing_part = _execute_with_cursor(
                cur,
                'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
                (base_part_number,),
                fetch='one'
            )

            if not existing_part:
                _execute_with_cursor(
                    cur,
                    'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                    (part_number, base_part_number)
                )

            manufacturer_id = None
            if manufacturer_name:
                mfg = _execute_with_cursor(
                    cur,
                    '''
                    SELECT m1.id, m1.merged_into, m2.id as canonical_id
                    FROM manufacturers m1
                    LEFT JOIN manufacturers m2 ON m1.merged_into = m2.id
                    WHERE LOWER(m1.name) = LOWER(?)
                    ''',
                    (manufacturer_name,),
                    fetch='one'
                )

                if mfg:
                    manufacturer_id = mfg['canonical_id'] if mfg['merged_into'] else mfg['id']
                else:
                    inserted_mfg = _execute_with_cursor(
                        cur,
                        'INSERT INTO manufacturers (name) VALUES (?) RETURNING id',
                        (manufacturer_name,),
                        fetch='one'
                    )
                    manufacturer_id = _get_inserted_id(inserted_mfg, cur)

            suggested_suppliers = _execute_with_cursor(
                cur,
                '''
                SELECT DISTINCT supplier_id FROM requisitions
                WHERE base_part_number = ?
                ''',
                (base_part_number,),
                fetch='all'
            ) or []
            suggested_suppliers_str = ','.join(str(row['supplier_id']) for row in suggested_suppliers)

            _execute_with_cursor(
                cur,
                '''
                INSERT INTO rfq_lines (rfq_id, line_number, base_part_number, quantity, 
                                     manufacturer_id, suggested_suppliers)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                (rfq_id, line_number, base_part_number, quantity,
                 manufacturer_id, suggested_suppliers_str)
            )

            if manufacturer_id:
                association = _execute_with_cursor(
                    cur,
                    '''
                    SELECT * FROM part_manufacturers
                    WHERE base_part_number = ? AND manufacturer_id = ?
                    ''',
                    (base_part_number, manufacturer_id),
                    fetch='one'
                )

                if not association:
                    _execute_with_cursor(
                        cur,
                        '''
                        INSERT INTO part_manufacturers (base_part_number, manufacturer_id)
                        VALUES (?, ?)
                        ''',
                        (base_part_number, manufacturer_id)
                    )

            if customer_part_number:
                customer_id = get_customer_id_by_rfq(rfq_id)
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO customer_part_numbers (base_part_number, customer_part_number, customer_id)
                    VALUES (?, ?, ?)
                    ''',
                    (base_part_number, customer_part_number, customer_id)
                )

        flash('RFQ line added successfully with suggested suppliers!', 'success')
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))
    except Exception as e:
        flash(f'Error adding RFQ line: {str(e)}', 'error')
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))


@rfqs_bp.route('/<int:rfq_id>/set_default_margin', methods=['POST'])
def set_default_margin(rfq_id):
    default_margin = float(request.form['default_margin'])
    db_execute('UPDATE rfq_lines SET margin = ? WHERE rfq_id = ?', (default_margin, rfq_id), commit=True)
    return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

@rfqs_bp.route('/<int:rfq_id>/set_default_supplier', methods=['POST'])
def set_default_supplier(rfq_id):
    default_supplier = request.form['default_supplier']
    db_execute('UPDATE rfq_lines SET suggested_suppliers = ? WHERE rfq_id = ?', (default_supplier, rfq_id), commit=True)
    return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))


@rfqs_bp.route('/<int:rfq_id>/generate_requisitions')
def generate_requisitions(rfq_id):
    rfq_lines = get_rfq_lines(rfq_id)
    suppliers = get_suppliers()
    supplier_emails = {}

    def row_to_dict(row):
        return {key: row[key] for key in row.keys()}

    rfq_lines = [row_to_dict(line) for line in rfq_lines]

    for line in rfq_lines:
        base_part_number = line['base_part_number']
        part_number_row = db_execute('SELECT part_number FROM part_numbers WHERE base_part_number = ?',
                                     (base_part_number,), fetch='one')
        part_number = _row_value(part_number_row, 'part_number') or 'Unknown'
        line['part_number'] = part_number

    # Create a top-level requisition
    top_level_requisition_id, top_level_reference = create_top_level_requisition()

    # Generate requisitions grouped by suppliers
    requisitions = {}
    for line in rfq_lines:
        suggested_suppliers = line['suggested_suppliers'].split(',') if line['suggested_suppliers'] else []
        for supplier_id in suggested_suppliers:
            if supplier_id not in requisitions:
                reference = f"REQ-{rfq_id}-{supplier_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                requisitions[supplier_id] = {
                    'reference': reference,
                    'lines': []
                }
            requisitions[supplier_id]['lines'].append(line)

    with db_cursor(commit=True) as cur:
        for supplier_id, requisition in requisitions.items():
            inserted_requisition_row = _execute_with_cursor(
                cur,
                '''
                INSERT INTO requisitions (rfq_id, supplier_id, date, reference)
                VALUES (?, ?, ?, ?)
                RETURNING id
                ''',
                (rfq_id, supplier_id, datetime.now().strftime('%Y-%m-%d'), requisition['reference']),
                fetch='one'
            )
            requisition_id = _get_inserted_id(inserted_requisition_row, cur)

            add_requisition_to_top_level(top_level_requisition_id, requisition_id)

            supplier = next((s for s in suppliers if s['id'] == int(supplier_id)), None)
            supplier_name = None
            if supplier:
                supplier_name = supplier['name']
                supplier_emails[supplier_name] = {
                    'contact_name': supplier['contact_name'],
                    'contact_email': supplier['contact_email'],
                    'lines': []
                }

            for line in requisition['lines']:
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO requisition_lines (requisition_id, rfq_line_id, part_number, quantity, cost, supplier_lead_time, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        requisition_id, line['id'], line['part_number'], line['quantity'], line['cost'],
                        line['supplier_lead_time'], line['note']
                    )
                )

                _execute_with_cursor(
                    cur,
                    '''
                    UPDATE rfq_lines
                    SET status_id = ?
                    WHERE id = ?
                    ''',
                    (2, line['id'])
                )

                if supplier and supplier_name:
                    supplier_emails[supplier_name]['lines'].append({
                        'part_number': line['part_number'],
                        'quantity': line['quantity'],
                        'note': line['note']
                    })

    return render_template('requisitions.html', supplier_emails=supplier_emails, rfq_id=rfq_id)


@rfqs_bp.route('/<int:rfq_id>/extract_data', methods=['POST'])
def extract_data(rfq_id):
    logging.debug(f"Received request to extract data for RFQ ID: {rfq_id}")
    logging.debug(f"Request headers: {request.headers}")
    logging.debug(f"Form data: {request.form}")

    try:
        request_data = request.form.get('request_data')
        if not request_data:
            logging.error("'request_data' not found in form data")
            return jsonify({'success': False, 'error': "'request_data' is required"}), 400

        logging.debug(f"Extracting data from: {request_data}")

        extracted_lines = extract_part_numbers_and_quantities(request_data)
        logging.debug(f"Extracted lines: {extracted_lines}")

        existing_lines = get_rfq_lines(rfq_id)
        next_line_number = len(existing_lines) + 1

        with db_cursor(commit=True) as cur:
            for part_number, quantity in extracted_lines:
                base_part_number = create_base_part_number(part_number)
                logging.debug(f"Processing part: {part_number}, quantity: {quantity}, base: {base_part_number}")

                existing_part = _execute_with_cursor(
                    cur,
                    'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
                    (base_part_number,),
                    fetch='one'
                )

                if not existing_part:
                    logging.debug(f"Inserting new part: {part_number}")
                    _execute_with_cursor(
                        cur,
                        'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                        (part_number, base_part_number)
                    )

                logging.debug(f"Inserting RFQ line: rfq_id={rfq_id}, line_number={next_line_number}, base_part_number={base_part_number}, quantity={quantity}")
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO rfq_lines (rfq_id, line_number, base_part_number, quantity, cost)
                    VALUES (?, ?, ?, ?, ?)
                    ''',
                    (rfq_id, next_line_number, base_part_number, quantity, 0.0)
                )

                next_line_number += 1

        logging.debug("RFQ lines added successfully")
        return jsonify({'success': True, 'message': 'RFQ lines added successfully!'})
    except Exception as e:
        logging.exception(f'Error in extract_data: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500

@rfqs_bp.route('/<int:rfq_id>/update_all_rfq_lines', methods=['POST'])
def update_all_rfq_lines(rfq_id):
    try:
        print("Form Data:", request.form)  # Print all form data for debugging
        with db_cursor(commit=True) as cur:
            rfq_lines = _execute_with_cursor(
                cur,
                'SELECT id FROM rfq_lines WHERE rfq_id = ?',
                (rfq_id,),
                fetch='all'
            ) or []

            for line in rfq_lines:
                line_id = _row_value(line, 'id')
                try:
                    line_number = request.form.get(f'line_number_{line_id}', '')
                    part_number = request.form.get(f'part_number_{line_id}', '')
                    manufacturer_id = request.form.get(f'manufacturer_{line_id}', None)
                    quantity = request.form.get(f'quantity_{line_id}', '0')
                    cost = request.form.get(f'cost_{line_id}', '0.0')
                    supplier_lead_time = request.form.get(f'supplier_lead_time_{line_id}', '0')
                    margin = request.form.get(f'margin_{line_id}', '0.0')
                    price = request.form.get(f'price_{line_id}', '0.0')
                    lead_time = request.form.get(f'lead_time_{line_id}', '0')
                    line_value = request.form.get(f'line_value_{line_id}', '0.0')
                    note = request.form.get(f'note_{line_id}', '')

                    manufacturer_id = manufacturer_id if manufacturer_id else None
                    note = note if note != 'None' else ''

                    base_part_number_row = _execute_with_cursor(
                        cur,
                        'SELECT base_part_number FROM part_numbers WHERE part_number = ?',
                        (part_number,),
                        fetch='one'
                    )
                    base_part_number = _row_value(base_part_number_row, 'base_part_number')
                    if not base_part_number:
                        base_part_number = create_base_part_number(part_number)
                        _execute_with_cursor(
                            cur,
                            'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                            (part_number, base_part_number)
                        )

                    print(f"Updating line {line_id}: {line_number}, {part_number}, {base_part_number}, {manufacturer_id}, {quantity}, {cost}, {supplier_lead_time}, {margin}, {price}, {lead_time}, {line_value}, {note}")

                    _execute_with_cursor(
                        cur,
                        '''
                        UPDATE rfq_lines
                        SET line_number = ?, part_number = ?, base_part_number = ?, manufacturer_id = ?, quantity = ?, cost = ?, supplier_lead_time = ?, margin = ?, price = ?, lead_time = ?, line_value = ?, note = ?
                        WHERE id = ?
                        ''',
                        (line_number, part_number, base_part_number, manufacturer_id, quantity, cost, supplier_lead_time, margin, price, lead_time, line_value, note, line_id)
                    )
                except KeyError as e:
                    print(f"Missing form data for key: {e}")
                    flash(f"Error updating line {line_id}: missing data.", 'danger')
                    continue

        flash('RFQ lines successfully updated', 'success')

    except Exception as e:
        flash(f'Error updating RFQ lines: {e}', 'danger')

    return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

# routes/rfqs.py

@rfqs_bp.route('/get_base_cost/<int:line_id>', methods=['GET'])
def get_base_cost(line_id):
    try:
        result = db_execute('SELECT base_cost FROM rfq_lines WHERE id = ?', (line_id,), fetch='one')
        if result:
            return jsonify({'success': True, 'base_cost': result['base_cost']})
        return jsonify({'success': False, 'error': 'Line not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@rfqs_bp.route('/update_rfq_line_field', methods=['POST'])
def update_rfq_line_field():
    data = request.get_json()
    logging.debug(f'Received data: {data}')
    line_id = data.get('line_id')
    field_name = data.get('field_name')
    field_value = data.get('field_value')

    # Special handling for manufacturer_name
    if field_name == 'manufacturer_name':
        try:
            with db_cursor(commit=True) as cur:
                logging.debug(f'Processing manufacturer: {field_value}')

                if not field_value or field_value.strip() == '':
                    _execute_with_cursor(
                        cur,
                        'UPDATE rfq_lines SET manufacturer_id = NULL WHERE id = ?',
                        (line_id,)
                    )
                    return jsonify({
                        'success': True,
                        'manufacturer_id': None,
                        'manufacturer_name': ''
                    })

                mfg = _execute_with_cursor(
                    cur,
                    '''
                    SELECT m1.id, m1.merged_into, m2.id as canonical_id
                    FROM manufacturers m1
                    LEFT JOIN manufacturers m2 ON m1.merged_into = m2.id
                    WHERE LOWER(m1.name) = LOWER(?)
                    ''',
                    (field_value,),
                    fetch='one'
                )

                if mfg:
                    manufacturer_id = mfg['canonical_id'] if mfg['merged_into'] else mfg['id']
                    logging.debug(f'Found existing manufacturer ID: {manufacturer_id}')
                else:
                    inserted_mfg = _execute_with_cursor(
                        cur,
                        'INSERT INTO manufacturers (name) VALUES (?) RETURNING id',
                        (field_value,),
                        fetch='one'
                    )
                    manufacturer_id = _get_inserted_id(inserted_mfg, cur)
                    logging.debug(f'Created new manufacturer ID: {manufacturer_id}')

                _execute_with_cursor(
                    cur,
                    'UPDATE rfq_lines SET manufacturer_id = ? WHERE id = ?',
                    (manufacturer_id, line_id)
                )

            return jsonify({
                'success': True,
                'manufacturer_id': manufacturer_id,
                'manufacturer_name': field_value
            })
        except Exception as e:
            logging.error(f'Error updating manufacturer: {str(e)}')
            return jsonify({'success': False, 'error': str(e)})

    # Handle all other fields with your existing code
    field_map = {
        'offer_id': 'offer_id',
        'cost': 'cost',
        'supplier_lead_time': 'supplier_lead_time',
        'chosen_supplier': 'chosen_supplier',
        'manufacturer_id': 'manufacturer_id',
        'quantity': 'quantity',
        'margin': 'margin',
        'lead_time': 'lead_time',
        'line_value': 'line_value',
        'note': 'note',
        'base_part_number': 'base_part_number',
        'price': 'price',
        'status_id': 'status_id',
        'suggested_suppliers': 'suggested_suppliers',
        'cost_currency': 'cost_currency',
        'line_number': 'line_number',
        'base_cost': 'base_cost'
    }


    db_column = field_map.get(field_name)

    if db_column is None:
        logging.error('Invalid field name')
        return jsonify({'success': False, 'error': 'Invalid field name'})

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                f'UPDATE rfq_lines SET {db_column} = ? WHERE id = ?',
                (field_value, line_id)
            )

            calculated_price = None
            calculated_line_value = None

            if field_name in ['margin', 'quantity']:
                line_data = _execute_with_cursor(
                    cur,
                    '''
                    SELECT base_cost, margin, quantity 
                    FROM rfq_lines WHERE id = ?
                    ''',
                    (line_id,),
                    fetch='one'
                )

                if line_data:
                    base_cost = float(line_data['base_cost']) if line_data['base_cost'] else 0
                    margin_value = float(line_data['margin']) if line_data['margin'] else 0
                    quantity_value = float(line_data['quantity']) if line_data['quantity'] else 0

                    if base_cost and quantity_value:
                        calculated_price = round(base_cost / (1 - (margin_value / 100)), 2)
                        calculated_line_value = round(calculated_price * quantity_value, 2)

                        _execute_with_cursor(
                            cur,
                            '''
                            UPDATE rfq_lines 
                            SET price = ?, line_value = ? 
                            WHERE id = ?
                            ''',
                            (calculated_price, calculated_line_value, line_id)
                        )

            response = {'success': True}
            if calculated_price is not None:
                response['calculated_price'] = calculated_price
                response['calculated_line_value'] = calculated_line_value

            return jsonify(response)
    except Exception as e:
        logging.error(f'Error updating field: {e}')
        return jsonify({'success': False, 'error': str(e)})


@rfqs_bp.route('/update_rfq_line_offer', methods=['POST'])
def update_rfq_line_offer():
    data = request.get_json()
    line_id = data.get('line_id')
    offer_id = data.get('offer_id')
    price = data.get('price')
    supplier_lead_time = data.get('lead_time')
    supplier_id = data.get('supplier_id')
    offered_base_part_number = data.get('offered_base_part_number')

    # Get new fields from the offer
    datecode = data.get('datecode')
    spq = data.get('spq')
    packaging = data.get('packaging')
    rohs = data.get('rohs')
    coc = data.get('coc')

    print(f"Received data: {data}")

    if not offer_id:
        return jsonify({'success': False, 'error': 'No offer ID provided'}), 400

    try:
        with db_cursor(commit=True) as cur:
            supplier_result = _execute_with_cursor(
                cur,
                'SELECT currency, buffer FROM suppliers WHERE id = ?',
                (supplier_id,),
                fetch='one'
            )
            cost_currency = supplier_result['currency'] if supplier_result else 'EUR'
            supplier_buffer = supplier_result['buffer'] if supplier_result else 0

            currency_result = _execute_with_cursor(
                cur,
                'SELECT exchange_rate_to_eur FROM currencies WHERE currency_code = ?',
                (cost_currency,),
                fetch='one'
            )
            exchange_rate = currency_result['exchange_rate_to_eur'] if currency_result else 1

            base_cost = float(price) * exchange_rate
            lead_time = int(supplier_lead_time) + int(supplier_buffer)

            update_query = '''
                UPDATE rfq_lines 
                SET offer_id = ?, 
                    cost = ?, 
                    supplier_lead_time = ?, 
                    lead_time = ?, 
                    chosen_supplier = ?,
                    cost_currency = ?, 
                    base_cost = ?, 
                    status_id = 3,
                    offered_base_part_number = ?,
                    datecode = ?,
                    spq = ?,
                    packaging = ?,
                    rohs = ?,
                    coc = ?
                WHERE id = ?
            '''
            update_params = (
                offer_id,
                price,
                supplier_lead_time,
                lead_time,
                supplier_id,
                cost_currency,
                base_cost,
                offered_base_part_number,
                datecode,
                spq,
                packaging,
                rohs,
                coc,
                line_id
            )

            print(f"Update query: {update_query}")
            print(f"Update params: {update_params}")

            _execute_with_cursor(cur, update_query, update_params)

            updated_line = _execute_with_cursor(
                cur,
                '''
                SELECT l.*, 
                       s.name AS chosen_supplier_name, 
                       s.currency AS chosen_supplier_currency,
                       l.datecode,
                       l.spq,
                       l.packaging,
                       l.rohs,
                       l.coc
                FROM rfq_lines l
                LEFT JOIN suppliers s ON l.chosen_supplier = s.id
                WHERE l.id = ?
                ''',
                (line_id,),
                fetch='one'
            )

            return jsonify({
                'success': True,
                'chosen_supplier_name': updated_line['chosen_supplier_name'] if updated_line else None,
                'cost_currency': updated_line['cost_currency'] if updated_line else None,
                'base_cost': updated_line['base_cost'] if updated_line else None,
                'offer_id': updated_line['offer_id'] if updated_line else None,
                'status_id': updated_line['status_id'] if updated_line else None,
                'lead_time': updated_line['lead_time'] if updated_line else None,
                'supplier_lead_time': updated_line['supplier_lead_time'] if updated_line else None,
                'datecode': updated_line['datecode'] if updated_line else None,
                'spq': updated_line['spq'] if updated_line else None,
                'packaging': updated_line['packaging'] if updated_line else None,
                'rohs': updated_line['rohs'] if updated_line else None,
                'coc': updated_line['coc'] if updated_line else None
            })
    except Exception as e:
        print(f"Error in update_rfq_line_offer: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@rfqs_bp.route('/duplicate_line/<int:line_id>', methods=['POST'])
def duplicate_line(line_id):
    try:
        # Fetch the original line using only line_id
        original_line = get_rfq_line_by_id(line_id)
        if not original_line:
            return jsonify({'success': False, 'error': 'Original line not found'}), 404

        # Logic to generate the new line number
        original_line_number = float(original_line['line_number'])
        whole_part = math.floor(original_line_number)
        decimal_part = original_line_number - whole_part
        new_decimal_part = round(decimal_part + 0.1, 1)

        if new_decimal_part >= 1:
            new_line_number = f"{whole_part + 1}"
        else:
            new_line_number = f"{whole_part}.{int(new_decimal_part * 10)}"

        # Duplicate the original line
        new_line = dict(original_line)
        new_line['line_number'] = new_line_number
        if 'id' in new_line:
            del new_line['id']

        # Insert the new duplicated line into the database
        new_line_id = insert_rfq_line(**new_line)

        if new_line_id:
            new_line['id'] = new_line_id
            return jsonify({'success': True, 'new_line': new_line})
        else:
            return jsonify({'success': False, 'error': 'Failed to insert new line'}), 500

    except Exception as e:
        logging.exception(f"Error duplicating line: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@rfqs_bp.route('/update_rfq_line_fields', methods=['POST'])
def update_rfq_line_fields():
    data = request.json
    print(f"Received data: {data}")

    line_id = data.get('line_id')
    new_price = data.get('price')
    new_line_value = data.get('line_value')
    new_cost = data.get('cost')

    # New fields
    new_datecode = data.get('datecode')
    new_spq = data.get('spq')
    new_packaging = data.get('packaging')
    new_rohs = data.get('rohs')
    new_coc = data.get('coc')

    print(f"line_id: {line_id}, price: {new_price}, line_value: {new_line_value}, cost: {new_cost}")
    print(f"datecode: {new_datecode}, spq: {new_spq}, packaging: {new_packaging}, rohs: {new_rohs}, coc: {new_coc}")

    if line_id is None:
        error_msg = 'Missing line_id in the request.'
        print(f"Error: {error_msg}")
        return jsonify({'success': False, 'error': error_msg}), 400

    try:
        if new_price is not None:
            new_price = float(new_price)
        if new_line_value is not None:
            new_line_value = float(new_line_value)
        if new_cost is not None:
            new_cost = float(new_cost)
        if new_spq is not None:
            new_spq = int(new_spq)

        update_fields = []
        update_values = []

        if new_price is not None:
            update_fields.append('price = ?')
            update_values.append(new_price)
        if new_line_value is not None:
            update_fields.append('line_value = ?')
            update_values.append(new_line_value)
        if new_cost is not None:
            update_fields.append('cost = ?')
            update_values.append(new_cost)
        if new_datecode is not None:
            update_fields.append('datecode = ?')
            update_values.append(new_datecode)
        if new_spq is not None:
            update_fields.append('spq = ?')
            update_values.append(new_spq)
        if new_packaging is not None:
            update_fields.append('packaging = ?')
            update_values.append(new_packaging)
        if new_rohs is not None:
            update_fields.append('rohs = ?')
            update_values.append(new_rohs)
        if new_coc is not None:
            update_fields.append('coc = ?')
            update_values.append(new_coc)

        if not update_fields:
            return jsonify({'success': True, 'message': 'No fields to update'})

        query = f'''
            UPDATE rfq_lines
            SET {', '.join(update_fields)}
            WHERE id = ?
        '''
        update_values.append(line_id)

        print(f"Update query: {query}")
        print(f"Update values: {update_values}")

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, query, tuple(update_values))

        print("Database updated successfully")
        return jsonify({'success': True})

    except (TypeError, ValueError) as e:
        error_msg = f"Invalid value in the request: {e}"
        print(f"Error: {error_msg}")
        return jsonify({'success': False, 'error': error_msg}), 400
    except Exception as e:
        error_msg = f"Exception occurred: {e}"
        print(f"Error: {error_msg}")
        return jsonify({'success': False, 'error': error_msg}), 500

@rfqs_bp.route('/<int:rfq_id>/generate_quote', methods=['POST'])
def generate_quote(rfq_id):
    try:
        logging.info(f'Generating quote for RFQ ID: {rfq_id}')
        rfq_lines = get_rfq_lines_with_offers(rfq_id)
        logging.debug(f'RFQ Lines: {rfq_lines}')

        with db_cursor(commit=True) as cur:
            for line in rfq_lines:
                try:
                    price = float(line['price'])
                    if price > 0 and line.get('status_id') != 7:  # Ensure there's a price and it's not 'No Bid'
                        _execute_with_cursor(
                            cur,
                            'UPDATE rfq_lines SET status_id = 4 WHERE id = ?',
                            (line['id'],)
                        )
                        logging.info(f'Updated line ID {line["id"]} to Quoted status')
                except (ValueError, TypeError):
                    logging.warning(f'Invalid price for line ID {line["id"]}: {line["price"]}')

            rfq_lines = get_rfq_lines_with_offers(rfq_id)
            logging.debug(f'Updated RFQ Lines: {rfq_lines}')

            line_statuses = [line['status_id'] for line in rfq_lines]
            if all(status in [4, 7] for status in line_statuses):  # 4 = Quoted, 7 = No Bid
                new_status = "quoted"
            else:
                new_status = "partially quoted"

            _execute_with_cursor(
                cur,
                'UPDATE rfqs SET status = ? WHERE id = ?',
                (new_status, rfq_id)
            )
            logging.info(f'Updated RFQ ID {rfq_id} to {new_status} status')

        rfq = get_rfq_by_id(rfq_id)
        customer = get_customer_by_id(rfq['customer_id'])

        with db_cursor() as cur:
            for line in rfq_lines:
                logging.debug(f"Processing line ID {line['id']}")
                line['part_number'] = line['offered_part_number']
                logging.debug(f"Using offered part number: {line['part_number']}")

                system_row = _execute_with_cursor(
                    cur,
                    '''
                    SELECT system_part_number 
                    FROM part_numbers 
                    WHERE base_part_number = ?
                    ''',
                    (line['offered_base_part_number'],),
                    fetch='one'
                )
                line['system_part_number'] = _row_value(system_row, 'system_part_number') or ''

                customer_row = _execute_with_cursor(
                    cur,
                    '''
                    SELECT customer_part_number 
                    FROM customer_part_numbers 
                    WHERE base_part_number = ? AND customer_id = ?
                    ''',
                    (line['base_part_number'], rfq['customer_id']),
                    fetch='one'
                )
                line['customer_part_number'] = _row_value(customer_row, 'customer_part_number') or ''

        quotable_lines = [line for line in rfq_lines if line.get('status_id') != 7]
        logging.debug(f'Quotable lines being passed to template: {quotable_lines}')
        return render_template('quote.html', rfq=rfq, rfq_lines=quotable_lines, customer=customer)
    except Exception as e:
        logging.error(f'Error generating quote: {e}')
        flash(f'Error generating quote: {e}', 'error')
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

@rfqs_bp.route('/<int:rfq_id>/quote', methods=['GET'])
def quote(rfq_id):
    try:
        rfq = get_rfq_by_id(rfq_id)
        rfq_lines = get_rfq_lines_with_offers(rfq_id)
        customer = get_customer_by_id(rfq['customer_id'])

        return render_template('quote.html', rfq=rfq, rfq_lines=rfq_lines, customer=customer)
    except Exception as e:
        logging.error(f'Error loading quote page: {e}')
        flash(f'Error loading quote page: {e}', 'error')
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

@rfqs_bp.route('/<int:rfq_id>/update_rfq', methods=['POST'])
def update_rfq(rfq_id):
    # Retrieve all form data
    entered_date = request.form['entered_date']
    customer_id = request.form['customer_id']
    contact_id = request.form['contact_id']
    customer_ref = request.form['customer_ref']
    status = request.form['status']
    currency = request.form['currency']
    salesperson_id = request.form.get('salesperson_id')  # Get the salesperson_id from the form

    db_execute('''
        UPDATE rfqs SET entered_date = ?, customer_id = ?, contact_id = ?, customer_ref = ?, status = ?, currency = ?, salesperson_id = ?
        WHERE id = ?
    ''', (entered_date, customer_id, contact_id, customer_ref, status, currency, salesperson_id, rfq_id), commit=True)

    return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))


@rfqs_bp.route('/<int:rfq_id>/generate_cost_list')
def generate_cost_list(rfq_id):
    rfq = get_rfq_by_id(rfq_id)

    rfq_lines = db_execute('''
        SELECT 
            rl.*,
            pn_requested.part_number as requested_part_number,
            rl.offered_base_part_number,
            pn_offered.part_number as offered_part_number,
            ol.internal_notes as offer_internal_notes,  -- Renamed to avoid column name clash
            rl.internal_notes as line_internal_notes,   -- Get internal notes from rfq_lines
            s.name as supplier_name,
            s.currency,
            s.fornitore
        FROM rfq_lines rl
        LEFT JOIN part_numbers pn_requested ON rl.base_part_number = pn_requested.base_part_number
        LEFT JOIN part_numbers pn_offered ON rl.offered_base_part_number = pn_offered.base_part_number
        LEFT JOIN offers o ON rl.offer_id = o.id
        LEFT JOIN offer_lines ol ON o.id = ol.offer_id
        LEFT JOIN suppliers s ON o.supplier_id = s.id
        WHERE rl.rfq_id = ?
    ''', (rfq_id,), fetch='all') or []

    suppliers = get_suppliers()
    currencies = get_currencies()

    suppliers_dict = {supplier['id']: supplier for supplier in suppliers}
    currencies_dict = {currency['id']: currency for currency in currencies}

    updated_rfq_lines = []
    for line in rfq_lines:
        line_dict = dict(line)
        chosen_supplier_id = line_dict.get('chosen_supplier')

        if chosen_supplier_id in suppliers_dict:
            chosen_supplier = suppliers_dict[chosen_supplier_id]
            line_dict['supplier_name'] = chosen_supplier['name']
            currency_id = chosen_supplier['currency']
            currency = currencies_dict.get(currency_id, {})
            line_dict['currency'] = currency.get('currency_code', 'Unknown')
            line_dict['fornitore'] = chosen_supplier['fornitore']
        else:
            line_dict['supplier_name'] = 'Unknown'
            line_dict['currency'] = 'Unknown'
            line_dict['fornitore'] = 'Unknown'

        updated_rfq_lines.append(line_dict)

    return render_template('cost_list.html', rfq_id=rfq_id, rfq_lines=updated_rfq_lines)

# Define safe_int function
def safe_int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default




@rfqs_bp.route('/<int:rfq_id>/update_costs', methods=['POST'])
def update_costs(rfq_id):
    logging.debug(f"Updating costs for RFQ ID: {rfq_id}")

    try:
        rfq_lines = get_rfq_lines(rfq_id)

        with db_cursor(commit=True) as cur:
            for line in rfq_lines:
                line_id = line['id']
                try:
                    cost = float(request.form.get(f'cost_{line_id}', 0))
                    supplier_lead_time = safe_int(request.form.get(f'supplier_lead_time_{line_id}', 0))
                    margin = float(request.form.get(f'margin_{line_id}', 0))
                    note = request.form.get(f'note_{line_id}', '')
                    chosen_supplier = request.form.get(f'chosen_supplier_{line_id}', None)
                    suggested_suppliers = request.form.getlist(f'suggested_suppliers_{line_id}')

                    cost_currency = line['cost_currency']

                    logging.debug(
                        f"Received values for line {line_id} - Cost: {cost}, Margin: {margin}, Supplier Lead Time: {supplier_lead_time}, Cost Currency: {cost_currency}")

                    chosen_supplier = safe_int(chosen_supplier, None) if chosen_supplier else None
                    buffer = get_supplier_buffer(chosen_supplier) if chosen_supplier else 0

                    price = cost / (1 - (margin / 100))
                    quantity = line['quantity']
                    lead_time = supplier_lead_time + buffer
                    line_value = price * quantity

                    logging.debug(
                        f"Calculated values for line {line_id} - Price: {price}, Lead Time: {lead_time}, Line Value: {line_value}")

                    update_rfq_line_base_cost(cur, line_id, cost, cost_currency)

                    _execute_with_cursor(
                        cur,
                        '''
                        UPDATE rfq_lines
                        SET cost = ?, supplier_lead_time = ?, margin = ?, price = ?, lead_time = ?, line_value = ?, note = ?, chosen_supplier = ?, suggested_suppliers = ?
                        WHERE id = ?
                        ''',
                        (
                            cost, supplier_lead_time, margin, price, lead_time, line_value, note, chosen_supplier,
                            ','.join(map(str, suggested_suppliers)), line_id
                        )
                    )
                except Exception as e:
                    logging.error(f"Error updating line {line_id}: {e}")

        logging.info(f"Successfully updated costs for RFQ ID: {rfq_id}")
        return redirect(url_for('rfqs.input_costs', rfq_id=rfq_id))
    except Exception as e:
        logging.error(f"Error updating costs for RFQ ID {rfq_id}: {e}")
        return "An error occurred while processing the request.", 500


@rfqs_bp.route('/<int:rfq_id>/delete', methods=['POST'])
def delete_rfq(rfq_id):
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                'UPDATE rfqs SET status = ? WHERE id = ?',
                ('deleted', rfq_id)
            )
            _execute_with_cursor(
                cur,
                'UPDATE rfq_lines SET status_id = ? WHERE rfq_id = ?',
                (8, rfq_id)
            )

        flash('RFQ and associated lines deleted successfully', 'success')
    except Exception as e:
        logging.error(f'Error deleting RFQ {rfq_id}: {e}')
        flash(f'Error deleting RFQ: {str(e)}', 'error')

    return redirect(url_for('rfqs.rfqs'))


@rfqs_bp.route('/<int:rfq_id>/bulk_update_suppliers', methods=['POST'])
def bulk_update_suppliers(rfq_id):
    default_supplier = request.form['default_supplier']
    db_execute('UPDATE rfq_lines SET suggested_suppliers = ? WHERE rfq_id = ?', (default_supplier, rfq_id),
               commit=True)
    return redirect(url_for('rfqs.input_costs', rfq_id=rfq_id))

@rfqs_bp.route('/<int:rfq_id>/bulk_update_lead_time', methods=['POST'])
def bulk_update_lead_time(rfq_id):
    lead_time = request.form['bulk_lead_time']
    logging.debug(f"Received lead time to update: {lead_time} for RFQ ID: {rfq_id}")

    with db_cursor(commit=True) as cur:
        _execute_with_cursor(
            cur,
            'UPDATE rfq_lines SET lead_time = ? WHERE rfq_id = ?',
            (lead_time, rfq_id)
        )
    logging.debug("Lead time update committed to the database.")

    # Redirect to input_costs to reload updated data
    return redirect(url_for('rfqs.input_costs', rfq_id=rfq_id))

@rfqs_bp.route('/<int:rfq_id>/bulk_update_chosen_supplier', methods=['POST'])
def bulk_update_chosen_supplier(rfq_id):
    chosen_supplier = request.form['bulk_chosen_supplier']
    db_execute('UPDATE rfq_lines SET chosen_supplier = ? WHERE rfq_id = ?', (chosen_supplier, rfq_id), commit=True)
    return redirect(url_for('rfqs.input_costs', rfq_id=rfq_id))


@rfqs_bp.route('/<int:rfq_id>/input_costs', methods=['GET', 'POST'])
def input_costs(rfq_id):
    logging.debug(f"Function input_costs called with rfq_id: {rfq_id}")

    if request.method == 'POST':
        try:
            rfq_lines = get_rfq_lines(rfq_id)

            with db_cursor(commit=True) as cur:
                for line in rfq_lines:
                    line_id = line['id']
                    cost = float(request.form.get(f'cost_{line_id}', 0) or 0)
                    cost_currency = request.form.get(f'cost_currency_{line_id}', 'EUR')
                    supplier_lead_time = safe_int(request.form.get(f'supplier_lead_time_{line_id}', 0) or 0)
                    margin = float(request.form.get(f'margin_{line_id}', 0) or 0)
                    note = request.form.get(f'note_{line_id}', '')
                    chosen_supplier = request.form.get(f'chosen_supplier_{line_id}', None)
                    suggested_suppliers = request.form.getlist(f'suggested_suppliers_{line_id}')

                    chosen_supplier = safe_int(chosen_supplier, None) if chosen_supplier else None

                    price = cost * (1 + margin / 100)
                    quantity = line['quantity']
                    line_value = price * quantity
                    lead_time = supplier_lead_time

                    logging.debug(
                        f"Updating line {line_id} with cost {cost} {cost_currency}, supplier_lead_time {supplier_lead_time}, margin {margin}, price {price}, lead_time {lead_time}, line_value {line_value}, note {note}, chosen_supplier {chosen_supplier}, suggested_suppliers {suggested_suppliers}"
                    )

                    try:
                        update_rfq_line_base_cost(cur, line_id, cost, cost_currency)
                    except ValueError as e:
                        logging.error(f"Error updating base cost for line {line_id}: {e}")
                        flash(f"Error updating base cost for line {line_id}: {e}", "error")
                        continue

                    _execute_with_cursor(
                        cur,
                        '''
                        UPDATE rfq_lines
                        SET cost = ?, cost_currency = ?, supplier_lead_time = ?, margin = ?, price = ?, lead_time = ?, line_value = ?, note = ?, chosen_supplier = ?, suggested_suppliers = ?
                        WHERE id = ?
                        ''',
                        (
                            cost, cost_currency, supplier_lead_time, margin, price, lead_time, line_value, note, chosen_supplier,
                            ','.join(map(str, suggested_suppliers)), line_id
                        )
                    )

            logging.info(f"Successfully updated costs for RFQ ID: {rfq_id}")
            flash("Costs updated successfully", "success")
            return redirect(url_for('rfqs.input_costs', rfq_id=rfq_id))

        except Exception as e:
            logging.error(f"Error updating costs: {e}")
            flash("An error occurred while updating costs", "error")
            return redirect(url_for('rfqs.input_costs', rfq_id=rfq_id))

    try:
        rfq_lines = get_rfq_lines_with_offers(rfq_id)
        suppliers = get_suppliers()
        statuses = get_statuses()
        requisitions = get_requisitions()
        currencies = get_currencies()

        for line in rfq_lines:
            part_number_row = db_execute(
                'SELECT part_number FROM part_numbers WHERE base_part_number = ?',
                (line['base_part_number'],),
                fetch='one'
            )
            line['part_number'] = part_number_row['part_number'] if part_number_row else 'Unknown'

            if 'cost_currency' not in line or line['cost_currency'] is None:
                supplier = get_supplier_by_id(line['chosen_supplier']) if line['chosen_supplier'] else None
                line['cost_currency'] = supplier['currency'] if supplier else 3

        requisitioned_suppliers = {}
        for req in requisitions:
            rfq_line_id = req['rfq_line_id']
            supplier_id = req['supplier_id']
            if rfq_line_id not in requisitioned_suppliers:
                requisitioned_suppliers[rfq_line_id] = set()
            requisitioned_suppliers[rfq_line_id].add(supplier_id)
            logging.debug(f"Mapping RFQ line {rfq_line_id} to supplier {supplier_id}")

        logging.debug(f"Final requisitioned suppliers mapping: {requisitioned_suppliers}")

        for line in rfq_lines:
            line_id = line['id']
            base_part_number = line['base_part_number']

            logging.debug(f"Processing suppliers for line {line_id} with base part number {base_part_number}")

            if isinstance(line.get('suggested_suppliers'), str):
                line['suggested_suppliers'] = [safe_int(supplier_id) for supplier_id in
                                               line['suggested_suppliers'].split(',')]
            elif isinstance(line.get('suggested_suppliers'), list):
                line['suggested_suppliers'] = [safe_int(supplier_id) for supplier_id in line['suggested_suppliers']]
            else:
                line['suggested_suppliers'] = []

            logging.debug(f"Parsed suggested suppliers for line {line_id}: {line['suggested_suppliers']}")

            received_suppliers = requisitioned_suppliers.get(line_id, set())
            logging.debug(f"Received suppliers for line {line_id}: {received_suppliers}")

            line['received_requisitions'] = [supplier for supplier in suppliers if supplier['id'] in received_suppliers]

            line['not_received_requisitions'] = [supplier for supplier in suppliers if
                                                 supplier['id'] not in received_suppliers]
            logging.debug(
                f"Not received suppliers for line {line_id}: {[supplier['id'] for supplier in line['not_received_requisitions']]}"
            )

            sorted_suppliers = line['received_requisitions'] + [supplier for supplier in
                                                                line['not_received_requisitions'] if
                                                                supplier['id'] in line['suggested_suppliers']]
            sorted_suppliers += [supplier for supplier in line['not_received_requisitions'] if
                                 supplier['id'] not in line['suggested_suppliers']]
            line['sorted_suppliers'] = sorted_suppliers

            logging.debug(
                f"Final sorted suppliers for line {line_id}: {[supplier['id'] for supplier in line['sorted_suppliers']]}"
            )

        for line in rfq_lines[:5]:
            logging.debug(f"Line ID: {line['id']}, Base Part Number: {line['base_part_number']}, "
                          f"Suggested Suppliers: {line['suggested_suppliers']}, "
                          f"Received Requisitions Count: {len(line['received_requisitions'])}, "
                          f"Not Received Requisitions Count: {len(line['not_received_requisitions'])}")

        first_line = rfq_lines[0] if rfq_lines else None
        if first_line:
            logging.debug(f"Line ID: {first_line['id']}, Base Part Number: {first_line['base_part_number']}")
            logging.debug(
                f"Received Requisition Supplier IDs: {[supplier['id'] for supplier in first_line['received_requisitions']]}"
            )
            logging.debug(
                f"Not Received Requisition Supplier IDs: {[supplier['id'] for supplier in first_line['not_received_requisitions']]}"
            )

        try:
            return render_template('input_costs.html',
                                   rfq_id=rfq_id,
                                   rfq_lines=rfq_lines,
                                   suppliers=suppliers,
                                   statuses=statuses,
                                   currencies=currencies,
                                   breadcrumbs=[
                                       ('Home', url_for('index')),
                                       ('RFQs', url_for('rfqs.rfqs')),
                                       (f'Edit RFQ #{rfq_id}', url_for('rfqs.edit_rfq', rfq_id=rfq_id)),
                                       ('Input Costs', '#')
                                   ])
        except Exception as e:
            logging.error(f"Error encountered in input_costs with rfq_id {rfq_id}: {e}")
            flash("An error occurred while loading the page", "error")
            return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

    except Exception as e:
        logging.error(f"Error while processing: {e}")
        flash("An error occurred", "error")
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))


def get_supplier_buffer(supplier_id):
    try:
        query = 'SELECT buffer FROM suppliers WHERE id = ?'
        result = db_execute(query, (supplier_id,), fetch='one')
        if result:
            buffer = int(result['buffer'])  # Convert buffer explicitly to integer
            logging.debug(f"Buffer retrieved for supplier {supplier_id}: {buffer}")
            return buffer
        logging.debug(f"No buffer found for supplier {supplier_id}. Returning 0.")
        return 0
    except Exception as e:
        logging.error(f"Error fetching buffer for supplier {supplier_id}: {e}")
        return 0

@rfqs_bp.route('/view_email/<int:rfq_id>', methods=['GET'])
def view_email(rfq_id):
    # Retrieve the RFQ details, including the email content
    rfq = get_rfq_by_id(rfq_id)

    if not rfq:
        flash('RFQ not found!', 'error')
        return redirect(url_for('rfqs.rfqs'))

    # Extract email details from the RFQ (assuming email content is in rfq['email'])
    subject = rfq.get('subject', 'No Subject')
    sender = rfq.get('sender', 'Unknown Sender')
    received_time = rfq.get('received_time', 'Unknown Time')
    body = rfq.get('email')  # Assuming the email body is in this field
    html_body = rfq.get('email')  # Assuming HTML content is stored

    # Render the full email view
    return render_template('view_email.html',
                           subject=subject,
                           sender=sender,
                           received_time=received_time,
                           body=body,
                           html_body=html_body)


@rfqs_bp.route('/view_email_frame/<int:rfq_id>', methods=['GET'])
def view_email_frame(rfq_id):
    rfq = get_rfq_by_id(rfq_id)
    if rfq and 'email' in rfq:
        email_content = rfq['email']

        # Remove all <img> tags
        email_content = re.sub(r'<img[^>]*>', '', email_content)

        return email_content, 200, {'Content-Type': 'text/html'}
    else:
        return "Email content not available.", 404


@rfqs_bp.route('/<int:rfq_id>/upload_email', methods=['POST'])
def upload_email(rfq_id):
    rfq = get_rfq_by_id(rfq_id)
    if not rfq:
        flash('RFQ not found!', 'error')
        return redirect(url_for('rfqs.rfqs'))

    # Check if an email file is present in the request
    if 'email_file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

    file = request.files['email_file']
    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

        try:
            # Save the file (same as in the upload route)
            file.save(file_path)

            # Process the .msg file as we did in the new upload logic
            msg = extract_msg.Message(file_path)

            # Extract email content (HTML body or plain text)
            email_content = msg.htmlBody if msg.htmlBody else msg.body

            # Ensure email content is in a proper format for display
            if msg.htmlBody:
                # Use the HTML body as-is
                email_content = msg.htmlBody.decode('utf-8', errors='ignore') if isinstance(msg.htmlBody, bytes) else msg.htmlBody
            elif msg.body:
                # Convert plain text to HTML for consistency
                email_content = msg.body.replace('\n', '<br>')

            returning_clause = ' RETURNING id' if _using_postgres() else ''
            insert_query = f'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?){returning_clause}'
            with db_cursor(commit=True) as cur:
                insert_row = _execute_with_cursor(cur, insert_query, (filename, file_path, datetime.now()),
                                                  fetch='one' if _using_postgres() else None)
                file_id = _get_inserted_id(insert_row, cur)

                _execute_with_cursor(
                    cur,
                    'INSERT INTO rfq_files (rfq_id, file_id) VALUES (?, ?)',
                    (rfq_id, file_id)
                )

                _execute_with_cursor(
                    cur,
                    'UPDATE rfqs SET email = ? WHERE id = ?',
                    (email_content, rfq_id)
                )

            flash('Email uploaded and processed successfully!', 'success')

        except Exception as e:
            flash(f'Error processing email: {str(e)}', 'error')
    else:
        flash('Invalid file type. Only .eml and .msg files are allowed.', 'error')

    return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))


def decode_content(part):
    content = part.get_payload(decode=True)
    if content is None:
        return None

    print("Raw content:", content[:100])  # Print the first 100 bytes of the raw content for debugging

    charset = part.get_content_charset()
    if charset is None:
        # Try to detect the charset
        detected = chardet.detect(content)
        charset = detected['encoding']
        print("Detected charset:", charset)

    if charset is not None:
        try:
            decoded_content = content.decode(charset)
            print("Decoded content with detected charset:", decoded_content[:100])  # Print the first 100 chars
            return decoded_content
        except UnicodeDecodeError as e:
            print(f"UnicodeDecodeError with charset {charset}: {e}")

    # If charset detection or decoding fails, try common encodings
    for encoding in ['utf-8', 'iso-8859-1', 'windows-1252', 'ascii']:
        try:
            decoded_content = content.decode(encoding)
            print(f"Decoded content with {encoding}:", decoded_content[:100])  # Print the first 100 chars
            return decoded_content
        except UnicodeDecodeError as e:
            print(f"UnicodeDecodeError with charset {encoding}: {e}")

    # If all else fails, decode with 'replace' error handler
    decoded_content = content.decode('utf-8', errors='replace')
    print("Decoded content with utf-8 replace:", decoded_content[:100])  # Print the first 100 chars
    return decoded_content

def format_plain_text(plain_content):
    lines = plain_content.split('\n')
    formatted_content = ''
    for line in lines:
        if line.strip() == '':
            formatted_content += '<br>'
        else:
            formatted_content += f'<p>{line.strip()}</p>'
    return formatted_content

def format_plain_text(plain_content):
    lines = plain_content.split('\n')
    formatted_content = ''
    in_list = False
    for line in lines:
        stripped_line = line.strip()
        if stripped_line == '':
            if in_list:
                formatted_content += '</ul>'
                in_list = False
            formatted_content += '<br>'
        elif stripped_line.startswith(('- ', '* ', '• ')):
            if not in_list:
                formatted_content += '<ul>'
                in_list = True
            formatted_content += f'<li>{stripped_line[2:]}</li>'
        else:
            if in_list:
                formatted_content += '</ul>'
                in_list = False
            formatted_content += f'<p>{stripped_line}</p>'
    if in_list:
        formatted_content += '</ul>'
    return formatted_content


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'eml', 'msg'}

def clean_html(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    for script in soup(["script", "style", "head"]):
        script.decompose()
    cleaned_html = soup.prettify()
    allowed_tags = list(bleach.ALLOWED_TAGS) + ['p', 'br', 'div', 'span', 'table', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li']
    allowed_attributes = {**bleach.ALLOWED_ATTRIBUTES, 'img': ['src', 'alt'], 'a': ['href', 'title']}
    return bleach.clean(cleaned_html, tags=allowed_tags, attributes=allowed_attributes, strip=True)

@rfqs_bp.route('/<int:rfq_id>/upload_file', methods=['POST'])
def upload_file(rfq_id):
    if 'file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

    file = request.files['file']
    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        returning_clause = ' RETURNING id' if _using_postgres() else ''
        insert_query = f'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?){returning_clause}'
        with db_cursor(commit=True) as cur:
            insert_row = _execute_with_cursor(
                cur,
                insert_query,
                (filename, filepath, datetime.now()),
                fetch='one' if _using_postgres() else None
            )
            file_id = _get_inserted_id(insert_row, cur)

            _execute_with_cursor(
                cur,
                'INSERT INTO rfq_files (rfq_id, file_id) VALUES (?, ?)',
                (rfq_id, file_id)
            )

        flash('File successfully uploaded and associated with RFQ', 'success')
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

@rfqs_bp.route('/<int:rfq_id>/manage_files', methods=['GET', 'POST'])
def manage_files(rfq_id):
    rfq = get_rfq_by_id(rfq_id)
    if not rfq:
        flash('RFQ not found!', 'error')
        return redirect(url_for('rfqs.rfqs'))

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part', 'error')
            return redirect(url_for('rfqs.manage_files', rfq_id=rfq_id))

        file = request.files['file']
        if file.filename == '':
            flash('No selected file', 'error')
            return redirect(url_for('rfqs.manage_files', rfq_id=rfq_id))

        filename = secure_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        upload_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        insert_file(rfq_id, filename, filepath, upload_date)

        flash('File uploaded successfully!', 'success')
        return redirect(url_for('rfqs.manage_files', rfq_id=rfq_id))

    files = get_files_by_rfq(rfq_id)  # Get files related to the RFQ

    return render_template('manage_files.html', rfq=rfq, files=files)

def get_salespeople():
    return db_execute('SELECT id, name FROM salespeople', fetch='all') or []


@rfqs_bp.route('/add_part_number', methods=['POST'])
def add_part_number():
    data = request.json
    part_number = data['part_number']
    system_part_number = data.get('system_part_number')
    base_part_number = create_base_part_number(part_number)

    part = session.query(PartNumber).filter_by(base_part_number=base_part_number).first()
    if not part:
        new_part = PartNumber(base_part_number=base_part_number, part_number=part_number,
                              system_part_number=system_part_number)
        session.add(new_part)
        session.commit()
        return jsonify({"message": "Part number added successfully"}), 201
    else:
        return jsonify({"message": "Part number already exists"}), 200

def get_files_by_rfq(rfq_id):
    files = db_execute('''
        SELECT f.*
        FROM files f
        JOIN rfq_files rf ON f.id = rf.file_id
        WHERE rf.rfq_id = ?
    ''', (rfq_id,), fetch='all') or []
    return [dict(file) for file in files]
# Ensure other routes and logic remain as they are in your existing `routes/rfqs.py`


def update_rfq_status_based_on_lines(rfq_id):
    lines = db_execute('SELECT status_id FROM rfq_lines WHERE rfq_id = ?', (rfq_id,), fetch='all') or []
    if not lines:
        return

    status_ids = [line['status_id'] for line in lines]

    if all(status_id in [3, 7] for status_id in status_ids):
        new_status = 'quoted'  # Fully Quoted
    elif any(status_id == 3 for status_id in status_ids):
        new_status = 'Partially Quoted'  # Partially Quoted
    else:
        new_status = 'Pending'  # Default status

    update_rfq_status(rfq_id, new_status)


@rfqs_bp.route('/update_rfq_status', methods=['POST'])
def update_rfq_status_route():
    data = request.get_json()
    rfq_id = data.get('rfq_id')
    status = data.get('status')

    try:
        update_rfq_status(rfq_id, status)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def update_rfq_status(rfq_id, status_text):
    db_execute('UPDATE rfqs SET status = ? WHERE id = ?', (status_text, rfq_id), commit=True)

@rfqs_bp.route('/mark_as_no_bid', methods=['POST'])
def mark_as_no_bid():
    data = request.get_json()
    line_id = data.get('line_id')

    # Update the line status to "No Bid"
    db_execute('UPDATE rfq_lines SET status_id = 7 WHERE id = ?', (line_id,), commit=True)

    # Update the RFQ status based on the line statuses
    line = get_rfq_line_by_id(line_id)
    update_rfq_status_based_on_lines(line['rfq_id'])

    return jsonify({'success': True})


@rfqs_bp.route('/lines')
@rfqs_bp.route('/lines/<int:rfq_id>')
def view_all_rfq_lines(rfq_id=None):
    if rfq_id:
        rfq = get_rfq_by_id(rfq_id)
        if not rfq:
            flash('RFQ not found', 'error')
            return redirect(url_for('rfqs.rfqs'))
        if rfq.get('status') == 'deleted':
            flash('This RFQ has been deleted', 'warning')
            return redirect(url_for('rfqs.rfqs'))
        rfq_lines = get_rfq_lines(rfq_id)
    else:
        rfq = None
        rfq_lines = get_all_rfq_lines()

    all_rfqs = get_all_rfqs()  # Fetch all RFQs

    logging.debug(f"Number of RFQ lines: {len(rfq_lines)}")
    if rfq_lines:
        logging.debug(f"Sample RFQ line: {rfq_lines[0]}")

    statuses = get_all_statuses()
    suppliers = get_suppliers()

    # Create a mapping of status_id to status name for RFQ line statuses
    status_map = {status['id']: status['status'] for status in statuses}

    # Initialize grouped_lines with known RFQ line statuses
    grouped_lines = {status['status']: [] for status in statuses}

    for line in rfq_lines:
        status_id = line['status_id']
        status_name = status_map.get(status_id, 'Unknown')
        if status_name not in grouped_lines:
            grouped_lines[status_name] = []
        grouped_lines[status_name].append(line)

    for status, lines in grouped_lines.items():
        logging.debug(f"RFQ Line Status '{status}': {len(lines)} lines")

    breadcrumbs = [
        ('Home', url_for('index')),
        ('RFQs', url_for('rfqs.rfqs')),
        ('All RFQ Lines' if rfq_id is None else f'RFQ #{rfq_id} Lines', '#')
    ]

    return render_template('rfq_lines_view.html',
                           rfq=rfq,
                           grouped_lines=grouped_lines,
                           statuses=statuses,
                           breadcrumbs=breadcrumbs,
                           suppliers=suppliers,
                           rfq_lines=rfq_lines,
                           all_rfqs=all_rfqs)  # Pass all RFQs to the template


@rfqs_bp.route('/generate_single_requisition', methods=['POST'])
def generate_single_requisition():
    data = request.json
    line_id = data.get('line_id')
    supplier_id = data.get('supplier_id')
    top_level_requisition_id = data.get('top_level_requisition_id')

    try:
        line = get_rfq_line_by_id(line_id)
        rfq_id = line['rfq_id']
        rfq = get_rfq_by_id(rfq_id)

        returning_clause = ' RETURNING id' if _using_postgres() else ''
        insert_query = f'''
            INSERT INTO requisitions (rfq_id, supplier_id, date, base_part_number, quantity, rfq_line_id)
            VALUES (?, ?, ?, ?, ?, ?){returning_clause}
        '''

        with db_cursor(commit=True) as cur:
            requisition = _execute_with_cursor(
                cur,
                '''
                SELECT id FROM requisitions
                WHERE rfq_id = ? AND supplier_id = ?
                ''',
                (rfq_id, supplier_id),
                fetch='one'
            )

            if requisition:
                requisition_id = requisition['id']
            else:
                insert_row = _execute_with_cursor(
                    cur,
                    insert_query,
                    (rfq_id, supplier_id, datetime.now().strftime('%Y-%m-%d'), line['base_part_number'],
                     line['quantity'], line_id),
                    fetch='one' if _using_postgres() else None
                )
                requisition_id = _get_inserted_id(insert_row, cur)

                # Link the requisition to the top-level requisition if provided
                if top_level_requisition_id:
                    add_requisition_to_top_level(top_level_requisition_id, requisition_id)

        supplier = get_supplier_by_id(supplier_id)
        part_number = get_part_number_by_base(line['base_part_number'])

        return jsonify({
            'success': True,
            'requisition_id': requisition_id,
            'rfq_id': rfq_id,
            'supplier_name': supplier['name'],
            'contact_name': supplier['contact_name'],
            'contact_email': supplier['contact_email'],
            'part_number': part_number,
            'quantity': line['quantity'],
            'note': line.get('note', '')
        })
    except Exception as e:
        logging.error(f"Error in generate_single_requisition: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@rfqs_bp.route('/<int:rfq_id>/line/<int:line_id>/edit', methods=['GET', 'POST'])
def edit_rfq_line(rfq_id, line_id):
    rfq = get_rfq_by_id(rfq_id)
    line = get_rfq_line_by_id(line_id)

    if not rfq or not line:
        flash('RFQ or RFQ line not found', 'error')
        return redirect(url_for('rfqs.rfqs'))

    if request.method == 'POST':
        # Update the RFQ line with the form data
        line_number = request.form.get('line_number')
        part_number = request.form.get('part_number')
        quantity = request.form.get('quantity')
        manufacturer = request.form.get('manufacturer')
        cost = request.form.get('cost')
        price = request.form.get('price')
        lead_time = request.form.get('lead_time')

        # You might need to add more fields depending on your RFQ line structure

        try:
            update_rfq_line(line_id, line_number, part_number, quantity, manufacturer, cost, price, lead_time)
            flash('RFQ line updated successfully', 'success')
            return redirect(url_for('rfqs.view_all_rfq_lines', rfq_id=rfq_id))
        except Exception as e:
            flash(f'Error updating RFQ line: {str(e)}', 'error')

    # For GET request, render the edit form
    manufacturers = get_all_manufacturers()  # You'll need to implement this function
    return render_template('edit_rfq_line.html', rfq=rfq, line=line, manufacturers=manufacturers)


def create_top_level_requisition():
    reference = f"TLR-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    returning_clause = ' RETURNING id' if _using_postgres() else ''
    insert_query = f'''
        INSERT INTO top_level_requisitions (reference, created_at)
        VALUES (?, ?){returning_clause}
    '''

    with db_cursor(commit=True) as cur:
        insert_row = _execute_with_cursor(
            cur,
            insert_query,
            (reference, created_at),
            fetch='one' if _using_postgres() else None
        )
        top_level_requisition_id = _get_inserted_id(insert_row, cur)

    return top_level_requisition_id, reference

def add_requisition_to_top_level(top_level_requisition_id, requisition_id, max_retries=5, delay=0.1):
    insert_query = '''
        INSERT INTO requisition_references (top_level_requisition_id, requisition_id)
        VALUES (?, ?)
    '''
    last_error = None
    for attempt in range(max_retries):
        try:
            with db_cursor(commit=True) as cur:
                _execute_with_cursor(cur, insert_query, (top_level_requisition_id, requisition_id))
            return
        except Exception as exc:
            last_error = exc
            is_locked = 'database is locked' in str(exc).lower()
            if is_locked and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2  # Exponential backoff
                continue
            raise
    raise RuntimeError("Database locked after maximum retries") from last_error

@rfqs_bp.route('/create_top_level_requisition', methods=['POST'])
def create_top_level_requisition_route():
    try:
        top_level_requisition_id, reference = create_top_level_requisition()
        return jsonify({
            'success': True,
            'top_level_requisition_id': top_level_requisition_id,
            'reference': reference
        })
    except Exception as e:
        logging.error("Error in create_top_level_requisition", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@rfqs_bp.route('/generate_bulk_requisitions', methods=['POST'])
def generate_bulk_requisitions():
    data = request.json
    logging.debug("Received data for bulk requisitions: %s", data)

    supplier_id = data.get('supplier_id')
    line_data = data.get('line_data')  # Changed from rfq_lines to line_data
    top_level_requisition_id = data.get('top_level_requisition_id')

    logging.debug(f"supplier_id: {supplier_id}")
    logging.debug(f"top_level_requisition_id: {top_level_requisition_id}")
    logging.debug(f"Number of lines: {len(line_data) if line_data else 0}")

    if not supplier_id or not line_data or not top_level_requisition_id:
        return jsonify({'success': False, 'error': 'Missing required data'}), 400

    # Assuming status_id 2 represents "sent to supplier"
    SENT_TO_SUPPLIER_STATUS_ID = 2
    returning_clause = ' RETURNING id' if _using_postgres() else ''
    insert_query = f'''
        INSERT INTO requisitions (rfq_id, supplier_id, date, base_part_number, quantity, rfq_line_id)
        VALUES (?, ?, ?, ?, ?, ?){returning_clause}
    '''

    try:
        with db_cursor(commit=True) as cur:
            for line in line_data:
                line_id = line.get('line_id')
                rfq_id = line.get('rfq_id')

                if not rfq_id:
                    raise ValueError(f"RFQ ID is missing for line ID: {line_id}")

                line_details = get_rfq_line_by_id(line_id)
                logging.debug(f"Processing Line ID: {line_id}, RFQ ID: {rfq_id}, Line Data: {line_details}")

                if not line_details:
                    raise ValueError(f"Line data not found for Line ID: {line_id}")

                insert_row = _execute_with_cursor(
                    cur,
                    insert_query,
                    (rfq_id, supplier_id, datetime.now().strftime('%Y-%m-%d'), line_details['base_part_number'],
                     line_details['quantity'], line_id),
                    fetch='one' if _using_postgres() else None
                )
                requisition_id = _get_inserted_id(insert_row, cur)
                logging.debug(f"Inserted requisition with ID: {requisition_id}")

                _execute_with_cursor(
                    cur,
                    '''
                    UPDATE rfq_lines
                    SET status_id = ?
                    WHERE id = ?
                    ''',
                    (SENT_TO_SUPPLIER_STATUS_ID, line_id)
                )
                logging.debug(f"Updated status for Line ID: {line_id}")

                if top_level_requisition_id:
                    _execute_with_cursor(
                        cur,
                        '''
                        INSERT INTO requisition_references (top_level_requisition_id, requisition_id)
                        VALUES (?, ?)
                        ''',
                        (top_level_requisition_id, requisition_id)
                    )
                    logging.debug(f"Inserted requisition reference for top-level requisition ID: "
                                  f"{top_level_requisition_id}")

        logging.debug("Bulk requisition transaction committed successfully")
        return jsonify({'success': True})
    except ValueError as ve:
        logging.error(f"Validation error in generate_bulk_requisitions: {ve}")
        return jsonify({'success': False, 'error': str(ve)})
    except Exception as e:
        logging.error(f"Error in generate_bulk_requisitions: {e}")
        return jsonify({'success': False, 'error': str(e)})

@rfqs_bp.route('/get_requisition_details', methods=['GET'])
def get_requisition_details():
    top_level_requisition_id = request.args.get('top_level_requisition_id')
    supplier_id = request.args.get('supplier_id')

    try:
        rows = db_execute('''
            SELECT
                r.id as requisition_id,
                s.name as supplier_name,
                s.contact_name,
                s.contact_email,
                rl.base_part_number,
                rl.quantity
            FROM requisitions r
            JOIN suppliers s ON r.supplier_id = s.id
            JOIN rfq_lines rl ON r.rfq_line_id = rl.id
            JOIN requisition_references rr ON r.id = rr.requisition_id
            WHERE rr.top_level_requisition_id = ? AND r.supplier_id = ?
        ''', (top_level_requisition_id, supplier_id), fetch='all') or []

        if not rows:
            return jsonify({'success': False, 'error': 'Requisition details not found'})

        supplier_info = rows[0]
        part_numbers = [row['base_part_number'] for row in rows]
        quantities = [
            str(row['quantity']) if row['quantity'] is not None else '0'
            for row in rows
        ]
        part_number_quantities = defaultdict(set)

        for part, qty in zip(part_numbers, quantities):
            if qty:
                part_number_quantities[part].add(int(qty))

        part_number_quantities_list = [
            (part, sorted(qtys)) for part, qtys in part_number_quantities.items()
        ]

        return jsonify({
            'success': True,
            'top_level_requisition_id': top_level_requisition_id,
            'supplier_name': supplier_info['supplier_name'],
            'contact_name': supplier_info['contact_name'],
            'contact_email': supplier_info['contact_email'],
            'part_numbers': part_numbers,
            'quantities': quantities,
            'part_number_quantities': part_number_quantities_list,
        })

    except Exception as e:
        print(f"Error in get_requisition_details: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@rfqs_bp.route('/<int:rfq_id>/generate_requisitions_ajax', methods=['POST'])
def generate_requisitions_ajax(rfq_id):
    max_retries = 5
    delay = 0.1

    for attempt in range(max_retries):
        try:
            top_level_reference = f"TLR-{rfq_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            returning_clause = ' RETURNING id' if _using_postgres() else ''
            insert_top_query = f'''
                INSERT INTO top_level_requisitions (created_at)
                VALUES (?){returning_clause}
            '''
            insert_requisition_query = f'''
                INSERT INTO requisitions (rfq_id, supplier_id, date, base_part_number, quantity, rfq_line_id)
                VALUES (?, ?, ?, ?, ?, ?){returning_clause}
            '''
            requisition_reference_query = '''
                INSERT INTO requisition_references (top_level_requisition_id, requisition_id)
                VALUES (?, ?)
            '''
            update_rfq_line_status_query = '''
                UPDATE rfq_lines
                SET status_id = ?
                WHERE id = ?
            '''

            rfq_lines = get_rfq_lines(rfq_id)
            suppliers = get_suppliers()
            supplier_emails = {}

            with db_cursor(commit=True) as cur:
                insert_top_row = _execute_with_cursor(
                    cur,
                    insert_top_query,
                    (created_at,),
                    fetch='one' if _using_postgres() else None
                )
                top_level_requisition_id = _get_inserted_id(insert_top_row, cur)

                for line in rfq_lines:
                    suggested_suppliers = line['suggested_suppliers'].split(',') if line['suggested_suppliers'] else []
                    base_part_number = line['base_part_number']
                    part_number = line['part_number']
                    processed_suppliers = set()

                    for supplier_id in suggested_suppliers:
                        supplier_id = int(supplier_id)
                        if supplier_id in processed_suppliers:
                            continue
                        processed_suppliers.add(supplier_id)

                        insert_row = _execute_with_cursor(
                            cur,
                            insert_requisition_query,
                            (
                                rfq_id,
                                supplier_id,
                                datetime.now().strftime('%Y-%m-%d'),
                                base_part_number,
                                line['quantity'],
                                line['id']
                            ),
                            fetch='one' if _using_postgres() else None
                        )
                        requisition_id = _get_inserted_id(insert_row, cur)

                        _execute_with_cursor(
                            cur,
                            requisition_reference_query,
                            (top_level_requisition_id, requisition_id)
                        )

                        _execute_with_cursor(
                            cur,
                            update_rfq_line_status_query,
                            (2, line['id'])
                        )

                        supplier = next((s for s in suppliers if s['id'] == supplier_id), None)
                        if supplier:
                            supplier_entry = supplier_emails.setdefault(supplier['name'], {
                                'contact_name': supplier['contact_name'],
                                'contact_email': supplier['contact_email'],
                                'lines': []
                            })
                            supplier_entry['lines'].append({
                                'part_number': part_number,
                                'quantity': line['quantity']
                            })

                    remaining_suppliers = [s for s in suggested_suppliers if int(s) not in processed_suppliers]
                    new_suggested_suppliers = ','.join(remaining_suppliers) if remaining_suppliers else None

                    _execute_with_cursor(
                        cur,
                        '''
                        UPDATE rfq_lines
                        SET suggested_suppliers = ?
                        WHERE id = ?
                        ''',
                        (new_suggested_suppliers, line['id'])
                    )

                status_sent_to_sourcing = "sent to sourcing"
                _execute_with_cursor(
                    cur,
                    'UPDATE rfqs SET status = ? WHERE id = ?',
                    (status_sent_to_sourcing, rfq_id)
                )

            return jsonify({
                'success': True,
                'message': 'Requisitions generated successfully and RFQ status updated to Sent to Sourcing!',
                'supplier_emails': supplier_emails,
                'top_level_reference': top_level_reference
            })

        except Exception as e:
            is_locked = 'database is locked' in str(e).lower()
            if is_locked and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2  # Exponential backoff
                continue
            logging.error("Error in generate_requisitions_ajax", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({'success': False, 'error': 'Database locked after maximum retries'}), 500



@rfqs_bp.route('/get_currencies', methods=['GET'])
def api_get_currencies():
    currencies = get_currencies()
    return jsonify(currencies)

@rfqs_bp.route('/test', methods=['GET'])
def test_route():
    return "RFQ blueprint is working!"


@rfqs_bp.route('/get_rfq_line_currency/<int:line_id>', methods=['GET'])
def get_rfq_line_currency_route(line_id):
    logging.debug(f"Entered get_rfq_line_currency_route with line_id: {line_id}")
    try:
        logging.debug("Attempting to call get_rfq_line_currency")
        result = get_rfq_line_currency(line_id)
        logging.debug(f"Result from get_rfq_line_currency: {result}")
        return jsonify(result)
    except Exception as e:
        logging.error(f"Error in get_rfq_line_currency_route: {str(e)}")
        logging.error(f"Error type: {type(e).__name__}")
        logging.error(f"Error traceback: {traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500

def get_rfq_line_currency(line_id):
    """Get currency information for an RFQ line including RFQ currency"""
    logging.debug(f"Entered get_rfq_line_currency with line_id: {line_id}")
    try:
        # First get the line details
        line_result = db_execute('''
            SELECT rl.cost_currency, rl.rfq_id
            FROM rfq_lines rl
            WHERE rl.id = ?
        ''', (line_id,), fetch='one')

        if not line_result:
            raise ValueError(f"RFQ line with ID {line_id} not found")

        cost_currency = line_result['cost_currency']
        rfq_id = line_result['rfq_id']

        # Get the RFQ currency
        rfq_result = db_execute('SELECT currency FROM rfqs WHERE id = ?', (rfq_id,), fetch='one')

        if not rfq_result:
            raise ValueError(f"RFQ with ID {rfq_id} not found")

        rfq_currency_id = rfq_result['currency']

        # Get exchange rates for both currencies
        currencies = db_execute('''
            SELECT id, currency_code, exchange_rate_to_eur 
            FROM currencies 
            WHERE id IN (?, ?)
        ''', (cost_currency, rfq_currency_id), fetch='all') or []

        currencies_dict = {currency['id']: currency for currency in currencies}

        # Extract currency data
        cost_currency_data = currencies_dict.get(cost_currency, {})
        rfq_currency_data = currencies_dict.get(rfq_currency_id, {})

        exchange_rate_to_eur = cost_currency_data.get('exchange_rate_to_eur', 1)
        rfq_exchange_rate_to_eur = rfq_currency_data.get('exchange_rate_to_eur', 1)

        logging.debug(f"Currency data: cost_currency={cost_currency}, exchange_rate={exchange_rate_to_eur}, "
                      f"rfq_currency={rfq_currency_id}, rfq_exchange_rate={rfq_exchange_rate_to_eur}")

        return {
            "success": True,
            "cost_currency": cost_currency,
            "exchange_rate_to_eur": exchange_rate_to_eur,
            "rfq_currency_id": rfq_currency_id,
            "rfq_exchange_rate_to_eur": rfq_exchange_rate_to_eur,
            "cost_currency_code": cost_currency_data.get('currency_code', 'Unknown'),
            "rfq_currency_code": rfq_currency_data.get('currency_code', 'Unknown')
        }
    except Exception as e:
        logging.error(f"Error in get_rfq_line_currency: {str(e)}")
        return {"success": False, "error": str(e)}

@rfqs_bp.route('/update_rfq_currency/<int:rfq_id>', methods=['POST'])
def update_rfq_currency(rfq_id):
    """Update the RFQ currency and return success/failure"""
    data = request.json
    currency_id = data.get('currency_id')

    if not currency_id:
        return jsonify({"success": False, "error": "No currency_id provided"}), 400

    try:
        db_execute('UPDATE rfqs SET currency = ? WHERE id = ?', (currency_id, rfq_id), commit=True)
        return jsonify({"success": True})
    except Exception as e:
        logging.error(f"Error updating RFQ currency: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@rfqs_bp.route('/get_currency_exchange_rate/<int:currency_id>', methods=['GET'])
def get_currency_exchange_rate_route(currency_id):
    """Get the exchange rate for a specific currency"""
    try:
        result = db_execute(
            'SELECT exchange_rate_to_eur FROM currencies WHERE id = ?',
            (currency_id,),
            fetch='one'
        )

        if not result:
            return jsonify({"success": False, "error": "Currency not found"}), 404

        return jsonify({
            "success": True,
            "exchange_rate_to_eur": result['exchange_rate_to_eur']
        })
    except Exception as e:
        logging.error(f"Error getting currency exchange rate: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@rfqs_bp.route('/update_base_cost', methods=['POST'])
def update_base_cost():
    data = request.json
    logging.debug(f"Received data in update_base_cost: {data}")

    line_id = data.get('line_id')
    cost = data.get('base_cost')  # This is actually the original cost, not base_cost
    cost_currency = data.get('cost_currency')
    exchange_rate = data.get('exchange_rate_to_eur')
    rfq_currency_id = data.get('rfq_currency_id')
    rfq_exchange_rate = data.get('rfq_exchange_rate_to_eur')

    logging.debug(
        f"Parsed values: line_id={line_id}, cost={cost}, cost_currency={cost_currency}, exchange_rate={exchange_rate}, "
        f"rfq_currency_id={rfq_currency_id}, rfq_exchange_rate={rfq_exchange_rate}")

    # Ensure that all fields are valid
    if not line_id or cost is None or not isinstance(cost_currency, int) or exchange_rate is None:
        return jsonify(success=False, error="Invalid input data"), 400

    try:
        # Calculate base cost in EUR
        base_cost = float(cost) * float(exchange_rate)
        logging.debug(f"Calculated base cost (EUR): {base_cost}")

        # Update the rfq_line with the new base cost
        db_execute('''
            UPDATE rfq_lines
            SET base_cost = ?, cost = ?, cost_currency = ?
            WHERE id = ?
        ''', (base_cost, cost, cost_currency, line_id), commit=True)

        return jsonify(success=True, message="Base cost updated successfully", base_cost=base_cost)
    except Exception as e:
        logging.error(f"Error updating base cost for line_id {line_id}: {str(e)}")
        return jsonify(success=False, error=str(e)), 500


@rfqs_bp.route('/update_rfq_line', methods=['POST'])
def update_rfq_line_with_price_list():
    data = request.json
    line_id = data['line_id']
    price = data.get('price')
    supplier_lead_time = data.get('supplier_lead_time')
    supplier_id = data.get('supplier_id')
    quantity = data.get('quantity')
    line_value = data.get('line_value')  # Get the updated line value

    # Fetch the current RFQ line data
    current_line = db_execute('SELECT * FROM rfq_lines WHERE id = ?', (line_id,), fetch='one')

    if not current_line:
        return jsonify({'success': False, 'message': 'RFQ line not found'}), 404

    # Prepare data for update
    update_data = {
        'chosen_supplier': supplier_id,
        'price': price,
        'supplier_lead_time': supplier_lead_time,
        'line_value': line_value  # Update line value
    }

    # Update the RFQ line
    try:
        update_rfq_line_db(line_id, update_data)
        return jsonify({'success': True, 'message': 'RFQ line updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@rfqs_bp.route('/get_history/<int:line_id>', methods=['GET'])
def get_history(line_id):
    try:
        base_part_number_row = db_execute('SELECT base_part_number FROM rfq_lines WHERE id = ?', (line_id,), fetch='one')

        if not base_part_number_row:
            return jsonify({'success': False, 'message': 'RFQ line not found'}), 404

        base_part_number = base_part_number_row['base_part_number']

        offer_history = db_execute('''
            SELECT 
                ol.price,
                ol.lead_time,
                ol.quantity,
                s.name as supplier_name,
                o.valid_to,
                c.currency_code,
                c.symbol as currency_symbol
            FROM offer_lines ol
            JOIN offers o ON ol.offer_id = o.id
            JOIN suppliers s ON o.supplier_id = s.id
            JOIN currencies c ON o.currency_id = c.id
            WHERE ol.base_part_number = ?
            ORDER BY o.valid_to DESC
        ''', (base_part_number,), fetch='all') or []

        rfq_lines = db_execute('''
            SELECT 
                rfl.price,
                rfl.quantity as rfq_quantity,
                rfl.supplier_lead_time as lead_time_days,
                r.entered_date,
                c.name as customer_name,
                cur.currency_code,
                cur.symbol as currency_symbol
            FROM rfq_lines rfl
            JOIN rfqs r ON rfl.rfq_id = r.id
            JOIN customers c ON r.customer_id = c.id
            JOIN currencies cur ON r.currency = cur.id
            WHERE rfl.base_part_number = ?
            ORDER BY r.entered_date DESC
        ''', (base_part_number,), fetch='all') or []

        sales_order_lines = db_execute('''
            SELECT 
                sol.price,
                sol.quantity,
                sol.delivery_date,
                sol.requested_date,
                sol.promise_date,
                so.sales_order_ref,
                c.name as customer_name,
                cur.currency_code,
                cur.symbol as currency_symbol,
                s.status as sales_status
            FROM sales_order_lines sol
            JOIN sales_orders so ON sol.sales_order_id = so.id
            JOIN customers c ON so.customer_id = c.id
            JOIN currencies cur ON so.currency_id = cur.id
            JOIN statuses s ON sol.sales_status_id = s.id
            WHERE sol.base_part_number = ?
            ORDER BY so.date_entered DESC
        ''', (base_part_number,), fetch='all') or []

        if offer_history or rfq_lines or sales_order_lines:
            return jsonify({
                'success': True,
                'history_html': render_template(
                    'history_modal.html',
                    offer_history=offer_history,
                    rfq_lines=rfq_lines,
                    sales_order_lines=sales_order_lines,
                    base_part_number=base_part_number
                )
            })
        else:
            return jsonify({'success': False, 'message': 'No history found'})
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@rfqs_bp.route('/<int:rfq_id>/simplified_edit', methods=['GET'])
def simplified_edit_rfq(rfq_id):
    # Debug to check if RFQ ID and object are valid
    rfq = get_rfq_by_id(rfq_id)
    if not rfq:
        flash('RFQ not found!', 'error')
        return redirect(url_for('rfqs.rfqs'))

    rfq_lines = get_rfq_lines_with_offers(rfq_id)
    suppliers = get_suppliers()
    statuses = get_all_statuses()
    currencies = get_currencies()
    all_manufacturers = get_all_manufacturers()

    return render_template(
        'simplified_rfq_edit.html',
        rfq=rfq,
        rfq_lines=rfq_lines,
        suppliers=suppliers,
        statuses=statuses,
        currencies=currencies,
        all_manufacturers=all_manufacturers
    )


@rfqs_bp.route('/rfqs/update_simplified_rfq_line', methods=['POST'])
def update_simplified_rfq_line():
    data = request.get_json()
    line_id = data.get('line_id')
    offer_id = data.get('offer_id')
    margin = data.get('margin')
    price = data.get('price')
    lead_time = data.get('lead_time')

    # Fetch the RFQ line
    rfq_line = get_rfq_line_by_id(line_id)
    if not rfq_line:
        return jsonify({'success': False, 'error': 'RFQ line not found.'}), 404

    try:
        update_data = {}

        # Update offer if provided
        if offer_id:
            offer = get_offer_by_id(offer_id)
            if not offer:
                return jsonify({'success': False, 'error': 'Offer not found.'}), 404
            update_data['chosen_supplier'] = offer['supplier_id']
            update_data['price'] = float(offer['price'])
            update_data['lead_time'] = float(offer['lead_time'])

        # Update margin if provided
        if margin is not None:
            update_data['margin'] = float(margin)
            # Recalculate price if margin is updated
            base_cost = rfq_line.get('base_cost', 0)
            if (1 - (update_data['margin'] / 100)) == 0:
                return jsonify({'success': False, 'error': 'Margin too high, division by zero.'}), 400
            update_data['price'] = base_cost / (1 - (update_data['margin'] / 100))

        # Update price and lead time if manually edited
        if price is not None:
            update_data['price'] = float(price)
        if lead_time is not None:
            update_data['lead_time'] = float(lead_time)

        # Recalculate line value
        quantity = rfq_line.get('quantity', 0)
        update_data['line_value'] = update_data['price'] * quantity

        # Update the RFQ line in the database
        update_rfq_line_db(
            line_id=line_id,
            update_data={
                'chosen_supplier': update_data.get('chosen_supplier', rfq_line.get('chosen_supplier')),
                'price': update_data.get('price', rfq_line.get('price')),
                'supplier_lead_time': update_data.get('lead_time', rfq_line.get('supplier_lead_time', 0)),
                'line_value': update_data.get('line_value', rfq_line.get('line_value'))
            }
        )

        return jsonify({
            'success': True,
            'price': update_data.get('price', rfq_line.get('price')),
            'lead_time': update_data.get('lead_time', rfq_line.get('lead_time')),
            'line_value': update_data.get('line_value', rfq_line.get('line_value'))
        })

    except Exception as e:
        logging.exception("Error updating RFQ line.")
        return jsonify({'success': False, 'error': str(e)}), 500

@rfqs_bp.route('/update_rfq_line_status/<int:line_id>', methods=['POST'])
def update_rfq_line_status(line_id):
    new_status_id = request.json.get('status_id')
    db_execute('UPDATE rfq_lines SET status_id = ? WHERE id = ?', (new_status_id, line_id), commit=True)

    return jsonify({'success': True})

@rfqs_bp.route('/<int:rfq_id>/upload_email_body', methods=['POST'])
def upload_email_body(rfq_id):
    # Get the RFQ by ID
    rfq = get_rfq_by_id(rfq_id)
    if not rfq:
        return jsonify({"status": "error", "message": "RFQ not found"}), 404

    # Log incoming data for debugging
    data = request.get_json()
    logging.info(f"Received data: {data}")

    email_body = data.get('email_body')
    if not email_body:
        return jsonify({"status": "error", "message": "No email content provided"}), 400

    # Update the RFQ's email column with the email body
    try:
        db_execute(
            'UPDATE rfqs SET email = ? WHERE id = ?',
            (email_body, rfq_id),
            commit=True
        )
        logging.info(f"Email content updated for RFQ {rfq_id}")
        return jsonify({"status": "success", "message": "Email content uploaded and processed successfully"}), 200
    except Exception as e:
        logging.error(f"Error updating email content for RFQ {rfq_id}: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@rfqs_bp.route('/delete_line/<int:line_id>', methods=['POST'])
def delete_rfq_line(line_id):
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                'UPDATE rfq_lines SET status_id = ? WHERE id = ?',
                (8, line_id)
            )
        return jsonify(success=True, message='Line deleted successfully'), 200
    except Exception as e:
        logging.error(f"Error deleting RFQ line {line_id}: {e}")
        return jsonify(success=False, error=str(e)), 500

@rfqs_bp.route('/<int:rfq_id>/excel_view', methods=['GET'])
def rfq_excel_view(rfq_id):
    rfq = get_rfq_by_id(rfq_id)

    if not rfq:
        flash('RFQ not found!', 'error')
        return redirect(url_for('rfqs.rfqs'))

    rfq_lines = get_rfq_lines(rfq_id)

    # Prepare RFQ lines data for Handsontable
    rfq_lines_json = [{
        'line_number': line['line_number'],
        'base_part_number': line['base_part_number'],
        'quantity': line['quantity'],
        'supplier': line.get('chosen_supplier_name', 'N/A'),
        'price': line.get('price', 0),
        'lead_time': line.get('lead_time', 0)
    } for line in rfq_lines]

    return render_template(
        'rfq_excel_view.html',
        rfq=rfq,
        rfq_lines_json=rfq_lines_json
    )

@rfqs_bp.route('/<int:rfq_id>/save_changes', methods=['POST'])
def save_rfq_changes(rfq_id):
    try:
        # Get the updated data from the request
        updated_data = request.get_json()

        if not updated_data or 'rfq_lines' not in updated_data:
            return jsonify({'success': False, 'error': 'No data received'})

        rfq_lines = updated_data['rfq_lines']

        with db_cursor(commit=True) as cur:
            for row in rfq_lines:
                line_number = row[0]  # Access the first column (line number)
                base_part_number = row[1]  # Access the second column (base part number)
                quantity = row[2]  # Access the third column (quantity)

                # Update the rfq_lines table with the new values
                _execute_with_cursor(
                    cur,
                    '''
                    UPDATE rfq_lines
                    SET base_part_number = ?, quantity = ?
                    WHERE rfq_id = ? AND line_number = ?
                    ''',
                    (base_part_number, quantity, rfq_id, line_number)
                )

        return jsonify({'success': True})
    except Exception as e:
        print(f"Error saving RFQ changes: {e}")
        return jsonify({'success': False, 'error': str(e)})

@rfqs_bp.route('/<int:rfq_id>/paste_parts', methods=['GET', 'POST'])
def paste_rfq_parts(rfq_id):
    # Get the RFQ data from the database directly
    rfq = db_execute('SELECT * FROM rfqs WHERE id = ?', (rfq_id,), fetch='one')

    if not rfq:
        return jsonify({'success': False, 'error': 'RFQ not found'}), 404

    if request.method == 'POST':
        part_numbers_data = request.json.get('part_numbers_data', [])

        results = []
        returning_clause = ' RETURNING id' if _using_postgres() else ''
        insert_part_query = f'''
            INSERT INTO part_numbers (base_part_number, part_number)
            VALUES (?, ?){returning_clause}
        '''
        insert_line_query = '''
            INSERT INTO rfq_lines (rfq_id, base_part_number, quantity, line_number)
            VALUES (?, ?, ?, ?)
        '''

        with db_cursor(commit=True) as cur:
            for entry in part_numbers_data:
                line_number = entry[0]  # First column: Line Number
                part_number = entry[1]  # Second column: Part Number
                quantity = entry[2]     # Third column: Quantity

                # Ignore empty lines (no part number or no quantity)
                if not part_number or not quantity:
                    continue

                # Strip the part number to its base form
                base_part_number = create_base_part_number(part_number)

                existing_part = _execute_with_cursor(
                    cur,
                    'SELECT * FROM part_numbers WHERE base_part_number = ?',
                    (base_part_number,),
                    fetch='one'
                )

                if not existing_part:
                    _execute_with_cursor(
                        cur,
                        insert_part_query,
                        (base_part_number, part_number),
                        fetch='one' if _using_postgres() else None
                    )

                _execute_with_cursor(
                    cur,
                    insert_line_query,
                    (rfq_id, base_part_number, quantity, line_number)
                )

                results.append(f"Added/Updated part number {part_number} for RFQ {rfq_id}")

        return jsonify({'success': True, 'results': results})

    return render_template('paste_parts.html', rfq=rfq)


@rfqs_bp.route('/<int:rfq_id>/view_and_edit', methods=['GET', 'POST'])
def view_and_edit_rfq(rfq_id):
    if request.method == 'POST':
        part_numbers_data = request.json.get('part_numbers_data', [])

        manufacturer_query = '''
                    SELECT m.id, m.merged_into, m2.id as canonical_id
                    FROM manufacturers m
                    LEFT JOIN manufacturers m2 ON m.merged_into = m2.id
                    WHERE LOWER(m.name) = LOWER(?)
                '''
        returning_clause = ' RETURNING id' if _using_postgres() else ''
        insert_manufacturer_query = f'INSERT INTO manufacturers (name) VALUES (?){returning_clause}'
        insert_line_query = '''
                    INSERT INTO rfq_lines 
                    (rfq_id, line_number, manufacturer_id, base_part_number, quantity) 
                    VALUES (?, ?, ?, ?, ?)
                '''
        insert_customer_part_query = '''
                    INSERT INTO customer_part_numbers 
                    (base_part_number, customer_part_number, customer_id) 
                    VALUES (?, ?, ?)
                    ON CONFLICT DO NOTHING
                '''

        try:
            with db_cursor(commit=True) as cur:
                rfq = _execute_with_cursor(
                    cur,
                    'SELECT * FROM rfqs WHERE id = ?',
                    (rfq_id,),
                    fetch='one'
                )
                if not rfq:
                    return jsonify({'success': False, 'error': 'RFQ not found'}), 404

                _execute_with_cursor(
                    cur,
                    'DELETE FROM rfq_lines WHERE rfq_id = ?',
                    (rfq_id,)
                )

                for entry in part_numbers_data:
                    line_number = float(entry[0]) if entry[0] else None
                    manufacturer_name = entry[1]
                    part_number = entry[2]
                    quantity = entry[3]
                    customer_part_number = entry[4]

                    if not part_number or not quantity:
                        continue

                    base_part_number = create_base_part_number(part_number)
                    manufacturer_id = None
                    if manufacturer_name:
                        manufacturer = _execute_with_cursor(
                            cur,
                            manufacturer_query,
                            (manufacturer_name,),
                            fetch='one'
                        )

                        if manufacturer:
                            manufacturer_id = manufacturer['canonical_id'] or manufacturer['id']
                        else:
                            manufacturer_row = _execute_with_cursor(
                                cur,
                                insert_manufacturer_query,
                                (manufacturer_name,),
                                fetch='one' if _using_postgres() else None
                            )
                            manufacturer_id = _get_inserted_id(manufacturer_row, cur)

                    _execute_with_cursor(
                        cur,
                        insert_line_query,
                        (rfq_id, line_number, manufacturer_id, base_part_number, quantity)
                    )

                    if customer_part_number:
                        _execute_with_cursor(
                            cur,
                            insert_customer_part_query,
                            (base_part_number, customer_part_number, rfq['customer_id'])
                        )

            return jsonify({'success': True})
        except Exception as e:
            logging.error(f"Error in view_and_edit_rfq POST: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    rfq = db_execute('SELECT * FROM rfqs WHERE id = ?', (rfq_id,), fetch='one')
    if not rfq:
        flash('RFQ not found!', 'error')
        return redirect(url_for('rfqs.rfqs'))

    # Fetch existing RFQ lines with manufacturer names and customer part numbers
    rfq_lines_query = '''
        SELECT rl.line_number, 
               m.name as manufacturer,
               rl.base_part_number as part_number,
               rl.quantity,
               cpn.customer_part_number
        FROM rfq_lines rl
        LEFT JOIN manufacturers m ON rl.manufacturer_id = m.id
        LEFT JOIN customer_part_numbers cpn 
               ON rl.base_part_number = cpn.base_part_number AND cpn.customer_id = ?
        WHERE rl.rfq_id = ?
        ORDER BY rl.line_number
    '''
    rfq_lines = db_execute(rfq_lines_query, (rfq['customer_id'], rfq_id), fetch='all') or []

    existing_lines = [
        [line['line_number'], line['manufacturer'], line['part_number'], line['quantity'], line['customer_part_number']]
        for line in rfq_lines
    ]

    return render_template(
        'view_and_edit_rfq.html',
        rfq=rfq,
        rfq_lines=existing_lines
    )

@rfqs_bp.route('/get_line_details/<line_id>', methods=['GET'])
def get_line_details(line_id):
    try:
        with db_cursor() as cursor:
            line_data = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    rl.base_part_number as requested_base_part_number,
                    pn_requested.part_number as requested_part_number,
                    rl.offered_base_part_number,
                    pn_offered.part_number as offered_part_number,
                    rl.datecode, 
                    rl.taret_price, 
                    rl.spq, 
                    rl.packaging, 
                    rl.rohs,
                    cpn.customer_part_number,
                    r.customer_id
                FROM rfq_lines rl
                LEFT JOIN part_numbers pn_requested ON rl.base_part_number = pn_requested.base_part_number
                LEFT JOIN part_numbers pn_offered ON rl.offered_base_part_number = pn_offered.base_part_number
                LEFT JOIN rfqs r ON rl.rfq_id = r.id
                LEFT JOIN customer_part_numbers cpn ON cpn.base_part_number = rl.base_part_number 
                    AND cpn.customer_id = r.customer_id
                WHERE rl.id = ?
                ''',
                (line_id,),
                fetch='one'
            )

            if line_data:
                logging.debug(f"Line data details for {line_id}: {line_data}")
                return jsonify({
                    'success': True,
                    'requested_base_part_number': line_data['requested_base_part_number'],
                    'requested_part_number': line_data['requested_part_number'],
                    'offered_base_part_number': line_data['offered_base_part_number'],
                    'offered_part_number': line_data['offered_part_number'],
                    'datecode': line_data['datecode'],
                    'target_price': line_data['taret_price'],
                    'spq': line_data['spq'],
                    'packaging': line_data['packaging'],
                    'rohs': bool(line_data['rohs']),
                    'customer_part_number': line_data['customer_part_number']
                })
            return jsonify({'success': False, 'error': 'Line not found'})

    except Exception as e:
        logging.error(f'Error fetching line details: {e}')
        return jsonify({'success': False, 'error': str(e)})

@rfqs_bp.route('/update_line_details/<line_id>', methods=['POST'])
def update_line_details(line_id):
    try:
        data = request.get_json()

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(
                cursor,
                '''
                UPDATE rfq_lines 
                SET datecode = ?, 
                    taret_price = ?, 
                    spq = ?, 
                    packaging = ?, 
                    rohs = ?
                WHERE id = ?
                ''',
                (
                    data['datecode'],
                    data['target_price'],
                    data['spq'],
                    data['packaging'],
                    1 if data['rohs'] else 0,
                    line_id
                )
            )

        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@rfqs_bp.route('/<int:rfq_id>/lines')
def get_rfq_lines_quick_view(rfq_id):
    print(f"Route accessed with rfq_id: {rfq_id}")

    status_rows = db_execute('''
        SELECT 
            base_part_number,
            status_id
        FROM rfq_lines 
        WHERE rfq_id = ?
    ''', (rfq_id,), fetch='all') or []
    print(f"Lines with their status: {status_rows}")

    rows = db_execute('''
        SELECT 
            r.base_part_number,
            COALESCE(p.part_number, r.base_part_number) as display_part_number,
            r.quantity
        FROM rfq_lines r
        LEFT JOIN part_numbers p ON r.base_part_number = p.base_part_number
        WHERE r.rfq_id = ?
        ORDER BY r.line_number
    ''', (rfq_id,), fetch='all') or []
    result_lines = []

    for row in rows:
        result_lines.append({
            'part_number': row[1],
            'quantity': row[2]
        })

    return jsonify(result_lines)

@rfqs_bp.route('/check_part_number', methods=['POST'])
def check_part_number():
    data = request.get_json()
    part_number = data.get('part_number')
    base_part_number = create_base_part_number(part_number)

    # Check if exists in database
    result = db_execute('''
        SELECT part_number, base_part_number 
        FROM part_numbers 
        WHERE base_part_number = ?
    ''', (base_part_number,), fetch='one')

    return jsonify({
        'exists': bool(result),
        'base_part_number': base_part_number,
        'display_part_number': result['part_number'] if result else part_number
    })


@rfqs_bp.route('/update_line_part_number', methods=['POST'])
def update_line_part_number():
    data = request.get_json()
    line_id = data.get('line_id')
    part_number = data.get('part_number')
    base_part_number = create_base_part_number(part_number)

    try:
        existing = db_execute('''
            SELECT part_number FROM part_numbers 
            WHERE base_part_number = ?
        ''', (base_part_number,), fetch='one')

        if not existing:
            insert_part_number(part_number, base_part_number)
            display_part_number = part_number
        else:
            display_part_number = existing['part_number']

        db_execute('''
            UPDATE rfq_lines 
            SET base_part_number = ?
            WHERE id = ?
        ''', (base_part_number, line_id), commit=True)

        return jsonify({
            'success': True,
            'display_part_number': display_part_number
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })


@rfqs_bp.route('/create_from_bom', methods=['POST'])
def create_rfq_from_bom():
    """Create a new RFQ from selected BOM components"""
    logging.debug("Entered create_rfq_from_bom route")
    data = request.get_json()
    logging.debug(f"Received data: {data}")  # Log the incoming data

    # Validate required fields
    required_fields = ['customer_id', 'customer_ref', 'entered_date', 'components']
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({'error': f'Missing required field: {field}'}), 400

    # Ensure customer_id is an integer
    try:
        customer_id = int(data['customer_id'])
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid customer ID'}), 400

    customer_currency = db_execute(
        'SELECT currency_id FROM customers WHERE id = ?',
        (customer_id,),
        fetch='one'
    )

    if not customer_currency:
        return jsonify({'error': 'Customer currency not found'}), 400

    if not isinstance(data['components'], list):
        return jsonify({'error': 'Components must be a list'}), 400

    for component in data['components']:
        if not component.get('base_part_number'):
            return jsonify({'error': 'Each component must have a base_part_number'}), 400
        try:
            component['quantity'] = int(component['quantity']) if component.get('quantity') else 1
        except ValueError:
            return jsonify({'error': f'Invalid quantity for part {component.get("base_part_number")}'}), 400

    try:
        insert_rfq_query = '''
            INSERT INTO rfqs (
                entered_date, 
                customer_id, 
                customer_ref, 
                status, 
                currency
            ) VALUES (?, ?, ?, ?, ?)
        '''
        with db_cursor(commit=True) as cur:
            insert_row = _execute_with_cursor(
                cur,
                insert_rfq_query,
                (
                    data['entered_date'],
                    customer_id,
                    data['customer_ref'],
                    'new',
                    customer_currency['currency_id'] if isinstance(customer_currency, dict) else customer_currency[0]
                ),
                fetch='one' if _using_postgres() else None
            )
            rfq_id = _get_inserted_id(insert_row, cur)

            for idx, component in enumerate(data['components'], 1):
                logging.debug(f"Processing component: {component}")

                manufacturer_id = component.get('manufacturer_id')
                if manufacturer_id:
                    try:
                        manufacturer_id = int(manufacturer_id)
                    except ValueError:
                        manufacturer_id = None

                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO rfq_lines (
                        rfq_id, 
                        line_number, 
                        base_part_number, 
                        quantity, 
                        manufacturer_id
                    ) VALUES (?, ?, ?, ?, ?)
                    ''',
                    (
                        rfq_id,
                        idx * 10,
                        component['base_part_number'],
                        component['quantity'],
                        manufacturer_id
                    )
                )

        logging.info(f'RFQ created successfully from BOM with ID {rfq_id}')
        return jsonify({'rfq_id': rfq_id}), 200
    except Exception as e:
        logging.error(f'Error creating RFQ from BOM: {e}')
        return jsonify({'error': str(e)}), 500


# Add this to your rfqs_bp routes

@rfqs_bp.route('/get_price_breaks/<string:part_number>', methods=['GET'])
def get_price_breaks(part_number):
    """Get all price breaks for a part number"""
    try:
        # Get base part number from part number
        result = db_execute('SELECT base_part_number FROM part_numbers WHERE part_number = ?', (part_number,), fetch='one')
        if not result:
            return jsonify({'error': 'Part number not found'}), 404

        base_part_number = result['base_part_number']

        price_breaks = db_execute('''
            SELECT 
                pb.quantity, pb.price, pli.lead_time,
                pl.supplier_id, s.name as supplier_name
            FROM price_list_items pli
            JOIN price_breaks pb ON pli.id = pb.price_list_item_id
            JOIN price_lists pl ON pli.price_list_id = pl.id
            JOIN suppliers s ON pl.supplier_id = s.id
            WHERE pli.base_part_number = ?
            ORDER BY pb.quantity ASC
        ''', (base_part_number,), fetch='all') or []

        # Convert to list of dicts
        result = []
        for pb in price_breaks:
            result.append({
                'quantity': pb['quantity'],
                'price': pb['price'],
                'lead_time': pb['lead_time'],
                'supplier_id': pb['supplier_id'],
                'supplier_name': pb['supplier_name']
            })

        return jsonify(result)

    except Exception as e:
        app.logger.error(f"Error getting price breaks: {str(e)}")
        return jsonify({'error': str(e)}), 500


# This should be added to the rfqs_bp blueprint
@rfqs_bp.route('/create_from_project', methods=['POST'])
def create_rfq_from_project():
    """
    Create a new RFQ from a project and link them together.
    """
    try:
        # Get form data
        project_id = request.form.get('project_id')
        customer_id = request.form.get('customer_id')
        entered_date = request.form.get('entered_date')
        customer_ref = request.form.get('customer_ref')
        salesperson_id = request.form.get('salesperson_id')

        if not all([project_id, customer_id, entered_date]):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        # Insert new RFQ (simplified version - expand based on your actual model)
        customer = db_execute('SELECT currency_id FROM customers WHERE id = ?', (customer_id,), fetch='one')
        currency_id = customer['currency_id'] if isinstance(customer, dict) else (customer[0] if customer else 1)

        insert_query = 'INSERT INTO rfqs (entered_date, customer_id, customer_ref, status, currency, salesperson_id) VALUES (?, ?, ?, ?, ?, ?)'
        with db_cursor(commit=True) as cur:
            insert_row = _execute_with_cursor(
                cur,
                insert_query,
                (entered_date, customer_id, customer_ref, 'new', currency_id, salesperson_id),
                fetch='one' if _using_postgres() else None
            )
            rfq_id = _get_inserted_id(insert_row, cur)

            _execute_with_cursor(
                cur,
                'INSERT INTO project_rfqs (project_id, rfq_id) VALUES (?, ?)',
                (project_id, rfq_id)
            )

        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@rfqs_bp.route('/<int:rfq_id>/pricing_playground', methods=['GET'])
def pricing_playground(rfq_id):
    """
    Render the RFQ pricing playground page.
    This is an enhanced version of the input_costs page with more advanced pricing features.
    """
    try:
        # Get the RFQ lines with their current values
        rfq_lines = get_rfq_lines_with_offers(rfq_id)
        suppliers = get_suppliers()
        statuses = get_statuses()
        currencies = get_currencies()

        for line in rfq_lines:
            part_number_row = db_execute(
                'SELECT part_number FROM part_numbers WHERE base_part_number = ?',
                (line['base_part_number'],),
                fetch='one'
            )
            line['part_number'] = part_number_row['part_number'] if part_number_row else 'Unknown'

            # Set default currency if not set
            if 'cost_currency' not in line or line['cost_currency'] is None:
                supplier = get_supplier_by_id(line['chosen_supplier']) if line['chosen_supplier'] else None
                line['cost_currency'] = supplier['currency'] if supplier else 3  # Default to EUR

            # Parse suggested suppliers
            if isinstance(line.get('suggested_suppliers'), str):
                line['suggested_suppliers'] = [safe_int(supplier_id) for supplier_id in
                                               line['suggested_suppliers'].split(',') if supplier_id]
            elif isinstance(line.get('suggested_suppliers'), list):
                line['suggested_suppliers'] = [safe_int(supplier_id) for supplier_id in line['suggested_suppliers']]
            else:
                line['suggested_suppliers'] = []

        # Convert database objects to JSON-serializable dictionaries
        def convert_to_dict(item):
            if hasattr(item, 'keys'):  # If it's already dict-like (like sqlite3.Row)
                return dict(item)
            return item  # Otherwise return as-is

        rfq_lines_json = json.dumps([convert_to_dict(line) for line in rfq_lines])
        suppliers_json = json.dumps([convert_to_dict(supplier) for supplier in suppliers])
        statuses_json = json.dumps([convert_to_dict(status) for status in statuses])
        currencies_json = json.dumps([convert_to_dict(currency) for currency in currencies])

        return render_template('pricing_playground.html',
                               rfq_id=rfq_id,
                               rfq_lines=rfq_lines_json,
                               suppliers=suppliers_json,
                               statuses=statuses_json,
                               currencies=currencies_json,
                               breadcrumbs=[
                                   ('Home', url_for('index')),
                                   ('RFQs', url_for('rfqs.rfqs')),
                                   (f'Edit RFQ #{rfq_id}', url_for('rfqs.edit_rfq', rfq_id=rfq_id)),
                                   ('Pricing Playground', '#')
                               ])

    except Exception as e:
        logging.error(f"Error in pricing_playground with rfq_id {rfq_id}: {e}")
        flash("An error occurred while loading the pricing playground", "error")
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

@rfqs_bp.route('/api/rfqs/save_pricing', methods=['POST'])
def save_pricing():
    """
    Save pricing data from the pricing playground.
    """
    try:
        data = request.json
        rfq_id = data.get('rfq_id')
        lines = data.get('lines', [])

        updated_count = 0
        with db_cursor(commit=True) as cursor:
            for line in lines:
                line_id = line.get('id')
                if not line_id:
                    continue

                _execute_with_cursor(
                    cursor,
                    '''
                    UPDATE rfq_lines
                    SET quantity = ?, 
                        chosen_supplier = ?, 
                        cost_currency = ?, 
                        cost = ?, 
                        supplier_lead_time = ?, 
                        margin = ?, 
                        price = ?, 
                        line_value = ?, 
                        note = ?,
                        status_id = ?
                    WHERE id = ?
                    ''',
                    (
                        line.get('quantity', 0),
                        line.get('chosen_supplier'),
                        line.get('cost_currency'),
                        line.get('cost', 0),
                        line.get('supplier_lead_time', 0),
                        line.get('margin', 0),
                        line.get('price', 0),
                        line.get('line_value', 0),
                        line.get('note', ''),
                        line.get('status_id'),
                        line_id
                    )
                )
                updated_count += 1

        logging.info(f"Successfully updated pricing for {updated_count} lines of RFQ ID: {rfq_id}")
        return jsonify({'success': True, 'message': f'Updated {updated_count} lines'})

    except Exception as e:
        logging.error(f"Error saving pricing: {e}")
        return jsonify({'success': False, 'error': str(e)})


@rfqs_bp.route('/api/rfq_lines/<int:line_id>/offers', methods=['GET'])
def get_line_offers(line_id):
    """
    Get offers for an RFQ line.
    """
    try:
        offers_query = '''
            SELECT o.id, o.supplier_id, s.name as supplier_name, 
                   o.price, o.currency_id, c.currency_code, 
                   o.lead_time
            FROM offer_lines ol
            JOIN offers o ON ol.offer_id = o.id
            JOIN suppliers s ON o.supplier_id = s.id
            JOIN currencies c ON o.currency_id = c.id
            WHERE ol.base_part_number = (
                SELECT base_part_number FROM rfq_lines WHERE id = ?
            )
        '''

        offers_data = db_execute(offers_query, (line_id,), fetch='all') or []

        # Format the offers for JSON response
        offers = []
        for offer in offers_data:
            offers.append({
                'id': offer['id'],
                'supplier': {
                    'id': offer['supplier_id'],
                    'name': offer['supplier_name']
                },
                'price': float(offer['price']),
                'currency': {
                    'id': offer['currency_id'],
                    'code': offer['currency_code']
                },
                'lead_time': offer['lead_time']
            })

        return jsonify({'success': True, 'offers': offers})

    except Exception as e:
        logging.error(f"Error getting offers for line {line_id}: {e}")
        return jsonify({'success': False, 'error': str(e)})


@rfqs_bp.route('/api/offers', methods=['POST'])
def create_offer():
    """
    Create a new offer.
    """
    try:
        data = request.json
        line_id = data.get('line_id')
        supplier_id = data.get('supplier_id')
        price = data.get('price')
        currency_id = data.get('currency_id')
        lead_time = data.get('lead_time')

        # Validate required fields
        if not all([line_id, supplier_id, price, currency_id, lead_time]):
            return jsonify({'success': False, 'error': 'Missing required fields'})

        line_query = 'SELECT base_part_number, rfq_id FROM rfq_lines WHERE id = ?'
        line_data = db_execute(line_query, (line_id,), fetch='one')

        if not line_data:
            return jsonify({'success': False, 'error': 'RFQ line not found'})

        base_part_number = line_data['base_part_number']
        rfq_id = line_data['rfq_id']

        current_date = datetime.now().strftime('%Y-%m-%d')
        valid_to_date = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        returning_clause = ' RETURNING id' if _using_postgres() else ''
        insert_offer_query = f'''
            INSERT INTO offers (supplier_id, valid_to, currency_id, price, lead_time)
            VALUES (?, ?, ?, ?, ?){returning_clause}
        '''
        insert_offer_line_query = '''
            INSERT INTO offer_lines (offer_id, base_part_number, quantity, price, lead_time)
            VALUES (?, ?, ?, ?, ?)
        '''

        with db_cursor(commit=True) as cur:
            offer_row = _execute_with_cursor(
                cur,
                insert_offer_query,
                (supplier_id, valid_to_date, currency_id, price, lead_time),
                fetch='one' if _using_postgres() else None
            )
            offer_id = _get_inserted_id(offer_row, cur)

            _execute_with_cursor(
                cur,
                insert_offer_line_query,
                (offer_id, base_part_number, 1, price, lead_time)
            )

        return jsonify({'success': True, 'offer_id': offer_id})

    except Exception as e:
        logging.error(f"Error creating offer: {e}")
        return jsonify({'success': False, 'error': str(e)})


@rfqs_bp.route('/api/offers/<int:offer_id>', methods=['GET'])
def get_offer(offer_id):
    """
    Get an offer by ID.
    """
    try:
        offer_query = '''
            SELECT o.id, o.supplier_id, o.price, o.currency_id, o.lead_time
            FROM offers o
            WHERE o.id = ?
        '''

        offer_data = db_execute(offer_query, (offer_id,), fetch='one')

        if not offer_data:
            return jsonify({'success': False, 'error': 'Offer not found'})

        offer = {
            'id': offer_data['id'],
            'supplier_id': offer_data['supplier_id'],
            'price': float(offer_data['price']),
            'currency_id': offer_data['currency_id'],
            'lead_time': offer_data['lead_time']
        }

        return jsonify({'success': True, 'offer': offer})

    except Exception as e:
        logging.error(f"Error getting offer {offer_id}: {e}")
        return jsonify({'success': False, 'error': str(e)})

@rfqs_bp.route('/get_rfq_updates/<int:rfq_id>', methods=['GET'])
@login_required
def get_rfq_updates_ajax(rfq_id):
    """Get all updates for an RFQ via AJAX"""
    try:
        updates = get_rfq_updates(rfq_id)
        return jsonify({"success": True, "updates": updates})
    except Exception as e:
        logging.error(f"Error getting RFQ updates: {e}")
        return jsonify({"success": False, "error": str(e)})

@rfqs_bp.route('/<int:rfq_id>/add_update', methods=['POST'])
@login_required
def add_rfq_update_route(rfq_id):
    """Route to add an update to an RFQ"""
    try:
        update_type = request.form.get('update_type', 'comment')
        update_text = request.form.get('update_text', None)

        # If update type is "chased" and no text provided, use a default
        if update_type == 'chased' and not update_text:
            update_text = "RFQ chased"

        # Add the update
        add_rfq_update(
            rfq_id=rfq_id,
            user_id=current_user.id,
            update_text=update_text,
            update_type=update_type
        )

        flash('Update added successfully!', 'success')
    except Exception as e:
        flash(f'Error adding update: {e}', 'error')

    # Return to the RFQ edit page
    return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))


@rfqs_bp.route('/<int:rfq_id>/quick_chase', methods=['POST'])
@login_required
def quick_chase_rfq(rfq_id):
    """Route for quick chasing an RFQ without a comment"""
    try:
        add_rfq_update(
            rfq_id=rfq_id,
            user_id=current_user.id,
            update_text=None,
            update_type='chased'
        )
        flash('RFQ chased successfully!', 'success')
    except Exception as e:
        flash(f'Error chasing RFQ: {e}', 'error')

    # Redirect back to the RFQ edit page
    return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))


@rfqs_bp.route('/<int:rfq_id>/get_updates', methods=['GET'])
@login_required
def get_rfq_updates_route(rfq_id):
    """Route to get all updates for an RFQ (for AJAX calls)"""
    try:
        updates = get_rfq_updates(rfq_id)
        return jsonify({"success": True, "updates": updates})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@rfqs_bp.route('/add_rfq_update', methods=['POST'])
@login_required
def add_rfq_update_ajax():
    """
    Add an update to an RFQ via AJAX
    This can be called from any page, including the main RFQs listing
    """
    try:
        rfq_id = request.form.get('rfq_id')
        update_type = request.form.get('update_type', 'comment')
        update_text = request.form.get('update_text', None)

        # Validate inputs
        if not rfq_id:
            return jsonify({"success": False, "error": "RFQ ID is required"})

        # If update type is "chased" and no text provided, use a default
        if update_type == 'chased' and not update_text:
            update_text = "RFQ chased"

        # Add the update
        update_id = add_rfq_update(
            rfq_id=int(rfq_id),
            user_id=current_user.id,
            update_text=update_text,
            update_type=update_type
        )

        # Get the newly created update for the response
        updates = get_rfq_updates(rfq_id)
        new_update = next((u for u in updates if u['id'] == update_id), None)

        return jsonify({
            "success": True,
            "message": f"Update added successfully",
            "update": new_update
        })

    except Exception as e:
        logging.error(f"Error adding RFQ update: {e}")
        return jsonify({"success": False, "error": str(e)})


@rfqs_bp.route('/chase_rfq', methods=['POST'])
@login_required
def chase_rfq():
    """
    Endpoint to quickly mark an RFQ as chased via AJAX
    """
    try:
        data = request.get_json() or {}
        rfq_id = data.get('rfq_id') or request.form.get('rfq_id')

        if not rfq_id:
            return jsonify({"success": False, "error": "RFQ ID is required"})

        # Add a "chased" update
        update_id = add_rfq_update(
            rfq_id=int(rfq_id),
            user_id=current_user.id,
            update_text="RFQ chased",
            update_type='chased'
        )

        # Get the new update details for the response
        updates = get_rfq_updates(rfq_id)
        new_update = next((u for u in updates if u['id'] == update_id), None)

        return jsonify({
            "success": True,
            "message": "RFQ marked as chased",
            "update": new_update
        })

    except Exception as e:
        logging.error(f"Error chasing RFQ: {e}")
        return jsonify({"success": False, "error": str(e)})


@rfqs_bp.route('/rfq_lines/<int:line_id>/update_quantity', methods=['POST'])
def update_rfq_line_quantity(line_id):
    """Update the quantity of an RFQ line."""
    data = request.get_json()
    quantity = data.get('quantity')

    if not quantity or int(quantity) <= 0:
        return jsonify({'success': False, 'error': 'Invalid quantity provided'})

    try:
        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(
                cursor,
                'UPDATE rfq_lines SET quantity = ? WHERE id = ?',
                (quantity, line_id)
            )

            result = _execute_with_cursor(
                cursor,
                '''
                SELECT cost, offer_id 
                FROM rfq_lines 
                WHERE id = ? AND offer_id IS NOT NULL
                ''',
                (line_id,),
                fetch='one'
            )

            if result and result['offer_id']:
                per_unit_cost = float(result['cost'])
                extended_cost = per_unit_cost * int(quantity)
                _execute_with_cursor(
                    cursor,
                    'UPDATE rfq_lines SET extended_cost = ? WHERE id = ?',
                    (extended_cost, line_id)
                )

        return jsonify({
            'success': True,
            'message': 'Quantity updated successfully',
            'new_quantity': quantity
        })
    except Exception as e:
        logging.error(f"Error updating RFQ line quantity {line_id}: {e}")
        return jsonify({'success': False, 'error': str(e)})


@rfqs_bp.route('/<int:rfq_id>/playground', methods=['GET'])
def rfq_playground(rfq_id):
    # Get basic RFQ information
    rfq = get_rfq_by_id(rfq_id)
    if not rfq:
        flash('RFQ not found!', 'error')
        return redirect(url_for('rfqs.rfqs'))

    # Get RFQ lines
    rfq_lines = get_rfq_lines_with_offers(rfq_id)

    # Get suppliers for the dropdown
    suppliers = get_suppliers()

    # Add part number and enhanced data to each line
    for line in rfq_lines:
        # Get the actual part number
        part_number_row = db_execute(
            'SELECT part_number FROM part_numbers WHERE base_part_number = ?',
            (line['base_part_number'],),
            fetch='one'
        )
        line['part_number'] = part_number_row['part_number'] if part_number_row else 'Unknown'

        # Get purchase order history
        line['purchase_history'] = db_execute('''
            SELECT 
                po.date_issued, 
                pol.quantity,
                pol.price,
                s.name as supplier_name
            FROM purchase_order_lines pol
            JOIN purchase_orders po ON pol.purchase_order_id = po.id
            JOIN suppliers s ON po.supplier_id = s.id
            WHERE pol.base_part_number = ?
            ORDER BY po.date_issued DESC
            LIMIT 5
        ''', (line['base_part_number'],), fetch='all') or []

        # Get offer history
        line['offer_history'] = db_execute('''
            SELECT 
                o.valid_to,
                ol.quantity,
                ol.price,
                s.name as supplier_name
            FROM offer_lines ol
            JOIN offers o ON ol.offer_id = o.id
            JOIN suppliers s ON o.supplier_id = s.id
            WHERE ol.base_part_number = ? AND ol.offer_id != ?
            ORDER BY o.valid_to DESC
            LIMIT 5
        ''', (line['base_part_number'], line.get('offer_id')), fetch='all') or []

        # Get RFQ history
        line['rfq_history'] = db_execute('''
            SELECT 
                r.entered_date,
                rl.quantity,
                rl.price,
                c.name as customer_name
            FROM rfq_lines rl
            JOIN rfqs r ON rl.rfq_id = r.id
            JOIN customers c ON r.customer_id = c.id
            WHERE rl.base_part_number = ? AND rl.id != ?
            ORDER BY r.entered_date DESC
            LIMIT 5
        ''', (line['base_part_number'], line['id']), fetch='all') or []

        # Get sales order history
        line['sales_history'] = db_execute('''
            SELECT 
                so.date_entered,
                sol.quantity,
                sol.price,
                c.name as customer_name
            FROM sales_order_lines sol
            JOIN sales_orders so ON sol.sales_order_id = so.id
            JOIN customers c ON so.customer_id = c.id
            WHERE sol.base_part_number = ?
            ORDER BY so.date_entered DESC
            LIMIT 5
        ''', (line['base_part_number'],), fetch='all') or []

    return render_template(
        'rfq_playground.html',
        rfq=rfq,
        rfq_lines=rfq_lines,
        suppliers=suppliers,  # Add suppliers to the template context
        breadcrumbs=[
            ('Home', url_for('index')),
            ('RFQs', url_for('rfqs.rfqs')),
            (f'RFQ #{rfq_id}', url_for('rfqs.edit_rfq', rfq_id=rfq_id)),
            ('Playground', '#')
        ]
    )
