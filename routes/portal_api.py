# this is portal_api.py - it lives in the office and serves the core CRM. It sends and receives data to/from the external portal app

import calendar
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, date

import jwt
from flask import Blueprint, current_app, jsonify, request
from functools import wraps
from werkzeug.security import check_password_hash

from db import db_cursor, execute as db_execute, get_currency_rate_column
from models import create_base_part_number

# ----------------------------------------------------------------------------
portal_api_bp = Blueprint('portal_api', __name__, url_prefix='/api/portal')


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _to_float(value):
    """Coerce numeric values to float to keep JSON responses numeric."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _sort_date_key(value):
    """Normalize mixed date/datetime values for consistent sorting."""
    if value is None:
        return datetime.min
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return datetime.min


_PORTAL_QUOTE_REQUESTS_HAS_CUSTOMER_REFERENCE = None


def _portal_quote_requests_has_customer_reference():
    """Check once whether customer_reference exists on portal_quote_requests."""
    global _PORTAL_QUOTE_REQUESTS_HAS_CUSTOMER_REFERENCE
    if _PORTAL_QUOTE_REQUESTS_HAS_CUSTOMER_REFERENCE is not None:
        return _PORTAL_QUOTE_REQUESTS_HAS_CUSTOMER_REFERENCE

    has_column = False
    try:
        if _using_postgres():
            result = db_execute("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'portal_quote_requests'
                  AND column_name = 'customer_reference'
                LIMIT 1
            """, fetch='one')
            has_column = bool(result)
        else:
            columns = db_execute("PRAGMA table_info(portal_quote_requests)", fetch='all') or []
            has_column = any(
                (col.get('name') if isinstance(col, dict) else col['name']) == 'customer_reference'
                for col in columns
            )
    except Exception:
        has_column = False

    _PORTAL_QUOTE_REQUESTS_HAS_CUSTOMER_REFERENCE = has_column
    return has_column


def _months_ago(reference: date, months: int) -> date:
    if months <= 0:
        return reference

    year = reference.year
    month = reference.month - months
    while month <= 0:
        month += 12
        year -= 1

    day = min(reference.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# ============================================================================
# AUTHENTICATION & SECURITY
# ============================================================================

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
        else:
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
        today = datetime.utcnow().date()
        rate_column = get_currency_rate_column()
        pricing = db_execute(f"""
            SELECT price, currency_id, c.{rate_column} as currency_rate
            FROM portal_customer_pricing pcp
            LEFT JOIN currencies c ON c.id = pcp.currency_id
            WHERE pcp.customer_id = ?
            AND pcp.base_part_number = ?
            AND pcp.is_active = TRUE
            AND (pcp.valid_from IS NULL OR pcp.valid_from <= ?)
            AND (pcp.valid_until IS NULL OR pcp.valid_until >= ?)
            ORDER BY pcp.date_created DESC
            LIMIT 1
        """, (customer_id, base_part_number, today, today), fetch='one')

        if pricing:
            # Convert to GBP if needed
            price_gbp = _to_float(pricing['price'])
            if pricing['currency_id'] != 3:  # Not GBP
                exchange_rate = _to_float(pricing['currency_rate'] or 1)
                if exchange_rate != 0:
                    price_gbp = price_gbp / exchange_rate

            return price_gbp

        return None
    except Exception as e:
        logging.exception(e)
        return None


def require_portal_auth(f):
    """Decorator to require portal authentication"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # BYPASS: If portal_user is already set (internal call), skip auth
        if hasattr(request, 'portal_user') and request.portal_user:
            return f(*args, **kwargs)

        # Normal auth flow continues...
        api_key = request.headers.get('X-API-Key')
        stored_key = get_portal_setting('api_key')

        if not api_key or not stored_key or api_key != stored_key:
            log_api_call(request.path, request.method, None, None, None, 401, request.remote_addr)
            return jsonify({'success': False, 'error': 'Invalid API key'}), 401

        # Check for JWT token (user session)
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            log_api_call(request.path, request.method, None, None, None, 401, request.remote_addr)
            return jsonify({'success': False, 'error': 'Missing authentication token'}), 401

        token = auth_header.split(' ')[1]

        try:
            # USE THE SAME SHARED SECRET
            jwt_secret = get_portal_setting('jwt_secret', 'shared-portal-jwt-secret-change-me')
            payload = jwt.decode(token, jwt_secret, algorithms=['HS256'])

            # Verify user still exists and is active
            user = db_execute("""
                SELECT pu.*, c.name as customer_name
                FROM portal_users pu
                JOIN customers c ON c.id = pu.customer_id
                WHERE pu.id = ? AND pu.is_active = TRUE
            """, (payload['user_id'],), fetch='one')

            if not user:
                return jsonify({'success': False, 'error': 'User not found or inactive'}), 401

            # Attach user info to request context
            request.portal_user = dict(user)

        except jwt.ExpiredSignatureError:
            return jsonify({'success': False, 'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'success': False, 'error': 'Invalid token'}), 401

        return f(*args, **kwargs)

    return decorated_function


def log_api_call(endpoint, method, portal_user_id, customer_id, request_data, status, ip_address):
    """Log API calls for security/debugging"""
    try:
        db_execute("""
            INSERT INTO portal_api_log 
            (endpoint, method, portal_user_id, customer_id, request_data, response_status, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            endpoint,
            method,
            portal_user_id,
            customer_id,
            str(request_data)[:1000],
            status,
            ip_address,
        ), commit=True)
    except Exception as e:
        logging.exception(f"Failed to log API call: {e}")


def log_search_history(portal_user_id, customer_id, search_type, parts_list, ip_address=None, user_agent=None):
    """Log customer search history for analytics"""
    try:
        parts_json = json.dumps(parts_list) if parts_list else None
        parts_count = len(parts_list) if parts_list else 0

        db_execute("""
            INSERT INTO portal_search_history 
            (portal_user_id, customer_id, search_type, parts_searched, parts_count, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            portal_user_id,
            customer_id,
            search_type,
            parts_json,
            parts_count,
            ip_address,
            user_agent,
        ), commit=True)
    except Exception as e:
        logging.exception(f"Failed to log search history: {e}")


# ============================================================================
# AUTHENTICATION ENDPOINTS
# ============================================================================
@portal_api_bp.route('/auth/login', methods=['POST'])
def portal_login():
    """Portal user login - returns JWT token"""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        if not email or not password:
            return jsonify({'success': False, 'error': 'Email and password required'}), 400
        user = db_execute("""
            SELECT pu.*, c.name as customer_name
            FROM portal_users pu
            JOIN customers c ON c.id = pu.customer_id
            WHERE LOWER(pu.email) = ? AND pu.is_active = TRUE
        """, (email,), fetch='one')

        if not user or not check_password_hash(user['password_hash'], password):
            log_api_call('/auth/login', 'POST', None, None, {'email': email}, 401, request.remote_addr)
            return jsonify({'success': False, 'error': 'Invalid email or password'}), 401

        # Update last login
        db_execute(
            "UPDATE portal_users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
            (user['id'],),
            commit=True,
        )

        # USE A SHARED SECRET KEY (not Flask's secret)
        # Get from portal settings or use a hardcoded one
        jwt_secret = get_portal_setting('jwt_secret', 'shared-portal-jwt-secret-change-me')

        token = jwt.encode({
            'user_id': user['id'],
            'customer_id': user['customer_id'],
            'email': user['email'],
            'exp': datetime.utcnow() + timedelta(days=7)
        }, jwt_secret, algorithm='HS256')

        log_api_call('/auth/login', 'POST', user['id'], user['customer_id'], {'email': email}, 200, request.remote_addr)

        return jsonify({
            'success': True,
            'token': token,
            'user': {
                'id': user['id'],
                'email': user['email'],
                'first_name': user['first_name'],
                'last_name': user['last_name'],
                'customer_name': user['customer_name']
            }
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': 'Login failed'}), 500


@portal_api_bp.route('/auth/refresh', methods=['POST'])
@require_portal_auth
def refresh_token():
    """Refresh JWT token"""
    try:
        user = request.portal_user

        secret = current_app.config.get('SECRET_KEY', 'your-secret-key')
        token = jwt.encode({
            'user_id': user['id'],
            'customer_id': user['customer_id'],
            'email': user['email'],
            'exp': datetime.utcnow() + timedelta(days=7)
        }, secret, algorithm='HS256')

        return jsonify({'success': True, 'token': token})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': 'Token refresh failed'}), 500


# ============================================================================
# QUOTE ANALYSIS ENDPOINT
# ============================================================================

@portal_api_bp.route('/quote/analyze', methods=['POST'])
@require_portal_auth
def analyze_quote():
    """
    Analyze parts for customer quote
    Returns stock availability, estimated pricing, and debug info for the portal test page
    """
    try:
        user = request.portal_user
        data = request.get_json()
        parts = data.get('parts', [])

        if not parts:
            return jsonify({'success': False, 'error': 'No parts provided'}), 400

        margins = get_customer_margins(user['customer_id'])
        stock_margin = margins['stock']
        vq_margin = margins['vq']
        po_margin = margins['po']

        so_months = int(get_portal_setting('sales_order_recency_months', 6))
        vq_months = int(get_portal_setting('vq_recency_months', 12))
        po_months = int(get_portal_setting('po_recency_months', 12))
        cq_months = int(get_portal_setting('cq_recency_months', 6))
        min_stock = int(get_portal_setting('min_stock_threshold', 1))

        show_quantities = bool(int(get_portal_setting('show_stock_quantities', 1)))
        show_estimates = bool(int(get_portal_setting('show_estimated_prices', 1)))
        default_lead_days = int(get_portal_setting('default_lead_time_days', 7))

        today = datetime.utcnow().date()
        so_cutoff = _months_ago(today, so_months)
        vq_cutoff = _months_ago(today, vq_months)
        po_cutoff = _months_ago(today, po_months)
        cq_cutoff = _months_ago(today, cq_months)

        results = []

        with db_cursor() as cursor:
            for part in parts:
                part_number = part.get('part_number', '').strip()
                if not part_number:
                    continue

                quantity = int(part.get('quantity', 1))
                base_part_number = create_base_part_number(part_number)

                try:
                    agreement_price = get_customer_pricing_agreement(user['customer_id'], base_part_number)
                    if agreement_price:
                        result = {
                            'part_number': part_number,
                            'base_part_number': base_part_number,
                            'quantity_requested': quantity,
                            'in_stock': False,
                            'estimated_price': agreement_price,
                            'price_source': 'pricing_agreement',
                            'currency': 'GBP',
                            'status': 'available',
                            'estimated_lead_days': 0,
                            'debug_info': {
                                'winning_source': 'pricing_agreement',
                                'source_details': {'type': 'Contract', 'price': agreement_price}
                            }
                        }
                        if show_quantities:
                            result['stock_quantity'] = 0
                        else:
                            result['stock_available'] = False
                        results.append(result)
                        continue

                    stock = _execute_with_cursor(cursor, """
                        SELECT SUM(available_quantity) as total_stock, AVG(cost_per_unit) as avg_cost
                        FROM stock_movements
                        WHERE base_part_number = ? AND movement_type = 'IN' AND available_quantity >= ?
                    """, (base_part_number, min_stock)).fetchone()

                    total_stock = stock['total_stock'] if stock and stock['total_stock'] else 0
                    has_stock = total_stock >= quantity
                    avg_cost_value = _to_float(stock['avg_cost']) if stock and stock['avg_cost'] else None
                    stock_price = round(avg_cost_value * (1 + stock_margin / 100), 2) if (has_stock and avg_cost_value is not None) else None

                    cq_price = _execute_with_cursor(cursor, """
                        SELECT cl.unit_price as most_recent_price, c.entry_date as quote_date, curr.currency_code, c.cq_number
                        FROM cq_lines cl JOIN cqs c ON cl.cq_id = c.id
                        LEFT JOIN currencies curr ON c.currency_id = curr.id
                        WHERE cl.base_part_number = ? AND c.entry_date >= ?
                        AND cl.unit_price > 0 AND cl.is_no_quote = FALSE
                        ORDER BY c.entry_date DESC LIMIT 1
                    """, (base_part_number, cq_cutoff)).fetchone()

                    sales_price = _execute_with_cursor(cursor, """
                        SELECT sol.price as most_recent_price, so.date_entered as sale_date, curr.currency_code, so.sales_order_ref
                        FROM sales_order_lines sol JOIN sales_orders so ON sol.sales_order_id = so.id
                        LEFT JOIN currencies curr ON so.currency_id = curr.id
                        WHERE sol.base_part_number = ? AND so.date_entered >= ?
                        AND sol.price > 0 ORDER BY so.date_entered DESC LIMIT 1
                    """, (base_part_number, so_cutoff)).fetchone()

                    pl_customer_quote = _execute_with_cursor(cursor, """
                        SELECT cql.quote_price_gbp as most_recent_price, cql.date_created as quote_date
                        FROM customer_quote_lines cql JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
                        WHERE pll.base_part_number = ? AND cql.quoted_status = 'quoted'
                        AND cql.quote_price_gbp > 0 AND COALESCE(cql.is_no_bid, 0) = 0
                        AND cql.date_created >= ?
                        ORDER BY cql.date_created DESC LIMIT 1
                    """, (base_part_number, cq_cutoff)).fetchone()

                    pl_supplier_quote_price = None
                    pl_supplier_quote_date = None
                    pl_supplier_details = {}

                    rate_column = get_currency_rate_column()
                    pl_supplier_quote = _execute_with_cursor(cursor, f"""
                        SELECT
                            sql.unit_price as supplier_cost,
                            COALESCE(sq.quote_date, sq.date_created) as effective_date,
                            sq.currency_id,
                            c.{rate_column} as currency_rate,
                            s.name as supplier_name,
                            sq.quote_reference
                        FROM parts_list_supplier_quote_lines sql
                        JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                        JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                        LEFT JOIN suppliers s ON s.id = sq.supplier_id
                        LEFT JOIN currencies c ON c.id = sq.currency_id
                        WHERE pll.base_part_number = ?
                            AND sql.unit_price > 0
                            AND (sql.is_no_bid = FALSE OR sql.is_no_bid IS NULL)
                            AND COALESCE(sq.quote_date, sq.date_created) >= ?
                        ORDER BY COALESCE(sq.quote_date, sq.date_created) DESC
                        LIMIT 1
                    """, (base_part_number, vq_cutoff)).fetchone()

                    if pl_supplier_quote:
                        pl_supplier_quote_date = pl_supplier_quote['effective_date']
                        cost_gbp = _to_float(pl_supplier_quote['supplier_cost'])
                        if pl_supplier_quote['currency_id'] and pl_supplier_quote['currency_id'] != 3:
                            exchange_rate = _to_float(pl_supplier_quote['currency_rate'] or 1)
                            if exchange_rate != 0:
                                cost_gbp = cost_gbp / exchange_rate

                        pl_supplier_quote_price = round(cost_gbp * (1 + vq_margin / 100), 2)
                        pl_supplier_details = {
                            'supplier': pl_supplier_quote['supplier_name'],
                            'cost': _to_float(pl_supplier_quote['supplier_cost']),
                            'reference': pl_supplier_quote['quote_reference'],
                            'date': pl_supplier_quote_date
                        }

                    vq_price = None
                    vq_date = None
                    vq_details = {}
                    po_price = None
                    po_date = None
                    po_details = {}

                    if show_estimates:
                        vq_data = _execute_with_cursor(cursor, """
                            SELECT vl.vendor_price, v.entry_date, s.name as supplier_name, v.vq_number
                            FROM vq_lines vl JOIN vqs v ON vl.vq_id = v.id
                            LEFT JOIN suppliers s ON s.id = v.supplier_id
                            WHERE vl.base_part_number = ? AND v.entry_date >= ?
                            AND vl.vendor_price > 0 ORDER BY v.entry_date DESC LIMIT 1
                        """, (base_part_number, vq_cutoff)).fetchone()
                        if vq_data:
                            vendor_price = _to_float(vq_data['vendor_price'])
                            vq_price = round(vendor_price * (1 + vq_margin / 100), 2)
                            vq_date = vq_data['entry_date']
                            vq_details = {
                                'supplier': vq_data['supplier_name'],
                                'cost': vendor_price,
                                'reference': vq_data['vq_number']
                            }

                        po_data = _execute_with_cursor(cursor, """
                            SELECT pol.price, po.date_issued, s.name as supplier_name, po.purchase_order_ref
                            FROM purchase_order_lines pol JOIN purchase_orders po ON pol.purchase_order_id = po.id
                            LEFT JOIN suppliers s ON s.id = po.supplier_id
                            WHERE pol.base_part_number = ? AND po.date_issued >= ?
                            AND pol.price > 0 ORDER BY po.date_issued DESC LIMIT 1
                        """, (base_part_number, po_cutoff)).fetchone()
                        if po_data:
                            po_price_base = _to_float(po_data['price'])
                            po_price = round(po_price_base * (1 + po_margin / 100), 2)
                            po_date = po_data['date_issued']
                            po_details = {
                                'supplier': po_data['supplier_name'],
                                'cost': po_price_base,
                                'reference': po_data['purchase_order_ref']
                            }

                    estimated_lead_days = default_lead_days
                    if has_stock:
                        estimated_lead_days = 0
                    else:
                        sq_lead = _execute_with_cursor(cursor, """
                            SELECT sql.lead_time_days, s.buffer
                            FROM parts_list_supplier_quote_lines sql
                            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                            JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                            LEFT JOIN suppliers s ON s.id = sq.supplier_id
                            WHERE pll.base_part_number = ?
                            ORDER BY COALESCE(sq.quote_date, sq.date_created) DESC LIMIT 1
                        """, (base_part_number,)).fetchone()

                        if sq_lead:
                            lead_time = int(sq_lead['lead_time_days'] or 0)
                            buffer_days = int(sq_lead['buffer'] or 0)
                            estimated_lead_days = lead_time + buffer_days

                    estimated_lead_days = int(estimated_lead_days) if estimated_lead_days > 0 else 0

                    result = {
                        'part_number': part_number,
                        'base_part_number': base_part_number,
                        'quantity_requested': quantity,
                        'in_stock': has_stock,
                        'stock_available': has_stock
                    }
                    if show_quantities:
                        result['stock_quantity'] = int(total_stock) if total_stock else 0

                    debug_info = {}

                    if stock_price and has_stock:
                        result['estimated_price'] = stock_price
                        result['price_source'] = 'stock'
                        result['currency'] = 'GBP'
                        result['status'] = 'available'
                        debug_info = {
                            'winning_source': 'stock',
                            'source_details': {'cost': avg_cost_value, 'type': 'Inventory'}
                        }
                    else:
                        price_candidates = []

                        if cq_price and cq_price['most_recent_price']:
                            price_candidates.append({
                                'price': round(cq_price['most_recent_price'], 2),
                                'source': 'recent_quote',
                                'currency': cq_price['currency_code'] or 'GBP',
                                'date': cq_price['quote_date'],
                                'priority': 1,
                                'details': {'reference': cq_price['cq_number'], 'type': 'Historic Quote'}
                            })
                        if sales_price and sales_price['most_recent_price']:
                            price_candidates.append({
                                'price': round(sales_price['most_recent_price'], 2),
                                'source': 'recent_sale',
                                'currency': sales_price['currency_code'] or 'GBP',
                                'date': sales_price['sale_date'],
                                'priority': 1,
                                'details': {'reference': sales_price['sales_order_ref'], 'type': 'Historic Sale'}
                            })
                        if pl_customer_quote and pl_customer_quote['most_recent_price']:
                            price_candidates.append({
                                'price': round(pl_customer_quote['most_recent_price'], 2),
                                'source': 'parts_list_customer_quote',
                                'currency': 'GBP',
                                'date': pl_customer_quote['quote_date'],
                                'priority': 1,
                                'details': {'type': 'Previous Quote Request'}
                            })
                        if pl_supplier_quote_price:
                            price_candidates.append({
                                'price': pl_supplier_quote_price,
                                'source': 'parts_list_supplier_quote',
                                'currency': 'GBP',
                                'date': pl_supplier_quote_date,
                                'priority': 1,
                                'details': pl_supplier_details
                            })

                        if po_price and show_estimates:
                            price_candidates.append({
                                'price': po_price,
                                'source': 'purchase_order_estimate',
                                'currency': 'GBP',
                                'date': po_date,
                                'priority': 2,
                                'details': po_details
                            })
                        if vq_price and show_estimates:
                            price_candidates.append({
                                'price': vq_price,
                                'source': 'vendor_quote_estimate',
                                'currency': 'GBP',
                                'date': vq_date,
                                'priority': 2,
                                'details': vq_details
                            })

                        chosen = None
                        if price_candidates:
                            priority_1 = sorted(
                                [p for p in price_candidates if p['priority'] == 1],
                                key=lambda x: _sort_date_key(x['date']),
                                reverse=True
                            )
                            priority_2 = sorted(
                                [p for p in price_candidates if p['priority'] == 2],
                                key=lambda x: _sort_date_key(x['date']),
                                reverse=True
                            )
                            chosen = priority_1[0] if priority_1 else (priority_2[0] if priority_2 else None)

                        if chosen:
                            result['estimated_price'] = chosen['price']
                            result['price_source'] = chosen['source']
                            result['currency'] = chosen['currency']
                            result['status'] = 'quote_required'
                            debug_info = {
                                'winning_source': chosen['source'],
                                'source_details': chosen.get('details', {})
                            }
                        else:
                            result['estimated_price'] = None
                            result['price_source'] = None
                            result['currency'] = 'GBP'
                            result['status'] = 'quote_required'
                            debug_info = {'winning_source': 'none', 'source_details': {}}

                    result['estimated_lead_days'] = estimated_lead_days
                    result['debug_info'] = debug_info
                    results.append(result)

                except Exception as part_error:
                    logging.exception(f"ERROR processing part {part_number}: {part_error}")
                    if _using_postgres():
                        pg_cursor = getattr(cursor, '_cursor', None)
                        try:
                            if pg_cursor is not None and getattr(pg_cursor, 'connection', None) is not None:
                                pg_cursor.connection.rollback()
                        except Exception:
                            pass
                    continue

        return jsonify({
            'success': True,
            'results': results,
            'settings': {'show_stock_quantities': show_quantities}
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500
# ============================================================================
# QUOTE REQUEST SUBMISSION
# ============================================================================

@portal_api_bp.route('/quote/submit', methods=['POST'])
@require_portal_auth
def submit_quote_request():
    """
    Submit a formal quote request from customer portal
    Creates a parts_list and portal_quote_request
    """
    try:
        user = request.portal_user
        data = request.get_json()
        parts = data.get('parts', [])
        notes = data.get('notes', '')
        customer_reference = (data.get('customer_reference') or '').strip()

        log_api_call('/quote/submit', 'POST', user['id'], user['customer_id'], data, 200, request.remote_addr)

        if not parts:
            return jsonify({'success': False, 'error': 'No parts provided'}), 400

        ref_number = f"PR-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(4).upper()}"

        with db_cursor(commit=True) as cursor:
            parts_list_row = _execute_with_cursor(cursor, """
                INSERT INTO parts_lists 
                (name, customer_id, salesperson_id, status_id, notes)
                VALUES (?, ?, 1, 1, ?)
                RETURNING id
            """, (
                f"Portal Request {ref_number}",
                user['customer_id'],
                f"Customer portal request from {user['first_name']} {user['last_name']}\\n\\n{notes}"
            )).fetchone()
            parts_list_id = parts_list_row['id']

            if _portal_quote_requests_has_customer_reference():
                request_row = _execute_with_cursor(cursor, """
                    INSERT INTO portal_quote_requests
                    (portal_user_id, customer_id, parts_list_id, reference_number, customer_reference, customer_notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    RETURNING id
                """, (
                    user['id'],
                    user['customer_id'],
                    parts_list_id,
                    ref_number,
                    customer_reference,
                    notes,
                )).fetchone()
            else:
                request_row = _execute_with_cursor(cursor, """
                    INSERT INTO portal_quote_requests
                    (portal_user_id, customer_id, parts_list_id, reference_number, customer_notes)
                    VALUES (?, ?, ?, ?, ?)
                    RETURNING id
                """, (
                    user['id'],
                    user['customer_id'],
                    parts_list_id,
                    ref_number,
                    notes,
                )).fetchone()
            request_id = request_row['id']

            for idx, part in enumerate(parts, 1):
                part_number = part.get('part_number', '').strip()
                if not part_number:
                    continue

                quantity = int(part.get('quantity', 1))
                base_part_number = create_base_part_number(part_number)

                _execute_with_cursor(cursor, """
                    INSERT INTO parts_list_lines
                    (parts_list_id, line_number, customer_part_number, base_part_number, quantity)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    parts_list_id,
                    idx,
                    part_number,
                    base_part_number,
                    quantity,
                ))

                _execute_with_cursor(cursor, """
                    INSERT INTO portal_quote_request_lines
                    (portal_quote_request_id, line_number, part_number, base_part_number, quantity)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    request_id,
                    idx,
                    part_number,
                    base_part_number,
                    quantity,
                ))

        from routes.portal_admin import notify_new_quote_request
        notify_new_quote_request(request_id)

        return jsonify({
            'success': True,
            'request_id': request_id,
            'reference_number': ref_number,
            'message': 'Quote request submitted successfully'
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500
@portal_api_bp.route('/quote/requests', methods=['GET'])
@require_portal_auth
def get_quote_requests():
    """Get all quote requests for logged-in customer"""
    try:
        user = request.portal_user

        customer_reference_select = (
            "pqr.customer_reference"
            if _portal_quote_requests_has_customer_reference()
            else "NULL"
        )
        requests = db_execute(f"""
            SELECT 
                pqr.id,
                pqr.reference_number,
                {customer_reference_select} as customer_reference,
                pqr.status,
                pqr.date_submitted,
                pqr.date_processed,
                COUNT(pqrl.id) as line_count,
                SUM(CASE WHEN pqrl.status = 'quoted' THEN 1 ELSE 0 END) as quoted_lines
            FROM portal_quote_requests pqr
            LEFT JOIN portal_quote_request_lines pqrl ON pqrl.portal_quote_request_id = pqr.id
            WHERE pqr.portal_user_id = ?
            GROUP BY pqr.id
            ORDER BY pqr.date_submitted DESC
        """, (user['id'],), fetch='all') or []

        return jsonify({
            'success': True,
            'requests': [dict(r) for r in requests]
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

@portal_api_bp.route('/quote/requests/<int:request_id>', methods=['GET'])
@require_portal_auth
def get_quote_request_details(request_id):
    """Get details of a specific quote request"""
    try:
        user = request.portal_user

        request_data = db_execute("""
            SELECT * FROM portal_quote_requests
            WHERE id = ? AND portal_user_id = ?
        """, (request_id, user['id']), fetch='one')

        if not request_data:
            return jsonify({'success': False, 'error': 'Request not found'}), 404

        lines = db_execute("""
            SELECT 
                pqrl.*,
                c.currency_code
            FROM portal_quote_request_lines pqrl
            LEFT JOIN currencies c ON c.id = pqrl.quoted_currency_id
            WHERE pqrl.portal_quote_request_id = ?
            ORDER BY pqrl.line_number
        """, (request_id,), fetch='all') or []

        return jsonify({
            'success': True,
            'request': dict(request_data),
            'lines': [dict(l) for l in lines]
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

@portal_api_bp.route('/common-parts', methods=['GET'])
@require_portal_auth
def get_common_parts():
    """Get commonly purchased parts for this customer with current availability"""
    try:
        user = request.portal_user

        margins = get_customer_margins(user['customer_id'])
        stock_margin = margins['stock']

        show_quantities = bool(int(get_portal_setting('show_stock_quantities', 1)))
        min_stock = int(get_portal_setting('min_stock_threshold', 1))
        default_lead_days = int(get_portal_setting('default_lead_time_days', 7))

        today = datetime.utcnow().date()
        common_cutoff = _months_ago(today, 24)

        common_parts = db_execute("""
            SELECT 
                sol.base_part_number,
                pn.part_number,
                COUNT(DISTINCT so.id) as order_count,
                MAX(so.date_entered) as last_order_date,
                AVG(sol.price) as avg_price
            FROM sales_order_lines sol
            JOIN sales_orders so ON sol.sales_order_id = so.id
            JOIN part_numbers pn ON pn.base_part_number = sol.base_part_number
            WHERE so.customer_id = ?
            AND so.date_entered >= ?
            GROUP BY sol.base_part_number, pn.part_number
            ORDER BY order_count DESC, last_order_date DESC
            LIMIT 10
        """, (user['customer_id'], common_cutoff), fetch='all') or []

        results = []

        with db_cursor() as cursor:
            for part in common_parts:
                base_part_number = part['base_part_number']

                agreement_price = get_customer_pricing_agreement(user['customer_id'], base_part_number)
                if agreement_price:
                    result = {
                        'part_number': part['part_number'],
                        'base_part_number': base_part_number,
                        'order_count': part['order_count'],
                        'last_order_date': part['last_order_date'],
                        'in_stock': False,
                        'stock_quantity': None,
                        'estimated_price': agreement_price,
                        'price_source': 'pricing_agreement',
                        'currency': 'GBP',
                        'estimated_lead_days': 0
                    }
                    results.append(result)
                    continue

                stock = _execute_with_cursor(cursor, """
                    SELECT 
                        SUM(available_quantity) as total_stock,
                        AVG(cost_per_unit) as avg_cost
                    FROM stock_movements
                    WHERE base_part_number = ?
                      AND movement_type = 'IN'
                      AND available_quantity >= ?
                """, (base_part_number, min_stock)).fetchone()

                total_stock = stock['total_stock'] if stock and stock['total_stock'] else 0
                has_stock = total_stock > 0
                avg_cost_value = _to_float(stock['avg_cost']) if stock and stock['avg_cost'] else None

                estimated_lead_days = default_lead_days
                if has_stock:
                    estimated_lead_days = 0
                else:
                    sq_lead = _execute_with_cursor(cursor, """
                        SELECT sql.lead_time_days, s.buffer
                        FROM parts_list_supplier_quote_lines sql
                        JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                        JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                        LEFT JOIN suppliers s ON s.id = sq.supplier_id
                        WHERE pll.base_part_number = ?
                        ORDER BY COALESCE(sq.quote_date, sq.date_created) DESC LIMIT 1
                    """, (base_part_number,)).fetchone()

                    if sq_lead:
                        lead_time = int(sq_lead['lead_time_days'] or 0)
                        buffer_days = int(sq_lead['buffer'] or 0)
                        estimated_lead_days = lead_time + buffer_days

                estimated_lead_days = int(estimated_lead_days) if estimated_lead_days > 0 else 0

                estimated_price = None
                price_source = None
                currency = 'GBP'

                if has_stock and avg_cost_value is not None:
                    estimated_price = round(avg_cost_value * (1 + stock_margin / 100), 2)
                    price_source = 'stock'
                else:
                    raw_avg_price = part.get('avg_price')
                    part_avg = _to_float(raw_avg_price) if raw_avg_price is not None else None
                    if part_avg is not None:
                        estimated_price = round(part_avg, 2)
                        price_source = 'recent_sale'
                    else:
                        price_source = None

                estimated_price = _to_float(estimated_price)

                result = {
                    'part_number': part['part_number'],
                    'base_part_number': base_part_number,
                    'order_count': part['order_count'],
                    'last_order_date': part['last_order_date'],
                    'in_stock': has_stock,
                    'stock_quantity': int(total_stock) if show_quantities and total_stock else None,
                    'estimated_price': estimated_price,
                    'price_source': price_source,
                    'currency': currency,
                    'estimated_lead_days': estimated_lead_days
                }

                results.append(result)

        return jsonify({'success': True, 'parts': results})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

@portal_api_bp.route('/pricing-agreements', methods=['GET'])
@require_portal_auth
def get_pricing_agreements():
    """Get active pricing agreements for this customer with stock availability"""
    try:
        user = request.portal_user

        show_quantities = bool(int(get_portal_setting('show_stock_quantities', 1)))
        min_stock = int(get_portal_setting('min_stock_threshold', 1))
        default_lead_days = int(get_portal_setting('default_lead_time_days', 7))
        today = datetime.utcnow().date()

        agreements = db_execute("""
            SELECT 
                pcp.base_part_number,
                pn.part_number,
                pcp.price,
                pcp.currency_id,
                c.currency_code,
                pcp.valid_until,
                pcp.notes
            FROM portal_customer_pricing pcp
            LEFT JOIN part_numbers pn ON pn.base_part_number = pcp.base_part_number
            LEFT JOIN currencies c ON c.id = pcp.currency_id
            WHERE pcp.customer_id = ?
            AND pcp.is_active = TRUE
            AND (pcp.valid_from IS NULL OR pcp.valid_from <= ?)
            AND (pcp.valid_until IS NULL OR pcp.valid_until >= ?)
            ORDER BY pn.part_number
        """, (user['customer_id'], today, today), fetch='all') or []

        results = []

        with db_cursor() as cursor:
            for agreement in agreements:
                base_part_number = agreement['base_part_number']

                stock = _execute_with_cursor(cursor, """
                    SELECT SUM(available_quantity) as total_stock
                    FROM stock_movements
                    WHERE base_part_number = ?
                      AND movement_type = 'IN'
                      AND available_quantity >= ?
                """, (base_part_number, min_stock)).fetchone()

                total_stock = stock['total_stock'] if stock and stock['total_stock'] else 0
                has_stock = total_stock > 0

                estimated_lead_days = default_lead_days
                if has_stock:
                    estimated_lead_days = 0
                else:
                    sq_lead = _execute_with_cursor(cursor, """
                        SELECT sql.lead_time_days, s.buffer
                        FROM parts_list_supplier_quote_lines sql
                        JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                        JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                        LEFT JOIN suppliers s ON s.id = sq.supplier_id
                        WHERE pll.base_part_number = ?
                        ORDER BY COALESCE(sq.quote_date, sq.date_created) DESC LIMIT 1
                    """, (base_part_number,)).fetchone()

                    if sq_lead:
                        lead_time = int(sq_lead['lead_time_days'] or 0)
                        buffer_days = int(sq_lead['buffer'] or 0)
                        estimated_lead_days = lead_time + buffer_days

                estimated_lead_days = int(estimated_lead_days) if estimated_lead_days > 0 else 0

                result = {
                    'part_number': agreement['part_number'] or base_part_number,
                    'base_part_number': base_part_number,
                    'price': _to_float(agreement['price']),
                    'currency': agreement['currency_code'] or 'GBP',
                    'valid_until': agreement['valid_until'],
                    'notes': agreement['notes'],
                    'in_stock': has_stock,
                    'stock_quantity': int(total_stock) if show_quantities and total_stock else None,
                    'estimated_lead_days': estimated_lead_days
                }

                results.append(result)

        return jsonify({'success': True, 'agreements': results})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

@portal_api_bp.route('/suggested-parts', methods=['GET'])
@require_portal_auth
def get_suggested_parts():
    """Get parts suggested specifically for this customer with current availability"""
    try:
        user = request.portal_user

        show_quantities = bool(int(get_portal_setting('show_stock_quantities', 1)))
        min_stock = int(get_portal_setting('min_stock_threshold', 1))
        default_lead_days = int(get_portal_setting('default_lead_time_days', 7))
        margins = get_customer_margins(user['customer_id'])
        stock_margin = margins['stock']

        suggestions = db_execute("""
            SELECT 
                psp.base_part_number,
                psp.notes,
                psp.priority,
                psp.date_created,
                pn.part_number
            FROM portal_suggested_parts psp
            LEFT JOIN part_numbers pn ON pn.base_part_number = psp.base_part_number
            WHERE psp.customer_id = ?
            AND psp.is_active = TRUE
            ORDER BY psp.priority DESC, psp.date_created DESC
        """, (user['customer_id'],), fetch='all') or []

        results = []
        with db_cursor() as cursor:
            for suggestion in suggestions:
                base_part_number = suggestion['base_part_number']

                agreement_price = get_customer_pricing_agreement(user['customer_id'], base_part_number)
                if agreement_price:
                    result = {
                        'part_number': suggestion['part_number'] or base_part_number,
                        'base_part_number': base_part_number,
                        'notes': suggestion['notes'],
                        'priority': suggestion['priority'],
                        'date_suggested': suggestion['date_created'],
                        'in_stock': False,
                        'stock_quantity': None,
                        'estimated_price': agreement_price,
                        'price_source': 'pricing_agreement',
                        'currency': 'GBP',
                        'estimated_lead_days': 0
                    }
                    results.append(result)
                    continue

                stock = _execute_with_cursor(cursor, """
                    SELECT 
                        SUM(available_quantity) as total_stock,
                        AVG(cost_per_unit) as avg_cost
                    FROM stock_movements
                    WHERE base_part_number = ?
                      AND movement_type = 'IN'
                      AND available_quantity >= ?
                """, (base_part_number, min_stock)).fetchone()

                total_stock = stock['total_stock'] if stock and stock['total_stock'] else 0
                has_stock = total_stock > 0

                estimated_lead_days = default_lead_days
                if has_stock:
                    estimated_lead_days = 0
                else:
                    sq_lead = _execute_with_cursor(cursor, """
                        SELECT sql.lead_time_days, s.buffer
                        FROM parts_list_supplier_quote_lines sql
                        JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                        JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                        LEFT JOIN suppliers s ON s.id = sq.supplier_id
                        WHERE pll.base_part_number = ?
                        ORDER BY COALESCE(sq.quote_date, sq.date_created) DESC LIMIT 1
                    """, (base_part_number,)).fetchone()

                    if sq_lead:
                        lead_time = int(sq_lead['lead_time_days'] or 0)
                        buffer_days = int(sq_lead['buffer'] or 0)
                        estimated_lead_days = lead_time + buffer_days

                estimated_lead_days = int(estimated_lead_days) if estimated_lead_days > 0 else 0

                estimated_price = None
                price_source = None
                currency = 'GBP'

                avg_cost_value = _to_float(stock['avg_cost']) if stock and stock['avg_cost'] else None
                if has_stock and avg_cost_value is not None:
                    estimated_price = round(avg_cost_value * (1 + stock_margin / 100), 2)
                    price_source = 'stock'

                estimated_price = _to_float(estimated_price)

                result = {
                    'part_number': suggestion['part_number'] or base_part_number,
                    'base_part_number': base_part_number,
                    'notes': suggestion['notes'],
                    'priority': suggestion['priority'],
                    'date_suggested': suggestion['date_created'],
                    'in_stock': has_stock,
                    'stock_quantity': int(total_stock) if show_quantities and total_stock else None,
                    'estimated_price': estimated_price,
                    'price_source': price_source,
                    'currency': currency,
                    'estimated_lead_days': estimated_lead_days
                }

                results.append(result)

        return jsonify({'success': True, 'suggestions': results})

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# PO SUBMISSION FROM CUSTOMER PORTAL
# ============================================================================

@portal_api_bp.route('/po/submit', methods=['POST'])
@require_portal_auth
def submit_purchase_order():
    """Receive and store a purchase order from the customer portal.
    This creates a binding PO with all details captured."""
    try:
        user = request.portal_user
        data = request.get_json()

        quote_id = data.get('quote_id')
        po_reference = data.get('po_reference')
        lines = data.get('lines', [])
        delivery_address = data.get('delivery_address', {})
        invoice_address = data.get('invoice_address', {})
        authorizer = data.get('authorizer', {})
        customer_notes = data.get('customer_notes', '')

        log_api_call('/po/submit', 'POST', user['id'], user['customer_id'],
                     {'po_reference': po_reference, 'line_count': len(lines)}, 200, request.remote_addr)

        if not lines:
            return jsonify({'success': False, 'error': 'No lines provided'}), 400
        if not po_reference:
            return jsonify({'success': False, 'error': 'PO reference required'}), 400

        for line in lines:
            if not line.get('price') or float(line.get('price', 0)) <= 0:
                return jsonify({
                    'success': False,
                    'error': f"Line {line.get('part_number')} has no valid pricing"
                }), 400

        existing = db_execute(
            "SELECT id FROM portal_purchase_orders WHERE po_reference = ?",
            (po_reference,),
            fetch='one'
        )
        if existing:
            return jsonify({'success': False, 'error': 'A PO with this reference already exists'}), 400

        total_value = sum(float(line['price']) * int(line['quantity']) for line in lines)
        same_as_delivery = (
            delivery_address.get('company') == invoice_address.get('company') and
            delivery_address.get('street') == invoice_address.get('street') and
            delivery_address.get('city') == invoice_address.get('city')
        )

        with db_cursor(commit=True) as cursor:
            header_row = _execute_with_cursor(cursor, """
                INSERT INTO portal_purchase_orders (
                    portal_user_id,
                    customer_id,
                    portal_quote_request_id,
                    po_reference,
                    total_value,
                    currency_id,
                    line_count,
                    status,
                    customer_notes,
                    delivery_company,
                    delivery_street,
                    delivery_city,
                    delivery_zip,
                    delivery_country,
                    invoice_company,
                    invoice_street,
                    invoice_city,
                    invoice_zip,
                    invoice_country,
                    same_as_delivery,
                    authorizer_name,
                    authorizer_title,
                    authorization_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (
                user['id'],
                user['customer_id'],
                quote_id,
                po_reference,
                total_value,
                3,
                len(lines),
                'submitted',
                customer_notes,
                delivery_address.get('company'),
                delivery_address.get('street'),
                delivery_address.get('city'),
                delivery_address.get('zip'),
                delivery_address.get('country'),
                invoice_address.get('company'),
                invoice_address.get('street'),
                invoice_address.get('city'),
                invoice_address.get('zip'),
                invoice_address.get('country'),
                1 if same_as_delivery else 0,
                authorizer.get('name'),
                authorizer.get('title'),
                authorizer.get('timestamp')
            )).fetchone()
            po_id = header_row['id']

            for idx, line in enumerate(lines, 1):
                part_number = line.get('part_number', '').strip()
                quantity = int(line.get('quantity', 1))
                unit_price = float(line.get('price', 0))
                line_total = unit_price * quantity
                base_part_number = create_base_part_number(part_number)

                _execute_with_cursor(cursor, """
                    INSERT INTO portal_purchase_order_lines (
                        portal_purchase_order_id,
                        line_number,
                        part_number,
                        base_part_number,
                        description,
                        quantity,
                        unit_price,
                        line_total,
                        price_source,
                        portal_quote_request_line_id,
                        status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    po_id,
                    idx,
                    part_number,
                    base_part_number,
                    line.get('description', ''),
                    quantity,
                    unit_price,
                    line_total,
                    line.get('price_source'),
                    line.get('line_id'),
                    'pending'
                ))

        return jsonify({
            'success': True,
            'po_id': po_id,
            'po_number': po_reference,
            'line_count': len(lines),
            'total_value': total_value,
            'message': 'Purchase order submitted successfully'
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

@portal_api_bp.route('/po/list', methods=['GET'])
@require_portal_auth
def list_purchase_orders():
    """
    Get all purchase orders submitted by this customer user
    """
    try:
        user = request.portal_user

        pos = db_execute("""
            SELECT 
                ppo.id,
                ppo.po_reference,
                ppo.total_value,
                c.currency_code,
                ppo.line_count,
                ppo.status,
                ppo.date_submitted,
                ppo.date_acknowledged,
                ppo.date_dispatched,
                ppo.customer_notes
            FROM portal_purchase_orders ppo
            LEFT JOIN currencies c ON c.id = ppo.currency_id
            WHERE ppo.portal_user_id = ?
            ORDER BY ppo.date_submitted DESC
        """, (user['id'],), fetch='all') or []

        return jsonify({
            'success': True,
            'purchase_orders': [dict(po) for po in pos]
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

@portal_api_bp.route('/po/<int:po_id>', methods=['GET'])
@require_portal_auth
def get_purchase_order_details(po_id):
    """Get detailed information about a specific purchase order"""
    try:
        user = request.portal_user

        po = db_execute("""
            SELECT 
                ppo.*,
                c.currency_code,
                pu.email as submitted_by_email,
                pu.first_name as submitted_by_first_name,
                pu.last_name as submitted_by_last_name
            FROM portal_purchase_orders ppo
            LEFT JOIN currencies c ON c.id = ppo.currency_id
            LEFT JOIN portal_users pu ON pu.id = ppo.portal_user_id
            WHERE ppo.id = ? AND ppo.portal_user_id = ?
        """, (po_id, user['id']), fetch='one')

        if not po:
            return jsonify({'success': False, 'error': 'Purchase order not found'}), 404

        lines = db_execute("""
            SELECT * FROM portal_purchase_order_lines
            WHERE portal_purchase_order_id = ?
            ORDER BY line_number
        """, (po_id,), fetch='all') or []

        return jsonify({
            'success': True,
            'purchase_order': dict(po),
            'lines': [dict(line) for line in lines]
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

@portal_api_bp.route('/po/<int:po_id>/cancel', methods=['POST'])
@require_portal_auth
def cancel_purchase_order(po_id):
    """Request cancellation of a purchase order (only if not yet acknowledged)"""
    try:
        user = request.portal_user
        data = request.get_json()
        cancellation_reason = data.get('reason', '')

        po = db_execute("""
            SELECT id, status, date_acknowledged
            FROM portal_purchase_orders
            WHERE id = ? AND portal_user_id = ?
        """, (po_id, user['id']), fetch='one')

        if not po:
            return jsonify({'success': False, 'error': 'Purchase order not found'}), 404

        if po['status'] == 'cancelled':
            return jsonify({'success': False, 'error': 'PO is already cancelled'}), 400

        if po['date_acknowledged']:
            return jsonify({
                'success': False,
                'error': 'Cannot cancel - order has already been acknowledged. Please contact support.'
            }), 400

        db_execute("""
            UPDATE portal_purchase_orders
            SET status = 'cancelled',
                customer_notes = customer_notes || '\\n\\nCANCELLATION REQUEST: ' || ?
            WHERE id = ?
        """, (cancellation_reason, po_id), commit=True)

        log_api_call('/po/cancel', 'POST', user['id'], user['customer_id'],
                     {'po_id': po_id}, 200, request.remote_addr)

        return jsonify({
            'success': True,
            'message': 'Cancellation request submitted. Our team will contact you to confirm.'
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

def _analyze_quote_internal(customer_id, parts):
    """Internal version without auth decorator - for testing"""
    user = db_execute("""
        SELECT pu.*, c.name as customer_name
        FROM portal_users pu
        JOIN customers c ON c.id = pu.customer_id
        WHERE pu.customer_id = ? AND pu.is_active = TRUE
        LIMIT 1
    """, (customer_id,), fetch='one')

    if not user:
        return jsonify({'success': False, 'error': 'Customer not found'}), 400

    # Set portal_user to bypass auth
    request.portal_user = dict(user)

    # Mock get_json for the internal call
    original_get_json = request.get_json
    request.get_json = lambda: {'parts': parts}

    # Call the actual function
    result = analyze_quote()

    # Restore
    request.get_json = original_get_json

    return result


@portal_api_bp.route('/agreements/request', methods=['POST'])
@require_portal_auth
def request_pricing_agreement():
    """Submit a request for a pricing agreement on a specific part
    Creates a record that internal staff can review and respond to"""
    try:
        user = request.portal_user
        data = request.get_json()

        part_number = data.get('part_number', '').strip()
        quantity = data.get('quantity', 1)
        notes = data.get('notes', '')

        log_api_call('/agreements/request', 'POST', user['id'], user['customer_id'],
                     data, 200, request.remote_addr)

        if not part_number:
            return jsonify({'success': False, 'error': 'Part number required'}), 400

        base_part_number = create_base_part_number(part_number)

        ref_number = f"PA-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"

        with db_cursor(commit=True) as cursor:
            row = _execute_with_cursor(cursor, """
                INSERT INTO portal_pricing_agreement_requests (
                    portal_user_id,
                    customer_id,
                    part_number,
                    base_part_number,
                    quantity,
                    reference_number,
                    customer_notes,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (
                user['id'],
                user['customer_id'],
                part_number,
                base_part_number,
                quantity,
                ref_number,
                notes,
                'pending'
            )).fetchone()
            request_id = row['id']

        from routes.portal_admin import notify_new_pricing_agreement_request
        notify_new_pricing_agreement_request(request_id)

        return jsonify({
            'success': True,
            'request_id': request_id,
            'reference_number': ref_number,
            'message': f'Pricing agreement request submitted for {part_number}. A sales rep will contact you.'
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500

@portal_api_bp.route('/agreements/requests', methods=['GET'])
@require_portal_auth
def get_pricing_agreement_requests():
    """Get all pricing agreement requests submitted by this customer"""
    try:
        user = request.portal_user

        requests = db_execute("""
            SELECT 
                id,
                reference_number,
                part_number,
                quantity,
                status,
                date_submitted,
                date_processed,
                customer_notes,
                internal_notes
            FROM portal_pricing_agreement_requests
            WHERE portal_user_id = ?
            ORDER BY date_submitted DESC
        """, (user['id'],), fetch='all') or []

        return jsonify({
            'success': True,
            'requests': [dict(r) for r in requests]
        })

    except Exception as e:
        logging.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500
