from flask import Blueprint, render_template, request, jsonify, url_for, session, current_app
from flask_login import current_user, login_required
from models import get_db_connection
import logging
import os
import requests
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from urllib.parse import quote
import re

supplier_portal_bp = Blueprint('supplier_portal', __name__, url_prefix='/supplier-portal')

ONLINECOMPONENTS_API_BASE_URL = 'https://api.onlinecomponents.com/wapi'
ONLINECOMPONENTS_API_NAME = 'cgpriceavailability'
ONLINECOMPONENTS_SELECTION_SALT = 'onlinecomponents-sourcing-selection-v1'


def _using_postgres():
    """Check if we're using PostgreSQL based on DATABASE_URL."""
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _get_supplier_setting(cur, key):
    """Get a global supplier setting from app_settings."""
    cur.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = cur.fetchone()
    return row['value'] if row else None


def _set_supplier_setting(cur, key, value):
    """Set a global supplier setting in app_settings."""
    cur.execute("SELECT 1 FROM app_settings WHERE key = ?", (key,))
    exists = cur.fetchone()

    if exists:
        cur.execute("UPDATE app_settings SET value = ? WHERE key = ?", (str(value), key))
    else:
        cur.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, str(value)))


def _get_onlinecomponents_api_key():
    """Read the saved key first, then fall back to runtime/environment config."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        value = _get_supplier_setting(cur, 'ONLINECOMPONENTS_API_KEY')
        if value:
            return value.strip()
    except Exception:
        logging.exception("Unable to read the OnlineComponents API key")
    finally:
        if conn:
            conn.close()
    return (current_app.config.get('ONLINECOMPONENTS_API_KEY') or os.getenv('ONLINECOMPONENTS_API_KEY', '')).strip()


def _onlinecomponents_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt=ONLINECOMPONENTS_SELECTION_SALT)


def _normalized_part_number(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def _number_from_api(value, default=None):
    match = re.search(r'-?\d[\d,]*(?:\.\d+)?', str(value or ''))
    if not match:
        return default
    try:
        return Decimal(match.group(0).replace(',', ''))
    except InvalidOperation:
        return default


def _positive_int_from_api(value, default=1):
    number = _number_from_api(value)
    if number is None:
        return default
    return max(default, int(number))


def _currency_from_price(value):
    text = str(value or '')
    if '£' in text or 'GBP' in text.upper():
        return 'GBP'
    if '€' in text or 'EUR' in text.upper():
        return 'EUR'
    if '$' in text or 'USD' in text.upper():
        return 'USD'
    # The configured OnlineComponents storefront/account is GBP.
    return 'GBP'


def _request_onlinecomponents(query, *, results_count=50, in_stock_only=True, exact_match=True):
    api_key = _get_onlinecomponents_api_key()
    if not api_key:
        return None, 'OnlineComponents API key is not configured in Settings.'

    path_values = (
        '1', ONLINECOMPONENTS_API_NAME, query,
        '1' if in_stock_only else '0',
        '1' if exact_match else '0',
        str(results_count), api_key,
    )
    encoded_path = '/'.join(quote(str(value), safe=',') for value in path_values)
    endpoint = f'{ONLINECOMPONENTS_API_BASE_URL}/v{encoded_path}'
    try:
        response = requests.get(endpoint, headers={'Accept': 'application/json'}, timeout=(5, 45))
    except requests.RequestException as exc:
        # Do not log the exception text: requests may include the key-bearing URL.
        logging.error('OnlineComponents API request failed (%s)', type(exc).__name__)
        return None, 'Could not connect to the OnlineComponents API.'

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not response.ok:
        return None, f'OnlineComponents returned HTTP {response.status_code}.'
    if not isinstance(payload, list):
        return None, 'OnlineComponents returned an unexpected response.'
    return payload, None


@supplier_portal_bp.route('/onlinecomponents')
@login_required
def onlinecomponents_test():
    breadcrumbs = [
        ('Home', url_for('index')),
        ('Supplier Portal', url_for('supplier_portal.supplier_portal_home')),
        ('OnlineComponents API Test', None),
    ]
    return render_template(
        'supplier_portal_onlinecomponents.html',
        api_key_configured=bool(_get_onlinecomponents_api_key()),
        breadcrumbs=breadcrumbs,
    )


@supplier_portal_bp.route('/api/onlinecomponents/search', methods=['POST'])
@login_required
def onlinecomponents_search():
    """Proxy an Inventory and Pricing search without exposing the API key."""
    data = request.get_json(silent=True) or {}
    query = str(data.get('query') or '').strip()
    version = str(data.get('version') or '1').strip().lstrip('vV')

    if not query:
        return jsonify(success=False, message='Search query is required.'), 400
    if len(query) > 1000:
        return jsonify(success=False, message='Search query is too long.'), 400
    if not version.isdigit() or not 1 <= len(version) <= 3:
        return jsonify(success=False, message='Version must be a number.'), 400

    try:
        results_count = int(data.get('results_count', 10))
    except (TypeError, ValueError):
        return jsonify(success=False, message='Results count must be a number.'), 400
    if not 1 <= results_count <= 50:
        return jsonify(success=False, message='Results count must be between 1 and 50.'), 400

    # The standalone tester allows a version override; sourcing uses v1.
    api_key = _get_onlinecomponents_api_key()
    if not api_key:
        return jsonify(success=False, message='OnlineComponents API key is not configured in Settings.'), 400
    path_values = (
        version, ONLINECOMPONENTS_API_NAME, query,
        '1' if data.get('in_stock_only', True) else '0',
        '1' if data.get('exact_match', False) else '0',
        str(results_count), api_key,
    )
    endpoint = f"{ONLINECOMPONENTS_API_BASE_URL}/v{'/'.join(quote(str(value), safe=',') for value in path_values)}"
    try:
        response = requests.get(endpoint, headers={'Accept': 'application/json'}, timeout=(5, 30))
        payload = response.json()
    except requests.RequestException as exc:
        logging.error('OnlineComponents API request failed (%s)', type(exc).__name__)
        return jsonify(success=False, message='Could not connect to the OnlineComponents API.'), 502
    except ValueError:
        return jsonify(success=False, message='OnlineComponents returned a non-JSON response.'), 502
    if not response.ok:
        return jsonify(success=False, message=f'OnlineComponents returned HTTP {response.status_code}.'), 502
    return jsonify(success=True, results=payload, raw=payload)


@supplier_portal_bp.route('/api/onlinecomponents/parts-list/<int:list_id>/search', methods=['POST'])
@login_required
def onlinecomponents_parts_list_search(list_id):
    """Search uncosted list lines and return signed, reviewable price-break choices."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM parts_lists WHERE id = ?", (list_id,))
        if not cur.fetchone():
            return jsonify(success=False, message='Parts list not found.'), 404
        cur.execute("""
            SELECT id, line_number, customer_part_number, base_part_number, quantity
            FROM parts_list_lines
            WHERE parts_list_id = ? AND chosen_cost IS NULL
            ORDER BY line_number, id
        """, (list_id,))
        lines = [dict(row) for row in cur.fetchall()]
    finally:
        if conn:
            conn.close()

    searchable = []
    for line in lines:
        part_number = (line.get('customer_part_number') or line.get('base_part_number') or '').strip()
        if part_number:
            line['search_part_number'] = part_number
            searchable.append(line)
    if not searchable:
        return jsonify(success=False, message='There are no uncosted lines with part numbers to search.'), 400
    if len(searchable) > 100:
        return jsonify(success=False, message='OnlineComponents sourcing is limited to 100 uncosted lines per search.'), 400

    api_results = []
    # The documented endpoint accepts comma-separated part numbers. Keep batches small
    # enough for predictable URL sizes while remaining well below the API rate limit.
    for offset in range(0, len(searchable), 20):
        batch = searchable[offset:offset + 20]
        payload, error = _request_onlinecomponents(
            ','.join(line['search_part_number'] for line in batch),
            results_count=50,
            in_stock_only=True,
            exact_match=True,
        )
        if error:
            return jsonify(success=False, message=error), 502
        api_results.extend(payload)

    results_by_part = defaultdict(list)
    for item in api_results:
        if isinstance(item, dict):
            results_by_part[_normalized_part_number(item.get('partNumber'))].append(item)

    serializer = _onlinecomponents_serializer()
    grouped_lines = []
    for line in searchable:
        requested_quantity = max(1, int(line.get('quantity') or 1))
        product_results = []
        for item in results_by_part.get(_normalized_part_number(line['search_part_number']), []):
            price_options = []
            for price in item.get('price_breaks') or []:
                break_quantity = _positive_int_from_api(price.get('pricebreak'), default=1)
                unit_price = _number_from_api(price.get('pricelist'))
                if unit_price is None or unit_price < 0:
                    continue
                currency_code = _currency_from_price(price.get('pricelist'))
                selection = {
                    'list_id': list_id,
                    'line_id': line['id'],
                    'quoted_part_number': str(item.get('partNumber') or line['search_part_number'])[:100],
                    'manufacturer': str(item.get('manufacturer') or '')[:500],
                    'break_quantity': break_quantity,
                    'unit_price': str(unit_price),
                    'currency_code': currency_code,
                    'qty_available': _positive_int_from_api(item.get('quantityAvailable'), default=0),
                    'moq': _positive_int_from_api(item.get('moq'), default=1),
                    'multiple': _positive_int_from_api(item.get('multiple'), default=1),
                    'product_url': str(item.get('productUrl') or '')[:1000],
                    'datasheet_url': str(item.get('datasheetUrl') or '')[:1000],
                }
                price_options.append({
                    'break_quantity': break_quantity,
                    'unit_price': float(unit_price),
                    'currency_code': currency_code,
                    'token': serializer.dumps(selection),
                })
            if not price_options:
                continue
            recommended_index = min(
                range(len(price_options)),
                key=lambda index: abs(price_options[index]['break_quantity'] - requested_quantity),
            )
            product_results.append({
                'part_number': item.get('partNumber'),
                'manufacturer': item.get('manufacturer'),
                'description': item.get('description'),
                'quantity_available': item.get('quantityAvailableTxt') or item.get('quantityAvailable'),
                'moq': item.get('moq'),
                'multiple': item.get('multiple'),
                'product_url': item.get('productUrl'),
                'datasheet_url': item.get('datasheetUrl'),
                'price_options': price_options,
                'recommended_index': recommended_index,
            })
        grouped_lines.append({
            'line_id': line['id'],
            'line_number': line.get('line_number'),
            'part_number': line['search_part_number'],
            'requested_quantity': requested_quantity,
            'results': product_results,
        })

    matched_lines = sum(1 for line in grouped_lines if line['results'])
    return jsonify(
        success=True,
        lines=grouped_lines,
        searched_lines=len(searchable),
        matched_lines=matched_lines,
    )


@supplier_portal_bp.route('/api/onlinecomponents/parts-list/<int:list_id>/create-offer', methods=['POST'])
@login_required
def onlinecomponents_create_offer(list_id):
    """Create one supplier quote from signed OnlineComponents selections."""
    tokens = (request.get_json(silent=True) or {}).get('selection_tokens') or []
    if not isinstance(tokens, list) or not tokens:
        return jsonify(success=False, message='Select at least one OnlineComponents result.'), 400

    serializer = _onlinecomponents_serializer()
    selections = []
    seen_line_ids = set()
    try:
        for token in tokens:
            selection = serializer.loads(str(token), max_age=1800)
            if int(selection.get('list_id')) != list_id:
                raise BadSignature('Wrong parts list')
            line_id = int(selection.get('line_id'))
            if line_id in seen_line_ids:
                return jsonify(success=False, message='Only one result may be selected per parts-list line.'), 400
            seen_line_ids.add(line_id)
            selections.append(selection)
    except SignatureExpired:
        return jsonify(success=False, message='The search results expired. Run the search again.'), 400
    except (BadSignature, TypeError, ValueError, KeyError):
        return jsonify(success=False, message='One or more selections are invalid. Run the search again.'), 400

    currency_codes = {selection.get('currency_code') or 'GBP' for selection in selections}
    if len(currency_codes) != 1:
        return jsonify(success=False, message='Selections with different currencies cannot be added to one quote.'), 400
    currency_code = next(iter(currency_codes))

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM parts_lists WHERE id = ?", (list_id,))
        if not cur.fetchone():
            return jsonify(success=False, message='Parts list not found.'), 404

        supplier_id = _get_supplier_setting(cur, 'onlinecomponents_supplier_id')
        if supplier_id:
            cur.execute("SELECT id FROM suppliers WHERE id = ?", (supplier_id,))
            supplier_row = cur.fetchone()
        else:
            cur.execute("""
                SELECT id FROM suppliers
                WHERE LOWER(REPLACE(REPLACE(name, ' ', ''), '.', '')) IN
                      ('onlinecomponents', 'onlinecomponentscom')
                ORDER BY id LIMIT 1
            """)
            supplier_row = cur.fetchone()
        if not supplier_row:
            return jsonify(
                success=False,
                message='Configure the OnlineComponents supplier record on the Supplier Portal first.',
            ), 400
        supplier_id = supplier_row['id']

        placeholders = ','.join('?' for _ in seen_line_ids)
        cur.execute(f"""
            SELECT id, quantity FROM parts_list_lines
            WHERE parts_list_id = ? AND id IN ({placeholders})
        """, [list_id, *seen_line_ids])
        line_rows = {int(row['id']): dict(row) for row in cur.fetchall()}
        if len(line_rows) != len(seen_line_ids):
            return jsonify(success=False, message='One or more parts-list lines no longer exist.'), 400

        cur.execute("SELECT id FROM currencies WHERE currency_code = ?", (currency_code,))
        currency_row = cur.fetchone()
        if not currency_row:
            return jsonify(success=False, message=f'Currency {currency_code} is not configured.'), 400

        cur.execute("""
            INSERT INTO parts_list_supplier_quotes
            (parts_list_id, supplier_id, quote_reference, quote_date, currency_id, notes, created_by_user_id)
            VALUES (?, ?, 'OnlineComponents API', CURRENT_DATE, ?, ?, ?)
            RETURNING id
        """, (
            list_id, supplier_id, currency_row['id'],
            'Imported from OnlineComponents Inventory and Pricing API',
            current_user.id if getattr(current_user, 'is_authenticated', False) else session.get('user_id'),
        ))
        quote_id = cur.fetchone()['id']

        for selection in selections:
            line_id = int(selection['line_id'])
            requested_quantity = max(1, int(line_rows[line_id].get('quantity') or 1))
            break_quantity = max(1, int(selection.get('break_quantity') or 1))
            moq = max(1, int(selection.get('moq') or 1))
            multiple = max(1, int(selection.get('multiple') or 1))
            quantity_quoted = max(requested_quantity, break_quantity, moq)
            quantity_quoted = int((Decimal(quantity_quoted) / Decimal(multiple)).to_integral_value(rounding=ROUND_CEILING)) * multiple
            links = []
            if selection.get('product_url'):
                links.append(f"Product: {selection['product_url']}")
            if selection.get('datasheet_url'):
                links.append(f"Datasheet: {selection['datasheet_url']}")
            cur.execute("""
                INSERT INTO parts_list_supplier_quote_lines
                (supplier_quote_id, parts_list_line_id, quoted_part_number, manufacturer,
                 quantity_quoted, unit_price, condition_code, is_no_bid,
                 qty_available, purchase_increment, moq, line_notes)
                VALUES (?, ?, ?, ?, ?, ?, 'NE', FALSE, ?, ?, ?, ?)
            """, (
                quote_id, line_id, selection.get('quoted_part_number'), selection.get('manufacturer'),
                quantity_quoted, Decimal(str(selection.get('unit_price'))),
                int(selection.get('qty_available') or 0), multiple, moq, '\n'.join(links) or None,
            ))
        conn.commit()
        return jsonify(
            success=True,
            quote_id=quote_id,
            lines_created=len(selections),
            message=f'Created OnlineComponents supplier quote with {len(selections)} line(s).',
        )
    except Exception:
        conn.rollback()
        logging.exception('Failed to create OnlineComponents supplier quote')
        return jsonify(success=False, message='Failed to create the OnlineComponents supplier quote.'), 500
    finally:
        conn.close()


def _get_user_supplier_settings(cur, user_id, supplier_key):
    """
    Get supplier settings for a specific user.
    Returns dict with auto_search_new_parts and auto_create_supplier_offer.
    """
    table_name = f"user_{supplier_key}_settings"
    try:
        cur.execute(f"""
            SELECT auto_search_new_parts, auto_create_supplier_offer
            FROM {table_name}
            WHERE user_id = ?
        """, (user_id,))
        row = cur.fetchone()
        if row:
            return {
                'auto_search_new_parts': bool(row['auto_search_new_parts']),
                'auto_create_supplier_offer': bool(row['auto_create_supplier_offer'])
            }
    except Exception as e:
        logging.debug(f"Table {table_name} not found or error: {e}")

    # Return defaults if no settings found
    return {
        'auto_search_new_parts': False,
        'auto_create_supplier_offer': False
    }


def _set_user_supplier_settings(cur, user_id, supplier_key, auto_search_new_parts=None, auto_create_supplier_offer=None):
    """
    Set supplier settings for a specific user.
    Only updates fields that are provided (not None).
    """
    table_name = f"user_{supplier_key}_settings"

    # Check if user settings exist
    cur.execute(f"SELECT 1 FROM {table_name} WHERE user_id = ?", (user_id,))
    exists = cur.fetchone()

    if exists:
        # Update existing settings
        updates = []
        params = []
        if auto_search_new_parts is not None:
            updates.append("auto_search_new_parts = ?")
            params.append(auto_search_new_parts)
        if auto_create_supplier_offer is not None:
            updates.append("auto_create_supplier_offer = ?")
            params.append(auto_create_supplier_offer)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(user_id)
            sql = f"UPDATE {table_name} SET {', '.join(updates)} WHERE user_id = ?"
            cur.execute(sql, params)
    else:
        # Insert new settings
        cur.execute(f"""
            INSERT INTO {table_name}
            (user_id, auto_search_new_parts, auto_create_supplier_offer)
            VALUES (?, ?, ?)
        """, (
            user_id,
            auto_search_new_parts if auto_search_new_parts is not None else False,
            auto_create_supplier_offer if auto_create_supplier_offer is not None else False
        ))


@supplier_portal_bp.route('/')
@login_required
def supplier_portal_home():
    """
    Main supplier portal page showing all configured suppliers and their settings.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        user_id = current_user.id if getattr(current_user, 'is_authenticated', False) else session.get('user_id')

        # Get Monroe settings
        monroe_supplier_id = _get_supplier_setting(cur, 'monroe_supplier_id')
        monroe_settings = {}
        if monroe_supplier_id:
            cur.execute("SELECT id, name FROM suppliers WHERE id = ?", (monroe_supplier_id,))
            supplier = cur.fetchone()
            if supplier:
                monroe_settings['supplier'] = dict(supplier)
                if user_id:
                    monroe_settings['user_settings'] = _get_user_supplier_settings(cur, user_id, 'monroe')

        proponent_settings = {}
        proponent_supplier_id = _get_supplier_setting(cur, 'proponent_supplier_id')
        if proponent_supplier_id:
            cur.execute("SELECT id, name FROM suppliers WHERE id = ?", (proponent_supplier_id,))
            supplier = cur.fetchone()
            if supplier:
                proponent_settings['supplier'] = dict(supplier)

        onlinecomponents_settings = {}
        onlinecomponents_supplier_id = _get_supplier_setting(cur, 'onlinecomponents_supplier_id')
        if onlinecomponents_supplier_id:
            cur.execute("SELECT id, name FROM suppliers WHERE id = ?", (onlinecomponents_supplier_id,))
            supplier = cur.fetchone()
            if supplier:
                onlinecomponents_settings['supplier'] = dict(supplier)

        # Get all suppliers for dropdown
        cur.execute("SELECT id, name FROM suppliers ORDER BY name")
        all_suppliers = [dict(row) for row in cur.fetchall()]

        conn.close()

        breadcrumbs = [
            ('Home', url_for('index')),
            ('Supplier Portal', None)
        ]

        return render_template('supplier_portal.html',
                             monroe_settings=monroe_settings,
                             proponent_settings=proponent_settings,
                             onlinecomponents_settings=onlinecomponents_settings,
                             all_suppliers=all_suppliers,
                             breadcrumbs=breadcrumbs)

    except Exception as e:
        logging.exception(e)
        return str(e), 500


@supplier_portal_bp.route('/api/supplier-settings/<supplier_key>', methods=['GET', 'POST'])
@login_required
def supplier_settings(supplier_key):
    """
    Get or set supplier settings (currently supports 'monroe' and 'proponent').
    """
    if supplier_key not in ['monroe', 'proponent', 'onlinecomponents']:
        return jsonify(success=False, message="Unknown supplier"), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        user_id = current_user.id if getattr(current_user, 'is_authenticated', False) else session.get('user_id')

        if request.method == 'POST':
            data = request.get_json() or {}
            supplier_id = data.get('supplier_id')
            auto_search_new_parts = data.get('auto_search_new_parts')
            auto_create_supplier_offer = data.get('auto_create_supplier_offer')

            # Global setting: supplier ID
            if supplier_id:
                _set_supplier_setting(cur, f'{supplier_key}_supplier_id', supplier_id)

            # User-level settings (Monroe only)
            if supplier_key == 'monroe' and user_id and (
                auto_search_new_parts is not None or auto_create_supplier_offer is not None
            ):
                _set_user_supplier_settings(
                    cur,
                    user_id,
                    supplier_key,
                    auto_search_new_parts=auto_search_new_parts,
                    auto_create_supplier_offer=auto_create_supplier_offer
                )

            conn.commit()
            conn.close()

            return jsonify(
                success=True,
                supplier_id=supplier_id,
                auto_search_new_parts=auto_search_new_parts,
                auto_create_supplier_offer=auto_create_supplier_offer
            )

        # GET - return current settings
        supplier_id = _get_supplier_setting(cur, f'{supplier_key}_supplier_id')

        # Get user-specific settings if user is logged in
        user_settings = {}
        if user_id and supplier_key == 'monroe':
            user_settings = _get_user_supplier_settings(cur, user_id, supplier_key)

        cur.execute("SELECT id, name FROM suppliers ORDER BY name")
        suppliers = [dict(row) for row in cur.fetchall()]
        conn.close()

        return jsonify(
            success=True,
            supplier_id=int(supplier_id) if supplier_id else None,
            auto_search_new_parts=user_settings.get('auto_search_new_parts', False),
            auto_create_supplier_offer=user_settings.get('auto_create_supplier_offer', False),
            suppliers=suppliers
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@supplier_portal_bp.route('/api/scrape-queue/<supplier_key>', methods=['GET'])
@login_required
def get_scrape_queue(supplier_key):
    """
    Get recent scrape results for a supplier.
    Currently supports 'monroe'.
    """
    if supplier_key not in ['monroe']:
        return jsonify(success=False, message="Unknown supplier"), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        parts_list_id = request.args.get('parts_list_id', type=int)

        # Get recent searches (last 7 days)
        if _using_postgres():
            date_filter = "msr.search_date > CURRENT_TIMESTAMP - INTERVAL '7 days'"
        else:
            date_filter = "msr.search_date > datetime('now', '-7 days')"

        params = []
        where_clauses = [date_filter]
        if parts_list_id:
            where_clauses.append("msr.parts_list_id = ?")
            params.append(parts_list_id)

        where_sql = " AND ".join(where_clauses)
        result_limit = 1000 if parts_list_id else 100

        cur.execute(f"""
            SELECT DISTINCT
                msr.parts_list_id,
                pl.name as parts_list_name
            FROM monroe_search_results msr
            LEFT JOIN parts_lists pl ON pl.id = msr.parts_list_id
            WHERE {date_filter}
              AND msr.parts_list_id IS NOT NULL
            ORDER BY pl.name
        """)
        parts_lists = [dict(row) for row in cur.fetchall()]

        # Check if debug_info column exists by querying table schema
        has_debug = False
        try:
            if _using_postgres():
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'monroe_search_results'
                    AND column_name = 'debug_info'
                """)
                has_debug = cur.fetchone() is not None
            else:
                cur.execute("PRAGMA table_info(monroe_search_results)")
                columns = [row[1] for row in cur.fetchall()]
                has_debug = 'debug_info' in columns
        except Exception as e:
            logging.warning(f"Could not check for debug_info column: {e}")
            has_debug = False

        if has_debug:
            cur.execute(f"""
                SELECT
                    msr.id,
                    msr.parts_list_id,
                    msr.parts_list_line_id,
                    msr.base_part_number,
                    msr.searched_part_number,
                    msr.unit_price,
                    msr.inventory,
                    msr.minimum_order,
                    msr.purchase_increment,
                    msr.currency_code,
                    msr.search_date,
                    msr.error_message,
                    msr.debug_info,
                    pl.name as parts_list_name,
                    pll.line_number,
                    pll.quantity as requested_quantity
                FROM monroe_search_results msr
                LEFT JOIN parts_lists pl ON pl.id = msr.parts_list_id
                LEFT JOIN parts_list_lines pll ON pll.id = msr.parts_list_line_id
                WHERE {where_sql}
                ORDER BY msr.search_date DESC
                LIMIT {result_limit}
            """, params)
        else:
            cur.execute(f"""
                SELECT
                    msr.id,
                    msr.parts_list_id,
                    msr.parts_list_line_id,
                    msr.base_part_number,
                    msr.searched_part_number,
                    msr.unit_price,
                    msr.inventory,
                    msr.minimum_order,
                    msr.purchase_increment,
                    msr.currency_code,
                    msr.search_date,
                    msr.error_message,
                    pl.name as parts_list_name,
                    pll.line_number,
                    pll.quantity as requested_quantity
                FROM monroe_search_results msr
                LEFT JOIN parts_lists pl ON pl.id = msr.parts_list_id
                LEFT JOIN parts_list_lines pll ON pll.id = msr.parts_list_line_id
                WHERE {where_sql}
                ORDER BY msr.search_date DESC
                LIMIT {result_limit}
            """, params)

        results = [dict(row) for row in cur.fetchall()]
        conn.close()

        return jsonify(success=True, results=results, parts_lists=parts_lists)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@supplier_portal_bp.route('/api/scrape-status/<supplier_key>', methods=['GET'])
@login_required
def get_scrape_status(supplier_key):
    """
    Get current scraping status for a supplier.
    Returns active and recent scraping sessions.
    """
    if supplier_key not in ['monroe']:
        return jsonify(success=False, message="Unknown supplier"), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get active scrapes (in progress or queued)
        cur.execute("""
            SELECT
                id,
                parts_list_id,
                parts_list_name,
                status,
                total_lines,
                processed_lines,
                successful_lines,
                failed_lines,
                current_part_number,
                error_message,
                started_at
            FROM supplier_scrape_status
            WHERE supplier_key = ?
              AND status IN ('queued', 'in_progress')
            ORDER BY started_at DESC
        """, (supplier_key,))

        active = [dict(row) for row in cur.fetchall()]

        # Get recent completed scrapes (last 24 hours)
        if _using_postgres():
            date_filter = "started_at > CURRENT_TIMESTAMP - INTERVAL '24 hours'"
        else:
            date_filter = "started_at > datetime('now', '-24 hours')"

        cur.execute(f"""
            SELECT
                id,
                parts_list_id,
                parts_list_name,
                status,
                total_lines,
                processed_lines,
                successful_lines,
                failed_lines,
                error_message,
                started_at,
                completed_at
            FROM supplier_scrape_status
            WHERE supplier_key = ?
              AND status IN ('completed', 'failed', 'cancelled')
              AND {date_filter}
            ORDER BY started_at DESC
            LIMIT 10
        """, (supplier_key,))

        recent = [dict(row) for row in cur.fetchall()]
        conn.close()

        return jsonify(success=True, active=active, recent=recent)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@supplier_portal_bp.route('/api/scrape-cancel/<supplier_key>/<int:status_id>', methods=['POST'])
@login_required
def cancel_scrape(supplier_key, status_id):
    """Cancel an active scrape session for a supplier."""
    if supplier_key not in ['monroe']:
        return jsonify(success=False, message="Unknown supplier"), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, status
            FROM supplier_scrape_status
            WHERE id = ?
              AND supplier_key = ?
        """, (status_id, supplier_key))
        row = cur.fetchone()

        if not row:
            conn.close()
            return jsonify(success=False, message="Scrape session not found"), 404

        if row['status'] in ('completed', 'failed', 'cancelled'):
            conn.close()
            return jsonify(success=False, message=f"Scrape is already {row['status']}"), 400

        cur.execute("""
            UPDATE supplier_scrape_status
            SET status = 'cancelled',
                error_message = 'Cancelled by user',
                completed_at = CURRENT_TIMESTAMP,
                current_part_number = NULL
            WHERE id = ?
              AND supplier_key = ?
              AND status IN ('queued', 'in_progress')
        """, (status_id, supplier_key))

        updated = cur.rowcount
        conn.commit()
        conn.close()

        if updated == 0:
            return jsonify(success=False, message="Scrape could not be cancelled"), 409

        return jsonify(success=True, message="Scrape cancellation requested")

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@supplier_portal_bp.route('/api/scrape-by-id/<supplier_key>', methods=['POST'])
@login_required
def scrape_by_parts_list_id(supplier_key):
    """
    Trigger a scrape for a specific parts list by ID.
    """
    if supplier_key not in ['monroe']:
        return jsonify(success=False, message="Unknown supplier"), 400

    try:
        data = request.get_json() or {}
        parts_list_id = data.get('parts_list_id')

        if not parts_list_id:
            return jsonify(success=False, message="Parts list ID is required"), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # Verify parts list exists
        cur.execute("SELECT id, name FROM parts_lists WHERE id = ?", (parts_list_id,))
        parts_list = cur.fetchone()

        if not parts_list:
            conn.close()
            return jsonify(success=False, message="Parts list not found"), 404

        # Get all uncosted lines from this parts list
        cur.execute("""
            SELECT id, customer_part_number, base_part_number, quantity
            FROM parts_list_lines
            WHERE parts_list_id = ?
              AND chosen_cost IS NULL
        """, (parts_list_id,))

        lines = [dict(row) for row in cur.fetchall()]

        if not lines:
            conn.close()
            return jsonify(success=False, message="No uncosted lines found in this parts list"), 400

        user_id = current_user.id if getattr(current_user, 'is_authenticated', False) else session.get('user_id')
        line_ids = [line['id'] for line in lines]

        # Create scraping status entry
        if _using_postgres():
            cur.execute("""
                INSERT INTO supplier_scrape_status
                (supplier_key, parts_list_id, parts_list_name, status, total_lines, triggered_by_user_id)
                VALUES (?, ?, ?, 'queued', ?, ?)
                RETURNING id
            """, (supplier_key, parts_list_id, parts_list['name'], len(lines), user_id))
            status_row = cur.fetchone()
            status_id = status_row['id'] if status_row else None
        else:
            cur.execute("""
                INSERT INTO supplier_scrape_status
                (supplier_key, parts_list_id, parts_list_name, status, total_lines, triggered_by_user_id)
                VALUES (?, ?, ?, 'queued', ?, ?)
            """, (supplier_key, parts_list_id, parts_list['name'], len(lines), user_id))
            status_id = cur.lastrowid

        conn.commit()
        conn.close()

        # Import the trigger function from parts_list_ai
        from routes.parts_list_ai import trigger_monroe_auto_check_with_status

        # Trigger the background check with status tracking
        trigger_monroe_auto_check_with_status(parts_list_id, line_ids, user_id, status_id, force=True)

        return jsonify(
            success=True,
            message=f"Started scraping {len(lines)} lines from '{parts_list['name']}'",
            parts_list_name=parts_list['name'],
            line_count=len(lines),
            status_id=status_id
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@supplier_portal_bp.route('/api/create-offers-from-results/<supplier_key>', methods=['POST'])
@login_required
def create_offers_from_results(supplier_key):
    """Create supplier offers from selected recent scrape results."""
    if supplier_key not in ['monroe']:
        return jsonify(success=False, message="Unknown supplier"), 400

    try:
        data = request.get_json() or {}
        result_ids = data.get('result_ids', [])

        if not isinstance(result_ids, list) or not result_ids:
            return jsonify(success=False, message="No scrape results were selected"), 400

        cleaned_result_ids = []
        for result_id in result_ids:
            try:
                cleaned_result_ids.append(int(result_id))
            except (TypeError, ValueError):
                continue

        cleaned_result_ids = list(dict.fromkeys(cleaned_result_ids))
        if not cleaned_result_ids:
            return jsonify(success=False, message="No valid scrape result IDs were provided"), 400

        conn = get_db_connection()
        cur = conn.cursor()

        rows = []
        batch_size = 500
        for idx in range(0, len(cleaned_result_ids), batch_size):
            batch_ids = cleaned_result_ids[idx:idx + batch_size]
            placeholders = ",".join("?" for _ in batch_ids)
            cur.execute(f"""
                SELECT id, parts_list_id, unit_price
                FROM monroe_search_results
                WHERE id IN ({placeholders})
            """, batch_ids)
            rows.extend(dict(row) for row in cur.fetchall())

        if not rows:
            conn.close()
            return jsonify(success=False, message="Selected scrape results were not found"), 404

        grouped_result_ids = defaultdict(list)
        skipped_without_price = 0
        skipped_without_list = 0

        for row in rows:
            if row.get('unit_price') is None:
                skipped_without_price += 1
                continue
            if not row.get('parts_list_id'):
                skipped_without_list += 1
                continue
            grouped_result_ids[row['parts_list_id']].append(row['id'])

        if not grouped_result_ids:
            conn.close()
            return jsonify(
                success=False,
                message="None of the selected results were successful Monroe matches tied to a parts list."
            ), 400

        from routes.parts_list_ai import _auto_create_monroe_offer

        user_id = current_user.id if getattr(current_user, 'is_authenticated', False) else session.get('user_id', 1)

        created_quotes = []
        skipped_parts_lists = []

        for parts_list_id, list_result_ids in grouped_result_ids.items():
            quote_id = _auto_create_monroe_offer(cur, parts_list_id, list_result_ids, user_id)
            if quote_id:
                created_quotes.append({
                    'parts_list_id': parts_list_id,
                    'quote_id': quote_id,
                    'result_count': len(list_result_ids),
                })
            else:
                skipped_parts_lists.append(parts_list_id)

        if not created_quotes:
            conn.rollback()
            conn.close()
            return jsonify(
                success=False,
                message="No supplier offers were created. The selected results may all be below MOQ or otherwise invalid."
            ), 400

        conn.commit()
        conn.close()

        message = f"Created {len(created_quotes)} supplier offer(s) from {sum(item['result_count'] for item in created_quotes)} selected result(s)."
        if skipped_without_price:
            message += f" Skipped {skipped_without_price} result(s) without pricing."
        if skipped_without_list:
            message += f" Skipped {skipped_without_list} result(s) not linked to a parts list."
        if skipped_parts_lists:
            message += f" {len(skipped_parts_lists)} parts list(s) did not produce an offer."

        return jsonify(
            success=True,
            message=message,
            created_quotes=created_quotes,
            skipped_parts_lists=skipped_parts_lists,
            skipped_without_price=skipped_without_price,
            skipped_without_list=skipped_without_list,
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500
