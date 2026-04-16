from flask import Blueprint, render_template, request, jsonify, url_for, session
from flask_login import current_user
from routes.emails import send_graph_email, send_graph_reply
from routes.email_signatures import get_user_default_signature
from models import get_email_signature_by_id, create_base_part_number
from db import db_cursor, execute as db_execute
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
import os

customer_quoting_bp = Blueprint('customer_quoting', __name__)


def _using_postgres() -> bool:
    """Return True if DATABASE_URL points at Postgres so '?' placeholders must be translated."""
    return os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://'))


def _prepare_query(query: str) -> str:
    """Translate SQLite `?` placeholders into C-style `%s` for Postgres."""
    if not _using_postgres():
        return query
    return query.replace('?', '%s')


def _execute_with_cursor(cur, query, params=None):
    """Execute SQL while translating placeholders for Postgres."""
    cur.execute(_prepare_query(query), params or [])
    return cur


def _with_returning_clause(query: str) -> str:
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


def _parse_decimal(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _ensure_part_number(cur, base_part_number, part_number):
    if not base_part_number:
        return
    part_value = part_number or base_part_number
    _execute_with_cursor(cur, """
        INSERT INTO part_numbers (base_part_number, part_number)
        VALUES (?, ?)
        ON CONFLICT (base_part_number) DO NOTHING
    """, (base_part_number, part_value))


def _get_default_signature(user_id=None):
    signature = get_user_default_signature(user_id) if user_id else None
    if not signature and current_user and getattr(current_user, "is_authenticated", False):
        signature = get_user_default_signature(current_user.id)
    if signature:
        return signature
    return get_email_signature_by_id(1)


def _parse_recipient_list(value):
    if not value:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).replace(";", ",").split(",")
    cleaned = []
    for item in raw_items:
        email_value = (item or "").strip()
        if email_value:
            cleaned.append(email_value)
    return cleaned


def _to_decimal(value, default):
    parsed = _parse_decimal(value)
    return parsed if parsed is not None else default


def _get_supplier_quote_metadata(cur, parts_list_line_id, supplier_id, source_type=None, source_reference=None):
    """Resolve supplier metadata for a line, preferring the explicitly chosen quote line."""
    condition = None
    certs = None
    manufacturer = None

    explicit_quote = None
    source_type_value = (source_type or '').strip().lower()
    if source_type_value == 'quote' and source_reference is not None:
        explicit_quote = _execute_with_cursor(cur, """
            SELECT sql.condition_code, sql.certifications, sql.manufacturer
            FROM parts_list_supplier_quote_lines sql
            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
            WHERE sql.parts_list_line_id = ?
              AND sq.supplier_id = ?
              AND CAST(sql.id AS TEXT) = ?
              AND sql.is_no_bid = FALSE
            LIMIT 1
        """, (parts_list_line_id, supplier_id, str(source_reference))).fetchone()

    if explicit_quote:
        condition = (explicit_quote['condition_code'] or '').strip() or None
        certs = (explicit_quote['certifications'] or '').strip() or None
        manufacturer = (explicit_quote['manufacturer'] or '').strip() or None

    latest_quote = None
    if supplier_id and (not condition or not certs or not manufacturer):
        latest_quote = _execute_with_cursor(cur, """
            SELECT sql.condition_code, sql.certifications, sql.manufacturer
            FROM parts_list_supplier_quote_lines sql
            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
            WHERE sql.parts_list_line_id = ?
              AND sq.supplier_id = ?
              AND sql.is_no_bid = FALSE
              AND (
                    (sql.condition_code IS NOT NULL AND TRIM(sql.condition_code) != '')
                 OR (sql.certifications IS NOT NULL AND TRIM(sql.certifications) != '')
                 OR (sql.manufacturer IS NOT NULL AND TRIM(sql.manufacturer) != '')
              )
            ORDER BY sq.quote_date DESC, sql.date_modified DESC, sql.id DESC
            LIMIT 1
            """, (parts_list_line_id, supplier_id)).fetchone()

    if latest_quote:
        if not condition:
            condition = (latest_quote['condition_code'] or '').strip() or None
        if not certs:
            certs = (latest_quote['certifications'] or '').strip() or None
        if not manufacturer:
            manufacturer = (latest_quote['manufacturer'] or '').strip() or None

    if supplier_id and (not condition or not certs):
        supplier_defaults = _execute_with_cursor(
            cur,
            "SELECT standard_condition, standard_certs FROM suppliers WHERE id = ?",
            (supplier_id,)
        ).fetchone()

        if supplier_defaults:
            if not condition:
                condition = (supplier_defaults['standard_condition'] or '').strip() or None
            if not certs:
                certs = (supplier_defaults['standard_certs'] or '').strip() or None

    return {
        'condition': condition or '',
        'certs': certs or '',
        'manufacturer': manufacturer or ''
    }


def _get_condition_and_certs(cur, parts_list_line_id, supplier_id, source_type=None, source_reference=None):
    """
    Resolve condition/certs for a line:
    1) Latest non-empty from supplier quote for chosen supplier
    2) Otherwise supplier's standard defaults
    Returns tuple (condition, certs) using empty strings when missing.
    """
    metadata = _get_supplier_quote_metadata(
        cur,
        parts_list_line_id,
        supplier_id,
        source_type=source_type,
        source_reference=source_reference,
    )
    return metadata['condition'], metadata['certs']


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote', methods=['GET'])
def customer_quote(list_id):
    """
    Display customer quoting interface for a parts list
    Shows all lines with chosen costs and allows margin addition
    """
    try:
        lines_with_bom = []
        with db_cursor() as cur:
                # Get list header
                header = _execute_with_cursor(cur, """
                    SELECT pl.*,
                           c.name as customer_name,
                           c.system_code as customer_system_code,
                           c.currency_id as customer_currency_id,
                           s.name as status_name,
                           ct.name as contact_name,
                           ct.email as contact_email,
                           p.name as project_name
                    FROM parts_lists pl
                    LEFT JOIN customers c ON c.id = pl.customer_id
                    LEFT JOIN parts_list_statuses s ON s.id = pl.status_id
                    LEFT JOIN contacts ct ON ct.id = pl.contact_id
                    LEFT JOIN projects p ON p.id = pl.project_id
                    WHERE pl.id = ?
                """, (list_id,)).fetchone()

                if not header:
                    return "Parts list not found", 404

                # Get all lines with their chosen costs and quote line data
                lines = _execute_with_cursor(cur, """
                    SELECT 
                        pll.id,
                        pll.line_number,
                        pll.parent_line_id,
                        pll.line_type,
                        pll.customer_part_number,
                        parent.customer_part_number as parent_customer_part_number,
                        pll.base_part_number,
                        pll.quantity,
                        pll.chosen_qty,
                        COALESCE(pll.chosen_qty, pll.quantity) as effective_quantity,
                      pll.chosen_supplier_id,
                      pll.chosen_cost,
                      pll.chosen_price,
                      pll.chosen_currency_id,
                      pll.chosen_lead_days,
                      pll.internal_notes,
                      pll.chosen_source_type,
                      pll.chosen_source_reference,
                      s.name as chosen_supplier_name,
                        s.delivery_cost as supplier_delivery_cost,
                        c.currency_code as chosen_currency_code,
                        c.symbol as chosen_currency_symbol,
                        c.exchange_rate_to_base as chosen_currency_rate,

                        -- Quote line data (if exists)
                        cql.id as quote_line_id,
                        cql.display_part_number,
                        cql.quoted_part_number,
                        cql.base_cost_gbp,
                        cql.delivery_per_unit,
                        cql.delivery_per_line,
                        cql.margin_percent,
                        cql.quote_price_gbp,
                        cql.lead_days,
                        cql.is_no_bid,
                        cql.quoted_status,
                        cql.quoted_on,
                        cql.line_notes,
                        cql.standard_condition,
                        cql.standard_certs,

                        -- Check if we have a supplier quote for this line
                        (SELECT sql.quoted_part_number 
                         FROM parts_list_supplier_quote_lines sql
                         JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                         WHERE sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                           AND sq.supplier_id = pll.chosen_supplier_id
                         LIMIT 1) as supplier_quoted_part_number,
                        (
                            SELECT sql.condition_code
                            FROM parts_list_supplier_quote_lines sql
                            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                            WHERE sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                              AND sql.is_no_bid = FALSE
                              AND sql.condition_code IS NOT NULL
                              AND TRIM(sql.condition_code) != ''
                            ORDER BY sq.quote_date DESC,
                                     sql.date_modified DESC,
                                     sql.id DESC
                            LIMIT 1
                        ) AS supplier_condition_code,
                        CASE
                            WHEN pll.chosen_source_type = 'quote'
                                 AND pll.chosen_source_reference IS NOT NULL THEN (
                                SELECT sql.certifications
                                FROM parts_list_supplier_quote_lines sql
                                JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                                WHERE CAST(sql.id AS TEXT) = pll.chosen_source_reference
                                  AND sql.is_no_bid = FALSE
                                  AND sql.certifications IS NOT NULL
                                  AND TRIM(sql.certifications) != ''
                                LIMIT 1
                            )
                            ELSE (
                                SELECT sql.certifications
                                FROM parts_list_supplier_quote_lines sql
                                JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                                WHERE sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                                  AND sql.is_no_bid = FALSE
                                  AND sql.certifications IS NOT NULL
                                  AND TRIM(sql.certifications) != ''
                                ORDER BY sq.quote_date DESC,
                                         sql.date_modified DESC,
                                         sql.id DESC
                                LIMIT 1
                            )
                        END AS supplier_certifications,
                        s.standard_condition AS supplier_standard_condition,
                        s.standard_certs AS supplier_standard_certs,

                        -- Stock availability
                        (SELECT COALESCE(SUM(sm.available_quantity), 0)
                         FROM stock_movements sm
                         WHERE sm.base_part_number = pll.base_part_number
                           AND sm.movement_type = 'IN'
                           AND sm.available_quantity > 0) as stock_quantity

                    FROM parts_list_lines pll
                    LEFT JOIN parts_list_lines parent ON parent.id = pll.parent_line_id
                    LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
                    LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                    LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                    WHERE pll.parts_list_id = ?
                    ORDER BY pll.line_number ASC
                """, (list_id,)).fetchall()

                # Get BOM guide prices for each line
                for line in lines:
                    line_dict = dict(line)
                    has_parent_part = line_dict.get('parent_customer_part_number')
                    is_alt_line = line_dict.get('line_type') == 'alternate' or line_dict.get('parent_line_id')
                    line_dict['requested_part_number'] = (
                        line_dict['parent_customer_part_number']
                        if is_alt_line and has_parent_part
                        else line_dict.get('customer_part_number')
                    )

                    # Get highest BOM guide price for this part
                    bom_data = _execute_with_cursor(cur, """
                        SELECT 
                            bl.guide_price,
                            bh.name as bom_name
                        FROM bom_lines bl
                        JOIN bom_headers bh ON bl.bom_header_id = bh.id
                        WHERE bl.base_part_number = ?
                        ORDER BY bl.guide_price DESC
                        LIMIT 1
                    """, (line['base_part_number'],)).fetchone()

                    # Add BOM guide price to line
                    if bom_data:
                        line_dict['bom_guide_price'] = bom_data['guide_price']
                        line_dict['bom_name'] = bom_data['bom_name']
                    else:
                        line_dict['bom_guide_price'] = None
                        line_dict['bom_name'] = None

                    # Default condition/certs: quote override -> supplier quote -> supplier standard
                    line_dict['standard_condition'] = (
                        (line_dict.get('standard_condition') or '').strip()
                        or (line_dict.get('supplier_condition_code') or '').strip()
                        or (line_dict.get('supplier_standard_condition') or '').strip()
                    )
                    line_dict['standard_certs'] = (
                        (line_dict.get('standard_certs') or '').strip()
                        or (line_dict.get('supplier_certifications') or '').strip()
                        or (line_dict.get('supplier_standard_certs') or '').strip()
                    )

                    # Auto-set display_part_number if not already set
                    if not line_dict['display_part_number']:
                        # Use supplier quoted P/N if different, otherwise customer P/N
                        if line_dict['supplier_quoted_part_number'] and \
                                line_dict['supplier_quoted_part_number'] != line_dict['customer_part_number']:
                            line_dict['suggested_display_pn'] = line_dict['supplier_quoted_part_number']
                        else:
                            line_dict['suggested_display_pn'] = line_dict['customer_part_number']
                    else:
                        line_dict['suggested_display_pn'] = line_dict['display_part_number']

                    lines_with_bom.append(line_dict)

                # Get all currencies for the page
                currencies = _execute_with_cursor(cur, """
                    SELECT id, currency_code, symbol, exchange_rate_to_base as exchange_rate_to_eur
                    FROM currencies
                    ORDER BY id ASC
                """).fetchall()


        # Calculate stats - now using quoted_status
        total_lines = len(lines_with_bom)
        lines_with_cost = sum(1 for l in lines_with_bom if l['chosen_cost'] is not None)
        lines_created = sum(1 for l in lines_with_bom if (l.get('quoted_status') or 'created') == 'created')
        lines_in_progress = sum(1 for l in lines_with_bom if l.get('quoted_status') == 'in_progress')
        lines_quoted = sum(1 for l in lines_with_bom if l.get('quoted_status') == 'quoted')
        lines_no_bid = sum(1 for l in lines_with_bom if l.get('quoted_status') == 'no_bid')

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Parts Lists', url_for('parts_list.view_parts_lists')),
            (header['name'], url_for('parts_list.view_parts_list', list_id=list_id)),
            ('Customer Quote', None)
        ]

        contact_name = header.get('contact_name') if isinstance(header, dict) else None
        contact_email = header.get('contact_email') if isinstance(header, dict) else None
        contact_first_name = contact_name.split()[0] if contact_name else ''
        current_user_name = None
        if current_user and getattr(current_user, "is_authenticated", False):
            current_user_name = (getattr(current_user, "username", "") or "").replace('_', ' ').title()

        return render_template('customer_quote.html',
                               list_id=list_id,
                               list_name=header['name'],
                               list_notes=header.get('notes'),
                               customer_name=header['customer_name'],
                               nav_is_pinned=bool(header.get('is_pinned')),
                               project_id=header.get('project_id'),
                               project_name=header.get('project_name'),
                               status_id=header.get('status_id'),
                               status_name=header.get('status_name'),
                               customer_system_code=header.get('customer_system_code'),
                               customer_currency_id=header.get('customer_currency_id'),
                               contact_name=contact_name,
                               contact_email=contact_email,
                               contact_first_name=contact_first_name,
                               current_user_name=current_user_name,
                               lines=lines_with_bom,
                               currencies=[dict(c) for c in currencies],
                               total_lines=total_lines,
                               lines_with_cost=lines_with_cost,
                               lines_created=lines_created,
                               lines_in_progress=lines_in_progress,
                               lines_quoted=lines_quoted,
                               lines_no_bid=lines_no_bid,
                               breadcrumbs=breadcrumbs)

    except Exception as e:
        logging.exception(e)
        return str(e), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/calculate-base-costs', methods=['POST'])
def calculate_base_costs(list_id):
    """
    Calculate and store base costs (in GBP) for all lines with chosen costs
    Only recalculates lines that are NOT in 'quoted' status (to preserve quoted prices)
    """
    try:
        created_count = 0
        updated_count = 0
        skipped_count = 0
        with db_cursor(commit=True) as cur:
            lines = _execute_with_cursor(cur, """
                SELECT 
                    pll.id,
                    pll.chosen_supplier_id,
                    pll.chosen_cost,
                    pll.chosen_currency_id,
                    pll.chosen_source_type,
                    pll.chosen_source_reference,
                    c.exchange_rate_to_base as exchange_rate_to_eur,
                    cql.id as quote_line_id,
                    cql.quoted_status
                FROM parts_list_lines pll
                LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pll.parts_list_id = ?
                  AND pll.chosen_cost IS NOT NULL
            """, (list_id,)).fetchall()

            for line in lines:
                if line['quoted_status'] == 'quoted':
                    skipped_count += 1
                    continue

                chosen_cost = line['chosen_cost'] or 0
                exchange_rate = line['exchange_rate_to_eur'] or 1

                if exchange_rate != 0:
                    base_cost_gbp = chosen_cost / exchange_rate
                else:
                    base_cost_gbp = chosen_cost

                metadata = _get_supplier_quote_metadata(
                    cur,
                    line['id'],
                    line['chosen_supplier_id'],
                    source_type=line['chosen_source_type'],
                    source_reference=line['chosen_source_reference']
                )

                if line['quote_line_id']:
                    _execute_with_cursor(cur, """
                        UPDATE customer_quote_lines 
                        SET base_cost_gbp = ?,
                            standard_condition = ?,
                            standard_certs = ?,
                            manufacturer = ?,
                            date_modified = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (base_cost_gbp, metadata['condition'], metadata['certs'], metadata['manufacturer'], line['quote_line_id']))
                    updated_count += 1
                else:
                    condition, certs = metadata['condition'], metadata['certs']
                    _execute_with_cursor(cur, """
                        INSERT INTO customer_quote_lines 
                        (parts_list_line_id, base_cost_gbp, delivery_per_unit, delivery_per_line, margin_percent, quote_price_gbp, quoted_status, standard_condition, standard_certs)
                        VALUES (?, ?, 0, 0, 0, ?, 'created', ?, ?)
                    """, (line['id'], base_cost_gbp, base_cost_gbp, condition, certs))
                    created_count += 1

        return jsonify(success=True, created=created_count, updated=updated_count, skipped=skipped_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/calculate-delivery-costs', methods=['POST'])
def calculate_delivery_costs(list_id):
    """
    Calculate delivery cost per line based on supplier delivery costs
    Creates quote lines if they don't exist
    """
    try:
        skipped_count = 0
        updated_count = 0
        created_count = 0
        with db_cursor(commit=True) as cur:
            lines = _execute_with_cursor(cur, """
                SELECT 
                    pll.id as parts_list_line_id,
                    pll.chosen_supplier_id,
                    pll.quantity,
                    pll.chosen_qty,
                    pll.chosen_lead_days,
                    pll.chosen_cost,
                    pll.chosen_currency_id,
                    pll.chosen_source_type,
                    pll.chosen_source_reference,
                    COALESCE(pll.chosen_qty, pll.quantity) as effective_quantity,
                    c.exchange_rate_to_base as exchange_rate_to_eur,
                    s.delivery_cost as supplier_delivery_cost,
                    s.buffer as supplier_buffer,
                    cql.id as quote_line_id,
                    cql.base_cost_gbp,
                    cql.margin_percent,
                    cql.quoted_status
                FROM parts_list_lines pll
                LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
                LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pll.parts_list_id = ?
                  AND pll.chosen_supplier_id IS NOT NULL
                  AND COALESCE(pll.chosen_qty, pll.quantity) > 0
            """, (list_id,)).fetchall()

            supplier_lines = {}

            for line in lines:
                if line['quoted_status'] == 'quoted':
                    skipped_count += 1
                    continue

                supplier_id = line['chosen_supplier_id']
                supplier_entry = supplier_lines.setdefault(supplier_id, {
                    'delivery_cost': line['supplier_delivery_cost'] or 0,
                    'buffer': line['supplier_buffer'] or 0,
                    'lines': []
                })
                supplier_entry['lines'].append(line)

            for supplier_id, data in supplier_lines.items():
                total_delivery = data['delivery_cost']
                supplier_buffer = data['buffer']
                lines_count = len(data['lines'])

                if lines_count > 0 and total_delivery > 0:
                    delivery_per_line = total_delivery / lines_count

                    for line in data['lines']:
                        effective_qty = line['effective_quantity']
                        delivery_per_unit = delivery_per_line / effective_qty if effective_qty > 0 else 0

                        supplier_lead_days = line['chosen_lead_days'] or 0
                        customer_lead_days = supplier_lead_days + supplier_buffer

                        metadata = _get_supplier_quote_metadata(
                            cur,
                            line['parts_list_line_id'],
                            supplier_id,
                            source_type=line['chosen_source_type'],
                            source_reference=line['chosen_source_reference']
                        )

                        if not line['quote_line_id']:
                            chosen_cost = line['chosen_cost'] or 0
                            exchange_rate = line['exchange_rate_to_eur'] or 1
                            base_cost_gbp = chosen_cost / exchange_rate if exchange_rate != 0 else chosen_cost
                            margin = 0
                            quote_price = base_cost_gbp + delivery_per_unit
                            condition, certs = metadata['condition'], metadata['certs']

                            _execute_with_cursor(cur, """
                                INSERT INTO customer_quote_lines 
                                (parts_list_line_id, base_cost_gbp, delivery_per_unit, delivery_per_line, 
                                 margin_percent, quote_price_gbp, lead_days, quoted_status, standard_condition, standard_certs, manufacturer)
                                VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
                            """, (line['parts_list_line_id'], base_cost_gbp, delivery_per_unit, delivery_per_line,
                                  margin, quote_price, customer_lead_days, condition, certs, metadata['manufacturer']))
                            created_count += 1
                        else:
                            base_cost = line['base_cost_gbp'] or 0
                            margin = line['margin_percent'] or 0

                            if margin > 0 and margin < 100 and base_cost > 0:
                                price_before_delivery = base_cost / (1 - margin / 100)
                            else:
                                price_before_delivery = base_cost

                            quote_price = price_before_delivery + delivery_per_unit

                            _execute_with_cursor(cur, """
                                UPDATE customer_quote_lines
                                SET delivery_per_unit = ?,
                                    delivery_per_line = ?,
                                    lead_days = ?,
                                    quote_price_gbp = ?,
                                    standard_condition = ?,
                                    standard_certs = ?,
                                    manufacturer = ?,
                                    date_modified = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (
                                delivery_per_unit, delivery_per_line, customer_lead_days, quote_price,
                                metadata['condition'], metadata['certs'], metadata['manufacturer'], line['quote_line_id']))
                            updated_count += 1

        return jsonify(success=True, updated_count=updated_count, created_count=created_count, skipped=skipped_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/line/<int:line_id>/calculate-base-cost', methods=['POST'])
def calculate_base_cost_line(list_id, line_id):
    """
    Calculate and store base cost (GBP) for a single line.
    """
    try:
        with db_cursor(commit=True) as cur:
            line = _execute_with_cursor(cur, """
                SELECT 
                    pll.id,
                    pll.chosen_supplier_id,
                    pll.chosen_cost,
                    pll.chosen_currency_id,
                    pll.chosen_source_type,
                    pll.chosen_source_reference,
                    c.exchange_rate_to_base as exchange_rate_to_eur,
                    cql.id as quote_line_id,
                    cql.quote_price_gbp,
                    cql.margin_percent,
                    cql.delivery_per_line,
                    cql.quoted_status
                FROM parts_list_lines pll
                LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pll.id = ? AND pll.parts_list_id = ?
            """, (line_id, list_id)).fetchone()

            if not line:
                return jsonify(success=False, message="Line not found"), 404

            if line['quoted_status'] == 'quoted':
                return jsonify(success=True, skipped=True, message="Line is quoted")

            if line['chosen_cost'] is None:
                return jsonify(success=False, message="No chosen cost set for this line"), 400

            exchange_rate = line['exchange_rate_to_eur'] or 1
            base_cost_gbp = line['chosen_cost'] / exchange_rate if exchange_rate != 0 else line['chosen_cost']

            metadata = _get_supplier_quote_metadata(
                cur,
                line_id,
                line['chosen_supplier_id'],
                source_type=line['chosen_source_type'],
                source_reference=line['chosen_source_reference']
            )

            update_quote_price = False
            if line['quote_line_id']:
                _execute_with_cursor(cur, """
                    UPDATE customer_quote_lines 
                    SET base_cost_gbp = ?,
                        standard_condition = ?,
                        standard_certs = ?,
                        manufacturer = ?,
                        date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (base_cost_gbp, metadata['condition'], metadata['certs'], metadata['manufacturer'], line['quote_line_id']))
                quote_price = line['quote_price_gbp']
                margin_percent = line['margin_percent'] or 0
                delivery_per_line = line['delivery_per_line'] or 0
            else:
                condition, certs = metadata['condition'], metadata['certs']
                _execute_with_cursor(cur, """
                    INSERT INTO customer_quote_lines 
                    (parts_list_line_id, base_cost_gbp, delivery_per_unit, delivery_per_line,
                     margin_percent, quote_price_gbp, quoted_status, standard_condition, standard_certs)
                    VALUES (?, ?, 0, 0, 0, ?, 'created', ?, ?)
                """, (line_id, base_cost_gbp, base_cost_gbp, condition, certs))
                quote_price = base_cost_gbp
                margin_percent = 0
                delivery_per_line = 0
                update_quote_price = True

        return jsonify(
            success=True,
            base_cost_gbp=base_cost_gbp,
            quote_price_gbp=quote_price,
            margin_percent=margin_percent,
            delivery_per_line=delivery_per_line,
            quoted_status=line['quoted_status'] or 'created',
            update_quote_price=update_quote_price
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/line/<int:line_id>/calculate-delivery', methods=['POST'])
def calculate_delivery_line(list_id, line_id):
    """
    Calculate and store delivery costs for a single line.
    """
    try:
        with db_cursor(commit=True) as cur:
            line = _execute_with_cursor(cur, """
                SELECT 
                    pll.id as parts_list_line_id,
                    pll.chosen_supplier_id,
                    pll.quantity,
                    pll.chosen_qty,
                    pll.chosen_lead_days,
                    pll.chosen_cost,
                    pll.chosen_currency_id,
                    pll.chosen_source_type,
                    pll.chosen_source_reference,
                    COALESCE(pll.chosen_qty, pll.quantity) as effective_quantity,
                    c.exchange_rate_to_base as exchange_rate_to_eur,
                    s.delivery_cost as supplier_delivery_cost,
                    s.buffer as supplier_buffer,
                    cql.id as quote_line_id,
                    cql.base_cost_gbp,
                    cql.margin_percent,
                    cql.quoted_status
                FROM parts_list_lines pll
                LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
                LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pll.id = ? AND pll.parts_list_id = ?
            """, (line_id, list_id)).fetchone()

            if not line:
                return jsonify(success=False, message="Line not found"), 404

            if line['quoted_status'] == 'quoted':
                return jsonify(success=True, skipped=True, message="Line is quoted")

            supplier_id = line['chosen_supplier_id']
            if not supplier_id:
                return jsonify(success=False, message="No supplier selected for this line"), 400

            eligible = _execute_with_cursor(cur, """
                SELECT COUNT(*) as line_count
                FROM parts_list_lines pll
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pll.parts_list_id = ?
                  AND pll.chosen_supplier_id = ?
                  AND COALESCE(pll.chosen_qty, pll.quantity) > 0
                  AND (cql.quoted_status IS NULL OR cql.quoted_status != 'quoted')
            """, (list_id, supplier_id)).fetchone()

            line_count = eligible['line_count'] if eligible else 0
            total_delivery = line['supplier_delivery_cost'] or 0

            delivery_per_line = (total_delivery / line_count) if line_count > 0 and total_delivery > 0 else 0
            effective_qty = line['effective_quantity'] or 0
            delivery_per_unit = delivery_per_line / effective_qty if effective_qty > 0 else 0

            supplier_lead_days = line['chosen_lead_days'] or 0
            supplier_buffer = line['supplier_buffer'] or 0
            customer_lead_days = supplier_lead_days + supplier_buffer

            metadata = _get_supplier_quote_metadata(
                cur,
                line['parts_list_line_id'],
                supplier_id,
                source_type=line['chosen_source_type'],
                source_reference=line['chosen_source_reference']
            )

            if not line['quote_line_id']:
                chosen_cost = line['chosen_cost'] or 0
                exchange_rate = line['exchange_rate_to_eur'] or 1
                base_cost_gbp = chosen_cost / exchange_rate if exchange_rate != 0 else chosen_cost
                margin = 0
                quote_price = base_cost_gbp + delivery_per_unit
                condition, certs = metadata['condition'], metadata['certs']

                _execute_with_cursor(cur, """
                    INSERT INTO customer_quote_lines 
                    (parts_list_line_id, base_cost_gbp, delivery_per_unit, delivery_per_line,
                     margin_percent, quote_price_gbp, lead_days, quoted_status, standard_condition, standard_certs, manufacturer)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
                """, (
                    line['parts_list_line_id'],
                    base_cost_gbp,
                    delivery_per_unit,
                    delivery_per_line,
                    margin,
                    quote_price,
                    customer_lead_days,
                    condition,
                    certs,
                    metadata['manufacturer']
                ))
            else:
                base_cost = line['base_cost_gbp'] or 0
                margin = line['margin_percent'] or 0

                if margin > 0 and margin < 100 and base_cost > 0:
                    price_before_delivery = base_cost / (1 - margin / 100)
                else:
                    price_before_delivery = base_cost

                quote_price = price_before_delivery + delivery_per_unit

                _execute_with_cursor(cur, """
                    UPDATE customer_quote_lines
                    SET delivery_per_unit = ?,
                        delivery_per_line = ?,
                        lead_days = ?,
                        quote_price_gbp = ?,
                        standard_condition = ?,
                        standard_certs = ?,
                        manufacturer = ?,
                        date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    delivery_per_unit,
                    delivery_per_line,
                    customer_lead_days,
                    quote_price,
                    metadata['condition'],
                    metadata['certs'],
                    metadata['manufacturer'],
                    line['quote_line_id']
                ))

        base_cost_value = line['base_cost_gbp'] if line['quote_line_id'] else base_cost_gbp
        margin_value = line['margin_percent'] or 0

        return jsonify(
            success=True,
            delivery_per_line=delivery_per_line,
            delivery_per_unit=delivery_per_unit,
            quote_price_gbp=quote_price,
            lead_days=customer_lead_days,
            base_cost_gbp=base_cost_value,
            margin_percent=margin_value,
            quoted_status=line['quoted_status'] or 'created'
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/line/<int:line_id>/calculate-margin', methods=['POST'])
def calculate_margin_line(list_id, line_id):
    """
    Calculate and store quote price from the margin percent for a single line.
    """
    try:
        data = request.get_json(force=True)
        margin_percent = data.get('margin_percent')

        if margin_percent is None:
            return jsonify(success=False, message="margin_percent is required"), 400

        margin_percent = float(margin_percent)
        if margin_percent < 0 or margin_percent >= 100:
            return jsonify(success=False, message="margin_percent must be between 0 and 100"), 400

        with db_cursor(commit=True) as cur:
            line = _execute_with_cursor(cur, """
                SELECT 
                    pll.id,
                    pll.chosen_cost,
                    pll.chosen_currency_id,
                    pll.chosen_supplier_id,
                    c.exchange_rate_to_base as exchange_rate_to_eur,
                    cql.id as quote_line_id,
                    cql.base_cost_gbp,
                    cql.delivery_per_unit,
                    cql.delivery_per_line,
                    cql.quoted_status
                FROM parts_list_lines pll
                LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pll.id = ? AND pll.parts_list_id = ?
            """, (line_id, list_id)).fetchone()

            if not line:
                return jsonify(success=False, message="Line not found"), 404

            if line['quoted_status'] == 'quoted':
                return jsonify(success=True, skipped=True, message="Line is quoted")

            if not line['quote_line_id']:
                chosen_cost = line['chosen_cost'] or 0
                exchange_rate = line['exchange_rate_to_eur'] or 1
                base_cost_gbp = float(chosen_cost / exchange_rate) if exchange_rate != 0 else float(chosen_cost)
                delivery_per_unit = 0.0
                delivery_per_line = 0.0

                price_before_delivery = base_cost_gbp / (1 - margin_percent / 100) if margin_percent > 0 else base_cost_gbp
                quote_price = price_before_delivery + delivery_per_unit
                condition, certs = _get_condition_and_certs(cur, line_id, line['chosen_supplier_id'])

                _execute_with_cursor(cur, """
                    INSERT INTO customer_quote_lines 
                    (parts_list_line_id, base_cost_gbp, delivery_per_unit, delivery_per_line,
                     margin_percent, quote_price_gbp, quoted_status, standard_condition, standard_certs)
                    VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?)
                """, (
                    line_id,
                    base_cost_gbp,
                    delivery_per_unit,
                    delivery_per_line,
                    margin_percent,
                    quote_price,
                    condition,
                    certs
                ))
            else:
                base_cost_gbp = float(line['base_cost_gbp'] or 0)
                delivery_per_unit = float(line['delivery_per_unit'] or 0)
                delivery_per_line = float(line['delivery_per_line'] or 0)

                price_before_delivery = base_cost_gbp / (1 - margin_percent / 100) if margin_percent > 0 else base_cost_gbp
                quote_price = price_before_delivery + delivery_per_unit

                _execute_with_cursor(cur, """
                    UPDATE customer_quote_lines
                    SET margin_percent = ?,
                        quote_price_gbp = ?,
                        date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (margin_percent, quote_price, line['quote_line_id']))

        return jsonify(
            success=True,
            margin_percent=margin_percent,
            quote_price_gbp=quote_price,
            base_cost_gbp=base_cost_gbp,
            delivery_per_line=delivery_per_line,
            quoted_status=line['quoted_status'] or 'created'
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/bulk-update', methods=['POST'])
def bulk_update_quote_lines(list_id):
    """
    Bulk update multiple quote lines at once (for table editing)
    Also handles chosen_qty updates to parts_list_lines
    Auto-updates quoted_status based on changes
    """
    try:
        data = request.get_json(force=True)
        updates = data.get('updates', [])

        logging.info(f"=== BULK UPDATE START for list {list_id} ===")
        logging.info(f"Received {len(updates)} updates")
        if updates:
            logging.info(f"First update sample: {updates[0]}")

        if not updates:
            return jsonify(success=False, message="No updates provided"), 400

        updated_count = 0
        with db_cursor(commit=True) as cur:
            for update in updates:
                parts_list_line_id = update.get('parts_list_line_id')
                if not parts_list_line_id:
                    continue

                line = _execute_with_cursor(cur, """
                    SELECT 
                        pll.id,
                        pll.base_part_number,
                        pll.customer_part_number,
                        cql.id as quote_line_id, 
                        cql.base_cost_gbp,
                        cql.display_part_number,
                        cql.quoted_part_number,
                        cql.quoted_status
                    FROM parts_list_lines pll
                    LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                    WHERE pll.id = ? AND pll.parts_list_id = ?
                """, (parts_list_line_id, list_id)).fetchone()

                if not line:
                    logging.warning(f"Line {parts_list_line_id} not found or doesn't belong to list {list_id}")
                    continue

                current_status = line['quoted_status'] or 'created'
                requested_status = update.get('quoted_status')
                is_locked = current_status == 'quoted' and (requested_status is None or requested_status == 'quoted')

                if 'chosen_qty' in update and not is_locked:
                    _execute_with_cursor(cur, """
                        UPDATE parts_list_lines
                        SET chosen_qty = ?
                        WHERE id = ?
                    """, (update['chosen_qty'], parts_list_line_id))
                    logging.debug(f"Updated chosen_qty for line {parts_list_line_id}")

                if not line['quote_line_id']:
                    chosen = _execute_with_cursor(cur, """
                        SELECT 
                            pll.chosen_cost,
                            pll.chosen_currency_id,
                            c.exchange_rate_to_base as exchange_rate_to_eur,
                            pll.chosen_supplier_id
                        FROM parts_list_lines pll
                        LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                        WHERE pll.id = ?
                    """, (parts_list_line_id,)).fetchone()

                    if chosen and chosen['chosen_cost']:
                        exchange_rate = chosen['exchange_rate_to_eur'] or 1
                        base_cost_gbp = chosen['chosen_cost'] / exchange_rate if exchange_rate != 0 else chosen['chosen_cost']
                    else:
                        base_cost_gbp = 0

                    condition, certs = _get_condition_and_certs(cur, parts_list_line_id,
                                                                chosen['chosen_supplier_id'] if chosen else None)

                    insert_query = _with_returning_clause("""
                        INSERT INTO customer_quote_lines 
                        (parts_list_line_id, base_cost_gbp, delivery_per_unit, delivery_per_line, margin_percent, quote_price_gbp, quoted_status, standard_condition, standard_certs, manufacturer)
                        VALUES (?, ?, 0, 0, 0, ?, 'created', ?, ?, ?)
                    """)
                    _execute_with_cursor(
                        cur,
                        insert_query,
                        (parts_list_line_id, base_cost_gbp, base_cost_gbp, condition, certs, update.get('manufacturer'))
                    )
                    quote_line_id = _last_inserted_id(cur)
                    logging.info(f"Created new quote line {quote_line_id} for parts_list_line {parts_list_line_id}")
                else:
                    quote_line_id = line['quote_line_id']
                    base_cost_gbp = line['base_cost_gbp']

                fields = []
                params = []

                new_status = current_status if is_locked else None
                is_no_bid = update.get('is_no_bid', 0)
                quote_price = update.get('quote_price_gbp', 0)

                if not is_locked:
                    if is_no_bid:
                        new_status = 'no_bid'
                    elif quote_price and float(quote_price) > 0:
                        new_status = 'quoted'
                    if 'quoted_status' in update:
                        new_status = update['quoted_status']
                elif requested_status:
                    new_status = requested_status

                if new_status:
                    fields.append("quoted_status = ?")
                    params.append(new_status)
                    logging.debug(f"Line {parts_list_line_id}: quoted_status = {new_status}")
                    if new_status != current_status:
                        if new_status == 'quoted':
                            fields.append("quoted_on = CURRENT_TIMESTAMP")
                        elif current_status == 'quoted':
                            fields.append("quoted_on = NULL")

                if new_status == 'quoted' and not is_no_bid:
                    part_value = (
                        update.get('quoted_part_number')
                        or update.get('display_part_number')
                        or line['quoted_part_number']
                        or line['display_part_number']
                        or line['customer_part_number']
                        or line['base_part_number']
                    )
                    base_part_number = create_base_part_number(part_value) if part_value else line['base_part_number']
                    if base_part_number:
                        _ensure_part_number(cur, base_part_number, part_value)

                if 'margin_percent' in update and not is_locked:
                    fields.append("margin_percent = ?")
                    params.append(float(update['margin_percent'] or 0))
                    logging.debug(f"Line {parts_list_line_id}: margin_percent = {update['margin_percent']}")

                if 'quote_price_gbp' in update and not is_locked:
                    fields.append("quote_price_gbp = ?")
                    params.append(float(update['quote_price_gbp'] or 0))
                    logging.debug(f"Line {parts_list_line_id}: quote_price_gbp = {update['quote_price_gbp']}")

                if 'delivery_per_unit' in update and not is_locked:
                    fields.append("delivery_per_unit = ?")
                    params.append(float(update['delivery_per_unit'] or 0))
                    logging.debug(f"Line {parts_list_line_id}: delivery_per_unit = {update['delivery_per_unit']}")

                if 'delivery_per_line' in update and not is_locked:
                    fields.append("delivery_per_line = ?")
                    params.append(float(update['delivery_per_line'] or 0))
                    logging.debug(f"Line {parts_list_line_id}: delivery_per_line = {update['delivery_per_line']}")

                if 'lead_days' in update:
                    fields.append("lead_days = ?")
                    params.append(int(update['lead_days'] or 0))
                    logging.debug(f"Line {parts_list_line_id}: lead_days = {update['lead_days']}")

                for field in ['display_part_number', 'quoted_part_number', 'line_notes', 'standard_condition', 'standard_certs']:
                    if field in update:
                        fields.append(f"{field} = ?")
                        params.append(update[field])
                if 'manufacturer' in update:
                    fields.append("manufacturer = ?")
                    params.append(update['manufacturer'])

                if 'is_no_bid' in update and not is_locked:
                    fields.append("is_no_bid = ?")
                    params.append(update['is_no_bid'])

                if fields:
                    params.append(quote_line_id)
                    sql = f"""
                        UPDATE customer_quote_lines
                        SET {', '.join(fields)}, date_modified = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """
                    logging.debug(f"Executing SQL: {sql} with params: {params}")
                    _execute_with_cursor(cur, sql, params)
                    updated_count += 1

        logging.info(f"=== BULK UPDATE COMPLETE: {updated_count} lines updated ===")
        return jsonify(success=True, updated=updated_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/toggle-no-bid', methods=['POST'])
def toggle_no_bid(list_id):
    """
    Toggle no-bid status for a quote line
    """
    try:
        data = request.get_json(force=True)
        parts_list_line_id = data.get('parts_list_line_id')
        is_no_bid = data.get('is_no_bid', False)

        if not parts_list_line_id:
            return jsonify(success=False, message="parts_list_line_id required"), 400

        quoted_status = 'no_bid' if is_no_bid else 'created'

        with db_cursor(commit=True) as cur:
            quote_line = _execute_with_cursor(cur, """
                SELECT cql.id
                FROM customer_quote_lines cql
                JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
                WHERE cql.parts_list_line_id = ? AND pll.parts_list_id = ?
            """, (parts_list_line_id, list_id)).fetchone()

            if quote_line:
                _execute_with_cursor(cur, """
                    UPDATE customer_quote_lines
                    SET is_no_bid = ?, quoted_status = ?, quoted_on = NULL, date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (is_no_bid, quoted_status, quote_line['id']))
            else:
                _execute_with_cursor(cur, """
                    INSERT INTO customer_quote_lines 
                    (parts_list_line_id, is_no_bid, quoted_status, base_cost_gbp, delivery_per_unit, delivery_per_line, margin_percent, quote_price_gbp)
                    VALUES (?, ?, ?, 0, 0, 0, 0, 0)
                """, (parts_list_line_id, is_no_bid, quoted_status))

        return jsonify(success=True)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/summary', methods=['GET'])
def quote_summary(list_id):
    """
    Get summary statistics for the customer quote
    """
    try:
        summary = db_execute("""
            SELECT 
                COUNT(*) as total_lines,
                COUNT(CASE WHEN COALESCE(cql.quoted_status, 'created') = 'created' THEN 1 END) as created_lines,
                COUNT(CASE WHEN cql.quoted_status = 'in_progress' THEN 1 END) as in_progress_lines,
                COUNT(CASE WHEN cql.quoted_status = 'quoted' THEN 1 END) as quoted_lines,
                COUNT(CASE WHEN cql.quoted_status = 'no_bid' THEN 1 END) as no_bid_lines,
                COALESCE(SUM(CASE WHEN COALESCE(cql.quoted_status, 'created') != 'no_bid' 
                    THEN COALESCE(cql.base_cost_gbp, 0) * COALESCE(pll.chosen_qty, pll.quantity) 
                    ELSE 0 END), 0) as total_cost_gbp,
                COALESCE(SUM(CASE WHEN cql.quoted_status = 'quoted' 
                    THEN COALESCE(cql.quote_price_gbp, 0) * COALESCE(pll.chosen_qty, pll.quantity) 
                    ELSE 0 END), 0) as total_quote_gbp,
                COALESCE(AVG(CASE WHEN cql.quoted_status = 'quoted' AND cql.margin_percent > 0 THEN cql.margin_percent END), 0) as avg_margin
            FROM parts_list_lines pll
            LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
            WHERE pll.parts_list_id = ?
        """, (list_id,), fetch='one')

        return jsonify(success=True, summary=dict(summary))

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/mark-as-quoted', methods=['POST'])
def mark_as_quoted(list_id):
    """
    Mark all lines with margins and prices as 'quoted' status
    Called before generating email quote to lock in prices
    """
    try:
        with db_cursor(commit=True) as cur:
            rows = _execute_with_cursor(cur, """
                SELECT
                    pll.base_part_number,
                    pll.customer_part_number,
                    cql.display_part_number,
                    cql.quoted_part_number
                FROM parts_list_lines pll
                JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pll.parts_list_id = ?
                  AND cql.quoted_status IN ('created', 'in_progress')
                  AND cql.quote_price_gbp > 0
                  AND cql.margin_percent > 0
                  AND (cql.is_no_bid IS NULL OR cql.is_no_bid = 0)
            """, (list_id,)).fetchall()

            for row in rows or []:
                part_value = (
                    row.get('quoted_part_number')
                    or row.get('display_part_number')
                    or row.get('customer_part_number')
                    or row.get('base_part_number')
                )
                base_part_number = create_base_part_number(part_value) if part_value else row.get('base_part_number')
                if base_part_number:
                    _ensure_part_number(cur, base_part_number, part_value)

            result = _execute_with_cursor(cur, """
                UPDATE customer_quote_lines
                SET quoted_status = 'quoted',
                    quoted_on = CURRENT_TIMESTAMP,
                    date_modified = CURRENT_TIMESTAMP
                WHERE parts_list_line_id IN (
                    SELECT pll.id 
                    FROM parts_list_lines pll
                    WHERE pll.parts_list_id = ?
                )
                AND quoted_status IN ('created', 'in_progress')
                AND quote_price_gbp > 0
                AND margin_percent > 0
                AND (is_no_bid IS NULL OR is_no_bid = 0)
            """, (list_id,))
            marked_count = result.rowcount

        return jsonify(success=True, marked_count=marked_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/send-email', methods=['POST'])
def send_customer_quote_email(list_id):
    """
    Send customer quote email via Graph, optionally replying to a selected message.
    """
    try:
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return jsonify(success=False, message="You must be logged in to send emails"), 401

        data = request.get_json(force=True) or {}
        subject = (data.get("subject") or "").strip()
        body_html = data.get("body_html") or ""
        to_emails = _parse_recipient_list(data.get("to_emails"))
        cc_emails = _parse_recipient_list(data.get("cc_emails"))
        reply_to_message_id = (data.get("reply_to_message_id") or "").strip() or None

        if not body_html:
            return jsonify(success=False, message="Email body is required"), 400

        if not reply_to_message_id and not to_emails:
            return jsonify(success=False, message="Recipient email is required"), 400
        if not reply_to_message_id and not subject:
            return jsonify(success=False, message="Subject is required"), 400

        signature = _get_default_signature(current_user.id)
        if signature and signature.get("signature_html"):
            body_html = f"{body_html}{signature['signature_html']}"

        if reply_to_message_id:
            result = send_graph_reply(
                reply_to_message_id,
                body_html,
                reply_all=False,
                user_id=current_user.id,
            )
        else:
            result = send_graph_email(
                subject=subject,
                html_body=body_html,
                to_emails=to_emails,
                cc_emails=cc_emails or None,
                user_id=current_user.id,
            )

        if not result.get("success"):
            return jsonify(success=False, message=result.get("error", "Graph send failed")), 500

        return jsonify(success=True)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/bulk-apply-margin', methods=['POST'])
def bulk_apply_margin(list_id):
    """
    Apply margin percentage to multiple lines at once on the backend
    Creates quote lines if they don't exist
    """
    try:
        data = request.get_json(force=True)
        margin_percent = _parse_decimal(data.get('margin_percent'))
        scope = data.get('scope', 'all')  # 'all' or 'empty'

        if margin_percent is None or margin_percent < 0 or margin_percent >= 100:
            return jsonify(success=False, message="Invalid margin percentage"), 400

        if scope == 'empty':
            scope_condition = "AND (cql.margin_percent IS NULL OR cql.margin_percent = 0)"
        else:
            scope_condition = ""

        updated_count = 0
        created_count = 0
        with db_cursor(commit=True) as cur:
            lines = _execute_with_cursor(cur, f"""
                SELECT 
                    pll.id as parts_list_line_id,
                    pll.quantity,
                    pll.chosen_qty,
                    pll.chosen_cost,
                    pll.chosen_currency_id,
                    pll.chosen_source_type,
                    pll.chosen_source_reference,
                    c.exchange_rate_to_base as exchange_rate_to_eur,
                    cql.id as quote_line_id,
                    cql.base_cost_gbp,
                    cql.delivery_per_unit,
                    cql.quoted_status
                FROM parts_list_lines pll
                LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pll.parts_list_id = ?
                  AND pll.chosen_cost IS NOT NULL
                  AND pll.chosen_cost > 0
                  AND (cql.quoted_status IS NULL OR cql.quoted_status != 'no_bid')
                  AND (cql.quoted_status IS NULL OR cql.quoted_status != 'quoted')
                  {scope_condition}
            """, (list_id,)).fetchall()

            for line in lines:
                if not line['quote_line_id']:
                    chosen_cost = _to_decimal(line['chosen_cost'], Decimal('0'))
                    exchange_rate = _to_decimal(line['exchange_rate_to_eur'], Decimal('1'))
                    base_cost_gbp = chosen_cost / exchange_rate if exchange_rate != 0 else chosen_cost
                    delivery = Decimal('0')

                    margin_factor = Decimal('1') - (margin_percent / Decimal('100'))
                    price_before_delivery = (
                        base_cost_gbp / margin_factor if margin_percent > 0 else base_cost_gbp
                    )
                    quote_price = price_before_delivery + delivery

                    _execute_with_cursor(cur, """
                        INSERT INTO customer_quote_lines 
                        (parts_list_line_id, base_cost_gbp, delivery_per_unit, delivery_per_line, 
                         margin_percent, quote_price_gbp, quoted_status)
                        VALUES (?, ?, ?, ?, ?, ?, 'created')
                    """, (line['parts_list_line_id'], base_cost_gbp, delivery, 0, margin_percent, quote_price))
                    created_count += 1
                else:
                    base_cost = _to_decimal(line['base_cost_gbp'], Decimal('0'))
                    delivery = _to_decimal(line['delivery_per_unit'], Decimal('0'))

                    margin_factor = Decimal('1') - (margin_percent / Decimal('100'))
                    price_before_delivery = (
                        base_cost / margin_factor if margin_percent > 0 else base_cost
                    )
                    quote_price = price_before_delivery + delivery

                    _execute_with_cursor(cur, """
                        UPDATE customer_quote_lines
                        SET margin_percent = ?,
                            quote_price_gbp = ?,
                            date_modified = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (margin_percent, quote_price, line['quote_line_id']))

                    updated_count += 1

        return jsonify(success=True, updated_count=updated_count, created_count=created_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/bulk-margin-preview', methods=['POST'])
def bulk_margin_preview(list_id):
    """
    Preview what the bulk margin application will do
    Returns old and new prices for review
    """
    try:
        data = request.get_json(force=True)
        margin_percent = _parse_decimal(data.get('margin_percent'))
        scope = data.get('scope', 'all')

        if margin_percent is None or margin_percent < 0 or margin_percent >= 100:
            return jsonify(success=False, message="Invalid margin percentage"), 400

        if scope == 'empty':
            scope_condition = "AND (cql.margin_percent IS NULL OR cql.margin_percent = 0)"
        else:
            scope_condition = ""

        with db_cursor() as cur:
            lines = _execute_with_cursor(cur, f"""
            SELECT 
                pll.id as parts_list_line_id,
                pll.line_number,
                pll.customer_part_number,
                pll.quantity,
                pll.chosen_qty,
                COALESCE(pll.chosen_qty, pll.quantity) as effective_quantity,
                pll.chosen_cost,
                pll.chosen_currency_id,
                c.exchange_rate_to_base as exchange_rate_to_eur,
                c.currency_code,
                cql.id as quote_line_id,
                cql.base_cost_gbp,
                cql.delivery_per_unit,
                cql.margin_percent as old_margin,
                cql.quote_price_gbp as old_price,
                cql.quoted_status
            FROM parts_list_lines pll
            LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
            LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
            WHERE pll.parts_list_id = ?
              AND pll.chosen_cost IS NOT NULL
              AND pll.chosen_cost > 0
              AND (cql.quoted_status IS NULL OR cql.quoted_status != 'no_bid')
              AND (cql.quoted_status IS NULL OR cql.quoted_status != 'quoted')
              {scope_condition}
            ORDER BY pll.line_number
        """, (list_id,)).fetchall()

        # Calculate new prices
        preview_lines = []
        for line in lines:
            # Calculate base cost if needed
            if line['quote_line_id']:
                base_cost = _to_decimal(line['base_cost_gbp'], Decimal('0'))
                delivery = _to_decimal(line['delivery_per_unit'], Decimal('0'))
            else:
                chosen_cost = _to_decimal(line['chosen_cost'], Decimal('0'))
                exchange_rate = _to_decimal(line['exchange_rate_to_eur'], Decimal('1'))
                base_cost = chosen_cost / exchange_rate if exchange_rate != 0 else chosen_cost
                delivery = Decimal('0')

            # Calculate new price with new margin
            margin_factor = Decimal('1') - (margin_percent / Decimal('100'))
            price_before_delivery = (
                base_cost / margin_factor if margin_percent > 0 else base_cost
            )
            new_price = price_before_delivery + delivery

            old_price = _to_decimal(line['old_price'], Decimal('0'))
            old_margin = _to_decimal(line['old_margin'], Decimal('0'))

            effective_qty = line['effective_quantity']

            preview_lines.append({
                'parts_list_line_id': line['parts_list_line_id'],
                'line_number': line['line_number'],
                'customer_part_number': line['customer_part_number'],
                'quantity': effective_qty,
                'base_cost_gbp': base_cost,
                'delivery_per_unit': delivery,
                'old_margin': old_margin,
                'old_price': old_price,
                'old_line_total': old_price * effective_qty,
                'new_margin': margin_percent,
                'new_price': new_price,
                'new_line_total': new_price * effective_qty,
                'has_quote_line': bool(line['quote_line_id'])
            })

        return jsonify(success=True, lines=preview_lines)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/bulk-margin-apply', methods=['POST'])
def bulk_margin_apply(list_id):
    """
    Actually apply the bulk margin (after preview confirmation)
    """
    try:
        data = request.get_json(force=True)
        margin_percent = _parse_decimal(data.get('margin_percent'))
        scope = data.get('scope', 'all')

        if margin_percent is None or margin_percent < 0 or margin_percent >= 100:
            return jsonify(success=False, message="Invalid margin percentage"), 400

        if scope == 'empty':
            scope_condition = "AND (cql.margin_percent IS NULL OR cql.margin_percent = 0)"
        else:
            scope_condition = ""

        updated_count = 0
        created_count = 0
        with db_cursor(commit=True) as cur:
            lines = _execute_with_cursor(cur, f"""
                SELECT 
                    pll.id as parts_list_line_id,
                    pll.chosen_cost,
                    pll.chosen_currency_id,
                    pll.chosen_source_type,
                    pll.chosen_source_reference,
                    c.exchange_rate_to_base as exchange_rate_to_eur,
                    cql.id as quote_line_id,
                    cql.base_cost_gbp,
                    cql.delivery_per_unit,
                    cql.quoted_status
                FROM parts_list_lines pll
                LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pll.parts_list_id = ?
                  AND pll.chosen_cost IS NOT NULL
                  AND pll.chosen_cost > 0
                  AND (cql.quoted_status IS NULL OR cql.quoted_status != 'no_bid')
                  AND (cql.quoted_status IS NULL OR cql.quoted_status != 'quoted')
                  {scope_condition}
            """, (list_id,)).fetchall()

            for line in lines:
                if not line['quote_line_id']:
                    chosen_cost = _to_decimal(line['chosen_cost'], Decimal('0'))
                    exchange_rate = _to_decimal(line['exchange_rate_to_eur'], Decimal('1'))
                    base_cost_gbp = chosen_cost / exchange_rate if exchange_rate != 0 else chosen_cost
                    delivery = Decimal('0')

                    margin_factor = Decimal('1') - (margin_percent / Decimal('100'))
                    price_before_delivery = (
                        base_cost_gbp / margin_factor if margin_percent > 0 else base_cost_gbp
                    )
                    quote_price = price_before_delivery + delivery

                    _execute_with_cursor(cur, """
                        INSERT INTO customer_quote_lines 
                        (parts_list_line_id, base_cost_gbp, delivery_per_unit, delivery_per_line, 
                         margin_percent, quote_price_gbp, quoted_status)
                        VALUES (?, ?, ?, ?, ?, ?, 'created')
                    """, (line['parts_list_line_id'], base_cost_gbp, delivery, 0, margin_percent, quote_price))
                    created_count += 1
                else:
                    base_cost = _to_decimal(line['base_cost_gbp'], Decimal('0'))
                    delivery = _to_decimal(line['delivery_per_unit'], Decimal('0'))

                    margin_factor = Decimal('1') - (margin_percent / Decimal('100'))
                    price_before_delivery = (
                        base_cost / margin_factor if margin_percent > 0 else base_cost
                    )
                    quote_price = price_before_delivery + delivery

                    _execute_with_cursor(cur, """
                        UPDATE customer_quote_lines
                        SET margin_percent = ?,
                            quote_price_gbp = ?,
                            date_modified = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (margin_percent, quote_price, line['quote_line_id']))
                    updated_count += 1

        return jsonify(success=True, updated_count=updated_count, created_count=created_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


# Add these routes to your existing customer_quoting.py file

@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/minimum-line-value', methods=['GET'])
def minimum_line_value_review(list_id):
    """
    Review and fix lines below minimum line value (£25)
    """
    try:
        MIN_LINE_VALUE = 25.0

        with db_cursor() as cur:
            header = _execute_with_cursor(cur, """
                SELECT pl.*, c.name as customer_name
                FROM parts_lists pl
                LEFT JOIN customers c ON c.id = pl.customer_id
                WHERE pl.id = ?
            """, (list_id,)).fetchone()

            if not header:
                return "Parts list not found", 404

            lines = _execute_with_cursor(cur, """
                SELECT 
                    pll.id,
                    pll.line_number,
                    pll.parent_line_id,
                    pll.line_type,
                    pll.customer_part_number,
                    parent.customer_part_number as parent_customer_part_number,
                    pll.base_part_number,
                    pll.quantity,
                    pll.chosen_qty,
                    COALESCE(pll.chosen_qty, pll.quantity) as effective_quantity,
                      pll.chosen_supplier_id,
                      s.name as chosen_supplier_name,
                      pll.chosen_source_type,
                    cql.base_cost_gbp,
                    cql.delivery_per_unit,
                    cql.margin_percent,
                    cql.quote_price_gbp,
                    cql.quoted_status,
                    cql.id as quote_line_id
                FROM parts_list_lines pll
                LEFT JOIN parts_list_lines parent ON parent.id = pll.parent_line_id
                LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
                LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                WHERE pll.parts_list_id = ?
                  AND cql.quote_price_gbp > 0
                  AND COALESCE(cql.quoted_status, 'created') IN ('created', 'in_progress')
                  AND cql.is_no_bid = FALSE
                  AND (cql.quote_price_gbp * COALESCE(pll.chosen_qty, pll.quantity)) < ?
                ORDER BY pll.line_number ASC
            """, (list_id, MIN_LINE_VALUE)).fetchall()

        # Calculate suggestions for each line
        lines_with_suggestions = []
        for line in lines:
            line_dict = dict(line)

            base_cost = line['base_cost_gbp'] or 0
            delivery = line['delivery_per_unit'] or 0
            current_margin = line['margin_percent'] or 0
            current_price = line['quote_price_gbp'] or 0
            current_qty = line['effective_quantity']
            current_line_total = current_price * current_qty

            # Calculate suggestions
            suggestions = []

            # Option 1: Increase quantity (keeping current margin)
            if current_price > 0:
                qty_needed = MIN_LINE_VALUE / current_price
                suggested_qty = int(qty_needed) + 1  # Round up
                new_line_total = current_price * suggested_qty
                suggestions.append({
                    'type': 'quantity',
                    'qty': suggested_qty,
                    'margin': current_margin,
                    'price': current_price,
                    'line_total': new_line_total
                })

            # Option 2: Increase margin (keeping current qty)
            if current_qty > 0 and base_cost > 0:
                target_price_per_unit = MIN_LINE_VALUE / current_qty
                total_cost = base_cost + delivery

                if target_price_per_unit > total_cost:
                    suggested_margin = (1 - (base_cost / (target_price_per_unit - delivery))) * 100
                    suggested_margin = min(suggested_margin, 90)  # Cap at 90%

                    # Recalculate actual price with this margin
                    new_price = (base_cost / (1 - suggested_margin / 100)) + delivery
                    new_line_total = new_price * current_qty

                    suggestions.append({
                        'type': 'margin',
                        'qty': current_qty,
                        'margin': suggested_margin,
                        'price': new_price,
                        'line_total': new_line_total
                    })

            # Option 3: Balanced approach (slight qty increase + slight margin increase)
            if current_qty > 0 and base_cost > 0:
                # Try doubling the quantity
                balanced_qty = current_qty * 2
                target_price_per_unit = MIN_LINE_VALUE / balanced_qty
                total_cost = base_cost + delivery

                if target_price_per_unit > total_cost:
                    balanced_margin = (1 - (base_cost / (target_price_per_unit - delivery))) * 100
                    balanced_margin = min(balanced_margin, 90)

                    balanced_price = (base_cost / (1 - balanced_margin / 100)) + delivery
                    balanced_line_total = balanced_price * balanced_qty

                    if balanced_line_total >= MIN_LINE_VALUE:
                        suggestions.append({
                            'type': 'balanced',
                            'qty': balanced_qty,
                            'margin': balanced_margin,
                            'price': balanced_price,
                            'line_total': balanced_line_total
                        })

            line_dict['current_line_total'] = current_line_total
            line_dict['suggestions'] = suggestions
            line_dict['shortfall'] = MIN_LINE_VALUE - current_line_total

            lines_with_suggestions.append(line_dict)

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Parts Lists', url_for('parts_list.view_parts_lists')),
            (header['name'], url_for('parts_list.view_parts_list', list_id=list_id)),
            ('Customer Quote', url_for('customer_quoting.customer_quote', list_id=list_id)),
            ('Minimum Line Value Review', None)
        ]

        return render_template('minimum_line_value.html',
                               list_id=list_id,
                               list_name=header['name'],
                               customer_name=header['customer_name'],
                               customer_system_code=header.get('customer_system_code'),
                               customer_currency_id=header.get('customer_currency_id'),
                               lines=lines_with_suggestions,
                               min_line_value=MIN_LINE_VALUE,
                               breadcrumbs=breadcrumbs)

    except Exception as e:
        logging.exception(e)
        return str(e), 500


@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote/apply-line-adjustment', methods=['POST'])
def apply_line_adjustment(list_id):
    """
    Apply quantity and/or margin adjustment to a single line
    """
    try:
        data = request.get_json(force=True)
        parts_list_line_id = data.get('parts_list_line_id')
        new_qty = data.get('chosen_qty')
        new_margin = data.get('margin_percent')

        if not parts_list_line_id:
            return jsonify(success=False, message="parts_list_line_id required"), 400

        with db_cursor(commit=True) as cur:
            if new_qty is not None:
                _execute_with_cursor(cur, """
                    UPDATE parts_list_lines
                    SET chosen_qty = ?
                    WHERE id = ? AND parts_list_id = ?
                """, (new_qty, parts_list_line_id, list_id))

            if new_margin is not None:
                line_data = _execute_with_cursor(cur, """
                    SELECT cql.base_cost_gbp, cql.delivery_per_unit, cql.id
                    FROM customer_quote_lines cql
                    JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
                    WHERE pll.id = ? AND pll.parts_list_id = ?
                """, (parts_list_line_id, list_id)).fetchone()

                if line_data:
                    base_cost = line_data['base_cost_gbp'] or 0
                    delivery = line_data['delivery_per_unit'] or 0

                    price_before_delivery = base_cost / (1 - new_margin / 100) if new_margin > 0 else base_cost
                    new_price = price_before_delivery + delivery

                    _execute_with_cursor(cur, """
                        UPDATE customer_quote_lines
                        SET margin_percent = ?,
                            quote_price_gbp = ?,
                            date_modified = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (new_margin, new_price, line_data['id']))

        return jsonify(success=True)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


# 1. ADD THIS NEW ROUTE (Place it before customer_quote_simple)
@customer_quoting_bp.route('/parts-lists/<int:list_id>/update-status', methods=['POST'])
def update_list_status(list_id):
    """
    Updates the main status of the parts list (e.g. changing from 'New' to 'Quoted')
    """
    try:
        data = request.get_json(force=True)
        status_id = data.get('status_id')

        if not status_id:
            return jsonify(success=False, message="Status ID is required"), 400

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, """
                UPDATE parts_lists 
                SET status_id = ?, date_modified = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (status_id, list_id))

        return jsonify(success=True)
    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

@customer_quoting_bp.route('/parts-lists/<int:list_id>/customer-quote-simple', methods=['GET'])
def customer_quote_simple(list_id):
    try:
        lines_with_bom = []
        with db_cursor() as cur:
            header = _execute_with_cursor(cur, """
                SELECT pl.*,
                       c.name as customer_name,
                       c.system_code as customer_system_code,
                       c.currency_id as customer_currency_id,
                       s.name as status_name,
                       ct.name as contact_name,
                       ct.email as contact_email,
                       p.name as project_name
                FROM parts_lists pl
                LEFT JOIN customers c ON c.id = pl.customer_id
                LEFT JOIN parts_list_statuses s ON s.id = pl.status_id
                LEFT JOIN contacts ct ON ct.id = pl.contact_id
                LEFT JOIN projects p ON p.id = pl.project_id
                WHERE pl.id = ?
            """, (list_id,)).fetchone()

            if not header:
                return "Parts list not found", 404

            all_statuses = _execute_with_cursor(cur, """
                SELECT id, name FROM parts_list_statuses ORDER BY id ASC
            """).fetchall()

            lines = _execute_with_cursor(cur, """
                  SELECT 
                      pll.id,
                      pll.line_number,
                      pll.parent_line_id,
                      pll.line_type,
                      pll.customer_part_number,
                      CASE
                          WHEN pll.chosen_source_type = 'quote'
                               AND pll.chosen_source_reference IS NOT NULL THEN (
                              SELECT sql.revision
                              FROM parts_list_supplier_quote_lines sql
                              WHERE CAST(sql.id AS TEXT) = pll.chosen_source_reference
                              LIMIT 1
                          )
                          ELSE pll.revision
                      END AS revision,
                      parent.customer_part_number as parent_customer_part_number,
                      pll.base_part_number,
                      pll.quantity,
                      pll.chosen_qty,
                      COALESCE(pll.chosen_qty, pll.quantity) as effective_quantity,
                    pll.chosen_supplier_id,
                      pll.chosen_cost,
                      pll.chosen_price,
                      pll.chosen_currency_id,
                      pll.chosen_lead_days,
                      pll.internal_notes,
                      pll.chosen_source_type,
                      pll.chosen_source_reference,
                      s.name as chosen_supplier_name,
                    s.delivery_cost as supplier_delivery_cost,
                    c.currency_code as chosen_currency_code,
                    c.symbol as chosen_currency_symbol,
                    c.exchange_rate_to_base as chosen_currency_rate,
                    cql.id as quote_line_id,
                    cql.display_part_number,
                    cql.quoted_part_number,
                    cql.manufacturer,
                    cql.base_cost_gbp,
                    cql.delivery_per_unit,
                    cql.delivery_per_line,
                    cql.margin_percent,
                    cql.quote_price_gbp,
                    cql.lead_days,
                    cql.is_no_bid,
                    cql.quoted_status,
                    cql.quoted_on,
                    cql.line_notes,
                    cql.standard_condition,
                    cql.standard_certs,
                    (
                        SELECT sql.condition_code
                        FROM parts_list_supplier_quote_lines sql
                        JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                        WHERE sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                          AND sql.is_no_bid = FALSE
                          AND sql.condition_code IS NOT NULL
                          AND TRIM(sql.condition_code) != ''
                        ORDER BY sq.quote_date DESC,
                                 sql.date_modified DESC,
                                 sql.id DESC
                        LIMIT 1
                    ) AS supplier_condition_code,
                    CASE
                        WHEN pll.chosen_source_type = 'quote'
                             AND pll.chosen_source_reference IS NOT NULL THEN (
                            SELECT sql.certifications
                            FROM parts_list_supplier_quote_lines sql
                            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                            WHERE CAST(sql.id AS TEXT) = pll.chosen_source_reference
                              AND sql.is_no_bid = FALSE
                              AND sql.certifications IS NOT NULL
                              AND TRIM(sql.certifications) != ''
                            LIMIT 1
                        )
                        ELSE (
                            SELECT sql.certifications
                            FROM parts_list_supplier_quote_lines sql
                            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                            WHERE sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                              AND sql.is_no_bid = FALSE
                              AND sql.certifications IS NOT NULL
                              AND TRIM(sql.certifications) != ''
                            ORDER BY sq.quote_date DESC,
                                     sql.date_modified DESC,
                                     sql.id DESC
                            LIMIT 1
                        )
                    END AS supplier_certifications,
                    (
                        SELECT sql.manufacturer
                        FROM parts_list_supplier_quote_lines sql
                        JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                        WHERE sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                          AND sql.is_no_bid = FALSE
                          AND sql.manufacturer IS NOT NULL
                          AND TRIM(sql.manufacturer) != ''
                        ORDER BY sq.quote_date DESC,
                                 sql.date_modified DESC,
                                 sql.id DESC
                        LIMIT 1
                    ) AS supplier_manufacturer,
                    (SELECT sql.quoted_part_number 
                     FROM parts_list_supplier_quote_lines sql
                     JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                     WHERE sql.parts_list_line_id = COALESCE(pll.parent_line_id, pll.id)
                       AND sq.supplier_id = pll.chosen_supplier_id
                     LIMIT 1) as supplier_quoted_part_number,
                    s.standard_condition AS supplier_standard_condition,
                    s.standard_certs AS supplier_standard_certs,
                    (SELECT COALESCE(SUM(sm.available_quantity), 0)
                     FROM stock_movements sm
                     WHERE sm.base_part_number = pll.base_part_number
                       AND sm.movement_type = 'IN'
                       AND sm.available_quantity > 0) as stock_quantity
                  FROM parts_list_lines pll
                  LEFT JOIN parts_list_lines parent ON parent.id = pll.parent_line_id
                  LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
                  LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
                  LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
                  WHERE pll.parts_list_id = ?
                ORDER BY pll.line_number ASC
            """, (list_id,)).fetchall()

            for line in lines:
                line_dict = dict(line)
                has_parent_part = line_dict.get('parent_customer_part_number')
                is_alt_line = line_dict.get('line_type') == 'alternate' or line_dict.get('parent_line_id')
                line_dict['requested_part_number'] = (
                    line_dict['parent_customer_part_number']
                    if is_alt_line and has_parent_part
                    else line_dict.get('customer_part_number')
                )
                bom_data = _execute_with_cursor(cur, """
                    SELECT bl.guide_price, bh.name as bom_name
                    FROM bom_lines bl
                    JOIN bom_headers bh ON bl.bom_header_id = bh.id
                    WHERE bl.base_part_number = ?
                    ORDER BY bl.guide_price DESC
                    LIMIT 1
                """, (line['base_part_number'],)).fetchone()

                if bom_data:
                    line_dict['bom_guide_price'] = bom_data['guide_price']
                    line_dict['bom_name'] = bom_data['bom_name']
                else:
                    line_dict['bom_guide_price'] = None
                    line_dict['bom_name'] = None

                line_dict['standard_condition'] = (
                    (line_dict.get('standard_condition') or '').strip()
                    or (line_dict.get('supplier_condition_code') or '').strip()
                    or (line_dict.get('supplier_standard_condition') or '').strip()
                )
                line_dict['standard_certs'] = (
                    (line_dict.get('standard_certs') or '').strip()
                    or (line_dict.get('supplier_certifications') or '').strip()
                    or (line_dict.get('supplier_standard_certs') or '').strip()
                )

                line_dict['manufacturer'] = (
                    (line_dict.get('manufacturer') or '').strip()
                    or (line_dict.get('supplier_manufacturer') or '').strip()
                )

                if not line_dict['display_part_number']:
                    if line_dict['supplier_quoted_part_number'] and \
                            line_dict['supplier_quoted_part_number'] != line_dict['customer_part_number']:
                        line_dict['suggested_display_pn'] = line_dict['supplier_quoted_part_number']
                    else:
                        line_dict['suggested_display_pn'] = line_dict['customer_part_number']
                else:
                    line_dict['suggested_display_pn'] = line_dict['display_part_number']

                lines_with_bom.append(line_dict)

            currencies = _execute_with_cursor(cur, """
                SELECT id, currency_code, symbol, exchange_rate_to_base as exchange_rate_to_eur
                FROM currencies
                ORDER BY id ASC
            """).fetchall()

        total_lines = len(lines_with_bom)
        lines_with_cost = sum(1 for l in lines_with_bom if l['chosen_cost'] is not None)
        lines_created = sum(1 for l in lines_with_bom if (l.get('quoted_status') or 'created') == 'created')
        lines_in_progress = sum(1 for l in lines_with_bom if l.get('quoted_status') == 'in_progress')
        lines_quoted = sum(1 for l in lines_with_bom if l.get('quoted_status') == 'quoted')
        lines_no_bid = sum(1 for l in lines_with_bom if l.get('quoted_status') == 'no_bid')

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Parts Lists', url_for('parts_list.view_parts_lists')),
            (header['name'], url_for('parts_list.view_parts_list', list_id=list_id)),
            ('Customer Quote (Simple)', None)
        ]

        contact_name = header.get('contact_name') if isinstance(header, dict) else None
        contact_email = header.get('contact_email') if isinstance(header, dict) else None
        contact_first_name = contact_name.split()[0] if contact_name else ''
        current_user_name = None
        if current_user and getattr(current_user, "is_authenticated", False):
            current_user_name = (getattr(current_user, "username", "") or "").replace('_', ' ').title()

        return render_template('customer_quote_simple.html',
                               list_id=list_id,
                               list_name=header['name'],
                               list_notes=header.get('notes'),
                               customer_name=header['customer_name'],
                               nav_is_pinned=bool(header.get('is_pinned')),
                               project_id=header.get('project_id'),
                               project_name=header.get('project_name'),
                               status_id=header.get('status_id'),
                               status_name=header.get('status_name'),
                               customer_system_code=header.get('customer_system_code'),
                               customer_currency_id=header.get('customer_currency_id'),
                               contact_name=contact_name,
                               contact_email=contact_email,
                               contact_first_name=contact_first_name,
                               current_user_name=current_user_name,
                               lines=lines_with_bom,
                               currencies=[dict(c) for c in currencies],

                               all_statuses=[dict(s) for s in all_statuses],

                               total_lines=total_lines,
                               lines_with_cost=lines_with_cost,
                               lines_created=lines_created,
                               lines_in_progress=lines_in_progress,
                               lines_quoted=lines_quoted,
                               lines_no_bid=lines_no_bid,
                               breadcrumbs=breadcrumbs)
    except Exception as e:
        logging.exception(e)
        return str(e), 500
