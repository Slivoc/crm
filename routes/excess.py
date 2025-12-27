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


def _parse_optional_int(value):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@excess_bp.route('/excess_lists/new', methods=['GET', 'POST'])
def new_excess_list():
    if request.method == 'POST':
        name = request.form.get('name')
        customer_id = _parse_optional_int(request.form.get('customer_id'))
        supplier_id = _parse_optional_int(request.form.get('supplier_id'))
        entered_date = request.form.get('entered_date')
        status = request.form.get('status', 'new')

        try:
            with db_cursor(commit=True) as cur:
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO excess_stock_lists (
                        name, customer_id, supplier_id, entered_date, status, upload_date
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    RETURNING id
                    ''',
                    (name, customer_id, supplier_id, entered_date, status, datetime.now())
                )
                row = cur.fetchone()
                new_list_id = row['id'] if row else None

            return str(new_list_id)
        except Exception as e:
            return f'Error creating excess list: {str(e)}', 400

    return render_template('excess_list_edit.html')


@excess_bp.route('/excess_lists', methods=['GET', 'POST'])
def view_excess_lists():
    if request.method == 'POST':
        name = request.form.get('name')
        customer_id = _parse_optional_int(request.form.get('customer_id'))
        supplier_id = _parse_optional_int(request.form.get('supplier_id'))
        entered_date = request.form.get('entered_date')

        try:
            db_execute(
                '''
                INSERT INTO excess_stock_lists (
                    name, customer_id, supplier_id, entered_date, upload_date
                ) VALUES (?, ?, ?, ?, ?)
                ''',
                (name, customer_id, supplier_id, entered_date, datetime.now()),
                commit=True
            )
            flash('Excess stock list created successfully!', 'success')
        except Exception as e:
            flash(f'Error creating excess list: {str(e)}', 'error')

    excess_lists = db_execute(
        '''
        SELECT e.id, e.name, e.entered_date, c.name AS customer_name, s.name AS supplier_name
        FROM excess_stock_lists e
        LEFT JOIN customers c ON e.customer_id = c.id
        LEFT JOIN suppliers s ON e.supplier_id = s.id
        ''',
        fetch='all'
    ) or []

    return render_template('excess_lists.html',
                           excess_lists=excess_lists)


@excess_bp.route('/excess_lists/<int:list_id>/edit', methods=['GET', 'POST'])
def edit_excess_list(list_id):
    if request.method == 'POST':
        name = request.form.get('name')
        customer_id = _parse_optional_int(request.form.get('customer_id'))
        supplier_id = _parse_optional_int(request.form.get('supplier_id'))
        entered_date = request.form.get('entered_date')

        try:
            db_execute(
                '''
                UPDATE excess_stock_lists
                SET name = ?, customer_id = ?, supplier_id = ?, entered_date = ?
                WHERE id = ?
                ''',
                (name, customer_id, supplier_id, entered_date, list_id),
                commit=True
            )
            flash('Excess stock list updated successfully!', 'success')
            return redirect(url_for('excess.view_excess_lists'))
        except Exception as e:
            flash(f'Error updating excess list: {str(e)}', 'error')

    try:
        search_query = (request.args.get('q') or '').strip()
        show_all = str(request.args.get('show_all', '')).lower() in ('1', 'true', 'on', 'yes')
        line_limit = 50
        search_limit = 200

        with db_cursor() as cur:
            _execute_with_cursor(cur, 'SELECT * FROM excess_stock_lists WHERE id = ?', (list_id,))
            excess_list = cur.fetchone()

            if not excess_list:
                flash(f'Excess stock list with ID {list_id} not found!', 'error')
                return redirect(url_for('excess.view_excess_lists'))

            _execute_with_cursor(cur, '''
                SELECT c.name AS customer_name, s.name AS supplier_name
                FROM excess_stock_lists e
                LEFT JOIN customers c ON e.customer_id = c.id
                LEFT JOIN suppliers s ON e.supplier_id = s.id
                WHERE e.id = ?
            ''', (list_id,))
            names_row = cur.fetchone()

            if search_query:
                search_param = f'%{search_query}%'
                if _using_postgres():
                    line_query = '''
                        SELECT l.*, c.currency_code AS unit_price_currency_code
                        FROM excess_stock_lines l
                        LEFT JOIN currencies c ON c.id = l.unit_price_currency_id
                        WHERE l.excess_stock_list_id = ?
                          AND (
                              l.base_part_number ILIKE ?
                              OR l.part_number ILIKE ?
                              OR l.manufacturer ILIKE ?
                              OR l.date_code ILIKE ?
                          )
                        ORDER BY l.id
                        LIMIT ?
                    '''
                else:
                    line_query = '''
                        SELECT l.*, c.currency_code AS unit_price_currency_code
                        FROM excess_stock_lines l
                        LEFT JOIN currencies c ON c.id = l.unit_price_currency_id
                        WHERE l.excess_stock_list_id = ?
                          AND (
                              LOWER(l.base_part_number) LIKE LOWER(?)
                              OR LOWER(l.part_number) LIKE LOWER(?)
                              OR LOWER(l.manufacturer) LIKE LOWER(?)
                              OR LOWER(l.date_code) LIKE LOWER(?)
                          )
                        ORDER BY l.id
                        LIMIT ?
                    '''
                _execute_with_cursor(cur, line_query, (list_id, search_param, search_param, search_param, search_param, search_limit))
                excess_stock_lines = [dict(row) for row in cur.fetchall()]
            else:
                line_query = '''
                    SELECT l.*, c.currency_code AS unit_price_currency_code
                    FROM excess_stock_lines l
                    LEFT JOIN currencies c ON c.id = l.unit_price_currency_id
                    WHERE l.excess_stock_list_id = ?
                    ORDER BY l.id
                '''
                params = [list_id]
                if not show_all:
                    line_query += ' LIMIT ?'
                    params.append(line_limit)
                _execute_with_cursor(cur, line_query, params)
                excess_stock_lines = [dict(row) for row in cur.fetchall()]

            _execute_with_cursor(cur, '''
                SELECT files.id, files.filename
                FROM files
                JOIN excess_stock_files ON files.id = excess_stock_files.file_id
                WHERE excess_stock_files.excess_stock_list_id = ?
            ''', (list_id,))
            attachments = [dict(row) for row in cur.fetchall()]

        excess_list_dict = dict(excess_list)
        if names_row:
            if isinstance(names_row, dict):
                excess_list_dict['customer_name'] = names_row.get('customer_name')
                excess_list_dict['supplier_name'] = names_row.get('supplier_name')
            else:
                excess_list_dict['customer_name'] = names_row[0] if len(names_row) > 0 else None
                excess_list_dict['supplier_name'] = names_row[1] if len(names_row) > 1 else None

        total_lines_row = db_execute(
            'SELECT COUNT(*) AS count FROM excess_stock_lines WHERE excess_stock_list_id = ?',
            (list_id,),
            fetch='one',
        )
        total_lines = total_lines_row['count'] if total_lines_row else 0

        filtered_count = None
        if search_query:
            if _using_postgres():
                count_query = '''
                    SELECT COUNT(*) AS count
                    FROM excess_stock_lines
                    WHERE excess_stock_list_id = ?
                      AND (
                          base_part_number ILIKE ?
                          OR part_number ILIKE ?
                          OR manufacturer ILIKE ?
                          OR date_code ILIKE ?
                      )
                '''
            else:
                count_query = '''
                    SELECT COUNT(*) AS count
                    FROM excess_stock_lines
                    WHERE excess_stock_list_id = ?
                      AND (
                          LOWER(base_part_number) LIKE LOWER(?)
                          OR LOWER(part_number) LIKE LOWER(?)
                          OR LOWER(manufacturer) LIKE LOWER(?)
                          OR LOWER(date_code) LIKE LOWER(?)
                      )
                '''
            count_row = db_execute(
                count_query,
                (list_id, f'%{search_query}%', f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'),
                fetch='one',
            )
            filtered_count = count_row['count'] if count_row else 0

        return render_template('excess_list_edit.html',
                               excess_list=excess_list_dict,
                               attachments=attachments,
                               excess_list_lines=excess_stock_lines,
                               excess_stock_list_id=list_id,
                               total_lines=total_lines,
                               filtered_lines=filtered_count,
                               line_limit=line_limit,
                               line_show_all=show_all,
                               line_search_query=search_query)

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
    wipe_existing = str(request.form.get('wipe_existing', '')).lower() in ('1', 'true', 'on', 'yes')

    try:
        file.save(file_path)
        msg = extract_msg.Message(file_path)
        email_content = msg.htmlBody if msg.htmlBody else msg.body

        with db_cursor(commit=True) as cur:
            if wipe_existing:
                _execute_with_cursor(
                    cur,
                    'DELETE FROM excess_stock_lines WHERE excess_stock_list_id = ?',
                    (list_id,),
                )
                _execute_with_cursor(
                    cur,
                    'DELETE FROM excess_stock_files WHERE excess_stock_list_id = ?',
                    (list_id,),
                )
                _execute_with_cursor(
                    cur,
                    'UPDATE excess_stock_lists SET email = NULL WHERE id = ?',
                    (list_id,),
                )
            _execute_with_cursor(
                cur,
                'UPDATE excess_stock_lists SET email = ?, upload_date = ? WHERE id = ?',
                (email_content, datetime.now(), list_id),
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


@excess_bp.route('/excess_lists/<int:list_id>/delete', methods=['POST'])
def delete_excess_list(list_id):
    try:
        exists = db_execute('SELECT 1 FROM excess_stock_lists WHERE id = ?', (list_id,), fetch='one')
        if not exists:
            return jsonify(success=False, message='Excess list not found'), 404

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, 'DELETE FROM excess_stock_lines WHERE excess_stock_list_id = ?', (list_id,))
            _execute_with_cursor(cur, 'DELETE FROM excess_stock_files WHERE excess_stock_list_id = ?', (list_id,))
            _execute_with_cursor(cur, 'DELETE FROM excess_stock_lists WHERE id = ?', (list_id,))

        return jsonify(success=True)
    except Exception as e:
        current_app.logger.exception("Error deleting excess list")
        return jsonify(success=False, message=str(e)), 500
