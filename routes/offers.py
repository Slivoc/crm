from flask import Flask, g, send_from_directory, jsonify, session, current_app, render_template, request, Blueprint, redirect, url_for, flash
from werkzeug.utils import secure_filename
from models import create_base_part_number, get_table_columns
import os
from datetime import datetime, date, timedelta
from ai_helper import extract_part_numbers_and_quantities, extract_quote_info, extract_quote_info_with_examples
import logging
import re
import pdfplumber
from collections import defaultdict
import tabula
import pandas as pd
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
from itertools import zip_longest
from difflib import SequenceMatcher
from typing import Tuple, List
from pypdf import PdfReader
import io
import extract_msg
from db import db_cursor, execute as db_execute

logging.basicConfig(level=logging.DEBUG)


offers_bp = Blueprint('offers', __name__)


def _using_postgres() -> bool:
    """Return True when DATABASE_URL points at Postgres."""
    return os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://'))


def _execute_with_cursor(cur, query, params=None):
    """Execute a query with placeholder translation for Postgres."""
    prepared = query.replace('?', '%s') if _using_postgres() else query
    cur.execute(prepared, params or [])
    return cur


def _get_inserted_id(row, cursor=None):
    """Return the inserted row id from a RETURNING row or fallback to cursor.lastrowid."""
    if row is None:
        return getattr(cursor, 'lastrowid', None) if cursor is not None else None
    if isinstance(row, dict):
        return row.get('id')
    try:
        return row['id']
    except Exception:
        pass
    try:
        return row[0]
    except Exception:
        pass
    return getattr(cursor, 'lastrowid', None) if cursor is not None else None


@offers_bp.route('/new', methods=['GET', 'POST'], endpoint='create_offer')
def create_offer():
    if request.method == 'POST':
        supplier_id = request.form.get('supplier_id')
        valid_to = request.form.get('valid_to')
        supplier_reference = request.form.get('supplier_reference')
        uploaded_file = request.files.get('file')

        if not supplier_id:
            flash('Supplier ID is required.', 'danger')
            return redirect(url_for('offers.create_offer'))

        filename = filepath = None
        if uploaded_file and uploaded_file.filename != '':
            filename = secure_filename(uploaded_file.filename)
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            uploaded_file.save(filepath)

        file_id = None
        with db_cursor(commit=True) as cursor:
            if filename and filepath:
                _execute_with_cursor(
                    cursor,
                    'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?) RETURNING id',
                    (filename, filepath, datetime.now()),
                )
                file_row = cursor.fetchone()
                file_id = _get_inserted_id(file_row, cursor)

            _execute_with_cursor(cursor, 'SELECT currency FROM suppliers WHERE id = ?', (supplier_id,))
            supplier_currency = cursor.fetchone()
            currency_id = supplier_currency['currency'] if supplier_currency else None

            _execute_with_cursor(
                cursor,
                '''
                INSERT INTO offers (supplier_id, valid_to, supplier_reference, file_id, currency_id)
                VALUES (?, ?, ?, ?, ?) RETURNING id
                ''',
                (supplier_id, valid_to, supplier_reference, file_id, currency_id),
            )
            offer_row = cursor.fetchone()
            offer_id = _get_inserted_id(offer_row, cursor)

        flash('Offer successfully created', 'success')
        return redirect(url_for('offers.edit_offer', offer_id=offer_id))

    suppliers = db_execute('SELECT id, name FROM suppliers ORDER BY name', fetch='all')
    default_valid_to = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')

    return render_template(
        'offer_edit.html',
        offer=None,
        suppliers=suppliers,
        offer_lines=[],
        rfqs=[],
        default_valid_to=default_valid_to,
    )


@offers_bp.route('/offers/<int:offer_id>/edit', methods=['GET', 'POST'], endpoint='edit_offer')
def edit_offer(offer_id):
    if request.method == 'POST':
        if 'file' in request.files:
            uploaded_file = request.files['file']
            if uploaded_file and uploaded_file.filename != '':
                filename = secure_filename(uploaded_file.filename)
                filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                uploaded_file.save(filepath)

                with db_cursor(commit=True) as cursor:
                    _execute_with_cursor(
                        cursor,
                        'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?) RETURNING id',
                        (filename, filepath, datetime.now()),
                    )
                    file_row = cursor.fetchone()
                    file_id = _get_inserted_id(file_row, cursor)
                    _execute_with_cursor(cursor, 'UPDATE offers SET file_id = ? WHERE id = ?', (file_id, offer_id))
                    _execute_with_cursor(
                        cursor,
                        'INSERT INTO offer_files (offer_id, file_id) VALUES (?, ?)',
                        (offer_id, file_id),
                    )

                return jsonify({'success': True, 'message': 'File uploaded successfully'})
        else:
            supplier_id = request.form['supplier_id']
            valid_to = request.form['valid_to']
            supplier_reference = request.form['supplier_reference']

            with db_cursor(commit=True) as cursor:
                _execute_with_cursor(
                    cursor,
                    '''
                    UPDATE offers 
                    SET supplier_id = ?, valid_to = ?, supplier_reference = ?
                    WHERE id = ?
                    ''',
                    (supplier_id, valid_to, supplier_reference, offer_id),
                )

            flash('Offer successfully updated', 'success')
            return redirect(url_for('offers.edit_offer', offer_id=offer_id))

    offer = db_execute('SELECT * FROM offers WHERE id = ?', (offer_id,), fetch='one')
    suppliers = db_execute('SELECT id, name FROM suppliers ORDER BY name', fetch='all')
    manufacturers = db_execute('SELECT id, name FROM manufacturers ORDER BY name', fetch='all')

    offer_lines = db_execute(
        '''
        SELECT ol.*, 
               pn_offered.part_number as part_number,
               pn_requested.part_number as requested_part_number
        FROM offer_lines ol
        LEFT JOIN part_numbers pn_offered ON ol.base_part_number = pn_offered.base_part_number
        LEFT JOIN part_numbers pn_requested ON ol.requested_base_part_number = pn_requested.base_part_number
        WHERE ol.offer_id = ?
        ''',
        (offer_id,),
        fetch='all',
    )

    offer_lines_dicts = [dict(line) for line in offer_lines]

    for line in offer_lines_dicts:
        lookup_part_number = line['requested_base_part_number'] or line['base_part_number']
        associated_rfqs = db_execute(
            '''
            SELECT r.id AS rfq_id, c.name AS customer_name
            FROM rfqs r
            JOIN customers c ON r.customer_id = c.id
            JOIN rfq_lines rl ON r.id = rl.rfq_id
            WHERE rl.base_part_number = ?
            ''',
            (lookup_part_number,),
            fetch='all',
        )

        line['open_rfq_urls'] = [
            {
                'url': url_for('rfqs.edit_rfq', rfq_id=rfq['rfq_id']),
                'rfq_id': rfq['rfq_id'],
                'customer_name': rfq['customer_name'],
            }
            for rfq in associated_rfqs
        ]

    max_line_row = db_execute(
        'SELECT MAX(CAST(line_number AS INTEGER)) AS max_line FROM offer_lines WHERE offer_id = ?',
        (offer_id,),
        fetch='one',
    )
    max_line_number = max_line_row['max_line'] if max_line_row and max_line_row['max_line'] else 0

    rfqs = db_execute(
        '''
        SELECT r.id AS req_id, q.id AS rfq_id, c.name as customer_name, r.date
        FROM requisitions r
        JOIN rfqs q ON r.rfq_id = q.id
        JOIN customers c ON q.customer_id = c.id
        WHERE r.supplier_id = ?
        ''',
        (offer['supplier_id'],),
        fetch='all',
    )

    attachments = db_execute(
        '''
        SELECT f.id, f.filename 
        FROM files f
        JOIN offer_files of ON f.id = of.file_id
        WHERE of.offer_id = ?
        ''',
        (offer_id,),
        fetch='all',
    )

    return render_template(
        'offer_edit.html',
        offer=offer,
        suppliers=suppliers,
        manufacturers=manufacturers,
        offer_lines=offer_lines_dicts,
        rfqs=rfqs,
        max_line_number=max_line_number,
        attachments=attachments,
    )

def process_file_content(filepath):
    file_content = ''
    if filepath.lower().endswith('.pdf'):
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                file_content += page.extract_text()
    else:  # Assume it's an email or text file
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            file_content = f.read()
    return file_content

@offers_bp.route('/offers/<int:offer_id>/add_line', methods=['POST'], endpoint='add_offer_line')
def add_offer_line(offer_id):
    part_number = request.form['part_number']
    base_part_number = create_base_part_number(part_number)
    line_number = request.form['line_number']
    manufacturer_id = request.form['manufacturer_id']
    quantity = request.form['quantity']
    price = request.form['price']
    lead_time = request.form['lead_time']

    # New field assignments
    datecode = request.form.get('datecode')
    spq = request.form.get('spq', type=int)  # Convert to integer if present
    packaging = request.form.get('packaging')
    rohs = request.form.get('rohs', type=bool)  # Convert to boolean if present
    coc = request.form.get('coc', type=bool)

    with db_cursor(commit=True) as cursor:
        _execute_with_cursor(
            cursor,
            'SELECT 1 FROM part_numbers WHERE base_part_number = ?',
            (base_part_number,),
        )
        part_exists = cursor.fetchone()
        if not part_exists:
            _execute_with_cursor(
                cursor,
                '''
                INSERT INTO part_numbers (base_part_number, part_number)
                VALUES (?, ?)
                ''',
                (base_part_number, part_number),
            )

        _execute_with_cursor(
            cursor,
            '''
            INSERT INTO offer_lines (
                offer_id, 
                base_part_number, 
                requested_base_part_number,
                line_number, 
                manufacturer_id, 
                quantity, 
                price, 
                lead_time,
                datecode,
                spq,
                packaging,
                rohs,
                coc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                offer_id,
                base_part_number,
                base_part_number,
                line_number,
                manufacturer_id,
                quantity,
                price,
                lead_time,
                datecode,
                spq,
                packaging,
                rohs,
                coc,
            ),
        )

        _execute_with_cursor(
            cursor,
            '''
            SELECT 1 FROM part_manufacturers
            WHERE base_part_number = ? AND manufacturer_id = ?
            ''',
            (base_part_number, manufacturer_id),
        )
        association_exists = cursor.fetchone()

        if not association_exists:
            _execute_with_cursor(
                cursor,
                '''
                INSERT INTO part_manufacturers (base_part_number, manufacturer_id)
                VALUES (?, ?)
                ''',
                (base_part_number, manufacturer_id),
            )

    flash('Offer line successfully added', 'success')
    return redirect(url_for('offers.edit_offer', offer_id=offer_id))

@offers_bp.route('/offers/<int:offer_id>/update_lines', methods=['POST'])
def update_offer_lines(offer_id):
    offer_lines = db_execute(
        'SELECT id FROM offer_lines WHERE offer_id = ?',
        (offer_id,),
        fetch='all',
    )

    with db_cursor(commit=True) as cursor:
        for line in offer_lines:
            try:
                line_number = request.form[f'line_number_{line["id"]}']
                part_number = request.form[f'part_number_{line["id"]}']
                manufacturer_id = request.form[f'manufacturer_id_{line["id"]}']
                quantity = request.form[f'quantity_{line["id"]}']
                price = request.form[f'price_{line["id"]}']
                lead_time = request.form[f'lead_time_{line["id"]}']
                internal_notes = request.form.get(f'internal_notes_{line["id"]}', '')

                datecode = request.form.get(f'datecode_{line["id"]}')
                spq = request.form.get(f'spq_{line["id"]}', type=int)
                packaging = request.form.get(f'packaging_{line["id"]}')
                rohs = request.form.get(f'rohs_{line["id"]}', type=bool)
                coc = request.form.get(f'coc_{line["id"]}', type=bool)

                _execute_with_cursor(
                    cursor,
                    '''
                    UPDATE offer_lines 
                    SET base_part_number = ?, 
                        line_number = ?, 
                        manufacturer_id = ?, 
                        quantity = ?, 
                        price = ?, 
                        lead_time = ?,
                        internal_notes = ?,
                        datecode = ?,
                        spq = ?,
                        packaging = ?,
                        rohs = ?,
                        coc = ?
                    WHERE id = ?
                    ''',
                    (
                        part_number,
                        line_number,
                        manufacturer_id,
                        quantity,
                        price,
                        lead_time,
                        internal_notes,
                        datecode,
                        spq,
                        packaging,
                        rohs,
                        coc,
                        line["id"],
                    ),
                )

                if manufacturer_id:
                    _execute_with_cursor(
                        cursor,
                        '''
                        SELECT 1 FROM part_manufacturers
                        WHERE base_part_number = ? AND manufacturer_id = ?
                        ''',
                        (part_number, manufacturer_id),
                    )
                    association = cursor.fetchone()

                    if not association:
                        _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO part_manufacturers (base_part_number, manufacturer_id)
                            VALUES (?, ?)
                            ''',
                            (part_number, manufacturer_id),
                        )

            except KeyError as e:
                print(f"Missing form data for key: {e}")
                flash(f"Error updating line {line['id']}: missing data for {e}.", 'danger')
                continue

    flash('Offer lines successfully updated', 'success')
    return redirect(url_for('offers.edit_offer', offer_id=offer_id))

    flash('Offer lines successfully updated', 'success')
    return redirect(url_for('offers.edit_offer', offer_id=offer_id))

@offers_bp.route('/offers/update_offer_line/<int:line_id>', methods=['POST'])
def update_offer_line(line_id):
    data = request.json
    field_name = data.get('field_name')
    field_value = data.get('field_value')

    try:
        allowed_fields = ['line_number', 'base_part_number', 'manufacturer_id', 'quantity',
                         'price', 'lead_time', 'internal_notes', 'datecode', 'spq',
                         'packaging', 'rohs', 'coc']  # Added new fields

        if field_name not in allowed_fields:
            raise ValueError(f"Invalid field name: {field_name}")

        # Convert to appropriate type
        if field_name in ['quantity', 'lead_time', 'manufacturer_id', 'spq']:
            try:
                field_value = int(field_value) if field_value else None
            except ValueError:
                raise ValueError(f"Invalid value for {field_name}: {field_value}")
        elif field_name == 'price':
            try:
                field_value = float(field_value) if field_value else None
            except ValueError:
                raise ValueError(f"Invalid value for {field_name}: {field_value}")
        elif field_name in ['rohs', 'coc']:
            field_value = bool(field_value) if field_value is not None else None

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(
                cursor,
                f'''
                UPDATE offer_lines
                SET {field_name} = ?
                WHERE id = ?
                ''',
                (field_value, line_id),
            )
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@offers_bp.route('/offers/<int:offer_id>/extract_offer_lines', methods=['POST'], endpoint='extract_offer_lines')
def extract_offer_lines(offer_id):
    offer_info = request.form.get('offer_info')
    print(f"Received offer_info for offer_id {offer_id}: {offer_info}")  # Log the offer_info received

    # Check if offer_info is empty or None
    if not offer_info:
        print("Error: No offer_info received")
        return jsonify({'error': 'No offer_info received'}), 400

    # Log the raw offer_info
    print(f"Raw offer_info: {offer_info}")

    # Extract offer lines using your extraction function
    try:
        extracted_lines = extract_quote_info(offer_info)
        print(f"Extracted Lines: {extracted_lines}")  # Log the extracted lines
    except Exception as e:
        print(f"Error during extraction: {e}")
        return jsonify({'error': 'Error during extraction'}), 500

    if not extracted_lines:
        print("No lines extracted from offer_info.")
        return jsonify({'error': 'No lines extracted from offer_info'}), 400

    max_line_row = db_execute(
        'SELECT MAX(CAST(line_number AS INTEGER)) AS max_line FROM offer_lines WHERE offer_id = ?',
        (offer_id,),
        fetch='one',
    )
    max_line_number = max_line_row['max_line'] if max_line_row and max_line_row['max_line'] else 0
    current_line_number = max_line_number + 1

    try:
        with db_cursor(commit=True) as cursor:
            for line in extracted_lines:
                part_number = line[0] if len(line) > 0 else None
                quantity = line[1] if len(line) > 1 else 0
                price = line[2] if len(line) > 2 else 0.0
                lead_time = line[3] if len(line) > 3 else 0
                manufacturer = line[4] if len(line) > 4 else None

                print(f"Processing line: Part Number: {part_number}, Quantity: {quantity}, Price: {price}, Lead Time: {lead_time}, Manufacturer: {manufacturer}")

                if not part_number:
                    print("Skipping line due to missing part number")
                    continue

                base_part_number = create_base_part_number(part_number)
                print(f"Base Part Number: {base_part_number}")

                _execute_with_cursor(
                    cursor,
                    'SELECT part_number FROM part_numbers WHERE base_part_number = ?',
                    (base_part_number,),
                )
                existing_part_number = cursor.fetchone()
                if not existing_part_number:
                    print(f"Inserting new part number: {base_part_number}, {part_number}")
                    _execute_with_cursor(
                        cursor,
                        'INSERT INTO part_numbers (base_part_number, part_number) VALUES (?, ?)',
                        (base_part_number, part_number),
                    )
                else:
                    print(f"Part number already exists: {existing_part_number[0]}")

                _execute_with_cursor(
                    cursor,
                    '''
                    INSERT INTO offer_lines (
                        offer_id, 
                        base_part_number, 
                        requested_base_part_number,
                        line_number, 
                        manufacturer_id, 
                        quantity, 
                        price, 
                        lead_time
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        offer_id,
                        base_part_number,
                        base_part_number,
                        current_line_number,
                        manufacturer,
                        quantity,
                        price,
                        lead_time,
                    ),
                )
                print(f"Inserted offer line {current_line_number} for offer_id {offer_id}")
                current_line_number += 1

        print(f"Successfully inserted lines for offer {offer_id}")
        return jsonify({'success': True, 'message': 'Offer lines extracted successfully'}), 200
    except Exception as e:
        print(f"Error during database operations: {e}")
        return jsonify({'error': 'An error occurred during database operations'}), 500

def create_base_part_number(part_number):
    return ''.join(e for e in part_number if e.isalnum()).upper()


@offers_bp.route('/offers', methods=['GET'], endpoint='offers_list')
def offers_list():
    offers = db_execute(
        '''
        SELECT 
            o.id,
            o.valid_to,
            o.supplier_reference,
            s.name AS supplier_name,
            o.supplier_id
        FROM offers o
        JOIN suppliers s ON o.supplier_id = s.id
        ORDER BY o.id DESC
        ''',
        fetch='all',
    )

    suppliers = db_execute(
        'SELECT id, name FROM suppliers ORDER BY name',
        fetch='all',
    )

    offers = [dict(offer) for offer in offers]
    today = date.today()

    for offer in offers:
        valid_to_str = offer.get('valid_to')
        status = 'unknown'
        if valid_to_str:
            try:
                valid_to = datetime.strptime(valid_to_str, '%Y-%m-%d').date()
                offer['days_remaining'] = (valid_to - today).days
                if valid_to < today:
                    status = 'expired'
                elif valid_to <= today + timedelta(days=7):
                    status = 'expiring'
                else:
                    status = 'active'
                offer['valid_to'] = valid_to.strftime('%b %d, %Y')
            except ValueError:
                offer['days_remaining'] = None
        else:
            offer['days_remaining'] = None

        offer['status'] = status

    default_valid_to = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')

    return render_template('offers.html', offers=offers, suppliers=suppliers, default_valid_to=default_valid_to)

@offers_bp.route('/api/get_manufacturers_for_part', methods=['GET'])
def get_manufacturers_for_part():
    logging.info("Accessed /api/get_manufacturers_for_part")
    part_number = request.args.get('part_number')
    logging.info(f"Received part number: {part_number}")
    part_number = request.args.get('part_number')
    base_part_number = create_base_part_number(part_number)

    manufacturers = db_execute(
        '''
        SELECT m.id, m.name
        FROM part_manufacturers pm
        JOIN manufacturers m ON pm.manufacturer_id = m.id
        WHERE pm.base_part_number = ?
        ''',
        (base_part_number,),
        fetch='all',
    )

    if manufacturers:
        return jsonify(manufacturers=[{'id': man['id'], 'name': man['name']} for man in manufacturers])
    return jsonify({'message': 'No manufacturers found'}), 404


@offers_bp.route('/upload_pdf', methods=['POST'])
def upload_pdf():
    print("Route has been hit")  # Use print to ensure logging isn't an issue
    if 'pdf_file' in request.files:
        print("File is present")
    else:
        print("No file uploaded")
    return "Check your console"

    if 'pdf_file' not in request.files:
        logging.warning("No file part in request files")
        flash('No file part', 'error')
        return redirect(url_for('offers.offers_list'))

    file = request.files['pdf_file']
    if file.filename == '':
        logging.warning("No file selected for upload")
        flash('No selected file', 'error')
        return redirect(url_for('offers.offers_list'))

    if file and file.filename.endswith('.pdf'):
        filename = secure_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        try:
            file.save(filepath)
            logging.info(f"File saved to {filepath}")

            with pdfplumber.open(filepath) as pdf:
                pdf_text = ''
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        pdf_text += page_text
                    else:
                        logging.info(f"No text found on page {pdf.pages.index(page) + 1}")
                logging.debug(f"Extracted text: {pdf_text[:100]}...")

            session['pdf_text'] = pdf_text
            session['pdf_filename'] = filename
            flash('PDF uploaded and processed successfully!', 'success')
        except Exception as e:
            logging.error(f"Error processing the PDF file: {str(e)}")
            flash('Error processing the PDF file.', 'error')
        return redirect(url_for('offers.view_pdf_text'))
    else:
        logging.warning("Invalid file type attempted to upload")
        flash('Invalid file type. Only PDFs are allowed.', 'error')
        return redirect(url_for('offers.offers_list'))


@offers_bp.route('/offers/<int:offer_id>/files', methods=['GET'])
def manage_files(offer_id):
    files = db_execute(
        '''
        SELECT f.id, f.filename, f.filepath, f.upload_date
        FROM files f
        JOIN offer_files of ON f.id = of.file_id
        WHERE of.offer_id = ?
        ''',
        (offer_id,),
        fetch='all',
    )

    offer = db_execute('SELECT * FROM offers WHERE id = ?', (offer_id,), fetch='one')

    if offer is None:
        flash('Offer not found', 'error')
        return redirect(url_for('offers.offers_list'))

    return render_template('offer_manage_files.html', files=files, offer=offer, offer_id=offer_id)

@offers_bp.route('/offers/<int:offer_id>/upload', methods=['POST'])
def upload_file(offer_id):
    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('offers.manage_files', offer_id=offer_id))

    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect(url_for('offers.manage_files', offer_id=offer_id))

    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(
                cursor,
                'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?) RETURNING id',
                (filename, filepath, datetime.now()),
            )
            file_row = cursor.fetchone()
            file_id = _get_inserted_id(file_row, cursor)

            _execute_with_cursor(
                cursor,
                'INSERT INTO offer_files (offer_id, file_id) VALUES (?, ?)',
                (offer_id, file_id),
            )
            _execute_with_cursor(
                cursor,
                'UPDATE offers SET file_id = ? WHERE id = ?',
                (file_id, offer_id),
            )

        flash('File successfully uploaded and associated with the offer')
        return redirect(url_for('offers.manage_files', offer_id=offer_id))

@offers_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@offers_bp.route('/offers/view_file', methods=['GET'])
def view_offer_file():
    offer_id = request.args.get('offer_id')
    file_id = request.args.get('file_id')

    if not offer_id or not file_id:
        flash('Missing offer_id or file_id', 'error')
        return redirect(url_for('offers.offers_list'))

    file = db_execute(
        'SELECT filename, filepath FROM files WHERE id = ?',
        (file_id,),
        fetch='one',
    )

    if file:
        with open(file['filepath'], 'rb') as f:
            pdf = pdfplumber.open(f)
            text = ''
            for page in pdf.pages:
                text += page.extract_text()
            pdf.close()

        session['pdf_text'] = text
        session['pdf_filename'] = file['filename']
        return redirect(url_for('offers.view_pdf_text', offer_id=offer_id))

    flash('No file associated with this offer', 'error')
    return redirect(url_for('offers.edit_offer', offer_id=offer_id))


@offers_bp.route('/view_pdf_text', methods=['GET', 'POST'])
def view_pdf_text():
    offer_id = request.args.get('offer_id')
    file_id = request.args.get('file_id')

    if not offer_id:
        flash('No offer ID provided', 'error')
        return redirect(url_for('offers.offers_list'))

    offer = db_execute('SELECT * FROM offers WHERE id = ?', (offer_id,), fetch='one')

    if offer is None:
        flash(f'No offer found with ID {offer_id}', 'error')
        return redirect(url_for('offers.offers_list'))

    if request.method == 'POST':
        if 'offer_info' in request.form:
            offer_info = request.form['offer_info']
            extracted_lines = extract_part_numbers_and_quantities(offer_info)

            max_line_row = db_execute(
                'SELECT MAX(CAST(line_number AS INTEGER)) AS max_line FROM offer_lines WHERE offer_id = ?',
                (offer_id,),
                fetch='one',
            )
            max_line_number = max_line_row['max_line'] if max_line_row and max_line_row['max_line'] else 0

            try:
                with db_cursor(commit=True) as cursor:
                    for part_number, quantity in extracted_lines:
                        base_part_number = create_base_part_number(part_number)
                        price = 0.0
                        lead_time = 0
                        max_line_number += 1
                        current_line_number = max_line_number

                        _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO offer_lines (offer_id, base_part_number, line_number, manufacturer_id, quantity, price, lead_time)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ''',
                            (offer_id, base_part_number, current_line_number, None, quantity, price, lead_time),
                        )

                flash('Offer lines successfully extracted', 'success')
                return redirect(url_for('offers.view_pdf_text', offer_id=offer_id, file_id=file_id))
            except Exception as exc:
                logging.exception("Error extracting lines", exc_info=exc)
                flash('Error extracting offer lines', 'danger')
                return redirect(url_for('offers.view_pdf_text', offer_id=offer_id, file_id=file_id))
        else:
            supplier_reference = request.form['supplier_reference']
            valid_to = request.form['valid_to']

            with db_cursor(commit=True) as cursor:
                _execute_with_cursor(
                    cursor,
                    '''
                    UPDATE offers SET supplier_reference = ?, valid_to = ?
                    WHERE id = ?
                    ''',
                    (supplier_reference, valid_to, offer_id),
                )

            flash('Offer details updated successfully', 'success')
            return redirect(url_for('offers.view_pdf_text', offer_id=offer_id, file_id=file_id))

    if file_id:
        file = db_execute('SELECT * FROM files WHERE id = ?', (file_id,), fetch='one')
    else:
        file = db_execute('SELECT * FROM files WHERE id = ?', (offer['file_id'],), fetch='one')

    if file:
        with open(file['filepath'], 'rb') as f:
            pdf = pdfplumber.open(f)
            pdf_text = ''
            for page in pdf.pages:
                pdf_text += page.extract_text()
            pdf.close()
    else:
        pdf_text = 'No text available'

    pdf_filename = file['filename'] if file else None
    return render_template('view_pdf_text.html', pdf_text=pdf_text, pdf_filename=pdf_filename, offer=offer)

@offers_bp.route('/offers/<int:offer_id>/update_file', methods=['POST'], endpoint='update_offer_file')
def update_offer_file(offer_id):
    file_id = request.form['file_id']

    with db_cursor(commit=True) as cursor:
        _execute_with_cursor(
            cursor,
            'UPDATE offers SET file_id = ? WHERE id = ?',
            (file_id, offer_id),
        )

    flash('Offer file updated successfully', 'success')
    return redirect(url_for('offers.edit_offer', offer_id=offer_id))

@offers_bp.route('/api/requisitions/<int:supplier_id>', methods=['GET'])
def get_requisitions_for_supplier(supplier_id):
    requisitions = db_execute(
        '''
        SELECT r.id, rfq_id, c.name as customer_name, r.supplier_id
        FROM requisitions r
        JOIN customers c ON r.customer_id = c.id
        WHERE r.supplier_id = ?
        ''',
        (supplier_id,),
        fetch='all',
    )

    requisitions_list = [
        {'id': req['id'], 'rfq_id': req['rfq_id'], 'customer_name': req['customer_name'], 'supplier_id': req['supplier_id']}
        for req in requisitions
    ]

    return jsonify(requisitions=requisitions_list)

@offers_bp.route('/offers/<int:offer_id>/import_rfq_lines', methods=['POST'])
def import_rfq_lines(offer_id):
    try:
        data = request.get_json()
        req_id = data['rfq_id']  # This is actually the requisition ID
        logging.debug(f'Importing RFQ lines for Offer ID: {offer_id} and Requisition ID: {req_id}')

        rfq_lines = db_execute(
            '''
            SELECT rl.base_part_number, rl.line_number, rl.manufacturer_id, rl.quantity
            FROM rfq_lines rl
            JOIN requisitions r ON rl.rfq_id = r.rfq_id
            WHERE r.id = ?
            ''',
            (req_id,),
            fetch='all',
        )

        logging.debug(f'RFQ Lines: {rfq_lines}')

        with db_cursor(commit=True) as cursor:
            for line in rfq_lines:
                logging.debug(f'Inserting line: {line}')
                _execute_with_cursor(
                    cursor,
                    '''
                    INSERT INTO offer_lines (offer_id, base_part_number, line_number, manufacturer_id, quantity, price, lead_time)
                    VALUES (?, ?, ?, ?, ?, 0, 0)
                    ''',
                    (
                        offer_id,
                        line['base_part_number'],
                        line['line_number'],
                        line['manufacturer_id'],
                        line['quantity'],
                    ),
                )

        return jsonify({'success': True, 'message': 'RFQ lines successfully imported'})
    except Exception as e:
        logging.error(f'Error importing RFQ lines: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500


@offers_bp.route('/upload_pdf_test/<int:offer_id>', methods=['GET', 'POST'])
def upload_pdf_test(offer_id):
    extracted_table = []

    if request.method == 'POST':
        if 'pdf_file' in request.files:
            file = request.files['pdf_file']
            if file and file.filename.endswith('.pdf'):
                try:
                    with pdfplumber.open(file) as pdf:
                        for page in pdf.pages:
                            tables = page.extract_tables()
                            for table in tables:
                                for row in table:
                                    # Append the extracted row to the final list
                                    extracted_table.append(row)

                    # Render extracted table for manual column mapping
                    return render_template('pdf_column_mapping.html', extracted_table=extracted_table,
                                           offer_id=offer_id)
                except Exception as e:
                    return f"An error occurred: {str(e)}"

    return render_template('pdf_column_mapping.html', extracted_table=None, offer_id=offer_id)


@offers_bp.route('/map_columns/<int:offer_id>', methods=['POST'])
def map_columns(offer_id):
    column_mapping = {}

    # Collect the user's column mapping selections
    for key, value in request.form.items():
        if key.startswith('column_mapping_') and value:
            column_index = int(key.split('_')[-1])
            column_mapping[column_index] = value

    # Retrieve the extracted data from the previous step (for simplicity, you'd keep it in session or DB)
    extracted_table = session.get('extracted_table')  # Assuming the table is stored in session

    # Process the table with the user's mapping
    processed_data = []
    for row in extracted_table:
        row_data = {}
        for index, cell in enumerate(row):
            if index in column_mapping:
                field_name = column_mapping[index]
                row_data[field_name] = cell
        processed_data.append(row_data)

    # You can now use `processed_data` to update the database, save to an offer, etc.
    # For now, we'll just display the processed data for confirmation
    return render_template('pdf_mapped_data.html', processed_data=processed_data)


@offers_bp.route('/map_pdf_columns/<int:offer_id>', methods=['GET'])
def map_pdf_columns(offer_id):
    # Hard-coded file path for now
    file_path = os.path.join('uploads', 'NV-22413_2024.pdf')

    extracted_table = []

    try:
        # Open and extract the table using pdfplumber
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                # Extract tables in a more structured way
                tables = page.extract_tables()
                for table in tables:
                    extracted_table.append(table)

        # Render the template with the extracted table
        return render_template('pdf_column_mapping.html', extracted_table=extracted_table, offer_id=offer_id)
    except FileNotFoundError:
        return f"File not found at the given path: {file_path}", 404
    except Exception as e:
        return f"An error occurred: {str(e)}"


def parse_table_row(row_text):
    # Improved patterns to capture all information
    patterns = [
        r'^(\d+)',  # Item number
        r'(D\d+/\d+[A-Z]+)',  # Part Number
        r'(.+?)(?=\d+pcs)',  # Description (everything up to the quantity)
        r'(\d+pcs)',  # Quantity
        r'(Stock|STB \d+-\d+ wks)',  # Estimated Leadtime
        r'(\d+\.\d+)',  # Unit Price
        r'(\d+\.\d+)$'  # Total Price
    ]

    results = []
    remaining_text = row_text

    for pattern in patterns:
        match = re.search(pattern, remaining_text)
        if match:
            results.append(match.group(1).strip())
            remaining_text = remaining_text[match.end():].strip()
        else:
            results.append('')

    # If we have less than 7 columns, pad with empty strings
    results += [''] * (7 - len(results))

    return results

from pdf2image import convert_from_path
import numpy as np
from flask import render_template, jsonify
import os



@offers_bp.route('/check_supplier', methods=['POST'])
def check_supplier():
    data = request.json
    contact_email = data.get('contact_email')  # Updated to get 'contact_email' field

    # Add debugging to print the email being received
    print(f"Received contact_email: {contact_email}")

    if not contact_email:
        return jsonify({'error': 'Contact email is required'}), 400

    supplier = db_execute(
        'SELECT id FROM suppliers WHERE contact_email = ?',
        (contact_email,),
        fetch='one',
    )

    print(f"Query result: {supplier}")

    if supplier:
        return jsonify(supplier['id']), 200
    return jsonify({'error': 'Supplier not found'}), 404


@offers_bp.route('/offers/<int:offer_id>/test_offer_lines', methods=['POST'], endpoint='test_offer_lines')
def test_offer_lines(offer_id):
    offer_info = request.form.get('offer_info')

    # Log the offer_info to see if the data is being sent correctly
    print(f"Test Route - Received offer_info: {offer_info}")

    if not offer_info:
        return jsonify({'error': 'No offer_info received'}), 400

    # Just return success if data is received
    return jsonify({'success': True, 'offer_info': offer_info}), 200


@offers_bp.route('/api/new', methods=['POST'], endpoint='api_create_offer')
def api_create_offer():
    try:
        # Retrieve form data
        supplier_id = request.form.get('supplier_id')
        valid_to = request.form.get('valid_to')
        supplier_reference = request.form.get('supplier_reference')
        currency_id = request.form.get('currency_id')
        file = request.files.get('file')

        # Validate supplier_id
        if not supplier_id:
            return jsonify({'error': 'Supplier ID missing'}), 400

        file_id = None
        with db_cursor(commit=True) as cursor:
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)

                _execute_with_cursor(
                    cursor,
                    'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?) RETURNING id',
                    (filename, filepath, datetime.now()),
                )
                file_row = cursor.fetchone()
                file_id = _get_inserted_id(file_row, cursor)

            if not currency_id:
                _execute_with_cursor(cursor, 'SELECT currency FROM suppliers WHERE id = ?', (supplier_id,))
                supplier_currency = cursor.fetchone()
                currency_id = supplier_currency['currency'] if supplier_currency else None

            _execute_with_cursor(
                cursor,
                '''
                INSERT INTO offers (supplier_id, valid_to, supplier_reference, file_id, currency_id)
                VALUES (?, ?, ?, ?, ?) RETURNING id
                ''',
                (supplier_id, valid_to, supplier_reference, file_id, currency_id),
            )
            offer_row = cursor.fetchone()
            offer_id = _get_inserted_id(offer_row, cursor)

        return jsonify({'offer_id': offer_id}), 201

    except Exception as e:
        # Log the error and return a meaningful message
        print(f"Error creating offer: {str(e)}")
        return jsonify({'error': str(e)}), 500

def normalize_part_number(part_number: str) -> str:
    """
    Normalize part numbers by:
    1. Converting to uppercase
    2. Removing excess spaces
    3. Standardizing common separators
    4. Removing special characters
    """
    if not part_number:
        return ""

    # Convert to uppercase and trim
    normalized = part_number.upper().strip()

    # Replace multiple spaces/separators with single space
    normalized = re.sub(r'[\s\-\_\.]+', ' ', normalized)

    # Remove any other special characters
    normalized = re.sub(r'[^A-Z0-9\s]', '', normalized)

    return normalized

def get_part_segments(part_number: str) -> List[str]:
    """
    Break a part number into segments for comparison
    """
    normalized = normalize_part_number(part_number)
    return normalized.split()

def get_part_segments(part_number: str) -> List[str]:
    """
    Break a part number into segments for comparison
    """
    normalized = normalize_part_number(part_number)
    return normalized.split()


def calculate_part_similarity(part1: str, part2: str) -> Tuple[float, List[str]]:
    """
    Calculate similarity between two part numbers and return
    explanation of differences
    """
    norm1 = normalize_part_number(part1)
    norm2 = normalize_part_number(part2)

    if not norm1 or not norm2:
        return 0.0, ["One or both part numbers are empty"]

    # Get segments
    segments1 = get_part_segments(norm1)
    segments2 = get_part_segments(norm2)

    # Calculate overall string similarity
    full_similarity = SequenceMatcher(None, norm1, norm2).ratio()

    # Calculate segment matches
    segment_matches = 0
    total_segments = max(len(segments1), len(segments2))
    differences = []

    # Compare each segment
    for i in range(min(len(segments1), len(segments2))):
        seg1 = segments1[i]
        seg2 = segments2[i]

        # Check for exact or close matches
        if seg1 == seg2:
            segment_matches += 1
        else:
            seg_similarity = SequenceMatcher(None, seg1, seg2).ratio()
            if seg_similarity > 0.8:  # Very similar segments
                segment_matches += 0.8
            elif seg_similarity > 0.5:  # Somewhat similar segments
                segment_matches += 0.5
                differences.append(f"Segment {i + 1} differs: {seg1} vs {seg2}")
            else:
                differences.append(f"Segment {i + 1} differs significantly: {seg1} vs {seg2}")

    # Calculate final score based on both full string and segment matching
    segment_score = segment_matches / total_segments if total_segments > 0 else 0
    final_score = (full_similarity + segment_score) / 2

    return final_score, differences


@offers_bp.route('/get_recent_rfq_lines/<base_part_number>')
def get_recent_rfq_lines(base_part_number):
    try:
        threshold_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        query = '''
            SELECT DISTINCT
                rl.id,
                rl.rfq_id,
                rl.base_part_number,
                pn.part_number,
                r.entered_date as date,
                c.name as customer_name,
                COALESCE(s.status, 'Unknown') as status
            FROM rfq_lines rl
            JOIN rfqs r ON rl.rfq_id = r.id
            JOIN customers c ON r.customer_id = c.id
            JOIN part_numbers pn ON rl.base_part_number = pn.base_part_number
            LEFT JOIN statuses s ON rl.status_id = s.id
            WHERE r.entered_date >= ?
            ORDER BY r.entered_date DESC
        '''

        results = db_execute(query, (threshold_date,), fetch='all')

        # Process results and calculate similarity scores
        scored_results = []
        normalized_input = normalize_part_number(base_part_number)

        for row in results:
            row_dict = dict(row)
            similarity, differences = calculate_part_similarity(
                normalized_input,
                row_dict['base_part_number']
            )

            if similarity >= 0.6:  # Reduced threshold for more matches
                row_dict['match_score'] = int(similarity * 100)
                row_dict['differences'] = differences
                # Add normalized versions for comparison
                row_dict['normalized_input'] = normalized_input
                row_dict['normalized_match'] = normalize_part_number(row_dict['base_part_number'])
                scored_results.append(row_dict)

        # Sort by similarity score and date
        scored_results.sort(key=lambda x: (x['match_score'], x['date']), reverse=True)
        scored_results = scored_results[:10]

        return jsonify({
            'success': True,
            'rfq_lines': scored_results
        })

    except Exception as e:
        print(f"Error in get_recent_rfq_lines: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        })

def highlight_differences(str1, str2):
    """
    Returns HTML-formatted string highlighting differences between two strings
    """
    result = []
    for i, (c1, c2) in enumerate(zip_longest(str1, str2, fillvalue=None)):
        if c1 == c2:
            result.append(c2)
        else:
            if c2 is not None:
                result.append(f'<span class="text-danger">{c2}</span>')
    return ''.join(result)


@offers_bp.route('/update_part_number_match', methods=['POST'])
def update_part_number_match():
    try:
        data = request.get_json()
        offer_line_id = data.get('offer_line_id')
        requested_base_part_number = data.get('requested_base_part_number')

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(
                cursor,
                '''
                UPDATE offer_lines
                SET requested_base_part_number = ?
                WHERE id = ?
                ''',
                (requested_base_part_number, offer_line_id),
            )

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in update_part_number_match: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        })


@offers_bp.route('/api/customers')
def get_customers_api():
    print("Customers API called")  # Debug print
    customers = db_execute('SELECT * FROM customers', fetch='all')
    return jsonify({
        'customers': [dict(customer) for customer in customers]
    })


@offers_bp.route('/api/search_rfq_lines', methods=['POST'])
def search_rfq_lines():
    data = request.json

    query = '''
        SELECT 
            rl.id,
            rl.rfq_id,
            rl.base_part_number,
            rl.quantity,
            r.entered_date,
            c.name as customer_name
        FROM rfq_lines rl
        JOIN rfqs r ON rl.rfq_id = r.id
        JOIN customers c ON r.customer_id = c.id
        WHERE 1=1
    '''
    params = []

    if data.get('customer_id'):
        query += ' AND c.id = ?'
        params.append(data['customer_id'])

    if data.get('part_number'):
        query += ' AND rl.base_part_number LIKE ?'
        params.append(f'%{data["part_number"]}%')

    if data.get('rfq_number'):
        query += ' AND r.id LIKE ?'
        params.append(f'%{data["rfq_number"]}%')

    query += ' ORDER BY r.entered_date DESC LIMIT 50'

    results = db_execute(query, tuple(params), fetch='all')
    lines = [dict(row) for row in results]

    return jsonify({
        'rfq_lines': [{
            'rfq_id': line['rfq_id'],
            'date': line['entered_date'],
            'customer_name': line['customer_name'],
            'part_number': line['base_part_number'],
            'base_part_number': line['base_part_number'],
            'quantity': line['quantity']
        } for line in lines]
    })


@offers_bp.route('/process-offer', methods=['GET'])
def process_offer_page():
    try:
        # Path to your preloaded PDF
        pdf_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'NV-22413_2024.pdf')

        with open(pdf_path, 'rb') as pdf_file:
            pdf_reader = PdfReader(pdf_file)
            text_content = ""
            for page in pdf_reader.pages:
                text_content += page.extract_text()

        return render_template('process_offer.html',
                               pdf_path=url_for('static', filename='uploads/NV-22413_2024.pdf'),
                               text_content=text_content)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@offers_bp.route('/api/upload-offer', methods=['POST'])
def upload_offer():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if file and allowed_file(file.filename):
        try:
            # Read PDF content
            pdf_reader = PdfReader(io.BytesIO(file.read()))
            text_content = ""
            for page in pdf_reader.pages:
                text_content += page.extract_text()

            # Store the text content in session for later processing
            session['offer_text'] = text_content

            return jsonify({
                'success': True,
                'message': 'File uploaded successfully',
                'text_content': text_content
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    return jsonify({'error': 'Invalid file type'}), 400


@offers_bp.route('/api/process-offer-text', methods=['POST'])
def process_offer_text():
    text = request.json.get('text')
    examples = request.json.get('examples', [])

    print("Received text:", text[:100], "...")  # First 100 chars
    print("Received examples:", examples)  # Show what examples we got

    if not text:
        return jsonify({'error': 'No text provided'}), 400

    try:
        # Add logging here too
        if examples:
            print("Using extract_quote_info_with_examples")
            extracted_lines = extract_quote_info_with_examples(text, examples)
            print("Extracted lines from with_examples:", extracted_lines)
        else:
            print("Using regular extract_quote_info")
            extracted_lines = extract_quote_info(text)
            print("Extracted lines from regular:", extracted_lines)

        return jsonify({
            'success': True,
            'extracted_lines': extracted_lines
        })
    except Exception as e:
        print("Error occurred:", str(e))  # Add error logging
        return jsonify({'error': str(e)}), 500

def allowed_file(filename):
    # Add .msg and .eml to allowed extensions
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf', 'msg', 'eml'}


@offers_bp.route('/offers/<int:offer_id>/upload_email', methods=['POST'])
def upload_email(offer_id):
    if 'email_file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('offers.edit_offer', offer_id=offer_id))

    file = request.files['email_file']
    if file.filename == '' or not file.filename.endswith(('.msg', '.eml')):
        flash('Invalid or no file selected', 'error')
        return redirect(url_for('offers.edit_offer', offer_id=offer_id))

    try:
        filename = secure_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        msg = extract_msg.Message(filepath)
        email_content = msg.htmlBody if msg.htmlBody else msg.body

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(
                cursor,
                'UPDATE offers SET email_content = ? WHERE id = ?',
                (email_content, offer_id),
            )

            for attachment in msg.attachments:
                att_filename = secure_filename(attachment.longFilename)
                att_path = os.path.join(current_app.config['UPLOAD_FOLDER'], att_filename)

                with open(att_path, 'wb') as f:
                    f.write(attachment.data)

                _execute_with_cursor(
                    cursor,
                    'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?) RETURNING id',
                    (att_filename, att_path, datetime.now()),
                )
                file_row = cursor.fetchone()
                file_id = _get_inserted_id(file_row, cursor)

                _execute_with_cursor(
                    cursor,
                    'INSERT INTO offer_files (offer_id, file_id) VALUES (?, ?)',
                    (offer_id, file_id),
                )

        flash('Email uploaded and processed successfully!', 'success')

    except Exception as e:
        flash(f'Error processing email: {str(e)}', 'error')

    return redirect(url_for('offers.edit_offer', offer_id=offer_id))

@offers_bp.route('/offers/<int:offer_id>/view_email_frame', methods=['GET'])
def view_email_frame(offer_id):
    try:
        email_content = db_execute('SELECT email_content FROM offers WHERE id = ?', (offer_id,), fetch='one')

        if not email_content or not email_content['email_content']:
            return "No email content available.", 200

        return email_content['email_content']
    except Exception as e:
        return f"Error loading email content: {str(e)}", 500


@offers_bp.route('/process_pdf/<int:file_id>/<int:offer_id>')
def process_pdf(file_id, offer_id):
    try:
        file = db_execute('SELECT * FROM files WHERE id = ?', (file_id,), fetch='one')

        if not file:
            flash('File not found', 'error')
            return redirect(url_for('offers.offers_list'))

        with open(file['filepath'], 'rb') as pdf_file:
            pdf_reader = PdfReader(pdf_file)
            text_content = ""
            for page in pdf_reader.pages:
                text_content += page.extract_text()

        return render_template('process_offer.html',
                           pdf_path=url_for('offers.uploaded_file', filename=file['filename']),
                           text_content=text_content,
                           offer_id=offer_id)

    except Exception as e:
        flash(f'Error processing PDF: {str(e)}', 'error')
        return redirect(url_for('offers.offers_list'))


@offers_bp.route('/<int:offer_id>/add_lines', methods=['POST'])
def add_offer_lines(offer_id):
    data = request.json
    lines = data.get('lines', [])

    try:
        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(
                cursor,
                'SELECT MAX(CAST(line_number AS INTEGER)) AS max_line FROM offer_lines WHERE offer_id = ?',
                (offer_id,),
            )
            max_row = cursor.fetchone()
            max_line = max_row['max_line'] if max_row and max_row['max_line'] else 0
            current_line = int(max_line) + 1

            for line in lines:
                part_number = line['part_number']
                base_part_number = create_base_part_number(part_number)

                manufacturer_id = None
                manufacturer_name = line.get('manufacturer')
                if manufacturer_name:
                    _execute_with_cursor(
                        cursor,
                        'SELECT id FROM manufacturers WHERE name = ?',
                        (manufacturer_name,),
                    )
                    manufacturer = cursor.fetchone()
                    if manufacturer:
                        manufacturer_id = manufacturer['id']
                    else:
                        _execute_with_cursor(
                            cursor,
                            'INSERT INTO manufacturers (name) VALUES (?) RETURNING id',
                            (manufacturer_name,),
                        )
                        manufacturer_row = cursor.fetchone()
                        manufacturer_id = _get_inserted_id(manufacturer_row, cursor)

                _execute_with_cursor(
                    cursor,
                    '''
                    INSERT INTO part_numbers (base_part_number, part_number)
                    VALUES (?, ?)
                    ON CONFLICT(base_part_number, part_number) DO NOTHING
                    ''',
                    (base_part_number, part_number),
                )

                _execute_with_cursor(
                    cursor,
                    '''
                    INSERT INTO offer_lines (
                        offer_id,
                        base_part_number,
                        requested_base_part_number,
                        line_number,
                        manufacturer_id,
                        quantity,
                        price,
                        lead_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        offer_id,
                        base_part_number,
                        base_part_number,
                        current_line,
                        manufacturer_id,
                        line['quantity'],
                        line['price'],
                        line['lead_time'],
                    ),
                )

                current_line += 1

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


import math
from flask import request, flash, redirect, url_for, render_template, jsonify

@offers_bp.route('/rfq/<int:rfq_id>/compare', methods=['GET', 'POST'], endpoint='compare_offers')
def compare_offers(rfq_id):
    """
    Compare and select offers for RFQ lines with delivery cost amortization and
    minimum line value handling.
    """
    if request.method == 'POST':
        return handle_submit_selected_offers(rfq_id)
    else:
        return show_offer_comparison_page(rfq_id)


def show_offer_comparison_page(rfq_id):
    """
    Show the offer comparison page for the given RFQ with all available offers.
    """
    with db_cursor() as cursor:
        rfq = cursor.execute('SELECT * FROM rfqs WHERE id = ?', (rfq_id,)).fetchone()
        if not rfq:
            flash('RFQ not found', 'danger')
            return redirect(url_for('rfqs.rfqs'))

        rfq_lines = get_rfq_lines(cursor, rfq_id)
        suppliers = get_suppliers()
        supplier_settings = {str(s['id']): s for s in suppliers}

        for line in rfq_lines:
            line['available_offers'] = get_available_offers(cursor, line, exact_match_only=True)
            for offer in line['available_offers']:
                offer['extended_price'] = float(offer.get('price', 0)) * float(line.get('quantity', 0))
                supplier_id = str(offer.get('supplier_id', ''))
                supplier = supplier_settings.get(supplier_id, {})
                if 'minimum_line_value' in supplier:
                    offer['min_line_value'] = supplier['minimum_line_value'] or 0
                elif 'minimum_line_value' in offer:
                    offer['min_line_value'] = offer['minimum_line_value'] or 0
                else:
                    offer['min_line_value'] = 0

                offer['delivery_cost'] = supplier.get('delivery_cost', 0) or 0

                if offer['min_line_value'] > 0 and offer['extended_price'] < offer['min_line_value']:
                    offer['below_min'] = True
                    if float(offer.get('price', 0)) > 0:
                        offer['min_quantity'] = math.ceil(offer['min_line_value'] / float(offer.get('price', 0)))
                    else:
                        offer['min_quantity'] = line.get('quantity', 1)
                else:
                    offer['below_min'] = False

        try:
            currencies = cursor.execute('SELECT * FROM currencies').fetchall()
            currencies = [dict(currency) for currency in currencies]
        except:
            currencies = []

    return render_template('offer_comparison.html',
                           rfq=rfq,
                           rfq_lines=rfq_lines,
                           suppliers=suppliers,
                           supplier_settings=supplier_settings,
                           currencies=currencies)


def get_rfq_lines(cursor, rfq_id):
    """Get all RFQ lines for the given RFQ ID."""
    rfq_lines = cursor.execute('''
        SELECT rl.*, 
               pn.part_number,
               m.name as manufacturer_name
        FROM rfq_lines rl
        LEFT JOIN part_numbers pn ON rl.base_part_number = pn.base_part_number
        LEFT JOIN part_manufacturers pm ON rl.base_part_number = pm.base_part_number
        LEFT JOIN manufacturers m ON pm.manufacturer_id = m.id
        WHERE rl.rfq_id = ? AND rl.status_id != 8
        ORDER BY rl.line_number
    ''', (rfq_id,)).fetchall()

    # Convert to list of dicts
    return [dict(line) for line in rfq_lines]


def get_suppliers(cursor=None):
    """Get all suppliers with their settings, adding defaults when columns are missing."""
    columns = set(get_table_columns('suppliers'))
    select_fields = ['id', 'name', 'buffer', 'currency']
    if 'delivery_cost' in columns:
        select_fields.append('delivery_cost')
    if 'minimum_line_value' in columns:
        select_fields.append('minimum_line_value')

    query = f"SELECT {', '.join(select_fields)} FROM suppliers ORDER BY name"
    rows = db_execute(query, fetch='all') or []
    suppliers = [dict(row) for row in rows]

    for supplier in suppliers:
        supplier.setdefault('delivery_cost', 0)
        supplier.setdefault('minimum_line_value', 0)

    return suppliers


def get_available_offers(cursor, line, exact_match_only=False):
    """
    Get all available offers for a given RFQ line.
    When exact_match_only is True, only return direct matches.
    """
    base_part_number = line.get('base_part_number')
    part_number = line.get('part_number')

    if not base_part_number:
        print(f"Warning: RFQ line has no base_part_number")
        return []

    all_offers = []

    # Step 1: Direct match by base_part_number or requested_base_part_number
    try:
        query = '''
            SELECT ol.*, o.supplier_id, o.valid_to, o.supplier_reference,
                   s.name as supplier_name, m.name as manufacturer_name,
                   pn.part_number
            FROM offer_lines ol
            JOIN offers o ON ol.offer_id = o.id
            JOIN suppliers s ON o.supplier_id = s.id
            LEFT JOIN manufacturers m ON ol.manufacturer_id = m.id
            LEFT JOIN part_numbers pn ON ol.base_part_number = pn.base_part_number
            WHERE (ol.base_part_number = ? OR ol.requested_base_part_number = ?)
            ORDER BY o.supplier_id, o.valid_to DESC
        '''
        cursor.execute(query, (base_part_number, base_part_number))
        direct_matches = [dict(offer) for offer in cursor.fetchall()]
        if direct_matches:
            print(f"Found {len(direct_matches)} direct matches for {base_part_number}")
            all_offers.extend(direct_matches)
    except Exception as e:
        print(f"Error in direct matching: {str(e)}")

    # Only continue to other matching steps if exact_match_only is False
    if exact_match_only or all_offers:
        return all_offers

    # Step 3: Fuzzy match by part_number pattern
    if not all_offers and part_number:
        try:
            # Get all possible offers
            query = '''
                SELECT ol.*, o.supplier_id, o.valid_to, o.supplier_reference,
                       s.name as supplier_name, m.name as manufacturer_name,
                       pn.part_number
                FROM offer_lines ol
                JOIN offers o ON ol.offer_id = o.id
                JOIN suppliers s ON o.supplier_id = s.id
                LEFT JOIN manufacturers m ON ol.manufacturer_id = m.id
                LEFT JOIN part_numbers pn ON ol.base_part_number = pn.base_part_number
                ORDER BY o.supplier_id, o.valid_to DESC
            '''
            cursor.execute(query)
            all_possible_offers = [dict(offer) for offer in cursor.fetchall()]

            # Filter for similar part numbers
            fuzzy_matches = []
            clean_part = part_number.replace('-', '').replace(' ', '').upper()

            for offer in all_possible_offers:
                if offer.get('part_number'):
                    clean_offer_part = offer['part_number'].replace('-', '').replace(' ', '').upper()

                    # Check for similarity
                    if (clean_part in clean_offer_part or
                            clean_offer_part in clean_part or
                            (len(clean_part) > 3 and clean_part[:4] == clean_offer_part[:4])):
                        fuzzy_matches.append(offer)

            if fuzzy_matches:
                print(f"Found {len(fuzzy_matches)} fuzzy matches for {part_number}")
                all_offers.extend(fuzzy_matches)
        except Exception as e:
            print(f"Error in fuzzy matching: {str(e)}")

    # Step 4: Last resort - just show all offers if we still have none
    if not all_offers:
        try:
            query = '''
                SELECT ol.*, o.supplier_id, o.valid_to, o.supplier_reference,
                       s.name as supplier_name, m.name as manufacturer_name
                FROM offer_lines ol
                JOIN offers o ON ol.offer_id = o.id
                JOIN suppliers s ON o.supplier_id = s.id
                LEFT JOIN manufacturers m ON ol.manufacturer_id = m.id
                LIMIT 20
            '''
            cursor.execute(query)
            last_resort = [dict(offer) for offer in cursor.fetchall()]
            if last_resort:
                print(f"Using last resort: showing {len(last_resort)} offers for {base_part_number}")
                all_offers.extend(last_resort)
        except Exception as e:
            print(f"Error in last resort: {str(e)}")

    # Now apply date filtering only to the offers we've found
    valid_offers = []
    for offer in all_offers:
        # Check if valid_to is in the future (if it exists)
        if 'valid_to' not in offer or not offer['valid_to']:
            valid_offers.append(offer)
        else:
            try:
                # Only add if valid_to is in the future
                # But if we can't parse the date, include it anyway
                from datetime import datetime
                valid_to = datetime.strptime(offer['valid_to'], '%Y-%m-%d')
                if valid_to >= datetime.now():
                    valid_offers.append(offer)
            except:
                # If we can't parse the date, include it
                valid_offers.append(offer)

    # Remove duplicates by offer_id
    seen_offer_ids = set()
    unique_offers = []
    for offer in valid_offers:
        if offer['offer_id'] not in seen_offer_ids:
            seen_offer_ids.add(offer['offer_id'])
            unique_offers.append(offer)

    print(f"Final result: {len(unique_offers)} unique valid offers for {base_part_number}")
    return unique_offers


def search_for_similar_parts(cursor, part_number):
    """Search for offers with similar part numbers."""
    columns = set(get_table_columns('suppliers'))
    extra = ', s.minimum_line_value' if 'minimum_line_value' in columns else ''
    query = f'''
        SELECT ol.*, o.supplier_id, o.valid_to, o.supplier_reference,
               s.name as supplier_name{extra},
               m.name as manufacturer_name,
               pn.part_number
        FROM offer_lines ol
        JOIN offers o ON ol.offer_id = o.id
        JOIN suppliers s ON o.supplier_id = s.id
        LEFT JOIN manufacturers m ON ol.manufacturer_id = m.id
        LEFT JOIN part_numbers pn ON ol.base_part_number = pn.base_part_number
        WHERE o.valid_to >= ?
        ORDER BY o.supplier_id, o.valid_to DESC
    '''
    today = datetime.now().date().isoformat()
    _execute_with_cursor(cursor, query, (today,))
    all_offers = [dict(offer_line) for offer_line in cursor.fetchall()]
    similar_offers = []

    # Filter manually to find similarly named parts
    part_pattern = part_number.replace('-', '').replace(' ', '').upper()

    for offer in all_offers:
        if offer.get('part_number'):
            offer_pattern = offer['part_number'].replace('-', '').replace(' ', '').upper()
            # Check for pattern similarity
            if part_pattern in offer_pattern or offer_pattern in part_pattern or \
                    (len(part_pattern) > 3 and part_pattern[:4] == offer_pattern[:4]):
                similar_offers.append(offer)

    return similar_offers


def calculate_offer_details(line):
    """Calculate extended price and min line value requirements for each offer."""
    for offer in line['available_offers']:
        # Calculate extended price
        offer['extended_price'] = float(offer['price']) * float(line['quantity'])

        # Handle minimum line value
        if 'minimum_line_value' in offer:
            offer['min_line_value'] = offer['minimum_line_value'] or 0
        else:
            offer['min_line_value'] = 0

        # Flag if below minimum line value
        if offer['min_line_value'] > 0 and offer['extended_price'] < offer['min_line_value']:
            offer['below_min'] = True
            # Calculate minimum quantity to meet minimum line value
            if float(offer['price']) > 0:
                offer['min_quantity'] = math.ceil(offer['min_line_value'] / float(offer['price']))
            else:
                offer['min_quantity'] = line['quantity']
        else:
            offer['below_min'] = False


def handle_submit_selected_offers(rfq_id):
    """Process the form submission with selected offers."""
    try:
        selections = request.form.to_dict()

        # Extract settings per supplier
        supplier_settings = extract_supplier_settings(selections)

        # Update supplier settings and RFQ lines in a transaction
        with db_cursor(commit=True) as cursor:
            update_supplier_settings(cursor, supplier_settings)
            supplier_lines = group_lines_by_supplier(selections)
            update_rfq_lines(cursor, supplier_lines, supplier_settings)

        flash('Offers successfully applied to RFQ lines with delivery costs amortized', 'success')
        return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))

    except Exception as e:
        flash(f'Error applying offers: {str(e)}', 'danger')
        return redirect(url_for('offers.compare_offers', rfq_id=rfq_id))


def extract_supplier_settings(form_data):
    """Extract supplier settings from form data."""
    supplier_settings = {}

    for key, value in form_data.items():
        if key.startswith('delivery_cost_'):
            supplier_id = key.split('_')[-1]
            if supplier_id not in supplier_settings:
                supplier_settings[supplier_id] = {}
            supplier_settings[supplier_id]['delivery_cost'] = float(value) if value else 0
        elif key.startswith('min_line_value_'):
            supplier_id = key.split('_')[-1]
            if supplier_id not in supplier_settings:
                supplier_settings[supplier_id] = {}
            supplier_settings[supplier_id]['min_line_value'] = float(value) if value else 0

    return supplier_settings


def update_supplier_settings(cursor, supplier_settings):
    """Update supplier settings in the database."""
    columns = set(get_table_columns('suppliers'))
    if 'delivery_cost' not in columns:
        cursor.execute('ALTER TABLE suppliers ADD COLUMN delivery_cost NUMERIC(10, 2) DEFAULT 0')
        columns.add('delivery_cost')
    if 'minimum_line_value' not in columns:
        cursor.execute('ALTER TABLE suppliers ADD COLUMN minimum_line_value NUMERIC(10, 2) DEFAULT 0')
        columns.add('minimum_line_value')

    for supplier_id, settings in supplier_settings.items():
        delivery_cost = settings.get('delivery_cost', 0)
        min_line_value = settings.get('min_line_value', 0)

        _execute_with_cursor(cursor, '''
            UPDATE suppliers 
            SET delivery_cost = ?, minimum_line_value = ?
            WHERE id = ?
        ''', (delivery_cost, min_line_value, supplier_id))


def group_lines_by_supplier(form_data):
    """Group selected lines by supplier for delivery cost amortization."""
    supplier_lines = {}

    for key, value in form_data.items():
        if key.startswith('line_'):
            parts = key.split('_')
            line_id = parts[1]
            offer_data = value.split('|')

            if len(offer_data) >= 2:
                offer_id = offer_data[0]
                supplier_id = offer_data[1]

                if supplier_id not in supplier_lines:
                    supplier_lines[supplier_id] = []

                supplier_lines[supplier_id].append({
                    'line_id': line_id,
                    'offer_id': offer_id
                })

    return supplier_lines


def update_rfq_lines(cursor, supplier_lines, supplier_settings):
    """Update RFQ lines with selected offers and amortized delivery costs."""
    for supplier_id, lines in supplier_lines.items():
        # Get supplier settings
        delivery_cost = supplier_settings.get(supplier_id, {}).get('delivery_cost', 0)
        min_line_value = supplier_settings.get(supplier_id, {}).get('min_line_value', 0)

        # Calculate amortized delivery cost
        per_line_delivery = delivery_cost / len(lines) if lines else 0

        for line_item in lines:
            update_single_rfq_line(
                cursor,
                line_item,
                supplier_id,
                per_line_delivery,
                min_line_value
            )


def update_single_rfq_line(cursor, line_item, supplier_id, per_line_delivery, min_line_value):
    """Update a single RFQ line with selected offer and delivery cost."""
    line_id = line_item['line_id']
    offer_id = line_item['offer_id']

    # Get RFQ line data
    rfq_line = cursor.execute('SELECT quantity FROM rfq_lines WHERE id = ?', (line_id,)).fetchone()
    quantity = rfq_line['quantity'] if rfq_line else 1

    # Get offer line data
    offer_line = cursor.execute('''
        SELECT price, lead_time, base_part_number, datecode, spq, packaging, rohs, coc
        FROM offer_lines
        WHERE offer_id = ? AND (base_part_number = (
            SELECT base_part_number FROM rfq_lines WHERE id = ?
        ) OR requested_base_part_number = (
            SELECT base_part_number FROM rfq_lines WHERE id = ?
        ))
    ''', (offer_id, line_id, line_id)).fetchone()

    if not offer_line:
        print(f"No matching offer line found for rfq_line {line_id}, offer {offer_id}")
        return

    # Convert to dict
    offer_line = dict(offer_line)

    # Calculate effective cost including amortized delivery
    per_unit_delivery = per_line_delivery / quantity
    price = float(offer_line['price'])
    effective_price = price + per_unit_delivery

    # Check if this line meets the minimum line value requirement
    extended_price = price * quantity
    if min_line_value > 0 and extended_price < min_line_value:
        # Just log this - actual adjustment would be done via the UI
        print(f"Line {line_id} below minimum value ({extended_price} < {min_line_value})")

    # Get currency and buffer data from supplier
    cursor.execute('SELECT currency, buffer FROM suppliers WHERE id = ?', (supplier_id,))
    supplier_result = cursor.fetchone()
    cost_currency = supplier_result['currency'] if supplier_result else 'EUR'
    supplier_buffer = supplier_result['buffer'] if supplier_result else 0

    # Get exchange rate
    cursor.execute('SELECT exchange_rate_to_eur FROM currencies WHERE currency_code = ?', (cost_currency,))
    currency_result = cursor.fetchone()
    exchange_rate = currency_result['exchange_rate_to_eur'] if currency_result else 1

    base_cost = effective_price * exchange_rate
    lead_time = int(offer_line['lead_time']) + int(supplier_buffer)

    # Update the RFQ line
    cursor.execute('''
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
    ''', (
        offer_id,
        effective_price,
        offer_line['lead_time'],
        lead_time,
        supplier_id,
        cost_currency,
        base_cost,
        offer_line['base_part_number'],
        offer_line['datecode'],
        offer_line['spq'],
        offer_line['packaging'],
        offer_line['rohs'],
        offer_line['coc'],
        line_id
    ))


# Add these route handlers to the offers_bp Blueprint in offers.py

@offers_bp.route('/rfq/<int:rfq_id>/add_offer', methods=['GET'])
def add_offer_for_rfq(rfq_id):
    """
    Render a form to add a new offer for a specific RFQ.
    Shows all RFQ lines and allows pricing entry.
    """
    rfq = db_execute('SELECT * FROM rfqs WHERE id = ?', (rfq_id,), fetch='one')
    if not rfq:
        flash('RFQ not found', 'danger')
        return redirect(url_for('rfqs.rfqs'))

    rfq_lines = db_execute(
        '''
        SELECT rl.*, 
               pn.part_number,
               m.name as manufacturer_name
        FROM rfq_lines rl
        LEFT JOIN part_numbers pn ON rl.base_part_number = pn.base_part_number
        LEFT JOIN manufacturers m ON rl.manufacturer_id = m.id
        WHERE rl.rfq_id = ? AND rl.status_id != 8
        ORDER BY rl.line_number
        ''',
        (rfq_id,),
        fetch='all',
    )

    suppliers = db_execute('SELECT id, name FROM suppliers ORDER BY name', fetch='all')
    currencies = db_execute('SELECT * FROM currencies', fetch='all')

    default_valid_to = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')

    return render_template('add_offer_rfq.html',
                           rfq=rfq,
                           rfq_lines=rfq_lines,
                           suppliers=suppliers,
                           currencies=currencies,
                           default_valid_to=default_valid_to)


@offers_bp.route('/rfq/<int:rfq_id>/add_offer', methods=['POST'])
def submit_offer_for_rfq(rfq_id):
    """
    Process the submission of a new offer for a specific RFQ.
    Creates offer and associated offer lines.
    """
    supplier_id = request.form.get('supplier_id')
    valid_to = request.form.get('valid_to')
    supplier_reference = request.form.get('supplier_reference')
    currency_id = request.form.get('currency_id')

    # Validate required fields
    if not supplier_id:
        flash('Supplier is required', 'danger')
        return redirect(url_for('offers.add_offer_for_rfq', rfq_id=rfq_id))

    try:
        with db_cursor(commit=True) as cursor:
            if not currency_id:
                _execute_with_cursor(cursor, 'SELECT currency FROM suppliers WHERE id = ?', (supplier_id,))
                supplier_currency = cursor.fetchone()
                currency_id = supplier_currency['currency'] if supplier_currency else None

            _execute_with_cursor(
                cursor,
                '''
                INSERT INTO offers (supplier_id, valid_to, supplier_reference, currency_id)
                VALUES (?, ?, ?, ?) RETURNING id
                ''',
                (supplier_id, valid_to, supplier_reference, currency_id),
            )
            offer_row = cursor.fetchone()
            offer_id = _get_inserted_id(offer_row, cursor)

            _execute_with_cursor(
                cursor,
                '''
                SELECT rl.*, pn.part_number
                FROM rfq_lines rl
                LEFT JOIN part_numbers pn ON rl.base_part_number = pn.base_part_number
                WHERE rl.rfq_id = ?
                ''',
                (rfq_id,),
            )
            rfq_lines = [dict(line) for line in cursor.fetchall()]

            for line in rfq_lines:
                line_id = line['id']
                include_key = f'include_{line_id}'

                if include_key in request.form:
                    price = request.form.get(f'price_{line_id}')
                    moq = request.form.get(f'moq_{line_id}') or line['quantity']
                    lead_time = request.form.get(f'leadtime_{line_id}') or 0
                    datecode = request.form.get('datecode')
                    packaging = request.form.get('packaging')
                    rohs = 'rohs' in request.form
                    coc = 'coc' in request.form

                    if price and float(price) > 0:
                        _execute_with_cursor(
                            cursor,
                            'SELECT MAX(CAST(line_number AS INTEGER)) AS max_line FROM offer_lines WHERE offer_id = ?',
                            (offer_id,),
                        )
                        max_row = cursor.fetchone()
                        max_line = max_row['max_line'] if max_row and max_row['max_line'] else 0
                        line_number = int(max_line) + 1

                        _execute_with_cursor(
                            cursor,
                            '''
                            INSERT INTO offer_lines (
                                offer_id,
                                base_part_number,
                                requested_base_part_number,
                                line_number,
                                manufacturer_id,
                                quantity,
                                price,
                                lead_time,
                                datecode,
                                spq,
                                packaging,
                                rohs,
                                coc
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''',
                            (
                                offer_id,
                                line['base_part_number'],
                                line['base_part_number'],
                                line_number,
                                line['manufacturer_id'],
                                moq,
                                price,
                                lead_time,
                                datecode,
                                moq,
                                packaging,
                                rohs,
                                coc,
                            ),
                        )

        flash('Offer successfully created', 'success')
        return redirect(url_for('offers.edit_offer', offer_id=offer_id))
    except Exception as e:
        flash(f'Error creating offer: {str(e)}', 'danger')
        return redirect(url_for('offers.add_offer_for_rfq', rfq_id=rfq_id))


@offers_bp.route('/offers/<int:offer_id>/add_lines', methods=['POST'])
def add_multiple_offer_lines(offer_id):
    """
    Add multiple offer lines to an existing offer via AJAX.
    Simplified version with error handling.
    """
    try:
        print(f"Received request to add lines to offer {offer_id}")

        # Get and validate data
        data = request.json
        if not data or 'lines' not in data:
            print("No lines data in request")
            return jsonify({'error': 'No lines data provided'}), 400

        lines = data.get('lines', [])
        print(f"Processing {len(lines)} lines")

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, 'SELECT id FROM offers WHERE id = ?', (offer_id,))
            offer = cursor.fetchone()
            if not offer:
                return jsonify({'error': 'Offer not found'}), 404

            for i, line in enumerate(lines):
                try:
                    print(f"Processing line {i + 1}: {line}")
                    base_part_number = line.get('base_part_number')
                    if not base_part_number:
                        print(f"Skipping line {i + 1} - missing base_part_number")
                        continue

                    line_number = line.get('line_number', i + 1)
                    price = float(line.get('price', 0))
                    quantity = int(line.get('quantity', 1))
                    lead_time = int(line.get('lead_time', 0))

                    _execute_with_cursor(
                        cursor,
                        '''
                        INSERT INTO offer_lines (
                            offer_id, 
                            base_part_number, 
                            requested_base_part_number,
                            line_number, 
                            quantity, 
                            price, 
                            lead_time
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            offer_id,
                            base_part_number,
                            base_part_number,
                            line_number,
                            quantity,
                            price,
                            lead_time,
                        ),
                    )

                    print(f"Successfully inserted line {i + 1}")
                except Exception as line_error:
                    print(f"Error processing line {i + 1}: {str(line_error)}")
                    # Continue with next line instead of failing completely

        print(f"Successfully added {len(lines)} lines to offer {offer_id}")
        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in add_multiple_offer_lines: {str(e)}")
        return jsonify({'error': str(e)}), 500
