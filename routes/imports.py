from flask import Blueprint, jsonify, request, render_template, current_app, url_for, redirect
import json
from werkzeug.exceptions import BadRequest
from datetime import date, datetime
import os
import io
import zipfile
from werkzeug.utils import secure_filename
import pandas as pd

from db import db_cursor, execute as db_execute

imports_bp = Blueprint('imports', __name__, url_prefix='/imports')


def _using_postgres():
    """Detect whether DATABASE_URL indicates a Postgres connection."""
    return bool(os.getenv('DATABASE_URL'))


def _prepare_query(query):
    """Translate SQLite '?' placeholders to Postgres '%s' when needed."""
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    """Run a query on the provided cursor with placeholder translation."""
    cur.execute(_prepare_query(query), params or [])
    return cur


def _parse_date(value):
    """Normalize DB date/datetime/strings to a datetime for comparison."""
    if not value:
        return None

    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())

    value_str = str(value).strip()
    if not value_str:
        return None

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(value_str, fmt)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(value_str)
    except ValueError:
        return None


def _format_latest(value):
    """Return a friendly date string for display or None."""
    parsed = _parse_date(value)
    return parsed.strftime("%b %d, %Y") if parsed else None


def _get_latest_date(query):
    """Fetch MAX(date) style queries and normalize the result."""
    row = db_execute(query, fetch='one')
    if not row:
        return None

    latest = row['latest'] if isinstance(row, dict) else row[0]
    return _parse_date(latest)


def _combine_latest(*values):
    """Return the most recent non-null datetime from provided values."""
    filtered = [v for v in values if v]
    return max(filtered) if filtered else None


class ImportHelpers:
    """Helper class for import operations using shared DB helpers."""
    
    def lookup_part_number(self, system_part_number):
        result = db_execute(
            "SELECT base_part_number FROM part_numbers WHERE system_part_number = ?",
            (system_part_number,),
            fetch='one'
        )
        return result['base_part_number'] if result else None

    def lookup_customer(self, system_code):
        result = db_execute(
            "SELECT id FROM customers WHERE system_code = ?",
            (system_code,),
            fetch='one'
        )
        return result['id'] if result else None


def _save_import_file_bytes(filename, content_bytes, import_type='stock_levels'):
    safe_name = secure_filename(filename or 'mailbox_import.xlsx')
    upload_dir = os.path.join(current_app.root_path, 'uploads')
    os.makedirs(upload_dir, exist_ok=True)

    base, ext = os.path.splitext(safe_name)
    candidate = safe_name
    counter = 1
    while os.path.exists(os.path.join(upload_dir, candidate)):
        candidate = f"{base}_{counter}{ext}"
        counter += 1

    filepath = os.path.join(upload_dir, candidate)
    with open(filepath, 'wb') as handle:
        handle.write(content_bytes)

    insert_sql = """
        INSERT INTO files (filename, filepath, upload_date, import_type)
        VALUES (?, ?, ?, ?)
    """
    with db_cursor(commit=True) as cur:
        if _using_postgres():
            _execute_with_cursor(cur, insert_sql + " RETURNING id", (candidate, filepath, datetime.now(), import_type))
            row = cur.fetchone()
            file_id = row['id'] if isinstance(row, dict) else row[0]
        else:
            _execute_with_cursor(cur, insert_sql, (candidate, filepath, datetime.now(), import_type))
            file_id = getattr(cur, 'lastrowid', None)

    return file_id, filepath, candidate


def _fetch_mailbox_attachment_bytes(message_id, attachment_id):
    from routes.emails import (
        _build_msal_app,
        _get_graph_settings,
        _load_graph_cache,
        _save_graph_cache,
    )
    import requests
    from urllib.parse import quote

    settings = _get_graph_settings(include_secret=True)
    cache = _load_graph_cache()
    app = _build_msal_app(settings, cache=cache)
    accounts = app.get_accounts()
    if not accounts:
        raise BadRequest('No Graph account connected. Click Connect with Microsoft first.')

    token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
    _save_graph_cache(cache)
    if not token or "access_token" not in token:
        raise BadRequest('Failed to refresh Graph access token')

    headers = {"Authorization": f"Bearer {token['access_token']}"}
    safe_message_id = quote(message_id, safe="")
    safe_attachment_id = quote(attachment_id, safe="")
    response = requests.get(
        f"https://graph.microsoft.com/v1.0/me/messages/{safe_message_id}/attachments/{safe_attachment_id}",
        headers=headers,
        timeout=20,
    )

    try:
        payload = response.json() if response.content else None
    except ValueError:
        payload = None

    if response.status_code >= 400 or not isinstance(payload, dict):
        raise BadRequest('Failed to fetch mailbox attachment')

    content_b64 = payload.get('contentBytes')
    if not content_b64:
        raise BadRequest('Mailbox attachment has no content')

    import base64
    return {
        'name': payload.get('name') or 'attachment',
        'content_type': payload.get('contentType') or '',
        'bytes': base64.b64decode(content_b64),
    }


def _extract_vs_inventory_workbook(zip_bytes):
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise BadRequest('Attachment is not a valid ZIP file') from exc

    workbook_names = [
        info.filename for info in archive.infolist()
        if not info.is_dir() and info.filename.lower().endswith(('.xlsx', '.xls'))
    ]
    if not workbook_names:
        raise BadRequest('ZIP file does not contain an Excel workbook')
    if len(workbook_names) > 1:
        raise BadRequest('ZIP file contains multiple Excel workbooks; expected exactly one')

    workbook_name = workbook_names[0]
    workbook_bytes = archive.read(workbook_name)
    return workbook_name, workbook_bytes


def _build_vs_stock_dataframe(workbook_bytes):
    df = pd.read_excel(io.BytesIO(workbook_bytes))
    df.columns = [str(col).strip() for col in df.columns]

    required_cols = ['Part_Number', 'QTY_Rem', 'Warehouse']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise BadRequest(f"Workbook missing required column(s): {', '.join(missing_cols)}")

    main_mask = df['Warehouse'].astype(str).str.strip().str.upper() == 'MAIN'
    filtered = df.loc[main_mask].copy()
    filtered['Part_Number'] = filtered['Part_Number'].astype(str).str.strip()
    filtered['QTY_Rem'] = pd.to_numeric(filtered['QTY_Rem'], errors='coerce')
    filtered = filtered[filtered['Part_Number'].ne('')]
    filtered = filtered[filtered['Part_Number'].str.lower().ne('nan')]
    filtered = filtered[filtered['QTY_Rem'].fillna(0) > 0]

    output = pd.DataFrame({
        'partNumber': filtered['Part_Number'],
        'remainingQty': filtered['QTY_Rem'],
    })
    output['unitCost'] = filtered['LastSourceCost'] if 'LastSourceCost' in filtered.columns else None
    return output


@imports_bp.route('/mappings', methods=['GET'])
def get_mappings():
    """Get all saved mappings for a specific import type"""
    import_type = request.args.get('type', 'sales_orders')

    mappings = db_execute(
        """
            SELECT id, name, mapping, is_default
            FROM import_column_maps
            WHERE import_type = ?
            ORDER BY is_default DESC, name
        """,
        (import_type,),
        fetch='all'
    ) or []

    return jsonify([dict(row) for row in mappings])


@imports_bp.route('/mappings', methods=['POST'])
def save_mapping():
    """Save a new column mapping"""
    data = request.get_json()

    # Basic validation
    required_fields = ['name', 'import_type', 'mapping']
    if not all(field in data for field in required_fields):
        raise BadRequest('Missing required fields')

    try:
        with db_cursor(commit=True) as cur:
            if data.get('is_default'):
                _execute_with_cursor(
                    cur,
                    "UPDATE import_column_maps SET is_default = 0 WHERE import_type = ?",
                    (data['import_type'],),
                )

            insert_sql = """
                INSERT INTO import_column_maps (name, import_type, mapping, is_default)
                VALUES (?, ?, ?, ?)
            """

            if _using_postgres():
                _execute_with_cursor(
                    cur,
                    insert_sql + " RETURNING id",
                    (
                        data['name'],
                        data['import_type'],
                        json.dumps(data['mapping']),
                        data.get('is_default', False),
                    ),
                )
                row = cur.fetchone()
                mapping_id = row['id'] if isinstance(row, dict) else row[0]
            else:
                _execute_with_cursor(
                    cur,
                    insert_sql,
                    (
                        data['name'],
                        data['import_type'],
                        json.dumps(data['mapping']),
                        data.get('is_default', False),
                    ),
                )
                mapping_id = getattr(cur, 'lastrowid', None)

            if 'headers' in data:
                for header in data['headers']:
                    _execute_with_cursor(
                        cur,
                        """
                            INSERT INTO import_headers (import_column_map_id, column_name, sample_value)
                            VALUES (?, ?, ?)
                        """,
                        (mapping_id, header['name'], header.get('sample')),
                    )

        return jsonify({'id': mapping_id, 'message': 'Mapping saved successfully'})

    except Exception as e:
        raise BadRequest(f'Database error: {str(e)}')


# Error handlers
@imports_bp.errorhandler(BadRequest)
def handle_bad_request(e):
    return jsonify(error=str(e)), 400


@imports_bp.errorhandler(Exception)
def handle_db_error(e):
    current_app.logger.error(f"Database error: {str(e)}")
    return jsonify(error=f"Database error: {str(e)}"), 500


@imports_bp.route('/files', methods=['GET'])
def list_files():
    """Show file upload page and list existing files with their import types"""
    files = db_execute(
        """
            SELECT f.*, 
                   EXISTS(SELECT 1 FROM import_status WHERE file_id = f.id) as import_status,
                   (SELECT import_type FROM import_status 
                    WHERE file_id = f.id 
                    ORDER BY created_at DESC LIMIT 1) as processed_import_type
            FROM files f
            ORDER BY upload_date DESC
        """,
        fetch='all'
    ) or []

    processed_files = []
    for file in files:
        file_dict = dict(file)

        # Use the processed import type if available, otherwise use the stored import type
        if file_dict.get('processed_import_type'):
            file_dict['import_type'] = file_dict['processed_import_type']

        processed_files.append(file_dict)

    return render_template('import_files.html', files=processed_files)


@imports_bp.route('/files/upload', methods=['POST'])
def upload_file():
    """Handle file upload with optional import type"""
    if 'file' not in request.files:
        return jsonify(success=False, message="No file provided"), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify(success=False, message="No file selected"), 400

    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify(success=False, message="Only Excel files are allowed"), 400

    # Get optional import type
    import_type = request.form.get('import_type', None)

    try:
        # Secure the filename
        filename = secure_filename(file.filename)

        # Ensure upload directory exists
        upload_dir = os.path.join(current_app.root_path, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)

        # Generate unique filename if needed
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(os.path.join(upload_dir, filename)):
            filename = f"{base}_{counter}{ext}"
            counter += 1

        # Save the file
        filepath = os.path.join(upload_dir, filename)
        file.save(filepath)

        # Record in database
        insert_sql = """
            INSERT INTO files (filename, filepath, upload_date, import_type)
            VALUES (?, ?, ?, ?)
        """
        file_id = None
        with db_cursor(commit=True) as cur:
            if _using_postgres():
                _execute_with_cursor(cur, insert_sql + " RETURNING id", (filename, filepath, datetime.now(), import_type))
                row = cur.fetchone()
                file_id = row['id'] if isinstance(row, dict) else row[0]
            else:
                _execute_with_cursor(cur, insert_sql, (filename, filepath, datetime.now(), import_type))
                file_id = getattr(cur, 'lastrowid', None)

        return jsonify(success=True, file_id=file_id)

    except Exception as e:
        current_app.logger.error(f"File upload error: {str(e)}")
        return jsonify(success=False, message="Error uploading file"), 500


@imports_bp.route('/files/<int:file_id>', methods=['DELETE'])
def delete_file(file_id):
    """Delete a file"""
    try:
        file = db_execute("SELECT filepath FROM files WHERE id = ?", (file_id,), fetch='one')

        if not file:
            return jsonify(success=False, message="File not found"), 404

        # Delete physical file
        if os.path.exists(file['filepath']):
            os.remove(file['filepath'])

        # Delete from database
        db_execute("DELETE FROM files WHERE id = ?", (file_id,), commit=True)

        return jsonify(success=True)

    except Exception as e:
        current_app.logger.error(f"File deletion error: {str(e)}")
        return jsonify(success=False, message="Error deleting file"), 500


@imports_bp.route('/files/<int:file_id>/start', methods=['GET'])
def start_mapping(file_id):
    """Redirect to handson mapping interface"""
    # Verify file exists
    file = db_execute("SELECT id FROM files WHERE id = ?", (file_id,), fetch='one')
    if not file:
        return "File not found", 404

    # Redirect to handson mapping interface
    return redirect(url_for('handson.view_file_in_handson', file_id=file_id))

# Add these to imports_bp.py (the first file)

@imports_bp.route('/part_numbers', methods=['GET'])
def list_part_number_imports():
    """Show part number import page and list existing files"""
    files = db_execute(
        """
            SELECT f.*, 
                   EXISTS(SELECT 1 FROM import_status WHERE file_id = f.id AND import_type = 'part_numbers') as import_status
            FROM files f
            ORDER BY upload_date DESC
        """,
        fetch='all'
    ) or []

    return render_template('part_number_imports.html', files=files)

@imports_bp.route('/purchase_orders', methods=['GET'])
def list_purchase_order_imports():
    """Show purchase order import page and list existing files"""
    files = db_execute(
        """
            SELECT f.*, 
                   EXISTS(SELECT 1 FROM import_status WHERE file_id = f.id AND import_type = 'purchase_orders') as import_status
            FROM files f
            ORDER BY upload_date DESC
        """,
        fetch='all'
    ) or []

    return render_template('purchase_order_imports.html', files=files)


@imports_bp.route('/stock_movements', methods=['GET'])
def list_stock_movement_imports():
    """Show stock movement import page and list existing files"""
    files = db_execute(
        """
            SELECT f.*, 
                   EXISTS(SELECT 1 FROM import_status WHERE file_id = f.id AND import_type = 'stock_movements') as import_status
            FROM files f
            ORDER BY upload_date DESC
        """,
        fetch='all'
    ) or []

    return render_template('stock_movement_imports.html', files=files)

@imports_bp.route('/unified', methods=['GET'])
def unified_imports():
    """Show unified imports page with all import types and latest record dates."""
    latest_sales_orders = _get_latest_date("SELECT MAX(date_entered) AS latest FROM sales_orders")
    latest_purchase_orders = _get_latest_date("SELECT MAX(date_issued) AS latest FROM purchase_orders")
    latest_vq_entry = _get_latest_date("SELECT MAX(entry_date) AS latest FROM vqs")
    latest_vq_line = _get_latest_date("SELECT MAX(quoted_date) AS latest FROM vq_lines")
    latest_cqs = _get_latest_date("SELECT MAX(entry_date) AS latest FROM cqs")
    latest_stock = _get_latest_date("SELECT MAX(movement_date) AS latest FROM stock_movements")

    latest_dates = {
        'sales_orders': _format_latest(latest_sales_orders),
        'purchase_orders': _format_latest(latest_purchase_orders),
        'vendor_quotes': _format_latest(_combine_latest(latest_vq_entry, latest_vq_line)),
        'customer_quotes': _format_latest(latest_cqs),
        'stock_levels': _format_latest(latest_stock),
    }

    mailbox_import = {
        'message_id': (request.args.get('message_id') or '').strip(),
        'attachment_id': (request.args.get('attachment_id') or '').strip(),
        'attachment_name': (request.args.get('attachment_name') or '').strip(),
    }

    return render_template('unified_imports.html', latest_dates=latest_dates, mailbox_import=mailbox_import)


@imports_bp.route('/unified/stock-from-mailbox', methods=['POST'])
def import_stock_from_mailbox():
    payload = request.get_json(silent=True) or {}
    message_id = (payload.get('message_id') or '').strip()
    attachment_id = (payload.get('attachment_id') or '').strip()

    if not message_id or not attachment_id:
        return jsonify(success=False, message='message_id and attachment_id are required'), 400

    try:
        attachment = _fetch_mailbox_attachment_bytes(message_id, attachment_id)
        attachment_name = attachment['name'] or 'attachment.zip'
        if not attachment_name.lower().endswith('.zip'):
            return jsonify(success=False, message='Expected a ZIP attachment'), 400

        workbook_name, workbook_bytes = _extract_vs_inventory_workbook(attachment['bytes'])
        stock_df = _build_vs_stock_dataframe(workbook_bytes)
        file_id, _filepath, saved_name = _save_import_file_bytes(workbook_name, workbook_bytes, import_type='stock_levels')

        from routes.stock_movements import process_stock_levels_dataframe
        results = process_stock_levels_dataframe(
            stock_df,
            file_id=file_id,
            reference_prefix='MAILBOX-VS-IMPORT',
        )

        return jsonify(
            success=True,
            message=f'Imported stock levels from mailbox attachment {attachment_name}',
            source_file=saved_name,
            results=results,
        )
    except BadRequest as exc:
        return jsonify(success=False, message=str(exc)), 400
    except Exception as exc:
        current_app.logger.exception("Mailbox stock import failed")
        return jsonify(success=False, message=str(exc)), 500
