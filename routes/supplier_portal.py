from flask import Blueprint, render_template, request, jsonify, url_for, session
from flask_login import current_user, login_required
from models import get_db_connection
import logging
import os

supplier_portal_bp = Blueprint('supplier_portal', __name__, url_prefix='/supplier-portal')


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
                             all_suppliers=all_suppliers,
                             breadcrumbs=breadcrumbs)

    except Exception as e:
        logging.exception(e)
        return str(e), 500


@supplier_portal_bp.route('/api/supplier-settings/<supplier_key>', methods=['GET', 'POST'])
@login_required
def supplier_settings(supplier_key):
    """
    Get or set supplier settings (currently supports 'monroe').
    """
    if supplier_key not in ['monroe']:
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

            # User-level settings
            if user_id and (auto_search_new_parts is not None or auto_create_supplier_offer is not None):
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
        if user_id:
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

        # Get recent searches (last 7 days)
        if _using_postgres():
            date_filter = "msr.search_date > CURRENT_TIMESTAMP - INTERVAL '7 days'"
        else:
            date_filter = "msr.search_date > datetime('now', '-7 days')"

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
                WHERE {date_filter}
                ORDER BY msr.search_date DESC
                LIMIT 100
            """)
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
                WHERE {date_filter}
                ORDER BY msr.search_date DESC
                LIMIT 100
            """)

        results = [dict(row) for row in cur.fetchall()]
        conn.close()

        return jsonify(success=True, results=results)

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
              AND status IN ('completed', 'failed')
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
