from flask import Blueprint, render_template, request, jsonify, url_for, session
from models import get_db_connection
import logging
from openai import OpenAI
import json

# Initialize OpenAI client
client = OpenAI()

parts_list_ai_bp = Blueprint('parts_list_ai', __name__, url_prefix='/parts-list-ai')


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
                 WHERE parts_list_line_id = pll.id), 0) as suppliers_contacted
            FROM parts_list_lines pll
            LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
            LEFT JOIN currencies curr ON curr.id = pll.chosen_currency_id
            WHERE pll.parts_list_id = ?
            ORDER BY pll.line_number
        """, (list_id,))
        lines = cur.fetchall()

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

    # Calculate total value if costed
    total_cost = sum(
        (l['chosen_cost'] or 0) * (l['chosen_qty'] or l['quantity'])
        for l in lines
        if l['chosen_cost']
    )

    # Build line summaries
    line_summaries = []
    for line in lines:
        sourcing_status = "NEEDS SOURCING"
        best_price = None

        # Safely handle None values
        stock_qty = line['stock_qty'] or 0
        vq_count = line['vq_count'] or 0
        quote_count = line['quote_count'] or 0
        contacted_count = line['contacted_count'] or 0
        ils_count = line['ils_count'] or 0

        if line['chosen_cost']:
            sourcing_status = f"COSTED - {line['chosen_supplier_name'] or 'Unknown'}"
            best_price = line['chosen_cost']
        elif stock_qty >= line['quantity']:
            sourcing_status = f"IN STOCK ({stock_qty} available)"
            best_price = line['stock_cost']
        elif quote_count > 0:
            sourcing_status = f"QUOTED ({quote_count} suppliers)"
            best_price = line['quote_price']
        elif vq_count > 0:
            sourcing_status = f"VQ AVAILABLE ({vq_count} quotes)"
            best_price = line['vq_price']
        elif contacted_count > 0:
            sourcing_status = f"PENDING ({contacted_count} suppliers contacted)"
        elif ils_count > 0:
            sourcing_status = f"ILS AVAILABLE ({ils_count} suppliers)"

        line_summaries.append({
            'line': line['line_number'],
            'part': line['customer_part_number'],
            'qty': line['quantity'],
            'status': sourcing_status,
            'best_price': best_price,
            'lead_days': line['chosen_lead_days'],
            'stock': stock_qty,
            'vq_count': vq_count,
            'quote_count': quote_count,
            'contacted': contacted_count
        })

    return {
        'list_name': header['name'],
        'customer': header['customer_name'] or 'No customer',
        'status': header['status_name'],
        'total_lines': total_lines,
        'costed_lines': costed_lines,
        'in_stock_lines': in_stock_lines,
        'quoted_lines': quoted_lines,
        'need_sourcing': need_sourcing,
        'total_cost': total_cost,
        'lines': line_summaries
    }


def _call_ai_for_analysis(context):
    """
    Call OpenAI to generate comprehensive analysis
    """
    prompt = f"""You are an aviation parts procurement analyst. Analyze this parts list and provide actionable insights.

PARTS LIST: {context['list_name']}
Customer: {context['customer']}
Status: {context['status']}

SUMMARY STATS:
- Total lines: {context['total_lines']}
- Fully costed: {context['costed_lines']}
- Available in stock: {context['in_stock_lines']}
- Supplier quotes received: {context['quoted_lines']}
- Need sourcing: {context['need_sourcing']}
- Total cost (costed items): ${context['total_cost']:,.2f}

LINE DETAILS:
{json.dumps(context['lines'], indent=2, default=str)}

Provide analysis in the following sections:

1. EXECUTIVE SUMMARY (2-3 sentences)
   - Overall readiness to quote customer
   - Key risks or concerns
   - Estimated timeline to complete

2. SOURCING BREAKDOWN
   - Group parts by sourcing strategy (stock/VQ/supplier quotes/need sourcing)
   - Highlight any parts with multiple good options
   - Flag parts with only one source or no sources

3. COST OPTIMIZATION OPPORTUNITIES
   - Parts where stock is available but more expensive than VQ
   - Parts where we should negotiate better pricing
   - Parts where alternative suppliers might help

4. TIMELINE & LEAD TIME ANALYSIS
   - Identify longest lead time parts
   - Flag any potential delivery bottlenecks
   - Suggest expediting strategies if needed

5. NEXT ACTIONS (prioritized list)
   - What needs to be done immediately
   - Which suppliers to contact
   - Any pricing negotiations needed

Keep it concise and actionable. Use bullet points. Focus on decisions the user needs to make.
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
        max_tokens=2000,
        temperature=0.3
    )

    return response.choices[0].message.content


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

            if not line:
                continue

            # Get historical data
            cur.execute("""
                SELECT DISTINCT
                    s.id,
                    s.name,
                    COUNT(*) as quote_count,
                    AVG(vl.vendor_price) as avg_price,
                    AVG(vl.lead_days) as avg_lead_days
                FROM vq_lines vl
                JOIN vqs v ON vl.vq_id = v.id
                LEFT JOIN suppliers s ON v.supplier_id = s.id
                WHERE vl.base_part_number = ?
                  AND s.id IS NOT NULL
                  AND vl.vendor_price > 0
                GROUP BY s.id
                ORDER BY avg_price ASC
                LIMIT 5
            """, (line['base_part_number'],))
            vq_suppliers = cur.fetchall()

            cur.execute("""
                SELECT DISTINCT
                    s.id,
                    s.name,
                    COUNT(*) as ils_results
                FROM ils_search_results ils
                JOIN suppliers s ON ils.supplier_id = s.id
                WHERE ils.base_part_number = ?
                GROUP BY s.id
                ORDER BY ils_results DESC
                LIMIT 5
            """, (line['base_part_number'],))
            ils_suppliers = cur.fetchall()

            # Combine and rank
            supplier_scores = {}

            for idx, vq in enumerate(vq_suppliers):
                score = 10 - idx  # Lower index = better score
                if vq['id'] not in supplier_scores:
                    supplier_scores[vq['id']] = {
                        'supplier_id': vq['id'],
                        'supplier_name': vq['name'],
                        'score': 0,
                        'reasons': []
                    }
                supplier_scores[vq['id']]['score'] += score
                supplier_scores[vq['id']]['reasons'].append(
                    f"{vq['quote_count']} past quotes, avg ${vq['avg_price']:.2f}"
                )

            for idx, ils in enumerate(ils_suppliers):
                score = 5 - idx
                if ils['id'] not in supplier_scores:
                    supplier_scores[ils['id']] = {
                        'supplier_id': ils['id'],
                        'supplier_name': ils['name'],
                        'score': 0,
                        'reasons': []
                    }
                supplier_scores[ils['id']]['score'] += score
                supplier_scores[ils['id']]['reasons'].append(
                    f"{ils['ils_results']} ILS results"
                )

            # Sort by score
            ranked = sorted(supplier_scores.values(), key=lambda x: x['score'], reverse=True)[:3]

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
