from flask import Blueprint, render_template, request, jsonify, url_for, redirect, session, flash, abort
from models import create_base_part_number, get_global_alternatives, insert_update
from db import execute as db_execute, db_cursor
import logging
import openai
from openai import OpenAI
from datetime import datetime, timedelta, date
from decimal import Decimal
import json
import re
import copy
from flask_login import current_user
import tempfile
import extract_msg
from extract_msg.exceptions import InvalidFileFormatError
import email
from email import policy
from email.message import EmailMessage
import os
import html
from routes.emails import send_graph_email, build_graph_inline_attachments
from routes.email_signatures import get_user_default_signature
from routes.parts_list_ai import trigger_monroe_auto_check

# Optional rich RTF→text converter; falls back to a lightweight stripper if missing
try:
    from striprtf.striprtf import rtf_to_text as striprtf_to_text
except Exception:
    striprtf_to_text = None

# Initialize OpenAI client
client = OpenAI()

parts_list_bp = Blueprint('parts_list', __name__)

AI_PARTS_MAX_CHARS = 15000
AI_PARTS_RESPONSE_TOKENS = 3000
AI_PARTS_HEADER_LINES = 20
AI_PARTS_HEADER_CHAR_LIMIT = 2000


def _execute_with_cursor(cur, query, params=None):
    """Execute a query on the given cursor with Postgres placeholder translation."""
    prepared = query.replace('?', '%s') if os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')) else query
    cur.execute(prepared, params or [])
    return cur


def _safe_row_get(row, key, default=None):
    """
    Return a key from a DB row that could be a dict (psycopg RealDictRow) or sqlite3.Row.
    """
    if row is None:
        return default
    try:
        return row.get(key, default)
    except AttributeError:
        if hasattr(row, 'keys') and key in row.keys():
            return row[key]
        return default


def _ensure_part_number(base_part_number, part_number):
    if not base_part_number:
        return
    part_value = part_number or base_part_number
    db_execute(
        """
        INSERT INTO part_numbers (base_part_number, part_number)
        VALUES (?, ?)
        ON CONFLICT (base_part_number) DO NOTHING
        """,
        (base_part_number, part_value),
        commit=True,
    )


def _repair_project_linked_parts_list_lines(list_id):
    """
    For project-linked lists created from project parts, ensure base_part_number/description
    are populated correctly in parts_list_lines based on the project source data.
    """
    if not list_id:
        return

    with db_cursor(commit=True) as cur:
        rows = _execute_with_cursor(
            cur,
            """
            SELECT
                pll.id,
                pll.base_part_number,
                pll.description,
                pll.customer_part_number,
                ppl.description AS project_description,
                ppl.customer_part_number AS project_customer_part_number
            FROM parts_list_lines pll
            JOIN project_parts_list_lines ppl ON ppl.parts_list_line_id = pll.id
            WHERE pll.parts_list_id = ?
            """,
            (list_id,),
        ).fetchall() or []

        for row in rows:
            current_base = _safe_row_get(row, 'base_part_number')
            current_desc = _safe_row_get(row, 'description')
            customer_part = _safe_row_get(row, 'customer_part_number') or _safe_row_get(row, 'project_customer_part_number')
            project_desc = _safe_row_get(row, 'project_description')

            if not customer_part:
                continue

            normalized_base = create_base_part_number(customer_part)
            if not normalized_base:
                continue

            new_base = current_base
            new_desc = current_desc
            should_update = False

            if not current_base or current_base == project_desc:
                new_base = normalized_base
                should_update = True

            if not current_desc:
                if project_desc:
                    new_desc = project_desc
                    should_update = True
                elif current_base and current_base != normalized_base:
                    new_desc = current_base
                    should_update = True

            if should_update:
                _execute_with_cursor(
                    cur,
                    """
                    UPDATE parts_list_lines
                    SET base_part_number = ?,
                        description = COALESCE(?, description),
                        date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                      AND parts_list_id = ?
                    """,
                    (new_base, new_desc, row['id'], list_id),
                )


def _log_parts_list_creation_communication(list_id, list_name, customer_id, contact_id, salesperson_id):
    if not (list_id and customer_id and contact_id and salesperson_id):
        return
    list_url = url_for('parts_list.view_parts_list', list_id=list_id)
    safe_name = html.escape(list_name or f"Parts List {list_id}")
    notes = (
        f"<i class=\"bi bi-list-check text-primary me-1\"></i> "
        f"Parts list created: <a href=\"{list_url}\">{safe_name}</a>"
    )
    insert_update(
        customer_id,
        salesperson_id,
        notes,
        contact_id=contact_id,
        communication_type='Other',
    )


_ALLOWED_LINE_TYPES = {'normal', 'price_break', 'alternate'}


def _normalize_line_type(value, default='normal'):
    if value is None:
        return default
    value = str(value).strip().lower()
    return value if value in _ALLOWED_LINE_TYPES else None


def _next_child_line_number(cur, list_id, parent_id, parent_number):
    parent_number = Decimal(str(parent_number or 0))
    last_row = _execute_with_cursor(cur, """
        SELECT line_number
        FROM parts_list_lines
        WHERE parts_list_id = ? AND parent_line_id = ?
        ORDER BY line_number DESC, id DESC
        LIMIT 1
    """, (list_id, parent_id)).fetchone()
    if last_row:
        last_number = Decimal(str(last_row['line_number']))
        next_number = last_number + Decimal('0.1')
        if next_number <= parent_number:
            next_number = parent_number + Decimal('0.1')
    else:
        next_number = parent_number + Decimal('0.1')
    return next_number.quantize(Decimal('0.1'))


def ensure_no_response_table(cur):
    """
    Make sure the dismissal table for the no-response view exists.
    """
    if os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')):
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parts_list_no_response_dismissals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER NOT NULL UNIQUE,
            dismissed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_no_response_email
        ON parts_list_no_response_dismissals(email_id)
    """)


@parts_list_bp.route('/parts-lists/<int:list_id>/supplier-quotes', methods=['GET'])
def get_supplier_quotes(list_id):
    """
    Get all supplier quotes for a parts list
    """
    try:
        quotes = db_execute(
            """
            SELECT
                sq.id,
                sq.quote_reference,
                sq.quote_date,
                s.name as supplier_name,
                s.id as supplier_id,
                c.currency_code,
                sq.notes,
                sq.date_created,
                sq.email_message_id,
                sq.email_conversation_id,
                (SELECT COUNT(*) FROM parts_list_supplier_quote_lines
                 WHERE supplier_quote_id = sq.id) as line_count,
                (SELECT COUNT(*) FROM parts_list_supplier_quote_lines
                WHERE supplier_quote_id = sq.id AND is_no_bid = TRUE) as no_bid_count
            FROM parts_list_supplier_quotes sq
            JOIN suppliers s ON s.id = sq.supplier_id
            LEFT JOIN currencies c ON c.id = sq.currency_id
            WHERE sq.parts_list_id = ?
            ORDER BY sq.date_created DESC
            """,
            (list_id,),
            fetch='all',
        )
        return jsonify(success=True, quotes=[dict(q) for q in quotes or []])

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/supplier-quotes/create', methods=['POST'])
def create_supplier_quote(list_id):
    """
    Create a new supplier quote
    """
    try:
        data = request.get_json(force=True)
        supplier_id = data.get('supplier_id')

        if not supplier_id:
            return jsonify(success=False, message="supplier_id is required"), 400

        # Verify list exists
        list_exists = db_execute(
            "SELECT 1 FROM parts_lists WHERE id = ?",
            (list_id,),
            fetch='one',
        )
        if not list_exists:
            return jsonify(success=False, message="Parts list not found"), 404

        # Get currency_id from the request data (what user selected on page)
        # If not provided, fallback to GBP
        currency_id = data.get('currency_id', 1)

        # Get email tracking fields if provided (for quotes created from mailbox)
        email_message_id = data.get('email_message_id') or None
        email_conversation_id = data.get('email_conversation_id') or None

        # Create quote header
        row = db_execute(
            """
            INSERT INTO parts_list_supplier_quotes
            (parts_list_id, supplier_id, quote_reference, quote_date,
             currency_id, notes, created_by_user_id, email_message_id, email_conversation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                list_id,
                supplier_id,
                data.get('quote_reference'),
                data.get('quote_date'),
                currency_id,
                data.get('notes'),
                session.get('user_id'),
                email_message_id,
                email_conversation_id
            ),
            fetch='one',
            commit=True,
        )

        quote_id = _safe_row_get(row, 'id')
        if quote_id is None and row:
            try:
                quote_id = list(row.values())[0]
            except Exception:
                quote_id = None

        return jsonify(success=True, quote_id=quote_id)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/parts-lists/<int:list_id>/supplier-quotes/<int:quote_id>', methods=['GET'])
def get_supplier_quote_details(list_id, quote_id):
    """
    Get detailed quote information including all lines
    """
    try:
        # Get quote header
        quote = db_execute(
            """
            SELECT 
                sq.*,
                s.name as supplier_name,
                c.currency_code,
                c.symbol
            FROM parts_list_supplier_quotes sq
            JOIN suppliers s ON s.id = sq.supplier_id
            LEFT JOIN currencies c ON c.id = sq.currency_id
            WHERE sq.id = ? AND sq.parts_list_id = ?
            """,
            (quote_id, list_id),
            fetch='one',
        )

        if not quote:
            return jsonify(success=False, message="Quote not found"), 404

        # Get the supplier_id from the quote
        supplier_id = quote['supplier_id']

        # DEBUG: Check if there are ANY email records for this supplier
        email_check = db_execute(
            """
            SELECT COUNT(*) as cnt FROM parts_list_line_supplier_emails
            WHERE supplier_id = ?
            """,
            (supplier_id,),
            fetch='one',
        )
        logging.info(f"Total emails for supplier {supplier_id}: {email_check['cnt']}")

        # Get quote lines with parts list line info
        lines = db_execute(
            """
            SELECT 
                sql.id,
                COALESCE(sql.parts_list_line_id, pll.id) as parts_list_line_id,
                sql.quoted_part_number,
                sql.manufacturer,
                sql.quantity_quoted,
                sql.qty_available,
                sql.purchase_increment,
                sql.moq,
                sql.unit_price,
                sql.lead_time_days,
                sql.condition_code,
                sql.certifications,
                sql.is_no_bid,
                sql.line_notes,
                pll.customer_part_number,
                pll.base_part_number,
                pll.quantity as requested_quantity,
                pll.line_number,
                -- Check if this line has other quotes
                (SELECT COUNT(*) FROM parts_list_supplier_quote_lines sql2
                 WHERE sql2.parts_list_line_id = pll.id 
                   AND sql2.supplier_quote_id != ?
                   AND sql2.is_no_bid = FALSE) as other_quotes_count,
                -- Check if a quote request email was sent for this line/supplier combination
                COALESCE((
                    SELECT 1 FROM parts_list_line_supplier_emails plse
                    WHERE plse.parts_list_line_id = pll.id
                      AND plse.supplier_id = ?
                    LIMIT 1
                ), 0) as quote_requested
            FROM parts_list_lines pll
            LEFT JOIN parts_list_supplier_quote_lines sql 
                ON sql.parts_list_line_id = pll.id 
               AND sql.supplier_quote_id = ?
            WHERE pll.parts_list_id = ?
            ORDER BY pll.line_number ASC
            """,
            (quote_id, supplier_id, quote_id, list_id),
            fetch='all',
        )

        # DEBUG: Log each line's quote_requested status
        for line in lines or []:
            logging.info(f"Line {line['line_number']} (ID: {line['parts_list_line_id']}): quote_requested = {line['quote_requested']}")

        return jsonify(
            success=True,
            quote=dict(quote),
            lines=[dict(line) for line in lines or []]
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/supplier-quotes/<int:quote_id>/update', methods=['POST'])
def update_supplier_quote(list_id, quote_id):
    """
    Update quote header information
    """
    try:
        data = request.get_json(force=True)

        # Build update query
        fields = []
        params = []

        if 'supplier_id' in data:
            supplier_id = data.get('supplier_id')
            if not supplier_id:
                return jsonify(success=False, message="supplier_id is required"), 400

            supplier_exists = db_execute(
                "SELECT 1 FROM suppliers WHERE id = ?",
                (supplier_id,),
                fetch='one',
            )
            if not supplier_exists:
                return jsonify(success=False, message="Supplier not found"), 404

            fields.append("supplier_id = ?")
            params.append(supplier_id)

        for field in ['quote_reference', 'quote_date', 'currency_id', 'notes']:
            if field in data:
                if field == 'quote_date' and (data[field] is None or str(data[field]).strip() == ''):
                    data[field] = None
                fields.append(f"{field} = ?")
                params.append(data[field])

        if not fields:
            return jsonify(success=False, message="No fields to update"), 400

        params.extend([quote_id, list_id])

        with db_cursor(commit=True) as cur:
            cur.execute(f"""
            UPDATE parts_list_supplier_quotes 
            SET {', '.join(fields)}, date_modified = CURRENT_TIMESTAMP
            WHERE id = ? AND parts_list_id = ?
            """, params)

            if cur.rowcount == 0:
                return jsonify(success=False, message="Quote not found"), 404

        return jsonify(success=True)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/supplier-quotes/<int:quote_id>/lines/save', methods=['POST'])
def save_supplier_quote_lines(list_id, quote_id):
    """
    Save/update multiple quote lines at once
    """
    try:
        data = request.get_json(force=True)
        lines = data.get('lines', [])

        # DEBUG: Log the entire request
        logging.info(f"=== SAVE QUOTE LINES DEBUG ===")
        logging.info(f"Total lines received: {len(lines)}")
        logging.info(f"Full data: {json.dumps(data, indent=2)}")

        if not lines:
            return jsonify(success=False, message="No lines provided"), 400

        # Verify quote exists and belongs to list
        quote_exists = db_execute(
            """
            SELECT 1 FROM parts_list_supplier_quotes 
            WHERE id = ? AND parts_list_id = ?
            """,
            (quote_id, list_id),
            fetch='one',
        )
        if not quote_exists:
            return jsonify(success=False, message="Quote not found"), 404

        saved_count = 0
        skipped_count = 0

        for idx, line in enumerate(lines):
            parts_list_line_id = line.get('parts_list_line_id')

            logging.info(f"\n--- Line {idx + 1} ---")
            logging.info(f"parts_list_line_id: {parts_list_line_id}")

            if not parts_list_line_id:
                logging.info("Skipping: No parts_list_line_id")
                skipped_count += 1
                continue

            quoted_part_number = line.get('quoted_part_number')
            quantity_quoted_raw = line.get('quantity_quoted')
            qty_available_raw = line.get('qty_available')
            purchase_increment_raw = line.get('purchase_increment')
            moq_raw = line.get('moq')
            unit_price_raw = line.get('unit_price')
            lead_time_days_raw = line.get('lead_time_days')
            condition_code_raw = line.get('condition_code')
            certifications_raw = line.get('certifications')
            manufacturer_raw = line.get('manufacturer')
            is_no_bid = line.get('is_no_bid', False)
            line_notes_raw = line.get('line_notes')

            quantity_quoted = _safe_int(quantity_quoted_raw)
            qty_available = _safe_int(qty_available_raw)
            purchase_increment = _safe_int(purchase_increment_raw)
            moq = _safe_int(moq_raw)
            unit_price = _safe_float(unit_price_raw)
            lead_time_days = _safe_int(lead_time_days_raw)
            condition_code = _normalize_optional_text(condition_code_raw)
            certifications = _normalize_optional_text(certifications_raw)
            manufacturer = _normalize_optional_text(manufacturer_raw)
            line_notes = _normalize_optional_text(line_notes_raw)

            if isinstance(is_no_bid, str):
                is_no_bid = is_no_bid.strip().lower() in ('true', '1', 'yes', 'y')
            else:
                is_no_bid = bool(is_no_bid)

            # DEBUG: Log all field values
            logging.info(f"quoted_part_number: '{quoted_part_number}' (type: {type(quoted_part_number).__name__})")
            logging.info(f"quantity_quoted: '{quantity_quoted}' (type: {type(quantity_quoted).__name__})")
            logging.info(f"qty_available: '{qty_available}' (type: {type(qty_available).__name__})")
            logging.info(f"purchase_increment: '{purchase_increment}' (type: {type(purchase_increment).__name__})")
            logging.info(f"moq: '{moq}' (type: {type(moq).__name__})")
            logging.info(f"unit_price: '{unit_price}' (type: {type(unit_price).__name__})")
            logging.info(f"lead_time_days: '{lead_time_days}' (type: {type(lead_time_days).__name__})")
            logging.info(f"condition_code: '{condition_code}' (type: {type(condition_code).__name__})")
            logging.info(f"certifications: '{certifications}' (type: {type(certifications).__name__})")
            logging.info(f"is_no_bid: {is_no_bid} (type: {type(is_no_bid).__name__})")
            logging.info(f"line_notes: '{line_notes}' (type: {type(line_notes).__name__})")

            # Skip if there's no meaningful data to save
            has_quote_data = (
                    unit_price is not None or
                    qty_available is not None or
                    purchase_increment is not None or
                    moq is not None or
                    lead_time_days is not None or
                    (condition_code and condition_code.strip()) or
                    (certifications and certifications.strip()) or
                    (manufacturer and manufacturer.strip()) or
                    is_no_bid is True or
                    (line_notes and line_notes.strip())
            )

            logging.info(f"has_quote_data: {has_quote_data}")

            if not has_quote_data:
                logging.info("Skipping: No meaningful quote data")
                skipped_count += 1
                continue

            logging.info("SAVING THIS LINE")

            if not is_no_bid:
                line_info = db_execute(
                    """
                    SELECT base_part_number, customer_part_number
                    FROM parts_list_lines
                    WHERE id = ?
                    """,
                    (parts_list_line_id,),
                    fetch='one',
                )
                line_base = _safe_row_get(line_info, 'base_part_number')
                line_customer = _safe_row_get(line_info, 'customer_part_number')
                part_value = quoted_part_number or line_customer or line_base
                base_part_number = create_base_part_number(part_value) if part_value else line_base
                if base_part_number:
                    _ensure_part_number(base_part_number, part_value)

            # Check if line already exists
            existing = db_execute(
                """
                SELECT id FROM parts_list_supplier_quote_lines
                WHERE supplier_quote_id = ? AND parts_list_line_id = ?
                """,
                (quote_id, parts_list_line_id),
                fetch='one',
            )

            if existing:
                logging.info(f"Updating existing line ID: {existing['id']}")
                # Update existing line
                db_execute(
                    """
                    UPDATE parts_list_supplier_quote_lines
                    SET quoted_part_number = ?,
                        quantity_quoted = ?,
                        qty_available = ?,
                        purchase_increment = ?,
                        moq = ?,
                        unit_price = ?,
                        lead_time_days = ?,
                        condition_code = ?,
                        certifications = ?,
                        manufacturer = ?,
                        is_no_bid = ?,
                        line_notes = ?,
                        date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        quoted_part_number, quantity_quoted, qty_available, purchase_increment,
                        moq, unit_price, lead_time_days, condition_code, certifications,
                        manufacturer, is_no_bid, line_notes, existing['id']
                    ),
                    commit=True,
                )
            else:
                logging.info("Inserting new line")
                # Insert new line
                db_execute(
                    """
                    INSERT INTO parts_list_supplier_quote_lines
                    (supplier_quote_id, parts_list_line_id, quoted_part_number,
                     manufacturer, quantity_quoted, qty_available, purchase_increment, moq,
                     unit_price, lead_time_days, condition_code, certifications, is_no_bid, line_notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        quote_id, parts_list_line_id, quoted_part_number,
                        manufacturer, quantity_quoted, qty_available, purchase_increment, moq,
                        unit_price, lead_time_days, condition_code, certifications, is_no_bid, line_notes
                    ),
                    commit=True,
                )

            saved_count += 1

        logging.info(f"\n=== SUMMARY ===")
        logging.info(f"Saved: {saved_count}, Skipped: {skipped_count}")

        return jsonify(success=True, saved_count=saved_count, skipped_count=skipped_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/parts-lists/<int:list_id>/supplier-quotes/<int:quote_id>/delete', methods=['POST'])
def delete_supplier_quote(list_id, quote_id):
    """
    Delete a supplier quote (cascades to lines)
    """
    try:
        # Ensure it exists first to give a clean 404
        exists = db_execute(
            "SELECT 1 FROM parts_list_supplier_quotes WHERE id = ? AND parts_list_id = ?",
            (quote_id, list_id),
            fetch='one',
        )
        if not exists:
            return jsonify(success=False, message="Quote not found"), 404

        db_execute(
            "DELETE FROM parts_list_supplier_quotes WHERE id = ? AND parts_list_id = ?",
            (quote_id, list_id),
            commit=True,
        )

        return jsonify(success=True)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/supplier-quotes/manage', methods=['GET'])
def manage_supplier_quotes(list_id):
    """
    Simple management page for supplier quotes cleanup.
    """
    header = db_execute(
        """
        SELECT 
            pl.id,
            pl.name,
            pl.status_id,
            pl.project_id,
            pl.notes,
            c.name AS customer_name,
            s.name AS status_name,
            p.name AS project_name
        FROM parts_lists pl
        LEFT JOIN customers c ON c.id = pl.customer_id
        LEFT JOIN parts_list_statuses s ON s.id = pl.status_id
        LEFT JOIN projects p ON p.id = pl.project_id
        WHERE pl.id = ?
        """,
        (list_id,),
        fetch='one',
    )
    if not header:
        return "Parts list not found", 404

    cache_bust = datetime.now().strftime('%Y%m%d')

    return render_template(
        'parts_list_supplier_quotes_manage.html',
        list_id=list_id,
        list_name=header['name'],
        list_notes=header.get('notes'),
        customer_name=header.get('customer_name'),
        project_id=header.get('project_id'),
        project_name=header.get('project_name'),
        status_id=header.get('status_id'),
        status_name=header.get('status_name'),
        cache_bust=cache_bust,
    )


@parts_list_bp.route('/supplier-quotes/lines/manage', methods=['GET'])
def manage_supplier_quote_lines():
    """
    Global management page for supplier quote lines.
    """
    cache_bust = datetime.now().strftime('%Y%m%d')
    return render_template(
        'parts_list_supplier_quote_lines_manage.html',
        cache_bust=cache_bust,
    )


@parts_list_bp.route('/supplier-quotes/search', methods=['GET'])
def search_supplier_quotes():
    """
    Search supplier quotes for selection dropdowns.
    """
    try:
        query = (request.args.get('q') or '').strip().lower()
        list_id = request.args.get('list_id', type=int)
        limit = request.args.get('limit', type=int) or 50

        where = []
        params = []

        if query:
            like = f"%{query}%"
            where.append("(LOWER(s.name) LIKE ? OR LOWER(COALESCE(sq.quote_reference, '')) LIKE ?)")
            params.extend([like, like])

        if list_id:
            where.append("sq.parts_list_id = ?")
            params.append(list_id)

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        rows = db_execute(
            f"""
            SELECT
                sq.id,
                sq.quote_reference,
                sq.quote_date,
                sq.parts_list_id,
                pl.name AS list_name,
                s.name AS supplier_name
            FROM parts_list_supplier_quotes sq
            JOIN suppliers s ON s.id = sq.supplier_id
            JOIN parts_lists pl ON pl.id = sq.parts_list_id
            {where_clause}
            ORDER BY sq.date_created DESC
            LIMIT ?
            """,
            (*params, limit),
            fetch='all',
        )

        return jsonify(success=True, quotes=[dict(r) for r in rows or []])

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/supplier-quotes/lines/data', methods=['GET'])
def supplier_quote_lines_data():
    """
    Return supplier quote lines with optional filters.
    """
    try:
        list_id = request.args.get('list_id', type=int)
        quote_id = request.args.get('quote_id', type=int)
        supplier_id = request.args.get('supplier_id', type=int)
        part_query = (request.args.get('part_number') or '').strip().lower()
        limit = request.args.get('limit', type=int) or 500
        offset = request.args.get('offset', type=int) or 0

        where = []
        params = []

        if list_id:
            where.append("sq.parts_list_id = ?")
            params.append(list_id)

        if quote_id:
            where.append("sql.supplier_quote_id = ?")
            params.append(quote_id)

        if supplier_id:
            where.append("sq.supplier_id = ?")
            params.append(supplier_id)

        if part_query:
            like = f"%{part_query}%"
            where.append("""
                (
                    LOWER(COALESCE(pll.customer_part_number, '')) LIKE ?
                    OR LOWER(COALESCE(pll.base_part_number, '')) LIKE ?
                    OR LOWER(COALESCE(sql.quoted_part_number, '')) LIKE ?
                )
            """)
            params.extend([like, like, like])

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        total_row = db_execute(
            f"""
            SELECT COUNT(*) AS total_count
            FROM parts_list_supplier_quote_lines sql
            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
            JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
            {where_clause}
            """,
            params,
            fetch='one',
        )

        rows = db_execute(
            f"""
            SELECT
                sql.id,
                sql.supplier_quote_id,
                sq.quote_reference,
                sq.quote_date,
                sq.parts_list_id,
                pl.name AS list_name,
                s.name AS supplier_name,
                pll.id AS parts_list_line_id,
                pll.line_number,
                pll.customer_part_number,
                pll.base_part_number,
                sql.quoted_part_number,
                sql.manufacturer,
                sql.quantity_quoted,
                sql.qty_available,
                sql.purchase_increment,
                sql.moq,
                sql.unit_price,
                sql.lead_time_days,
                sql.is_no_bid,
                sql.line_notes,
                sql.date_created,
                sql.date_modified
            FROM parts_list_supplier_quote_lines sql
            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
            JOIN suppliers s ON s.id = sq.supplier_id
            JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
            JOIN parts_lists pl ON pl.id = sq.parts_list_id
            {where_clause}
            ORDER BY sq.date_created DESC, sql.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
            fetch='all',
        )

        return jsonify(
            success=True,
            total_count=total_row['total_count'] if total_row else 0,
            lines=[dict(r) for r in rows or []],
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/supplier-quotes/lines/reassign', methods=['POST'])
def reassign_supplier_quote_lines():
    """
    Move quote lines to a different supplier quote.
    """
    try:
        data = request.get_json(force=True) or {}
        raw_line_ids = data.get('line_ids') or []
        new_quote_id = data.get('supplier_quote_id')

        if not raw_line_ids or not new_quote_id:
            return jsonify(success=False, message="line_ids and supplier_quote_id are required"), 400

        line_ids = []
        for line_id in raw_line_ids:
            try:
                line_ids.append(int(line_id))
            except (TypeError, ValueError):
                continue

        if not line_ids:
            return jsonify(success=False, message="line_ids are required"), 400

        with db_cursor(commit=True) as cur:
            quote_exists = _execute_with_cursor(
                cur,
                "SELECT 1 FROM parts_list_supplier_quotes WHERE id = ?",
                (new_quote_id,),
            ).fetchone()
            if not quote_exists:
                return jsonify(success=False, message="Quote not found"), 404

            placeholders = ",".join(["?"] * len(line_ids))
            _execute_with_cursor(
                cur,
                f"""
                UPDATE parts_list_supplier_quote_lines
                SET supplier_quote_id = ?, date_modified = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                [new_quote_id, *line_ids],
            )

            updated_count = cur.rowcount

        return jsonify(success=True, updated_count=updated_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/supplier-quotes/lines/delete', methods=['POST'])
def delete_supplier_quote_lines_global():
    """
    Delete supplier quote lines by id.
    """
    try:
        data = request.get_json(force=True) or {}
        raw_line_ids = data.get('line_ids') or []
        if not raw_line_ids:
            return jsonify(success=False, message="line_ids are required"), 400

        line_ids = []
        for line_id in raw_line_ids:
            try:
                line_ids.append(int(line_id))
            except (TypeError, ValueError):
                continue

        if not line_ids:
            return jsonify(success=False, message="line_ids are required"), 400

        with db_cursor(commit=True) as cur:
            placeholders = ",".join(["?"] * len(line_ids))
            _execute_with_cursor(
                cur,
                f"""
                DELETE FROM parts_list_supplier_quote_lines
                WHERE id IN ({placeholders})
                """,
                line_ids,
            )
            deleted_count = cur.rowcount

        return jsonify(success=True, deleted_count=deleted_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/extract_supplier_quote', methods=['POST'])
def extract_supplier_quote():
    from flask import request, jsonify
    import logging

    logger = logging.getLogger(__name__)

    try:
        data = request.get_json() or {}
        quote_text = data.get('quote_text', '')
        context_parts = data.get('context_parts', '')

        logger.info("extract_supplier_quote route hit")
        logger.debug("Raw quote_text (first 500 chars): %r", quote_text[:500])
        logger.debug("Raw context_parts (first 500 chars): %r", context_parts[:500])

        currency_warning = _detect_multiple_currencies(quote_text)
        extracted_items = extract_supplier_quote_data(quote_text, context_parts)

        logger.info("extract_supplier_quote: got %d items from extractor",
                    len(extracted_items))
        logger.debug("extract_supplier_quote: items: %r", extracted_items)

        # If you’re supposed to INSERT into DB here, add logging around that:
        # for item in extracted_items:
        #     logger.debug("Inserting item into DB: %r", item)
        #     ... insert logic ...

        return jsonify({
            "success": True,
            "items": extracted_items,
            "currency_warning": currency_warning
        })

    except Exception as e:
        logger.exception("Error in extract_supplier_quote route")
        return jsonify({"success": False, "error": str(e)}), 500



import json
import logging
import re

logger = logging.getLogger(__name__)


def _safe_float(value):
    """Try to extract a float from messy text; return None on failure."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    s = str(value)
    s = s.replace('\xa0', ' ').strip()

    match = re.search(r'-?\d[\d,]*\.?\d*', s)
    if not match:
        logger.debug(f"_safe_float: no numeric match in {s!r}")
        return None

    num_str = match.group(0).replace(',', '')
    try:
        return float(num_str)
    except ValueError:
        logger.debug(f"_safe_float: ValueError converting {num_str!r}")
        return None


def _safe_int(value):
    """Try to extract an int from messy text; return None on failure."""
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(round(value))

    s = str(value)
    s = s.replace('\xa0', ' ').strip()

    match = re.search(r'-?\d+', s)
    if not match:
        logger.debug(f"_safe_int: no numeric match in {s!r}")
        return None

    try:
        return int(match.group(0))
    except ValueError:
        logger.debug(f"_safe_int: ValueError converting {match.group(0)!r}")
        return None


def _normalize_optional_text(value):
    """Normalize optional text fields; treat placeholder 'none' values as None."""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.lower() in ('none', 'n/a', 'na', 'null', 'nil', '-'):
        return None

    return text


def _standardize_certifications(raw_value, existing_notes=None):
    """Standardize certification text to a few clear categories.

    - Prefer concise values like "OEM certs", "EASA Form 1", "8130-3", or "no trace".
    - Move compliance/test-report details to notes so the certifications column stays clean.
    """
    normalized = _normalize_optional_text(raw_value)
    notes_parts = []
    if existing_notes:
        notes_parts.append(existing_notes)

    def append_note(text):
        if text:
            notes_parts.append(text)

    # Route DFARS, ITAR, or test-report details to notes instead of certifications
    if normalized:
        lower_value = normalized.lower()
        compliance_terms = (
            'dfar', 'dfars', 'itar', 'test report', 'bench test', 'functional test',
            'burn in', 'burn-in', 'analysis report', 'ndt', 'x-ray', 'ultrasonic'
        )
        if any(term in lower_value for term in compliance_terms):
            append_note(normalized)
            normalized = None

    if normalized:
        lower_value = normalized.lower()
    else:
        lower_value = ""

    def has_oem_trace_signal(text):
        if not text:
            return False

        oem_terms = (
            'oem c of c', 'oem coc', 'oem cert', 'oem certificate',
            'mfg c of c', 'mfr c of c', 'manufacturer c of c',
            'factory c of c', 'factory cert', 'factory trace',
            'manufacturer cert', 'full trace to oem', 'full trace',
            'oem trace', 'mfr trace', 'mfg trace', 'factory traceability'
        )
        if not any(term in text for term in oem_terms):
            return False

        negation_terms = ('no oem', 'no mfg', 'no manufacturer', 'without oem', 'without mfg', 'no factory')
        if any(term in text for term in negation_terms):
            return False

        return True

    no_trace_terms = (
        'no trace', 'no cert', 'no certs', 'no certification', 'trace not',
        'trace unavailable', 'trace unknown', 'no paperwork'
    )

    notes_lower = ' '.join(note.lower() for note in notes_parts if note)

    if lower_value:
        if any(term in lower_value for term in no_trace_terms):
            normalized = "no trace"
        elif 'easa' in lower_value and 'form' in lower_value:
            normalized = "EASA Form 1"
        elif '8130' in lower_value:
            normalized = "8130-3"
        elif 'dual release' in lower_value or ('dual' in lower_value and 'release' in lower_value):
            normalized = "Dual release (8130/EASA)"
        elif 'distributor' in lower_value and ('c of c' in lower_value or 'coc' in lower_value):
            normalized = "no trace"
        elif any(term in lower_value for term in (
            'oem', 'factory', 'manufacturer trace', 'mfr trace', 'mfg trace',
            'full trace', 'traceable to oem', 'factory trace', 'factory new trace',
            'manufacturer c of c', 'mfr c of c', 'mfg c of c', 'c of c', 'coc'
        )):
            normalized = "OEM certs"

    oem_context = notes_lower or lower_value
    no_trace_context = notes_lower or lower_value

    if normalized is None and any(term in notes_lower for term in no_trace_terms):
        normalized = "no trace"

    if (normalized is None or normalized == "no trace") and oem_context:
        if not any(term in no_trace_context for term in no_trace_terms) and has_oem_trace_signal(oem_context):
            normalized = "OEM certs"

    combined_notes = '; '.join([note for note in notes_parts if note]) or None
    return normalized, combined_notes


def _extract_quoted_part_number(text):
    """
    Extract quoted/alternative part number from text.
    Looks for patterns like "Quoting: NAS9301B-5-10" or "Alt PN: AF3212-4-06"
    """
    if not text:
        return None

    # Try multiple patterns in order of specificity
    patterns = [
        r'\b(?:quoting|quoted|alt\s*pn?|alternate?\s*pn?)\s*[:#-]?\s*(?:p/?n\s*[:#-]?\s*)?([A-Z0-9][A-Z0-9\-\/\.]+)',
        r'/\s*(?:quoting|quoted)\s*[:#-]?\s*([A-Z0-9][A-Z0-9\-\/\.]+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return None


def _extract_requested_part_number(text):
    """
    Extract the original/requested part number from notes.
    Looks for patterns like "Requested PN: CR3212-4-04"
    """
    if not text:
        return None

    # Look for "Requested PN:" or similar patterns
    match = re.search(
        r'\b(?:requested|original|req)\s*(?:pn?|part\s*number?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9\-\/\.]+)',
        text,
        re.IGNORECASE
    )
    if match:
        return match.group(1).strip()

    return None


def _normalize_part_number_key(value):
    if not value:
        return ''
    return re.sub(r'[^A-Z0-9]', '', str(value).upper())


def _is_peerless_quote(text):
    if not text:
        return False
    upper_text = text.upper()
    return 'PEERLESS AEROSPACE' in upper_text or 'PAFCORP' in upper_text


def _extract_peerless_part_pairs(text):
    if not text:
        return []
    pattern = re.compile(r'([A-Z0-9][A-Z0-9\-\.]+)\s*/\s*([A-Z0-9][A-Z0-9\-\.]+)', re.IGNORECASE)
    pairs = []
    for match in pattern.finditer(text):
        left = match.group(1).strip()
        right = match.group(2).strip()
        if not re.search(r'[A-Z]', left, re.IGNORECASE):
            continue
        if not re.search(r'[A-Z]', right, re.IGNORECASE):
            continue
        pairs.append((left, right))
    return pairs


def _build_peerless_part_lookup(text):
    pairs = _extract_peerless_part_pairs(text)
    by_requested = {}
    by_quoted = {}
    for requested, quoted in pairs:
        req_key = _normalize_part_number_key(requested)
        quoted_key = _normalize_part_number_key(quoted)
        if req_key and quoted_key:
            by_requested[req_key] = (requested, quoted)
            by_quoted[quoted_key] = (requested, quoted)
    return by_requested, by_quoted


_CURRENCY_CODE_PATTERN = re.compile(
    r'\b(USD|EUR|GBP|CAD|AUD|NZD|CHF|JPY|CNY|SEK|NOK|DKK|SGD|HKD|INR|KRW|MXN|BRL|AED|SAR|ZAR|TRY|PLN|CZK|HUF|RON)\b',
    re.IGNORECASE,
)
_CURRENCY_SYMBOLS = {
    '$': {'USD', 'CAD', 'AUD', 'NZD', 'SGD', 'HKD'},
    '£': {'GBP'},
    '€': {'EUR'},
    '¥': {'JPY', 'CNY'},
}


def _detect_multiple_currencies(text):
    if not text:
        return None

    codes = {match.group(1).upper() for match in _CURRENCY_CODE_PATTERN.finditer(text)}
    symbols = {symbol for symbol in _CURRENCY_SYMBOLS if symbol in text}

    warning = False
    if len(codes) >= 2:
        warning = True
    elif len(codes) == 1:
        only_code = next(iter(codes))
        if symbols:
            if symbols == {'$'} and only_code in _CURRENCY_SYMBOLS['$']:
                warning = False
            elif symbols == {'£'} and only_code == 'GBP':
                warning = False
            elif symbols == {'€'} and only_code == 'EUR':
                warning = False
            elif symbols == {'¥'} and only_code in _CURRENCY_SYMBOLS['¥']:
                warning = False
            else:
                warning = True
    else:
        if len(symbols) >= 2:
            warning = True

    if not warning:
        return None

    marker_list = sorted(codes) + sorted(symbols)
    return {
        "message": "Multiple currencies detected in the supplier quote. Please confirm line currencies before saving.",
        "markers": marker_list,
    }


def extract_supplier_quote_data(quote_text, context_parts=""):
    """
    Use OpenAI to extract supplier quote information from text
    """
    try:
        logger.info("extract_supplier_quote_data: starting")
        logger.debug("Quote text (first 1000 chars): %r", quote_text[:1000])
        logger.debug("Context parts (first 1000 chars): %r", context_parts[:1000])

        peerless_lookup_requested = {}
        peerless_lookup_quoted = {}
        if _is_peerless_quote(quote_text):
            peerless_lookup_requested, peerless_lookup_quoted = _build_peerless_part_lookup(quote_text)

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": """You are an assistant that extracts supplier quote information from emails or text responses.
We are in the aerospace hardware industry.

Output ONLY a valid JSON array of objects with DOUBLE QUOTES for all keys and string values.
Do NOT use markdown formatting like ```json or any wrappers. Output raw JSON only.

  Each object should have:
  - part_number: The part number the supplier is quoting (clean, no extra text). CRITICAL: If the text includes "Quoting:", "Quoted:", "Quoting PN:", "Alt PN:", or similar followed by a part number, USE THAT part number as the part_number field, NOT the requested/original part number.
  - quantity: Quantity quoted (integer, default to 1 if not specified)
  - qty_available: Quantity available/in stock (integer, null if not specified)
  - purchase_increment: Purchasing/order increment (integer, null if not specified)
  - moq: Minimum order quantity (integer, null if not specified)
  - price: Unit price (decimal number, extract just the number)
  - lead_time_days: Lead time in days (integer, null if not specified)
  - condition: Condition code like "NE", "OH", "SV", "AR" (use null if not specified)
- certifications: Keep this concise. Prefer "OEM certs" if there is full trace to OEM/manufacturer. Use "EASA Form 1" or "8130-3" when those certificates are mentioned. Use "Dual release (8130/EASA)" if both are present. Use "no trace" ONLY when explicitly stated (e.g., "no certs", "no trace", "distributor C of C only"). If not mentioned, use null. Omit DFARS/ITAR/testing notes from this field.
- is_no_bid: true if supplier declined to quote this part, false otherwise
- manufacturer: Extract the manufacturer name if mentioned. Look for common aerospace hardware brands like "Cherry", "Alcoa", "Arconic", "Allfast", "SPS", "Monogram", "Fairchild", "Kaynar", "Huck", "Shur-Lok".
- notes: Any additional relevant notes about this line. Include the originally requested part number here if it differs from what the supplier is quoting. If DFARS/ITAR compliance, test reports, or other paperwork details are mentioned, put them here instead of certifications.

Look for common patterns:
- "No quote", "Not available", "NQ", "N/A" = is_no_bid: true
- Lead times like "3-4 weeks", "Stock", "ARO" should be converted to days (weeks * 7)
- Condition codes are usually 2 letters
- Prices might have currency symbols - extract just the number
- IMPORTANT: When suppliers quote alternative part numbers (e.g., "CR3212-4-04 / Quoting: NAS9301B-5-10"), the part_number field MUST be the alternative/quoted part (NAS9301B-5-10), NOT the requested part (CR3212-4-04)
- Peerless Aerospace often shows "CUSTOMER PART / PAF PART". In that format, treat the right side as the quoted part_number and include "Requested PN: <left side>" in notes.
- If MOQ is present, the quoted quantity should be at least the MOQ"""
                },
                {
                    "role": "user",
                    "content": f"""Extract quote information from this supplier response.

Parts we requested:
{context_parts}

Supplier's response:
{quote_text}

Extract all quoted items into a JSON array.

CRITICAL REMINDER: If you see text like "CR3212-4-04 / Quoting: NAS9301B-5-10", the part_number field must be "NAS9301B-5-10" (the quoted/alternative part), and the notes field should mention "Requested PN: CR3212-4-04"."""
                }
            ],
            max_tokens=5000,
            temperature=0.2,
        )

        raw_content = response.choices[0].message.content
        logger.debug("Raw model content: %r", raw_content)

        extracted_text = raw_content.strip()

        # Strip markdown fences if present
        if extracted_text.startswith('```json'):
            logger.debug("Stripping ```json fences from output")
            extracted_text = extracted_text[7:-3].strip()
        elif extracted_text.startswith('```'):
            logger.debug("Stripping ``` fences from output")
            extracted_text = extracted_text[3:-3].strip()

        # Extra safety: isolate the JSON array if there's stray text
        start = extracted_text.find('[')
        end = extracted_text.rfind(']')
        if start != -1 and end != -1 and start < end:
            if start != 0 or end != len(extracted_text) - 1:
                logger.debug("Isolating JSON array from surrounding text")
            extracted_text = extracted_text[start:end+1]

        logger.debug("Extracted JSON candidate string (first 1000 chars): %r",
                     extracted_text[:1000])

        # Parse JSON
        try:
            parsed_data = json.loads(extracted_text)
            logger.debug("json.loads succeeded. Type: %s", type(parsed_data))
        except json.JSONDecodeError as e:
            logger.error("JSON parsing error: %s", e)
            logger.error("Extracted text that failed JSON parse (first 2000 chars): %r",
                         extracted_text[:2000])
            import ast
            try:
                parsed_data = ast.literal_eval(extracted_text)
                logger.debug("ast.literal_eval succeeded. Type: %s", type(parsed_data))
            except Exception as e2:
                logger.error("ast.literal_eval also failed: %s", e2)
                return []

        # Sometimes the model returns a single object instead of a list
        if isinstance(parsed_data, dict):
            logger.debug("Parsed data is dict; wrapping in list")
            parsed_data = [parsed_data]

        if not isinstance(parsed_data, list):
            logger.error("Parsed data is not a list. Type: %s, value: %r",
                         type(parsed_data), parsed_data)
            return []

        logger.info("Parsed %d raw items from model", len(parsed_data))

        # Validate and clean data
        cleaned_data = []
        for idx, item in enumerate(parsed_data):
            logger.debug("Raw item %d: %r", idx, item)

            if not isinstance(item, dict):
                logger.debug("Skipping non-dict item at index %d", idx)
                continue

            try:
                part_number = str(item.get('part_number', '')).strip()
                if not part_number:
                    logger.debug("Skipping item %d due to empty part_number", idx)
                    continue

                quantity = _safe_int(item.get('quantity', 1)) or 1
                qty_available = _safe_int(
                    item.get('qty_available')
                    or item.get('quantity_available')
                    or item.get('available_qty')
                    or item.get('inventory')
                )
                purchase_increment = _safe_int(
                    item.get('purchase_increment')
                    or item.get('increment')
                    or item.get('order_increment')
                )
                moq = _safe_int(
                    item.get('moq')
                    or item.get('minimum_order')
                    or item.get('minimum_order_quantity')
                )
                price = _safe_float(item.get('price'))
                lead_time_days = _safe_int(item.get('lead_time_days'))

                condition = _normalize_optional_text(item.get('condition'))
                certifications = _normalize_optional_text(item.get('certifications'))
                manufacturer = _normalize_optional_text(
                    item.get('manufacturer')
                    or item.get('manufacturer_name')
                    or item.get('mfg')
                    or item.get('mfr')
                )

                is_no_bid_raw = item.get('is_no_bid', False)
                if isinstance(is_no_bid_raw, str):
                    is_no_bid = is_no_bid_raw.strip().lower() in ('true', '1', 'yes', 'y')
                else:
                    is_no_bid = bool(is_no_bid_raw)

                notes = str(item.get('notes', '')).strip() or None

                if peerless_lookup_requested or peerless_lookup_quoted:
                    peerless_pair = None
                    if '/' in part_number:
                        pairs = _extract_peerless_part_pairs(part_number)
                        if pairs:
                            peerless_pair = pairs[0]
                    if not peerless_pair and notes and '/' in notes:
                        pairs = _extract_peerless_part_pairs(notes)
                        if pairs:
                            peerless_pair = pairs[0]
                    if not peerless_pair:
                        key = _normalize_part_number_key(part_number)
                        peerless_pair = peerless_lookup_quoted.get(key) or peerless_lookup_requested.get(key)
                    if peerless_pair:
                        requested_pn, quoted_pn = peerless_pair
                        part_number = quoted_pn
                        if not notes or requested_pn not in notes:
                            notes = f"{notes}; Requested PN: {requested_pn}" if notes else f"Requested PN: {requested_pn}"

                # First, check if the AI already put "Requested PN: XXX" in the notes
                # This means the AI already identified the quoted vs requested part numbers
                requested_pn_from_notes = _extract_requested_part_number(notes)

                if requested_pn_from_notes:
                    # AI already did the work: part_number is the quoted one, notes has the requested one
                    match_part_number = requested_pn_from_notes
                else:
                    # AI didn't identify it, so we need to extract it ourselves
                    # Keep the original part_number for matching purposes
                    match_part_number = part_number

                    # Try to extract quoted part number from notes first
                    quoted_part_number = _extract_quoted_part_number(notes)

                    # If not found in notes, try extracting from the part_number field itself
                    # (in case AI included the full text there)
                    if not quoted_part_number:
                        quoted_part_number = _extract_quoted_part_number(part_number)

                    # If we found a quoted part number different from what we have, update it
                    if quoted_part_number and quoted_part_number != part_number:
                        # Keep track of the original/requested part number for matching
                        match_part_number = part_number
                        # Keep track of the original/requested part number in notes
                        if not notes or part_number not in notes:
                            notes = f"{notes}; Requested PN: {part_number}" if notes else f"Requested PN: {part_number}"
                        part_number = quoted_part_number

                certifications, notes = _standardize_certifications(certifications, notes)

                if moq is not None and (quantity is None or quantity < moq):
                    quantity = moq

                cleaned_item = {
                    'part_number': part_number,
                    'match_part_number': match_part_number,
                    'quantity': quantity,
                    'qty_available': qty_available,
                    'purchase_increment': purchase_increment,
                    'moq': moq,
                    'price': price,
                    'lead_time_days': lead_time_days,
                    'condition': condition,
                    'certifications': certifications,
                    'is_no_bid': is_no_bid,
                    'notes': notes,
                    'manufacturer': manufacturer
                }

                logger.debug("Cleaned item %d: %r", idx, cleaned_item)
                cleaned_data.append(cleaned_item)

            except Exception as item_exc:
                logger.exception("Error cleaning item at index %d: %r", idx, item)
                # Continue with the next item instead of killing everything
                continue

        logger.info("extract_supplier_quote_data: returning %d cleaned items",
                    len(cleaned_data))
        return cleaned_data

    except Exception as e:
        logger.exception(f"Error extracting supplier quote data: {e}")
        return []



@parts_list_bp.route('/parts_list')
def parts_list():
    """
    Display the parts list lookup interface (input/analysis only)
    """
    list_id = request.args.get('list_id', type=int)

    # If there's a list_id, redirect to the view page
    if list_id:
        return redirect(url_for('parts_list.view_parts_list', list_id=list_id))

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Parts List Lookup', None)
    ]

    return render_template('parts_list.html',
                           breadcrumbs=breadcrumbs,
                           loaded_list=None)


@parts_list_bp.route('/extract_parts_data', methods=['POST'])
def extract_parts_data():
    """
    Extract part numbers and quantities from free-form text using AI
    """
    logging.debug("Received request to extract parts data")
    logging.debug(f"Request headers: {request.headers}")
    logging.debug(f"Form data: {request.form}")

    try:
        request_data = request.form.get('request_data')
        if not request_data:
            logging.error("'request_data' not found in form data")
            return jsonify({'success': False, 'error': "'request_data' is required"}), 400

        logging.debug(f"Extracting data from: {request_data}")

        extracted_parts, warnings, batched = extract_part_numbers_and_quantities_batched(request_data)
        logging.debug(f"Extracted parts: {extracted_parts}")

        if not extracted_parts:
            error_message = "AI did not return any part numbers."
            if batched:
                error_message = (
                    "AI did not return any part numbers. The text was large, so it was split into batches. "
                    "Try removing headers/signatures or splitting the request into smaller chunks."
                )
            if warnings:
                error_message = f"{error_message} {' '.join(warnings)}"
            return jsonify({'success': False, 'error': error_message, 'warnings': warnings, 'batched': batched})

        return jsonify({'success': True, 'parts': extracted_parts, 'warnings': warnings, 'batched': batched})
    except Exception as e:
        logging.exception(f'Error in extract_parts_data: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


def _split_parts_ai_chunks(request_data):
    if isinstance(request_data, bytes):
        request_data = request_data.decode(errors='ignore')
    request_text = str(request_data or "")
    if len(request_text) <= AI_PARTS_MAX_CHARS:
        return [request_text]

    lines = request_text.splitlines()
    header_lines = lines[:AI_PARTS_HEADER_LINES]
    header_text = "\n".join(header_lines).strip()
    if len(header_text) > AI_PARTS_HEADER_CHAR_LIMIT:
        header_text = header_text[:AI_PARTS_HEADER_CHAR_LIMIT].rstrip()
    header_prefix = f"{header_text}\n\n" if header_text else ""

    body_lines = lines[AI_PARTS_HEADER_LINES:]
    chunks = []
    chunk_body_max = max(AI_PARTS_MAX_CHARS - len(header_prefix), 1000)
    current_lines = []
    current_len = 0
    for line in body_lines:
        line_len = len(line) + 1
        if current_lines and current_len + line_len > chunk_body_max:
            chunks.append(header_prefix + "\n".join(current_lines))
            current_lines = [line]
            current_len = line_len
        else:
            current_lines.append(line)
            current_len += line_len
    if current_lines:
        chunks.append(header_prefix + "\n".join(current_lines))

    return chunks or [request_text[:AI_PARTS_MAX_CHARS]]


def _dedupe_extracted_parts(parts):
    deduped = []
    seen = set()
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_number = str(part.get('part_number', '')).strip()
        if not part_number:
            continue
        quantity = part.get('quantity', 1)
        key = (part_number.lower(), str(quantity))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(part)
    return deduped


def extract_part_numbers_and_quantities_batched(request_data):
    chunks = _split_parts_ai_chunks(request_data)
    warnings = []
    parts = []
    if len(chunks) > 1:
        logging.info("AI parts extraction: splitting input into %s batches", len(chunks))
        warnings.append(f"Input was split into {len(chunks)} batches to avoid token limits.")

    for index, chunk in enumerate(chunks, start=1):
        try:
            extracted = extract_part_numbers_and_quantities(chunk)
            parts.extend(extracted or [])
        except Exception as exc:
            warnings.append(f"Batch {index} failed: {exc}")
            logging.exception("AI parts extraction failed on batch %s", index)

    return _dedupe_extracted_parts(parts), warnings, len(chunks) > 1


def extract_part_numbers_and_quantities(request_data):
    """
    Use OpenAI to extract part numbers and quantities from text
    """
    print("Starting extract_part_numbers_and_quantities function")
    # Using aerospace context
    print(f"Input request_data:\n{request_data}")

    try:
        # Hard cap content sent to the model to avoid context blowups from huge emails
        if isinstance(request_data, bytes):
            request_data = request_data.decode(errors='ignore')
        request_data = str(request_data)
        if len(request_data) > AI_PARTS_MAX_CHARS:
            print(f"Trimming request_data from {len(request_data)} to {AI_PARTS_MAX_CHARS} characters for AI call")
            request_data = request_data[:AI_PARTS_MAX_CHARS]

        print("Attempting to send request to OpenAI API")
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system",
                 "content": "You are an assistant tasked with extracting part numbers and quantities from text in the aerospace hardware industry. "
                            "Look for common aerospace hardware like Cherry rivets, Alcoa fasteners, nuts, bolts, and electronic components. "
                            "Output ONLY a valid JSON array of objects, using DOUBLE QUOTES for all keys and string values, like: "
                            "[{\"part_number\": \"ABC-123\", \"quantity\": 2}, {\"part_number\": \"XYZ-456\", \"quantity\": 1}]. "
                            "Do NOT use markdown formatting like ```json or any wrappers. Output raw JSON only. "
                            "Do not include any additional text, explanations, or the word 'quantity' outside of the JSON key. "
                            "If quantity is not mentioned for a part, default to 1. "
                            "Aircraft part numbers can contain letters, numbers, and hyphens. "
                            "Ensure part_number values are clean and do not include words like 'quantity' or numbers attached erroneously."},
                {"role": "user",
                 "content": f"Please extract part numbers and quantities from the following text:\n\n{request_data}"}
            ],
            max_tokens=AI_PARTS_RESPONSE_TOKENS,
            temperature=0.2,
        )
        print("Successfully received response from OpenAI API")
        print(f"Full API response:\n{response}")

        extracted_data = response.choices[0].message.content.strip()
        print(f"Extracted data from API response:\n{extracted_data}")

        # Strip markdown fences if present (e.g., ```json ... ```)
        if extracted_data.startswith('```json') and extracted_data.endswith('```'):
            extracted_data = extracted_data[7:-3].strip()  # Remove ```json and ```
        elif extracted_data.startswith('```') and extracted_data.endswith('```'):
            extracted_data = extracted_data[3:-3].strip()  # Generic markdown strip
        print(f"Data after markdown stripping:\n{extracted_data}")

        # Parse as JSON for reliability, with fallback to ast.literal_eval for single-quote Python-like output
        import json
        import ast
        parsed_data = []
        try:
            # First, try strict JSON
            parsed_data = json.loads(extracted_data)
            print("Successfully parsed with json.loads")
        except json.JSONDecodeError as e:
            print(f"JSON parsing error: {e}. Trying ast.literal_eval for Python-like syntax.")
            try:
                # Fallback: Treat as Python literal (handles single quotes)
                parsed_data = ast.literal_eval(extracted_data)
                print("Successfully parsed with ast.literal_eval")
            except (ValueError, SyntaxError) as e2:
                print(f"ast.literal_eval also failed: {e2}. Falling back to legacy parsing.")
                # Final fallback to original line-based parsing if both fail
                parsed_data = parse_extracted_parts_data(extracted_data)

        # Ensure it's a list of dicts with required keys; default quantity if missing
        if isinstance(parsed_data, list):
            for part in parsed_data:
                if isinstance(part, dict) and 'part_number' in part:
                    if 'quantity' not in part:
                        part['quantity'] = 1
                    # Clean part_number: remove any stray 'quantity' mentions
                    part['part_number'] = str(part['part_number']).replace('quantity', '').strip()
                else:
                    print(f"Warning: Invalid part structure: {part}")
                    # Optionally remove invalid parts
                    parsed_data = [p for p in parsed_data if isinstance(p, dict) and 'part_number' in p]
        else:
            print(f"Warning: Expected list, got: {type(parsed_data)}")
            parsed_data = []

        print(f"Final parsed data: {parsed_data}")

        return parsed_data

    except openai.AuthenticationError as e:
        print(f"Authentication error: {str(e)}")
        print("Check your OpenAI API key.")
        raise
    except openai.APIError as e:
        print(f"OpenAI API error: {str(e)}")
        raise
    except Exception as e:
        print(f"Unexpected error in extract_part_numbers_and_quantities: {str(e)}")
        raise


def parse_extracted_parts_data(extracted_data):
    """
    Fallback: Parse the AI response to extract part numbers and quantities (legacy method)
    """
    parts = []
    lines = extracted_data.strip().split('\n')

    current_part = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Look for part number (case-insensitive)
        if 'part number:' in line.lower():
            # If we have a current part being built, save it
            if current_part.get('part_number'):
                parts.append(current_part)
                current_part = {}

            # Extract part number
            part_number = line.split(':', 1)[1].strip()
            # Clean: remove any stray 'quantity' mentions
            part_number = part_number.replace('quantity', '').strip()
            current_part['part_number'] = part_number

        # Look for quantity (case-insensitive, but avoid word 'quantity' in key)
        elif 'quantity:' in line.lower():
            quantity_str = line.split(':', 1)[1].strip()
            # Clean quantity_str too, in case it has extra text
            quantity_str = quantity_str.replace('quantity', '').strip()
            try:
                quantity = int(quantity_str)
                current_part['quantity'] = quantity
            except ValueError:
                current_part['quantity'] = 1

    # Don't forget the last part
    if current_part.get('part_number'):
        parts.append(current_part)

    # Ensure all parts have a quantity
    for part in parts:
        if 'quantity' not in part:
            part['quantity'] = 1
        # Double-check cleaning on all part_numbers
        part['part_number'] = str(part['part_number']).replace('quantity', '').strip()

    return parts


# UPDATED analyze_parts_list ROUTE WITH WILDCARD SUPPORT
# Replace your existing analyze_parts_list route with this version

@parts_list_bp.route('/analyze', methods=['POST'])
def analyze_parts_list():
    """
    Lookup a submitted list of parts and return metrics including BOM, VQ, Sales Order,
    CQ, PO, Stock, and ILS data
    Supports wildcard search using * character (e.g., ABC*, *123, ABC*XYZ)
    """
    try:
        data = request.get_json()
        parts_data = data.get('parts', [])
        customer_ids = data.get('customer_ids', [])  # Optional customer filter - array of IDs

        if not parts_data:
            return jsonify(success=False, message="No parts data provided"), 400

        # Process each part number to get base part number or handle wildcard
        processed_parts = []
        for part_row in parts_data:
            part_number = part_row.get('part_number', '').strip()
            if not part_number:
                continue

            # Check if this is a wildcard search
            if '*' in part_number:
                processed_parts.append({
                    'part_number': part_number,
                    'base_part_number': None,  # Will be handled differently
                    'is_wildcard': True,
                    'quantity': part_row.get('quantity', 1),
                    'line_id': part_row.get('line_id'),
                    'line_number': part_row.get('line_number')
                })
            else:
                base_part_number = create_base_part_number(part_number)
                processed_parts.append({
                    'part_number': part_number,
                    'base_part_number': base_part_number,
                    'is_wildcard': False,
                    'quantity': part_row.get('quantity', 1),
                    'line_id': part_row.get('line_id'),
                    'line_number': part_row.get('line_number')
                })

        # Get comprehensive part information from database
        results = []
        shared_cache = {}
        with db_cursor() as cursor:
            for part in processed_parts:
                quantity = part['quantity']

                # Handle wildcard searches
                if part.get('is_wildcard'):
                    wildcard_pattern = part['part_number'].replace('*', '%')

                    # Find matching parts
                    matching_parts = _execute_with_cursor(cursor, '''
                        SELECT DISTINCT base_part_number, part_number
                        FROM part_numbers
                        WHERE base_part_number LIKE ? OR part_number LIKE ?
                        LIMIT 50
                    ''', (wildcard_pattern, wildcard_pattern)).fetchall()

                    if not matching_parts:
                        # No matches found
                        result = {
                            'input_part_number': part['part_number'],
                            'base_part_number': None,
                            'quantity': quantity,
                            'line_id': part.get('line_id'),
                            'line_number': part.get('line_number'),
                            'found': False,
                            'is_wildcard': True,
                            'wildcard_match_count': 0,
                            'message': f"No parts found matching pattern: {part['part_number']}"
                        }
                        results.append(result)
                        continue

                    # Process each matching part
                    for match_idx, match in enumerate(matching_parts):
                        base_part_number = match['base_part_number']
                        matched_part_number = match['part_number']

                        # Add a sub-line number for wildcard matches (e.g., 1.1, 1.2, etc.)
                        if part.get('line_number'):
                            sub_line_number = Decimal(str(part['line_number'])) + (Decimal(match_idx) * Decimal('0.1'))
                        else:
                            sub_line_number = None

                        # Now do the normal lookup for this specific part
                        result = _lookup_single_part(
                            cursor=cursor,
                            base_part_number=base_part_number,
                            input_part_number=matched_part_number,
                            quantity=quantity,
                            line_id=None,  # Wildcard matches don't have line IDs
                            line_number=sub_line_number,
                            customer_ids=customer_ids,
                            shared_cache=shared_cache,
                            is_wildcard_match=True,
                            wildcard_pattern=part['part_number']
                        )
                        results.append(result)

                    continue  # Move to next part

                # Regular (non-wildcard) lookup
                base_part_number = part['base_part_number']
                result = _lookup_single_part(
                    cursor=cursor,
                    base_part_number=base_part_number,
                    input_part_number=part['part_number'],
                    quantity=quantity,
                    line_id=part.get('line_id'),
                    line_number=part.get('line_number'),
                    customer_ids=customer_ids,
                    shared_cache=shared_cache,
                    is_wildcard_match=False
                )
                results.append(result)

        return jsonify(success=True, results=results)

    except Exception as e:
        logging.error(f'Error looking up parts: {e}')
        return jsonify(success=False, message=str(e)), 500


def _lookup_single_part(cursor, base_part_number, input_part_number, quantity,
                        line_id, line_number, customer_ids, shared_cache=None,
                        is_wildcard_match=False, wildcard_pattern=None):
    """
    Helper function to look up a single part's data
    Extracted from analyze_parts_list to handle both regular and wildcard searches
    """
    line_type = None
    parent_line_id = None
    parent_customer_part_number = None

    if line_id:
        line_meta = _execute_with_cursor(cursor, """
            SELECT
                pll.line_number,
                pll.parent_line_id,
                pll.line_type,
                parent.customer_part_number AS parent_customer_part_number
            FROM parts_list_lines pll
            LEFT JOIN parts_list_lines parent ON parent.id = pll.parent_line_id
            WHERE pll.id = ?
        """, (line_id,)).fetchone()
        if line_meta:
            parent_line_id = line_meta['parent_line_id']
            line_type = line_meta['line_type'] or 'normal'
            parent_customer_part_number = line_meta['parent_customer_part_number']
            line_number = line_meta['line_number']

    def _get_line_specific(line_id_value):
        chosen_cost = None
        chosen_supplier_name = None
        chosen_currency_code = None
        chosen_currency_symbol = None
        suggested_suppliers_count = 0
        emails_sent_count = 0
        quoted_price = None
        quoted_supplier_name = None
        quoted_currency_code = None
        quoted_currency_symbol = None
        contacted_suppliers = []
        contacted_suppliers_count = 0
        supplier_quote_count = 0

        if not line_id_value:
            return (
                chosen_cost,
                chosen_supplier_name,
                chosen_currency_code,
                chosen_currency_symbol,
                suggested_suppliers_count,
                emails_sent_count,
                quoted_price,
                quoted_supplier_name,
                quoted_currency_code,
                quoted_currency_symbol,
                contacted_suppliers,
                contacted_suppliers_count,
                supplier_quote_count
            )

        chosen_info = _execute_with_cursor(cursor, '''
            SELECT 
                pll.chosen_cost,
                pll.chosen_currency_id,
                pll.chosen_supplier_id,
                s.name as supplier_name,
                c.currency_code,
                c.symbol
            FROM parts_list_lines pll
            LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
            LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
            WHERE pll.id = ?
        ''', (line_id_value,)).fetchone()

        if chosen_info:
            chosen_cost = chosen_info['chosen_cost']
            chosen_supplier_name = chosen_info['supplier_name']
            chosen_currency_code = chosen_info['currency_code']
            chosen_currency_symbol = chosen_info['symbol']

        suggested_count = _execute_with_cursor(cursor, '''
            SELECT COUNT(*) as count
            FROM parts_list_line_suggested_suppliers
            WHERE parts_list_line_id = ?
        ''', (line_id_value,)).fetchone()
        suggested_suppliers_count = suggested_count['count'] if suggested_count else 0

        email_count = _execute_with_cursor(cursor, '''
            SELECT COUNT(*) as count
            FROM parts_list_line_supplier_emails
            WHERE parts_list_line_id = ?
        ''', (line_id_value,)).fetchone()
        emails_sent_count = email_count['count'] if email_count else 0

        quoted_info = _execute_with_cursor(cursor, '''
            SELECT
                cql.quote_price_gbp,
                cql.quoted_status,
                cql.is_no_bid
            FROM customer_quote_lines cql
            WHERE cql.parts_list_line_id = ?
              AND cql.quoted_status = 'quoted'
              AND COALESCE(CAST(cql.is_no_bid AS INTEGER), 0) = 0
              AND cql.quote_price_gbp IS NOT NULL
            ORDER BY cql.date_modified DESC, cql.id DESC
            LIMIT 1
        ''', (line_id_value,)).fetchone()

        if quoted_info:
            quoted_price = quoted_info['quote_price_gbp']
            quoted_supplier_name = None
            quoted_currency_code = 'GBP'
            quoted_currency_symbol = '£'

        quote_line_id = parent_line_id or line_id_value
        supplier_quote_info = _execute_with_cursor(cursor, '''
            SELECT COUNT(*) as count
            FROM parts_list_supplier_quote_lines
            WHERE parts_list_line_id = ?
        ''', (quote_line_id,)).fetchone()
        supplier_quote_count = supplier_quote_info['count'] if supplier_quote_info else 0

        contacted_count = _execute_with_cursor(cursor, '''
            SELECT COUNT(DISTINCT supplier_id) as count
            FROM parts_list_line_supplier_emails
            WHERE parts_list_line_id = ?
        ''', (line_id_value,)).fetchone()
        contacted_suppliers_count = contacted_count['count'] if contacted_count else 0

        contacted_rows = _execute_with_cursor(cursor, '''
            SELECT s.name as supplier_name, MAX(se.date_sent) as last_sent
            FROM parts_list_line_supplier_emails se
            JOIN suppliers s ON s.id = se.supplier_id
            WHERE se.parts_list_line_id = ?
            GROUP BY s.id, s.name
            ORDER BY last_sent DESC
            LIMIT 3
        ''', (line_id_value,)).fetchall()
        contacted_suppliers = [row['supplier_name'] for row in contacted_rows or [] if row['supplier_name']]

        return (
            chosen_cost,
            chosen_supplier_name,
            chosen_currency_code,
            chosen_currency_symbol,
            suggested_suppliers_count,
            emails_sent_count,
            quoted_price,
            quoted_supplier_name,
            quoted_currency_code,
            quoted_currency_symbol,
            contacted_suppliers,
            contacted_suppliers_count,
            supplier_quote_count
        )

    cache_key = (base_part_number, tuple(customer_ids or []))
    if shared_cache is not None and cache_key in shared_cache:
        cached_result = copy.deepcopy(shared_cache[cache_key])
        cached_result['input_part_number'] = input_part_number
        cached_result['quantity'] = quantity
        cached_result['line_id'] = line_id
        cached_result['line_number'] = line_number
        cached_result['is_wildcard_match'] = is_wildcard_match
        if is_wildcard_match:
            cached_result['wildcard_pattern'] = wildcard_pattern
            cached_result['global_alternatives'] = []
            cached_result['global_alternatives_count'] = 0
            cached_result['global_alternatives_in_stock_count'] = 0
        else:
            cached_result.pop('wildcard_pattern', None)

        chosen_cost, chosen_supplier_name, chosen_currency_code, chosen_currency_symbol, suggested_suppliers_count, emails_sent_count, quoted_price, quoted_supplier_name, quoted_currency_code, quoted_currency_symbol, contacted_suppliers, contacted_suppliers_count, supplier_quote_count = _get_line_specific(line_id)
        cached_result['chosen_cost'] = chosen_cost
        cached_result['has_chosen_cost'] = bool(chosen_cost is not None)
        cached_result['chosen_supplier_name'] = chosen_supplier_name
        cached_result['chosen_currency_code'] = chosen_currency_code
        cached_result['chosen_currency_symbol'] = chosen_currency_symbol
        cached_result['suggested_suppliers_count'] = suggested_suppliers_count
        cached_result['emails_sent_count'] = emails_sent_count
        cached_result['line_quote_price'] = quoted_price
        cached_result['line_quote_supplier_name'] = quoted_supplier_name
        cached_result['line_quote_currency_code'] = quoted_currency_code
        cached_result['line_quote_currency_symbol'] = quoted_currency_symbol
        cached_result['line_contacted_suppliers'] = contacted_suppliers
        cached_result['line_contacted_suppliers_count'] = contacted_suppliers_count
        cached_result['line_supplier_quote_count'] = supplier_quote_count
        return cached_result

    # Get basic part details
    part_info = _execute_with_cursor(cursor, '''
        SELECT base_part_number, part_number, system_part_number, category_id
        FROM part_numbers
        WHERE base_part_number = ?
    ''', (base_part_number,)).fetchone()

    # Get BOM usage - where this part is used as a component
    bom_usage = _execute_with_cursor(cursor, '''
        SELECT 
            bh.id as bom_id,
            bh.name as bom_name,
            bl.quantity as qty_per_bom,
            bl.guide_price
        FROM bom_lines bl
        JOIN bom_headers bh ON bl.bom_header_id = bh.id
        WHERE bl.base_part_number = ?
    ''', (base_part_number,)).fetchall()

    # Get VQ data - vendor quotes for this part
    vq_data = _execute_with_cursor(cursor, '''
        SELECT 
            v.vq_number,
            v.entry_date,
            v.supplier_id,
            s.name as supplier_name,
            vl.vendor_price,
            vl.quantity_quoted,
            vl.lead_days,
            c.currency_code,
            v.currency_id
        FROM vq_lines vl
        JOIN vqs v ON vl.vq_id = v.id
        LEFT JOIN suppliers s ON v.supplier_id = s.id
        LEFT JOIN currencies c ON v.currency_id = c.id
        WHERE vl.base_part_number = ?
        ORDER BY v.entry_date DESC
        LIMIT 5
    ''', (base_part_number,)).fetchall()

    # Get Sales Order data - with optional customer filter
    if customer_ids:
        placeholders = ','.join('?' * len(customer_ids))
        so_data = _execute_with_cursor(cursor, f'''
            SELECT 
                so.sales_order_ref,
                so.date_entered,
                c.name as customer_name,
                sol.quantity as order_quantity,
                sol.price as sale_price,
                curr.currency_code
            FROM sales_order_lines sol
            JOIN sales_orders so ON sol.sales_order_id = so.id
            JOIN customers c ON so.customer_id = c.id
            LEFT JOIN currencies curr ON so.currency_id = curr.id
            WHERE sol.base_part_number = ? AND so.customer_id IN ({placeholders})
            ORDER BY so.date_entered DESC
            LIMIT 10
        ''', (base_part_number, *customer_ids)).fetchall()
    else:
        so_data = _execute_with_cursor(cursor, '''
            SELECT 
                so.sales_order_ref,
                so.date_entered,
                c.name as customer_name,
                sol.quantity as order_quantity,
                sol.price as sale_price,
                curr.currency_code
            FROM sales_order_lines sol
            JOIN sales_orders so ON sol.sales_order_id = so.id
            JOIN customers c ON so.customer_id = c.id
            LEFT JOIN currencies curr ON so.currency_id = curr.id
            WHERE sol.base_part_number = ?
            ORDER BY so.date_entered DESC
            LIMIT 10
        ''', (base_part_number,)).fetchall()

    # Get Stock balances
    stock_data = _execute_with_cursor(cursor, '''
        SELECT 
            sm.movement_id,
            sm.base_part_number,
            pn.part_number,
            sm.datecode,
            sm.movement_date,
            sm.cost_per_unit,
            sm.quantity,
            sm.available_quantity,
            sm.reference
        FROM stock_movements sm
        JOIN part_numbers pn ON sm.base_part_number = pn.base_part_number
        WHERE sm.base_part_number = ?
          AND sm.movement_type = 'IN' 
          AND sm.available_quantity > 0
        ORDER BY sm.movement_date
    ''', (base_part_number,)).fetchall()

    # Get Customer Quotes - COMBINED from both CQs and Parts List Customer Quotes
    customer_quotes = _execute_with_cursor(cursor, """
        SELECT 
            'cq' as quote_type,
            COALESCE(c.cq_number, 'CQ #' || c.id) as reference,
            c.id as source_id,
            c.status,
            c.entry_date as quote_date,
            c.due_date,
            cust.name as customer_name,
            cl.quantity_requested,
            cl.quantity_quoted,
            cl.unit_price,
            cl.lead_days,
            curr.currency_code,
            cl.condition_code,
            CASE WHEN COALESCE(CAST(cl.is_no_quote AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END as is_no_quote
        FROM cq_lines cl
        JOIN cqs c ON cl.cq_id = c.id
        LEFT JOIN customers cust ON c.customer_id = cust.id
        LEFT JOIN currencies curr ON c.currency_id = curr.id
        WHERE cl.base_part_number = ?

        UNION ALL

        SELECT 
            'pl_customer_quote' as quote_type,
            'PL #' || pl.id || ' - ' || pl.name as reference,
            pl.id as source_id,
            cql.quoted_status as status,
            cql.date_created as quote_date,
            NULL as due_date,
            c.name as customer_name,
            pll.quantity as quantity_requested,
            COALESCE(pll.chosen_qty, pll.quantity) as quantity_quoted,
            cql.quote_price_gbp as unit_price,
            cql.lead_days,
            'GBP' as currency_code,
            NULL as condition_code,
            CASE WHEN COALESCE(CAST(cql.is_no_bid AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END as is_no_quote
        FROM customer_quote_lines cql
        JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
        JOIN parts_lists pl ON pl.id = pll.parts_list_id
        LEFT JOIN customers c ON c.id = pl.customer_id
        WHERE pll.base_part_number = ?
          AND cql.quoted_status = 'quoted'

        ORDER BY quote_date DESC
        LIMIT 20
    """, (base_part_number, base_part_number)).fetchall()



    # Get Parts List Supplier Quotes data
    parts_list_quotes = _execute_with_cursor(cursor, '''
        SELECT 
            sql.id as quote_line_id,
            sql.quoted_part_number,
            sql.quantity_quoted,
            sql.unit_price,
            sql.lead_time_days,
            sql.condition_code,
            sql.certifications,
            sql.is_no_bid,
            sql.line_notes,
            sq.id as quote_id,
            sq.quote_reference,
            sq.quote_date,
            sq.supplier_id,
            s.name as supplier_name,
            sq.currency_id,
            c.currency_code,
            c.symbol,
            pl.id as parts_list_id,
            pl.name as parts_list_name
        FROM parts_list_supplier_quote_lines sql
        JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
        JOIN suppliers s ON s.id = sq.supplier_id
        LEFT JOIN currencies c ON c.id = sq.currency_id
        JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
        JOIN parts_lists pl ON pl.id = pll.parts_list_id
        WHERE pll.base_part_number = ?
        ORDER BY sq.quote_date DESC, sq.date_created DESC
        LIMIT 10
    ''', (base_part_number,)).fetchall()

    # Get Purchase Order data
    po_data = _execute_with_cursor(cursor, '''
        SELECT 
            po.purchase_order_ref,
            po.date_issued,
            po.supplier_id,
            s.name as supplier_name,
            pol.quantity,
            pol.price,
            pol.ship_date,
            curr.currency_code,
            po.currency_id,
            pos.name as status_name
        FROM purchase_order_lines pol
        JOIN purchase_orders po ON pol.purchase_order_id = po.id
        LEFT JOIN suppliers s ON po.supplier_id = s.id
        LEFT JOIN currencies curr ON po.currency_id = curr.id
        LEFT JOIN purchase_order_statuses pos ON po.purchase_status_id = pos.id
        WHERE pol.base_part_number = ?
        ORDER BY po.date_issued DESC
        LIMIT 10
    ''', (base_part_number,)).fetchall()

    excess_data = _execute_with_cursor(cursor, '''
        SELECT
            l.id,
            l.excess_stock_list_id,
            l.quantity,
            l.date_code,
            l.manufacturer,
            l.unit_price,
            l.unit_price_currency_id,
            c.currency_code,
            c.symbol,
            el.name as list_name,
            el.upload_date,
            el.entered_date,
            s.id as supplier_id,
            s.name as supplier_name
        FROM excess_stock_lines l
        JOIN excess_stock_lists el ON el.id = l.excess_stock_list_id
        LEFT JOIN suppliers s ON s.id = el.supplier_id
        LEFT JOIN currencies c ON c.id = l.unit_price_currency_id
        WHERE l.base_part_number = ?
        ORDER BY el.upload_date DESC, el.entered_date DESC, l.id DESC
        LIMIT 10
    ''', (base_part_number,)).fetchall()

    # Parts List Quotes metrics
    parts_list_quotes_count = len(parts_list_quotes)
    lowest_parts_list_quote_price = None
    lowest_parts_list_quote_supplier = None
    parts_list_quotes_unique_suppliers = set()

    # Around line 670-681 - Parts List Quotes metrics
    if parts_list_quotes:
        # Get unique suppliers
        parts_list_quotes_unique_suppliers = {
            quote['supplier_name']
            for quote in parts_list_quotes
            if quote['supplier_name']
        }

        # Find lowest non-no-bid price - FIXED WITH TYPE CHECK
        prices_with_suppliers = [
            (quote['unit_price'], quote['supplier_name'])
            for quote in parts_list_quotes
            if quote['unit_price'] is not None
               and isinstance(quote['unit_price'], (int, float))  # ADD THIS CHECK
               and not quote['is_no_bid']
        ]
        if prices_with_suppliers:
            lowest_parts_list_quote_price, lowest_parts_list_quote_supplier = min(
                prices_with_suppliers,
                key=lambda x: x[0]
            )

    # Calculate simple sales metrics
    unique_customers = set()
    avg_sale_price = None
    if so_data:
        unique_customers = {so['customer_name'] for so in so_data}
        prices = [so['sale_price'] for so in so_data if so['sale_price']]
        if prices:
            avg_sale_price = sum(prices) / len(prices)

    # Stock metrics
    total_available_stock = sum(row['available_quantity'] for row in stock_data) if stock_data else 0

    # Customer Quotes metrics (combined)
    cq_count = len(customer_quotes)
    total_cq_quantity_requested = sum(cq['quantity_requested'] or 0 for cq in customer_quotes)
    total_cq_quantity_quoted = sum(cq['quantity_quoted'] or 0 for cq in customer_quotes)

    # Calculate average price from valid quotes only
    valid_prices = [cq['unit_price'] for cq in customer_quotes
                    if cq['unit_price'] and not cq['is_no_quote']]
    avg_cq_price = sum(valid_prices) / len(valid_prices) if valid_prices else None

    # Count by type for display
    cq_from_cqs = sum(1 for cq in customer_quotes if cq['quote_type'] == 'cq')
    cq_from_parts_lists = sum(1 for cq in customer_quotes if cq['quote_type'] == 'pl_customer_quote')

    # VQ metrics
    # VQ metrics
    lowest_vq_price = None
    lowest_vq_supplier = None
    if vq_data:
        prices_with_suppliers = [
            (vq['vendor_price'], vq['supplier_name'])
            for vq in vq_data
            if vq['vendor_price'] is not None
               and isinstance(vq['vendor_price'], (int, float))  # ADD THIS CHECK
        ]
        if prices_with_suppliers:
            lowest_vq_price, lowest_vq_supplier = min(prices_with_suppliers, key=lambda x: x[0])

    # PO metrics
    total_po_quantity = 0
    avg_po_price = None
    most_recent_po_supplier = None
    if po_data:
        total_po_quantity = sum(po['quantity'] or 0 for po in po_data)
        prices = [po['price'] for po in po_data if po['price']]
        if prices:
            avg_po_price = sum(prices) / len(prices)
        if po_data:
            most_recent_po_supplier = po_data[0]['supplier_name']

    # Excess metrics
    excess_count = len(excess_data)
    lowest_excess_price = None
    lowest_excess_supplier = None
    lowest_excess_supplier_id = None
    lowest_excess_currency_code = None
    lowest_excess_currency_id = None
    lowest_excess_list_id = None
    if excess_data:
        priced_rows = [
            row for row in excess_data
            if row['unit_price'] is not None and isinstance(row['unit_price'], (int, float))
        ]
        if priced_rows:
            lowest_row = min(priced_rows, key=lambda x: x['unit_price'])
            lowest_excess_price = lowest_row['unit_price']
            lowest_excess_supplier = lowest_row['supplier_name']
            lowest_excess_supplier_id = lowest_row['supplier_id']
            lowest_excess_currency_code = lowest_row['currency_code']
            lowest_excess_currency_id = lowest_row['unit_price_currency_id']
            lowest_excess_list_id = lowest_row['excess_stock_list_id']

    # ILS data
    ils_data = _execute_with_cursor(cursor, '''
        SELECT 
            r.id,
            r.search_date,
            r.ils_company_name,
            r.ils_cage_code,
            r.part_number,
            r.alt_part_number,
            r.quantity,
            r.condition_code,
            r.description,
            r.price,
            r.email,
            r.phone,
            s.name as supplier_name,
            s.id as supplier_id
        FROM ils_search_results r
        LEFT JOIN suppliers s ON r.supplier_id = s.id
        WHERE r.base_part_number = ?
          AND r.id IN (
            SELECT MAX(id) 
            FROM ils_search_results 
            WHERE base_part_number = ?
            GROUP BY ils_company_name
        )
        ORDER BY r.search_date DESC, r.ils_company_name
        LIMIT 50
    ''', (base_part_number, base_part_number)).fetchall()

    ils_total_suppliers = len(ils_data)
    ils_preferred_suppliers = len({row['supplier_id'] for row in ils_data if row['supplier_id']})
    ils_total_quantity = sum(int(qty) for qty in [row['quantity'] for row in ils_data] if qty and qty.isdigit())
    ils_latest_search_date = ils_data[0]['search_date'] if ils_data else None

    qpl_row = _execute_with_cursor(cursor, '''
        SELECT COUNT(*) as count
        FROM manufacturer_approvals
        WHERE airbus_material_base = ? OR manufacturer_part_number_base = ?
    ''', (base_part_number, base_part_number)).fetchone()
    qpl_count = qpl_row['count'] if qpl_row else 0

    # Chosen cost & email/suggested supplier counts
    chosen_cost, chosen_supplier_name, chosen_currency_code, chosen_currency_symbol, suggested_suppliers_count, emails_sent_count, quoted_price, quoted_supplier_name, quoted_currency_code, quoted_currency_symbol, contacted_suppliers, contacted_suppliers_count, supplier_quote_count = _get_line_specific(line_id)

    # Build main result
    result = {
        'input_part_number': input_part_number,
        'base_part_number': base_part_number,
        'quantity': quantity,
        'line_id': line_id,
        'line_number': line_number,
        'line_type': line_type or 'normal',
        'parent_line_id': parent_line_id,
        'found': bool(part_info),
        'is_wildcard_match': is_wildcard_match,

        # BOM
        'bom_usage_count': len(bom_usage),
        'bom_details': [dict(bom) for bom in bom_usage],

        # VQ
        'vq_count': len(vq_data),
        'vq_details': [dict(vq) for vq in vq_data],
        'lowest_vq_price': lowest_vq_price,
        'lowest_vq_supplier': lowest_vq_supplier,

        # Sales Orders
        'so_count': len(so_data),
        'unique_customers_count': len(unique_customers),
        'avg_sale_price': avg_sale_price,
        'so_details': [dict(so) for so in so_data],

        # Stock
        'total_available_stock': total_available_stock,
        'stock_movement_count': len(stock_data),
        'stock_details': [
            {
                'movement_id': row['movement_id'],
                'base_part_number': row['base_part_number'],
                'part_number': row['part_number'],
                'datecode': row['datecode'],
                'receipt_date': row['movement_date'],
                'cost_per_unit': row['cost_per_unit'],
                'original_quantity': row['quantity'],
                'available_quantity': row['available_quantity'],
                'reference': row['reference']
            } for row in stock_data
        ],

        # CQ
        # WITH THESE:
        'cq_count': cq_count,  # ✅ CORRECT
        'cq_from_cqs': cq_from_cqs,  # ✅ NEW - shows breakdown
        'cq_from_parts_lists': cq_from_parts_lists,  # ✅ NEW - shows breakdown
        'total_cq_quantity_requested': total_cq_quantity_requested,
        'total_cq_quantity_quoted': total_cq_quantity_quoted,
        'avg_cq_price': avg_cq_price,
        'cq_details': [dict(cq) for cq in customer_quotes],  # ✅ CORRECT

        # Parts List Supplier Quotes (NEW)
        'parts_list_quotes_count': parts_list_quotes_count,
        'parts_list_quotes_unique_suppliers': len(parts_list_quotes_unique_suppliers),
        'lowest_parts_list_quote_price': lowest_parts_list_quote_price,
        'lowest_parts_list_quote_supplier': lowest_parts_list_quote_supplier,
        'parts_list_quotes_details': [dict(plq) for plq in parts_list_quotes],

        # PO
        'po_count': len(po_data),
        'total_po_quantity': total_po_quantity,
        'avg_po_price': avg_po_price,
        'most_recent_po_supplier': most_recent_po_supplier,
        'po_details': [dict(po) for po in po_data],

        # Excess
        'excess_count': excess_count,
        'lowest_excess_price': lowest_excess_price,
        'lowest_excess_supplier': lowest_excess_supplier,
        'lowest_excess_supplier_id': lowest_excess_supplier_id,
        'lowest_excess_currency_code': lowest_excess_currency_code,
        'lowest_excess_currency_id': lowest_excess_currency_id,
        'lowest_excess_list_id': lowest_excess_list_id,
        'excess_details': [dict(row) for row in excess_data],

        # ILS
        'ils_total_suppliers': ils_total_suppliers,
        'ils_preferred_suppliers': ils_preferred_suppliers,
        'ils_total_quantity': ils_total_quantity,
        'ils_latest_search_date': ils_latest_search_date,
        'ils_details': [dict(row) for row in ils_data],

        # QPL
        'qpl_count': qpl_count,

        # Chosen cost & actions
        'chosen_cost': chosen_cost,
        'has_chosen_cost': bool(chosen_cost is not None),  # ADD THIS LINE
        'chosen_supplier_name': chosen_supplier_name,
        'chosen_currency_code': chosen_currency_code,
        'chosen_currency_symbol': chosen_currency_symbol,
        'suggested_suppliers_count': suggested_suppliers_count,
        'emails_sent_count': emails_sent_count,
        'line_quote_price': quoted_price,
        'line_quote_supplier_name': quoted_supplier_name,
        'line_quote_currency_code': quoted_currency_code,
        'line_quote_currency_symbol': quoted_currency_symbol,
        'line_contacted_suppliers': contacted_suppliers,
        'line_contacted_suppliers_count': contacted_suppliers_count,
        'line_supplier_quote_count': supplier_quote_count,
    }

    if is_wildcard_match:
        result['wildcard_pattern'] = wildcard_pattern

    if part_info:
        result.update({
            'system_part_number': part_info['system_part_number'],
            'category_id': part_info['category_id']
        })

    if line_type == 'alternate' and parent_customer_part_number:
        result['is_global_alternative'] = True
        result['parent_base_part_number'] = parent_customer_part_number

    # === Global Alternatives (only if main part found AND not already an alternative) ===
    # Check if this part is itself a child line (alt or price break)
    is_already_alternative = bool(parent_line_id) or (line_type in {'alternate', 'price_break'}) or (line_number and (line_number % 1 != 0))

    if part_info and not is_already_alternative and not is_wildcard_match:
        global_alts = get_global_alternatives(base_part_number)
        alternatives_with_stock = []

        for alt_base_part in global_alts:
            alt_part_info = _execute_with_cursor(cursor, '''
                SELECT base_part_number, part_number, system_part_number, category_id
                FROM part_numbers
                WHERE base_part_number = ?
                LIMIT 1
            ''', (alt_base_part,)).fetchone()

            alt_stock = _execute_with_cursor(cursor, '''
                SELECT SUM(available_quantity) as total_stock
                FROM stock_movements
                WHERE base_part_number = ?
                  AND movement_type = 'IN'
                  AND available_quantity > 0
            ''', (alt_base_part,)).fetchone()

            alt_has_stock = bool(alt_stock and alt_stock['total_stock'] and alt_stock['total_stock'] > 0)
            alt_total_stock = alt_stock['total_stock'] if alt_has_stock else 0
            alt_part_number = alt_part_info['part_number'] if alt_part_info else alt_base_part

            alt_info = {
                'input_part_number': alt_part_number,
                'base_part_number': alt_base_part,
                'has_stock': alt_has_stock,
                'total_available_stock': alt_total_stock,
                'found': bool(alt_part_info)
            }
            if alt_part_info:
                alt_info.update({
                    'system_part_number': alt_part_info['system_part_number'],
                    'category_id': alt_part_info['category_id']
                })

            alternatives_with_stock.append(alt_info)

        # Attach to main result
        result['global_alternatives'] = alternatives_with_stock
        result['global_alternatives_count'] = len(alternatives_with_stock)
        result['global_alternatives_in_stock_count'] = sum(1 for a in alternatives_with_stock if a['has_stock'])
    else:
        # Part is already an alternative or wildcard - don't look for alternatives
        result['global_alternatives'] = []
        result['global_alternatives_count'] = 0
        result['global_alternatives_in_stock_count'] = 0

    if shared_cache is not None and cache_key not in shared_cache:
        shared_cache[cache_key] = copy.deepcopy(result)

    return result

@parts_list_bp.route('/parts-lists/<int:list_id>/costing', methods=['GET'])
def parts_list_costing(list_id):
    """
    Display costing interface for a parts list
    """
    try:
        header = db_execute(
            """
            SELECT 
                pl.*,
                c.name AS customer_name,
                cont.name AS contact_name,
                cont.email AS contact_email,
                s.name AS status_name,
                p.name AS project_name
            FROM parts_lists pl
            LEFT JOIN customers c ON c.id = pl.customer_id
            LEFT JOIN contacts cont ON cont.id = pl.contact_id
            LEFT JOIN parts_list_statuses s ON s.id = pl.status_id
            LEFT JOIN projects p ON p.id = pl.project_id
            WHERE pl.id = ?
            """,
            (list_id,),
            fetch='one',
        )

        if not header:
            return "Parts list not found", 404

        if header.get('project_id'):
            _repair_project_linked_parts_list_lines(list_id)

        lines = db_execute(
            """
            SELECT 
                pll.*,
                (SELECT COUNT(*) 
                 FROM parts_list_line_suggested_suppliers 
                 WHERE parts_list_line_id = pll.id) as suggested_suppliers_count,
                (SELECT COUNT(*)
                 FROM parts_list_supplier_quote_lines sql
                 JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                 WHERE sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                 AND sql.is_no_bid = FALSE) as quotes_count,
                (SELECT sql.line_notes
                 FROM parts_list_supplier_quote_lines sql
                 WHERE sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                   AND sql.line_notes IS NOT NULL
                   AND TRIM(sql.line_notes) != ''
                 ORDER BY sql.date_modified DESC, sql.id DESC
                 LIMIT 1) as supplier_quote_notes,
                (SELECT COALESCE(SUM(sm.available_quantity), 0)
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity > 0) as stock_available,
                (SELECT SUM(sm.available_quantity * sm.cost_per_unit) / NULLIF(SUM(sm.available_quantity), 0)
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity > 0
                   AND sm.cost_per_unit > 0) as stock_weighted_cost,
                (SELECT sm.cost_per_unit
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity >= COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity)
                   AND sm.cost_per_unit > 0
                 ORDER BY sm.cost_per_unit ASC
                 LIMIT 1) as stock_cost_covering_qty,
                (SELECT sm.available_quantity
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity >= COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity)
                   AND sm.cost_per_unit > 0
                 ORDER BY sm.cost_per_unit ASC
                 LIMIT 1) as stock_covering_qty,
                (SELECT sm.movement_id
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity >= COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity)
                   AND sm.cost_per_unit > 0
                 ORDER BY sm.cost_per_unit ASC
                 LIMIT 1) as stock_covering_movement_id,
                (SELECT sm.cost_per_unit
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity > 0
                   AND sm.cost_per_unit > 0
                 ORDER BY sm.cost_per_unit DESC
                 LIMIT 1) as stock_highest_cost,
                (SELECT sm.movement_id
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity > 0
                   AND sm.cost_per_unit > 0
                 ORDER BY sm.cost_per_unit DESC
                 LIMIT 1) as stock_highest_movement_id
            FROM parts_list_lines pll
            WHERE pll.parts_list_id = ?
            ORDER BY pll.line_number ASC
            """,
            (list_id,),
            fetch='all',
        )

        suppliers = db_execute(
            """
            SELECT id, name FROM suppliers 
            ORDER BY name ASC
            """,
            fetch='all',
        )

        currencies = db_execute(
            """
            SELECT id, currency_code FROM currencies 
            ORDER BY id ASC
            """,
            fetch='all',
        )

        proponent_supplier_id = None
        proponent_setting = db_execute(
            "SELECT value FROM app_settings WHERE key = 'proponent_supplier_id'",
            fetch='one',
        )
        if proponent_setting and proponent_setting.get('value'):
            try:
                proponent_supplier_id = int(proponent_setting['value'])
            except (TypeError, ValueError):
                proponent_supplier_id = None

        # Calculate stats
        total_lines = len(lines)
        lines_with_cost = sum(1 for l in lines if l['chosen_cost'] is not None)
        lines_without_cost = total_lines - lines_with_cost
        lines_fully_in_stock = sum(1 for l in lines if (l['stock_available'] or 0) >= (l['chosen_qty'] or l['quantity']))

        # Calculate total cost (in GBP for simplicity)
        total_cost = sum(
            (l['chosen_cost'] or 0) * (l['chosen_qty'] or l['quantity'])
            for l in lines
            if l['chosen_cost'] and l['chosen_currency_id'] == 3
        )

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Parts Lists', url_for('parts_list.view_parts_lists')),
            (header['name'], url_for('parts_list.parts_list', list_id=list_id)),
            ('Costing', None)
        ]


        open_quote_id = request.args.get('open_quote_id', type=int)

        return render_template('parts_list_costing.html',
                               list_id=list_id,
                               list_name=header['name'],
                               list_notes=header.get('notes'),
                               customer_name=header.get('customer_name'),
                               project_id=header.get('project_id'),
                               project_name=header.get('project_name'),
                               status_id=header.get('status_id'),
                               status_name=header.get('status_name'),
                               lines=[dict(l) for l in lines],
                               suppliers=[dict(s) for s in suppliers],
                               currencies=[dict(c) for c in currencies],
                               open_quote_id=open_quote_id,
                               total_lines=total_lines,
                               lines_with_cost=lines_with_cost,
                               lines_without_cost=lines_without_cost,
                               lines_fully_in_stock=lines_fully_in_stock,
                               total_cost=f"£{total_cost:.2f}",
                               proponent_supplier_id=proponent_supplier_id,
                               breadcrumbs=breadcrumbs)

    except Exception as e:
        logging.exception(e)
        return str(e), 500

@parts_list_bp.route('/parts-lists/qpl', methods=['GET'])
def get_qpl_matches():
    """
    Return QPL manufacturer approvals for a base part number.
    """
    try:
        base_part_number = (request.args.get('part') or '').strip()
        if not base_part_number:
            return jsonify(success=False, message="part parameter is required"), 400

        rows = db_execute(
            """
            SELECT manufacturer_name, cage_code, location
            FROM manufacturer_approvals
            WHERE airbus_material_base = ? OR manufacturer_part_number_base = ?
            ORDER BY manufacturer_name, cage_code, location
            """,
            (base_part_number, base_part_number),
            fetch='all',
        ) or []

        return jsonify(success=True, results=[dict(row) for row in rows])
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/email-suppliers', methods=['GET', 'POST'])
def email_suppliers():
    """
    Display suppliers from ILS results or suggested suppliers for emailing
    Mode: 'ils' (default) or 'suggested'
    """

    # POST: Store data in session and redirect
    if request.method == 'POST':
        data = request.get_json()
        mode = data.get('mode', 'ils')

        logging.info(f"Storing NEW email data in session: list_id={data.get('list_id')}, mode={mode}")
        logging.info(f"Number of parts being stored: {len(data.get('results', []))}")
        logging.info(
            f"First part number: {data['results'][0].get('input_part_number') if data.get('results') else 'NONE'}")

        session['email_data'] = data
        session['email_mode'] = mode

        return jsonify({'success': True, 'redirect': url_for('parts_list.email_suppliers', mode=mode)})

    # GET: Display the page
    mode = request.args.get('mode')

    # If mode is specified in URL, update session
    if mode:
        session['email_mode'] = mode
    else:
        # Otherwise use session or default to 'ils'
        mode = session.get('email_mode', 'ils')

    email_data = session.get('email_data')

    if not email_data:
        return redirect(url_for('parts_list.parts_list'))

    list_id = email_data.get('list_id')
    list_header = None

    logging.info(f"Retrieved email data from session: list_id={email_data.get('list_id')}, mode={mode}")
    logging.info(f"Number of parts: {len(email_data.get('results', []))}")
    if email_data.get('results'):
        logging.info(f"Sample part data: {email_data['results'][0]}")

    with db_cursor() as cursor:
        if list_id:
            list_header = _execute_with_cursor(cursor, """
                SELECT
                    pl.name,
                    pl.status_id,
                    pl.notes,
                    pl.project_id,
                    c.name AS customer_name,
                    s.name AS status_name,
                    p.name AS project_name
                FROM parts_lists pl
                LEFT JOIN customers c ON c.id = pl.customer_id
                LEFT JOIN parts_list_statuses s ON s.id = pl.status_id
                LEFT JOIN projects p ON p.id = pl.project_id
                WHERE pl.id = ?
            """, (list_id,)).fetchone()

        # Branch based on mode
        request_cutoff = datetime.now() - timedelta(days=30)
        if mode == 'suggested':
            suppliers_map = process_suggested_suppliers(email_data, cursor, request_cutoff, list_id=list_id)
            page_title = 'Email Suggested Suppliers'
            days_back = None
        else:
            # ILS mode (existing logic)
            days_back = request.args.get('days', 7, type=int)
            cutoff_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
            suppliers_map = process_ils_suppliers(email_data, cursor, cutoff_date, request_cutoff, list_id=list_id)
            page_title = 'Email ILS Suppliers'

        # Fetch supplier contact details (common for both modes)
        for supplier_id, supplier_data in suppliers_map.items():
            supplier_info = _execute_with_cursor(cursor, '''
                SELECT contact_name, contact_email, warning
                FROM suppliers
                WHERE id = ?
            ''', (supplier_id,)).fetchone()

            if supplier_info:
                supplier_data['contact_name'] = supplier_info['contact_name']
                supplier_data['contact_email'] = supplier_info['contact_email']
                supplier_data['warning'] = supplier_info['warning']

        recent_no_bid_lookup = _get_recent_no_bid_lookup(
            cursor,
            suppliers_map.keys(),
            [part.get('base_part_number') for supplier in suppliers_map.values() for part in supplier['parts']],
            days=30,
        )

        for supplier_data in suppliers_map.values():
            supplier_id = supplier_data['supplier_id']
            for part in supplier_data['parts']:
                base_part_number = part.get('base_part_number')
                last_no_bid_date = recent_no_bid_lookup.get((supplier_id, base_part_number))
                part['recent_no_bid'] = bool(last_no_bid_date)
                part['recent_no_bid_date'] = last_no_bid_date

    # Convert to list and sort by number of parts
    suppliers_list = sorted(suppliers_map.values(),
                            key=lambda x: len(x['parts']),
                            reverse=True)

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Parts List Lookup', url_for('parts_list.parts_list')),
        (page_title, None)
    ]

    logging.info(f"Total suppliers found: {len(suppliers_list)}")
    for s in suppliers_list:
        logging.info(f"Supplier: {s['supplier_name']}, Parts: {len(s['parts'])}, Email: {s['contact_email']}")

    return render_template('parts_list_email_suppliers.html',
                           breadcrumbs=breadcrumbs,
                           suppliers=suppliers_list,
                           days_back=days_back,
                           total_parts=len(email_data['results']),
                           email_data=email_data,
                           mode=mode,
                           page_title=page_title,
                           list_id=list_id,
                           list_name=list_header['name'] if list_header else None,
                           list_notes=list_header['notes'] if list_header else None,
                           customer_name=list_header['customer_name'] if list_header else None,
                           project_id=list_header.get('project_id') if list_header else None,
                           project_name=list_header.get('project_name') if list_header else None,
                           status_id=list_header['status_id'] if list_header else None,
                           status_name=list_header['status_name'] if list_header else None)


@parts_list_bp.route('/ils-copy-queue', methods=['GET'])
def get_ils_copy_queue():
    """
    Return the shared ILS copy queue for anyone to consume.
    """
    try:
        with db_cursor() as cursor:
            rows = _execute_with_cursor(cursor, """
                SELECT
                    q.id,
                    q.parts_list_id,
                    q.chunk_type,
                    q.parts_json,
                    q.note,
                    q.created_at,
                    u.username,
                    pl.name AS parts_list_name
                FROM parts_list_ils_copy_queue q
                LEFT JOIN users u ON u.id = q.created_by_user_id
                LEFT JOIN parts_lists pl ON pl.id = q.parts_list_id
                ORDER BY q.created_at DESC
            """).fetchall()
    except Exception:
        logging.exception('Failed to load ILS copy queue')
        return jsonify(queue=[], error='Unable to load queue'), 500

    queue = []
    for row in rows or []:
        parts = []
        try:
            parts = json.loads(row['parts_json'] or '[]')
        except Exception:
            logging.warning('Unable to decode parts_json for queue entry %s', row['id'])
        created_at = row['created_at']
        if created_at and hasattr(created_at, 'isoformat'):
            created_at = created_at.isoformat()

        queue.append({
            'id': row['id'],
            'parts_list_id': row['parts_list_id'],
            'parts_list_name': _safe_row_get(row, 'parts_list_name'),
            'chunk_type': row['chunk_type'],
            'note': row['note'],
            'parts': parts,
            'created_at': created_at,
            'created_by_username': _safe_row_get(row, 'username')
        })

    return jsonify(queue=queue)


@parts_list_bp.route('/ils-copy-queue', methods=['POST'])
def add_to_ils_copy_queue():
    """
    Persist a chunk of part numbers into the shared ILS queue.
    """
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(success=False, error='Invalid request payload'), 400
    parts = data.get('parts')
    if not parts:
        return jsonify(success=False, error='parts array is required'), 400

    # Normalize incoming chunk data
    if isinstance(parts, str):
        parts = [parts]
    else:
        parts = list(parts)

    sanitized_parts = [str(p) for p in parts if p]
    if not sanitized_parts:
        return jsonify(success=False, error='No valid part numbers provided'), 400

    chunk_type = (data.get('chunk_type') or 'uncosted').lower()
    if chunk_type not in {'uncosted', 'costed'}:
        chunk_type = 'uncosted'

    payload = (
        data.get('parts_list_id'),
        chunk_type,
        json.dumps(sanitized_parts),
        data.get('note'),
        current_user.id if current_user.is_authenticated else session.get('user_id')
    )

    try:
        with db_cursor(commit=True) as cursor:
            inserted = _execute_with_cursor(cursor, """
                INSERT INTO parts_list_ils_copy_queue
                (parts_list_id, chunk_type, parts_json, note, created_by_user_id)
                VALUES (?, ?, ?, ?, ?)
                RETURNING id
            """, payload).fetchone()
    except Exception:
        logging.exception('Failed to add ILS chunk to queue')
        return jsonify(success=False, error='Unable to add chunk to queue'), 500

    entry_id = inserted['id'] if inserted else None
    return jsonify(success=True, entry_id=entry_id)


@parts_list_bp.route('/ils-copy-queue/<int:entry_id>', methods=['DELETE'])
def clear_ils_copy_queue(entry_id):
    """
    Remove a queued chunk once it has been dispatched or is no longer needed.
    """
    try:
        with db_cursor(commit=True) as cursor:
            result_cursor = _execute_with_cursor(cursor, """
                DELETE FROM parts_list_ils_copy_queue
                WHERE id = ?
            """, (entry_id,))
            if result_cursor.rowcount == 0:
                return jsonify(success=False, error='Queue entry not found'), 404
    except Exception:
        logging.exception('Failed to clear ILS queue entry')
        return jsonify(success=False, error='Unable to clear queue entry'), 500

    return jsonify(success=True)


def _format_date_display(value):
    if hasattr(value, 'date'):
        return value.date().isoformat()
    if isinstance(value, str):
        return value.split()[0]
    return None


def _get_recent_no_bid_lookup(cursor, supplier_ids, base_part_numbers, days=30):
    if not supplier_ids or not base_part_numbers:
        return {}

    unique_suppliers = sorted({int(supplier_id) for supplier_id in supplier_ids if supplier_id})
    unique_bases = sorted({base for base in base_part_numbers if base})
    if not unique_suppliers or not unique_bases:
        return {}

    supplier_placeholders = ','.join(['?'] * len(unique_suppliers))
    base_placeholders = ','.join(['?'] * len(unique_bases))
    cutoff_date = datetime.now() - timedelta(days=days)

    rows = _execute_with_cursor(cursor, f"""
        SELECT
            sq.supplier_id,
            pll.base_part_number,
            MAX(sql.date_created) AS last_no_bid_date
        FROM parts_list_supplier_quote_lines sql
        JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
        JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
        WHERE sql.is_no_bid = TRUE
          AND sql.date_created >= ?
          AND sq.supplier_id IN ({supplier_placeholders})
          AND pll.base_part_number IN ({base_placeholders})
        GROUP BY sq.supplier_id, pll.base_part_number
    """, [cutoff_date, *unique_suppliers, *unique_bases]).fetchall() or []

    lookup = {}
    for row in rows:
        last_no_bid_date = _safe_row_get(row, 'last_no_bid_date')
        display_date = _format_date_display(last_no_bid_date)
        lookup[(row['supplier_id'], row['base_part_number'])] = display_date

    return lookup


def process_ils_suppliers(email_data, cursor, cutoff_date, request_cutoff, list_id=None):
    """
    Process ILS supplier data (existing logic)
    """
    suppliers_map = {}
    recent_request_filter = ""
    recent_request_params = []
    if list_id:
        recent_request_filter = "AND pll.parts_list_id != ?"
        recent_request_params.append(list_id)

    logging.info(f"Total parts in email_data: {len(email_data['results'])}")
    parts_with_ils = sum(1 for p in email_data['results'] if p.get('ils_details'))
    logging.info(f"Parts with ILS details: {parts_with_ils}")

    for part in email_data['results']:
        if not part.get('ils_details'):
            logging.info(f"Skipping {part.get('input_part_number')} - no ILS details")
            continue

        for ils in part['ils_details']:
            logging.info(
                f"Processing {part.get('input_part_number')} - Supplier ID: {ils.get('supplier_id')}, Search Date: {ils.get('search_date')}, Cutoff: {cutoff_date}")

            # Skip if no supplier mapping
            if not ils.get('supplier_id'):
                logging.info(f"  -> Skipped: No supplier_id")
                continue

            # Skip if search is too old
            if ils.get('search_date'):
                search_date_only = ils['search_date'].split()[0]  # Get just the date part
                if search_date_only < cutoff_date:
                    continue

            supplier_id = ils['supplier_id']

            if supplier_id not in suppliers_map:
                suppliers_map[supplier_id] = {
                    'supplier_id': supplier_id,
                    'supplier_name': ils.get('supplier_name', 'Unknown'),
                    'contact_email': None,
                    'contact_name': None,
                    'warning': None,
                    'parts': []
                }

            # Add part if not already there
            part_exists = any(p['part_number'] == part['input_part_number']
                              for p in suppliers_map[supplier_id]['parts'])

            if part_exists:
                logging.info(f"  -> Skipped: Part already exists for this supplier")
                continue

            logging.info(f"  -> ADDING part {part['input_part_number']} to supplier {supplier_id}")

            line_id = part.get('line_id')

            part_data = {
                'part_number': part['input_part_number'],
                'base_part_number': part.get('base_part_number') or create_base_part_number(part['input_part_number']),
                'quantity': part['quantity'],
                'ils_quantity': ils.get('quantity', 'Unknown'),
                'condition': ils.get('condition_code', ''),
                'search_date': ils.get('search_date', ''),
                'line_id': line_id
            }

            # Query status flags including supplier-specific email tracking
            if line_id:
                status = _execute_with_cursor(cursor, f"""
                    SELECT
                        (chosen_cost IS NOT NULL) as has_chosen_cost,
                        (SELECT COUNT(*) FROM parts_list_supplier_quote_lines sql
                         JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                         WHERE sql.parts_list_line_id = line.id AND sql.is_no_bid = FALSE) as quote_count,
                        (SELECT COUNT(*) FROM parts_list_line_supplier_emails
                         WHERE parts_list_line_id = line.id) as email_count,
                        (SELECT COUNT(*) FROM parts_list_line_supplier_emails
                         WHERE parts_list_line_id = line.id AND supplier_id = ?) as sent_to_this_supplier,
                        (SELECT COUNT(*) FROM parts_list_supplier_quote_lines sql
                         JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                         WHERE sql.parts_list_line_id = line.id
                           AND sql.is_no_bid = FALSE
                           AND sq.supplier_id = ?) as supplier_quote_count,
                        (SELECT MAX(sql.date_created) FROM parts_list_supplier_quote_lines sql
                         JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                         WHERE sql.parts_list_line_id = line.id
                           AND sql.is_no_bid = FALSE
                           AND sq.supplier_id = ?) as supplier_last_quote_date,
                        (SELECT MAX(se.date_sent) FROM parts_list_line_supplier_emails se
                         JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                         WHERE pll.base_part_number = line.base_part_number
                           AND se.supplier_id = ?
                           AND se.date_sent >= ?
                           {recent_request_filter}) as recent_request_date
                    FROM
                        parts_list_lines line
                    WHERE
                        line.id = ?
                """, (
                    supplier_id,
                    supplier_id,
                    supplier_id,
                    supplier_id,
                    request_cutoff,
                    *recent_request_params,
                    line_id,
                )).fetchone()

                part_data.update({
                    'quote_count': status['quote_count'],
                    'has_quotes': bool(status['quote_count']),
                    'has_chosen_cost': bool(status['has_chosen_cost']),
                    'has_email_sent': bool(status['email_count']),
                    'sent_to_this_supplier': bool(status['sent_to_this_supplier']),
                    'supplier_quote_count': status['supplier_quote_count'],
                    'supplier_has_quote': bool(status['supplier_quote_count']),
                    'supplier_last_quote_date': _format_date_display(status['supplier_last_quote_date']),
                    'recent_request_date': _format_date_display(status['recent_request_date']),
                })
            else:
                part_data.update({
                    'quote_count': 0,
                    'has_quotes': False,
                    'has_chosen_cost': False,
                    'has_email_sent': False,
                    'sent_to_this_supplier': False,
                    'supplier_quote_count': 0,
                    'supplier_has_quote': False,
                    'supplier_last_quote_date': None,
                    'recent_request_date': None,
                })

            # Append part data to the supplier
            suppliers_map[supplier_id]['parts'].append(part_data)

    return suppliers_map


def process_suggested_suppliers(email_data, cursor, request_cutoff, list_id=None):
    """
    Process suggested suppliers from parts_list_line_suggested_suppliers
    """
    suppliers_map = {}
    recent_request_filter = ""
    recent_request_params = []
    if list_id:
        recent_request_filter = "AND pll.parts_list_id != ?"
        recent_request_params.append(list_id)

    logging.info(f"Total parts in email_data: {len(email_data['results'])}")

    for part in email_data['results']:
        line_id = part.get('line_id')

        if not line_id:
            logging.info(f"Skipping {part.get('input_part_number')} - no line_id")
            continue

        # Get suggested suppliers for this line
        suggested = _execute_with_cursor(cursor, """
            SELECT 
                ss.supplier_id,
                s.name as supplier_name,
                ss.source_type
            FROM parts_list_line_suggested_suppliers ss
            JOIN suppliers s ON s.id = ss.supplier_id
            WHERE ss.parts_list_line_id = ?
        """, (line_id,)).fetchall()

        logging.info(f"Part {part.get('input_part_number')} (line {line_id}) has {len(suggested)} suggested suppliers")

        for sugg in suggested:
            supplier_id = sugg['supplier_id']

            if supplier_id not in suppliers_map:
                suppliers_map[supplier_id] = {
                    'supplier_id': supplier_id,
                    'supplier_name': sugg['supplier_name'],
                    'contact_email': None,
                    'contact_name': None,
                    'warning': None,
                    'parts': []
                }

            # Check if part already exists for this supplier
            part_exists = any(p['part_number'] == part['input_part_number']
                              for p in suppliers_map[supplier_id]['parts'])

            if part_exists:
                continue

            # Get status flags
            status = _execute_with_cursor(cursor, f"""
                SELECT
                    (chosen_cost IS NOT NULL) as has_chosen_cost,
                (SELECT COUNT(*) FROM parts_list_supplier_quote_lines sql
                 JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                 WHERE sql.parts_list_line_id = line.id AND sql.is_no_bid = FALSE) as quote_count,
                (SELECT COUNT(*) FROM parts_list_line_supplier_emails
                 WHERE parts_list_line_id = line.id) as email_count,
                (SELECT COUNT(*) FROM parts_list_line_supplier_emails
                 WHERE parts_list_line_id = line.id AND supplier_id = ?) as sent_to_this_supplier,
                (SELECT COUNT(*) FROM parts_list_supplier_quote_lines sql
                 JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                 WHERE sql.parts_list_line_id = line.id
                   AND sql.is_no_bid = FALSE
                   AND sq.supplier_id = ?) as supplier_quote_count,
                (SELECT MAX(sql.date_created) FROM parts_list_supplier_quote_lines sql
                 JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                 WHERE sql.parts_list_line_id = line.id
                   AND sql.is_no_bid = FALSE
                   AND sq.supplier_id = ?) as supplier_last_quote_date,
                (SELECT MAX(se.date_sent) FROM parts_list_line_supplier_emails se
                 JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                 WHERE pll.base_part_number = line.base_part_number
                   AND se.supplier_id = ?
                   AND se.date_sent >= ?
                   {recent_request_filter}) as recent_request_date
            FROM
                parts_list_lines line
            WHERE
                line.id = ?
        """, (
            supplier_id,
            supplier_id,
            supplier_id,
            supplier_id,
            request_cutoff,
            *recent_request_params,
            line_id,
        )).fetchone()

            part_data = {
                'part_number': part['input_part_number'],
                'base_part_number': part.get('base_part_number') or create_base_part_number(part['input_part_number']),
                'quantity': part['quantity'],
                'ils_quantity': 'N/A',  # Not applicable for suggested suppliers
                'condition': '',
                'search_date': '',
                'line_id': line_id,
                'source_type': sugg['source_type'],
                'quote_count': status['quote_count'],
                'has_quotes': bool(status['quote_count']),
                'has_chosen_cost': bool(status['has_chosen_cost']),
                'has_email_sent': bool(status['email_count']),
                'sent_to_this_supplier': bool(status['sent_to_this_supplier']),
                'supplier_quote_count': status['supplier_quote_count'],
                'supplier_has_quote': bool(status['supplier_quote_count']),
                'supplier_last_quote_date': _format_date_display(status['supplier_last_quote_date']),
                'recent_request_date': _format_date_display(status['recent_request_date']),
            }

            suppliers_map[supplier_id]['parts'].append(part_data)
            logging.info(
                f"Added {part['input_part_number']} to supplier {supplier_id} ({sugg['supplier_name']}) - source: {sugg['source_type']}")

    logging.info(f"Total suppliers with parts: {len(suppliers_map)}")
    return suppliers_map


@parts_list_bp.route('/request-details/<int:supplier_id>/<base_part_number>', methods=['GET'])
def get_request_details(supplier_id, base_part_number):
    """
    Get details about a recent request to a supplier for a specific part,
    including any quote response received.
    """
    try:
        with db_cursor() as cursor:
            # Get the most recent request sent to this supplier for this part
            recent_request = _execute_with_cursor(cursor, """
                SELECT
                    se.id,
                    se.date_sent,
                    se.email_subject,
                    se.recipient_email,
                    pll.parts_list_id,
                    pl.name as list_name
                FROM parts_list_line_supplier_emails se
                JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                JOIN parts_lists pl ON pl.id = pll.parts_list_id
                WHERE pll.base_part_number = ?
                  AND se.supplier_id = ?
                ORDER BY se.date_sent DESC
                LIMIT 1
            """, (base_part_number, supplier_id)).fetchone()

            if not recent_request:
                return jsonify({'success': True, 'request': None, 'response': None})

            request_date = recent_request['date_sent']

            # Look for any quote response from this supplier for this part after the request
            quote_response = _execute_with_cursor(cursor, """
                SELECT
                    sql.unit_price,
                    sql.quantity_quoted,
                    sql.lead_time_days,
                    sql.condition_code,
                    sql.is_no_bid,
                    sql.line_notes,
                    sql.quoted_part_number,
                    sql.manufacturer,
                    sql.date_created as quote_date,
                    sq.quote_reference,
                    sq.currency_id,
                    c.currency_code
                FROM parts_list_supplier_quote_lines sql
                JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                LEFT JOIN currencies c ON c.id = sq.currency_id
                WHERE pll.base_part_number = ?
                  AND sq.supplier_id = ?
                  AND sql.date_created >= ?
                ORDER BY sql.date_created DESC
                LIMIT 1
            """, (base_part_number, supplier_id, request_date)).fetchone()

            # Format the response
            request_info = {
                'date_sent': _format_date_display(request_date),
                'email_subject': recent_request['email_subject'],
                'recipient_email': recent_request['recipient_email'],
                'list_name': recent_request['list_name'],
                'list_id': recent_request['parts_list_id']
            }

            response_info = None
            if quote_response:
                response_info = {
                    'is_no_bid': bool(quote_response['is_no_bid']),
                    'quote_date': _format_date_display(quote_response['quote_date']),
                    'unit_price': float(quote_response['unit_price']) if quote_response['unit_price'] else None,
                    'quantity_quoted': quote_response['quantity_quoted'],
                    'lead_time_days': quote_response['lead_time_days'],
                    'condition_code': quote_response['condition_code'],
                    'currency_code': quote_response['currency_code'] or 'GBP',
                    'quote_reference': quote_response['quote_reference'],
                    'quoted_part_number': quote_response['quoted_part_number'],
                    'manufacturer': quote_response['manufacturer'],
                    'notes': quote_response['line_notes']
                }

            return jsonify({
                'success': True,
                'request': request_info,
                'response': response_info
            })

    except Exception as e:
        logging.exception("Error fetching request details")
        return jsonify({'success': False, 'error': str(e)}), 500


@parts_list_bp.route('/generate-supplier-email', methods=['POST'])
def generate_supplier_email():
    """
    Generate email content for a specific supplier
    """
    try:
        data = request.get_json()

        supplier_id = data.get('supplier_id')
        supplier_name = data.get('supplier_name', 'Unknown')
        contact_name = data.get('contact_name', '')
        contact_email = data.get('contact_email', '')
        parts = data.get('parts', [])
        list_id = data.get('list_id')  # Get the parts list ID

        # Build parts table
        table_rows = ''
        for part in parts:
            table_rows += f'''
                <tr>
                    <td style="padding: 4px 8px; border: 1px solid #dee2e6;">{part['part_number']}</td>
                    <td style="padding: 4px 8px; border: 1px solid #dee2e6; text-align: center;">{part['quantity']}</td>
                </tr>
            '''

        # === Get current user's name for signature ===
        sender_name = "Purchasing Team"
        if current_user.is_authenticated and getattr(current_user, 'username', None):
            sender_name = current_user.username.replace('_', ' ').title()

        # Generate email content
        greeting = f"Hi {contact_name}" if contact_name else "Hello"

        # Build reference number: list_id-supplier_id (e.g., 1234-22)
        reference = f"{list_id}-{supplier_id}" if list_id and supplier_id else None

        # Include reference in subject if available
        if reference:
            subject = f"Parts Availability Request - {reference} - {datetime.now().strftime('%d/%m/%Y')}"
        else:
            subject = f"Parts Availability Request - {datetime.now().strftime('%d/%m/%Y')}"

        body_html = f'''<p>{greeting}</p>
<p>Please can you quote for the following:</p>
<table style="border-collapse: collapse; max-width: 500px;">
    <thead>
        <tr style="background-color: #f8f9fa;">
            <th style="padding: 4px 8px; border: 1px solid #dee2e6; text-align: left;">Part Number</th>
            <th style="padding: 4px 8px; border: 1px solid #dee2e6; text-align: center;">Quantity</th>
        </tr>
    </thead>
    <tbody>
        {table_rows}
    </tbody>
</table>
<p>Thanks,</p>
<p>{sender_name}</p>'''

        body_html_without_signature = body_html

        signature = None
        if current_user and getattr(current_user, "is_authenticated", False):
            signature = get_user_default_signature(current_user.id)
        else:
            signature = get_user_default_signature()

        if signature and signature.get('signature_html'):
            body_html += signature['signature_html']

        return jsonify({
            'success': True,
            'supplier_id': supplier_id,
            'subject': subject,
            'body_html': body_html,
            'body_html_without_signature': body_html_without_signature,
            'recipient_email': contact_email,
            'recipient_name': contact_name,
            'parts': parts
        })

    except Exception as e:
        logging.error(f'Error generating supplier email: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@parts_list_bp.route('/send-supplier-email', methods=['POST'])
def send_supplier_email():
    try:
        data = request.get_json(force=True)
        recipient_email = (data.get('recipient_email') or '').strip()
        subject = (data.get('subject') or '').strip()
        body_html = data.get('body_html') or ''

        if not (recipient_email and subject and body_html):
            return jsonify(success=False, error="recipient_email, subject, and body_html are required"), 400

        if not current_user or not getattr(current_user, "is_authenticated", False):
            return jsonify(success=False, error="You must be logged in to send emails"), 401

        attachments = build_graph_inline_attachments()
        result = send_graph_email(
            subject=subject,
            html_body=body_html,
            to_emails=[recipient_email],
            attachments=attachments,
            user_id=current_user.id,
        )

        if not result.get("success"):
            return jsonify(success=False, error=result.get("error", "Graph send failed")), 500

        return jsonify(success=True)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, error=str(e)), 500

# Updated routes for parts_list blueprint

@parts_list_bp.route('/table-view/<int:list_id>', methods=['GET'])
def table_view(list_id):
    """
    Display parts list in a Handsontable view for easier quantity editing
    Works with saved parts lists (list_id)
    """
    header = db_execute(
        """
        SELECT pl.*, c.name AS customer_name, s.name AS status_name, p.name AS project_name
        FROM parts_lists pl
        LEFT JOIN customers c ON c.id = pl.customer_id
        LEFT JOIN parts_list_statuses s ON s.id = pl.status_id
        LEFT JOIN projects p ON p.id = pl.project_id
        WHERE pl.id = ?
        """,
        (list_id,),
        fetch='one',
    )

    if not header:
        flash('Parts list not found', 'error')
        return redirect(url_for('parts_list.view_parts_lists'))

    # Get list lines WITH chosen supplier name
    lines = db_execute(
        """
        SELECT 
            pll.*,
            s.name as chosen_supplier_name
        FROM parts_list_lines pll
        LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
        WHERE pll.parts_list_id = ?
        ORDER BY pll.line_number ASC, pll.id ASC
        """,
        (list_id,),
        fetch='all',
    ) or []

    lines = [dict(line) for line in lines]

    # Bulk fetch QPL approvals for all base part numbers in this list
    base_part_numbers = sorted({line.get('base_part_number') for line in lines if line.get('base_part_number')})
    qpl_lookup = {}

    if base_part_numbers:
        placeholders = ','.join(['?'] * len(base_part_numbers))
        qpl_query = f"""
            SELECT
                COALESCE(NULLIF(ma.manufacturer_part_number_base, ''), ma.airbus_material_base) AS base_part_number,
                ma.manufacturer_name,
                ma.cage_code,
                ma.approval_status,
                ma.location
            FROM manufacturer_approvals ma
            WHERE ma.airbus_material_base IN ({placeholders})
               OR ma.manufacturer_part_number_base IN ({placeholders})
            ORDER BY base_part_number, manufacturer_name
        """

        with db_cursor() as cur:
            qpl_rows = _execute_with_cursor(cur, qpl_query, (*base_part_numbers, *base_part_numbers)).fetchall() or []

        for qpl in qpl_rows:
            base_key = _safe_row_get(qpl, 'base_part_number')
            if not base_key:
                continue

            entry = qpl_lookup.setdefault(base_key, {
                'approvals': [],
                'manufacturers': set(),
            })

            approval = {
                'manufacturer_name': _safe_row_get(qpl, 'manufacturer_name'),
                'cage_code': _safe_row_get(qpl, 'cage_code'),
                'approval_status': _safe_row_get(qpl, 'approval_status'),
                'location': _safe_row_get(qpl, 'location'),
            }

            entry['approvals'].append(approval)
            if approval['manufacturer_name']:
                entry['manufacturers'].add(approval['manufacturer_name'])

        for base_key, entry in qpl_lookup.items():
            entry['manufacturer_names'] = sorted(entry['manufacturers'])
            entry['approval_count'] = len(entry['approvals'])
            entry.pop('manufacturers', None)

    line_ids = [line.get('id') for line in lines if line.get('id')]
    no_bid_line_ids = sorted({line.get('parent_line_id') or line.get('id') for line in lines if line.get('id')})
    contacted_lookup = {}
    no_bid_lookup = {}

    if line_ids or no_bid_line_ids:
        with db_cursor() as cur:
            if line_ids:
                placeholders = ','.join(['?'] * len(line_ids))
                contacted_rows = _execute_with_cursor(cur, f"""
                    SELECT se.parts_list_line_id, s.name as supplier_name
                    FROM parts_list_line_supplier_emails se
                    JOIN suppliers s ON s.id = se.supplier_id
                    WHERE se.parts_list_line_id IN ({placeholders})
                    ORDER BY s.name
                """, line_ids).fetchall() or []

                for row in contacted_rows:
                    line_id = _safe_row_get(row, 'parts_list_line_id')
                    supplier_name = _safe_row_get(row, 'supplier_name')
                    if not (line_id and supplier_name):
                        continue
                    contacted_lookup.setdefault(line_id, set()).add(supplier_name)

            if no_bid_line_ids:
                placeholders = ','.join(['?'] * len(no_bid_line_ids))
                no_bid_rows = _execute_with_cursor(cur, f"""
                    SELECT sql.parts_list_line_id, s.name as supplier_name
                    FROM parts_list_supplier_quote_lines sql
                    JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                    JOIN suppliers s ON s.id = sq.supplier_id
                    WHERE sql.parts_list_line_id IN ({placeholders})
                      AND sql.is_no_bid = TRUE
                    ORDER BY s.name
                """, no_bid_line_ids).fetchall() or []

                for row in no_bid_rows:
                    line_id = _safe_row_get(row, 'parts_list_line_id')
                    supplier_name = _safe_row_get(row, 'supplier_name')
                    if not (line_id and supplier_name):
                        continue
                    no_bid_lookup.setdefault(line_id, set()).add(supplier_name)

    for line in lines:
        base_key = line.get('base_part_number')
        qpl_summary = qpl_lookup.get(base_key, {})
        line['qpl_approval_count'] = qpl_summary.get('approval_count', 0)
        line['qpl_manufacturers'] = qpl_summary.get('manufacturer_names', [])
        line['qpl_approvals'] = qpl_summary.get('approvals', [])
        approvals = line['qpl_approvals'] or []
        formatted_approvals = []
        for approval in approvals:
            name = (approval or {}).get('manufacturer_name')
            cage = (approval or {}).get('cage_code')
            status = (approval or {}).get('approval_status')
            location = (approval or {}).get('location')
            parts = [p for p in [name, f"CAGE {cage}" if cage else None, status, location] if p]
            if parts:
                formatted_approvals.append(" - ".join(parts))
        line['qpl_approvals_display'] = "; ".join(formatted_approvals)

        contacted_suppliers = sorted(contacted_lookup.get(line.get('id'), set()))
        no_bid_key = line.get('parent_line_id') or line.get('id')
        no_bid_suppliers = sorted(no_bid_lookup.get(no_bid_key, set()))
        line['contacted_suppliers'] = contacted_suppliers
        line['no_bid_suppliers'] = no_bid_suppliers
        line['contacted_suppliers_display'] = ", ".join(contacted_suppliers)
        line['no_bid_suppliers_display'] = ", ".join(no_bid_suppliers)

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Parts Lists', url_for('parts_list.view_parts_lists')),
        (header['name'], url_for('parts_list.view_parts_list', list_id=list_id)),
        ('Excel View', None)
    ]

    return render_template('parts_list_table.html',
                           breadcrumbs=breadcrumbs,
                           list_id=list_id,
                           list_name=header['name'],
                           list_notes=header.get('notes'),
                           customer_name=header['customer_name'],
                           project_id=header.get('project_id'),
                           project_name=header.get('project_name'),
                           status_id=header.get('status_id'),
                           status_name=header.get('status_name'),
                           lines=lines)


# Your existing update_line route is already perfect for this!
@parts_list_bp.route('/<int:list_id>/lines/<int:line_id>/update', methods=['POST'])
def update_line(list_id, line_id):
    """
    Update a single line.
    Body JSON may include: line_number, customer_part_number, base_part_number, quantity,
        chosen_supplier_id, chosen_cost, chosen_price, chosen_currency_id, chosen_lead_days,
        customer_notes, internal_notes
    If customer_part_number changes and base_part_number not provided, it will be recalculated.
    """
    try:
        data = request.get_json(force=True)

        # Build update set
        allowed = {
            'line_number', 'customer_part_number', 'base_part_number', 'quantity',
            'chosen_supplier_id', 'chosen_cost', 'chosen_price', 'chosen_currency_id', 'chosen_lead_days', 'chosen_qty',
            'customer_notes', 'internal_notes'
        }
        fields = []
        params = []

        # If CPN changes and no BPN provided, compute it
        if 'customer_part_number' in data and 'base_part_number' not in data:
            new_cpn = (data.get('customer_part_number') or '').strip()
            if new_cpn:
                data['base_part_number'] = create_base_part_number(new_cpn)

        for k, v in data.items():
            if k in allowed:
                fields.append(f"{k} = ?")
                params.append(v)

        if not fields:
            return jsonify(success=False, message="No fields to update"), 400

        with db_cursor(commit=True) as cur:
            # Ensure line belongs to the list
            owned = _execute_with_cursor(cur, """
                SELECT 1 FROM parts_list_lines WHERE id = ? AND parts_list_id = ?
            """, (line_id, list_id)).fetchone()
            if not owned:
                return jsonify(success=False, message="Line not found for this list"), 404

            _execute_with_cursor(cur, f"""
                UPDATE parts_list_lines
                SET {', '.join(fields)}, date_modified = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (*params, line_id))

            # If line_number changed, re-normalize ordering
            if 'line_number' in data:
                _renumber_lines(cur, list_id)

        return jsonify(success=True)
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500
# =========================
# Parts Lists – API Routes
# =========================

def _renumber_lines(cursor, parts_list_id):
    """Renumber parent lines 1..N and keep child lines as decimals (x.y)."""
    parents = _execute_with_cursor(cursor, """
        SELECT id
        FROM parts_list_lines
        WHERE parts_list_id = ?
          AND parent_line_id IS NULL
        ORDER BY line_number ASC, id ASC
    """, (parts_list_id,)).fetchall()

    for idx, parent in enumerate(parents, start=1):
        _execute_with_cursor(cursor, """
            UPDATE parts_list_lines
            SET line_number = ?, date_modified = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (idx, parent['id']))

        children = _execute_with_cursor(cursor, """
            SELECT id
            FROM parts_list_lines
            WHERE parts_list_id = ?
              AND parent_line_id = ?
            ORDER BY line_number ASC, id ASC
        """, (parts_list_id, parent['id'])).fetchall()

        parent_number = Decimal(str(idx))
        for child_idx, child in enumerate(children, start=1):
            child_number = (parent_number + (Decimal(child_idx) / Decimal('10'))).quantize(Decimal('0.1'))
            _execute_with_cursor(cursor, """
                UPDATE parts_list_lines
                SET line_number = ?, date_modified = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (child_number, child['id']))


@parts_list_bp.route('/parts-lists/statuses', methods=['GET'])
def parts_list_statuses():
    """Return list of statuses for dropdowns."""
    try:
        rows = db_execute(
            """
            SELECT id, name, display_order
            FROM parts_list_statuses
            ORDER BY display_order ASC, name ASC
            """,
            fetch='all',
        )
        return jsonify(success=True, statuses=[dict(r) for r in rows or []])
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/parts-lists/top-quotes', methods=['GET'])
def top_parts_list_quotes():
    """Return the highest quoted parts lists by total quoted value."""
    try:
        status_id = request.args.get('status_id', type=int)
        salesperson_id = request.args.get('salesperson_id', type=int)
        limit = request.args.get('limit', type=int) or 10
        limit = max(5, min(limit, 50))  # keep the result set reasonable

        where_clauses = []
        params = []

        if not salesperson_id and current_user.is_authenticated:
            user_salesperson = db_execute(
                'SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?',
                (current_user.id,),
                fetch='one',
            )
            if user_salesperson:
                salesperson_id = user_salesperson['legacy_salesperson_id']

        if status_id:
            where_clauses.append("pl.status_id = ?")
            params.append(status_id)

        if salesperson_id:
            where_clauses.append("pl.salesperson_id = ?")
            params.append(salesperson_id)

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        rows = db_execute(
            f"""
            SELECT
                pl.id,
                pl.name,
                pl.status_id,
                pls.name AS status_name,
                pl.date_modified,
                c.name AS customer_name,
                COUNT(DISTINCT pll.id) AS line_count,
                  COUNT(DISTINCT CASE 
                      WHEN cql.quoted_status = 'quoted' 
                           AND COALESCE(cql.is_no_bid::int, 0) = 0
                           AND cql.quote_price_gbp > 0 
                      THEN pll.id END) AS quoted_lines,
                  COALESCE(SUM(CASE 
                      WHEN cql.quoted_status = 'quoted' 
                           AND COALESCE(cql.is_no_bid::int, 0) = 0
                           AND cql.quote_price_gbp > 0
                      THEN cql.quote_price_gbp * COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity, 0)
                      ELSE 0 END), 0) AS quoted_value_gbp
            FROM parts_lists pl
            LEFT JOIN customers c ON c.id = pl.customer_id
            LEFT JOIN parts_list_statuses pls ON pls.id = pl.status_id
            LEFT JOIN parts_list_lines pll ON pll.parts_list_id = pl.id
            LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
            {where_sql}
            GROUP BY pl.id, pl.name, pl.status_id, pl.date_modified, c.name, pls.name
              HAVING COALESCE(SUM(CASE 
                  WHEN cql.quoted_status = 'quoted' 
                       AND COALESCE(cql.is_no_bid::int, 0) = 0
                       AND cql.quote_price_gbp > 0
                  THEN cql.quote_price_gbp * COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity, 0)
                  ELSE 0 END), 0) > 0
            ORDER BY quoted_value_gbp DESC, pl.date_modified DESC
            LIMIT ?
            """,
            (*params, limit),
            fetch='all',
        )

        return jsonify(success=True, results=[dict(r) for r in rows])

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/parts-lists', methods=['GET'])
def view_parts_lists():
    """
    List saved parts lists (headers only) with filtering and quote metrics.
    """
    try:
        # Get optional filters from query parameters
        raw_status_id = request.args.get('status_id')
        status_id = None
        if raw_status_id is not None:
            raw_status_id = raw_status_id.strip()
            if raw_status_id == '':
                status_id = 0
            else:
                try:
                    status_id = int(raw_status_id)
                except ValueError:
                    status_id = None

        customer_id = request.args.get('customer_id', type=int)
        salesperson_id_param = request.args.get('salesperson_id', type=int)
        salesperson_id = salesperson_id_param
        part_search = (request.args.get('q') or '').strip()
        quoted_date = (request.args.get('quoted_date') or '').strip()
        quoted_date_filter = None
        if quoted_date:
            try:
                quoted_date_filter = datetime.strptime(quoted_date, '%Y-%m-%d').date().isoformat()
            except ValueError:
                quoted_date_filter = None

        current_user_salesperson_id = None
        if current_user.is_authenticated:
            user_salesperson = db_execute(
                'SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?',
                (current_user.id,),
                fetch='one',
            )
            if user_salesperson:
                current_user_salesperson_id = user_salesperson['legacy_salesperson_id']

        # Default to current user's salesperson if no explicit filter
        if not salesperson_id and current_user_salesperson_id:
            salesperson_id = current_user_salesperson_id

        has_explicit_filters = any([
            request.args.get('customer_id'),
            request.args.get('salesperson_id'),
            request.args.get('quoted_date'),
            request.args.get('q'),
        ])
        if raw_status_id is None and not has_explicit_filters:
            status_id = 1

        # Get all salespeople with their parts list counts
        all_salespeople = db_execute(
            """
            SELECT s.id, 
                   s.name, 
                   COUNT(DISTINCT pl.id) as parts_list_count
            FROM salespeople s
            LEFT JOIN parts_lists pl ON s.id = pl.salesperson_id
            GROUP BY s.id, s.name
            ORDER BY s.name
            """,
            fetch='all',
        )

        # Get current salesperson if filtered
        current_salesperson = None
        if salesperson_id:
            current_salesperson = db_execute(
                "SELECT id, name FROM salespeople WHERE id = ?",
                (salesperson_id,),
                fetch='one',
            )
        elif current_user_salesperson_id:
            # If user is a salesperson but no filter selected, get their info
            current_salesperson = db_execute(
                "SELECT id, name FROM salespeople WHERE id = ?",
                (current_user_salesperson_id,),
                fetch='one',
            )

        lists_data = []
        if not part_search:
            # Build SQL query with optional filters and preview
            sql = """
                SELECT pl.id,
                       pl.name,
                       pl.date_created,
                       pl.date_modified,
                       pl.status_id,
                       pl.customer_id,
                       pl.contact_id,
                       pl.salesperson_id,
                       pl.project_id,
                       COALESCE(c.name, '') AS customer_name,
                     COALESCE(ct.name, '') AS contact_name,
                     pls.name AS status_name,
                     p.name AS project_name,
                     (SELECT COUNT(*) FROM parts_list_lines pll WHERE pll.parts_list_id = pl.id) AS line_count,
                     (SELECT COUNT(*)
                     FROM parts_list_lines pll
                     WHERE pll.parts_list_id = pl.id
                       AND pll.chosen_cost IS NOT NULL) AS costed_line_count,
                     (SELECT COUNT(DISTINCT pll.id)
                      FROM parts_list_lines pll
                      LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                      WHERE pll.parts_list_id = pl.id
                        AND cql.quoted_status = 'quoted') AS quoted_line_count
                    ,(SELECT COUNT(DISTINCT pll.id)
                      FROM parts_list_lines pll
                      WHERE pll.parts_list_id = pl.id
                        AND EXISTS (
                            SELECT 1
                            FROM manufacturer_approvals ma
                            WHERE ma.airbus_material_base = pll.base_part_number
                               OR ma.manufacturer_part_number_base = pll.base_part_number
                        )) AS qpl_line_count
                    ,(SELECT COALESCE(SUM(CASE
                        WHEN cql.quoted_status = 'quoted'
                             AND COALESCE(cql.is_no_bid::int, 0) = 0
                             AND cql.quote_price_gbp > 0
                        THEN cql.quote_price_gbp * COALESCE(NULLIF(pll.chosen_qty, 0), pll.quantity, 0)
                        ELSE 0 END), 0)
                      FROM parts_list_lines pll
                      LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                      WHERE pll.parts_list_id = pl.id) AS quoted_value_gbp
                FROM parts_lists pl
                LEFT JOIN customers c ON c.id = pl.customer_id
                LEFT JOIN contacts ct ON ct.id = pl.contact_id
                LEFT JOIN parts_list_statuses pls ON pls.id = pl.status_id
                LEFT JOIN projects p ON p.id = pl.project_id
            """

            where_clauses = []
            params = []

            if status_id:
                where_clauses.append("pl.status_id = ?")
                params.append(status_id)

            if customer_id:
                where_clauses.append("pl.customer_id = ?")
                params.append(customer_id)

            if salesperson_id:
                where_clauses.append("pl.salesperson_id = ?")
                params.append(salesperson_id)

            if quoted_date_filter:
                where_clauses.append("""
                    EXISTS (
                        SELECT 1
                        FROM parts_list_lines pll2
                        JOIN customer_quote_lines cql2 ON cql2.parts_list_line_id = pll2.id
                        WHERE pll2.parts_list_id = pl.id
                          AND cql2.quoted_status = 'quoted'
                          AND cql2.quoted_on IS NOT NULL
                          AND DATE(cql2.quoted_on) = ?
                    )
                """)
                params.append(quoted_date_filter)

            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)

            sql += " ORDER BY pl.date_modified DESC, pl.date_created DESC"

            rows = db_execute(sql, params, fetch='all')

            lists_data = [dict(r) for r in rows] if rows else []
            preview_map = {}
            if lists_data:
                parts_list_ids = [row['id'] for row in lists_data]
                placeholders = ','.join('?' for _ in parts_list_ids)
                line_rows = db_execute(
                    f"""
                    SELECT parts_list_id, customer_part_number, base_part_number
                    FROM parts_list_lines
                    WHERE parts_list_id IN ({placeholders})
                    ORDER BY parts_list_id, line_number ASC, id ASC
                    """,
                    tuple(parts_list_ids),
                    fetch='all',
                )
                for line in line_rows or []:
                    preview_lines = preview_map.setdefault(line['parts_list_id'], [])
                    if len(preview_lines) < 5:
                        preview_lines.append(line['customer_part_number'] or line['base_part_number'])

                for list_dict in lists_data:
                    preview_lines = preview_map.get(list_dict['id'], [])
                    list_dict['preview_parts'] = ', '.join(filter(None, preview_lines)) if preview_lines else ''

        selected_customer_name = None
        if customer_id:
            customer_row = db_execute(
                "SELECT name FROM customers WHERE id = ?",
                (customer_id,),
                fetch='one',
            )
            if customer_row:
                selected_customer_name = customer_row['name']

        statuses = db_execute("SELECT id, name FROM parts_list_statuses ORDER BY display_order ASC", fetch='all')
        projects = db_execute("SELECT id, name FROM projects ORDER BY name", fetch='all')

        return render_template('parts_lists.html',
                               lists=lists_data,
                               statuses=[dict(s) for s in statuses],
                               projects=[dict(p) for p in projects] if projects else [],
                               all_salespeople=[dict(sp) for sp in all_salespeople],
                               current_salesperson=dict(current_salesperson) if current_salesperson else None,
                               current_user_salesperson_id=current_user_salesperson_id,
                               selected_status_id=status_id,
                               selected_customer_id=customer_id,
                               selected_customer_name=selected_customer_name,
                               selected_salesperson_id=salesperson_id,
                               explicit_salesperson_id=salesperson_id_param,
                               selected_quoted_date=quoted_date_filter,
                               initial_part_search=part_search)
    except Exception as e:
        logging.exception(e)
        # Fallback to the original simple rendering if there's an error
        return render_template('parts_lists.html',
                               lists=[],
                               projects=[],
                               all_salespeople=[],
                               current_salesperson=None,
                               current_user_salesperson_id=None,
                               selected_quoted_date=None,
                               initial_part_search=part_search)

@parts_list_bp.route('/parts-lists/<int:list_id>/update', methods=['POST'])
def update_parts_list_header(list_id):
    try:
        data = request.get_json(force=True)
        fields = []
        params = []

        for key in ('name', 'customer_id', 'contact_id', 'salesperson_id', 'status_id', 'notes', 'project_id'):
            if key in data:
                fields.append(f"{key} = ?")
                params.append(data.get(key))

        if not fields:
            return jsonify(success=False, message="No fields to update"), 400

        exists = db_execute("SELECT 1 FROM parts_lists WHERE id = ?", (list_id,), fetch='one')
        if not exists:
            return jsonify(success=False, message="Parts list not found"), 404

        db_execute(
            f"""
            UPDATE parts_lists
            SET {', '.join(fields)}, date_modified = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*params, list_id),
            commit=True,
        )
        return jsonify(success=True)
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/delete', methods=['POST'])
def delete_parts_list(list_id):
    """Delete a parts list and all related data."""
    try:
        with db_cursor(commit=True) as cur:
            exists = _execute_with_cursor(cur, "SELECT 1 FROM parts_lists WHERE id = ?", (list_id,)).fetchone()
            if not exists:
                return jsonify(success=False, message="Parts list not found"), 404

            # Get all line IDs for this parts list
            line_ids = _execute_with_cursor(
                cur,
                "SELECT id FROM parts_list_lines WHERE parts_list_id = ?",
                (list_id,)
            ).fetchall()
            line_id_list = [row['id'] for row in line_ids]

            # Delete monroe_search_results that reference these lines (no cascade on FK)
            if line_id_list:
                placeholders = ','.join('?' * len(line_id_list))
                _execute_with_cursor(
                    cur,
                    f"DELETE FROM monroe_search_results WHERE parts_list_line_id IN ({placeholders})",
                    line_id_list
                )

            # Delete the parts list (cascades to lines and other related tables)
            _execute_with_cursor(cur, "DELETE FROM parts_lists WHERE id = ?", (list_id,))

        return jsonify(success=True)
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/duplicate', methods=['POST'])
def duplicate_parts_list(list_id):
    """Duplicate a parts list header + lines."""
    try:
        with db_cursor(commit=True) as cur:
            header = _execute_with_cursor(cur, "SELECT * FROM parts_lists WHERE id = ?", (list_id,)).fetchone()
            if not header:
                return jsonify(success=False, message="Parts list not found"), 404

            new_name = f"{header['name']} (Copy)"
            new_row = _execute_with_cursor(cur, """
                INSERT INTO parts_lists (name, customer_id, salesperson_id, status_id, notes)
                VALUES (?, ?, ?, ?, ?)
                RETURNING id
            """, (new_name, header['customer_id'], header['salesperson_id'], header['status_id'], header['notes'])).fetchone()
            new_id = new_row['id'] if new_row else getattr(cur, 'lastrowid', None)

            # Copy lines (preserve parent/child links)
            lines = _execute_with_cursor(cur, """
                SELECT id, line_number, customer_part_number, base_part_number, description, quantity,
                       chosen_supplier_id, chosen_cost, chosen_price, chosen_currency_id, chosen_lead_days,
                       customer_notes, internal_notes, parent_line_id, line_type
                FROM parts_list_lines
                WHERE parts_list_id = ?
                ORDER BY line_number ASC, id ASC
            """, (list_id,)).fetchall()

            line_id_map = {}
            pending_parent_links = []

            for ln in lines:
                new_line_row = _execute_with_cursor(cur, """
                    INSERT INTO parts_list_lines
                    (parts_list_id, line_number, customer_part_number, base_part_number, description, quantity,
                     chosen_supplier_id, chosen_cost, chosen_price, chosen_currency_id, chosen_lead_days,
                     customer_notes, internal_notes, parent_line_id, line_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    RETURNING id
                """, (
                    new_id, ln['line_number'], ln['customer_part_number'], ln['base_part_number'], ln['description'],
                    ln['quantity'],
                    ln['chosen_supplier_id'], ln['chosen_cost'], ln['chosen_price'], ln['chosen_currency_id'],
                    ln['chosen_lead_days'], ln['customer_notes'], ln['internal_notes'],
                    ln['line_type'] or 'normal'
                )).fetchone()

                created_id = new_line_row['id'] if new_line_row else getattr(cur, 'lastrowid', None)
                line_id_map[ln['id']] = created_id
                if ln['parent_line_id']:
                    pending_parent_links.append((created_id, ln['parent_line_id']))

            for new_line_id, old_parent_id in pending_parent_links:
                new_parent_id = line_id_map.get(old_parent_id)
                if new_parent_id:
                    _execute_with_cursor(cur, """
                        UPDATE parts_list_lines
                        SET parent_line_id = ?
                        WHERE id = ?
                    """, (new_parent_id, new_line_id))

        return jsonify(success=True, id=new_id)
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/add', methods=['POST'])
def add_lines(list_id):
    """
    Add one or many lines.
    Body JSON:
      lines: [
        { "customer_part_number": "...", "quantity": 2, "base_part_number": "..."?,
          "parent_line_id": 123?, "parent_line_number": 1?, "line_type": "price_break|alternate|normal"? }
      ]
    If base_part_number absent, it will be created via create_base_part_number().
    If parent_line_number is provided, the new line gets parent_line_number + 0.1, 0.2, etc.
    """
    try:
        data = request.get_json(force=True)
        lines = data.get('lines', [])
        if not lines:
            return jsonify(success=False, message="lines is required"), 400

        created_line_ids = []

        with db_cursor(commit=True) as cur:
            # Confirm list exists
            exists = _execute_with_cursor(cur, "SELECT 1 FROM parts_lists WHERE id = ?", (list_id,)).fetchone()
            if not exists:
                return jsonify(success=False, message="Parts list not found"), 404

            for item in lines:
                cpn = (item.get('customer_part_number') or '').strip()
                if not cpn:
                    continue
                qty = int(item.get('quantity') or 1)
                bpn = item.get('base_part_number')
                if not bpn:
                    bpn = create_base_part_number(cpn)

                parent_line_id = item.get('parent_line_id')
                parent_line_number = item.get('parent_line_number')
                default_line_type = 'price_break' if (parent_line_id or parent_line_number) else 'normal'
                line_type = _normalize_line_type(item.get('line_type'), default=default_line_type)

                if line_type is None:
                    return jsonify(success=False, message="Invalid line_type"), 400

                if parent_line_id:
                    parent_row = _execute_with_cursor(cur, """
                        SELECT id, line_number
                        FROM parts_list_lines
                        WHERE id = ? AND parts_list_id = ?
                    """, (parent_line_id, list_id)).fetchone()
                    if not parent_row:
                        return jsonify(success=False, message="Parent line not found"), 404
                    parent_line_number = parent_row['line_number']
                    next_line_number = _next_child_line_number(cur, list_id, parent_line_id, parent_line_number)
                elif parent_line_number:
                    parent_row = _execute_with_cursor(cur, """
                        SELECT id, line_number
                        FROM parts_list_lines
                        WHERE parts_list_id = ?
                          AND line_number = ?
                          AND parent_line_id IS NULL
                        ORDER BY id ASC
                        LIMIT 1
                    """, (list_id, parent_line_number)).fetchone()
                    if parent_row:
                        parent_line_id = parent_row['id']
                        parent_line_number = parent_row['line_number']
                        next_line_number = _next_child_line_number(cur, list_id, parent_line_id, parent_line_number)
                    else:
                        next_line_number = Decimal(str(parent_line_number)) + Decimal('0.1')
                else:
                    # Regular line - get next whole number
                    last = _execute_with_cursor(cur, """
                        SELECT COALESCE(MAX(CAST(line_number AS INTEGER)), 0) AS max_ln
                        FROM parts_list_lines
                        WHERE parts_list_id = ?
                    """, (list_id,)).fetchone()['max_ln']
                    next_line_number = int(last) + 1

                description = (item.get('description') or '').strip() or None
                row = _execute_with_cursor(cur, """
                    INSERT INTO parts_list_lines
                    (parts_list_id, line_number, customer_part_number, base_part_number, description, quantity,
                     parent_line_id, line_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                """, (list_id, next_line_number, cpn, bpn, description, qty, parent_line_id, line_type)).fetchone()

                if row:
                    created_line_ids.append(row['id'] if isinstance(row, dict) else row[0])

        # Trigger Monroe auto-check in background if enabled
        if created_line_ids:
            user_id = current_user.id if current_user.is_authenticated else session.get('user_id')
            trigger_monroe_auto_check(list_id, created_line_ids, user_id=user_id)

        return jsonify(success=True)
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/parts-lists/<int:list_id>/supplier-quotes/<int:quote_id>/lines/delete', methods=['POST'])
def delete_supplier_quote_lines(list_id, quote_id):
    """
    Delete one or more supplier quote lines for a quote
    """
    try:
        data = request.get_json(force=True) or {}
        raw_line_ids = data.get('line_ids') or []
        if not raw_line_ids:
            return jsonify(success=False, message="line_ids are required"), 400

        line_ids = []
        for line_id in raw_line_ids:
            try:
                line_ids.append(int(line_id))
            except (TypeError, ValueError):
                continue

        if not line_ids:
            return jsonify(success=False, message="line_ids are required"), 400

        with db_cursor(commit=True) as cur:
            exists = _execute_with_cursor(
                cur,
                "SELECT 1 FROM parts_list_supplier_quotes WHERE id = ? AND parts_list_id = ?",
                (quote_id, list_id),
            ).fetchone()
            if not exists:
                return jsonify(success=False, message="Quote not found"), 404

            placeholders = ",".join(["?"] * len(line_ids))
            _execute_with_cursor(
                cur,
                f"""
                DELETE FROM parts_list_supplier_quote_lines
                WHERE supplier_quote_id = ?
                  AND id IN ({placeholders})
                """,
                [quote_id, *line_ids],
            )

            deleted_count = cur.rowcount

        return jsonify(success=True, deleted_count=deleted_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/duplicate', methods=['POST'])
def duplicate_line(list_id, line_id):
    """
    Duplicate a line as a child line (price break or alternate).
    Defaults to line_type = price_break.
    """
    try:
        data = request.get_json(silent=True) or {}
        line_type = _normalize_line_type(data.get('line_type'), default='price_break')
        if line_type is None:
            return jsonify(success=False, message="Invalid line_type"), 400

        with db_cursor(commit=True) as cur:
            line = _execute_with_cursor(cur, """
                SELECT *
                FROM parts_list_lines
                WHERE id = ? AND parts_list_id = ?
            """, (line_id, list_id)).fetchone()
            if not line:
                return jsonify(success=False, message="Line not found for this list"), 404

            parent_line_id = line['parent_line_id'] or line['id']
            parent_row = _execute_with_cursor(cur, """
                SELECT line_number
                FROM parts_list_lines
                WHERE id = ? AND parts_list_id = ?
            """, (parent_line_id, list_id)).fetchone()
            if not parent_row:
                return jsonify(success=False, message="Parent line not found"), 404

            next_line_number = _next_child_line_number(cur, list_id, parent_line_id, parent_row['line_number'])

            new_row = _execute_with_cursor(cur, """
                INSERT INTO parts_list_lines
                (parts_list_id, line_number, customer_part_number, base_part_number, description, quantity,
                 parent_line_id, line_type, customer_notes, internal_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (
                list_id,
                next_line_number,
                line['customer_part_number'],
                line['base_part_number'],
                line['description'],
                line['quantity'],
                parent_line_id,
                line_type,
                line['customer_notes'],
                line['internal_notes']
            )).fetchone()

            new_id = new_row['id'] if new_row else getattr(cur, 'lastrowid', None)

        return jsonify(success=True, line_id=new_id, line_number=str(next_line_number), parent_line_id=parent_line_id, line_type=line_type)
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/delete', methods=['POST'])
def delete_line(list_id, line_id):
    """Delete a line and renumber remaining lines."""
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, """
                DELETE FROM parts_list_lines
                WHERE id = ? AND parts_list_id = ?
            """, (line_id, list_id))
            if cur.rowcount == 0:
                return jsonify(success=False, message="Line not found for this list"), 404

            _renumber_lines(cur, list_id)

        return jsonify(success=True)
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/reorder', methods=['POST'])
def reorder_lines(list_id):
    """
    Reorder lines.
    Body JSON:
      - order: [line_id1, line_id2, ...]  => will set line_number 1..N in this exact order
    """
    try:
        data = request.get_json(force=True)
        order = data.get('order', [])
        if not order or not isinstance(order, list):
            return jsonify(success=False, message="order must be a non-empty array"), 400

        with db_cursor(commit=True) as cur:
            # Validate ownership and apply positions
            for idx, lid in enumerate(order, start=1):
                _execute_with_cursor(cur, """
                    UPDATE parts_list_lines
                    SET line_number = ?, date_modified = CURRENT_TIMESTAMP
                    WHERE id = ? AND parts_list_id = ?
                """, (idx, lid, list_id))

            # Final normalization in case some lines were not in list
            _renumber_lines(cur, list_id)

        return jsonify(success=True)
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/replace', methods=['POST'])
def replace_all_lines(list_id):
    """
    Replace all existing lines with a new set (useful after AI extraction).
    Body JSON:
      - lines: [{customer_part_number, quantity, base_part_number?}, ...]
    """
    try:
        data = request.get_json(force=True)
        lines = data.get('lines', [])
        if not isinstance(lines, list):
            return jsonify(success=False, message="lines must be an array"), 400

        with db_cursor(commit=True) as cur:
            # Ensure list exists
            exists = _execute_with_cursor(cur, "SELECT 1 FROM parts_lists WHERE id = ?", (list_id,)).fetchone()
            if not exists:
                return jsonify(success=False, message="Parts list not found"), 404

            # Delete old
            _execute_with_cursor(cur, "DELETE FROM parts_list_lines WHERE parts_list_id = ?", (list_id,))

            # Insert new
            ln = 1
            for item in lines:
                cpn = (item.get('customer_part_number') or '').strip()
                if not cpn:
                    continue
                qty = int(item.get('quantity') or 1)
                bpn = item.get('base_part_number') or create_base_part_number(cpn)
                description = (item.get('description') or '').strip() or None
                _execute_with_cursor(cur, """
                    INSERT INTO parts_list_lines
                    (parts_list_id, line_number, customer_part_number, base_part_number, description, quantity)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (list_id, ln, cpn, bpn, description, qty))
                ln += 1

        return jsonify(success=True, count=ln - 1)
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/save', methods=['POST'])
def save_parts_list():
    try:
        payload = request.get_json() or {}
        name = (payload.get('name') or '').strip()
        if not name:
            return jsonify(success=False, message="List name is required"), 400

        customer_id = payload.get('customer_id')
        contact_id = payload.get('contact_id')  # ← NEW
        notes = payload.get('notes') or ''
        lines = payload.get('lines') or []
        if not isinstance(lines, list) or len(lines) == 0:
            return jsonify(success=False, message="No lines to save"), 400

        salesperson_id = 1
        if current_user.is_authenticated:
            logging.info(f"Looking up salesperson for user_id: {current_user.id}")
            sp = db_execute(
                "SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?",
                (current_user.id,),
                fetch='one',
            )
            logging.info(f"Lookup result: {sp}")
            if sp and sp['legacy_salesperson_id']:
                salesperson_id = sp['legacy_salesperson_id']
                logging.info(f"Using salesperson_id: {salesperson_id}")
            else:
                logging.warning(f"No legacy_salesperson_id found for user {session.get('user_id')}, defaulting to 1")
        else:
            logging.warning("No user_id in session, defaulting to salesperson_id = 1")

        with db_cursor(commit=True) as cur:
            # Insert header with contact_id
            header_row = _execute_with_cursor(cur, """
                INSERT INTO parts_lists
                    (name, customer_id, contact_id, salesperson_id, status_id, notes,
                    date_created, date_modified)
                VALUES (?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
            """, (name, customer_id, contact_id, salesperson_id, notes)).fetchone()

            parts_list_id = header_row['id'] if header_row else getattr(cur, 'lastrowid', None)

            # Insert lines and capture IDs for Monroe auto-check
            created_line_ids = []
            insert_sql = """
                INSERT INTO parts_list_lines (
                    parts_list_id, line_number, customer_part_number, base_part_number, description, quantity,
                    chosen_supplier_id, chosen_cost, chosen_price, chosen_currency_id, chosen_lead_days, chosen_qty,
                    customer_notes, internal_notes, date_created, date_modified
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
            """
            for line in lines:
                line_number = int(line.get('line_number') or 0) or 1
                customer_part_number = (line.get('customer_part_number') or '').strip()
                if not customer_part_number:
                    # skip totally empty rows
                    continue
                base_part_number = (line.get('base_part_number') or None)
                # If base_part_number missing, derive it (keeps your normalizer logic in one place)
                if not base_part_number:
                    base_part_number = create_base_part_number(customer_part_number)

                quantity = int(line.get('quantity') or 1)
                description = (line.get('description') or '').strip() or None

                # Prepopulate chosen_qty with quantity if chosen_qty is not provided
                chosen_qty = line.get('chosen_qty')
                if chosen_qty is None or chosen_qty == '':
                    chosen_qty = quantity

                row = _execute_with_cursor(cur, insert_sql, (
                    parts_list_id, line_number, customer_part_number, base_part_number, description, quantity, chosen_qty
                )).fetchone()

                if row:
                    created_line_ids.append(row['id'] if isinstance(row, dict) else row[0])

        # Transaction commits when exiting the 'with db_cursor' block above

        _log_parts_list_creation_communication(
            parts_list_id,
            name,
            customer_id,
            contact_id,
            salesperson_id,
        )

        # Trigger Monroe auto-check AFTER transaction commits
        # This ensures the background thread can see the committed data
        if created_line_ids:
            user_id = current_user.id if current_user.is_authenticated else session.get('user_id')
            trigger_monroe_auto_check(parts_list_id, created_line_ids, user_id=user_id)

        return jsonify(
            success=True,
            message="Parts list saved",
            parts_list_id=parts_list_id,
            redirect=url_for('parts_list.parts_list', list_id=parts_list_id)
        )

    except Exception as e:
        logging.exception("Error saving parts list")
        return jsonify(success=False, message=str(e)), 500



@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/suggested-suppliers/add', methods=['POST'])
def add_suggested_supplier(list_id, line_id):
    """
    Add a suggested supplier to a line
    UPDATED: Now returns supplier_name and suggested_id for live UI updates
    """
    try:
        data = request.get_json()
        supplier_id = data.get('supplier_id')
        source_type = data.get('source_type', 'manual')

        if not supplier_id:
            return jsonify(success=False, message='Supplier ID required'), 400

        # Check if already exists
        existing = db_execute(
            """
            SELECT id FROM parts_list_line_suggested_suppliers
            WHERE parts_list_line_id = ? AND supplier_id = ?
            """,
            (line_id, supplier_id),
            fetch='one',
        )

        if existing:
            supplier = db_execute(
                """
                SELECT name FROM suppliers WHERE id = ?
                """,
                (supplier_id,),
                fetch='one',
            )
            return jsonify(
                success=True,
                suggested_id=existing['id'],
                supplier_name=supplier['name'] if supplier else None,
                message='Supplier already in suggested list',
            )

        # Get supplier name
        supplier = db_execute(
            """
            SELECT name FROM suppliers WHERE id = ?
            """,
            (supplier_id,),
            fetch='one',
        )

        if not supplier:
            return jsonify(success=False, message='Supplier not found'), 404

        # Insert
        row = db_execute(
            """
            INSERT INTO parts_list_line_suggested_suppliers
                (parts_list_line_id, supplier_id, source_type, date_added)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (line_id, supplier_id, source_type),
            fetch='one',
            commit=True,
        )

        suggested_id = row['id'] if row else None

        # Return supplier name and suggested_id for live UI update
        return jsonify(
            success=True,
            suggested_id=suggested_id,
            supplier_name=supplier['name']
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/suggested-suppliers/bulk-add', methods=['POST'])
def bulk_add_suggested_suppliers(list_id):
    """
    Bulk add suggested suppliers to multiple lines.
    Body JSON: { entries: [{ line_id, supplier_id, source_type }] }
    """
    try:
        data = request.get_json() or {}
        entries = data.get('entries') or []

        if not isinstance(entries, list) or not entries:
            return jsonify(success=False, message='Entries required'), 400

        sanitized = []
        line_ids = []
        for entry in entries:
            line_id = entry.get('line_id')
            supplier_id = entry.get('supplier_id')
            source_type = entry.get('source_type') or 'bulk'

            if not line_id or not supplier_id:
                continue

            sanitized.append({
                'line_id': int(line_id),
                'supplier_id': int(supplier_id),
                'source_type': source_type,
            })
            line_ids.append(int(line_id))

        if not sanitized:
            return jsonify(success=False, message='No valid entries'), 400

        unique_line_ids = sorted(set(line_ids))
        line_placeholders = ','.join(['?'] * len(unique_line_ids))

        inserted = []
        existing_count = 0
        skipped_count = 0

        with db_cursor(commit=True) as cur:
            valid_lines = _execute_with_cursor(
                cur,
                f"""
                SELECT id
                FROM parts_list_lines
                WHERE parts_list_id = ?
                  AND id IN ({line_placeholders})
                """,
                (list_id, *unique_line_ids),
            ).fetchall()

            valid_line_ids = {row['id'] for row in valid_lines}

            for entry in sanitized:
                if entry['line_id'] not in valid_line_ids:
                    skipped_count += 1
                    continue

                row = _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO parts_list_line_suggested_suppliers
                        (parts_list_line_id, supplier_id, source_type, date_added)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(parts_list_line_id, supplier_id) DO NOTHING
                    RETURNING id
                    """,
                    (entry['line_id'], entry['supplier_id'], entry['source_type']),
                ).fetchone()

                if row:
                    inserted.append({
                        'line_id': entry['line_id'],
                        'supplier_id': entry['supplier_id'],
                        'source_type': entry['source_type'],
                        'suggested_id': row['id'],
                    })
                else:
                    existing_count += 1

            supplier_name_map = {}
            if inserted:
                supplier_ids = sorted({item['supplier_id'] for item in inserted})
                supplier_placeholders = ','.join(['?'] * len(supplier_ids))
                supplier_rows = _execute_with_cursor(
                    cur,
                    f"""
                    SELECT id, name
                    FROM suppliers
                    WHERE id IN ({supplier_placeholders})
                    """,
                    supplier_ids,
                ).fetchall()
                supplier_name_map = {row['id']: row['name'] for row in supplier_rows}

        response_items = [
            {
                **item,
                'supplier_name': supplier_name_map.get(item['supplier_id']),
            }
            for item in inserted
        ]

        return jsonify(
            success=True,
            added_count=len(inserted),
            existing_count=existing_count,
            skipped_count=skipped_count,
            items=response_items,
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/suggested-suppliers', methods=['GET'])
def get_suggested_suppliers(list_id, line_id):
    """
    Get all suggested suppliers for a line.
    """
    try:
        suppliers = db_execute(
            """
            SELECT 
                ss.id,
                ss.supplier_id,
                s.name as supplier_name,
                ss.source_type,
                ss.date_added
            FROM parts_list_line_suggested_suppliers ss
            JOIN suppliers s ON s.id = ss.supplier_id
            WHERE ss.parts_list_line_id = ?
            ORDER BY ss.date_added DESC
            """,
            (line_id,),
            fetch='all',
        )

        return jsonify(success=True, suppliers=[dict(s) for s in suppliers or []])

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/suggested-suppliers/<int:suggested_id>/remove',
                     methods=['POST'])
def remove_suggested_supplier(list_id, line_id, suggested_id):
    """
    Remove a supplier from suggested suppliers list.
    """
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, """
                DELETE FROM parts_list_line_suggested_suppliers 
                WHERE id = ? AND parts_list_line_id = ?
            """, (suggested_id, line_id))

            if cur.rowcount == 0:
                return jsonify(success=False, message="Suggested supplier not found"), 404

        return jsonify(success=True)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/supplier-emails/add', methods=['POST'])
def record_supplier_email(list_id, line_id):
    """
    Record that a supplier was emailed for a specific parts list line.
    Body JSON: {
        supplier_id: int,
        email_subject: str,
        email_body: str (optional),
        recipient_email: str,
        recipient_name: str (optional),
        notes: str (optional)
    }
    """
    try:
        data = request.get_json(force=True)
        supplier_id = data.get('supplier_id')
        email_subject = data.get('email_subject')
        recipient_email = data.get('recipient_email')

        if not supplier_id or not email_subject or not recipient_email:
            return jsonify(success=False, message="supplier_id, email_subject, and recipient_email are required"), 400

        with db_cursor(commit=True) as cur:
            # Verify line belongs to list
            line = _execute_with_cursor(cur, """
                SELECT id FROM parts_list_lines 
                WHERE id = ? AND parts_list_id = ?
            """, (line_id, list_id)).fetchone()

            if not line:
                return jsonify(success=False, message="Line not found"), 404

            # Get current user ID from session (if available)
            sent_by_user_id = session.get('user_id')

            # Insert email record
            email_row = _execute_with_cursor(cur, """
                INSERT INTO parts_list_line_supplier_emails 
                (parts_list_line_id, supplier_id, email_subject, email_body, 
                 recipient_email, recipient_name, sent_by_user_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (
                line_id,
                supplier_id,
                email_subject,
                data.get('email_body'),
                recipient_email,
                data.get('recipient_name'),
                sent_by_user_id,
                data.get('notes')
            )).fetchone()

            email_id = email_row['id'] if email_row else getattr(cur, 'lastrowid', None)

            # Get supplier name for response
            supplier = _execute_with_cursor(
                cur,
                "SELECT name FROM suppliers WHERE id = ?",
                (supplier_id,)
            ).fetchone()

            # Auto-update status to "Sent to Suppliers" if not already "Quoted"
            _execute_with_cursor(cur, """
                UPDATE parts_lists
                SET status_id = (SELECT id FROM parts_list_statuses WHERE name = 'Sent to Suppliers'),
                    date_modified = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (SELECT name FROM parts_list_statuses WHERE id = parts_lists.status_id) != 'Quoted'
                  AND EXISTS (SELECT 1 FROM parts_list_statuses WHERE name = 'Sent to Suppliers')
            """, (list_id,))

        return jsonify(
            success=True,
            message=f"Recorded email to {supplier['name'] if supplier else 'supplier'}",
            email_id=email_id
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/supplier-emails', methods=['GET'])
def get_supplier_emails(list_id, line_id):
    """
    Get all recorded emails for a specific parts list line.
    """
    try:
        emails = db_execute(
            """
            SELECT 
                se.id,
                se.supplier_id,
                s.name as supplier_name,
                se.date_sent,
                se.email_subject,
                se.recipient_email,
                se.recipient_name,
                se.notes,
                u.username as sent_by_username
            FROM parts_list_line_supplier_emails se
            JOIN suppliers s ON s.id = se.supplier_id
            LEFT JOIN users u ON u.id = se.sent_by_user_id
            WHERE se.parts_list_line_id = ?
            ORDER BY se.date_sent DESC
            """,
            (line_id,),
            fetch='all',
        )

        return jsonify(success=True, emails=[dict(e) for e in emails or []])

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/record-bulk-supplier-emails', methods=['POST'])
def record_bulk_supplier_emails():
    """
    Record multiple supplier emails at once (for bulk email operations).
    Body JSON: {
        emails: [{
            parts_list_line_id: int,
            supplier_id: int,
            email_subject: str,
            recipient_email: str,
            recipient_name: str (optional)
        }]
    }
    """
    try:
        data = request.get_json(force=True)
        emails = data.get('emails', [])

        if not emails:
            return jsonify(success=False, message="No emails to record"), 400

        sent_by_user_id = session.get('user_id')
        recorded_count = 0

        with db_cursor(commit=True) as cur:
            parts_list_ids = set()
            for email_data in emails:
                line_id = email_data.get('parts_list_line_id')
                _execute_with_cursor(cur, """
                    INSERT INTO parts_list_line_supplier_emails
                    (parts_list_line_id, supplier_id, email_subject, email_body,
                     recipient_email, recipient_name, sent_by_user_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    line_id,
                    email_data.get('supplier_id'),
                    email_data.get('email_subject'),
                    email_data.get('email_body'),
                    email_data.get('recipient_email'),
                    email_data.get('recipient_name'),
                    sent_by_user_id
                ))
                recorded_count += 1

                # Collect the parts list ID for this line
                if line_id:
                    line_row = _execute_with_cursor(cur,
                        "SELECT parts_list_id FROM parts_list_lines WHERE id = ?",
                        (line_id,)
                    ).fetchone()
                    if line_row:
                        parts_list_ids.add(line_row['parts_list_id'])

            # Auto-update status to "Sent to Suppliers" for all affected parts lists
            # (only if not already "Quoted")
            for list_id in parts_list_ids:
                _execute_with_cursor(cur, """
                    UPDATE parts_lists
                    SET status_id = (SELECT id FROM parts_list_statuses WHERE name = 'Sent to Suppliers'),
                        date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                      AND (SELECT name FROM parts_list_statuses WHERE id = parts_lists.status_id) != 'Quoted'
                      AND EXISTS (SELECT 1 FROM parts_list_statuses WHERE name = 'Sent to Suppliers')
                """, (list_id,))

        return jsonify(
            success=True,
            message=f"Recorded {recorded_count} supplier email(s)",
            count=recorded_count
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/parts-lists/<int:list_id>/supplier-panel-data', methods=['GET'])
def get_supplier_panel_data(list_id):
    """
    Get comprehensive supplier data for panel view modal.
    Shows all suppliers contacted, their lines, quote status, and dates.
    """
    try:
        with db_cursor() as cur:
            # Get all suppliers that have been emailed for this parts list
            suppliers = _execute_with_cursor(cur, """
                SELECT DISTINCT
                    s.id as supplier_id,
                    s.name as supplier_name,
                    s.contact_name,
                    s.contact_email
                FROM parts_list_line_supplier_emails se
                JOIN suppliers s ON s.id = se.supplier_id
                JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                WHERE pll.parts_list_id = ?
                ORDER BY s.name
            """, (list_id,)).fetchall()

            suppliers_data = []

            for sup in suppliers:
                supplier_id = sup['supplier_id']

                # Get all lines sent to this supplier with quote status
                lines = _execute_with_cursor(cur, """
                    SELECT DISTINCT
                        pll.id as line_id,
                        pll.line_number,
                        pll.customer_part_number,
                        pll.quantity,
                        pll.chosen_cost,
                        se.date_sent,
                        se.recipient_name,
                        -- Check if quoted
                        (SELECT sql.unit_price
                         FROM parts_list_supplier_quote_lines sql
                         JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                         WHERE sq.supplier_id = ?
                           AND sq.parts_list_id = ?
                           AND sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                           AND COALESCE(sql.is_no_bid, FALSE) = FALSE
                           AND sql.unit_price IS NOT NULL
                         ORDER BY sq.quote_date DESC
                         LIMIT 1) as quoted_price,
                        -- Get currency
                        (SELECT c.currency_code
                         FROM parts_list_supplier_quote_lines sql
                         JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                         LEFT JOIN currencies c ON c.id = sq.currency_id
                         WHERE sq.supplier_id = ?
                           AND sq.parts_list_id = ?
                           AND sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                           AND COALESCE(sql.is_no_bid, FALSE) = FALSE
                         ORDER BY sq.quote_date DESC
                         LIMIT 1) as currency_code,
                        -- Check if no bid
                        EXISTS(
                            SELECT 1
                            FROM parts_list_supplier_quote_lines sql
                            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                            WHERE sq.supplier_id = ?
                              AND sq.parts_list_id = ?
                              AND sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                              AND COALESCE(sql.is_no_bid, FALSE) = TRUE
                        ) as is_no_bid,
                        -- Check if line is costed (regardless of supplier)
                        CASE WHEN pll.chosen_cost IS NOT NULL THEN 1 ELSE 0 END as is_costed
                    FROM parts_list_line_supplier_emails se
                    JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                    WHERE pll.parts_list_id = ?
                      AND se.supplier_id = ?
                    ORDER BY pll.line_number
                """, (
                    supplier_id,
                    list_id,
                    supplier_id,
                    list_id,
                    supplier_id,
                    list_id,
                    list_id,
                    supplier_id
                )).fetchall()

                # Calculate statistics
                total_lines = len(lines)
                quoted_lines = sum(1 for l in lines if l['quoted_price'] is not None)
                no_bid_lines = sum(1 for l in lines if l['is_no_bid'])
                awaiting_lines = sum(1 for l in lines if not l['quoted_price'] and not l['is_no_bid'])
                costed_lines = sum(1 for l in lines if l['is_costed'])

                suppliers_data.append({
                    'supplier_id': supplier_id,
                    'supplier_name': sup['supplier_name'],
                    'contact_name': sup['contact_name'],
                    'contact_email': sup['contact_email'],
                    'total_lines': total_lines,
                    'quoted_lines': quoted_lines,
                    'no_bid_lines': no_bid_lines,
                    'awaiting_lines': awaiting_lines,
                    'costed_lines': costed_lines,
                    'lines': [dict(l) for l in lines]
                })

        return jsonify(success=True, suppliers=suppliers_data)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/parts-lists/<int:list_id>/sourcing', methods=['GET'])
def parts_list_sourcing(list_id):
    """
    Display sourcing interface for a parts list
    UPDATED: Now includes quoted prices in the contacted supplier section and supplier names for chosen costs
    """
    try:
        # Get list header
        header = db_execute(
            """
            SELECT 
                pl.*, 
                c.name as customer_name,
                cont.name as contact_name,
                cont.email as contact_email,
                s.name as status_name,
                p.name as project_name
            FROM parts_lists pl
            LEFT JOIN customers c ON c.id = pl.customer_id
            LEFT JOIN contacts cont ON cont.id = pl.contact_id
            LEFT JOIN parts_list_statuses s ON s.id = pl.status_id
            LEFT JOIN projects p ON p.id = pl.project_id
            WHERE pl.id = ?
            """,
            (list_id,),
            fetch='one',
        )

        if not header:
            return "Parts list not found", 404

        if header.get('project_id'):
            _repair_project_linked_parts_list_lines(list_id)

        with db_cursor() as cur:
            # Get all lines with base sourcing info AND supplier name for chosen cost
            lines = _execute_with_cursor(cur, """
                SELECT 
                    pll.*,
                    s.name as chosen_supplier_name,
                    (SELECT COUNT(*) 
                     FROM parts_list_line_suggested_suppliers 
                     WHERE parts_list_line_id = pll.id) as suggested_suppliers_count,
                    (SELECT COUNT(*) 
                     FROM parts_list_line_supplier_emails 
                     WHERE parts_list_line_id = pll.id) as emails_sent_count
                    ,
                    (SELECT 1
                     FROM customer_quote_lines cql
                     WHERE cql.parts_list_line_id = pll.id
                       AND cql.quoted_status = 'quoted'
                       AND cql.quote_price_gbp IS NOT NULL
                     LIMIT 1) as has_customer_quote
                FROM parts_list_lines pll
                LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
                WHERE pll.parts_list_id = ?
                ORDER BY pll.line_number ASC
            """, (list_id,)).fetchall()

            # For each line, get detailed sourcing data
            lines_with_data = []
            for line in lines:
                base_part_number = line['base_part_number']

                # Get no-bid suppliers
                no_bid_suppliers = _execute_with_cursor(cur, """
                    SELECT DISTINCT sq.supplier_id
                    FROM parts_list_supplier_quote_lines sql
                    JOIN parts_list_supplier_quotes sq
                        ON sql.supplier_quote_id = sq.id
                    WHERE sql.parts_list_line_id = ?
                      AND sql.is_no_bid = TRUE
                """, (line['parent_line_id'] or line['id'],)).fetchall()
                no_bid_supplier_ids = {row['supplier_id'] for row in no_bid_suppliers}

                # Get VQ data
                vq_data = _execute_with_cursor(cur, """
                    SELECT 
                        v.vq_number,
                        v.entry_date,
                        s.name as supplier_name,
                        s.id as supplier_id,
                        v.currency_id,
                        vl.vendor_price,
                        vl.quantity_quoted,
                        vl.lead_days,
                        COALESCE(c.currency_code, vl.foreign_currency, 'GBP') as currency_code
                    FROM vq_lines vl
                    JOIN vqs v ON vl.vq_id = v.id
                    LEFT JOIN suppliers s ON v.supplier_id = s.id
                    LEFT JOIN currencies c ON v.currency_id = c.id
                    WHERE vl.base_part_number = ?
                    ORDER BY v.entry_date DESC
                    LIMIT 10
                """, (base_part_number,)).fetchall()

                # Get PO data
                po_data = _execute_with_cursor(cur, """
                    SELECT 
                        po.purchase_order_ref,
                        po.date_issued,
                        s.name as supplier_name,
                        s.id as supplier_id,
                        pol.quantity,
                        pol.price,
                        pol.ship_date,
                        po.currency_id,
                        COALESCE(curr.currency_code, 'GBP') as currency_code,
                        pos.name as status_name
                    FROM purchase_order_lines pol
                    JOIN purchase_orders po ON pol.purchase_order_id = po.id
                    LEFT JOIN suppliers s ON po.supplier_id = s.id
                    LEFT JOIN currencies curr ON po.currency_id = curr.id
                    LEFT JOIN purchase_order_statuses pos ON po.purchase_status_id = pos.id
                    WHERE pol.base_part_number = ?
                    ORDER BY po.date_issued DESC
                    LIMIT 10
                """, (base_part_number,)).fetchall()

                # Get Stock data
                stock_data = _execute_with_cursor(cur, """
                    SELECT 
                        sm.movement_id,
                        sm.datecode,
                        sm.movement_date,
                        sm.cost_per_unit,
                        sm.available_quantity,
                        sm.reference
                    FROM stock_movements sm
                    WHERE sm.base_part_number = ? 
                        AND sm.movement_type = 'IN' 
                        AND sm.available_quantity > 0
                    ORDER BY sm.movement_date
                """, (base_part_number,)).fetchall()

                excess_data = _execute_with_cursor(cur, """
                    SELECT
                        l.id,
                        l.quantity,
                        l.date_code,
                        l.manufacturer,
                        l.unit_price,
                        l.unit_price_currency_id,
                        c.currency_code,
                        el.name as list_name,
                        el.upload_date,
                        el.entered_date,
                        s.id as supplier_id,
                        s.name as supplier_name
                    FROM excess_stock_lines l
                    JOIN excess_stock_lists el ON el.id = l.excess_stock_list_id
                    LEFT JOIN suppliers s ON s.id = el.supplier_id
                    LEFT JOIN currencies c ON c.id = l.unit_price_currency_id
                    WHERE l.base_part_number = ?
                    ORDER BY el.upload_date DESC, el.entered_date DESC, l.id DESC
                    LIMIT 10
                """, (base_part_number,)).fetchall()

                ils_data = _execute_with_cursor(cur, """
                    SELECT 
                        r.ils_company_name,
                        r.quantity,
                        r.condition_code,
                        r.search_date,
                        s.name as supplier_name,
                        s.id as supplier_id
                    FROM ils_search_results r
                    INNER JOIN suppliers s ON r.supplier_id = s.id
                    WHERE r.base_part_number = ?
                        AND r.id IN (
                            SELECT MAX(id) 
                            FROM ils_search_results 
                            WHERE base_part_number = ?
                                AND supplier_id IS NOT NULL
                            GROUP BY supplier_id
                        )
                    ORDER BY r.search_date DESC
                    LIMIT 20
                """, (base_part_number, base_part_number)).fetchall()

                # Get suggested suppliers with details
                suggested_suppliers = _execute_with_cursor(cur, """
                    SELECT 
                        ss.id,
                        ss.supplier_id,
                        s.name as supplier_name,
                        s.contact_name,
                        s.contact_email,
                        ss.source_type,
                        ss.date_added
                    FROM parts_list_line_suggested_suppliers ss
                    JOIN suppliers s ON s.id = ss.supplier_id
                    WHERE ss.parts_list_line_id = ?
                    ORDER BY ss.date_added DESC
                """, (line['id'],)).fetchall()

                # UPDATED: Get email history WITH quoted prices
                email_history_rows = _execute_with_cursor(cur, """
                    SELECT 
                        se.id,
                        se.supplier_id,
                        s.name as supplier_name,
                        se.date_sent,
                        se.email_subject,
                        se.recipient_email,
                        se.recipient_name,
                        u.username as sent_by_username,
                        (SELECT sql.unit_price
                         FROM parts_list_supplier_quote_lines sql
                         JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                         WHERE sq.supplier_id = se.supplier_id
                           AND sq.parts_list_id = ?
                           AND sql.parts_list_line_id = ?
                           AND sql.unit_price IS NOT NULL
                           AND sql.is_no_bid = FALSE
                         ORDER BY sq.quote_date DESC, sq.date_created DESC
                         LIMIT 1) as quoted_price,
                        (SELECT curr.currency_code
                         FROM parts_list_supplier_quote_lines sql
                         JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                         LEFT JOIN currencies curr ON curr.id = sq.currency_id
                         WHERE sq.supplier_id = se.supplier_id
                           AND sq.parts_list_id = ?
                           AND sql.parts_list_line_id = ?
                           AND sql.unit_price IS NOT NULL
                           AND sql.is_no_bid = FALSE
                         ORDER BY sq.quote_date DESC, sq.date_created DESC
                         LIMIT 1) as currency_code
                    FROM parts_list_line_supplier_emails se
                    JOIN suppliers s ON s.id = se.supplier_id
                    LEFT JOIN users u ON u.id = se.sent_by_user_id
                    WHERE se.parts_list_line_id = ?
                    ORDER BY se.date_sent DESC
                """, (list_id, line['id'], list_id, line['id'], line['id'])).fetchall()

                # Attach has_no_bid flag and quote data
                email_history = []
                for eh in email_history_rows:
                    eh_dict = dict(eh)
                    eh_dict['has_no_bid'] = eh['supplier_id'] in no_bid_supplier_ids
                    email_history.append(eh_dict)

                line_dict = dict(line)
                line_dict['vq_data'] = [dict(r) for r in vq_data]
                line_dict['po_data'] = [dict(r) for r in po_data]
                line_dict['stock_data'] = [dict(r) for r in stock_data]
                line_dict['excess_data'] = [dict(r) for r in excess_data]
                line_dict['ils_data'] = [dict(r) for r in ils_data]
                line_dict['suggested_suppliers'] = [dict(r) for r in suggested_suppliers]
                line_dict['email_history'] = email_history

                lines_with_data.append(line_dict)

        # Calculate stats
        total_lines = len(lines_with_data)
        lines_with_vq = sum(1 for l in lines_with_data if len(l['vq_data']) > 0)
        lines_with_po = sum(1 for l in lines_with_data if len(l['po_data']) > 0)
        lines_with_stock = sum(1 for l in lines_with_data if len(l['stock_data']) > 0)
        lines_with_excess = sum(1 for l in lines_with_data if len(l.get('excess_data') or []) > 0)
        lines_with_ils = sum(1 for l in lines_with_data if len(l['ils_data']) > 0)
        lines_with_suggested = sum(1 for l in lines_with_data if len(l['suggested_suppliers']) > 0)
        lines_contacted = sum(1 for l in lines_with_data if len(l['email_history']) > 0)

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Parts Lists', url_for('parts_list.view_parts_lists')),
            (header['name'], url_for('parts_list.parts_list', list_id=list_id)),
            ('Sourcing', None)
        ]

        return render_template('parts_list_sourcing.html',
                               list_id=list_id,
                               list_name=header['name'],
                               list_notes=header.get('notes'),
                               customer_name=header['customer_name'],
                               project_id=header.get('project_id'),
                               project_name=header.get('project_name'),
                               status_id=header.get('status_id'),
                               status_name=header.get('status_name'),
                               lines=lines_with_data,
                               total_lines=total_lines,
                               lines_with_vq=lines_with_vq,
                               lines_with_po=lines_with_po,
                               lines_with_stock=lines_with_stock,
                               lines_with_excess=lines_with_excess,
                               lines_with_ils=lines_with_ils,
                               lines_with_suggested=lines_with_suggested,
                               lines_contacted=lines_contacted,
                               breadcrumbs=breadcrumbs)

    except Exception as e:
        logging.exception(e)
        return str(e), 500


# Add this to your parts_list routes file

@parts_list_bp.route('/api/parts-lists/<int:list_id>/lines/<int:line_id>/ils-data', methods=['GET'])
def get_line_ils_data(list_id, line_id):
    """
    Get ILS data for a specific parts list line
    Returns both mapped and unmapped suppliers, sorted with mapped first
    """
    try:
        base_row = db_execute(
            """
            SELECT base_part_number 
            FROM parts_list_lines 
            WHERE id = ? AND parts_list_id = ?
            """,
            (line_id, list_id),
            fetch='one',
        )

        if not base_row:
            return jsonify({'success': False, 'error': 'Line not found'}), 404

        base_part_number = base_row['base_part_number']

        # Get ILS data - show most recent result per company
        ils_data = db_execute(
            """
            SELECT 
                r.id,
                r.ils_company_name,
                r.ils_cage_code,
                r.part_number,
                r.alt_part_number,
                r.quantity,
                r.condition_code,
                r.search_date,
                r.description,
                r.price,
                s.name as supplier_name,
                s.id as supplier_id
            FROM ils_search_results r
            LEFT JOIN suppliers s ON r.supplier_id = s.id
            WHERE r.base_part_number = ?
                AND r.id IN (
                    SELECT MAX(id) 
                    FROM ils_search_results 
                    WHERE base_part_number = ?
                    GROUP BY ils_company_name
                )
            ORDER BY 
                CASE WHEN s.id IS NOT NULL THEN 0 ELSE 1 END,
                r.search_date DESC
            LIMIT 50
            """,
            (base_part_number, base_part_number),
            fetch='all',
        )

        return jsonify({
            'success': True,
            'results': [dict(row) for row in ils_data or []]
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@parts_list_bp.route('/api/suppliers/no-bid-score', methods=['POST'])
def get_supplier_no_bid_scores():
    """
    Return per-supplier score based on requests sent vs no-bid and late/no responses.
    """
    try:
        data = request.get_json(silent=True) or {}
        raw_ids = data.get('supplier_ids', [])
        lookback_days = data.get('lookback_days', 60)
        response_window_days = data.get('response_window_days', 7)
        if not isinstance(raw_ids, list):
            return jsonify(success=False, error='supplier_ids must be a list'), 400
        try:
            lookback_days = int(lookback_days)
            response_window_days = int(response_window_days)
        except (TypeError, ValueError):
            return jsonify(success=False, error='lookback_days and response_window_days must be integers'), 400
        if lookback_days <= 0 or response_window_days <= 0:
            return jsonify(success=False, error='lookback_days and response_window_days must be positive'), 400

        supplier_ids = []
        for raw_id in raw_ids:
            try:
                supplier_id = int(raw_id)
                supplier_ids.append(supplier_id)
            except (TypeError, ValueError):
                continue

        supplier_ids = sorted(set(supplier_ids))
        if not supplier_ids:
            return jsonify(success=True, scores={})

        placeholders = ','.join(['?'] * len(supplier_ids))
        rows = db_execute(
            f"""
            WITH response_status AS (
                -- For each supplier+base_part_number, check if they responded at all
                -- Match by base_part_number so responses on ANY list count
                SELECT
                    sq.supplier_id,
                    pll.base_part_number,
                    1 AS has_response
                FROM parts_list_supplier_quotes sq
                JOIN parts_list_supplier_quote_lines sql
                    ON sql.supplier_quote_id = sq.id
                JOIN parts_list_lines pll
                    ON pll.id = sql.parts_list_line_id
                WHERE pll.base_part_number IS NOT NULL
                GROUP BY sq.supplier_id, pll.base_part_number
            ),
            no_bids AS (
                -- Parts where supplier explicitly said no-bid (match by base_part_number)
                SELECT
                    sq.supplier_id,
                    pll.base_part_number
                FROM parts_list_supplier_quotes sq
                JOIN parts_list_supplier_quote_lines sql
                    ON sql.supplier_quote_id = sq.id
                JOIN parts_list_lines pll
                    ON pll.id = sql.parts_list_line_id
                WHERE sql.is_no_bid = TRUE
                  AND pll.base_part_number IS NOT NULL
                GROUP BY sq.supplier_id, pll.base_part_number
            ),
            email_requests AS (
                -- Get emails with their base_part_number
                SELECT
                    se.supplier_id,
                    se.parts_list_line_id,
                    se.date_sent,
                    pll.base_part_number
                FROM parts_list_line_supplier_emails se
                JOIN parts_list_lines pll
                    ON pll.id = se.parts_list_line_id
                WHERE se.supplier_id IN ({placeholders})
                  AND se.date_sent >= NOW() - (? * INTERVAL '1 day')
            )
            SELECT
                er.supplier_id,
                COUNT(DISTINCT er.parts_list_line_id) AS requests_sent,
                COUNT(DISTINCT CASE WHEN nb.base_part_number IS NOT NULL THEN er.parts_list_line_id END) AS no_bid_count,
                -- Only count as no-response if:
                -- 1. Request is old enough (sent > response_window_days ago)
                -- 2. No response of any kind was received for this base_part_number
                COUNT(DISTINCT CASE
                    WHEN er.date_sent < NOW() - (? * INTERVAL '1 day')
                     AND rs.has_response IS NULL
                    THEN er.parts_list_line_id
                END) AS no_response_count,
                -- Track how many requests are still "young" (for debugging)
                COUNT(DISTINCT CASE
                    WHEN er.date_sent >= NOW() - (? * INTERVAL '1 day')
                    THEN er.parts_list_line_id
                END) AS young_requests
            FROM email_requests er
            LEFT JOIN response_status rs
                ON rs.supplier_id = er.supplier_id
               AND rs.base_part_number = er.base_part_number
            LEFT JOIN no_bids nb
                ON nb.supplier_id = er.supplier_id
               AND nb.base_part_number = er.base_part_number
            GROUP BY er.supplier_id
            """,
            supplier_ids + [lookback_days, response_window_days, response_window_days],
            fetch='all',
        )

        scores = {}
        for row in rows or []:
            requests_sent = int(row['requests_sent'] or 0)
            no_bid_count = int(row['no_bid_count'] or 0)
            no_response_count = int(row.get('no_response_count') or 0)
            young_requests = int(row.get('young_requests') or 0)
            # Mature requests = requests old enough to expect a response
            mature_requests = requests_sent - young_requests
            if requests_sent > 0:
                no_bid_rate = no_bid_count / requests_sent
                # No-response rate is based on mature requests only
                no_response_rate = no_response_count / mature_requests if mature_requests > 0 else 0
                # Score: penalize no-bids and no-responses equally
                # But only count no-responses against mature requests
                combined_bad = no_bid_count + no_response_count
                score = round((1 - combined_bad / requests_sent) * 100) if requests_sent > 0 else 100
                score = max(0, min(100, score))  # Clamp to 0-100
                if score >= 85:
                    rating = 'Great'
                elif score >= 70:
                    rating = 'Good'
                elif score >= 50:
                    rating = 'Mixed'
                else:
                    rating = 'Poor'
            else:
                no_bid_rate = None
                no_response_rate = None
                score = None
                rating = 'No history'

            scores[str(row['supplier_id'])] = {
                'requests_sent': requests_sent,
                'no_bid_count': no_bid_count,
                'no_bid_rate': no_bid_rate,
                'no_response_count': no_response_count,
                'no_response_rate': no_response_rate,
                'young_requests': young_requests,
                'mature_requests': mature_requests,
                'lookback_days': lookback_days,
                'response_window_days': response_window_days,
                'score': score,
                'rating': rating,
            }

        return jsonify(success=True, scores=scores)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, error=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines', methods=['GET'])
def get_parts_list_lines(list_id):
    """
    Get all lines for a parts list (for supplier quote input)
    UPDATED: Now includes quote_requested for highlighting
    """
    try:
        # Get optional supplier_id from query params
        supplier_id = request.args.get('supplier_id', type=int)
        include_status = request.args.get('include_status', type=int)

        if supplier_id:
            lines = db_execute(
                """
                SELECT 
                    pll.id,
                    pll.line_number,
                    pll.customer_part_number,
                    pll.base_part_number,
                    pll.quantity,
                    COALESCE((
                        SELECT 1 FROM parts_list_line_supplier_emails plse
                        WHERE plse.parts_list_line_id = pll.id
                          AND plse.supplier_id = ?
                        LIMIT 1
                    ), 0) AS quote_requested
                FROM parts_list_lines pll
                WHERE pll.parts_list_id = ?
                ORDER BY pll.line_number ASC
                """,
                (supplier_id, list_id),
                fetch='all',
            )
        elif include_status:
            lines = db_execute(
                """
                SELECT 
                    pll.id,
                    pll.line_number,
                    pll.customer_part_number,
                    pll.base_part_number,
                    pll.quantity,
                    pll.chosen_cost,
                    s.name as chosen_supplier_name,
                    c.symbol as chosen_currency_symbol,
                    (
                        SELECT cql.quote_price_gbp
                        FROM customer_quote_lines cql
                        WHERE cql.parts_list_line_id = pll.id
                          AND cql.quoted_status = 'quoted'
                          AND COALESCE(CAST(cql.is_no_bid AS INTEGER), 0) = 0
                          AND cql.quote_price_gbp IS NOT NULL
                        ORDER BY cql.date_modified DESC, cql.id DESC
                        LIMIT 1
                    ) as line_quote_price,
                    (
                        SELECT COUNT(*)
                        FROM parts_list_supplier_quote_lines sql
                        WHERE sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                    ) as line_supplier_quote_count,
                    (
                        SELECT COUNT(DISTINCT supplier_id)
                        FROM parts_list_line_supplier_emails plse
                        WHERE plse.parts_list_line_id = pll.id
                    ) as line_contacted_suppliers_count,
                    0 AS quote_requested
                FROM parts_list_lines pll
                LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
                LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                WHERE pll.parts_list_id = ?
                ORDER BY pll.line_number ASC
                """,
                (list_id,),
                fetch='all',
            )
        else:
            lines = db_execute(
                """
                SELECT 
                    pll.id,
                    pll.line_number,
                    pll.customer_part_number,
                    pll.base_part_number,
                    pll.quantity,
                    0 AS quote_requested
                FROM parts_list_lines pll
                WHERE pll.parts_list_id = ?
                ORDER BY pll.line_number ASC
                """,
                (list_id,),
                fetch='all',
            )

        return jsonify(success=True, lines=[dict(line) for line in lines or []])

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/suppliers/all', methods=['GET'])
def get_all_suppliers():
    """
    Get all suppliers for dropdown lists
    """
    try:
        suppliers = db_execute(
            '''
            SELECT DISTINCT s.id, s.name, c.id as currency_id, c.currency_code
            FROM suppliers s
            LEFT JOIN currencies c ON s.currency = c.id
            ORDER BY s.name
            ''',
            fetch='all',
        )

        return jsonify({
            'success': True,
            'suppliers': [dict(s) for s in suppliers or []]
        })

    except Exception as e:
        logging.error(f'Error loading suppliers: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/quotes', methods=['GET'])
def get_line_quotes(list_id, line_id):
    """
    Get all supplier quotes for a specific parts list line.
    For price break lines, shows ALL quotes for that part number across
    all lines in this parts list (not just the specific line).
    Returns quotes sorted by price (cheapest first).
    Also includes QPL approval data for manufacturer validation.
    """
    try:
        with db_cursor() as cur:
            # Verify line belongs to list and get part number info
            line = _execute_with_cursor(
                cur,
                """
                SELECT
                    pll.id,
                    pll.parts_list_id,
                    pll.customer_part_number,
                    pll.base_part_number,
                    pll.parent_line_id,
                    pn.pieces_per_pound
                FROM parts_list_lines pll
                LEFT JOIN part_numbers pn ON pn.base_part_number = pll.base_part_number
                WHERE pll.id = ? AND pll.parts_list_id = ?
                """,
                (line_id, list_id),
            ).fetchone()

            if not line:
                return jsonify(success=False, message="Line not found"), 404

            # Get ALL quote lines for this PART NUMBER in this parts list
            # This fixes the issue where price break lines couldn't see
            # offers from parent lines or sibling lines with same part
            quotes = _execute_with_cursor(
                cur,
                """
                SELECT
                    sql.id as quote_line_id,
                    sql.quoted_part_number,
                    sql.manufacturer,
                    sql.quantity_quoted,
                    sql.qty_available,
                    sql.purchase_increment,
                    sql.moq,
                    sql.unit_price,
                    sql.lead_time_days,
                    sql.condition_code,
                    sql.certifications,
                    sql.is_no_bid,
                    sql.line_notes,
                    sq.id as quote_id,
                    sq.quote_reference,
                    sq.quote_date,
                    sq.supplier_id,
                    s.name as supplier_name,
                    sq.currency_id,
                    c.currency_code,
                    c.symbol
                FROM parts_list_supplier_quote_lines sql
                JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                JOIN suppliers s ON s.id = sq.supplier_id
                LEFT JOIN currencies c ON c.id = sq.currency_id
                JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                WHERE pll.parts_list_id = ?
                AND pll.base_part_number = ?
                ORDER BY
                    sql.is_no_bid ASC,
                    CASE WHEN sql.unit_price IS NULL THEN 1 ELSE 0 END,
                    sql.unit_price ASC
                """,
                (list_id, line['base_part_number']),
            ).fetchall()

            # Get latest 3 offers for this part from OTHER parts lists
            # Use base_part_number for matching since that's normalized
            other_offers = _execute_with_cursor(
                cur,
                """
                SELECT
                    sql.id as quote_line_id,
                    sql.quoted_part_number,
                    sql.manufacturer,
                    sql.quantity_quoted,
                    sql.qty_available,
                    sql.purchase_increment,
                    sql.moq,
                    sql.unit_price,
                    sql.lead_time_days,
                    sql.condition_code,
                    sql.certifications,
                    sql.line_notes,
                    sq.id as quote_id,
                    sq.quote_reference,
                    sq.quote_date,
                    sq.supplier_id,
                    s.name as supplier_name,
                    sq.currency_id,
                    c.currency_code,
                    c.symbol,
                    pl.name as parts_list_name,
                    pl.id as parts_list_id
                FROM parts_list_supplier_quote_lines sql
                JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                JOIN suppliers s ON s.id = sq.supplier_id
                LEFT JOIN currencies c ON c.id = sq.currency_id
                JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                JOIN parts_lists pl ON pl.id = pll.parts_list_id
                WHERE pll.base_part_number = ?
                AND pll.parts_list_id != ?
                AND sql.is_no_bid = FALSE
                AND sql.unit_price IS NOT NULL
                ORDER BY sq.quote_date DESC
                LIMIT 3
                """,
                (line['base_part_number'], list_id),
            ).fetchall()

            # Get QPL approved manufacturers for this part number
            # Match on base_part_number against manufacturer_part_number or airbus_material
            qpl_approvals = []
            if line['base_part_number']:
                qpl_approvals = _execute_with_cursor(
                    cur,
                    """
                    SELECT DISTINCT
                        manufacturer_name,
                        cage_code,
                        approval_status
                    FROM manufacturer_approvals
                    WHERE airbus_material_base = ? OR manufacturer_part_number_base = ?
                    ORDER BY manufacturer_name
                    LIMIT 20
                    """,
                    (line['base_part_number'], line['base_part_number']),
                ).fetchall()

        return jsonify(
            success=True,
            quotes=[dict(q) for q in quotes or []],
            other_offers=[dict(o) for o in other_offers or []],
            qpl_approvals=[dict(q) for q in qpl_approvals or []],
            pieces_per_pound=line.get('pieces_per_pound') if line else None
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/parts-lists/<int:list_id>/lines/quote-availability', methods=['GET'])
def get_quote_availability_for_lines(list_id):
    """
    Bulk quote availability for costing page to avoid per-line requests.
    """
    try:
        rows = db_execute(
            """
            WITH lines AS (
                SELECT
                    id,
                    base_part_number,
                    COALESCE(parent_line_id, id) AS quote_line_id
                FROM parts_list_lines
                WHERE parts_list_id = ?
            ),
            this_list AS (
                SELECT
                    l.quote_line_id AS line_id,
                    SUM(CASE WHEN sql.is_no_bid = FALSE AND sql.unit_price IS NOT NULL THEN 1 ELSE 0 END) AS this_list_count
                FROM parts_list_supplier_quote_lines sql
                JOIN lines l ON l.quote_line_id = sql.parts_list_line_id
                GROUP BY l.quote_line_id
            ),
            other_offers AS (
                SELECT
                    l.base_part_number,
                    SUM(CASE WHEN sql.is_no_bid = FALSE AND sql.unit_price IS NOT NULL THEN 1 ELSE 0 END) AS other_offers_count
                FROM parts_list_supplier_quote_lines sql
                JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                JOIN lines l ON l.base_part_number = pll.base_part_number
                WHERE pll.parts_list_id != ?
                  AND l.base_part_number IS NOT NULL
                GROUP BY l.base_part_number
            )
            SELECT
                l.id AS line_id,
                COALESCE(t.this_list_count, 0) AS this_list_count,
                COALESCE(o.other_offers_count, 0) AS other_offers_count
            FROM lines l
            LEFT JOIN this_list t ON t.line_id = l.quote_line_id
            LEFT JOIN other_offers o ON o.base_part_number = l.base_part_number
            ORDER BY l.id
            """,
            (list_id, list_id),
            fetch='all',
        )
        return jsonify(success=True, lines=[dict(r) for r in rows or []])

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/parts-lists/search', methods=['GET'])
def search_parts_lists_by_part_number():
    """
    Fuzzy search parts lists by part number (customer or base).
    Query params:
      - q: search string (required)
      - customer_id: optional, filter by customer
      - limit: optional, max results (default 200)
    """
    try:
        query = (request.args.get('q') or '').strip()

        # If a user hits this URL directly in a browser, send them to the
        # main parts list page with the query so the page can auto-run the search.
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if not is_ajax and request.accept_mimetypes.accept_html:
            target = url_for('parts_list.view_parts_lists', q=query) if query else url_for('parts_list.view_parts_lists')
            return redirect(target)

        if not query:
            return jsonify(success=False, message="q (search term) is required"), 400

        # Optional filters
        customer_id = request.args.get('customer_id', type=int)
        limit = request.args.get('limit', default=200, type=int)
        if limit <= 0:
            limit = 200

        # Build fuzzy pattern (case-insensitive via UPPER)
        like_param = f"%{query.upper()}%"

        # Normalised base part number for exact match help
        try:
            base_query = create_base_part_number(query)
        except Exception:
            # Fallback: if normaliser blows up for some weird text, just ignore BPN equality
            base_query = None

        # Base SQL
        sql = """
            SELECT
                pll.id AS line_id,
                pll.parts_list_id,
                pll.line_number,
                pll.customer_part_number,
                pll.base_part_number,
                pll.quantity,
                pl.name AS list_name,
                pl.date_created,
                pl.date_modified,
                COALESCE(c.name, '') AS customer_name,
                pls.name AS status_name
            FROM parts_list_lines pll
            JOIN parts_lists pl ON pl.id = pll.parts_list_id
            LEFT JOIN customers c ON c.id = pl.customer_id
            LEFT JOIN parts_list_statuses pls ON pls.id = pl.status_id
            WHERE
                (
                    UPPER(pll.customer_part_number) LIKE ?
                    OR UPPER(pll.base_part_number) LIKE ?
        """

        params = [like_param, like_param]

        # Optional exact base PN match if we got one
        if base_query:
            sql += " OR pll.base_part_number = ?"
            params.append(base_query)

        sql += ")"

        # Optional customer filter
        if customer_id:
            sql += " AND pl.customer_id = ?"
            params.append(customer_id)

        sql += """
            ORDER BY
                pl.date_modified DESC,
                pl.date_created DESC,
                pll.line_number ASC
            LIMIT ?
        """
        params.append(limit)

        rows = db_execute(sql, params, fetch='all')

        return jsonify(
            success=True,
            total_matches=len(rows),
            results=[dict(r) for r in rows]
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/view/<int:list_id>')
def view_parts_list(list_id):
    """
    View a saved parts list (read-only view with navigation)
    """
    header = db_execute(
        """
        SELECT 
            pl.*, 
            c.name AS customer_name,
            cont.name AS contact_name,
            cont.email AS contact_email,
            s.name AS status_name,
            p.name AS project_name
        FROM parts_lists pl
        LEFT JOIN customers c ON c.id = pl.customer_id
        LEFT JOIN contacts cont ON cont.id = pl.contact_id
        LEFT JOIN parts_list_statuses s ON s.id = pl.status_id
        LEFT JOIN projects p ON p.id = pl.project_id
        WHERE pl.id = ?
        """,
        (list_id,),
        fetch='one',
    )

    if not header:
        flash('Parts list not found', 'error')
        return redirect(url_for('parts_list.view_parts_lists'))

    lines = db_execute(
        """
        SELECT *
        FROM parts_list_lines
        WHERE parts_list_id = ?
        ORDER BY line_number ASC, id ASC
        """,
        (list_id,),
        fetch='all',
    )

    loaded_list = {
        'id': list_id,
        'header': dict(header),
        'lines': [dict(line) for line in lines]
    }

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Parts Lists', url_for('parts_list.view_parts_lists')),
        (header['name'], None)
    ]

    return render_template('view_parts_list.html',
                           breadcrumbs=breadcrumbs,
                           loaded_list=loaded_list,
                           list_id=list_id,
                           list_name=header['name'],
                           list_notes=header.get('notes'),
                           customer_name=header['customer_name'],
                           project_id=header.get('project_id'),
                           project_name=header.get('project_name'),
                           status_id=header.get('status_id'),
                           status_name=header.get('status_name'))


@parts_list_bp.route('/extract-quote-from-pdf', methods=['POST'])
def extract_quote_from_pdf():
    """
    Upload PDF → extract text → run AI quote extraction → return lines ready for Handsontable
    """
    if 'file' not in request.files:
        return jsonify(success=False, message="No file uploaded")

    file = request.files['file']
    if not file.filename.lower().endswith('.pdf'):
        return jsonify(success=False, message="File must be a PDF")

    # Optional: get list_id from form (used in quick-quote page or modal)
    list_id = request.form.get('list_id', type=int)

    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"

        if not text.strip():
            return jsonify(success=False, message="No text found in PDF (might be scanned/image-only)")

        # Build context_parts exactly like you do in the normal extract route
        context_parts = ""
        if list_id:
            lines = db_execute(
                """
                SELECT line_number, customer_part_number, quantity 
                FROM parts_list_lines 
                WHERE parts_list_id = ? 
                ORDER BY line_number LIMIT 20
                """,
                (list_id,),
                fetch='all',
            )

            context_parts = "\n".join([
                f"Line {line['line_number']}: {line['customer_part_number']} (Qty: {line['quantity']})"
                for line in lines
            ])

        # This is your existing AI function — works perfectly
        extracted_lines = extract_supplier_quote_data(text, context_parts)

        return jsonify(
            success=True,
            extracted_lines=extracted_lines,
            raw_text=text[:3000] + "..." if len(text) > 3000 else text,
            message=f"Extracted {len(extracted_lines)} lines from PDF"
        )

    except Exception as e:
        logging.exception("PDF extraction failed")
        return jsonify(success=False, message="Failed to process PDF: " + str(e))

@parts_list_bp.route('/extract-quote-from-xlsx', methods=['POST'])
def extract_quote_from_xlsx():
    """
    Upload XLSX -> parse Proponent export -> return lines ready for Handsontable
    """
    if 'file' not in request.files:
        return jsonify(success=False, message="No file uploaded")

    file = request.files['file']
    if not file.filename.lower().endswith('.xlsx'):
        return jsonify(success=False, message="File must be an XLSX")

    list_id = request.form.get('list_id', type=int)

    def _normalize_part_number(value):
        if not value:
            return ''
        return re.sub(r'[^A-Z0-9]', '', str(value).upper())

    def _parse_number(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return None
        if 'no bid' in text.lower():
            return None
        match = re.search(r'-?\d+(?:\.\d+)?', text.replace(',', ''))
        return float(match.group(0)) if match else None

    def _parse_int(value):
        number = _parse_number(value)
        if number is None:
            return None
        return int(round(number))

    def _parse_qty_break(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            num = int(value)
            return (num, num)
        text = str(value).strip()
        if not text:
            return None
        if text.endswith('+'):
            minimum = _parse_int(text[:-1])
            return (minimum, None) if minimum is not None else None
        match = re.match(r'^\s*(\d+)\s*[-–]\s*(\d+)\s*$', text)
        if match:
            return (int(match.group(1)), int(match.group(2)))
        single = _parse_int(text)
        if single is not None:
            return (single, single)
        return None

    def _choose_price_break(breaks, target_qty):
        if not breaks:
            return None
        if target_qty is None:
            return breaks[0]['price']
        for item in breaks:
            minimum = item['min']
            maximum = item['max']
            if minimum is None:
                continue
            if maximum is None and target_qty >= minimum:
                return item['price']
            if maximum is not None and minimum <= target_qty <= maximum:
                return item['price']
        eligible = [b for b in breaks if b['min'] is not None and b['min'] <= target_qty]
        if eligible:
            return max(eligible, key=lambda b: b['min'])['price']
        return min(breaks, key=lambda b: b['min'] if b['min'] is not None else 0)['price']

    try:
        import openpyxl

        workbook = openpyxl.load_workbook(file, data_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))

        if not rows:
            return jsonify(success=False, message="No rows found in XLSX")

        header_row = rows[0]
        header_map = {}
        for idx, value in enumerate(header_row):
            if value:
                header_map[str(value).strip().lower()] = idx

        def _col(name):
            return header_map.get(name.lower())

        requested_part_idx = _col('requested part')
        quoted_part_idx = _col('quoted part')
        requested_qty_idx = _col('requested qty')
        quoted_qty_idx = _col('quoted qty')
        unit_price_idx = _col('unit price')
        moq_idx = _col('moq')
        qty_available_idx = _col('qty available')
        lead_time_idx = _col('out of stock lead time')
        certs_idx = _col('certs')
        notes_idx = _col('notes')

        breaks = []
        for i in range(1, 8):
            qty_idx = _col(f'qty break {i}')
            price_idx = _col(f'price {i}')
            if qty_idx is not None and price_idx is not None:
                breaks.append((qty_idx, price_idx))

        if requested_part_idx is None:
            return jsonify(success=False, message="Missing 'Requested Part' column in XLSX")

        requested_qty_by_part = {}
        if list_id:
            lines = db_execute(
                """
                SELECT customer_part_number, quantity
                FROM parts_list_lines
                WHERE parts_list_id = ?
                """,
                (list_id,),
                fetch='all',
            ) or []
            for line in lines:
                key = _normalize_part_number(line['customer_part_number'])
                if not key:
                    continue
                current = requested_qty_by_part.get(key)
                qty = _parse_int(line['quantity'])
                if qty is None:
                    continue
                requested_qty_by_part[key] = max(current, qty) if current is not None else qty

        best_by_part = {}
        for row in rows[1:]:
            requested_part = row[requested_part_idx] if requested_part_idx is not None else None
            if not requested_part:
                continue
            requested_part_text = str(requested_part).strip()
            if requested_part_text.lower().startswith('company name:'):
                continue

            quoted_part = row[quoted_part_idx] if quoted_part_idx is not None else None
            requested_qty = _parse_int(row[requested_qty_idx]) if requested_qty_idx is not None else None
            quoted_qty = _parse_int(row[quoted_qty_idx]) if quoted_qty_idx is not None else None
            qty_available = _parse_int(row[qty_available_idx]) if qty_available_idx is not None else None
            unit_price = _parse_number(row[unit_price_idx]) if unit_price_idx is not None else None
            moq = _parse_int(row[moq_idx]) if moq_idx is not None else None
            lead_time_days = _parse_int(row[lead_time_idx]) if lead_time_idx is not None else None

            if qty_available is None or qty_available <= 0:
                continue

            normalized_part = _normalize_part_number(requested_part_text)
            target_qty = requested_qty_by_part.get(normalized_part, requested_qty)
            if target_qty is None:
                target_qty = quoted_qty

            price_breaks = []
            for qty_idx, price_idx in breaks:
                qty_range = _parse_qty_break(row[qty_idx])
                price_value = _parse_number(row[price_idx])
                if qty_range and price_value is not None:
                    price_breaks.append({
                        'min': qty_range[0],
                        'max': qty_range[1],
                        'price': price_value
                    })

            selected_price = _choose_price_break(price_breaks, target_qty) if price_breaks else unit_price
            if selected_price is None:
                continue
            notes_parts = []
            certs_value = row[certs_idx] if certs_idx is not None else None
            notes_value = row[notes_idx] if notes_idx is not None else None
            if certs_value:
                notes_parts.append(str(certs_value).strip())
            if notes_value:
                notes_parts.append(str(notes_value).strip())

            line_entry = {
                'match_part_number': requested_part_text,
                'part_number': str(quoted_part).strip() if quoted_part else requested_part_text,
                'quantity': quoted_qty if quoted_qty is not None else requested_qty,
                'qty_available': qty_available,
                'purchase_increment': None,
                'moq': moq,
                'price': selected_price,
                'lead_time_days': lead_time_days,
                'condition': '',
                'certifications': str(certs_value).strip() if certs_value else '',
                'notes': "\n".join(notes_parts).strip(),
                '_exact_match': _normalize_part_number(requested_part_text) == _normalize_part_number(quoted_part)
            }

            existing = best_by_part.get(normalized_part)
            if not existing:
                best_by_part[normalized_part] = line_entry
            else:
                replace = False
                if line_entry['_exact_match'] and not existing.get('_exact_match'):
                    replace = True
                elif line_entry.get('_exact_match') == existing.get('_exact_match'):
                    if line_entry['price'] is not None and existing.get('price') is not None:
                        replace = line_entry['price'] < existing['price']
                    if line_entry['price'] == existing.get('price'):
                        replace = (line_entry.get('qty_available') or 0) > (existing.get('qty_available') or 0)
                if replace:
                    best_by_part[normalized_part] = line_entry

        extracted_lines = []
        for line_entry in best_by_part.values():
            line_entry.pop('_exact_match', None)
            extracted_lines.append(line_entry)

        return jsonify(
            success=True,
            extracted_lines=extracted_lines,
            message=f"Extracted {len(extracted_lines)} lines from XLSX"
        )

    except Exception as e:
        logging.exception("XLSX extraction failed")
        return jsonify(success=False, message="Failed to process XLSX: " + str(e))

@parts_list_bp.route('/extract-pdf-text', methods=['POST'])
def extract_pdf_text():
    if 'file' not in request.files:
        return jsonify(success=False, message="No file uploaded"), 400

    file = request.files['file']
    if not file or not file.filename or not file.filename.lower().endswith('.pdf'):
        return jsonify(success=False, message="Please upload a PDF file"), 400

    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text:
                    text_parts.append(page_text)
        text = "\n".join(text_parts).strip()
        max_chars = 60000
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
        return jsonify(success=True, text=text)
    except Exception as e:
        logging.exception("PDF text extraction failed")
        return jsonify(success=False, message="Failed to process PDF: " + str(e))

@parts_list_bp.route('/global-quick-search')
def global_quick_search():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify(success=False, message="Empty search")

    # Match 3245 or 3245-231
    match = re.match(r'^(\d+)(?:-(\d+))?$', q)
    if match:
        list_id = int(match.group(1))
        supplier_id = match.group(2)

        # Verify list exists (optional but nice)
        exists = db_execute("SELECT 1 FROM parts_lists WHERE id = ?", (list_id,), fetch='one')
        if not exists:
            return jsonify(success=False, message="Parts list not found")

        if supplier_id:
            supplier_id = int(supplier_id)
            return jsonify(success=True, redirect=url_for('parts_list.quick_supplier_quote', list_id=list_id, supplier_id=supplier_id))
        else:
            return jsonify(success=True, redirect=url_for('parts_list.parts_list_costing', list_id=list_id))

    # Fallback — existing behaviour
    return jsonify(success=True, redirect=url_for('parts_list.search_parts_lists_by_part_number', q=q))

@parts_list_bp.route('/parts-lists/<int:list_id>/quick-quote/<int:supplier_id>')
@parts_list_bp.route('/parts-lists/<int:list_id>/quick-quote')
def quick_supplier_quote(list_id, supplier_id=None):
    cache_bust = datetime.now().strftime('%Y%m%d')
    email_message_id = request.args.get('email_message_id') or None
    email_conversation_id = request.args.get('email_conversation_id') or None

    with db_cursor() as cur:
        # Verify list exists
        header = _execute_with_cursor(cur, """
            SELECT 
                pl.name,
                pl.customer_id,
                c.name AS customer_name,
                cont.name AS contact_name,
                cont.email AS contact_email
            FROM parts_lists pl
            LEFT JOIN customers c ON c.id = pl.customer_id
            LEFT JOIN contacts cont ON cont.id = pl.contact_id
            WHERE pl.id = ?
        """, (list_id,)).fetchone()
        if not header:
            abort(404, "Parts list not found")

        # Verify supplier exists if provided
        supplier_name = None
        existing_quote = None
        if supplier_id:
            supplier = _execute_with_cursor(cur, "SELECT name FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
            if not supplier:
                abort(404, "Supplier not found")
            supplier_name = supplier['name']
            existing_quote = _execute_with_cursor(cur, """
                SELECT id, quote_reference, quote_date
                FROM parts_list_supplier_quotes
                WHERE parts_list_id = ? AND supplier_id = ?
                ORDER BY (quote_date IS NULL), quote_date DESC, id DESC
                LIMIT 1
            """, (list_id, supplier_id)).fetchone()

        # Get lines for context + Handsontable WITH quote_requested flag
        if supplier_id:
            lines = _execute_with_cursor(cur, """
                SELECT 
                    pll.id as parts_list_line_id,
                    pll.line_number,
                    pll.customer_part_number,
                    pll.quantity,
                    pll.base_part_number,
                    COALESCE((
                        SELECT 1 FROM parts_list_line_supplier_emails plse
                        WHERE plse.parts_list_line_id = pll.id
                          AND plse.supplier_id = ?
                        LIMIT 1
                    ), 0) AS quote_requested
                FROM parts_list_lines pll
                WHERE pll.parts_list_id = ? 
                ORDER BY pll.line_number
            """, (supplier_id, list_id)).fetchall()
        else:
            lines = _execute_with_cursor(cur, """
                SELECT 
                    pll.id as parts_list_line_id,
                    pll.line_number,
                    pll.customer_part_number,
                    pll.quantity,
                    pll.base_part_number,
                    0 AS quote_requested
                FROM parts_list_lines pll
                WHERE pll.parts_list_id = ? 
                ORDER BY pll.line_number
            """, (list_id,)).fetchall()

        # All suppliers + currencies for dropdowns
        suppliers = _execute_with_cursor(cur, "SELECT id, name FROM suppliers ORDER BY name").fetchall()
        currencies = _execute_with_cursor(cur, "SELECT id, currency_code FROM currencies").fetchall()
        proponent_setting = _execute_with_cursor(
            cur,
            "SELECT value FROM app_settings WHERE key = 'proponent_supplier_id'"
        ).fetchone()
        proponent_supplier_id = None
        if proponent_setting and proponent_setting.get('value'):
            try:
                proponent_supplier_id = int(proponent_setting['value'])
            except (TypeError, ValueError):
                proponent_supplier_id = None

    return render_template('quick_supplier_quote.html',
                           list_id=list_id,
                           list_name=header['name'],
                           supplier_id=supplier_id,
                           supplier_name=supplier_name,
                           existing_quote=dict(existing_quote) if existing_quote else None,
                           lines=[dict(l) for l in lines],
                           suppliers=[dict(s) for s in suppliers],
                           currencies=[dict(c) for c in currencies],
                           email_message_id=email_message_id,
                           email_conversation_id=email_conversation_id,
                           proponent_supplier_id=proponent_supplier_id,
                           cache_bust=cache_bust)


@parts_list_bp.route('/parts-lists/<int:list_id>/emailed-suppliers', methods=['GET'])
def get_emailed_suppliers(list_id):
    """
    Return suppliers already emailed for this parts list.
    """
    try:
        with db_cursor() as cur:
            suppliers = _execute_with_cursor(cur, """
                SELECT DISTINCT
                    s.id AS supplier_id,
                    s.name AS supplier_name,
                    s.contact_email AS contact_email,
                    s.currency AS currency_id
                FROM parts_list_line_supplier_emails se
                JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                JOIN suppliers s ON s.id = se.supplier_id
                WHERE pll.parts_list_id = ?
                ORDER BY s.name
            """, (list_id,)).fetchall()

        return jsonify(success=True, suppliers=[dict(s) for s in suppliers])
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/api/parts-lists/<int:list_id>/quick-no-bid', methods=['GET'])
def get_quick_no_bid(list_id):
    """
    For a parts list, return suppliers that have been emailed,
    plus the lines that were sent to each supplier, and whether
    they already have a no-bid quote line.
    """
    try:
        with db_cursor() as cur:
            suppliers = _execute_with_cursor(cur, """
                SELECT DISTINCT
                    se.supplier_id,
                    s.name AS supplier_name
                FROM parts_list_line_supplier_emails se
                JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                JOIN suppliers s ON s.id = se.supplier_id
                WHERE pll.parts_list_id = ?
                ORDER BY s.name
            """, (list_id,)).fetchall()

            result = []

            for sup in suppliers:
                lines = _execute_with_cursor(cur, """
                    SELECT
                        pll.id AS line_id,
                        pll.line_number,
                        pll.customer_part_number,
                        EXISTS (
                            SELECT 1
                            FROM parts_list_supplier_quotes sq
                            JOIN parts_list_supplier_quote_lines sql
                              ON sql.supplier_quote_id = sq.id
                            WHERE sq.parts_list_id = ?
                              AND sq.supplier_id = ?
                              AND sql.parts_list_line_id = pll.id
                              AND sql.is_no_bid = TRUE
                        ) AS has_no_bid
                    FROM parts_list_line_supplier_emails se
                    JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                    WHERE pll.parts_list_id = ?
                      AND se.supplier_id = ?
                    GROUP BY pll.id
                    ORDER BY pll.line_number
                """, (list_id, sup['supplier_id'], list_id, sup['supplier_id'])).fetchall()

                result.append({
                    "supplier_id": sup['supplier_id'],
                    "supplier_name": sup['supplier_name'],
                    "lines": [dict(r) for r in lines],
                })

        return jsonify(success=True, suppliers=result)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/api/parts-lists/<int:list_id>/quick-no-bid', methods=['GET'])
def get_quick_no_bid_data(list_id):
    """
    Get suppliers that have been contacted for this parts list
    UPDATED: Now includes whether each line has a no-bid already set
    """
    try:
        suppliers_data = []

        with db_cursor() as cur:
            suppliers = _execute_with_cursor(cur, """
                SELECT DISTINCT 
                    s.id as supplier_id,
                    s.name as supplier_name
                FROM parts_list_line_supplier_emails se
                JOIN suppliers s ON s.id = se.supplier_id
                JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                WHERE pll.parts_list_id = ?
                ORDER BY s.name
            """, (list_id,)).fetchall()

            for sup in suppliers:
                lines = _execute_with_cursor(cur, """
                    SELECT DISTINCT
                        pll.id as line_id,
                        pll.line_number,
                        pll.customer_part_number,
                        EXISTS(
                            SELECT 1
                            FROM parts_list_supplier_quote_lines sql
                            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                            WHERE sq.supplier_id = ?
                              AND sq.parts_list_id = ?
                              AND sql.parts_list_line_id = pll.id
                              AND sql.is_no_bid = TRUE
                        ) as has_no_bid
                    FROM parts_list_line_supplier_emails se
                    JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                    WHERE se.supplier_id = ?
                      AND pll.parts_list_id = ?
                    ORDER BY pll.line_number
                """, (sup['supplier_id'], list_id, sup['supplier_id'], list_id)).fetchall()

                suppliers_data.append({
                    'supplier_id': sup['supplier_id'],
                    'supplier_name': sup['supplier_name'],
                    'lines': [dict(l) for l in lines]
                })

        return jsonify(success=True, suppliers=suppliers_data)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/api/parts-lists/<int:list_id>/quick-no-bid/<int:supplier_id>', methods=['POST'])
def apply_quick_no_bid(list_id, supplier_id):
    """
    Mark no-bid lines for a supplier on a parts list.
    - If payload has {"all": true}, mark all emailed lines.
    - If payload has {"line_ids": [..]}, mark just those lines.
    Only lines that belong to this parts list and were emailed
    are affected. No changes to other lines.
    """
    try:
        data = request.get_json(force=True) or {}
        mode_all = bool(data.get('all'))
        line_ids = data.get('line_ids') or []
        email_message_id = data.get('email_message_id') or None
        email_conversation_id = data.get('email_conversation_id') or None

        with db_cursor(commit=True) as cur:
            has_emails = _execute_with_cursor(cur, """
                SELECT 1
                FROM parts_list_line_supplier_emails se
                JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                WHERE pll.parts_list_id = ?
                  AND se.supplier_id = ?
                LIMIT 1
            """, (list_id, supplier_id)).fetchone()

            if not has_emails:
                return jsonify(success=False, message="No emails for this supplier on this parts list"), 400

            # Find or create quote header
            quote = _execute_with_cursor(cur, """
                SELECT id, email_message_id, email_conversation_id
                FROM parts_list_supplier_quotes
                WHERE parts_list_id = ? AND supplier_id = ?
                ORDER BY date_created ASC
                LIMIT 1
            """, (list_id, supplier_id)).fetchone()

            if quote:
                quote_id = quote['id']
                if (email_message_id or email_conversation_id) and (not quote['email_message_id'] and not quote['email_conversation_id']):
                    _execute_with_cursor(cur, """
                        UPDATE parts_list_supplier_quotes
                        SET email_message_id = COALESCE(?, email_message_id),
                            email_conversation_id = COALESCE(?, email_conversation_id)
                        WHERE id = ?
                    """, (email_message_id, email_conversation_id, quote_id))
            else:
                # --- Work out a non-null currency_id ---
                currency_row = _execute_with_cursor(cur, """
                    SELECT currency_id
                    FROM parts_list_supplier_quotes
                    WHERE parts_list_id = ?
                      AND currency_id IS NOT NULL
                    ORDER BY date_created DESC
                    LIMIT 1
                """, (list_id,)).fetchone()

                if currency_row and currency_row['currency_id']:
                    currency_id = currency_row['currency_id']
                else:
                    currency_row = _execute_with_cursor(cur, """
                        SELECT id
                        FROM currencies
                        WHERE currency_code = 'GBP'
                        LIMIT 1
                    """).fetchone()

                    if currency_row:
                        currency_id = currency_row['id']
                    else:
                        currency_row = _execute_with_cursor(cur, """
                            SELECT id
                            FROM currencies
                            ORDER BY id
                            LIMIT 1
                        """).fetchone()
                        if not currency_row:
                            return jsonify(success=False, message="No currencies defined in system"), 500
                        currency_id = currency_row['id']

                quote_row = _execute_with_cursor(cur, """
                    INSERT INTO parts_list_supplier_quotes
                        (parts_list_id, supplier_id, quote_reference, quote_date, currency_id, notes, created_by_user_id,
                         email_message_id, email_conversation_id)
                    VALUES (?, ?, ?, CURRENT_DATE, ?, ?, ?, ?, ?)
                    RETURNING id
                """, (
                    list_id,
                    supplier_id,
                    data.get('quote_reference') or 'Quick No Bid',
                    currency_id,
                    data.get('notes') or 'Auto-generated quick no-bid from sourcing screen',
                    session.get('user_id'),
                    email_message_id,
                    email_conversation_id
                )).fetchone()
                quote_id = quote_row['id'] if quote_row else getattr(cur, 'lastrowid', None)

            # If "all", fetch all emailed lines for this supplier on this list
            if mode_all:
                line_rows = _execute_with_cursor(cur, """
                    SELECT DISTINCT pll.id AS line_id
                    FROM parts_list_line_supplier_emails se
                    JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                    WHERE pll.parts_list_id = ?
                      AND se.supplier_id = ?
                """, (list_id, supplier_id)).fetchall()
                line_ids = [r['line_id'] for r in line_rows]

            # Upsert no-bid lines
            for lid in line_ids:
                exists_line = _execute_with_cursor(cur, """
                    SELECT 1
                    FROM parts_list_lines
                    WHERE id = ? AND parts_list_id = ?
                """, (lid, list_id)).fetchone()
                if not exists_line:
                    continue

                existing_line = _execute_with_cursor(cur, """
                    SELECT id, unit_price, is_no_bid
                    FROM parts_list_supplier_quote_lines
                    WHERE supplier_quote_id = ? AND parts_list_line_id = ?
                """, (quote_id, lid)).fetchone()

                if existing_line:
                    _execute_with_cursor(cur, """
                        UPDATE parts_list_supplier_quote_lines
                        SET is_no_bid = TRUE
                        WHERE id = ?
                    """, (existing_line['id'],))
                else:
                    _execute_with_cursor(cur, """
                        INSERT INTO parts_list_supplier_quote_lines
                            (supplier_quote_id, parts_list_line_id, is_no_bid)
                        VALUES (?, ?, TRUE)
                    """, (quote_id, lid))

        return jsonify(success=True, quote_id=quote_id)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/no-responses', methods=['GET'])
def view_no_response_suppliers():
    """
    Show suppliers that were emailed but have not responded with a quote or no-bid.
    """
    try:
        with db_cursor() as cur:
            ensure_no_response_table(cur)

            outstanding_rows = _execute_with_cursor(cur, """
                SELECT 
                    se.id AS email_id,
                    se.date_sent,
                    se.recipient_email,
                    pll.customer_part_number,
                    pll.quantity,
                    pll.line_number,
                    pl.id AS parts_list_id,
                    pl.name AS parts_list_name,
                    c.name AS customer_name,
                    s.id AS supplier_id,
                    s.name AS supplier_name,
                    s.contact_email AS supplier_contact_email,
                    s.contact_name AS supplier_contact_name,
                    EXISTS (
                        SELECT 1 FROM parts_list_lines pll2
                        WHERE pll2.id = pll.id AND pll2.chosen_cost IS NOT NULL
                    ) AS has_cost,
                    EXISTS (
                        SELECT 1 FROM parts_list_supplier_quote_lines sql
                        WHERE sql.parts_list_line_id = pll.id AND sql.is_no_bid = FALSE
                    ) AS has_quote
                FROM parts_list_line_supplier_emails se
                JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
                JOIN parts_lists pl ON pl.id = pll.parts_list_id
                LEFT JOIN customers c ON c.id = pl.customer_id
                JOIN suppliers s ON s.id = se.supplier_id
                LEFT JOIN parts_list_no_response_dismissals d ON d.email_id = se.id
                WHERE d.email_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM parts_list_supplier_quote_lines sql
                    JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                    WHERE sq.supplier_id = se.supplier_id
                      AND sq.parts_list_id = pl.id
                      AND sql.parts_list_line_id = se.parts_list_line_id
                  )
                ORDER BY se.date_sent DESC
            """).fetchall()

        suppliers_map = {}
        for row in outstanding_rows:
            sup_id = row['supplier_id']
            if sup_id not in suppliers_map:
                suppliers_map[sup_id] = {
                    'supplier_id': sup_id,
                    'supplier_name': row['supplier_name'],
                    'contact_email': row['supplier_contact_email'],
                    'contact_name': row['supplier_contact_name'],
                    'email_ids': [],
                    'lines': [],
                    'copy_lines': []
                }

            date_sent_raw = row['date_sent'] or ''
            date_sent_short = date_sent_raw.split(' ')[0] if date_sent_raw else ''
            parts_list_reference = f"{row['parts_list_id']}-{sup_id}"

            line_data = {
                'email_id': row['email_id'],
                'customer_part_number': row['customer_part_number'],
                'quantity': row['quantity'],
                'date_sent': row['date_sent'],
                'date_sent_short': date_sent_short,
                'parts_list_id': row['parts_list_id'],
                'parts_list_reference': parts_list_reference,
                'parts_list_name': row['parts_list_name'],
                'customer_name': row['customer_name'],
                'has_cost': bool(row['has_cost']),
                'has_quote': bool(row['has_quote'])
            }

            suppliers_map[sup_id]['lines'].append(line_data)
            suppliers_map[sup_id]['email_ids'].append(row['email_id'])
            suppliers_map[sup_id]['copy_lines'].append(
                f"{row['customer_part_number']} x{row['quantity']} ({date_sent_short}) ref {parts_list_reference} ({row['parts_list_name'] or 'List'})"
            )

        suppliers = sorted(
            suppliers_map.values(),
            key=lambda s: s['supplier_name'].lower() if s['supplier_name'] else ''
        )

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Supplier No Responses', None)
        ]

        return render_template(
            'parts_list_no_responses.html',
            suppliers=suppliers,
            breadcrumbs=breadcrumbs
        )

    except Exception as e:
        logging.exception(e)
        abort(500, description=str(e))


@parts_list_bp.route('/no-responses/dismiss', methods=['POST'])
def dismiss_no_response_entries():
    """
    Hide one or more outstanding email requests from the no-response list.
    """
    try:
        data = request.get_json(force=True) or {}
        email_ids = data.get('email_ids') or []
        if not isinstance(email_ids, list) or len(email_ids) == 0:
            return jsonify(success=False, message="email_ids list is required"), 400

        sanitized_ids = []
        for eid in email_ids:
            try:
                sanitized_ids.append(int(eid))
            except (TypeError, ValueError):
                logging.warning(f"Skipping invalid email_id in dismiss request: {eid}")

        if not sanitized_ids:
            return jsonify(success=False, message="No valid email_ids supplied"), 400

        inserted = 0
        with db_cursor(commit=True) as cur:
            ensure_no_response_table(cur)

            for eid in set(sanitized_ids):
                _execute_with_cursor(cur, """
                    INSERT INTO parts_list_no_response_dismissals (email_id)
                    VALUES (?)
                    ON CONFLICT(email_id) DO NOTHING
                """, (eid,))
                inserted += cur.rowcount

        return jsonify(success=True, dismissed_count=inserted)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/no-responses/generate-email', methods=['POST'])
def generate_no_response_email():
    """
    Build a follow-up email for suppliers that haven't responded.
    """
    try:
        data = request.get_json(force=True) or {}
        supplier_name = data.get('supplier_name') or 'Supplier'
        contact_name = data.get('contact_name') or ''
        contact_email = data.get('contact_email') or ''
        lines = data.get('lines') or []
        supplier_id = data.get('supplier_id')

        if not lines:
            return jsonify(success=False, message="No lines supplied"), 400

        # Reference is parts_list_id-supplier_id using first line's list id
        first_list_id = None
        for line in lines:
            if line.get('parts_list_id'):
                first_list_id = line['parts_list_id']
                break

        if first_list_id and supplier_id:
            subject_ref = f"{first_list_id}-{supplier_id}"
        else:
            subject_ref = 'Pending Quotes'
        subject = f"Follow up: quote request - {subject_ref}"

        greeting = f"Hi {contact_name}" if contact_name else "Hello"

        table_rows = ""
        for line in lines:
            part = line.get('customer_part_number') or ''
            qty = line.get('quantity') or ''
            date_sent = line.get('date_sent_short') or line.get('date_sent') or ''
            ref = line.get('parts_list_reference') or (f"{line.get('parts_list_id')}-{supplier_id}" if line.get('parts_list_id') and supplier_id else '')
            table_rows += f"""
                <tr>
                    <td style="padding: 8px; border: 1px solid #dee2e6;">{part}</td>
                    <td style="padding: 8px; border: 1px solid #dee2e6; text-align: center;">{qty}</td>
                    <td style="padding: 8px; border: 1px solid #dee2e6; text-align: center;">{date_sent}</td>
                    <td style="padding: 8px; border: 1px solid #dee2e6;">{ref}</td>
                </tr>
            """

        sender_name = "Purchasing Team"
        if current_user.is_authenticated and getattr(current_user, 'username', None):
            sender_name = current_user.username.replace('_', ' ').title()

        body_html = f"""
            <p>{greeting},</p>
            <p>Following up on our earlier request for the parts below. Please confirm pricing and availability.</p>
            <table style="border-collapse: collapse; width: 100%; max-width: 650px; margin: 20px 0;">
                <thead>
                    <tr style="background-color: #f8f9fa;">
                        <th style="padding: 8px; border: 1px solid #dee2e6; text-align: left;">Part</th>
                        <th style="padding: 8px; border: 1px solid #dee2e6; text-align: center;">Qty</th>
                        <th style="padding: 8px; border: 1px solid #dee2e6; text-align: center;">Date Sent</th>
                        <th style="padding: 8px; border: 1px solid #dee2e6; text-align: left;">Reference</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
            <p>Thank you,<br><br>{sender_name}</p>
        """

        return jsonify({
            'success': True,
            'supplier_name': supplier_name,
            'recipient_email': contact_email,
            'recipient_name': contact_name,
            'supplier_id': supplier_id,
            'subject': subject,
            'body_html': body_html
        })

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/update-status/<int:list_id>', methods=['POST'])
def update_parts_list_status(list_id):
    """
    Update status for a parts list. If status_id is provided in request body,
    set to that status directly. Otherwise cycle to the next status.
    """
    try:
        data = request.get_json(silent=True) or {}
        requested_status_id = data.get('status_id')

        with db_cursor(commit=True) as cur:
            current = _execute_with_cursor(
                cur,
                "SELECT status_id FROM parts_lists WHERE id = ?",
                (list_id,)
            ).fetchone()

            if not current:
                return jsonify({'success': False, 'message': 'Parts list not found'}), 404

            current_status_id = current['status_id']

            statuses = _execute_with_cursor(
                cur,
                "SELECT id FROM parts_list_statuses ORDER BY display_order ASC"
            ).fetchall()

            if not statuses:
                return jsonify({'success': False, 'message': 'No statuses available'}), 400

            status_ids = [s['id'] for s in statuses]

            if requested_status_id is not None:
                # Set to specific status if provided
                requested_status_id = int(requested_status_id)
                if requested_status_id not in status_ids:
                    return jsonify({'success': False, 'message': 'Invalid status ID'}), 400
                next_status_id = requested_status_id
            else:
                # Cycle to next status
                if current_status_id in status_ids:
                    current_index = status_ids.index(current_status_id)
                    next_index = (current_index + 1) % len(status_ids)
                    next_status_id = status_ids[next_index]
                else:
                    next_status_id = status_ids[0]

            _execute_with_cursor(
                cur,
                "UPDATE parts_lists SET status_id = ?, date_modified = CURRENT_TIMESTAMP WHERE id = ?",
                (next_status_id, list_id)
            )

            new_status = _execute_with_cursor(
                cur,
                "SELECT name FROM parts_list_statuses WHERE id = ?",
                (next_status_id,)
            ).fetchone()

        return jsonify({
            'success': True,
            'new_status_id': next_status_id,
            'new_status_name': new_status['name'] if new_status else 'Unknown'
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'message': str(e)}), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/set-cost', methods=['POST'])
def set_line_cost(list_id, line_id):
    """Set the chosen cost for a parts list line"""
    try:
        data = request.get_json()
        logging.info(f"set_line_cost called - list_id: {list_id}, line_id: {line_id}, data: {data}")

        cost = data.get('cost')

        if cost is None:
            logging.warning(f"No cost provided in request data: {data}")
            return jsonify(success=False, message='Cost is required'), 400

        with db_cursor(commit=True) as cur:
            line = _execute_with_cursor(cur, """
                SELECT id FROM parts_list_lines 
                WHERE id = ? AND parts_list_id = ?
            """, (line_id, list_id)).fetchone()

            logging.info(f"Line lookup result: {line}")

            if not line:
                logging.warning(f"Line {line_id} not found in list {list_id}")
                return jsonify(success=False, message='Line not found'), 404

            chosen_qty = data.get('chosen_qty')
            logging.info(f"Updating line {line_id} - cost: {cost}, chosen_qty: {chosen_qty}")

            if chosen_qty is not None:
                _execute_with_cursor(cur, """
                    UPDATE parts_list_lines 
                    SET chosen_cost = ?, chosen_qty = ?
                    WHERE id = ?
                """, (cost, chosen_qty, line_id))
                logging.info(f"Updated with qty - rows affected: {cur.rowcount}")
            else:
                _execute_with_cursor(cur, """
                    UPDATE parts_list_lines 
                    SET chosen_cost = ? 
                    WHERE id = ?
                """, (cost, line_id))
                logging.info(f"Updated without qty - rows affected: {cur.rowcount}")

        return jsonify(success=True, message='Cost saved successfully')

    except Exception as e:
        logging.exception(f"Error in set_line_cost: {e}")
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/use-cost', methods=['POST'])
def use_cost(list_id, line_id):
    """Update the chosen cost fields for a line."""
    try:
        data = request.get_json(force=True)
        logging.info(f"use_cost called - list_id: {list_id}, line_id: {line_id}, data: {data}")

        supplier_id = data.get('supplier_id')
        cost = data.get('cost')
        price = data.get('price')
        currency_id = data.get('currency_id')
        currency_code = data.get('currency_code')
        lead_days = data.get('lead_days')
        chosen_qty = data.get('chosen_qty')
        source_type = (data.get('source_type') or '').strip().lower() or None
        source_reference = data.get('source_reference')
        source_type_provided = 'source_type' in data
        source_reference_provided = 'source_reference' in data

        if cost is None:
            logging.warning(f"No cost provided in use_cost: {data}")
            return jsonify(success=False, message="cost is required"), 400

        with db_cursor(commit=True) as cur:
            if not currency_id and currency_code:
                currency = _execute_with_cursor(cur, """
                    SELECT id FROM currencies WHERE currency_code = ?
                """, (currency_code,)).fetchone()
                if currency:
                    currency_id = currency['id']
                    logging.info(f"Looked up currency_id {currency_id} from code {currency_code}")
                else:
                    logging.warning(f"Currency code {currency_code} not found in database")

            line = _execute_with_cursor(cur, """
                SELECT id FROM parts_list_lines 
                WHERE id = ? AND parts_list_id = ?
            """, (line_id, list_id)).fetchone()

            if not line:
                logging.warning(f"Line {line_id} not found in list {list_id}")
                return jsonify(success=False, message="Line not found"), 404

            logging.info(
                f"Updating line {line_id} with supplier: {supplier_id}, cost: {cost}, price: {price}, currency: {currency_id}, lead_days: {lead_days}, qty: {chosen_qty}")

            update_source = source_type_provided or source_reference_provided
            if update_source:
                _execute_with_cursor(cur, """
                    UPDATE parts_list_lines
                    SET chosen_supplier_id = ?,
                        chosen_cost = ?,
                        chosen_price = ?,
                        chosen_currency_id = ?,
                        chosen_lead_days = ?,
                        chosen_qty = ?,
                        chosen_source_type = ?,
                        chosen_source_reference = ?,
                        date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (supplier_id, cost, price, currency_id, lead_days, chosen_qty, source_type, source_reference, line_id))
            else:
                _execute_with_cursor(cur, """
                    UPDATE parts_list_lines
                    SET chosen_supplier_id = ?,
                        chosen_cost = ?,
                        chosen_price = ?,
                        chosen_currency_id = ?,
                        chosen_lead_days = ?,
                        chosen_qty = ?,
                        date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (supplier_id, cost, price, currency_id, lead_days, chosen_qty, line_id))

            logging.info(f"Update complete - rows affected: {cur.rowcount}")

        return jsonify(success=True, message="Cost updated successfully")

    except Exception as e:
        logging.exception(f"Error in use_cost: {e}")
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/parts-lists/<int:list_id>/lines/<int:line_id>/clear-cost', methods=['POST'])
def clear_cost(list_id, line_id):
    """Clear the chosen cost fields for a line."""
    try:
        with db_cursor(commit=True) as cur:
            line = _execute_with_cursor(cur, """
                SELECT id FROM parts_list_lines
                WHERE id = ? AND parts_list_id = ?
            """, (line_id, list_id)).fetchone()

            if not line:
                logging.warning(f"Line {line_id} not found in list {list_id} for clear_cost")
                return jsonify(success=False, message="Line not found"), 404

            _execute_with_cursor(cur, """
                UPDATE parts_list_lines
                SET chosen_supplier_id = NULL,
                    chosen_cost = NULL,
                    chosen_price = NULL,
                    chosen_currency_id = NULL,
                    chosen_lead_days = NULL,
                    chosen_qty = NULL,
                    chosen_source_type = NULL,
                    chosen_source_reference = NULL,
                    date_modified = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (line_id,))

        return jsonify(success=True, message="Cost cleared successfully")

    except Exception as e:
        logging.exception(f"Error in clear_cost: {e}")
        return jsonify(success=False, message=str(e)), 500

# UPDATE THE /parse-email ROUTE TO INCLUDE CONTACT MATCHING:
@parts_list_bp.route('/parse-email', methods=['POST'])
def parse_email():
    if 'file' not in request.files:
        return jsonify(success=False, message="No file uploaded"), 400

    file = request.files['file']
    if not (file.filename.endswith('.eml') or file.filename.endswith('.msg')):
        return jsonify(success=False, message="File must be .eml or .msg"), 400

    try:
        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name

        logging.info(f"Processing email file: {file.filename}")

        # Parse based on extension
        if file.filename.endswith('.msg'):
            msg = extract_msg.Message(tmp_path)
            subject = msg.subject or 'Untitled Email'
            sender_raw = msg.sender or ''
            body = msg.body or ''
            msg.close()
            logging.info(f"MSG parsed - Subject: {subject}, Sender: {sender_raw}")

            if '<' in sender_raw and '>' in sender_raw:
                sender = sender_raw.split('<')[1].split('>')[0]
            else:
                sender = sender_raw

        else:  # .eml
            with open(tmp_path, 'rb') as f:
                msg = email.message_from_bytes(f.read())
            subject = msg['subject'] or 'Untitled Email'
            sender_raw = msg['from'] or ''
            sender = email.utils.parseaddr(sender_raw)[1] if sender_raw else ''
            logging.info(f"EML parsed - Subject: {subject}, Sender raw: {sender_raw}, Extracted: {sender}")

            body = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/plain':
                        body = part.get_payload(decode=True).decode(errors='ignore')
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors='ignore')

        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except Exception as cleanup_error:
            logging.warning(f"Could not delete temp file {tmp_path}: {cleanup_error}")

        logging.info(f"Sender extracted: {sender}")
        logging.info(f"Body length: {len(body)} characters")

        # Match contact AND customer by sender email
        with db_cursor() as cur:
            customer_id = None
            customer_name = None
            contact_id = None
            contact_name = None

            if sender:
                logging.info(f"Looking up contact for email: {sender}")
                contact = _execute_with_cursor(cur, """
                    SELECT 
                        c.id as contact_id,
                        c.name,
                        c.second_name,
                        c.email,
                        c.customer_id, 
                        cust.name as customer_name
                    FROM contacts c
                    LEFT JOIN customers cust ON c.customer_id = cust.id
                    WHERE LOWER(c.email) = LOWER(?)
                    LIMIT 1
                """, (sender,)).fetchone()

                if contact:
                    contact_id = contact['contact_id']
                    contact_name = f"{contact['name']} {contact['second_name'] or ''}".strip()
                    customer_id = contact['customer_id']
                    customer_name = contact['customer_name']
                    logging.info(f"Contact found - ID: {contact_id}, Name: {contact_name}, Customer: {customer_name}")
                else:
                    logging.warning(f"No contact found for email: {sender}")

        # Extract parts from body (if any)
        lines = []
        if body.strip():
            logging.info(f"Extracting parts from body...")
            extracted = extract_part_numbers_and_quantities(body)
            logging.info(f"Extracted {len(extracted)} parts")

            if extracted:
                lines = [
                    {
                        'line_number': idx + 1,
                        'customer_part_number': part['part_number'],
                        'base_part_number': create_base_part_number(part['part_number']),
                        'quantity': part.get('quantity', 1)
                    }
                    for idx, part in enumerate(extracted)
                ]

        response_data = {
            'success': True,
            'subject': subject,
            'sender': sender,
            'customer_id': customer_id,
            'customer_name': customer_name,
            'contact_id': contact_id,
            'contact_name': contact_name,
            'parts': lines  # Will be empty list if no parts found
        }

        logging.info(f"Returning response with {len(lines)} parts, customer: {customer_name or 'None'}, contact: {contact_name or 'None'}")

        return jsonify(response_data)

    except Exception as e:
        logging.exception(f"Error parsing email: {e}")
        return jsonify(success=False, message=str(e)), 500

@parts_list_bp.route('/create-from-email', methods=['POST'])
def create_from_email():
    """
    Create a parts list directly from an email file upload (for Outlook macro).
    Returns a redirect URL to the newly created list.
    """
    uploaded_file = request.files.get('file')
    raw_body = request.get_data() if not uploaded_file else None
    email_message_id = request.form.get('email_message_id') if uploaded_file else request.headers.get('X-Email-Message-Id')
    email_conversation_id = request.form.get('email_conversation_id') if uploaded_file else request.headers.get('X-Email-Conversation-Id')

    if not uploaded_file and not raw_body:
        logging.warning("create-from-email: no multipart file and empty body")
        return jsonify(success=False, message="No file uploaded"), 400

    # Figure out filename/ext (supports multipart or raw body with X-Filename)
    if uploaded_file:
        original_filename = uploaded_file.filename or 'email.eml'
    else:
        original_filename = request.headers.get('X-Filename', 'email.eml')

    file_ext = os.path.splitext(original_filename)[1].lower()
    if file_ext not in ('.eml', '.msg'):
        # Default raw uploads without extension to .eml so they still parse
        file_ext = '.eml'
        original_filename = original_filename + '.eml'

    tmp_path = None

    def _strip_html(html_text: str) -> str:
        """Very small helper to turn HTML bodies into readable text."""
        text = re.sub(r'<[^>]+>', ' ', html_text or '')
        return ' '.join(text.split())

    def _clean_body(text: str) -> str:
        """Remove control chars and collapse whitespace from email bodies."""
        if not text:
            return ''
        # Drop non-printable control chars except \r\n\t
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', text)
        # Drop non-ASCII bytes to avoid binary blobs in AI prompt
        text = re.sub(r'[^\x09\x0a\x0d\x20-\x7e]', ' ', text)
        # Collapse whitespace
        return ' '.join(text.split())

    def _looks_like_rtf(text: str) -> bool:
        return text.strip().startswith('{\\rtf')

    def _rtf_to_text(rtf: str) -> str:
        """
        RTF to text using striprtf if available; fallback to lightweight stripper.
        """
        if not rtf:
            return ''
        if striprtf_to_text:
            try:
                return striprtf_to_text(rtf)
            except Exception:
                pass
        # Fallback lightweight stripper
        txt = re.sub(r'\\[a-zA-Z]+\d* ?', ' ', rtf)  # control words
        txt = re.sub(r'[{}]', ' ', txt)              # braces
        txt = txt.replace('\\\'', '')                # hex escapes indicator
        return ' '.join(txt.split())

    def _convert_msg_to_eml(msg_path: str) -> str:
        """Convert MSG to EML for consistent parsing."""
        msg_obj = extract_msg.Message(msg_path)

        eml = EmailMessage()
        eml['Subject'] = msg_obj.subject or ''
        eml['From'] = msg_obj.sender or ''
        eml['Date'] = msg_obj.date or ''

        # Use best available body
        chosen_body = ''
        if msg_obj.body:
            chosen_body = msg_obj.body
        elif hasattr(msg_obj, 'textBody') and msg_obj.textBody:
            chosen_body = msg_obj.textBody
        elif hasattr(msg_obj, 'htmlBody') and msg_obj.htmlBody:
            chosen_body = _strip_html(msg_obj.htmlBody)

        if _looks_like_rtf(chosen_body):
            chosen_body = _rtf_to_text(chosen_body)

        eml.set_content(chosen_body or '')

        msg_obj.close()

        eml_path = msg_path + ".eml"
        with open(eml_path, 'w', encoding='utf-8') as f:
            f.write(eml.as_string())

        return eml_path

    def _parse_eml(tmp_path_local: str):
        """Parse an .eml using the email package, favoring human-readable text and ignoring attachments."""
        with open(tmp_path_local, 'rb') as f:
            msg_obj = email.message_from_bytes(f.read(), policy=policy.default)

        subject_local = msg_obj['subject'] or 'Untitled Email'
        sender_raw_local = msg_obj['from'] or ''
        sender_local = email.utils.parseaddr(sender_raw_local)[1] if sender_raw_local else ''

        body_local = ''
        if msg_obj.is_multipart():
            # Prefer text/plain parts that are NOT attachments
            for part in msg_obj.walk():
                if part.get_content_disposition() == 'attachment':
                    continue
                if part.get_content_maintype() != 'text':
                    continue
                if part.get_content_type() == 'text/plain':
                    try:
                        body_local = part.get_content()
                    except Exception:
                        payload = part.get_payload(decode=True)
                        body_local = payload.decode(errors='ignore') if payload else ''
                    if body_local.strip():
                        break
            # If still empty, try first HTML (non-attachment)
            if not body_local:
                for part in msg_obj.walk():
                    if part.get_content_disposition() == 'attachment':
                        continue
                    if part.get_content_type() == 'text/html':
                        try:
                            body_local = _strip_html(part.get_content())
                        except Exception:
                            payload = part.get_payload(decode=True)
                            decoded = payload.decode(errors='ignore') if payload else ''
                            body_local = _strip_html(decoded)
                        break
        else:
            try:
                body_local = msg_obj.get_content()
            except Exception:
                payload = msg_obj.get_payload(decode=True)
                if payload:
                    body_local = payload.decode(errors='ignore')
                else:
                    body_local = msg_obj.get_payload() or ''

        return subject_local, sender_local, body_local

    try:
        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
            if uploaded_file:
                uploaded_file.save(tmp.name)
            else:
                tmp.write(raw_body)
            tmp_path = tmp.name

        logging.info(f"Processing email file for direct list creation: {original_filename} (ext {file_ext})")

        # Parse based on extension
        def _parse_uploaded_email(ext: str, path: str):
            """Mirror /parse-email behavior: prefer MSG body text, else EML with attachment skipping."""
            if ext == '.msg':
                try:
                    msg_obj = extract_msg.Message(path)
                    subject_local = msg_obj.subject or 'Untitled Email'
                    sender_raw_local = msg_obj.sender or ''
                    body_local = msg_obj.body or ''
                    if _looks_like_rtf(body_local):
                        body_local = _rtf_to_text(body_local)
                    # Some MSG files only have htmlBody/textBody
                    if not body_local:
                        if hasattr(msg_obj, 'textBody') and msg_obj.textBody:
                            body_local = msg_obj.textBody
                        elif hasattr(msg_obj, 'htmlBody') and msg_obj.htmlBody:
                            body_local = _strip_html(msg_obj.htmlBody)
                    msg_obj.close()

                    if '<' in sender_raw_local and '>' in sender_raw_local:
                        sender_local = sender_raw_local.split('<')[1].split('>')[0]
                    else:
                        sender_local = sender_raw_local

                    logging.info("Parsed MSG via extract_msg (macro flow)")
                    return subject_local, sender_local, body_local
                except InvalidFileFormatError:
                    logging.warning("File advertised as .msg but not OLE; falling back to EML parser")
                    return _parse_eml(path)
            else:
                return _parse_eml(path)

        # Parse uploaded email; if MSG, first convert to EML to normalize content
        if file_ext == '.msg':
            try:
                eml_path = _convert_msg_to_eml(tmp_path)
                subject, sender, body = _parse_eml(eml_path)
            except Exception as conv_exc:
                logging.warning(f"MSG->EML conversion failed ({conv_exc}); trying extract_msg directly")
                subject, sender, body = _parse_uploaded_email(file_ext, tmp_path)
        else:
            subject, sender, body = _parse_uploaded_email(file_ext, tmp_path)

        # Clean up temp file
        try:
            if tmp_path:
                os.unlink(tmp_path)
        except Exception as cleanup_error:
            logging.warning(f"Could not delete temp file {tmp_path}: {cleanup_error}")

        # Match contact and customer by sender email
        with db_cursor(commit=True) as cur:
            customer_id = None
            contact_id = None

            if sender:
                contact = _execute_with_cursor(cur, """
                    SELECT c.id as contact_id, c.customer_id
                    FROM contacts c
                    WHERE LOWER(c.email) = LOWER(?)
                    LIMIT 1
                """, (sender,)).fetchone()

                if contact:
                    contact_id = contact['contact_id']
                    customer_id = contact['customer_id']
                    logging.info(f"Found contact ID {contact_id} with customer ID {customer_id}")

            clean_body = _clean_body(body)
            if len(clean_body) > 20000:
                logging.info(f"Body too long ({len(clean_body)} chars); trimming to 20000 for AI prompt")
                clean_body = clean_body[:20000]
            logging.info(f"Cleaned body length: {len(clean_body)} characters")

            extracted = extract_part_numbers_and_quantities(clean_body) if clean_body.strip() else []
            logging.info(f"Extracted {len(extracted)} parts from email body")

            salesperson_id = 1  # Default
            if current_user.is_authenticated:
                sp = _execute_with_cursor(
                    cur,
                    "SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?",
                    (current_user.id,)
                ).fetchone()
                if sp and sp['legacy_salesperson_id']:
                    salesperson_id = sp['legacy_salesperson_id']

            list_name = f"Email: {subject[:50]}" if subject else "Email Import"
            
            list_row = _execute_with_cursor(cur, """
                INSERT INTO parts_lists 
                    (name, customer_id, contact_id, salesperson_id, status_id, notes, email_message_id, email_conversation_id, date_created, date_modified)
                VALUES (?, ?, ?, ?, 1, '', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
            """, (list_name, customer_id, contact_id, salesperson_id, email_message_id, email_conversation_id)).fetchone()
            
            list_id = list_row['id'] if list_row else getattr(cur, 'lastrowid', None)
            logging.info(f"Created parts list ID {list_id}")

            if extracted:
                for idx, part in enumerate(extracted):
                    _execute_with_cursor(cur, """
                        INSERT INTO parts_list_lines 
                        (parts_list_id, line_number, customer_part_number, base_part_number, quantity)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        list_id,
                        idx + 1,
                        part['part_number'],
                        create_base_part_number(part['part_number']),
                        part.get('quantity', 1)
                    ))
                logging.info(f"Inserted {len(extracted)} lines")
            else:
                logging.info("No parts extracted - created empty list")

        _log_parts_list_creation_communication(
            list_id,
            list_name,
            customer_id,
            contact_id,
            salesperson_id,
        )

        redirect_url = url_for('parts_list.view_parts_list', list_id=list_id, _external=True)
        logging.info(f"Returning redirect to: {redirect_url}")

        return jsonify({
            'success': True,
            'list_id': list_id,
            'redirect_url': redirect_url,
            'parts_count': len(extracted),
            'list_name': list_name
        })

    except Exception as e:
        logging.exception(f"Error creating list from email: {e}")
        return jsonify(success=False, message=str(e)), 500


@parts_list_bp.route('/outlook/macro', methods=['POST'])
def outlook_macro():
    """
    Create a parts list from Outlook macro JSON payload (selected text or full body).
    """
    data = request.get_json(force=True, silent=True)
    if data is None:
        raw_body = request.get_data(cache=False, as_text=True) or ''
        if raw_body.strip():
            try:
                data = json.loads(raw_body)
            except json.JSONDecodeError:
                logging.warning("Outlook macro received invalid JSON payload")
                return jsonify(success=False, message="Invalid JSON payload"), 400
        else:
            data = {}
    subject = data.get('subject') or 'Outlook import'
    sender = data.get('sender_email') or data.get('sender') or ''
    selected_text = data.get('selected_text') or ''
    body_text = data.get('body_text') or ''
    email_message_id = data.get('message_id') or data.get('email_message_id')
    email_conversation_id = data.get('conversation_id') or data.get('email_conversation_id')

    def _clean_body(text: str) -> str:
        """Remove control chars and collapse whitespace from email bodies."""
        if not text:
            return ''
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', text)
        text = re.sub(r'[^\x09\x0a\x0d\x20-\x7e]', ' ', text)
        return ' '.join(text.split())

    cleaned_selected = _clean_body(selected_text)
    cleaned_body = _clean_body(body_text)
    raw_text = cleaned_selected if cleaned_selected.strip() else cleaned_body
    if not raw_text.strip():
        return jsonify(success=False, message="No text provided"), 400

    clean_body = raw_text
    if len(clean_body) > 20000:
        logging.info(f"Outlook macro body too long ({len(clean_body)} chars); trimming to 20000")
        clean_body = clean_body[:20000]

    try:
        with db_cursor(commit=True) as cur:
            customer_id = None
            contact_id = None

            if sender:
                contact = _execute_with_cursor(cur, """
                    SELECT c.id as contact_id, c.customer_id
                    FROM contacts c
                    WHERE LOWER(c.email) = LOWER(?)
                    LIMIT 1
                """, (sender,)).fetchone()

                if contact:
                    contact_id = contact['contact_id']
                    customer_id = contact['customer_id']
                    logging.info(f"Outlook macro matched contact ID {contact_id} with customer ID {customer_id}")

            def _fallback_extract_tabular(text: str):
                parts = []
                for raw_line in (text or '').splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.lower().startswith('part #') or line.lower().startswith('part number'):
                        continue
                    if '\t' in line:
                        cols = [c.strip() for c in line.split('\t') if c.strip()]
                        if len(cols) >= 2:
                            part_number = cols[0]
                            qty_match = re.search(r'\b(\d+)\b', cols[-1])
                            qty = int(qty_match.group(1)) if qty_match else 1
                            parts.append({'part_number': part_number, 'quantity': qty})
                            continue
                    match = re.search(r'^\s*([A-Za-z0-9\-]+)\b.*?(\d+)\s*$', line)
                    if match:
                        parts.append({'part_number': match.group(1), 'quantity': int(match.group(2))})
                return parts

            extracted = extract_part_numbers_and_quantities(clean_body) if clean_body.strip() else []
            if not extracted:
                extracted = _fallback_extract_tabular(clean_body)
            logging.info(f"Outlook macro extracted {len(extracted)} parts")

            salesperson_id = 1  # Default
            if current_user.is_authenticated:
                sp = _execute_with_cursor(
                    cur,
                    "SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?",
                    (current_user.id,)
                ).fetchone()
                if sp and sp['legacy_salesperson_id']:
                    salesperson_id = sp['legacy_salesperson_id']

            list_name = f"Email: {subject[:50]}" if subject else "Outlook Import"

            list_row = _execute_with_cursor(cur, """
                INSERT INTO parts_lists 
                    (name, customer_id, contact_id, salesperson_id, status_id, notes, email_message_id, email_conversation_id, date_created, date_modified)
                VALUES (?, ?, ?, ?, 1, '', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
            """, (list_name, customer_id, contact_id, salesperson_id, email_message_id, email_conversation_id)).fetchone()

            list_id = list_row['id'] if list_row else getattr(cur, 'lastrowid', None)
            logging.info(f"Outlook macro created parts list ID {list_id}")

            if extracted:
                for idx, part in enumerate(extracted):
                    _execute_with_cursor(cur, """
                        INSERT INTO parts_list_lines 
                        (parts_list_id, line_number, customer_part_number, base_part_number, quantity)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        list_id,
                        idx + 1,
                        part['part_number'],
                        create_base_part_number(part['part_number']),
                        part.get('quantity', 1)
                    ))
                logging.info(f"Outlook macro inserted {len(extracted)} lines")
            else:
                logging.info("Outlook macro: no parts extracted")

        _log_parts_list_creation_communication(
            list_id,
            list_name,
            customer_id,
            contact_id,
            salesperson_id,
        )

        redirect_url = url_for('parts_list.view_parts_list', list_id=list_id, _external=True)

        return jsonify({
            'success': True,
            'list_id': list_id,
            'redirect_url': redirect_url,
            'parts_count': len(extracted),
            'list_name': list_name
        })

    except Exception as e:
        logging.exception(f"Error creating list from Outlook macro: {e}")
        return jsonify(success=False, message=str(e)), 500
    
@parts_list_bp.route('/debug-routes')
def debug_routes():
    from flask import current_app
    routes = []
    for rule in current_app.url_map.iter_rules():
        if 'parts_list' in rule.rule:
            routes.append(f"{rule.rule} -> {rule.endpoint} [{', '.join(rule.methods - {'HEAD', 'OPTIONS'})}]")
    return "<br>".join(sorted(routes))


@parts_list_bp.route('/parts-lists/<int:list_id>/related-emails', methods=['GET'])
def get_related_emails(list_id):
    """
    Display related emails for a parts list by fetching from Graph API using conversation_id.
    Returns emails from the original conversation thread plus any supplier quote emails.
    """
    try:
        # Get parts list with email tracking fields
        parts_list = db_execute(
            """
            SELECT id, name, email_message_id, email_conversation_id, customer_id
            FROM parts_lists
            WHERE id = ?
            """,
            (list_id,),
            fetch='one',
        )

        if not parts_list:
            flash('Parts list not found', 'error')
            return redirect(url_for('parts_list.view_parts_lists'))

        conversation_id = parts_list.get('email_conversation_id') if isinstance(parts_list, dict) else parts_list['email_conversation_id']
        source_message_id = parts_list.get('email_message_id') if isinstance(parts_list, dict) else parts_list['email_message_id']

        # Get customer info for breadcrumbs
        customer_name = None
        if parts_list.get('customer_id'):
            customer = db_execute("SELECT name FROM customers WHERE id = ?", (parts_list['customer_id'],), fetch='one')
            customer_name = customer['name'] if customer else None

        # Get supplier quotes with their email tracking
        supplier_quotes = db_execute(
            """
            SELECT
                sq.id,
                sq.quote_reference,
                sq.quote_date,
                sq.email_message_id,
                sq.email_conversation_id,
                sq.date_created,
                s.name as supplier_name
            FROM parts_list_supplier_quotes sq
            JOIN suppliers s ON s.id = sq.supplier_id
            WHERE sq.parts_list_id = ?
              AND (sq.email_message_id IS NOT NULL OR sq.email_conversation_id IS NOT NULL)
            ORDER BY sq.date_created DESC
            """,
            (list_id,),
            fetch='all',
        )

        # Get recorded supplier emails for this parts list
        supplier_line_emails = db_execute(
            """
            SELECT
                se.id,
                se.parts_list_line_id,
                COALESCE(pll.customer_part_number, pll.base_part_number) as part_number,
                se.supplier_id,
                s.name as supplier_name,
                se.date_sent,
                se.email_subject,
                se.recipient_email,
                se.recipient_name,
                se.notes,
                u.username as sent_by_username
            FROM parts_list_line_supplier_emails se
            JOIN parts_list_lines pll ON pll.id = se.parts_list_line_id
            JOIN suppliers s ON s.id = se.supplier_id
            LEFT JOIN users u ON u.id = se.sent_by_user_id
            WHERE pll.parts_list_id = ?
            ORDER BY se.date_sent DESC
            """,
            (list_id,),
            fetch='all',
        )

        # Collect all unique conversation/message IDs to fetch
        conversation_ids = set()
        if conversation_id:
            conversation_ids.add(conversation_id)
        message_ids = set()
        if source_message_id:
            message_ids.add(source_message_id)
        for sq in (supplier_quotes or []):
            sq_conv_id = sq.get('email_conversation_id') if isinstance(sq, dict) else sq['email_conversation_id']
            if sq_conv_id:
                conversation_ids.add(sq_conv_id)
            sq_msg_id = sq.get('email_message_id') if isinstance(sq, dict) else sq['email_message_id']
            if sq_msg_id:
                message_ids.add(sq_msg_id)

        # If no conversation or message IDs, show empty page
        if not conversation_ids and not message_ids:
            breadcrumbs = [
                ('Home', url_for('index')),
                ('Parts Lists', url_for('parts_list.view_parts_lists')),
                (parts_list['name'], url_for('parts_list.view_parts_list', list_id=list_id)),
                ('Related Emails', None)
            ]
            return render_template('parts_list_related_emails.html',
                                   list_id=list_id,
                                   list_name=parts_list['name'],
                                   list_notes=parts_list.get('notes'),
                                   customer_name=customer_name,
                                   source_email=None,
                                   conversation_emails=[],
                                   supplier_quote_emails=[dict(sq) for sq in (supplier_quotes or [])],
                                   supplier_line_emails=[dict(se) for se in (supplier_line_emails or [])],
                                   has_email_tracking=False,
                                   graph_connected=True)

        # Import Graph helpers from emails module
        from routes.emails import (
            _get_graph_settings,
            _load_graph_cache_for_request,
            _build_msal_app,
            _save_graph_cache_for_request,
        )
        import requests
        from urllib.parse import quote as url_quote
        from dateutil import parser as date_parser
        from datetime import timezone

        settings = _get_graph_settings(include_secret=True)
        cache, user_id = _load_graph_cache_for_request()
        app = _build_msal_app(settings, cache=cache)
        accounts = app.get_accounts()

        graph_connected = True
        if not accounts:
            graph_connected = False

        token = None
        if accounts:
            token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
            _save_graph_cache(cache)

        if not token or "access_token" not in token:
            graph_connected = False

        # Fetch emails for each conversation/message
        all_emails = []
        if graph_connected:
            headers = {"Authorization": f"Bearer {token['access_token']}"}

            for conv_id in conversation_ids:
                try:
                    # Use filter to get all messages in this conversation
                    params = {
                        "$filter": f"conversationId eq '{conv_id}'",
                        "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,webLink,conversationId,hasAttachments",
                        "$orderby": "receivedDateTime desc",
                        "$top": 50
                    }
                    resp = requests.get(
                        "https://graph.microsoft.com/v1.0/me/messages",
                        headers=headers,
                        params=params,
                        timeout=20,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        messages = data.get("value", [])
                        for msg in messages:
                            msg['_source_conversation_id'] = conv_id
                            msg['_is_source_conversation'] = (conv_id == conversation_id)
                            all_emails.append(msg)
                except Exception as e:
                    logging.warning(f"Failed to fetch conversation {conv_id}: {e}")

            for msg_id in message_ids:
                try:
                    resp = requests.get(
                        f"https://graph.microsoft.com/v1.0/me/messages/{url_quote(msg_id)}",
                        headers=headers,
                        params={
                            "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,webLink,conversationId,hasAttachments"
                        },
                        timeout=20,
                    )
                    if resp.status_code == 200:
                        msg = resp.json()
                        msg['_source_conversation_id'] = msg.get('conversationId')
                        msg['_is_source_conversation'] = (msg_id == source_message_id)
                        all_emails.append(msg)
                except Exception as e:
                    logging.warning(f"Failed to fetch message {msg_id}: {e}")

        # Dedupe by message id
        seen_ids = set()
        unique_emails = []
        for email_msg in all_emails:
            msg_id = email_msg.get('id')
            if msg_id and msg_id not in seen_ids:
                seen_ids.add(msg_id)
                unique_emails.append(email_msg)

        # Sort by date descending
        unique_emails.sort(
            key=lambda x: x.get('receivedDateTime', ''),
            reverse=True
        )

        def _format_graph_datetime_display(value):
            if not value:
                return None
            try:
                parsed = date_parser.isoparse(value)
            except Exception:
                try:
                    parsed = date_parser.parse(value)
                except Exception:
                    return None
            if parsed.tzinfo:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed.strftime("%b %d, %Y %I:%M %p")

        for email_msg in unique_emails:
            email_msg['receivedDateTime_display'] = _format_graph_datetime_display(
                email_msg.get('receivedDateTime')
            )

        # Find the source email
        source_email = None
        if source_message_id:
            for email_msg in unique_emails:
                if email_msg.get('id') == source_message_id:
                    source_email = email_msg
                    break

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Parts Lists', url_for('parts_list.view_parts_lists')),
            (parts_list['name'], url_for('parts_list.view_parts_list', list_id=list_id)),
            ('Related Emails', None)
        ]

        return render_template('parts_list_related_emails.html',
                               list_id=list_id,
                               list_name=parts_list['name'],
                               list_notes=parts_list.get('notes'),
                               customer_name=customer_name,
                               source_email=source_email,
                               conversation_emails=unique_emails,
                               supplier_quote_emails=[dict(sq) for sq in (supplier_quotes or [])],
                               supplier_line_emails=[dict(se) for se in (supplier_line_emails or [])],
                               has_email_tracking=bool(conversation_id or source_message_id or message_ids),
                               graph_connected=graph_connected)

    except Exception as e:
        logging.exception(f"Error fetching related emails for parts list {list_id}: {e}")
        flash('Error loading related emails', 'error')
        return redirect(url_for('parts_list.view_parts_list', list_id=list_id))


@parts_list_bp.route('/parts-lists/<int:list_id>/related-emails/data', methods=['GET'])
def get_related_emails_data(list_id):
    """
    Return related email metadata for a parts list (used for reply selection).
    Searches by conversation ID and also by customer contact email to find
    recent customer emails even if the conversation thread was broken.
    """
    try:
        parts_list = db_execute(
            """
            SELECT pl.id, pl.email_message_id, pl.email_conversation_id, pl.contact_id,
                   c.email as contact_email
            FROM parts_lists pl
            LEFT JOIN contacts c ON c.id = pl.contact_id
            WHERE pl.id = ?
            """,
            (list_id,),
            fetch='one',
        )
        if not parts_list:
            return jsonify(success=False, message="Parts list not found"), 404

        conversation_id = parts_list.get('email_conversation_id') if isinstance(parts_list, dict) else parts_list['email_conversation_id']
        source_message_id = parts_list.get('email_message_id') if isinstance(parts_list, dict) else parts_list['email_message_id']
        contact_email = parts_list.get('contact_email') if isinstance(parts_list, dict) else parts_list.get('contact_email')

        conversation_ids = set()
        if conversation_id:
            conversation_ids.add(conversation_id)
        message_ids = set()
        if source_message_id:
            message_ids.add(source_message_id)

        if not conversation_ids and not message_ids and not contact_email:
            return jsonify(success=True, emails=[], source_message_id=None, graph_connected=True)

        from routes.emails import (
            _get_graph_settings,
            _load_graph_cache_for_request,
            _build_msal_app,
            _save_graph_cache_for_request,
        )
        import requests
        from urllib.parse import quote as url_quote
        from dateutil import parser as date_parser
        from datetime import timezone

        settings = _get_graph_settings(include_secret=True)
        cache, user_id = _load_graph_cache_for_request()
        app = _build_msal_app(settings, cache=cache)
        accounts = app.get_accounts()

        if not accounts:
            return jsonify(success=False, message="No Graph account connected"), 400

        token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
        _save_graph_cache_for_request(user_id, cache)

        if not token or "access_token" not in token:
            return jsonify(success=False, message="Failed to refresh access token"), 400

        headers = {"Authorization": f"Bearer {token['access_token']}"}
        all_emails = []

        # 1. Search by conversation ID (finds emails in the same thread)
        for conv_id in conversation_ids:
            params = {
                "$filter": f"conversationId eq '{conv_id}'",
                "$select": "id,subject,from,receivedDateTime,bodyPreview,conversationId",
                "$orderby": "receivedDateTime desc",
                "$top": 50
            }
            resp = requests.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                headers=headers,
                params=params,
                timeout=20,
            )
            if resp.status_code == 200:
                data = resp.json()
                messages = data.get("value", [])
                for msg in messages:
                    msg['_is_source'] = False
                    all_emails.append(msg)

        # 2. Search by customer contact email (finds recent emails even if thread broke)
        if contact_email:
            # Search for emails FROM this contact (customer replies)
            params = {
                "$filter": f"from/emailAddress/address eq '{contact_email}'",
                "$select": "id,subject,from,receivedDateTime,bodyPreview,conversationId",
                "$orderby": "receivedDateTime desc",
                "$top": 20
            }
            resp = requests.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                headers=headers,
                params=params,
                timeout=20,
            )
            if resp.status_code == 200:
                data = resp.json()
                messages = data.get("value", [])
                for msg in messages:
                    msg['_is_source'] = False
                    all_emails.append(msg)

        # 3. Try to fetch the original source message directly
        source_found = False
        for msg_id in message_ids:
            resp = requests.get(
                f"https://graph.microsoft.com/v1.0/me/messages/{url_quote(msg_id)}",
                headers=headers,
                params={"$select": "id,subject,from,receivedDateTime,bodyPreview,conversationId"},
                timeout=20,
            )
            if resp.status_code == 200:
                msg = resp.json()
                msg['_is_source'] = (msg_id == source_message_id)
                all_emails.append(msg)
                source_found = True

        # Deduplicate emails, but ensure source message is properly marked
        seen_ids = {}
        for email_msg in all_emails:
            msg_id = email_msg.get('id')
            if not msg_id:
                continue
            if msg_id not in seen_ids:
                seen_ids[msg_id] = email_msg
            elif email_msg.get('_is_source'):
                # If this is the source, update the existing entry
                seen_ids[msg_id]['_is_source'] = True
        unique_emails = list(seen_ids.values())

        unique_emails.sort(
            key=lambda x: x.get('receivedDateTime', ''),
            reverse=True
        )

        def _format_graph_datetime_display(value):
            if not value:
                return None
            try:
                parsed = date_parser.isoparse(value)
            except Exception:
                try:
                    parsed = date_parser.parse(value)
                except Exception:
                    return None
            if parsed.tzinfo:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed.strftime("%b %d, %Y %I:%M %p")

        response_emails = []
        for email_msg in unique_emails:
            from_addr = email_msg.get('from', {}).get('emailAddress', {}) if isinstance(email_msg.get('from'), dict) else {}
            response_emails.append({
                "id": email_msg.get("id"),
                "subject": email_msg.get("subject"),
                "from_address": from_addr.get("address"),
                "from_name": from_addr.get("name"),
                "receivedDateTime": email_msg.get("receivedDateTime"),
                "receivedDateTime_display": _format_graph_datetime_display(email_msg.get("receivedDateTime")),
                "is_source": bool(email_msg.get("_is_source")),
            })

        return jsonify(
            success=True,
            emails=response_emails,
            source_message_id=source_message_id,
            graph_connected=True,
        )

    except Exception as e:
        logging.exception(f"Error fetching related emails data for parts list {list_id}: {e}")
        return jsonify(success=False, message="Failed to load related emails"), 500
