# this is portal_admin.py - it also lives in the office and serves the core CRM. It administers the settings for the office CRM side of the portal

import os

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, session
from db import db_cursor, execute as db_execute, get_currency_rate_column
from models import create_base_part_number, get_base_currency
from werkzeug.security import generate_password_hash
import logging
import secrets
from datetime import datetime
from routes.portal_api import _analyze_quote_internal
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

portal_admin_bp = Blueprint('portal_admin', __name__, url_prefix='/portal-admin')


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_single_value(row):
    if not row:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def _with_returning_clause(query, returning='id'):
    if not _using_postgres():
        return query
    trimmed = query.strip().rstrip(';')
    return f"{trimmed} RETURNING {returning}"


def _last_inserted_id(cur, key='id'):
    if _using_postgres():
        row = cur.fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            return row.get(key)
        return row[0]
    return getattr(cur, 'lastrowid', None)


def _get_base_currency():
    base_code = (get_base_currency() or 'GBP').upper()
    row = db_execute(
        "SELECT id, currency_code FROM currencies WHERE currency_code = ?",
        (base_code,),
        fetch='one'
    )
    if not row:
        rate_column = get_currency_rate_column()
        row = db_execute(
            f"SELECT id, currency_code FROM currencies WHERE {rate_column} = 1 ORDER BY id LIMIT 1",
            fetch='one'
        )
    if row:
        return {'id': row['id'], 'code': row['currency_code']}
    return {'id': None, 'code': base_code}


def _convert_to_base_currency(amount, currency_id=None, currency_code=None, currency_rate=None, base_currency=None):
    amount = _to_float(amount)
    if amount is None:
        return None
    base_currency = base_currency or _get_base_currency()
    base_id = base_currency.get('id')
    base_code = (base_currency.get('code') or '').upper()
    if base_id is not None and currency_id is not None and currency_id == base_id:
        return amount
    if currency_code and base_code and currency_code.upper() == base_code:
        return amount
    rate = _to_float(currency_rate) if currency_rate is not None else None
    if not rate:
        return amount
    return amount / rate


def get_portal_setting(key, default=None):
    """Get a portal setting value"""
    try:
        result = db_execute(
            "SELECT setting_value FROM portal_settings WHERE setting_key = ?",
            (key,),
            fetch='one'
        )
        return result['setting_value'] if result else default
    except Exception as e:
        logging.exception(e)
        return default


def get_customer_margins(customer_id):
    """Get customer-specific margins or fall back to global defaults"""
    try:
        margins = db_execute("""
            SELECT stock_margin_percentage, vq_margin_percentage, po_margin_percentage
            FROM portal_customer_margins
            WHERE customer_id = ?
        """, (customer_id,), fetch='one')

        if margins:
            return {
                'stock': float(margins['stock_margin_percentage'] or get_portal_setting('stock_margin_percentage', 15)),
                'vq': float(margins['vq_margin_percentage'] or get_portal_setting('vq_margin_percentage', 15)),
                'po': float(margins['po_margin_percentage'] or get_portal_setting('po_margin_percentage', 15))
            }
        return {
            'stock': float(get_portal_setting('stock_margin_percentage', 15)),
            'vq': float(get_portal_setting('vq_margin_percentage', 15)),
            'po': float(get_portal_setting('po_margin_percentage', 15))
        }
    except Exception as e:
        logging.exception(e)
        return {'stock': 15, 'vq': 15, 'po': 15}


def get_customer_pricing_agreement(customer_id, base_part_number):
    """Check if there's an active pricing agreement for this customer/part"""
    try:
        from db import _using_postgres
        rate_column = get_currency_rate_column()

        if _using_postgres():
            date_condition = "AND (pcp.valid_from IS NULL OR pcp.valid_from <= NOW()) AND (pcp.valid_until IS NULL OR pcp.valid_until >= NOW())"
        else:
            date_condition = "AND (pcp.valid_from IS NULL OR pcp.valid_from <= date('now')) AND (pcp.valid_until IS NULL OR pcp.valid_until >= date('now'))"

        pricing = db_execute(f"""
            SELECT price, currency_id, c.{rate_column} as currency_rate
            FROM portal_customer_pricing pcp
            LEFT JOIN currencies c ON c.id = pcp.currency_id
            WHERE pcp.customer_id = ?
            AND pcp.base_part_number = ?
            AND pcp.is_active = TRUE
            {date_condition}
            ORDER BY pcp.date_created DESC
            LIMIT 1
        """, (customer_id, base_part_number), fetch='one')

        if pricing:
            base_currency = _get_base_currency()
            return _convert_to_base_currency(
                pricing['price'],
                currency_id=pricing['currency_id'],
                currency_rate=pricing['currency_rate'],
                base_currency=base_currency
            )

        return None
    except Exception as e:
        logging.exception(e)
        return None


@portal_admin_bp.route('/')
def portal_admin_home():
    """Portal administration dashboard"""
    settings = db_execute("SELECT * FROM portal_settings ORDER BY setting_key", fetch='all') or []
    users_count = _extract_single_value(
        db_execute("SELECT COUNT(*) as count FROM portal_users WHERE is_active = TRUE", fetch='one')
    ) or 0
    recent_requests = db_execute("""
        SELECT
            pqr.*,
            pu.email as user_email,
            c.name as customer_name,
            (
                SELECT COUNT(*)
                FROM portal_quote_request_lines pqrl
                WHERE pqrl.portal_quote_request_id = pqr.id
            ) as line_count
        FROM portal_quote_requests pqr
        JOIN portal_users pu ON pu.id = pqr.portal_user_id
        JOIN customers c ON c.id = pqr.customer_id
        ORDER BY pqr.date_submitted DESC
        LIMIT 20
    """, fetch='all') or []

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Portal Admin', None)
    ]

    return render_template('portal_admin.html',
                           breadcrumbs=breadcrumbs,
                           settings={s['setting_key']: dict(s) for s in settings},
                           users_count=users_count,
                           recent_requests=[dict(r) for r in recent_requests])


@portal_admin_bp.route('/settings/update', methods=['POST'])
def update_settings():
    """Update portal settings"""
    try:
        data = request.get_json()

        with db_cursor(commit=True) as cur:
            for key, value in data.items():
                _execute_with_cursor(
                    cur,
                    """
                    UPDATE portal_settings 
                    SET setting_value = ?, date_modified = CURRENT_TIMESTAMP
                    WHERE setting_key = ?
                    """,
                    (value, key),
                )

        return jsonify({'success': True, 'message': 'Settings updated'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/api-key/generate', methods=['POST'])
def generate_api_key():
    """Generate new API key for portal"""
    try:
        new_key = f"portal_{secrets.token_urlsafe(32)}"

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_settings 
                SET setting_value = ?, date_modified = CURRENT_TIMESTAMP
                WHERE setting_key = 'api_key'
                """,
                (new_key,),
            )

        return jsonify({'success': True, 'api_key': new_key})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/users')
def portal_users():
    """Manage portal users"""
    users = db_execute("""
        SELECT 
            pu.*,
            c.name as customer_name,
            COUNT(DISTINCT pqr.id) as quote_count,
            MAX(pqr.date_submitted) as last_quote_date
        FROM portal_users pu
        JOIN customers c ON c.id = pu.customer_id
        LEFT JOIN portal_quote_requests pqr ON pqr.portal_user_id = pu.id
        GROUP BY pu.id
        ORDER BY pu.date_created DESC
    """, fetch='all') or []

    customers = db_execute("SELECT id, name FROM customers ORDER BY name", fetch='all') or []

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Portal Admin', url_for('portal_admin.portal_admin_home')),
        ('Users', None)
    ]

    return render_template('portal_users.html',
                           breadcrumbs=breadcrumbs,
                           users=[dict(u) for u in users],
                           customers=[dict(c) for c in customers])


@portal_admin_bp.route('/users/create', methods=['POST'])
def create_portal_user():
    """Create new portal user"""
    try:
        data = request.get_json()

        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        customer_id = data.get('customer_id')
        first_name = data.get('first_name', '')
        last_name = data.get('last_name', '')

        if not all([email, password, customer_id]):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        exists = db_execute(
            "SELECT 1 FROM portal_users WHERE email = ?",
            (email,),
            fetch='one'
        )

        if exists:
            return jsonify({'success': False, 'error': 'Email already registered'}), 400

        password_hash = generate_password_hash(password)
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                INSERT INTO portal_users 
                (customer_id, email, password_hash, first_name, last_name)
                VALUES (?, ?, ?, ?, ?)
                """,
                (customer_id, email, password_hash, first_name, last_name),
            )
            user_id = _last_inserted_id(cur)

        return jsonify({'success': True, 'user_id': user_id})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
def toggle_user_status(user_id):
    """Activate/deactivate portal user"""
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_users 
                SET is_active = NOT is_active
                WHERE id = ?
                """,
                (user_id,),
            )

        return jsonify({'success': True})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
def reset_user_password(user_id):
    """Reset portal user password"""
    try:
        data = request.get_json()
        new_password = data.get('password', '')

        if not new_password:
            return jsonify({'success': False, 'error': 'Password required'}), 400

        password_hash = generate_password_hash(new_password)
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_users 
                SET password_hash = ?
                WHERE id = ?
                """,
                (password_hash, user_id),
            )

        return jsonify({'success': True, 'message': 'Password reset successfully'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/requests')
def portal_requests():
    """View all portal quote requests"""
    status_filter = request.args.get('status', '')

    sql = """
        SELECT
            pqr.*,
            pu.email as user_email,
            pu.first_name,
            pu.last_name,
            c.name as customer_name,
            COALESCE(pqrl_stats.line_count, 0) as line_count,
            COALESCE(pqrl_stats.quoted_lines, 0) as quoted_lines,
            u.username as processed_by
        FROM portal_quote_requests pqr
        JOIN portal_users pu ON pu.id = pqr.portal_user_id
        JOIN customers c ON c.id = pqr.customer_id
        LEFT JOIN (
            SELECT
                portal_quote_request_id,
                COUNT(*) as line_count,
                SUM(CASE WHEN status = 'quoted' THEN 1 ELSE 0 END) as quoted_lines
            FROM portal_quote_request_lines
            GROUP BY portal_quote_request_id
        ) pqrl_stats ON pqrl_stats.portal_quote_request_id = pqr.id
        LEFT JOIN users u ON u.id = pqr.processed_by_user_id
    """

    params = []
    if status_filter:
        sql += " WHERE pqr.status = ?"
        params.append(status_filter)

    sql += " ORDER BY pqr.date_submitted DESC"

    requests = db_execute(sql, params, fetch='all') or []

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Portal Admin', url_for('portal_admin.portal_admin_home')),
        ('Quote Requests', None)
    ]

    return render_template('portal_requests.html',
                           breadcrumbs=breadcrumbs,
                           requests=[dict(r) for r in requests],
                           status_filter=status_filter)


@portal_admin_bp.route('/requests/<int:request_id>')
def view_portal_request(request_id):
    """View single portal request details with pricing grid - includes customer quote info"""
    request_data = db_execute("""
        SELECT 
            pqr.*,
            pu.email as user_email,
            pu.first_name,
            pu.last_name,
            c.name as customer_name,
            pl.name as parts_list_name,
            u.username as processed_by
        FROM portal_quote_requests pqr
        JOIN portal_users pu ON pu.id = pqr.portal_user_id
        JOIN customers c ON c.id = pqr.customer_id
        LEFT JOIN parts_lists pl ON pl.id = pqr.parts_list_id
        LEFT JOIN users u ON u.id = pqr.processed_by_user_id
        WHERE pqr.id = ?
    """, (request_id,), fetch='one')

    if not request_data:
        flash('Request not found', 'error')
        return redirect(url_for('portal_admin.portal_requests'))

    lines = db_execute("""
        SELECT 
            pqrl.*,
            c.currency_code,

            -- Parts List Line info
            pll.id as parts_list_line_id,
            pll.chosen_price as parts_list_chosen_price,
            pll.chosen_cost as parts_list_chosen_cost,
            pll.chosen_lead_days as parts_list_lead_days,
            pll.chosen_currency_id as parts_list_currency_id,
            pll.chosen_supplier_id as parts_list_supplier_id,
            pc.currency_code as parts_list_currency_code,
            s.name as parts_list_supplier_name,

            -- Customer Quote Line info (this is what you ACTUALLY quote!)
            cql.id as customer_quote_line_id,
            cql.display_part_number,
            cql.quoted_part_number,
            cql.base_cost_gbp,
            cql.delivery_per_unit,
            cql.delivery_per_line,
            cql.margin_percent,
            cql.quote_price_gbp,
            cql.quoted_status,
            cql.is_no_bid as customer_quote_no_bid,
            cql.line_notes as customer_quote_notes,

            -- Determine overall status for display
            CASE 
                WHEN cql.quoted_status = 'quoted' THEN 'customer_quoted'
                WHEN cql.quoted_status = 'no_bid' THEN 'no_bid'
                WHEN cql.quoted_status = 'created' THEN 'customer_created'
                WHEN pll.chosen_price IS NOT NULL THEN 'parts_list_priced'
                WHEN pll.chosen_cost IS NOT NULL THEN 'parts_list_costed'
                WHEN (SELECT COUNT(*) FROM parts_list_supplier_quote_lines sql
                      JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                      WHERE sql.parts_list_line_id = pll.id AND sql.is_no_bid = FALSE) > 0 THEN 'has_supplier_quotes'
                ELSE 'not_worked'
            END as overall_status

        FROM portal_quote_request_lines pqrl
        LEFT JOIN currencies c ON c.id = pqrl.quoted_currency_id

        -- Join to parts list lines
        LEFT JOIN parts_lists pl ON pl.id = ?
        LEFT JOIN parts_list_lines pll ON pll.parts_list_id = pl.id 
            AND pll.base_part_number = pqrl.base_part_number
        LEFT JOIN currencies pc ON pc.id = pll.chosen_currency_id
        LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id

        -- Join to customer quote lines (the REAL pricing!)
        LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id

        WHERE pqrl.portal_quote_request_id = ?
        ORDER BY pqrl.line_number
    """, (request_data['parts_list_id'], request_id), fetch='all') or []

    lines = [dict(l) for l in lines]

    portal_estimates = {}
    try:
        parts_payload = [
            {'part_number': line.get('part_number'), 'quantity': line.get('quantity')}
            for line in lines
            if line.get('part_number')
        ]
        if parts_payload:
            estimate_response = _analyze_quote_internal(request_data['customer_id'], parts_payload)
            estimate_data = (
                estimate_response[0].get_json()
                if isinstance(estimate_response, tuple)
                else estimate_response.get_json()
            )
            if estimate_data and estimate_data.get('success'):
                for item in estimate_data.get('results', []):
                    key = (item.get('base_part_number') or item.get('part_number') or '').strip()
                    if key:
                        portal_estimates[key] = item
    except Exception as e:
        logging.exception(e)

    if portal_estimates:
        for line in lines:
            key = line.get('base_part_number') or line.get('part_number')
            estimate = portal_estimates.get(key) if key else None
            if estimate:
                estimated_price = estimate.get('estimated_price')
                if estimated_price is not None:
                    try:
                        estimated_price = float(estimated_price)
                    except (TypeError, ValueError):
                        estimated_price = None

                line['portal_estimated_price'] = estimated_price
                line['portal_estimated_currency'] = estimate.get('currency')
                line['portal_estimated_lead_days'] = estimate.get('estimated_lead_days')
                line['portal_estimated_status'] = estimate.get('status')
                line['portal_price_source'] = estimate.get('price_source')

    currencies = db_execute("SELECT id, currency_code FROM currencies ORDER BY id", fetch='all') or []

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Portal Admin', url_for('portal_admin.portal_admin_home')),
        ('Quote Requests', url_for('portal_admin.portal_requests')),
        (request_data['reference_number'], None)
    ]

    return render_template('portal_request_detail.html',
                           breadcrumbs=breadcrumbs,
                           request=dict(request_data),
                           lines=lines,
                           currencies=[dict(c) for c in currencies])


@portal_admin_bp.route('/requests/<int:request_id>/lines/<int:line_id>/load-from-parts-list', methods=['POST'])
def load_line_from_parts_list(request_id, line_id):
    """Load pricing from parts list for a single line - uses chosen_price (selling price)"""
    try:
        rate_column = get_currency_rate_column()
        line_data = db_execute(f"""
            SELECT 
                pqrl.base_part_number,
                pqr.parts_list_id,
                pll.chosen_price,
                pll.chosen_lead_days,
                pll.chosen_currency_id,
                c.{rate_column} as currency_rate
            FROM portal_quote_request_lines pqrl
            JOIN portal_quote_requests pqr ON pqr.id = pqrl.portal_quote_request_id
            LEFT JOIN parts_list_lines pll ON pll.parts_list_id = pqr.parts_list_id
                AND pll.base_part_number = pqrl.base_part_number
            LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
            WHERE pqrl.id = ? AND pqr.id = ?
        """, (line_id, request_id), fetch='one')

        if not line_data:
            return jsonify({'success': False, 'error': 'Line not found'}), 404

        if not line_data['chosen_price']:
            return jsonify({'success': False, 'error': 'No selling price in parts list'}), 400

        base_currency = _get_base_currency()
        base_currency_id = base_currency.get('id')
        price_in_base = _convert_to_base_currency(
            line_data['chosen_price'],
            currency_id=line_data['chosen_currency_id'],
            currency_rate=line_data['currency_rate'],
            base_currency=base_currency
        )

        return jsonify({
            'success': True,
            'price': price_in_base,
            'lead_days': line_data['chosen_lead_days'],
            'currency_id': base_currency_id
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/requests/<int:request_id>/load-all-from-parts-list', methods=['POST'])
def load_all_from_parts_list(request_id):
    """
    Load pricing from parts list for ALL lines that have chosen_price set
    This uses chosen_price which should be the SELLING price (not cost)
    """
    try:
        request_data = db_execute("""
            SELECT parts_list_id FROM portal_quote_requests WHERE id = ?
        """, (request_id,), fetch='one')

        if not request_data or not request_data['parts_list_id']:
            return jsonify({'success': False, 'error': 'No parts list linked'}), 404

        parts_list_id = request_data['parts_list_id']

        rate_column = get_currency_rate_column()
        parts_list_lines = db_execute(f"""
            SELECT 
                pll.base_part_number, 
                pll.chosen_price, 
                pll.chosen_lead_days, 
                pll.chosen_currency_id,
                c.{rate_column} as currency_rate
            FROM parts_list_lines pll
            LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
            WHERE pll.parts_list_id = ? 
            AND pll.chosen_price IS NOT NULL
            AND pll.chosen_price > 0
        """, (parts_list_id,), fetch='all') or []

        loaded_count = 0

        base_currency = _get_base_currency()
        base_currency_id = base_currency.get('id')
        with db_cursor(commit=True) as cur:
            for pll in parts_list_lines:
                price_in_base = _convert_to_base_currency(
                    pll['chosen_price'],
                    currency_id=pll['chosen_currency_id'],
                    currency_rate=pll['currency_rate'],
                    base_currency=base_currency
                )

                _execute_with_cursor(
                    cur,
                    """
                    UPDATE portal_quote_request_lines
                    SET quoted_price = ?,
                        quoted_lead_days = ?,
                        quoted_currency_id = ?,
                        status = 'quoted'
                    WHERE portal_quote_request_id = ?
                    AND base_part_number = ?
                    """,
                    (
                        price_in_base,
                        pll['chosen_lead_days'],
                        base_currency_id,
                        request_id,
                        pll['base_part_number']
                    ),
                )

                if cur.rowcount > 0:
                    loaded_count += 1

        return jsonify({'success': True, 'loaded_count': loaded_count})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/requests/<int:request_id>/lines/<int:line_id>/save', methods=['POST'])
def save_portal_line(request_id, line_id):
    """Manually save pricing for a portal quote line"""
    try:
        data = request.get_json()
        price = data.get('price')
        lead_days = data.get('lead_days')
        currency_id = data.get('currency_id')

        if not price:
            return jsonify({'success': False, 'error': 'Price required'}), 400

        base_currency = _get_base_currency()
        base_currency_id = base_currency.get('id')
        price_in_base = _to_float(price)
        if currency_id and base_currency_id and currency_id != base_currency_id:
            rate_column = get_currency_rate_column()
            rate_row = db_execute(
                f"SELECT {rate_column} as currency_rate FROM currencies WHERE id = ?",
                (currency_id,),
                fetch='one'
            )
            price_in_base = _convert_to_base_currency(
                price_in_base,
                currency_id=currency_id,
                currency_rate=rate_row['currency_rate'] if rate_row else None,
                base_currency=base_currency
            )
            currency_id = base_currency_id
        elif currency_id is None:
            currency_id = base_currency_id

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_quote_request_lines
                SET quoted_price = ?,
                    quoted_lead_days = ?,
                    quoted_currency_id = ?,
                    status = 'quoted'
                WHERE id = ?
                AND portal_quote_request_id = ?
                """,
                (price_in_base, lead_days, currency_id, line_id, request_id),
            )

            if cur.rowcount == 0:
                return jsonify({'success': False, 'error': 'Line not found'}), 404

        return jsonify({'success': True})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/requests/<int:request_id>/lines/<int:line_id>/toggle-no-bid', methods=['POST'])
def toggle_line_no_bid(request_id, line_id):
    """Toggle no-bid status for a line"""
    try:
        current = db_execute("""
            SELECT status FROM portal_quote_request_lines
            WHERE id = ? AND portal_quote_request_id = ?
        """, (line_id, request_id), fetch='one')

        if not current:
            return jsonify({'success': False, 'error': 'Line not found'}), 404

        new_status = 'pending' if current['status'] == 'no_bid' else 'no_bid'

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_quote_request_lines
                SET status = ?,
                    quoted_price = CASE WHEN ? = 'no_bid' THEN NULL ELSE quoted_price END,
                    quoted_lead_days = CASE WHEN ? = 'no_bid' THEN NULL ELSE quoted_lead_days END
                WHERE id = ?
                """,
                (new_status, new_status, new_status, line_id),
            )

        return jsonify({'success': True, 'status': new_status})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/requests/<int:request_id>/publish', methods=['POST'])
def publish_portal_quote(request_id):
    """Publish quote to customer portal"""
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_quote_requests
                SET status = 'quoted',
                    date_processed = CURRENT_TIMESTAMP,
                    processed_by_user_id = ?
                WHERE id = ?
                """,
                (session.get('user_id'), request_id),
            )

        return jsonify({'success': True, 'message': 'Quote published to customer'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/requests/<int:request_id>/status', methods=['POST'])
def update_request_status(request_id):
    """Update request status (processing, etc.)"""
    try:
        data = request.get_json()
        status = data.get('status')

        if status not in ['pending', 'processing', 'quoted', 'declined']:
            return jsonify({'success': False, 'error': 'Invalid status'}), 400

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_quote_requests
                SET status = ?,
                    processed_by_user_id = ?
                WHERE id = ?
                """,
                (status, session.get('user_id'), request_id),
            )

        return jsonify({'success': True})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/requests/<int:request_id>/decline', methods=['POST'])
def decline_portal_request(request_id):
    """Decline a portal request"""
    try:
        data = request.get_json()
        reason = data.get('reason', '')

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_quote_requests
                SET status = 'declined',
                    date_processed = CURRENT_TIMESTAMP,
                    processed_by_user_id = ?,
                    customer_notes = COALESCE(customer_notes || '\\n\\nDECLINED: ', 'DECLINED: ') || ?
                WHERE id = ?
                """,
                (session.get('user_id'), reason, request_id),
            )

        return jsonify({'success': True})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/requests/<int:request_id>/lines/<int:line_id>/load-from-customer-quote', methods=['POST'])
def load_line_from_customer_quote(request_id, line_id):
    """Load pricing from customer_quote_lines (this is the REAL quoted price with margin)"""
    try:
        base_currency_id = _get_base_currency().get('id')
        line_data = db_execute("""
            SELECT 
                pqrl.base_part_number,
                pqr.parts_list_id,
                cql.quote_price_gbp,
                pll.chosen_lead_days,
                ? as currency_id
            FROM portal_quote_request_lines pqrl
            JOIN portal_quote_requests pqr ON pqr.id = pqrl.portal_quote_request_id
            LEFT JOIN parts_list_lines pll ON pll.parts_list_id = pqr.parts_list_id
                AND pll.base_part_number = pqrl.base_part_number
            LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
            WHERE pqrl.id = ? AND pqr.id = ?
        """, (base_currency_id, line_id, request_id), fetch='one')

        if not line_data:
            return jsonify({'success': False, 'error': 'Line not found'}), 404

        if not line_data['quote_price_gbp']:
            return jsonify({'success': False, 'error': 'No customer quote price available'}), 400


        return jsonify({
            'success': True,
            'price': line_data['quote_price_gbp'],
            'lead_days': line_data['chosen_lead_days'],
            'currency_id': line_data['currency_id']
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/requests/<int:request_id>/load-all-from-customer-quote', methods=['POST'])
def load_all_from_customer_quote(request_id):
    """Load ALL quoted prices from customer_quote_lines (the proper way!)"""
    try:
        request_data = db_execute("""
            SELECT parts_list_id FROM portal_quote_requests WHERE id = ?
        """, (request_id,), fetch='one')

        if not request_data or not request_data['parts_list_id']:
            return jsonify({'success': False, 'error': 'No parts list linked'}), 404

        parts_list_id = request_data['parts_list_id']

        quote_lines = db_execute("""
            SELECT 
                pll.base_part_number,
                cql.quote_price_gbp,
                pll.chosen_lead_days
            FROM customer_quote_lines cql
            JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
            WHERE pll.parts_list_id = ?
            AND cql.quoted_status = 'quoted'
            AND cql.quote_price_gbp IS NOT NULL
            AND cql.quote_price_gbp > 0
        """, (parts_list_id,), fetch='all') or []

        loaded_count = 0

        base_currency_id = _get_base_currency().get('id')
        with db_cursor(commit=True) as cur:
            for cql in quote_lines:
                _execute_with_cursor(
                    cur,
                    """
                    UPDATE portal_quote_request_lines
                    SET quoted_price = ?,
                        quoted_lead_days = ?,
                        quoted_currency_id = ?,
                        status = 'quoted'
                    WHERE portal_quote_request_id = ?
                    AND base_part_number = ?
                    """,
                    (
                        cql['quote_price_gbp'],
                        cql['chosen_lead_days'],
                        base_currency_id,
                        request_id,
                        cql['base_part_number']
                    ),
                )

                if cur.rowcount > 0:
                    loaded_count += 1

        return jsonify({'success': True, 'loaded_count': loaded_count})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# CUSTOMER PORTAL SETTINGS
# ============================================================================

@portal_admin_bp.route('/customer-settings')
def customer_portal_settings():
    """Manage customer-specific portal settings (margins and pricing agreements)"""
    customers = db_execute("""
        SELECT DISTINCT
            c.id,
            c.name,
            COUNT(DISTINCT pu.id) as user_count,
            COUNT(DISTINCT pqr.id) as request_count
        FROM customers c
        LEFT JOIN portal_users pu ON pu.customer_id = c.id
        LEFT JOIN portal_quote_requests pqr ON pqr.customer_id = c.id
        WHERE pu.id IS NOT NULL
        GROUP BY c.id, c.name
        ORDER BY c.name
    """, fetch='all') or []

    return render_template('portal_customer_settings.html',
                           customers=[dict(c) for c in customers])



@portal_admin_bp.route('/customer-settings/<int:customer_id>')
def view_customer_portal_settings(customer_id):
    """View and edit customer-specific portal settings"""
    customer = db_execute("""
        SELECT c.*, COUNT(DISTINCT pu.id) as user_count
        FROM customers c
        LEFT JOIN portal_users pu ON pu.customer_id = c.id
        WHERE c.id = ?
        GROUP BY c.id
    """, (customer_id,), fetch='one')

    if not customer:
        flash('Customer not found', 'error')
        return redirect(url_for('portal_admin.customer_portal_settings'))

    margin_settings = db_execute(
        "SELECT * FROM portal_customer_margins WHERE customer_id = ?",
        (customer_id,),
        fetch='one'
    )

    today = datetime.now().strftime('%Y-%m-%d')

    pricing_agreements_raw = db_execute("""
        SELECT 
            pcp.*,
            pn.part_number,
            c.currency_code
        FROM portal_customer_pricing pcp
        LEFT JOIN part_numbers pn ON pn.base_part_number = pcp.base_part_number
        LEFT JOIN currencies c ON c.id = pcp.currency_id
        WHERE pcp.customer_id = ?
        ORDER BY pcp.date_created DESC
    """, (customer_id,), fetch='all') or []

    # Add status to each pricing agreement
    pricing_agreements = []
    for pricing in pricing_agreements_raw:
        pricing_dict = dict(pricing)

        # Calculate status
        if pricing['is_active']:
            if pricing['valid_from'] and pricing['valid_from'] > today:
                pricing_dict['status'] = 'future'
            elif pricing['valid_until'] and pricing['valid_until'] < today:
                pricing_dict['status'] = 'expired'
            else:
                pricing_dict['status'] = 'active'
        else:
            pricing_dict['status'] = 'inactive'

        pricing_agreements.append(pricing_dict)

    suggested_parts_raw = db_execute("""
        SELECT 
            psp.*,
            pn.part_number,
            u.username as suggested_by_username,
            (SELECT SUM(available_quantity) 
             FROM stock_movements 
             WHERE base_part_number = psp.base_part_number 
             AND movement_type = 'IN' 
             AND available_quantity >= 1) as stock_quantity
        FROM portal_suggested_parts psp
        LEFT JOIN part_numbers pn ON pn.base_part_number = psp.base_part_number
        LEFT JOIN users u ON u.id = psp.suggested_by_user_id
        WHERE psp.customer_id = ?
        ORDER BY psp.priority DESC, psp.date_created DESC
    """, (customer_id,), fetch='all') or []

    suggested_parts = [dict(sp) for sp in suggested_parts_raw]

    currencies = db_execute("""
        SELECT id, currency_code FROM currencies ORDER BY id
    """, fetch='all') or []

    # Get global default margins
    global_margins = {
        'stock': float(get_portal_setting('stock_margin_percentage', 15)),
        'vq': float(get_portal_setting('vq_margin_percentage', 15)),
        'po': float(get_portal_setting('po_margin_percentage', 15))
    }

    return render_template('portal_customer_settings_detail.html',
                           customer=dict(customer),
                           margin_settings=dict(margin_settings) if margin_settings else None,
                           pricing_agreements=pricing_agreements,
                           suggested_parts=suggested_parts,
                           currencies=[dict(c) for c in currencies],
                           global_margins=global_margins)

@portal_admin_bp.route('/customer-settings/<int:customer_id>/margins', methods=['POST'])
def update_customer_margins(customer_id):
    """Update customer-specific margin settings"""
    try:
        data = request.get_json()

        stock_margin = data.get('stock_margin_percentage')
        vq_margin = data.get('vq_margin_percentage')
        po_margin = data.get('po_margin_percentage')

        exists = db_execute(
            "SELECT id FROM portal_customer_margins WHERE customer_id = ?",
            (customer_id,),
            fetch='one'
        )

        with db_cursor(commit=True) as cur:
            if exists:
                _execute_with_cursor(
                    cur,
                    """
                    UPDATE portal_customer_margins
                    SET stock_margin_percentage = ?,
                        vq_margin_percentage = ?,
                        po_margin_percentage = ?,
                        date_modified = CURRENT_TIMESTAMP
                    WHERE customer_id = ?
                    """,
                    (stock_margin, vq_margin, po_margin, customer_id),
                )
            else:
                _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO portal_customer_margins
                    (customer_id, stock_margin_percentage, vq_margin_percentage, po_margin_percentage)
                    VALUES (?, ?, ?, ?)
                    """,
                    (customer_id, stock_margin, vq_margin, po_margin),
                )

        return jsonify({'success': True, 'message': 'Margin settings updated'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/customer-settings/<int:customer_id>/margins', methods=['DELETE'])
def delete_customer_margins(customer_id):
    """Delete customer-specific margins (revert to global defaults)"""
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                "DELETE FROM portal_customer_margins WHERE customer_id = ?",
                (customer_id,),
            )

        return jsonify({'success': True, 'message': 'Reverted to global margin settings'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/customer-settings/<int:customer_id>/pricing', methods=['POST'])
def add_customer_pricing(customer_id):
    """Add a pricing agreement for a specific part"""
    try:
        data = request.get_json()

        part_number = data.get('part_number', '').strip()
        price = data.get('price')
        base_currency_id = _get_base_currency().get('id')
        currency_id = data.get('currency_id', base_currency_id)
        valid_from = data.get('valid_from')
        valid_until = data.get('valid_until')
        notes = data.get('notes', '')

        if not part_number or not price:
            return jsonify({'success': False, 'error': 'Part number and price required'}), 400

        base_part_number = create_base_part_number(part_number)

        from db import _using_postgres

        if _using_postgres():
            date_check = "AND (valid_until IS NULL OR valid_until >= NOW())"
        else:
            date_check = "AND (valid_until IS NULL OR valid_until >= date('now'))"

        existing = db_execute(f"""
            SELECT id FROM portal_customer_pricing
            WHERE customer_id = ?
            AND base_part_number = ?
            AND is_active = TRUE
            {date_check}
        """, (customer_id, base_part_number), fetch='one')

        if existing:
            return jsonify({'success': False, 'error': 'Active pricing agreement already exists for this part'}), 400

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                INSERT INTO portal_customer_pricing
                (customer_id, base_part_number, price, currency_id, valid_from, valid_until, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (customer_id, base_part_number, price, currency_id, valid_from, valid_until, notes),
            )
            pricing_id = _last_inserted_id(cur)

        return jsonify({'success': True, 'pricing_id': pricing_id, 'message': 'Pricing agreement added'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/customer-settings/<int:customer_id>/pricing/<int:pricing_id>', methods=['PUT'])
def update_customer_pricing(customer_id, pricing_id):
    """Update an existing pricing agreement"""
    try:
        data = request.get_json()

        price = data.get('price')
        currency_id = data.get('currency_id')
        valid_from = data.get('valid_from')
        valid_until = data.get('valid_until')
        notes = data.get('notes', '')
        is_active = data.get('is_active', 1)

        if not price:
            return jsonify({'success': False, 'error': 'Price required'}), 400

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_customer_pricing
                SET price = ?,
                    currency_id = ?,
                    valid_from = ?,
                    valid_until = ?,
                    notes = ?,
                    is_active = ?,
                    date_modified = CURRENT_TIMESTAMP
                WHERE id = ? AND customer_id = ?
                """,
                (price, currency_id, valid_from, valid_until, notes, is_active, pricing_id, customer_id),
            )

            if cur.rowcount == 0:
                return jsonify({'success': False, 'error': 'Pricing agreement not found'}), 404

        return jsonify({'success': True, 'message': 'Pricing agreement updated'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/customer-settings/<int:customer_id>/pricing/<int:pricing_id>/toggle', methods=['POST'])
def toggle_customer_pricing(customer_id, pricing_id):
    """Activate/deactivate a pricing agreement"""
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_customer_pricing
                SET is_active = NOT is_active,
                    date_modified = CURRENT_TIMESTAMP
                WHERE id = ? AND customer_id = ?
                """,
                (pricing_id, customer_id),
            )

            if cur.rowcount == 0:
                return jsonify({'success': False, 'error': 'Pricing agreement not found'}), 404

        return jsonify({'success': True})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/customer-settings/<int:customer_id>/pricing/<int:pricing_id>', methods=['DELETE'])
def delete_customer_pricing(customer_id, pricing_id):
    """Delete a pricing agreement"""
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                DELETE FROM portal_customer_pricing
                WHERE id = ? AND customer_id = ?
                """,
                (pricing_id, customer_id),
            )

            if cur.rowcount == 0:
                return jsonify({'success': False, 'error': 'Pricing agreement not found'}), 404

        return jsonify({'success': True, 'message': 'Pricing agreement deleted'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/customer-settings/<int:customer_id>/suggested-parts', methods=['POST'])
def add_suggested_part(customer_id):
    """Add a suggested part for a customer"""
    try:
        data = request.get_json()

        part_number = data.get('part_number', '').strip()
        notes = data.get('notes', '').strip()
        priority = data.get('priority', 0)

        if not part_number:
            return jsonify({'success': False, 'error': 'Part number required'}), 400

        base_part_number = create_base_part_number(part_number)

        exists = db_execute("""
            SELECT id FROM portal_suggested_parts
        WHERE customer_id = ? AND base_part_number = ? AND is_active = TRUE
        """, (customer_id, base_part_number), fetch='one')

        if exists:
            return jsonify({'success': False, 'error': 'Part already suggested to this customer'}), 400

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                INSERT INTO portal_suggested_parts
                (customer_id, base_part_number, notes, priority, suggested_by_user_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (customer_id, base_part_number, notes, priority, session.get('user_id')),
            )
            suggestion_id = _last_inserted_id(cur)

        return jsonify({'success': True, 'suggestion_id': suggestion_id, 'message': 'Part suggested'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/customer-settings/<int:customer_id>/suggested-parts/<int:suggestion_id>', methods=['PUT'])
def update_suggested_part(customer_id, suggestion_id):
    """Update a suggested part"""
    try:
        data = request.get_json()

        notes = data.get('notes', '').strip()
        priority = data.get('priority', 0)
        is_active = data.get('is_active', 1)

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_suggested_parts
                SET notes = ?,
                    priority = ?,
                    is_active = ?,
                    date_modified = CURRENT_TIMESTAMP
                WHERE id = ? AND customer_id = ?
                """,
                (notes, priority, is_active, suggestion_id, customer_id),
            )

            if cur.rowcount == 0:
                return jsonify({'success': False, 'error': 'Suggested part not found'}), 404

        return jsonify({'success': True, 'message': 'Suggested part updated'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/customer-settings/<int:customer_id>/suggested-parts/<int:suggestion_id>/toggle', methods=['POST'])
def toggle_suggested_part(customer_id, suggestion_id):
    """Activate/deactivate a suggested part"""
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_suggested_parts
                SET is_active = NOT is_active,
                    date_modified = CURRENT_TIMESTAMP
                WHERE id = ? AND customer_id = ?
                """,
                (suggestion_id, customer_id),
            )

            if cur.rowcount == 0:
                return jsonify({'success': False, 'error': 'Suggested part not found'}), 404

        return jsonify({'success': True})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/customer-settings/<int:customer_id>/suggested-parts/<int:suggestion_id>', methods=['DELETE'])
def delete_suggested_part(customer_id, suggestion_id):
    """Delete a suggested part"""
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                DELETE FROM portal_suggested_parts
                WHERE id = ? AND customer_id = ?
                """,
                (suggestion_id, customer_id),
            )

            if cur.rowcount == 0:
                return jsonify({'success': False, 'error': 'Suggested part not found'}), 404

        return jsonify({'success': True, 'message': 'Suggested part deleted'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


# Add this to your portal_admin.py file

@portal_admin_bp.route('/search-history')
def view_search_history():
    """View customer portal search history"""
    from db import _using_postgres

    customer_id = request.args.get('customer_id', type=int)
    search_type = request.args.get('search_type', '')
    days = request.args.get('days', 30, type=int)

    if _using_postgres():
        date_filter = "psh.date_searched >= NOW() - INTERVAL '1 day' * %s"
    else:
        date_filter = "psh.date_searched >= date('now', '-' || ? || ' days')"

    sql = f"""
        SELECT
            psh.*,
            pu.email as user_email,
            pu.first_name,
            pu.last_name,
            c.name as customer_name
        FROM portal_search_history psh
        JOIN portal_users pu ON pu.id = psh.portal_user_id
        JOIN customers c ON c.id = psh.customer_id
        WHERE {date_filter}
    """
    params = [days]

    if customer_id:
        sql += " AND psh.customer_id = ?"
        params.append(customer_id)

    if search_type:
        sql += " AND psh.search_type = ?"
        params.append(search_type)

    sql += " ORDER BY psh.date_searched DESC LIMIT 500"

    searches = db_execute(sql, params, fetch='all') or []

    customers = db_execute("""
        SELECT DISTINCT c.id, c.name
        FROM customers c
        JOIN portal_users pu ON pu.customer_id = c.id
        ORDER BY c.name
    """, fetch='all') or []

    if _using_postgres():
        stats_date_filter = "date_searched >= NOW() - INTERVAL '1 day' * %s"
    else:
        stats_date_filter = "date_searched >= date('now', '-' || ? || ' days')"

    stats = db_execute(f"""
        SELECT
            search_type,
            COUNT(*) as search_count,
            COUNT(DISTINCT customer_id) as unique_customers,
            SUM(parts_count) as total_parts
        FROM portal_search_history
        WHERE {stats_date_filter}
        GROUP BY search_type
    """, [days], fetch='all') or []

    import json
    most_searched_parts = []

    if _using_postgres():
        parts_date_filter = "date_searched >= NOW() - INTERVAL '1 day' * %s"
    else:
        parts_date_filter = "date_searched >= date('now', '-' || ? || ' days')"

    part_searches = db_execute(f"""
        SELECT parts_searched
        FROM portal_search_history
        WHERE search_type = 'quote_analysis'
        AND {parts_date_filter}
        AND parts_searched IS NOT NULL
    """, [days], fetch='all') or []

    # Count part occurrences
    part_counts = {}
    for search in part_searches:
        try:
            parts = json.loads(search['parts_searched'])
            for part in parts:
                pn = part.get('part_number')
                if pn:
                    base_pn = create_base_part_number(pn)
                    part_counts[base_pn] = part_counts.get(base_pn, 0) + 1
        except:
            pass

    # Sort and get top 20
    most_searched_parts = sorted(part_counts.items(), key=lambda x: x[1], reverse=True)[:20]

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Portal Admin', url_for('portal_admin.portal_admin_home')),
        ('Search History', None)
    ]

    return render_template('portal_search_history.html',
                           breadcrumbs=breadcrumbs,
                           searches=[dict(s) for s in searches],
                           customers=[dict(c) for c in customers],
                           stats=[dict(s) for s in stats],
                           most_searched_parts=most_searched_parts,
                           filters={
                               'customer_id': customer_id,
                               'search_type': search_type,
                               'days': days
                           })


@portal_admin_bp.route('/search-history/<int:search_id>')
def view_search_details(search_id):
    """View details of a specific search"""
    import json

    search = db_execute("""
        SELECT 
            psh.*,
            pu.email as user_email,
            pu.first_name,
            pu.last_name,
            c.name as customer_name
        FROM portal_search_history psh
        JOIN portal_users pu ON pu.id = psh.portal_user_id
        JOIN customers c ON c.id = psh.customer_id
        WHERE psh.id = ?
    """, (search_id,), fetch='one')

    if not search:
        flash('Search not found', 'error')
        return redirect(url_for('portal_admin.view_search_history'))

    # Parse parts if available
    parts_searched = []
    if search['parts_searched']:
        try:
            parts_searched = json.loads(search['parts_searched'])
        except:
            pass


    breadcrumbs = [
        ('Home', url_for('index')),
        ('Portal Admin', url_for('portal_admin.portal_admin_home')),
        ('Search History', url_for('portal_admin.view_search_history')),
        (f"Search #{search_id}", None)
    ]

    return render_template('portal_search_detail.html',
                           breadcrumbs=breadcrumbs,
                           search=dict(search),
                           parts_searched=parts_searched)


# ====================================================================================
# ADD THESE ROUTES TO portal_admin.py (after the last route, before the end of file)
# ====================================================================================

@portal_admin_bp.route('/test-portal')
def test_portal():
    """Test portal quote analysis interface"""
    customers = db_execute("""
        SELECT DISTINCT c.id, c.name
        FROM customers c
        JOIN portal_users pu ON pu.customer_id = c.id
        WHERE pu.is_active = TRUE
        ORDER BY c.name
    """, fetch='all') or []

    breadcrumbs = [
        ('Home', url_for('index')),
        ('Portal Admin', url_for('portal_admin.portal_admin_home')),
        ('Test Portal', None)
    ]

    return render_template('portal_test.html',
                           breadcrumbs=breadcrumbs,
                           customers=[dict(c) for c in customers])


@portal_admin_bp.route('/test-portal/analyze', methods=['POST'])
def test_portal_analyze():
    """Test endpoint that shows exactly what a customer would see, plus detailed price source info"""
    try:
        data = request.get_json()
        customer_id = data.get('customer_id')
        parts_input = data.get('parts', '').strip()

        if not parts_input:
            return jsonify({'success': False, 'error': 'Parts list required'}), 400

        # Parse parts input (one per line, optionally with quantity)
        parts = []
        for line in parts_input.split('\n'):
            line = line.strip()
            if not line:
                continue

            quantity = 1
            part_number = line

            if 'x' in line.lower():
                parts_split = line.lower().split('x')
                if len(parts_split) == 2:
                    try:
                        quantity = int(parts_split[0].strip())
                        part_number = parts_split[1].strip()
                    except ValueError:
                        try:
                            quantity = int(parts_split[1].strip())
                            part_number = parts_split[0].strip()
                        except ValueError:
                            part_number = line

            parts.append({
                'part_number': part_number.upper(),
                'quantity': quantity
            })

        if not parts:
            return jsonify({'success': False, 'error': 'No valid parts found'}), 400

        # If no customer selected, pick the first available customer
        if not customer_id or customer_id == '':
            first_customer = db_execute("""
                SELECT DISTINCT c.id
                FROM customers c
                JOIN portal_users pu ON pu.customer_id = c.id
                WHERE pu.is_active = TRUE
                LIMIT 1
            """, fetch='one')

            if not first_customer:
                return jsonify({'success': False, 'error': 'No portal customers available'}), 400

            customer_id = first_customer['id']

        # Call internal function directly (no HTTP request needed)
        result = _analyze_quote_internal(customer_id, parts)

        if isinstance(result, tuple):
            result_data = result[0].get_json()
        else:
            result_data = result.get_json()

        # Add detailed debug info with source details
        if result_data.get('success') and result_data.get('results'):
            for idx, result_item in enumerate(result_data['results']):
                base_pn = result_item.get('base_part_number')
                price_source = result_item.get('price_source')

                source_details = {}
                winning_source = price_source if price_source else 'none'

                if base_pn and price_source:
                    actual_source = price_source.replace('parts_list_', '')

                    if price_source == 'pricing_agreement':
                        pricing = db_execute("""
                            SELECT 
                                pcp.price,
                                pcp.date_created,
                                pcp.valid_from,
                                pcp.valid_until,
                                c.currency_code
                            FROM portal_customer_pricing pcp
                            LEFT JOIN currencies c ON c.id = pcp.currency_id
                            WHERE pcp.customer_id = ?
                            AND pcp.base_part_number = ?
                            AND pcp.is_active = TRUE
                            ORDER BY pcp.date_created DESC
                            LIMIT 1
                        """, (customer_id, base_pn), fetch='one')

                        if pricing:
                            source_details = {
                                'type': 'Pricing Agreement',
                                'price': f"{pricing['currency_code']} {pricing['price']:.2f}",
                                'date_created': pricing['date_created'],
                                'valid_from': pricing['valid_from'],
                                'valid_until': pricing['valid_until']
                            }

                    elif actual_source in ['customer_quote', 'recent_quote']:
                        quote = db_execute("""
                            SELECT 
                                cql.quote_price_gbp,
                                cql.date_created,
                                pl.name as parts_list_name,
                                pl.id as parts_list_id,
                                c.name as customer_name
                            FROM customer_quote_lines cql
                            JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
                            JOIN parts_lists pl ON pl.id = pll.parts_list_id
                            JOIN customers c ON c.id = pl.customer_id
                            WHERE pll.base_part_number = ?
                            AND cql.quoted_status = 'quoted'
                            ORDER BY cql.date_created DESC
                            LIMIT 1
                        """, (base_pn,), fetch='one')

                        if quote:
                            source_details = {
                                'type': 'Customer Quote',
                                'reference': f"PL-{quote['parts_list_id']}: {quote['parts_list_name']}",
                                'customer': quote['customer_name'],
                                'price': f"£{quote['quote_price_gbp']:.2f}",
                                'date': quote['date_created']
                            }

                    elif actual_source in ['sales_order', 'recent_sale']:
                        sale = db_execute("""
                            SELECT 
                                so.reference,
                                sol.unit_price_gbp,
                                so.date_created,
                                c.name as customer_name
                            FROM sales_order_lines sol
                            JOIN sales_orders so ON so.id = sol.sales_order_id
                            JOIN customers c ON c.id = so.customer_id
                            WHERE sol.base_part_number = ?
                            AND so.status != 'cancelled'
                            ORDER BY so.date_created DESC
                            LIMIT 1
                        """, (base_pn,), fetch='one')

                        if sale:
                            source_details = {
                                'type': 'Sales Order',
                                'reference': sale['reference'],
                                'customer': sale['customer_name'],
                                'price': f"£{sale['unit_price_gbp']:.2f}",
                                'date': sale['date_created']
                            }

                    elif actual_source == 'stock':
                        stock = db_execute("""
                            SELECT 
                                sm.reference_number,
                                sm.unit_cost_gbp,
                                sm.date_created,
                                s.name as supplier_name
                            FROM stock_movements sm
                            LEFT JOIN suppliers s ON s.id = sm.supplier_id
                            WHERE sm.base_part_number = ?
                            AND sm.movement_type = 'IN'
                            AND sm.available_quantity > 0
                            ORDER BY sm.date_created DESC
                            LIMIT 1
                        """, (base_pn,), fetch='one')

                        if stock:
                            source_details = {
                                'type': 'Stock Movement',
                                'reference': stock['reference_number'] or 'N/A',
                                'supplier': stock['supplier_name'] or 'Unknown',
                                'cost': f"£{stock['unit_cost_gbp']:.2f}" if stock['unit_cost_gbp'] else 'N/A',
                                'date': stock['date_created']
                            }

                    elif actual_source in ['vendor_quote', 'vendor_quote_estimate']:
                        vq = db_execute("""
                            SELECT 
                                vq.reference,
                                vql.unit_price,
                                vq.date_received,
                                s.name as supplier_name,
                                c.currency_code
                            FROM vendor_quote_lines vql
                            JOIN vendor_quotes vq ON vq.id = vql.vendor_quote_id
                            JOIN suppliers s ON s.id = vq.supplier_id
                            LEFT JOIN currencies c ON c.id = vql.currency_id
                            WHERE vql.base_part_number = ?
                            AND vq.status = 'received'
                            ORDER BY vq.date_received DESC
                            LIMIT 1
                        """, (base_pn,), fetch='one')

                        if vq:
                            source_details = {
                                'type': 'Vendor Quote',
                                'reference': vq['reference'],
                                'supplier': vq['supplier_name'],
                                'price': f"{vq['currency_code']} {vq['unit_price']:.2f}",
                                'date': vq['date_received']
                            }

                    elif actual_source in ['purchase_order', 'purchase_order_estimate']:
                        po = db_execute("""
                            SELECT 
                                po.reference,
                                pol.unit_price,
                                po.date_created,
                                s.name as supplier_name,
                                c.currency_code
                            FROM purchase_order_lines pol
                            JOIN purchase_orders po ON po.id = pol.purchase_order_id
                            JOIN suppliers s ON s.id = po.supplier_id
                            LEFT JOIN currencies c ON c.id = pol.currency_id
                            WHERE pol.base_part_number = ?
                            AND po.status != 'cancelled'
                            ORDER BY po.date_created DESC
                            LIMIT 1
                        """, (base_pn,), fetch='one')

                        if po:
                            source_details = {
                                'type': 'Purchase Order',
                                'reference': po['reference'],
                                'supplier': po['supplier_name'],
                                'price': f"{po['currency_code']} {po['unit_price']:.2f}",
                                'date': po['date_created']
                            }

                result_data['results'][idx]['debug_info'] = {
                    'winning_source': winning_source,
                    'source_details': source_details
                }

            portal_user = db_execute("""
                SELECT email FROM portal_users
                WHERE customer_id = ? AND is_active = TRUE
                LIMIT 1
            """, (customer_id,), fetch='one')

            result_data['test_info'] = {
                'customer_id': customer_id,
                'portal_user_email': portal_user['email'] if portal_user else None
            }

        return jsonify(result_data)

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500
# ============================================================================
# ADD THESE ROUTES TO portal_admin.py (in your office CRM)
# These let you (supplier) view and manage incoming customer POs
# ============================================================================

@portal_admin_bp.route('/purchase-orders')
def admin_portal_pos():
    """View all incoming portal purchase orders"""
    try:
        pos = db_execute("""
            SELECT 
                ppo.*,
                c.name as customer_name,
                pu.first_name as submitted_by_first_name,
                pu.last_name as submitted_by_last_name,
                pu.email as submitted_by_email,
                curr.currency_code
            FROM portal_purchase_orders ppo
            JOIN customers c ON c.id = ppo.customer_id
            JOIN portal_users pu ON pu.id = ppo.portal_user_id
            LEFT JOIN currencies curr ON curr.id = ppo.currency_id
            ORDER BY ppo.date_submitted DESC
        """, fetch='all') or []

        status_counts = {}
        for po in pos:
            status = po['status']
            status_counts[status] = status_counts.get(status, 0) + 1

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Portal Admin', url_for('portal_admin.portal_admin_home')),
            ('Purchase Orders', None)
        ]

        return render_template('admin_portal_pos.html',
                               breadcrumbs=breadcrumbs,
                               purchase_orders=[dict(po) for po in pos],
                               status_counts=status_counts)

    except Exception as e:
        logging.exception(e)
        flash('Error loading portal purchase orders', 'danger')
        return redirect(url_for('portal_admin.portal_admin_home'))


@portal_admin_bp.route('/purchase-orders/<int:po_id>/details')
def admin_portal_po_details(po_id):
    """Get PO details via AJAX"""
    try:
        po = db_execute("""
            SELECT 
                ppo.*,
                c.name as customer_name,
                pu.first_name as submitted_by_first_name,
                pu.last_name as submitted_by_last_name,
                pu.email as submitted_by_email,
                curr.currency_code
            FROM portal_purchase_orders ppo
            JOIN customers c ON c.id = ppo.customer_id
            JOIN portal_users pu ON pu.id = ppo.portal_user_id
            LEFT JOIN currencies curr ON curr.id = ppo.currency_id
            WHERE ppo.id = ?
        """, (po_id,), fetch='one')

        if not po:
            return jsonify({'success': False, 'error': 'PO not found'}), 404

        # Get lines
        lines = db_execute("""
            SELECT * FROM portal_purchase_order_lines
            WHERE portal_purchase_order_id = ?
            ORDER BY line_number
        """, (po_id,), fetch='all') or []

        return jsonify({
            'success': True,
            'po': dict(po),
            'lines': [dict(line) for line in lines]
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/purchase-orders/<int:po_id>/update-status', methods=['POST'])
def admin_update_po_status(po_id):
    """Update PO status"""
    try:
        data = request.get_json()
        new_status = data.get('status')
        notes = data.get('notes', '')

        if not new_status:
            return jsonify({'success': False, 'error': 'Status required'}), 400

        valid_statuses = ['submitted', 'acknowledged', 'processing', 'dispatched', 'completed', 'cancelled']
        if new_status not in valid_statuses:
            return jsonify({'success': False, 'error': 'Invalid status'}), 400

        po = db_execute(
            "SELECT * FROM portal_purchase_orders WHERE id = ?",
            (po_id,),
            fetch='one'
        )
        if not po:
            return jsonify({'success': False, 'error': 'PO not found'}), 404

        # Append notes if provided
        update_notes = po['internal_notes'] or ''
        if notes:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
            update_notes = f"{update_notes}\n\n[{timestamp}] Status changed to {new_status}:\n{notes}".strip()

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                """
                UPDATE portal_purchase_orders
                SET status = ?,
                    date_acknowledged = CASE 
                        WHEN ? = 'acknowledged' AND date_acknowledged IS NULL 
                        THEN CURRENT_TIMESTAMP 
                        ELSE date_acknowledged 
                    END,
                    date_dispatched = CASE 
                        WHEN ? = 'dispatched' AND date_dispatched IS NULL 
                        THEN CURRENT_TIMESTAMP 
                        ELSE date_dispatched 
                    END,
                    internal_notes = ?,
                    date_modified = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_status, new_status, new_status, update_notes, po_id),
            )

        return jsonify({'success': True, 'message': 'Status updated successfully'})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@portal_admin_bp.route('/purchase-orders/pending-count')
def admin_pending_pos_count():
    """Get count of pending POs for dashboard widget"""
    try:
        count = db_execute("""
            SELECT COUNT(*) as cnt
            FROM portal_purchase_orders
            WHERE status = 'submitted'
        """, fetch='one')

        return jsonify({
            'success': True,
            'count': count['cnt'] if count else 0
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

@portal_admin_bp.route('/pricing-agreement-requests')
def pricing_agreement_requests():
    """View all pricing agreement requests from customers"""
    try:
        status_filter = request.args.get('status', '')
        customer_id = request.args.get('customer_id', type=int)

        # Get filter parameters
        # Build query
        sql = """
            SELECT 
                par.*,
                pu.email as user_email,
                pu.first_name,
                pu.last_name,
                c.name as customer_name,
                u.username as processed_by,
                pn.part_number as full_part_number
            FROM portal_pricing_agreement_requests par
            JOIN portal_users pu ON pu.id = par.portal_user_id
            JOIN customers c ON c.id = par.customer_id
            LEFT JOIN users u ON u.id = par.processed_by_user_id
            LEFT JOIN part_numbers pn ON pn.base_part_number = par.base_part_number
        """

        params = []
        where_clauses = []

        if status_filter:
            where_clauses.append("par.status = ?")
            params.append(status_filter)

        if customer_id:
            where_clauses.append("par.customer_id = ?")
            params.append(customer_id)

        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)

        sql += " ORDER BY par.date_submitted DESC"

        requests = db_execute(sql, params, fetch='all') or []

        customers = db_execute("""
            SELECT DISTINCT c.id, c.name
            FROM customers c
            JOIN portal_pricing_agreement_requests par ON par.customer_id = c.id
            ORDER BY c.name
        """, fetch='all') or []

        status_counts = db_execute("""
            SELECT status, COUNT(*) as count
            FROM portal_pricing_agreement_requests
            GROUP BY status
        """, fetch='all') or []

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Portal Admin', url_for('portal_admin.portal_admin_home')),
            ('Agreement Requests', None)
        ]

        return render_template('portal_agreement_requests.html',
                               breadcrumbs=breadcrumbs,
                               requests=[dict(r) for r in requests],
                               customers=[dict(c) for c in customers],
                               status_counts={s['status']: s['count'] for s in status_counts},
                               filters={
                                   'status': status_filter,
                                   'customer_id': customer_id
                               })

    except Exception as e:
        logging.exception(e)
        flash('Error loading agreement requests', 'danger')
        return redirect(url_for('portal_admin.portal_admin_home'))


@portal_admin_bp.route('/pricing-agreement-requests/<int:request_id>/process', methods=['POST'])
def process_agreement_request(request_id):
    """Approve or reject a pricing agreement request"""
    try:
        data = request.get_json()
        action = data.get('action')  # 'approve' or 'reject'
        price = data.get('price')
        base_currency_id = _get_base_currency().get('id')
        currency_id = data.get('currency_id', base_currency_id)
        valid_from = data.get('valid_from')
        valid_until = data.get('valid_until')
        notes = data.get('notes', '')

        if action not in ['approve', 'reject']:
            return jsonify({'success': False, 'error': 'Invalid action'}), 400

        req = db_execute(
            "SELECT * FROM portal_pricing_agreement_requests WHERE id = ?",
            (request_id,),
            fetch='one'
        )

        if not req:
            return jsonify({'success': False, 'error': 'Request not found'}), 404

        if action == 'approve' and not price:
            return jsonify({'success': False, 'error': 'Price required for approval'}), 400

        with db_cursor(commit=True) as cur:
            if action == 'approve':
                _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO portal_customer_pricing
                    (customer_id, base_part_number, price, currency_id, valid_from, valid_until, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        req['customer_id'],
                        req['base_part_number'],
                        price,
                        currency_id,
                        valid_from,
                        valid_until,
                        notes,
                    ),
                )
                new_status = 'approved'
                message = 'Pricing agreement created and approved'
            else:
                new_status = 'rejected'
                message = 'Request rejected'

            _execute_with_cursor(
                cur,
                """
                UPDATE portal_pricing_agreement_requests
                SET status = ?,
                    date_processed = CURRENT_TIMESTAMP,
                    processed_by_user_id = ?,
                    internal_notes = ?
                WHERE id = ?
                """,
                (new_status, session.get('user_id'), notes, request_id),
            )

        return jsonify({'success': True, 'message': message})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

def get_email_config():
    """Get email configuration from portal settings"""
    return {
        'smtp_server': 'mail.privateemail.com',
        'smtp_port': 465,
        'smtp_username': 'tom@sproutt.app',
        'smtp_password': get_portal_setting('email_password', ''),
        'from_email': 'tom@sproutt.app',
        'from_name': 'Sproutt admin',
        'notification_email': get_portal_setting('notification_email', 'tom@sproutt.app')
    }


def send_email(to_email, subject, html_body, text_body=None):
    """Send an email using configured SMTP settings"""
    try:
        config = get_email_config()

        if not config['smtp_password']:
            logging.warning("Email password not configured in portal settings")
            return False

        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = formataddr((config['from_name'], config['from_email']))
        msg['To'] = to_email

        # Add text and HTML parts
        if text_body:
            part1 = MIMEText(text_body, 'plain')
            msg.attach(part1)

        part2 = MIMEText(html_body, 'html')
        msg.attach(part2)

        # Send via SMTP
        with smtplib.SMTP_SSL(config['smtp_server'], config['smtp_port']) as server:
            server.login(config['smtp_username'], config['smtp_password'])
            server.send_message(msg)

        logging.info(f"Email sent successfully to {to_email}: {subject}")
        return True

    except Exception as e:
        logging.exception(f"Failed to send email to {to_email}: {e}")
        return False

def notify_new_quote_request(request_id):
    """Send email notification for new quote request"""
    try:
        req = db_execute("""
            SELECT
                pqr.*,
                pu.email as user_email,
                pu.first_name,
                pu.last_name,
                c.name as customer_name,
                (
                    SELECT COUNT(*)
                    FROM portal_quote_request_lines pqrl
                    WHERE pqrl.portal_quote_request_id = pqr.id
                ) as line_count
            FROM portal_quote_requests pqr
            JOIN portal_users pu ON pu.id = pqr.portal_user_id
            JOIN customers c ON c.id = pqr.customer_id
            WHERE pqr.id = ?
        """, (request_id,), fetch='one')

        if not req:
            return False

        parts = db_execute("""
            SELECT part_number, quantity
            FROM portal_quote_request_lines
            WHERE portal_quote_request_id = ?
            ORDER BY line_number
            LIMIT 10
        """, (request_id,), fetch='all') or []

        config = get_email_config()

        # Build parts list for email
        parts_list_html = "<ul>"
        parts_list_text = ""
        for part in parts:
            parts_list_html += f"<li><strong>{part['part_number']}</strong> - Qty: {part['quantity']}</li>"
            parts_list_text += f"  • {part['part_number']} - Qty: {part['quantity']}\n"
        parts_list_html += "</ul>"

        if req['line_count'] > 10:
            parts_list_html += f"<p><em>...and {req['line_count'] - 10} more parts</em></p>"
            parts_list_text += f"  ...and {req['line_count'] - 10} more parts\n"

        # FIXED: Check if customer_notes exists and has content
        customer_notes_html = ''
        customer_notes_text = ''
        if req['customer_notes']:
            customer_notes_html = f'<p><strong>Customer Notes:</strong><br>{req["customer_notes"]}</p>'
            customer_notes_text = f"Customer Notes:\n{req['customer_notes']}\n"

        # HTML email body
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #0066cc;">Hello, Handsome! New Quote Request Received</h2>

            <p>A new quote request has been submitted via the customer portal.</p>

            <table style="border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Reference:</td>
                    <td style="padding: 8px;">{req['reference_number']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Customer:</td>
                    <td style="padding: 8px;">{req['customer_name']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Submitted By:</td>
                    <td style="padding: 8px;">{req['first_name']} {req['last_name']} ({req['user_email']})</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Parts Count:</td>
                    <td style="padding: 8px;">{req['line_count']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Date:</td>
                    <td style="padding: 8px;">{req['date_submitted']}</td>
                </tr>
            </table>

            <h3>Parts Requested:</h3>
            {parts_list_html}

            {customer_notes_html}

            <p style="margin-top: 30px;">
                <a href="http://your-crm-domain.com/portal-admin/requests/{request_id}" 
                   style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px;">
                    View Quote Request
                </a>
            </p>
        </body>
        </html>
        """

        # Plain text version
        text_body = f"""
New Quote Request Received

Reference: {req['reference_number']}
Customer: {req['customer_name']}
Submitted By: {req['first_name']} {req['last_name']} ({req['user_email']})
Parts Count: {req['line_count']}
Date: {req['date_submitted']}

Parts Requested:
{parts_list_text}

{customer_notes_text}

View in CRM: http://your-crm-domain.com/portal-admin/requests/{request_id}
        """

        # Send email
        return send_email(
            config['notification_email'],
            f"New Quote Request: {req['reference_number']} - {req['customer_name']}",
            html_body,
            text_body
        )

    except Exception as e:
        logging.exception(f"Failed to send quote request notification: {e}")
        return False


def notify_new_pricing_agreement_request(request_id):
    """Send email notification for new pricing agreement request"""
    try:
        req = db_execute("""
            SELECT 
                par.*,
                pu.email as user_email,
                pu.first_name,
                pu.last_name,
                c.name as customer_name,
                pn.part_number as full_part_number
            FROM portal_pricing_agreement_requests par
            JOIN portal_users pu ON pu.id = par.portal_user_id
            JOIN customers c ON c.id = par.customer_id
            LEFT JOIN part_numbers pn ON pn.base_part_number = par.base_part_number
            WHERE par.id = ?
        """, (request_id,), fetch='one')

        if not req:
            return False

        config = get_email_config()

        # FIXED: Check if customer_notes exists and has content
        customer_notes_html = ''
        customer_notes_text = ''
        if req['customer_notes']:
            customer_notes_html = f'<p><strong>Customer Notes:</strong><br>{req["customer_notes"]}</p>'
            customer_notes_text = f"Customer Notes:\n{req['customer_notes']}\n"

        # HTML email body
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #0066cc;">New Pricing Agreement Request</h2>

            <p>A customer has requested a pricing agreement via the portal.</p>

            <table style="border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Customer:</td>
                    <td style="padding: 8px;">{req['customer_name']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Requested By:</td>
                    <td style="padding: 8px;">{req['first_name']} {req['last_name']} ({req['user_email']})</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Part Number:</td>
                    <td style="padding: 8px;"><strong>{req['full_part_number'] or req['base_part_number']}</strong></td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Expected Quantity:</td>
                    <td style="padding: 8px;">{req['expected_quantity']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Date:</td>
                    <td style="padding: 8px;">{req['date_submitted']}</td>
                </tr>
            </table>

            {customer_notes_html}

            <p style="margin-top: 30px;">
                <a href="http://your-crm-domain.com/portal-admin/pricing-agreement-requests" 
                   style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px;">
                    View Agreement Request
                </a>
            </p>
        </body>
        </html>
        """

        # Plain text version
        text_body = f"""
New Pricing Agreement Request

Customer: {req['customer_name']}
Requested By: {req['first_name']} {req['last_name']} ({req['user_email']})
Part Number: {req['full_part_number'] or req['base_part_number']}
Expected Quantity: {req['expected_quantity']}
Date: {req['date_submitted']}

{customer_notes_text}

View in CRM: http://your-crm-domain.com/portal-admin/pricing-agreement-requests
        """

        # Send email
        return send_email(
            config['notification_email'],
            f"Pricing Agreement Request: {req['full_part_number'] or req['base_part_number']} - {req['customer_name']}",
            html_body,
            text_body
        )

    except Exception as e:
        logging.exception(f"Failed to send pricing agreement request notification: {e}")
        return False
