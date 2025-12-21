import os
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from werkzeug.utils import secure_filename
import extract_msg
from routes.emails import allowed_file
from models import (
    get_excess_stock_list_by_id,
    get_excess_list_line_by_id,
    get_excess_stock_lines,
    match_rfq_lines,
    match_sales_order_lines,
    get_customers,
    get_suppliers,
)
from db import db_cursor, execute as db_execute


excess_bp = Blueprint('excess', __name__)


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


@excess_bp.route('/excess_lists/new', methods=['GET', 'POST'])
def new_excess_list():
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        supplier_id = request.form.get('supplier_id')
        entered_date = request.form.get('entered_date')
        status = request.form.get('status', 'new')

        try:
            with db_cursor(commit=True) as cur:
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO excess_stock_lists (
                        customer_id, supplier_id, entered_date, status, upload_date
                    ) VALUES (?, ?, ?, ?, ?)
                    RETURNING id
                    ''',
                    (customer_id, supplier_id, entered_date, status, datetime.now())
                )
                row = cur.fetchone()
                new_list_id = row['id'] if row else None

            return str(new_list_id)
        except Exception as e:
            return f'Error creating excess list: {str(e)}', 400

    return render_template('excess_list_edit.html', customers=get_customers(), suppliers=get_suppliers())


@excess_bp.route('/excess_lists', methods=['GET', 'POST'])
def view_excess_lists():
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        supplier_id = request.form.get('supplier_id')
        entered_date = request.form.get('entered_date')

        try:
            db_execute(
                '''
                INSERT INTO excess_stock_lists (
                    customer_id, supplier_id, entered_date, upload_date
                ) VALUES (?, ?, ?, ?)
                ''',
                (customer_id, supplier_id, entered_date, datetime.now()),
                commit=True
            )
            flash('Excess stock list created successfully!', 'success')
        except Exception as e:
            flash(f'Error creating excess list: {str(e)}', 'error')

    excess_lists = db_execute(
        '''
        SELECT e.id, e.entered_date, c.name AS customer_name, s.name AS supplier_name
        FROM excess_stock_lists e
        LEFT JOIN customers c ON e.customer_id = c.id
        LEFT JOIN suppliers s ON e.supplier_id = s.id
        ''',
        fetch='all'
    ) or []

    return render_template('excess_lists.html',
                           excess_lists=excess_lists,
                           customers=get_customers(),
                           suppliers=get_suppliers())


@excess_bp.route('/excess_lists/<int:list_id>/edit', methods=['GET', 'POST'])
def edit_excess_list(list_id):
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        supplier_id = request.form.get('supplier_id')
        entered_date = request.form.get('entered_date')

        try:
            db_execute(
                '''
                UPDATE excess_stock_lists
                SET customer_id = ?, supplier_id = ?, entered_date = ?
                WHERE id = ?
                ''',
                (customer_id, supplier_id, entered_date, list_id),
                commit=True
            )
            flash('Excess stock list updated successfully!', 'success')
            return redirect(url_for('excess.view_excess_lists'))
        except Exception as e:
            flash(f'Error updating excess list: {str(e)}', 'error')

    try:
        with db_cursor() as cur:
            _execute_with_cursor(cur, 'SELECT * FROM excess_stock_lists WHERE id = ?', (list_id,))
            excess_list = cur.fetchone()

            if not excess_list:
                flash(f'Excess stock list with ID {list_id} not found!', 'error')
                return redirect(url_for('excess.view_excess_lists'))

            customers = get_customers()
            suppliers = get_suppliers()

            _execute_with_cursor(cur, 'SELECT * FROM excess_stock_lines WHERE excess_stock_list_id = ?', (list_id,))
            excess_stock_lines = [dict(row) for row in cur.fetchall()]

            _execute_with_cursor(cur, '''
                SELECT files.id, files.filename
                FROM files
                JOIN excess_stock_files ON files.id = excess_stock_files.file_id
                WHERE excess_stock_files.excess_stock_list_id = ?
            ''', (list_id,))
            attachments = [dict(row) for row in cur.fetchall()]

        excess_list_dict = dict(excess_list)
        return render_template('excess_list_edit.html',
                               excess_list=excess_list_dict,
                               customers=customers,
                               suppliers=suppliers,
                               attachments=attachments,
                               excess_list_lines=excess_stock_lines,
                               excess_stock_list_id=list_id)

    except Exception as e:
        flash(f'Error loading excess list: {str(e)}', 'error')
        return redirect(url_for('excess.view_excess_lists'))


@excess_bp.route('/excess_lists/<int:list_id>/view_email', methods=['GET'])
def view_email(list_id):
    email_row = db_execute('SELECT email FROM excess_stock_lists WHERE id = ?', (list_id,), fetch='one')
    if not email_row:
        flash('Email not found!', 'error')
        return redirect(url_for('excess.edit_excess_list', list_id=list_id))

    return render_template('view_email.html', html_body=email_row['email'], attachments=[])


@excess_bp.route('/excess_lists/<int:list_id>/upload_email', methods=['POST'])
def upload_email(list_id):
    excess_list = get_excess_stock_list_by_id(list_id)
    if not excess_list:
        flash('Excess list not found!', 'error')
        return redirect(url_for('excess.view_excess_lists'))

    if 'email_file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('excess.edit_excess_list', list_id=list_id))

    file = request.files['email_file']
    if not file or file.filename == '':
        flash('No selected file', 'error')
        return redirect(url_for('excess.edit_excess_list', list_id=list_id))

    if not allowed_file(file.filename):
        flash('Invalid file type. Only .eml and .msg files are allowed.', 'error')
        return redirect(url_for('excess.edit_excess_list', list_id=list_id))

    filename = secure_filename(file.filename)
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

    try:
        file.save(file_path)
        msg = extract_msg.Message(file_path)
        email_content = msg.htmlBody if msg.htmlBody else msg.body

        db_execute(
            'UPDATE excess_stock_lists SET email = ?, upload_date = ? WHERE id = ?',
            (email_content, datetime.now(), list_id),
            commit=True
        )
        flash('Email uploaded and processed successfully!', 'success')
    except Exception as e:
        flash(f'Error processing email: {str(e)}', 'error')

    return redirect(url_for('excess.edit_excess_list', list_id=list_id))


@excess_bp.route('/excess_lists/<int:list_id>/view_email_frame', methods=['GET'])
def view_email_frame(list_id):
    email_row = db_execute('SELECT email FROM excess_stock_lists WHERE id = ?', (list_id,), fetch='one')
    if not email_row or not email_row.get('email'):
        return "No email content available.", 200
    return email_row['email']


@excess_bp.route('/excess_list_lines/<int:line_id>', methods=['GET'])
def get_excess_list_line(line_id):
    excess_list_line = get_excess_list_line_by_id(line_id)
    if not excess_list_line:
        flash(f'Excess list line with ID {line_id} not found!', 'error')
        return redirect(url_for('excess.view_excess_lists'))
    return render_template('excess_list_line.html', excess_list_line=excess_list_line)


@excess_bp.route('/match_excess/<int:excess_stock_list_id>', methods=['GET'])
def match_excess(excess_stock_list_id):
    excess_stock_lines = get_excess_stock_lines(excess_stock_list_id)
    rfq_matches = match_rfq_lines(excess_stock_list_id)
    sales_order_matches = match_sales_order_lines(excess_stock_list_id)
    return render_template('match_excess.html',
                           excess_stock_lines=excess_stock_lines,
                           rfq_matches=rfq_matches,
                           sales_order_matches=sales_order_matches)


@excess_bp.route('/excess_lists/<int:list_id>/update_email', methods=['POST'])
def update_excess_list_email(list_id):
    email_body = request.form.get('email')
    try:
        db_execute(
            'UPDATE excess_stock_lists SET email = ? WHERE id = ?',
            (email_body, list_id),
            commit=True
        )
        return str(list_id)
    except Exception as e:
        return f'Error updating email content: {str(e)}', 400
