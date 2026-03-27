from flask import Blueprint, render_template, request, jsonify, url_for, session
from flask_login import current_user
from models import get_db_connection, get_base_currency, create_base_part_number
import logging
from openai import OpenAI
import json
import re
import time
import threading
from decimal import Decimal
import os

# Initialize OpenAI client
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
if client is None:
    logging.warning("OPENAI_API_KEY not found in routes.parts_list_ai. AI features are disabled.")

# Try to import playwright for Monroe scraping
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

parts_list_ai_bp = Blueprint('parts_list_ai', __name__, url_prefix='/parts-list-ai')


def _using_postgres():
    """Check if we're using PostgreSQL based on DATABASE_URL."""
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def get_db_type():
    """
    Detect database type from connection.
    Returns 'sqlite' or 'postgresql'
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Try PostgreSQL-specific query
        cursor.execute("SELECT version()")
        version = cursor.fetchone()[0]
        conn.close()
        if 'PostgreSQL' in version:
            return 'postgresql'
        return 'sqlite'
    except:
        # If that fails, assume SQLite
        conn.close()
        return 'sqlite'


def get_string_agg_function():
    """
    Get the appropriate string aggregation function for the database.
    SQLite: GROUP_CONCAT(column, separator)
    PostgreSQL: STRING_AGG(column, separator)
    """
    db_type = get_db_type()
    if db_type == 'postgresql':
        return 'STRING_AGG'
    return 'GROUP_CONCAT'


def get_current_date_sql():
    """
    Get current date SQL that works for both databases.
    SQLite: DATE('now')
    PostgreSQL: CURRENT_DATE
    """
    db_type = get_db_type()
    if db_type == 'postgresql':
        return 'CURRENT_DATE'
    return "DATE('now')"


def get_current_timestamp_sql():
    """
    Get current timestamp SQL that works for both databases.
    Both support CURRENT_TIMESTAMP
    """
    return 'CURRENT_TIMESTAMP'


def _ensure_part_number(cur, base_part_number, part_number):
    if not base_part_number:
        return
    part_value = part_number or base_part_number
    if _using_postgres():
        insert_query = """
            INSERT INTO part_numbers (base_part_number, part_number)
            VALUES (?, ?)
            ON CONFLICT (base_part_number) DO NOTHING
        """
    else:
        insert_query = """
            INSERT OR IGNORE INTO part_numbers (base_part_number, part_number)
            VALUES (?, ?)
        """
    cur.execute(insert_query, (base_part_number, part_value))


@parts_list_ai_bp.route('/')
def ai_home():
    """
    Home page for AI analysis - shows list of parts lists to analyze
    """
    try:
        # Get optional filters
        status_id = request.args.get('status_id', type=int)
        customer_id = request.args.get('customer_id', type=int)

        conn = get_db_connection()
        cur = conn.cursor()

        # Build simple query first
        sql = """
            SELECT
                pl.id,
                pl.name,
                pl.date_created,
                pl.date_modified,
                pl.status_id,
                COALESCE(c.name, '') AS customer_name,
                COALESCE(ct.name, '') AS contact_name,
                pls.name AS status_name,
                COALESCE((
                    SELECT COUNT(*)
                    FROM parts_list_lines pll
                    WHERE pll.parts_list_id = pl.id
                ), 0) AS line_count,
                COALESCE((
                    SELECT COUNT(*)
                    FROM parts_list_lines pll
                    WHERE pll.parts_list_id = pl.id
                      AND pll.chosen_cost IS NOT NULL
                ), 0) AS costed_lines,
                COALESCE((
                    SELECT COUNT(DISTINCT pll.id)
                    FROM parts_list_lines pll
                    LEFT JOIN parts_list_supplier_quote_lines psql
                        ON psql.parts_list_line_id = pll.id
                    LEFT JOIN parts_list_supplier_quotes psq
                        ON psq.id = psql.supplier_quote_id
                    WHERE pll.parts_list_id = pl.id
                      AND psql.is_no_bid = FALSE
                      AND psql.unit_price IS NOT NULL
                ), 0) AS quoted_lines
            FROM parts_lists pl
            LEFT JOIN customers c ON c.id = pl.customer_id
            LEFT JOIN contacts ct ON ct.id = pl.contact_id
            LEFT JOIN parts_list_statuses pls ON pls.id = pl.status_id
        """

        where_clauses = []
        params = []

        if status_id:
            where_clauses.append("pl.status_id = ?")
            params.append(status_id)

        if customer_id:
            where_clauses.append("pl.customer_id = ?")
            params.append(customer_id)

        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)

        sql += " ORDER BY pl.date_modified DESC LIMIT 100"

        cur.execute(sql, params)
        lists = cur.fetchall()

        # Get filter options
        cur.execute("SELECT id, name FROM parts_list_statuses ORDER BY display_order")
        statuses = cur.fetchall()
        
        cur.execute("SELECT id, name FROM customers ORDER BY name LIMIT 200")
        customers = cur.fetchall()

        conn.close()

        breadcrumbs = [
            ('Home', url_for('index')),
            ('AI Parts List Analysis', None)
        ]

        return render_template('parts_list_ai_home.html',
                             lists=[dict(l) for l in lists],
                             statuses=[dict(s) for s in statuses],
                             customers=[dict(c) for c in customers],
                             selected_status_id=status_id,
                             selected_customer_id=customer_id,
                             breadcrumbs=breadcrumbs)

    except Exception as e:
        logging.exception(e)
        return str(e), 500


@parts_list_ai_bp.route('/analyze/<int:list_id>')
def analyze_parts_list(list_id):
    """
    Display AI analysis results for a parts list
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get list header
        cur.execute("""
            SELECT
                pl.*,
                c.name AS customer_name,
                ct.name AS contact_name,
                pls.name AS status_name,
                sp.name AS salesperson_name
            FROM parts_lists pl
            LEFT JOIN customers c ON c.id = pl.customer_id
            LEFT JOIN contacts ct ON ct.id = pl.contact_id
            LEFT JOIN parts_list_statuses pls ON pls.id = pl.status_id
            LEFT JOIN salespeople sp ON sp.id = pl.salesperson_id
            WHERE pl.id = ?
        """, (list_id,))
        header = cur.fetchone()

        if not header:
            conn.close()
            return "Parts list not found", 404

        # Get all lines with comprehensive sourcing data
        cur.execute("""
            SELECT
                pll.id,
                pll.line_number,
                pll.customer_part_number,
                pll.base_part_number,
                pll.quantity,
                pll.chosen_cost,
                pll.chosen_qty,
                pll.chosen_supplier_id,
                s.name as chosen_supplier_name,
                pll.chosen_currency_id,
                curr.currency_code,
                pll.chosen_lead_days,
                -- Stock info
                COALESCE((SELECT SUM(sm.available_quantity)
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity > 0), 0) as stock_available,
                (SELECT MIN(sm.cost_per_unit)
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity > 0
                   AND sm.cost_per_unit > 0) as stock_min_cost,
                -- VQ info
                COALESCE((SELECT COUNT(DISTINCT vq_id)
                 FROM vq_lines
                 WHERE base_part_number = pll.base_part_number), 0) as vq_count,
                (SELECT MIN(vendor_price)
                 FROM vq_lines
                 WHERE base_part_number = pll.base_part_number
                   AND vendor_price > 0) as vq_min_price,
                -- Supplier quotes for this parts list
                COALESCE((SELECT COUNT(DISTINCT sq.supplier_id)
                 FROM parts_list_supplier_quote_lines sql
                 JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                 WHERE sql.parts_list_line_id = pll.id
                   AND sql.is_no_bid = FALSE
                   AND sql.unit_price IS NOT NULL), 0) as supplier_quote_count,
                (SELECT MIN(sql.unit_price)
                 FROM parts_list_supplier_quote_lines sql
                 JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                 WHERE sql.parts_list_line_id = pll.id
                   AND sql.is_no_bid = FALSE
                   AND sql.unit_price > 0) as supplier_quote_min_price,
                -- ILS info
                COALESCE((SELECT COUNT(DISTINCT ils_company_name)
                 FROM ils_search_results
                 WHERE base_part_number = pll.base_part_number), 0) as ils_supplier_count,
                -- Email tracking
                COALESCE((SELECT COUNT(DISTINCT supplier_id)
                 FROM parts_list_line_supplier_emails
                 WHERE parts_list_line_id = pll.id), 0) as suppliers_contacted,
                COALESCE((SELECT COUNT(*)
                 FROM parts_list_line_suggested_suppliers ss
                 WHERE ss.parts_list_line_id = pll.id), 0) as suggested_suppliers_count
            FROM parts_list_lines pll
            LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
            LEFT JOIN currencies curr ON curr.id = pll.chosen_currency_id
            WHERE pll.parts_list_id = ?
            ORDER BY pll.line_number
        """, (list_id,))
        lines = [dict(row) for row in cur.fetchall()]

        # For each line, get suggested suppliers with their names
        for line in lines:
            cur.execute("""
                SELECT s.id, s.name, ss.source_type, ss.date_added
                FROM parts_list_line_suggested_suppliers ss
                JOIN suppliers s ON s.id = ss.supplier_id
                WHERE ss.parts_list_line_id = ?
                ORDER BY ss.date_added ASC
            """, (line['id'],))
            line['suggested_supplier_list'] = [dict(row) for row in cur.fetchall()]

        conn.close()

        breadcrumbs = [
            ('Home', url_for('index')),
            ('AI Analysis', url_for('parts_list_ai.ai_home')),
            (header['name'], None)
        ]

        return render_template('parts_list_ai_analysis.html',
                             list_id=list_id,
                             header=dict(header),
                             lines=[dict(l) for l in lines],
                             breadcrumbs=breadcrumbs)

    except Exception as e:
        logging.exception(e)
        return str(e), 500


@parts_list_ai_bp.route('/api/generate-analysis/<int:list_id>', methods=['POST'])
def generate_analysis(list_id):
    """
    Generate AI analysis for a parts list
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get header
        cur.execute("""
            SELECT
                pl.*,
                c.name AS customer_name,
                pls.name AS status_name
            FROM parts_lists pl
            LEFT JOIN customers c ON c.id = pl.customer_id
            LEFT JOIN parts_list_statuses pls ON pls.id = pl.status_id
            WHERE pl.id = ?
        """, (list_id,))
        header = cur.fetchone()

        if not header:
            conn.close()
            return jsonify(success=False, message="Parts list not found"), 404

        # Get comprehensive line data
        cur.execute("""
            SELECT
                pll.line_number,
                pll.customer_part_number,
                pll.base_part_number,
                pll.quantity,
                  pll.chosen_cost,
                  pll.chosen_qty,
                  pll.chosen_source_type,
                  s.name as chosen_supplier_name,
                pll.chosen_lead_days,
                -- Stock
                COALESCE((SELECT SUM(sm.available_quantity)
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity > 0), 0) as stock_qty,
                (SELECT MIN(sm.cost_per_unit)
                 FROM stock_movements sm
                 WHERE sm.base_part_number = pll.base_part_number
                   AND sm.movement_type = 'IN'
                   AND sm.available_quantity > 0
                   AND sm.cost_per_unit > 0) as stock_cost,
                -- VQ
                COALESCE((SELECT COUNT(*)
                 FROM vq_lines
                 WHERE base_part_number = pll.base_part_number), 0) as vq_count,
                (SELECT MIN(vendor_price)
                 FROM vq_lines
                 WHERE base_part_number = pll.base_part_number
                   AND vendor_price > 0) as vq_price,
                -- Supplier quotes
                COALESCE((SELECT COUNT(DISTINCT sq.supplier_id)
                 FROM parts_list_supplier_quote_lines sql
                 JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                 WHERE sql.parts_list_line_id = pll.id
                   AND sql.is_no_bid = FALSE), 0) as quote_count,
                (SELECT MIN(sql.unit_price)
                 FROM parts_list_supplier_quote_lines sql
                 WHERE sql.parts_list_line_id = pll.id
                   AND sql.is_no_bid = FALSE
                   AND sql.unit_price > 0) as quote_price,
                -- ILS
                COALESCE((SELECT COUNT(DISTINCT ils_company_name)
                 FROM ils_search_results
                 WHERE base_part_number = pll.base_part_number), 0) as ils_count,
                -- Contacted
                COALESCE((SELECT COUNT(DISTINCT supplier_id)
                 FROM parts_list_line_supplier_emails
                 WHERE parts_list_line_id = pll.id), 0) as contacted_count
            FROM parts_list_lines pll
            LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
            WHERE pll.parts_list_id = ?
            ORDER BY pll.line_number
        """, (list_id,))
        lines = cur.fetchall()

        conn.close()

        # Build context for AI
        context = _build_analysis_context(header, lines)

        # Call OpenAI
        analysis = _call_ai_for_analysis(context)

        return jsonify(success=True, analysis=analysis)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


def _build_analysis_context(header, lines):
    """
    Build structured context for AI analysis
    """
    total_lines = len(lines)
    costed_lines = sum(1 for l in lines if l['chosen_cost'] is not None)

    # Handle potential None values from queries
    in_stock_lines = sum(1 for l in lines if (l['stock_qty'] or 0) >= l['quantity'])
    quoted_lines = sum(1 for l in lines if (l['quote_count'] or 0) > 0)
    need_sourcing = sum(1 for l in lines if (
        l['chosen_cost'] is None and
        (l['stock_qty'] or 0) < l['quantity'] and
        (l['quote_count'] or 0) == 0
    ))

    pending_lines = sum(1 for l in lines if (l['contacted_count'] or 0) > 0 and l['chosen_cost'] is None)

    # Calculate total value if costed
    total_cost = sum(
        (l['chosen_cost'] or 0) * (l['chosen_qty'] or l['quantity'])
        for l in lines
        if l['chosen_cost']
    )

    def _line_status(line, stock_qty, quote_count, vq_count, contacted_count, ils_count):
        if line['chosen_cost']:
            return f"COSTED - {line['chosen_supplier_name'] or 'Unknown'}"
        if stock_qty >= line['quantity']:
            return f"IN STOCK ({stock_qty} available)"
        if quote_count > 0:
            return f"QUOTED ({quote_count} suppliers)"
        if vq_count > 0:
            return f"VQ AVAILABLE ({vq_count} quotes)"
        if contacted_count > 0:
            return f"PENDING ({contacted_count} suppliers contacted)"
        if ils_count > 0:
            return f"ILS AVAILABLE ({ils_count} suppliers)"
        return "NEEDS SOURCING"

    # Build a short list of focus lines (uncosted only)
    focus_lines = []
    for line in lines:
        stock_qty = line['stock_qty'] or 0
        vq_count = line['vq_count'] or 0
        quote_count = line['quote_count'] or 0
        contacted_count = line['contacted_count'] or 0
        ils_count = line['ils_count'] or 0
        if line['chosen_cost']:
            continue

        status = _line_status(line, stock_qty, quote_count, vq_count, contacted_count, ils_count)
        best_price = None
        if "IN STOCK" in status:
            best_price = line['stock_cost']
        elif "QUOTED" in status:
            best_price = line['quote_price']
        elif "VQ AVAILABLE" in status:
            best_price = line['vq_price']

        focus_lines.append({
            'line': line['line_number'],
            'part': line['customer_part_number'],
            'qty': line['quantity'],
            'status': status,
            'best_price': best_price,
            'stock_qty': stock_qty,
            'quote_count': quote_count,
            'vq_count': vq_count,
            'contacted': contacted_count,
            'ils_count': ils_count
        })

    status_order = {
        "NEEDS SOURCING": 0,
        "PENDING": 1,
        "IN STOCK": 2,
        "QUOTED": 3,
        "VQ AVAILABLE": 4,
        "ILS AVAILABLE": 5
    }
    focus_lines.sort(key=lambda l: (status_order.get(l['status'].split(" (")[0], 9), -l['qty']))

    return {
        'list_name': header['name'],
        'customer': header['customer_name'] or 'No customer',
        'status': header['status_name'],
        'total_lines': total_lines,
        'costed_lines': costed_lines,
        'in_stock_lines': in_stock_lines,
        'quoted_lines': quoted_lines,
        'pending_lines': pending_lines,
        'need_sourcing': need_sourcing,
        'total_cost': total_cost,
        'focus_lines': focus_lines[:12]
    }


def _call_ai_for_analysis(context):
    """
    Call OpenAI to generate comprehensive analysis
    """
    prompt = f"""You are an aviation parts procurement analyst. Write a short travel-friendly briefing.

PARTS LIST: {context['list_name']}
Customer: {context['customer']}
Status: {context['status']}

SUMMARY STATS:
- Total lines: {context['total_lines']}
- Fully costed: {context['costed_lines']}
- In stock: {context['in_stock_lines']}
- Supplier quotes: {context['quoted_lines']}
- Pending replies: {context['pending_lines']}
- Need sourcing: {context['need_sourcing']}
- Total cost (costed items): ${context['total_cost']:,.2f}

FOCUS LINES (uncosted, top priority):
{json.dumps(context['focus_lines'], indent=2, default=str)}

Output format (strict, no paragraphs, max 8 bullets total):
- Each line is a single bullet that starts with one of: "Snapshot:", "Coverage:", "Blockers:", "Next actions:".
- Max 2 bullets per label.

Rules:
- Every bullet must reference specific line numbers or say "All remaining lines costed".
- No generic advice; only actions tied to the focus lines.
- Use the status text from focus lines (e.g., NEEDS SOURCING, IN STOCK) and include a concrete reason (counts or qty).
- Avoid vague words like "potential", "may", "could".
- If no actions are needed, write "Next actions: None".
- Keep it under 700 characters. Do not repeat the stats verbatim.
"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "You are an expert aviation parts procurement analyst. Provide clear, actionable analysis focused on helping complete sourcing and costing efficiently."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        max_tokens=500,
        temperature=0.2
    )

    return response.choices[0].message.content


def _get_base_currency_id(cur):
    base_code = (get_base_currency() or 'GBP').upper()
    cur.execute("SELECT id FROM currencies WHERE currency_code = ?", (base_code,))
    row = cur.fetchone()
    if row:
        return row['id']
    cur.execute("SELECT id FROM currencies ORDER BY id LIMIT 1")
    fallback = cur.fetchone()
    return fallback['id'] if fallback else None


def _rank_suppliers_for_part(cur, base_part_number):
    # 1. Past VQs
    cur.execute("""
        SELECT DISTINCT
            s.id,
            s.name,
            COUNT(*) as vq_count,
            AVG(vl.vendor_price) as avg_price,
            AVG(vl.lead_days) as avg_lead_days
        FROM vq_lines vl
        JOIN vqs v ON vl.vq_id = v.id
        LEFT JOIN suppliers s ON v.supplier_id = s.id
        WHERE vl.base_part_number = ?
          AND s.id IS NOT NULL
          AND vl.vendor_price > 0
        GROUP BY s.id, s.name
        ORDER BY avg_price ASC
        LIMIT 5
    """, (base_part_number,))
    vq_suppliers = cur.fetchall()

    # 2. ILS Results
    cur.execute("""
        SELECT DISTINCT
            s.id,
            s.name,
            COUNT(*) as ils_results
        FROM ils_search_results ils
        JOIN suppliers s ON ils.supplier_id = s.id
        WHERE ils.base_part_number = ?
          AND s.id IS NOT NULL
        GROUP BY s.id, s.name
        ORDER BY ils_results DESC
        LIMIT 5
    """, (base_part_number,))
    ils_suppliers = cur.fetchall()

    # 3. Supplier Quotes (historical from other parts lists)
    cur.execute("""
        SELECT DISTINCT
            s.id,
            s.name,
            COUNT(*) as quote_count,
            AVG(sql.unit_price) as avg_price
        FROM parts_list_supplier_quote_lines sql
        JOIN parts_list_supplier_quotes sq ON sql.supplier_quote_id = sq.id
        JOIN suppliers s ON sq.supplier_id = s.id
        JOIN parts_list_lines pll ON sql.parts_list_line_id = pll.id
        WHERE pll.base_part_number = ?
          AND s.id IS NOT NULL
          AND sql.is_no_bid = FALSE
          AND sql.unit_price > 0
        GROUP BY s.id, s.name
        ORDER BY avg_price ASC
        LIMIT 5
    """, (base_part_number,))
    sq_suppliers = cur.fetchall()

    # 4. Purchase Orders
    cur.execute("""
        SELECT DISTINCT
            s.id,
            s.name,
            COUNT(*) as po_count,
            AVG(pol.price) as avg_price
        FROM purchase_order_lines pol
        JOIN purchase_orders po ON pol.purchase_order_id = po.id
        JOIN suppliers s ON po.supplier_id = s.id
        WHERE pol.base_part_number = ?
          AND s.id IS NOT NULL
          AND pol.price > 0
        GROUP BY s.id, s.name
        ORDER BY avg_price ASC
        LIMIT 5
    """, (base_part_number,))
    po_suppliers = cur.fetchall()

    supplier_scores = {}

    def _get_entry(supplier_id, supplier_name):
        if supplier_id not in supplier_scores:
            supplier_scores[supplier_id] = {
                'supplier_id': supplier_id,
                'supplier_name': supplier_name,
                'score': 0,
                'reasons': []
            }
        return supplier_scores[supplier_id]

    # POs are highest signal (we actually bought from them)
    for idx, po in enumerate(po_suppliers):
        entry = _get_entry(po['id'], po['name'])
        entry['score'] += 15 - idx
        entry['reasons'].append(f"{po['po_count']} past orders, avg ${po['avg_price']:.2f}")

    # Supplier quotes are next best
    for idx, sq in enumerate(sq_suppliers):
        entry = _get_entry(sq['id'], sq['name'])
        entry['score'] += 12 - idx
        entry['reasons'].append(f"{sq['quote_count']} past quotes (PL), avg ${sq['avg_price']:.2f}")

    # VQs
    for idx, vq in enumerate(vq_suppliers):
        entry = _get_entry(vq['id'], vq['name'])
        entry['score'] += 10 - idx
        entry['reasons'].append(f"{vq['vq_count']} past VQs, avg ${vq['avg_price']:.2f}")

    # ILS is good for breadth
    for idx, ils in enumerate(ils_suppliers):
        entry = _get_entry(ils['id'], ils['name'])
        entry['score'] += 5 - idx
        entry['reasons'].append(f"{ils['ils_results']} ILS results")

    return sorted(supplier_scores.values(), key=lambda x: x['score'], reverse=True)[:5]


@parts_list_ai_bp.route('/api/assign-stock/<int:list_id>', methods=['POST'])
def assign_stock(list_id):
    """
    Assign stock costs to lines where stock fully covers quantity.
    """
    try:
        data = request.get_json() or {}
        line_ids = data.get('line_ids')

        conn = get_db_connection()
        cur = conn.cursor()

        base_currency_id = _get_base_currency_id(cur)

        if line_ids:
            placeholders = ",".join("?" for _ in line_ids)
            cur.execute(f"""
                SELECT
                    pll.id,
                    pll.quantity,
                    COALESCE((SELECT SUM(sm.available_quantity)
                     FROM stock_movements sm
                     WHERE sm.base_part_number = pll.base_part_number
                       AND sm.movement_type = 'IN'
                       AND sm.available_quantity > 0), 0) as stock_qty,
                    (SELECT MIN(sm.cost_per_unit)
                     FROM stock_movements sm
                     WHERE sm.base_part_number = pll.base_part_number
                       AND sm.movement_type = 'IN'
                       AND sm.available_quantity > 0
                       AND sm.cost_per_unit > 0) as stock_cost
                FROM parts_list_lines pll
                WHERE pll.parts_list_id = ?
                  AND pll.id IN ({placeholders})
                  AND pll.chosen_cost IS NULL
            """, (list_id, *line_ids))
        else:
            cur.execute("""
                SELECT
                    pll.id,
                    pll.quantity,
                    COALESCE((SELECT SUM(sm.available_quantity)
                     FROM stock_movements sm
                     WHERE sm.base_part_number = pll.base_part_number
                       AND sm.movement_type = 'IN'
                       AND sm.available_quantity > 0), 0) as stock_qty,
                    (SELECT MIN(sm.cost_per_unit)
                     FROM stock_movements sm
                     WHERE sm.base_part_number = pll.base_part_number
                       AND sm.movement_type = 'IN'
                       AND sm.available_quantity > 0
                       AND sm.cost_per_unit > 0) as stock_cost
                FROM parts_list_lines pll
                WHERE pll.parts_list_id = ?
                  AND pll.chosen_cost IS NULL
            """, (list_id,))
        lines = cur.fetchall()

        updated_ids = []
        for line in lines:
            stock_qty = line['stock_qty'] or 0
            stock_cost = line['stock_cost']
            if stock_qty >= line['quantity'] and stock_cost and stock_cost > 0:
                cur.execute("""
                    UPDATE parts_list_lines
                    SET chosen_cost = ?,
                        chosen_qty = ?,
                        chosen_currency_id = ?,
                        date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (stock_cost, line['quantity'], base_currency_id, line['id']))
                updated_ids.append(line['id'])

        conn.commit()
        conn.close()

        return jsonify(success=True, updated_count=len(updated_ids), line_ids=updated_ids)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_ai_bp.route('/api/add-suggested-suppliers/<int:list_id>', methods=['POST'])
def add_suggested_suppliers(list_id):
    """
    Add suggested suppliers to unsourced lines (first pass).
    """
    try:
        data = request.get_json() or {}
        line_ids = data.get('line_ids')

        conn = get_db_connection()
        cur = conn.cursor()

        if line_ids:
            placeholders = ",".join("?" for _ in line_ids)
            cur.execute(f"""
                SELECT
                    pll.id,
                    pll.base_part_number,
                    pll.customer_part_number,
                    pll.quantity,
                    pll.chosen_cost,
                    COALESCE((SELECT SUM(sm.available_quantity)
                     FROM stock_movements sm
                     WHERE sm.base_part_number = pll.base_part_number
                       AND sm.movement_type = 'IN'
                       AND sm.available_quantity > 0), 0) as stock_qty,
                    COALESCE((SELECT COUNT(DISTINCT sq.supplier_id)
                     FROM parts_list_supplier_quote_lines sql
                     JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                     WHERE sql.parts_list_line_id = pll.id
                       AND sql.is_no_bid = FALSE
                       AND sql.unit_price IS NOT NULL), 0) as quote_count,
                    COALESCE((SELECT COUNT(*)
                     FROM parts_list_line_suggested_suppliers ss
                     WHERE ss.parts_list_line_id = pll.id), 0) as suggested_count
                FROM parts_list_lines pll
                WHERE pll.parts_list_id = ?
                  AND pll.id IN ({placeholders})
            """, (list_id, *line_ids))
            candidate_lines = [
                line for line in cur.fetchall()
                if not line['chosen_cost']
                and (line['stock_qty'] or 0) < line['quantity']
                and (line['quote_count'] or 0) == 0
                and (line['suggested_count'] or 0) == 0
            ]
        else:
            cur.execute("""
                SELECT
                    pll.id,
                    pll.base_part_number,
                    pll.customer_part_number,
                    pll.quantity,
                    COALESCE((SELECT SUM(sm.available_quantity)
                     FROM stock_movements sm
                     WHERE sm.base_part_number = pll.base_part_number
                       AND sm.movement_type = 'IN'
                       AND sm.available_quantity > 0), 0) as stock_qty,
                    COALESCE((SELECT COUNT(DISTINCT sq.supplier_id)
                     FROM parts_list_supplier_quote_lines sql
                     JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                     WHERE sql.parts_list_line_id = pll.id
                       AND sql.is_no_bid = FALSE
                       AND sql.unit_price IS NOT NULL), 0) as quote_count,
                    COALESCE((SELECT COUNT(*)
                     FROM parts_list_line_suggested_suppliers ss
                     WHERE ss.parts_list_line_id = pll.id), 0) as suggested_count
                FROM parts_list_lines pll
                WHERE pll.parts_list_id = ?
                  AND pll.chosen_cost IS NULL
            """, (list_id,))
            candidate_lines = [
                line for line in cur.fetchall()
                if (line['stock_qty'] or 0) < line['quantity']
                and (line['quote_count'] or 0) == 0
                and (line['suggested_count'] or 0) == 0
            ]

        added_count = 0
        updated_lines = []

        for line in candidate_lines:
            if not line['base_part_number']:
                continue
            ranked = _rank_suppliers_for_part(cur, line['base_part_number'])
            if not ranked:
                continue

            cur.execute("""
                SELECT supplier_id
                FROM parts_list_line_suggested_suppliers
                WHERE parts_list_line_id = ?
            """, (line['id'],))
            existing_ids = {row['supplier_id'] for row in cur.fetchall()}

            line_added = 0
            for supplier in ranked:
                supplier_id = supplier['supplier_id']
                if supplier_id in existing_ids:
                    continue
                cur.execute("""
                    INSERT INTO parts_list_line_suggested_suppliers
                        (parts_list_line_id, supplier_id, source_type, date_added)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (line['id'], supplier_id, 'ai'))
                added_count += 1
                line_added += 1
                existing_ids.add(supplier_id)

            if line_added:
                updated_lines.append(line['id'])

        conn.commit()
        conn.close()

        return jsonify(
            success=True,
            updated_lines=len(updated_lines),
            added_count=added_count
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_ai_bp.route('/api/suggest-suppliers/<int:list_id>', methods=['POST'])
def suggest_suppliers(list_id):
    """
    Use AI to suggest best suppliers for unsourced parts
    """
    try:
        data = request.get_json() or {}
        line_ids = data.get('line_ids', [])

        conn = get_db_connection()
        cur = conn.cursor()

        suggestions = []

        for line_id in line_ids:
            # Get line info
            cur.execute("""
                SELECT
                    pll.customer_part_number,
                    pll.base_part_number,
                    pll.quantity
                FROM parts_list_lines pll
                WHERE pll.id = ? AND pll.parts_list_id = ?
            """, (line_id, list_id))
            line = cur.fetchone()

            if not line or not line['base_part_number']:
                continue

            # Use the shared ranking function
            ranked = _rank_suppliers_for_part(cur, line['base_part_number'])

            suggestions.append({
                'line_id': line_id,
                'part_number': line['customer_part_number'],
                'suggested_suppliers': ranked
            })

        conn.close()

        return jsonify(success=True, suggestions=suggestions)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


# =============================================================================
# Monroe Aerospace Integration
# =============================================================================

def _ensure_monroe_tables(cur):
    """Ensure Monroe-related tables exist."""
    # Use SERIAL for PostgreSQL (auto-detected by the DB wrapper)
    # This syntax works for both PostgreSQL and SQLite
    cur.execute("""
        CREATE TABLE IF NOT EXISTS monroe_search_results (
            id SERIAL PRIMARY KEY,
            parts_list_id INTEGER REFERENCES parts_lists(id),
            parts_list_line_id INTEGER REFERENCES parts_list_lines(id),
            base_part_number TEXT NOT NULL,
            searched_part_number TEXT,
            monroe_part_number TEXT,
            unit_price DECIMAL(12,4),
            inventory INTEGER,
            minimum_order INTEGER,
            purchase_increment INTEGER,
            currency_code TEXT DEFAULT 'USD',
            search_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            error_message TEXT,
            debug_info TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_monroe_results_line
        ON monroe_search_results(parts_list_line_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_monroe_results_part
        ON monroe_search_results(base_part_number)
    """)


def _get_monroe_supplier_id(cur):
    """Get the Monroe supplier ID from app_settings."""
    cur.execute("SELECT value FROM app_settings WHERE key = 'monroe_supplier_id'")
    row = cur.fetchone()
    if row:
        try:
            return int(row['value'])
        except (ValueError, TypeError):
            return None
    return None


def _set_monroe_supplier_id(cur, supplier_id):
    """Set the Monroe supplier ID in app_settings."""
    # Check if setting exists
    cur.execute("SELECT 1 FROM app_settings WHERE key = 'monroe_supplier_id'")
    exists = cur.fetchone()

    if exists:
        cur.execute(
            "UPDATE app_settings SET value = ? WHERE key = 'monroe_supplier_id'",
            (str(supplier_id),)
        )
    else:
        cur.execute(
            "INSERT INTO app_settings (key, value) VALUES ('monroe_supplier_id', ?)",
            (str(supplier_id),)
        )


def _get_monroe_auto_check(cur):
    """Get the Monroe auto-check setting from app_settings."""
    cur.execute("SELECT value FROM app_settings WHERE key = 'monroe_auto_check'")
    row = cur.fetchone()
    if row:
        return row['value'].lower() in ('true', '1', 'yes', 'on')
    return False


def _set_monroe_auto_check(cur, enabled):
    """Set the Monroe auto-check setting in app_settings."""
    value = 'true' if enabled else 'false'
    cur.execute("SELECT 1 FROM app_settings WHERE key = 'monroe_auto_check'")
    exists = cur.fetchone()

    if exists:
        cur.execute(
            "UPDATE app_settings SET value = ? WHERE key = 'monroe_auto_check'",
            (value,)
        )
    else:
        cur.execute(
            "INSERT INTO app_settings (key, value) VALUES ('monroe_auto_check', ?)",
            (value,)
        )


def _get_user_monroe_settings(cur, user_id):
    """
    Get Monroe settings for a specific user.
    Returns dict with auto_search_new_parts and auto_create_supplier_offer.
    """
    cur.execute("""
        SELECT auto_search_new_parts, auto_create_supplier_offer
        FROM user_monroe_settings
        WHERE user_id = ?
    """, (user_id,))
    row = cur.fetchone()
    if row:
        return {
            'auto_search_new_parts': bool(row['auto_search_new_parts']),
            'auto_create_supplier_offer': bool(row['auto_create_supplier_offer'])
        }
    # Return defaults if no settings found
    return {
        'auto_search_new_parts': False,
        'auto_create_supplier_offer': False
    }


def _set_user_monroe_settings(cur, user_id, auto_search_new_parts=None, auto_create_supplier_offer=None):
    """
    Set Monroe settings for a specific user.
    Only updates fields that are provided (not None).
    """
    # Check if user settings exist
    cur.execute("SELECT 1 FROM user_monroe_settings WHERE user_id = ?", (user_id,))
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
            sql = f"UPDATE user_monroe_settings SET {', '.join(updates)} WHERE user_id = ?"
            cur.execute(sql, params)
    else:
        # Insert new settings
        cur.execute("""
            INSERT INTO user_monroe_settings
            (user_id, auto_search_new_parts, auto_create_supplier_offer)
            VALUES (?, ?, ?)
        """, (
            user_id,
            auto_search_new_parts if auto_search_new_parts is not None else False,
            auto_create_supplier_offer if auto_create_supplier_offer is not None else False
        ))


def _run_monroe_check_background(list_id, line_ids, user_id=None, auto_create_offer=False):
    """
    Run Monroe check in background thread for specified line IDs.
    Called when auto-check is enabled and new lines are added.
    If auto_create_offer is True, will automatically create a supplier quote for results with prices.
    """
    logging.info(f"Monroe auto-check background started: list {list_id}, line_ids={line_ids}, user={user_id}")

    if not PLAYWRIGHT_AVAILABLE:
        logging.warning("Monroe auto-check skipped: Playwright not available")
        return

    if not line_ids:
        logging.warning(f"Monroe auto-check skipped for list {list_id}: no line_ids provided")
        return

    try:
        from routes.notifications import create_notification

        conn = get_db_connection()
        cur = conn.cursor()

        # Ensure tables exist
        _ensure_monroe_tables(cur)
        conn.commit()

        # Get line details
        placeholders = ",".join("?" for _ in line_ids)
        logging.info(f"Monroe auto-check: Fetching {len(line_ids)} lines from database")
        cur.execute(f"""
            SELECT id, customer_part_number, base_part_number, quantity
            FROM parts_list_lines
            WHERE parts_list_id = ? AND id IN ({placeholders})
        """, (list_id, *line_ids))
        lines = [dict(row) for row in cur.fetchall()]
        logging.info(f"Monroe auto-check: Found {len(lines)} lines in database")

        result_ids = []  # Track result IDs for auto-offer creation

        for line in lines:
            part_number = line['customer_part_number'] or line['base_part_number']
            if not part_number:
                continue

            # Check if we already have a recent result for this part (within 24 hours)
            # Use database-agnostic date comparison
            if _using_postgres():
                recent_query = """
                    SELECT id FROM monroe_search_results
                    WHERE base_part_number = ?
                      AND search_date > CURRENT_TIMESTAMP - INTERVAL '24 hours'
                      AND unit_price IS NOT NULL
                    LIMIT 1
                """
            else:
                recent_query = """
                    SELECT id FROM monroe_search_results
                    WHERE base_part_number = ?
                      AND search_date > datetime('now', '-24 hours')
                      AND unit_price IS NOT NULL
                    LIMIT 1
                """
            cur.execute(recent_query, (line['base_part_number'],))
            existing = cur.fetchone()
            if existing:
                logging.debug(f"Monroe auto-check: Skipping {part_number}, recent result exists")
                continue

            logging.info(f"Monroe auto-check: Checking {part_number}")

            # Scrape Monroe
            scrape_result = _scrape_monroe(part_number, headless=True)

            # Only store inventory/MOQ if we got a valid price
            # (avoids storing bogus numbers like phone numbers)
            has_price = scrape_result.get('unit_price') is not None
            inventory = scrape_result.get('inventory') if has_price else None
            minimum_order = scrape_result.get('minimum_order') if has_price else None
            purchase_increment = scrape_result.get('purchase_increment') if has_price else None

            # Store result
            if _using_postgres():
                cur.execute("""
                    INSERT INTO monroe_search_results
                    (parts_list_id, parts_list_line_id, base_part_number, searched_part_number,
                     unit_price, inventory, minimum_order, purchase_increment, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                """, (
                    list_id,
                    line['id'],
                    line['base_part_number'],
                    part_number,
                    scrape_result.get('unit_price'),
                    inventory,
                    minimum_order,
                    purchase_increment,
                    scrape_result.get('error')
                ))
                result_row = cur.fetchone()
                if result_row and has_price:
                    result_ids.append(result_row['id'])
            else:
                cur.execute("""
                    INSERT INTO monroe_search_results
                    (parts_list_id, parts_list_line_id, base_part_number, searched_part_number,
                     unit_price, inventory, minimum_order, purchase_increment, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    list_id,
                    line['id'],
                    line['base_part_number'],
                    part_number,
                    scrape_result.get('unit_price'),
                    inventory,
                    minimum_order,
                    purchase_increment,
                    scrape_result.get('error')
                ))
                if has_price:
                    result_ids.append(cur.lastrowid)

            conn.commit()

            # Small delay between requests to be respectful to Monroe's servers
            time.sleep(1)

        # Auto-create supplier offer if enabled and we have results with prices
        if auto_create_offer and result_ids:
            try:
                _auto_create_monroe_offer(cur, list_id, result_ids, user_id)
                conn.commit()
                logging.info(f"Monroe: Auto-created supplier offer for list {list_id} with {len(result_ids)} lines")
            except Exception as e:
                logging.exception(f"Failed to auto-create Monroe offer: {e}")

        # Get parts list name and customer for notification
        cur.execute("""
            SELECT pl.name, c.name as customer_name
            FROM parts_lists pl
            LEFT JOIN customers c ON c.id = pl.customer_id
            WHERE pl.id = ?
        """, (list_id,))
        pl_row = cur.fetchone()
        parts_list_name = pl_row['name'] if pl_row else f"Parts List #{list_id}"
        customer_name = pl_row['customer_name'] if pl_row and pl_row['customer_name'] else None

        # Count successful and failed results
        successful = len(result_ids)
        failed = len(lines) - successful

        conn.close()
        logging.info(f"Monroe auto-check completed for list {list_id}, {len(lines)} lines")

        # Create notification for the user if there were any results
        if user_id and len(lines) > 0:
            title = "Monroe Auto-Check Complete"
            # Include customer name if available
            if customer_name:
                message = f"{customer_name} - {parts_list_name}: {successful} prices found, {failed} not found"
            else:
                message = f"{parts_list_name}: {successful} prices found, {failed} not found"
            link_url = f"/parts_list/view/{list_id}"
            link_text = "View Parts List"

            create_notification(
                user_id=user_id,
                notification_type='scrape_complete',
                title=title,
                message=message,
                link_url=link_url,
                link_text=link_text,
                metadata={
                    'supplier': 'monroe',
                    'parts_list_id': list_id,
                    'customer_name': customer_name,
                    'successful': successful,
                    'failed': failed,
                    'total': len(lines),
                    'auto_check': True
                }
            )

    except Exception as e:
        logging.exception(f"Monroe auto-check failed: {e}")


def _monroe_inventory_meets_moq(result):
    """Return True when Monroe inventory is sufficient for the MOQ, if both are known."""
    inventory = result.get('inventory')
    moq = result.get('minimum_order')

    if inventory is None or moq is None:
        return True

    try:
        return int(inventory) >= int(moq)
    except (TypeError, ValueError):
        return True


def _filter_monroe_offer_results(results, context_label):
    """Skip Monroe results that cannot satisfy their MOQ with available inventory."""
    valid_results = []
    skipped_results = []

    for result in results:
        if _monroe_inventory_meets_moq(result):
            valid_results.append(result)
            continue

        skipped_results.append(result)
        logging.info(
            "Monroe %s: Skipping line %s (%s) because inventory %s is below MOQ %s",
            context_label,
            result.get('line_number') or result.get('parts_list_line_id'),
            result.get('searched_part_number') or result.get('base_part_number'),
            result.get('inventory'),
            result.get('minimum_order'),
        )

    return valid_results, skipped_results


def _get_monroe_offer_results(cur, result_ids):
    """Fetch Monroe results for offer creation in manageable batches."""
    cleaned_ids = []
    for result_id in result_ids or []:
        try:
            cleaned_ids.append(int(result_id))
        except (TypeError, ValueError):
            continue

    if not cleaned_ids:
        return []

    results = []
    batch_size = 500
    for idx in range(0, len(cleaned_ids), batch_size):
        batch_ids = cleaned_ids[idx:idx + batch_size]
        placeholders = ",".join("?" for _ in batch_ids)
        cur.execute(f"""
            SELECT msr.*, pll.line_number, pll.quantity as requested_quantity
            FROM monroe_search_results msr
            JOIN parts_list_lines pll ON pll.id = msr.parts_list_line_id
            WHERE msr.id IN ({placeholders})
              AND msr.unit_price IS NOT NULL
        """, batch_ids)
        results.extend(dict(row) for row in cur.fetchall())

    return results


def _auto_create_monroe_offer(cur, list_id, result_ids, user_id):
    """
    Automatically create a supplier quote/offer from Monroe results.
    Similar to monroe_load_as_offer endpoint but for background processing.
    """
    if not result_ids:
        return

    # Get Monroe supplier ID
    monroe_supplier_id = _get_monroe_supplier_id(cur)
    if not monroe_supplier_id:
        logging.error("Cannot auto-create Monroe offer: Monroe supplier not configured")
        return

    # Get USD currency ID
    cur.execute("SELECT id FROM currencies WHERE currency_code = 'USD'")
    currency_row = cur.fetchone()
    if currency_row:
        usd_currency_id = currency_row['id']
    else:
        # Try to create USD currency
        if _using_postgres():
            cur.execute("INSERT INTO currencies (currency_code, exchange_rate_to_eur) VALUES ('USD', 1.0) RETURNING id")
            currency_row = cur.fetchone()
            usd_currency_id = currency_row['id'] if currency_row else 1
        else:
            cur.execute("INSERT INTO currencies (currency_code, exchange_rate_to_eur) VALUES ('USD', 1.0)")
            usd_currency_id = cur.lastrowid

    # Get Monroe results
    results = _get_monroe_offer_results(cur, result_ids)
    valid_results, skipped_results = _filter_monroe_offer_results(results, "auto-offer")

    if not valid_results:
        logging.info(
            "Monroe auto-offer: No quote created for list %s because %s result(s) were below MOQ availability",
            list_id,
            len(skipped_results),
        )
        return

    # Create supplier quote header
    if _using_postgres():
        cur.execute("""
            INSERT INTO parts_list_supplier_quotes
            (parts_list_id, supplier_id, quote_reference, quote_date, currency_id, notes, created_by_user_id)
            VALUES (?, ?, ?, CURRENT_DATE, ?, ?, ?)
            RETURNING id
        """, (list_id, monroe_supplier_id, 'Monroe Web Scrape (Auto)', usd_currency_id,
              'Auto-imported from Monroe Aerospace website', user_id))
        quote_row = cur.fetchone()
        quote_id = quote_row['id']
    else:
        cur.execute("""
            INSERT INTO parts_list_supplier_quotes
            (parts_list_id, supplier_id, quote_reference, quote_date, currency_id, notes, created_by_user_id)
            VALUES (?, ?, ?, DATE('now'), ?, ?, ?)
        """, (list_id, monroe_supplier_id, 'Monroe Web Scrape (Auto)', usd_currency_id,
              'Auto-imported from Monroe Aerospace website', user_id))
        quote_id = cur.lastrowid

    # Create quote lines
    lines_created = 0
    for result in valid_results:
        requested_qty = result.get('requested_quantity') or 1
        moq = result.get('minimum_order') or 1
        quantity_quoted = max(requested_qty, moq)
        if quantity_quoted < 1:
            quantity_quoted = 1
        part_value = result.get('searched_part_number') or result.get('base_part_number')
        base_part_number = create_base_part_number(part_value) if part_value else result.get('base_part_number')
        if base_part_number:
            _ensure_part_number(cur, base_part_number, part_value)

        cur.execute("""
            INSERT INTO parts_list_supplier_quote_lines
            (supplier_quote_id, parts_list_line_id, quoted_part_number,
             quantity_quoted, unit_price, condition_code, is_no_bid,
             qty_available, purchase_increment, moq)
            VALUES (?, ?, ?, ?, ?, 'NE', FALSE, ?, ?, ?)
        """, (
            quote_id,
            result['parts_list_line_id'],
            result['searched_part_number'],
            quantity_quoted,
            result['unit_price'],
            result.get('inventory'),
            result.get('purchase_increment'),
            result.get('minimum_order')
        ))
        lines_created += 1

    logging.info(
        f"Auto-created Monroe quote {quote_id} with {lines_created} lines"
        + (f" ({len(skipped_results)} line(s) skipped for MOQ)" if skipped_results else "")
    )
    return quote_id


def trigger_monroe_auto_check(list_id, line_ids, user_id=None):
    """
    Trigger Monroe auto-check in a background thread if enabled for the user.
    Called from add_lines endpoint.
    """
    logging.info(f"Monroe auto-check trigger called for list {list_id}, user {user_id}, lines {line_ids}")

    if not PLAYWRIGHT_AVAILABLE:
        logging.warning(f"Monroe auto-check skipped for list {list_id}: Playwright not available")
        return

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # If no user_id provided, get it from the parts list
        if user_id is None:
            logging.info(f"Monroe auto-check: No user_id provided, looking up from parts list {list_id}")
            cur.execute("""
                SELECT salesperson_id FROM parts_lists WHERE id = ?
            """, (list_id,))
            row = cur.fetchone()
            if row:
                # Look up user_id from salesperson_id
                cur.execute("""
                    SELECT user_id FROM salesperson_user_link WHERE legacy_salesperson_id = ?
                """, (row['salesperson_id'],))
                user_row = cur.fetchone()
                if user_row:
                    user_id = user_row['user_id']
                    logging.info(f"Monroe auto-check: Found user_id {user_id} from salesperson {row['salesperson_id']}")
                else:
                    logging.warning(f"Monroe auto-check: No user linked to salesperson {row['salesperson_id']}")

        # Check user-specific settings
        if user_id:
            user_settings = _get_user_monroe_settings(cur, user_id)
            auto_search = user_settings.get('auto_search_new_parts', False)
            auto_create_offer = user_settings.get('auto_create_supplier_offer', False)
            logging.info(f"Monroe auto-check: User {user_id} settings: auto_search={auto_search}, auto_create_offer={auto_create_offer}")
        else:
            auto_search = False
            auto_create_offer = False
            logging.warning(f"Monroe auto-check skipped for list {list_id}: No user_id found")

        # If auto-search is not enabled for this user, exit
        if not auto_search:
            logging.info(f"Monroe auto-check skipped for list {list_id}: auto_search_new_parts is disabled for user {user_id}")
            conn.close()
            return

        # Check if Monroe supplier is configured
        monroe_supplier_id = _get_monroe_supplier_id(cur)
        if not monroe_supplier_id:
            logging.warning(f"Monroe auto-check skipped for list {list_id}: Monroe supplier not configured")
            conn.close()
            return

        logging.info(f"Monroe auto-check: All checks passed, starting background thread for list {list_id}")
        conn.close()

        # Run in background thread
        thread = threading.Thread(
            target=_run_monroe_check_background,
            args=(list_id, line_ids, user_id, auto_create_offer),
            daemon=True
        )
        thread.start()
        logging.info(f"Monroe auto-check started in background for list {list_id}, user {user_id}")

    except Exception as e:
        logging.exception(f"Failed to trigger Monroe auto-check: {e}")


def trigger_monroe_auto_check_with_status(list_id, line_ids, user_id, status_id, force=False):
    """
    Trigger Monroe check with status tracking.
    Used when manually triggering from Supplier Portal.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Check if Monroe supplier is configured
        monroe_supplier_id = _get_monroe_supplier_id(cur)
        if not monroe_supplier_id:
            # Update status to failed
            cur.execute("""
                UPDATE supplier_scrape_status
                SET status = 'failed', error_message = 'Monroe supplier not configured', completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status_id,))
            conn.commit()
            conn.close()
            return

        # Get user settings for auto_create_offer
        auto_create_offer = False
        if user_id:
            user_settings = _get_user_monroe_settings(cur, user_id)
            auto_create_offer = user_settings.get('auto_create_supplier_offer', False)

        conn.close()

        # Run in background thread with status tracking
        thread = threading.Thread(
            target=_run_monroe_check_with_status,
            args=(list_id, line_ids, user_id, auto_create_offer, status_id),
            daemon=True
        )
        thread.start()
        logging.info(f"Monroe check with status tracking started for list {list_id}, status {status_id}")

    except Exception as e:
        logging.exception(f"Failed to trigger Monroe check with status: {e}")


def _run_monroe_check_with_status(list_id, line_ids, user_id, auto_create_offer, status_id):
    """
    Run Monroe check with status updates.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return

    try:
        from routes.notifications import create_notification

        conn = get_db_connection()
        cur = conn.cursor()

        # Update status to in_progress
        cur.execute("""
            UPDATE supplier_scrape_status
            SET status = 'in_progress'
            WHERE id = ?
        """, (status_id,))
        conn.commit()

        # Ensure tables exist
        _ensure_monroe_tables(cur)
        conn.commit()

        # Get line details
        placeholders = ",".join("?" for _ in line_ids)
        cur.execute(f"""
            SELECT id, customer_part_number, base_part_number, quantity
            FROM parts_list_lines
            WHERE parts_list_id = ? AND id IN ({placeholders})
        """, (list_id, *line_ids))
        lines = [dict(row) for row in cur.fetchall()]

        result_ids = []
        processed = 0
        successful = 0
        failed = 0

        for line in lines:
            # Stop early if user cancelled this scrape from Supplier Portal
            cur.execute("SELECT status FROM supplier_scrape_status WHERE id = ?", (status_id,))
            status_row = cur.fetchone()
            if status_row and status_row['status'] == 'cancelled':
                logging.info(f"Monroe status {status_id}: cancelled by user")
                conn.close()
                return

            part_number = line['customer_part_number'] or line['base_part_number']
            if not part_number:
                processed += 1
                failed += 1
                continue

            # Update current part number
            cur.execute("""
                UPDATE supplier_scrape_status
                SET current_part_number = ?, processed_lines = ?
                WHERE id = ?
            """, (part_number, processed, status_id))
            conn.commit()

            logging.info(f"Monroe status {status_id}: Checking {part_number}")

            # Scrape Monroe
            scrape_result = _scrape_monroe(part_number, headless=True)

            has_price = scrape_result.get('unit_price') is not None
            inventory = scrape_result.get('inventory') if has_price else None
            minimum_order = scrape_result.get('minimum_order') if has_price else None
            purchase_increment = scrape_result.get('purchase_increment') if has_price else None

            # Store debug info as JSON string
            debug_info_json = json.dumps(scrape_result.get('debug_info', []))

            # Store result
            if _using_postgres():
                cur.execute("""
                    INSERT INTO monroe_search_results
                    (parts_list_id, parts_list_line_id, base_part_number, searched_part_number,
                     unit_price, inventory, minimum_order, purchase_increment, error_message, debug_info)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                """, (
                    list_id, line['id'], line['base_part_number'], part_number,
                    scrape_result.get('unit_price'), inventory, minimum_order,
                    purchase_increment, scrape_result.get('error'), debug_info_json
                ))
                result_row = cur.fetchone()
                if result_row and has_price:
                    result_ids.append(result_row['id'])
                    successful += 1
                else:
                    failed += 1
            else:
                cur.execute("""
                    INSERT INTO monroe_search_results
                    (parts_list_id, parts_list_line_id, base_part_number, searched_part_number,
                     unit_price, inventory, minimum_order, purchase_increment, error_message, debug_info)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    list_id, line['id'], line['base_part_number'], part_number,
                    scrape_result.get('unit_price'), inventory, minimum_order,
                    purchase_increment, scrape_result.get('error'), debug_info_json
                ))
                if has_price:
                    result_ids.append(cur.lastrowid)
                    successful += 1
                else:
                    failed += 1

            processed += 1
            conn.commit()

            # Small delay between requests
            time.sleep(1)

        # Auto-create supplier offer if enabled and we have results with prices
        if auto_create_offer and result_ids:
            try:
                _auto_create_monroe_offer(cur, list_id, result_ids, user_id)
                conn.commit()
                logging.info(f"Monroe: Auto-created supplier offer for list {list_id} with {len(result_ids)} lines")
            except Exception as e:
                logging.exception(f"Failed to auto-create Monroe offer: {e}")

        # Get parts list name and customer for notification
        cur.execute("""
            SELECT pl.name, c.name as customer_name
            FROM parts_lists pl
            LEFT JOIN customers c ON c.id = pl.customer_id
            WHERE pl.id = ?
        """, (list_id,))
        pl_row = cur.fetchone()
        parts_list_name = pl_row['name'] if pl_row else f"Parts List #{list_id}"
        customer_name = pl_row['customer_name'] if pl_row and pl_row['customer_name'] else None

        # If cancelled while finishing up, do not overwrite cancellation status.
        cur.execute("SELECT status FROM supplier_scrape_status WHERE id = ?", (status_id,))
        status_row = cur.fetchone()
        if status_row and status_row['status'] == 'cancelled':
            conn.close()
            logging.info(f"Monroe status {status_id}: cancellation preserved during finalization")
            return

        # Update final status
        cur.execute("""
            UPDATE supplier_scrape_status
            SET status = 'completed',
                processed_lines = ?,
                successful_lines = ?,
                failed_lines = ?,
                completed_at = CURRENT_TIMESTAMP,
                current_part_number = NULL
            WHERE id = ?
        """, (processed, successful, failed, status_id))
        conn.commit()
        conn.close()

        logging.info(f"Monroe check completed for list {list_id}, status {status_id}: {successful} successful, {failed} failed")

        # Create notification for the user
        if user_id:
            title = "Monroe Scraping Complete"
            # Include customer name if available
            if customer_name:
                message = f"{customer_name} - {parts_list_name}: {successful} prices found, {failed} not found"
            else:
                message = f"{parts_list_name}: {successful} prices found, {failed} not found"
            link_url = f"/parts_list/view/{list_id}"
            link_text = "View Parts List"

            create_notification(
                user_id=user_id,
                notification_type='scrape_complete',
                title=title,
                message=message,
                link_url=link_url,
                link_text=link_text,
                metadata={
                    'supplier': 'monroe',
                    'parts_list_id': list_id,
                    'customer_name': customer_name,
                    'successful': successful,
                    'failed': failed,
                    'total': processed
                }
            )

    except Exception as e:
        logging.exception(f"Monroe check with status failed: {e}")
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                UPDATE supplier_scrape_status
                SET status = 'failed', error_message = ?, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (str(e), status_id))
            conn.commit()
            conn.close()
        except:
            pass


def _scrape_monroe(product_name, headless=True):
    """
    Scrape Monroe Aerospace website for product information.
    Returns dict with unit_price, inventory, minimum_order, or error.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return {
            "product_name": product_name,
            "unit_price": None,
            "inventory": None,
            "minimum_order": None,
            "error": "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        }

    result = {
        "product_name": product_name,
        "unit_price": None,
        "inventory": None,
        "minimum_order": None,
        "purchase_increment": None,
        "error": None,
        "debug_info": []
    }

    screenshot_dir = "monroe_debug_screenshots"
    os.makedirs(screenshot_dir, exist_ok=True)
    timestamp = int(time.time())

    try:
        logging.info(f"Monroe scrape starting for: {product_name}")
        result["debug_info"].append(f"Starting scrape for: {product_name}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()

            # Set viewport for consistent rendering
            page.set_viewport_size({"width": 1920, "height": 1080})

            # Navigate to Monroe
            logging.info(f"Monroe: Navigating to catalog.monroeaerospace.com")
            result["debug_info"].append("Navigating to Monroe catalog")
            page.goto("https://catalog.monroeaerospace.com/express",
                     wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)

            # Take screenshot after page load
            page.screenshot(path=f"{screenshot_dir}/{timestamp}_01_pageload_{product_name.replace('/', '_')}.png")
            result["debug_info"].append(f"Screenshot saved: 01_pageload")

            # Find and fill the Express Ordering search input
            logging.info(f"Monroe: Looking for search input")
            result["debug_info"].append("Looking for search input #plp-express-search-text")

            try:
                search_input = page.wait_for_selector('#plp-express-search-text', timeout=10000)
                logging.info(f"Monroe: Found search input, filling with '{product_name}'")
                result["debug_info"].append(f"Found search input, filling with: {product_name}")
                search_input.click()
                search_input.fill(product_name)

                # Screenshot after filling
                page.screenshot(path=f"{screenshot_dir}/{timestamp}_02_search_filled_{product_name.replace('/', '_')}.png")
                result["debug_info"].append(f"Screenshot saved: 02_search_filled")
            except Exception as e:
                logging.error(f"Monroe: Failed to find search input: {e}")
                result["debug_info"].append(f"ERROR finding search input: {e}")
                page.screenshot(path=f"{screenshot_dir}/{timestamp}_ERROR_no_search_input.png")
                raise

            # Click the SEARCH button
            logging.info(f"Monroe: Looking for search button")
            result["debug_info"].append("Looking for search button")

            try:
                search_button = page.wait_for_selector(
                    'button:has-text("SEARCH"), .plp-cadpart-search-button, button[class*="search"]',
                    timeout=10000
                )
                logging.info(f"Monroe: Found search button, clicking")
                result["debug_info"].append("Found search button, clicking")
                search_button.click()
            except Exception as e:
                logging.error(f"Monroe: Failed to find search button: {e}")
                result["debug_info"].append(f"ERROR finding search button: {e}")
                page.screenshot(path=f"{screenshot_dir}/{timestamp}_ERROR_no_search_button.png")
                raise

            # Wait for results
            logging.info(f"Monroe: Waiting for search results")
            result["debug_info"].append("Waiting 3 seconds for results to load")
            time.sleep(3)

            # Screenshot after search
            page.screenshot(path=f"{screenshot_dir}/{timestamp}_03_search_results_{product_name.replace('/', '_')}.png")
            result["debug_info"].append(f"Screenshot saved: 03_search_results")

            # Debug: Get page content and search for part number
            page_content = page.content()
            logging.info(f"Monroe: Page title: {page.title()}")
            result["debug_info"].append(f"Page title: {page.title()}")

            # Check if part number appears anywhere on page
            if product_name.upper() in page_content.upper():
                logging.info(f"Monroe: Part number '{product_name}' found in page content")
                result["debug_info"].append(f"Part number found in page content")
            else:
                logging.warning(f"Monroe: Part number '{product_name}' NOT found in page content")
                result["debug_info"].append(f"WARNING: Part number NOT found in page content")

            def _has_exact_part_reference(haystack, part_number):
                """Return True when part_number appears as a standalone part token."""
                if not haystack or not part_number:
                    return False
                # Treat letters, numbers, slash, dot, and dash as part-number characters.
                # This avoids matching AN5C15 against AN5C15A or AN5C15-A.
                part_chars = r"A-Z0-9/.-"
                pattern = rf"(?<![{part_chars}]){re.escape(part_number.upper())}(?![{part_chars}])"
                return re.search(pattern, haystack.upper()) is not None

            # Try multiple selectors to find the product link
            product_link = None
            selectors_to_try = [
                f'a:has-text("{product_name}")',
                f'a[href*="{product_name}"]',
                '.plp-express-results a',
                '.plp-product-link',
                'a.product-link'
            ]

            for selector in selectors_to_try:
                logging.info(f"Monroe: Trying selector: {selector}")
                result["debug_info"].append(f"Trying selector: {selector}")

                links = page.query_selector_all(selector)
                logging.info(f"Monroe: Found {len(links)} links with selector '{selector}'")
                result["debug_info"].append(f"Found {len(links)} links")

                # Check each link for our part number
                for link in links:
                    link_text = link.text_content().strip()
                    link_href = link.get_attribute('href') or ''
                    logging.info(f"Monroe: Checking link text: '{link_text}', href: '{link_href}'")
                    text_exact_match = _has_exact_part_reference(link_text, product_name)
                    href_exact_match = _has_exact_part_reference(link_href, product_name)
                    result["debug_info"].append(
                        f"Link: text='{link_text}', text_exact_match={text_exact_match}, href_exact_match={href_exact_match}"
                    )

                    if text_exact_match or href_exact_match:
                        product_link = link
                        logging.info(f"Monroe: MATCH FOUND! Link text: {link_text}")
                        result["debug_info"].append(f"MATCH FOUND with text: {link_text}")
                        break

                if product_link:
                    break

            if product_link:
                logging.info(f"Monroe: Product link found, extracting price from results page")
                result["debug_info"].append("Product link found, extracting price")

                # Extract price from the search results page
                body = page.query_selector('body')
                if body:
                    all_text = body.text_content()
                    prices = re.findall(r'\$(\d+\.?\d*)', all_text)
                    logging.info(f"Monroe: Found {len(prices)} prices on page: {prices}")
                    result["debug_info"].append(f"Found {len(prices)} prices: {prices}")

                    if prices:
                        try:
                            result["unit_price"] = float(prices[0])
                            logging.info(f"Monroe: Set unit_price to ${result['unit_price']}")
                            result["debug_info"].append(f"Set unit_price to ${result['unit_price']}")
                        except ValueError as e:
                            logging.error(f"Monroe: Failed to convert price '{prices[0]}': {e}")
                            result["debug_info"].append(f"ERROR converting price: {e}")

                # Click product for detailed info
                logging.info(f"Monroe: Clicking product link for details")
                result["debug_info"].append("Clicking product link for details")

                try:
                    # Try to handle potential popup/new tab
                    with page.context.expect_page(timeout=5000) as new_page_info:
                        product_link.click()
                    new_page = new_page_info.value
                    page = new_page
                    logging.info(f"Monroe: New page opened")
                    result["debug_info"].append("New page opened")
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(2)
                except Exception as e:
                    # If no new page, continue on current page
                    logging.info(f"Monroe: No new page, continuing on current: {e}")
                    result["debug_info"].append(f"No new page opened, continuing on current")
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(2)

                # Screenshot product detail page
                page.screenshot(path=f"{screenshot_dir}/{timestamp}_04_product_details_{product_name.replace('/', '_')}.png")
                result["debug_info"].append(f"Screenshot saved: 04_product_details")

                # Try to extract price from detail page if not found yet
                if not result["unit_price"]:
                    logging.info(f"Monroe: No price found on search page, looking on detail page")
                    result["debug_info"].append("No price on search page, checking detail page")

                    detail_body = page.query_selector('body')
                    if detail_body:
                        detail_text = detail_body.text_content()
                        detail_prices = re.findall(r'\$(\d+\.?\d*)', detail_text)
                        logging.info(f"Monroe: Found {len(detail_prices)} prices on detail page: {detail_prices}")
                        result["debug_info"].append(f"Found {len(detail_prices)} prices on detail page: {detail_prices}")

                        if detail_prices:
                            try:
                                result["unit_price"] = float(detail_prices[0])
                                logging.info(f"Monroe: Set unit_price to ${result['unit_price']} from detail page")
                                result["debug_info"].append(f"Set unit_price to ${result['unit_price']} from detail page")
                            except ValueError as e:
                                logging.error(f"Monroe: Failed to convert detail price '{detail_prices[0]}': {e}")
                                result["debug_info"].append(f"ERROR converting detail price: {e}")

                # Scroll to see specifications
                logging.info(f"Monroe: Scrolling to see specifications")
                result["debug_info"].append("Scrolling to bottom for specifications")
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)

                # Screenshot after scroll
                page.screenshot(path=f"{screenshot_dir}/{timestamp}_05_after_scroll_{product_name.replace('/', '_')}.png")
                result["debug_info"].append(f"Screenshot saved: 05_after_scroll")

                # Extract from specifications table
                logging.info(f"Monroe: Looking for specifications table")
                result["debug_info"].append("Looking for specifications table .plp-table")

                spec_rows = page.query_selector_all('.plp-table tbody tr')
                logging.info(f"Monroe: Found {len(spec_rows)} specification rows")
                result["debug_info"].append(f"Found {len(spec_rows)} specification rows")

                for idx, row in enumerate(spec_rows):
                    cells = row.query_selector_all('td')
                    if len(cells) >= 2:
                        label = cells[0].text_content().strip().lower()
                        value = cells[1].text_content().strip()
                        logging.info(f"Monroe: Spec row {idx}: label='{label}', value='{value}'")
                        result["debug_info"].append(f"Spec row {idx}: {label} = {value}")

                        if 'inventory' in label and result["inventory"] is None:
                            # First check if the value is explicitly "0" or starts with "0"
                            if value.strip() == '0' or value.strip().startswith('0 '):
                                result["inventory"] = 0
                                logging.info(f"Monroe: Set inventory to 0 (explicit zero)")
                                result["debug_info"].append(f"Set inventory to 0 (explicit zero)")
                            # Only extract number if it's NOT part of a phone number pattern
                            # Avoid matching (877) or other phone-like patterns
                            elif not re.search(r'\(\d{3}\)', value) and not re.search(r'call|phone|email', value.lower()):
                                inv_match = re.search(r'\d+', value)
                                if inv_match:
                                    result["inventory"] = int(inv_match.group())
                                    logging.info(f"Monroe: Set inventory to {result['inventory']}")
                                    result["debug_info"].append(f"Set inventory to {result['inventory']}")
                            else:
                                # Value contains phone number or contact info, treat as zero inventory
                                result["inventory"] = 0
                                logging.info(f"Monroe: Set inventory to 0 (contact info detected in value: {value})")
                                result["debug_info"].append(f"Set inventory to 0 (contact info detected)")

                        if 'minimum order' in label:
                            moq_match = re.search(r'\d+', value)
                            if moq_match:
                                result["minimum_order"] = int(moq_match.group())
                                logging.info(f"Monroe: Set minimum_order to {result['minimum_order']}")
                                result["debug_info"].append(f"Set minimum_order to {result['minimum_order']}")

                        if 'increment' in label and not result["purchase_increment"]:
                            inc_match = re.search(r'\d+', value)
                            if inc_match:
                                result["purchase_increment"] = int(inc_match.group())
                                logging.info(f"Monroe: Set purchase_increment to {result['purchase_increment']}")
                                result["debug_info"].append(f"Set purchase_increment to {result['purchase_increment']}")

                # Post-processing: If inventory is 0, clear the price
                # Zero inventory means the product is not actually available
                if result.get("inventory") == 0:
                    if result.get("unit_price") is not None:
                        logging.info(f"Monroe: Clearing unit_price because inventory is 0")
                        result["debug_info"].append("Clearing unit_price because inventory is 0")
                        result["unit_price"] = None
            else:
                error_msg = f"Product '{product_name}' not found in Monroe catalog"
                logging.warning(f"Monroe: {error_msg}")
                result["error"] = error_msg
                result["debug_info"].append(f"ERROR: {error_msg}")

                # Save error screenshot
                page.screenshot(path=f"{screenshot_dir}/{timestamp}_ERROR_product_not_found_{product_name.replace('/', '_')}.png")

            # Final summary
            logging.info(f"Monroe scrape complete for {product_name}: price=${result.get('unit_price')}, inventory={result.get('inventory')}, moq={result.get('minimum_order')}")
            result["debug_info"].append(f"Scrape complete - Price: ${result.get('unit_price')}, Inventory: {result.get('inventory')}, MOQ: {result.get('minimum_order')}")

            browser.close()

    except Exception as e:
        error_msg = f"Error scraping Monroe: {str(e)}"
        logging.exception(f"Monroe: {error_msg}")
        result["error"] = error_msg
        result["debug_info"].append(f"EXCEPTION: {error_msg}")

    # Log debug summary
    debug_summary = "\n".join(result["debug_info"])
    logging.info(f"Monroe scrape debug summary for {product_name}:\n{debug_summary}")

    return result


def _get_lines_with_monroe_history(cur, list_id):
    """
    Get parts list lines that have historical purchases/quotes from Monroe.
    Returns list of line IDs that should be checked on Monroe.
    """
    monroe_supplier_id = _get_monroe_supplier_id(cur)
    if not monroe_supplier_id:
        return []

    # Check VQ history with Monroe
    cur.execute("""
        SELECT DISTINCT pll.id, pll.base_part_number
        FROM parts_list_lines pll
        JOIN vq_lines vl ON vl.base_part_number = pll.base_part_number
        JOIN vqs v ON v.id = vl.vq_id
        WHERE pll.parts_list_id = ?
          AND v.supplier_id = ?
          AND pll.chosen_cost IS NULL
    """, (list_id, monroe_supplier_id))
    vq_lines = {row['id'] for row in cur.fetchall()}

    # Check ILS history with Monroe
    cur.execute("""
        SELECT DISTINCT pll.id
        FROM parts_list_lines pll
        JOIN ils_search_results ils ON ils.base_part_number = pll.base_part_number
        WHERE pll.parts_list_id = ?
          AND ils.supplier_id = ?
          AND pll.chosen_cost IS NULL
    """, (list_id, monroe_supplier_id))
    ils_lines = {row['id'] for row in cur.fetchall()}

    # Check previous Monroe search results
    cur.execute("""
        SELECT DISTINCT pll.id
        FROM parts_list_lines pll
        JOIN monroe_search_results msr ON msr.base_part_number = pll.base_part_number
        WHERE pll.parts_list_id = ?
          AND msr.unit_price IS NOT NULL
          AND pll.chosen_cost IS NULL
    """, (list_id,))
    monroe_lines = {row['id'] for row in cur.fetchall()}

    return list(vq_lines | ils_lines | monroe_lines)


@parts_list_ai_bp.route('/api/monroe-settings', methods=['GET', 'POST'])
def monroe_settings():
    """Get or set Monroe supplier ID and per-user settings."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        user_id = current_user.id if getattr(current_user, 'is_authenticated', False) else session.get('user_id')

        if request.method == 'POST':
            data = request.get_json() or {}
            supplier_id = data.get('supplier_id')
            auto_search_new_parts = data.get('auto_search_new_parts')
            auto_create_supplier_offer = data.get('auto_create_supplier_offer')

            # Global setting: Monroe supplier ID
            if supplier_id:
                _set_monroe_supplier_id(cur, supplier_id)

            # User-level settings
            if user_id and (auto_search_new_parts is not None or auto_create_supplier_offer is not None):
                _set_user_monroe_settings(
                    cur,
                    user_id,
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

        # GET - return current settings and list of suppliers for selection
        monroe_id = _get_monroe_supplier_id(cur)

        # Get user-specific settings if user is logged in
        user_settings = {}
        if user_id:
            user_settings = _get_user_monroe_settings(cur, user_id)

        cur.execute("SELECT id, name FROM suppliers ORDER BY name")
        suppliers = [dict(row) for row in cur.fetchall()]
        conn.close()

        return jsonify(
            success=True,
            monroe_supplier_id=monroe_id,
            auto_search_new_parts=user_settings.get('auto_search_new_parts', False),
            auto_create_supplier_offer=user_settings.get('auto_create_supplier_offer', False),
            suppliers=suppliers
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_ai_bp.route('/api/monroe-suggest/<int:list_id>', methods=['GET'])
def monroe_suggest_lines(list_id):
    """
    Suggest which lines should be checked on Monroe based on history.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Ensure tables exist
        _ensure_monroe_tables(cur)
        conn.commit()

        monroe_supplier_id = _get_monroe_supplier_id(cur)

        # Get all uncosted lines
        cur.execute("""
            SELECT
                pll.id,
                pll.line_number,
                pll.customer_part_number,
                pll.base_part_number,
                pll.quantity
            FROM parts_list_lines pll
            WHERE pll.parts_list_id = ?
              AND pll.chosen_cost IS NULL
        """, (list_id,))
        uncosted_lines = [dict(row) for row in cur.fetchall()]

        suggested_lines = []
        other_lines = []

        if monroe_supplier_id:
            # Get lines with Monroe history
            history_line_ids = set(_get_lines_with_monroe_history(cur, list_id))

            for line in uncosted_lines:
                line_info = {
                    'id': line['id'],
                    'line_number': line['line_number'],
                    'part_number': line['customer_part_number'] or line['base_part_number'],
                    'base_part_number': line['base_part_number'],
                    'quantity': line['quantity'],
                    'has_monroe_history': line['id'] in history_line_ids
                }
                if line['id'] in history_line_ids:
                    suggested_lines.append(line_info)
                else:
                    other_lines.append(line_info)
        else:
            # No Monroe supplier configured - show all uncosted lines
            for line in uncosted_lines:
                other_lines.append({
                    'id': line['id'],
                    'line_number': line['line_number'],
                    'part_number': line['customer_part_number'] or line['base_part_number'],
                    'base_part_number': line['base_part_number'],
                    'quantity': line['quantity'],
                    'has_monroe_history': False
                })

        conn.close()

        return jsonify(
            success=True,
            monroe_supplier_id=monroe_supplier_id,
            suggested_lines=suggested_lines,
            other_lines=other_lines,
            playwright_available=PLAYWRIGHT_AVAILABLE
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_ai_bp.route('/api/monroe-check/<int:list_id>', methods=['POST'])
def monroe_check(list_id):
    """
    Check Monroe for specified lines. Scrapes the Monroe website.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return jsonify(
            success=False,
            message="Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ), 400

    try:
        data = request.get_json() or {}
        line_ids = data.get('line_ids', [])
        headless = data.get('headless', True)

        if not line_ids:
            return jsonify(success=False, message="No lines specified"), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # Ensure tables exist
        _ensure_monroe_tables(cur)

        # Get line details
        placeholders = ",".join("?" for _ in line_ids)
        cur.execute(f"""
            SELECT id, customer_part_number, base_part_number, quantity
            FROM parts_list_lines
            WHERE parts_list_id = ? AND id IN ({placeholders})
        """, (list_id, *line_ids))
        lines = [dict(row) for row in cur.fetchall()]

        results = []
        for line in lines:
            part_number = line['customer_part_number'] or line['base_part_number']
            if not part_number:
                continue

            # Scrape Monroe
            scrape_result = _scrape_monroe(part_number, headless=headless)

            # Only store inventory/MOQ if we got a valid price
            # (avoids storing bogus numbers like phone numbers)
            has_price = scrape_result.get('unit_price') is not None
            inventory = scrape_result.get('inventory') if has_price else None
            minimum_order = scrape_result.get('minimum_order') if has_price else None
            purchase_increment = scrape_result.get('purchase_increment') if has_price else None

            # Store result
            cur.execute("""
                INSERT INTO monroe_search_results
                (parts_list_id, parts_list_line_id, base_part_number, searched_part_number,
                 unit_price, inventory, minimum_order, purchase_increment, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                list_id,
                line['id'],
                line['base_part_number'],
                part_number,
                scrape_result.get('unit_price'),
                inventory,
                minimum_order,
                purchase_increment,
                scrape_result.get('error')
            ))

            results.append({
                'line_id': line['id'],
                'part_number': part_number,
                'quantity': line['quantity'],
                'unit_price': scrape_result.get('unit_price'),
                'inventory': inventory,
                'minimum_order': minimum_order,
                'purchase_increment': purchase_increment,
                'error': scrape_result.get('error')
            })

        conn.commit()
        conn.close()

        return jsonify(success=True, results=results)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_ai_bp.route('/api/monroe-results/<int:list_id>', methods=['GET'])
def monroe_results(list_id):
    """
    Get recent Monroe search results for a parts list.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Ensure tables exist
        _ensure_monroe_tables(cur)
        conn.commit()

        cur.execute("""
            SELECT
                msr.id,
                msr.parts_list_line_id,
                pll.line_number,
                pll.customer_part_number,
                pll.quantity,
                msr.searched_part_number,
                msr.unit_price,
                msr.inventory,
                msr.minimum_order,
                msr.purchase_increment,
                msr.currency_code,
                msr.search_date,
                msr.error_message
            FROM monroe_search_results msr
            JOIN parts_list_lines pll ON pll.id = msr.parts_list_line_id
            WHERE msr.parts_list_id = ?
            ORDER BY msr.search_date DESC
        """, (list_id,))
        results = [dict(row) for row in cur.fetchall()]

        conn.close()

        return jsonify(success=True, results=results)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@parts_list_ai_bp.route('/api/monroe-load-as-offer/<int:list_id>', methods=['POST'])
def monroe_load_as_offer(list_id):
    """
    Load Monroe results as a supplier quote/offer.
    Creates a supplier quote with the Monroe results.
    """
    try:
        data = request.get_json() or {}
        result_ids = data.get('result_ids', [])

        if not result_ids:
            return jsonify(success=False, message="No results specified"), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # Get Monroe supplier ID
        monroe_supplier_id = _get_monroe_supplier_id(cur)
        if not monroe_supplier_id:
            conn.close()
            return jsonify(
                success=False,
                message="Monroe supplier not configured. Please set the Monroe supplier in settings."
            ), 400

        # Get USD currency ID
        cur.execute("SELECT id FROM currencies WHERE currency_code = 'USD'")
        currency_row = cur.fetchone()
        if not currency_row:
            # Try to create USD currency
            cur.execute("INSERT INTO currencies (currency_code) VALUES ('USD') RETURNING id")
            currency_row = cur.fetchone()
        usd_currency_id = currency_row['id'] if currency_row else 1

        # Get user ID from session
        user_id = session.get('user_id', 1)

        # Get Monroe results
        results = _get_monroe_offer_results(cur, result_ids)
        valid_results, skipped_results = _filter_monroe_offer_results(results, "manual-offer")

        if not valid_results:
            conn.close()
            return jsonify(
                success=False,
                message="No supplier offer was created because every selected Monroe result has available quantity below its MOQ."
            ), 400

        # Create supplier quote header
        if _using_postgres():
            cur.execute("""
                INSERT INTO parts_list_supplier_quotes
                (parts_list_id, supplier_id, quote_reference, quote_date, currency_id, notes, created_by_user_id)
                VALUES (?, ?, ?, CURRENT_DATE, ?, ?, ?)
                RETURNING id
            """, (list_id, monroe_supplier_id, 'Monroe Web Scrape', usd_currency_id,
                  'Auto-imported from Monroe Aerospace website', user_id))
            quote_row = cur.fetchone()
            quote_id = quote_row['id']
        else:
            cur.execute("""
                INSERT INTO parts_list_supplier_quotes
                (parts_list_id, supplier_id, quote_reference, quote_date, currency_id, notes, created_by_user_id)
                VALUES (?, ?, ?, DATE('now'), ?, ?, ?)
            """, (list_id, monroe_supplier_id, 'Monroe Web Scrape', usd_currency_id,
                  'Auto-imported from Monroe Aerospace website', user_id))
            quote_id = cur.lastrowid

        # Create quote lines
        lines_created = 0
        for result in valid_results:
            requested_qty = result.get('requested_quantity') or 1
            moq = result.get('minimum_order') or 1
            quantity_quoted = max(requested_qty, moq)
            if quantity_quoted < 1:
                quantity_quoted = 1
            part_value = result.get('searched_part_number') or result.get('base_part_number')
            base_part_number = create_base_part_number(part_value) if part_value else result.get('base_part_number')
            if base_part_number:
                _ensure_part_number(cur, base_part_number, part_value)
            cur.execute("""
                INSERT INTO parts_list_supplier_quote_lines
                (supplier_quote_id, parts_list_line_id, quoted_part_number,
                 quantity_quoted, unit_price, condition_code, is_no_bid,
                 qty_available, purchase_increment, moq)
                VALUES (?, ?, ?, ?, ?, 'NE', FALSE, ?, ?, ?)
            """, (
                quote_id,
                result['parts_list_line_id'],
                result['searched_part_number'],
                quantity_quoted,
                result['unit_price'],
                result.get('inventory'),
                result.get('purchase_increment'),
                result.get('minimum_order')
            ))
            lines_created += 1

        conn.commit()
        conn.close()

        return jsonify(
            success=True,
            quote_id=quote_id,
            lines_created=lines_created,
            skipped_below_moq=len(skipped_results),
            message=f"Created supplier quote with {lines_created} lines"
                    + (f" ({len(skipped_results)} skipped below MOQ)" if skipped_results else "")
        )

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500
