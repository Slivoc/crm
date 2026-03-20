import os
import json
from datetime import datetime, timedelta, date
from decimal import Decimal
from math import ceil
from flask import Blueprint, render_template, request, jsonify, session
from db import db_cursor, execute as db_execute
from models import convert_currency

purchase_suggestions_bp = Blueprint('purchase_suggestions', __name__, url_prefix='/purchase-suggestions')


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _stringify_date(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    return str(value)


def _coerce_numeric(value):
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _execute_with_cursor(cur, query, params=None, fetch=None):
    cur.execute(_prepare_query(query), params or [])
    if fetch == 'one':
        return cur.fetchone()
    if fetch == 'all':
        return cur.fetchall()
    return cur


def convert_vq_price_to_gbp(price, currency_code):
    """
    Convert VQ price to GBP for comparison with sales prices

    Args:
        price: The price to convert
        currency_code: The currency code (e.g., 'USD', 'EUR', 'GBP')

    Returns:
        float: Price converted to GBP, or original price if conversion fails
    """
    if not price or not currency_code:
        return price

    # If already in GBP, return as-is
    if currency_code == 'GBP':
        return price

    try:
        return convert_currency(price, currency_code, 'GBP')
    except Exception as e:
        print(f"Warning: Could not convert {price} {currency_code} to GBP: {e}")
        return price  # Return original price if conversion fails


def _safe_float(value):
    try:
        if value is None or value == '':
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_recent_date_filter(column_name, days):
    if _using_postgres():
        return f"{column_name} >= CURRENT_DATE - INTERVAL '{int(days)} days'"
    return f"{column_name} >= date('now', '-{int(days)} days')"


def _parse_iso_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _load_speculative_buy_report(cursor, lookback_months=2):
    recent_sales_filter = _get_recent_date_filter('so.date_entered', 365)
    lookback_months = max(1, min(int(lookback_months or 2), 24))
    lookback_cutoff = datetime.utcnow().date() - timedelta(days=lookback_months * 30)

    quote_rows = _execute_with_cursor(
        cursor,
        '''
        SELECT
            psql.id AS quote_line_id,
            psq.id AS supplier_quote_id,
            pll.base_part_number,
            COALESCE(pn.part_number, pll.base_part_number) AS part_number,
            pn.system_part_number,
            psql.quoted_part_number,
            psql.manufacturer,
            psql.quantity_quoted,
            psql.unit_price,
            psql.lead_time_days,
            psql.condition_code,
            psq.quote_reference,
            psq.quote_date,
            psq.parts_list_id,
            pl.name AS parts_list_name,
            s.id AS supplier_id,
            s.name AS supplier_name,
            c.name AS customer_name,
            curr.currency_code,
            curr.symbol AS currency_symbol
        FROM parts_list_supplier_quote_lines psql
        JOIN parts_list_supplier_quotes psq ON psq.id = psql.supplier_quote_id
        JOIN parts_list_lines pll ON pll.id = psql.parts_list_line_id
        LEFT JOIN part_numbers pn ON pn.base_part_number = pll.base_part_number
        LEFT JOIN parts_lists pl ON pl.id = psq.parts_list_id
        LEFT JOIN customers c ON c.id = pl.customer_id
        LEFT JOIN suppliers s ON s.id = psq.supplier_id
        LEFT JOIN currencies curr ON curr.id = psq.currency_id
        WHERE COALESCE(psql.is_no_bid, FALSE) = FALSE
          AND psql.unit_price IS NOT NULL
          AND psql.unit_price > 0
          AND pll.base_part_number IS NOT NULL
          AND TRIM(pll.base_part_number) <> ''
        ORDER BY psq.quote_date DESC, psql.id DESC
        ''',
        fetch='all'
    ) or []

    sales_rows = _execute_with_cursor(
        cursor,
        f'''
        SELECT
            sol.base_part_number,
            COUNT(*) AS sales_order_count,
            COALESCE(SUM(sol.quantity), 0) AS total_sales_qty,
            MAX(so.date_entered) AS last_sale_date,
            AVG(CASE WHEN sol.price > 0 THEN sol.price END) AS avg_sale_price,
            MAX(CASE WHEN sol.price > 0 THEN sol.price END) AS max_sale_price,
            SUM(CASE WHEN {recent_sales_filter} THEN COALESCE(sol.quantity, 0) ELSE 0 END) AS recent_sales_qty,
            SUM(CASE WHEN {recent_sales_filter} THEN 1 ELSE 0 END) AS recent_sales_orders
        FROM sales_order_lines sol
        JOIN sales_orders so ON so.id = sol.sales_order_id
        WHERE sol.base_part_number IS NOT NULL
          AND TRIM(sol.base_part_number) <> ''
        GROUP BY sol.base_part_number
        ''',
        fetch='all'
    ) or []

    customer_quote_rows = _execute_with_cursor(
        cursor,
        '''
        SELECT
            pll.base_part_number,
            COUNT(*) AS customer_quote_count,
            AVG(cql.quote_price_gbp) AS avg_customer_quote_price,
            MAX(cql.quote_price_gbp) AS max_customer_quote_price,
            MAX(cql.date_created) AS last_customer_quote_date
        FROM customer_quote_lines cql
        JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
        WHERE cql.quote_price_gbp IS NOT NULL
          AND cql.quote_price_gbp > 0
          AND COALESCE(cql.is_no_bid, 0) = 0
          AND pll.base_part_number IS NOT NULL
          AND TRIM(pll.base_part_number) <> ''
        GROUP BY pll.base_part_number
        ''',
        fetch='all'
    ) or []

    stock_rows = _execute_with_cursor(
        cursor,
        '''
        SELECT
            base_part_number,
            COALESCE(SUM(available_quantity), 0) AS stock_quantity
        FROM stock_movements
        WHERE movement_type = 'IN'
          AND available_quantity > 0
          AND base_part_number IS NOT NULL
          AND TRIM(base_part_number) <> ''
        GROUP BY base_part_number
        ''',
        fetch='all'
    ) or []

    quotes_by_part = {}
    normalized_quotes = []

    for row in quote_rows:
        quote = dict(row)
        raw_price = _safe_float(quote.get('unit_price'))
        currency_code = quote.get('currency_code') or 'GBP'
        price_gbp = convert_vq_price_to_gbp(raw_price, currency_code) if raw_price is not None else None
        quote['unit_price'] = raw_price
        quote['unit_price_gbp'] = _safe_float(price_gbp)
        quote['quote_date'] = _stringify_date(quote.get('quote_date'))
        normalized_quotes.append(quote)
        quotes_by_part.setdefault(quote['base_part_number'], []).append(quote)

    sales_map = {}
    for row in sales_rows:
        sales_map[row['base_part_number']] = {
            'sales_order_count': int(row['sales_order_count'] or 0),
            'total_sales_qty': _safe_float(row['total_sales_qty']) or 0.0,
            'last_sale_date': _stringify_date(row.get('last_sale_date')),
            'avg_sale_price': _safe_float(row.get('avg_sale_price')),
            'max_sale_price': _safe_float(row.get('max_sale_price')),
            'recent_sales_qty': _safe_float(row.get('recent_sales_qty')) or 0.0,
            'recent_sales_orders': int(row['recent_sales_orders'] or 0),
        }

    customer_quote_map = {}
    for row in customer_quote_rows:
        customer_quote_map[row['base_part_number']] = {
            'customer_quote_count': int(row['customer_quote_count'] or 0),
            'avg_customer_quote_price': _safe_float(row.get('avg_customer_quote_price')),
            'max_customer_quote_price': _safe_float(row.get('max_customer_quote_price')),
            'last_customer_quote_date': _stringify_date(row.get('last_customer_quote_date')),
        }

    stock_map = {
        row['base_part_number']: _safe_float(row.get('stock_quantity')) or 0.0
        for row in stock_rows
    }

    best_opportunity_by_part = {}
    reason_counts = {
        'sales_orders': 0,
        'customer_quotes': 0,
    }

    for quote in normalized_quotes:
        current_price = quote.get('unit_price_gbp')
        if current_price is None or current_price <= 0:
            continue
        quote_date = _parse_iso_date(quote.get('quote_date'))
        if quote_date is None or quote_date < lookback_cutoff:
            continue

        base_part_number = quote['base_part_number']
        part_quotes = quotes_by_part.get(base_part_number, [])
        other_prices = [
            q['unit_price_gbp']
            for q in part_quotes
            if q['quote_line_id'] != quote['quote_line_id'] and q.get('unit_price_gbp')
        ]

        purchase_quote_count = len(other_prices)
        avg_purchase_price = sum(other_prices) / purchase_quote_count if purchase_quote_count else None
        best_purchase_price = min(other_prices) if other_prices else None
        sales_stats = sales_map.get(base_part_number, {})
        customer_quote_stats = customer_quote_map.get(base_part_number, {})

        reasons = []
        opportunity_score = 0.0

        avg_sale_price = sales_stats.get('avg_sale_price')
        sales_order_count = sales_stats.get('sales_order_count', 0)
        if avg_sale_price and sales_order_count >= 3:
            discount_to_sale_pct = ((avg_sale_price - current_price) / avg_sale_price) * 100
            if discount_to_sale_pct >= 35:
                reasons.append({
                    'source': 'sales_orders',
                    'label': 'Sales orders',
                    'detail': (
                        f"{discount_to_sale_pct:.1f}% under average sell price: "
                        f"GBP {current_price:.2f} buy vs GBP {avg_sale_price:.2f} average "
                        f"across {sales_order_count} sales order lines"
                    ),
                })
                opportunity_score += discount_to_sale_pct * 1.1

        avg_customer_quote_price = customer_quote_stats.get('avg_customer_quote_price')
        customer_quote_count = customer_quote_stats.get('customer_quote_count', 0)
        if avg_customer_quote_price and customer_quote_count >= 3:
            discount_to_customer_quote_pct = (
                (avg_customer_quote_price - current_price) / avg_customer_quote_price
            ) * 100
            if discount_to_customer_quote_pct >= 35:
                reasons.append({
                    'source': 'customer_quotes',
                    'label': 'Customer quotes',
                    'detail': (
                        f"{discount_to_customer_quote_pct:.1f}% under quoted sell price: "
                        f"GBP {current_price:.2f} buy vs GBP {avg_customer_quote_price:.2f} "
                        f"average across {customer_quote_count} customer quotes"
                    ),
                })
                opportunity_score += discount_to_customer_quote_pct

        if not reasons:
            continue

        recent_sales_qty = sales_stats.get('recent_sales_qty', 0.0)
        recent_sales_orders = sales_stats.get('recent_sales_orders', 0)
        stock_quantity = stock_map.get(base_part_number, 0.0)

        demand_score = min(25.0, (recent_sales_qty * 2.0) + (recent_sales_orders * 1.5) + (customer_quote_count * 0.5))
        stock_penalty = min(15.0, stock_quantity * 0.5)
        opportunity_score += demand_score
        opportunity_score -= stock_penalty

        candidate = {
            'quote_line_id': quote['quote_line_id'],
            'supplier_quote_id': quote['supplier_quote_id'],
            'parts_list_id': quote.get('parts_list_id'),
            'parts_list_name': quote.get('parts_list_name'),
            'customer_name': quote.get('customer_name'),
            'base_part_number': base_part_number,
            'part_number': quote.get('part_number') or base_part_number,
            'system_part_number': quote.get('system_part_number'),
            'quoted_part_number': quote.get('quoted_part_number'),
            'manufacturer': quote.get('manufacturer'),
            'supplier_id': quote.get('supplier_id'),
            'supplier_name': quote.get('supplier_name'),
            'quote_reference': quote.get('quote_reference'),
            'quote_date': quote.get('quote_date'),
            'quantity_quoted': quote.get('quantity_quoted'),
            'lead_time_days': quote.get('lead_time_days'),
            'condition_code': quote.get('condition_code'),
            'currency_code': quote.get('currency_code') or 'GBP',
            'currency_symbol': quote.get('currency_symbol') or '£',
            'unit_price_original': current_price if (quote.get('currency_code') or 'GBP') == 'GBP' else quote.get('unit_price'),
            'unit_price_gbp': current_price,
            'stock_quantity': stock_quantity,
            'sales_order_count': sales_order_count,
            'total_sales_qty': sales_stats.get('total_sales_qty', 0.0),
            'recent_sales_qty': recent_sales_qty,
            'recent_sales_orders': recent_sales_orders,
            'last_sale_date': sales_stats.get('last_sale_date'),
            'avg_sale_price': avg_sale_price,
            'max_sale_price': sales_stats.get('max_sale_price'),
            'customer_quote_count': customer_quote_count,
            'avg_customer_quote_price': avg_customer_quote_price,
            'max_customer_quote_price': customer_quote_stats.get('max_customer_quote_price'),
            'other_purchase_quote_count': purchase_quote_count,
            'avg_purchase_price': avg_purchase_price,
            'best_purchase_price': best_purchase_price,
            'reason_count': len(reasons),
            'reasons': reasons,
            'opportunity_score': round(max(opportunity_score, 0), 1),
        }

        existing = best_opportunity_by_part.get(base_part_number)
        if existing is None:
            best_opportunity_by_part[base_part_number] = candidate
            continue

        candidate_quote_date = _parse_iso_date(candidate.get('quote_date'))
        existing_quote_date = _parse_iso_date(existing.get('quote_date'))
        candidate_key = (
            candidate.get('opportunity_score', 0),
            candidate.get('reason_count', 0),
            candidate.get('recent_sales_qty', 0),
            candidate_quote_date or date.min,
        )
        existing_key = (
            existing.get('opportunity_score', 0),
            existing.get('reason_count', 0),
            existing.get('recent_sales_qty', 0),
            existing_quote_date or date.min,
        )
        if candidate_key > existing_key:
            best_opportunity_by_part[base_part_number] = candidate

    opportunities = list(best_opportunity_by_part.values())

    for opportunity in opportunities:
        for reason in opportunity['reasons']:
            reason_counts[reason['source']] += 1

    opportunities.sort(
        key=lambda item: (
            item.get('opportunity_score', 0),
            item.get('recent_sales_qty', 0),
            item.get('reason_count', 0),
            item.get('quote_date') or '',
        ),
        reverse=True,
    )

    return {
        'generated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        'opportunities': opportunities,
        'summary': {
            'total_candidates': len(opportunities),
            'distinct_parts': len({item['base_part_number'] for item in opportunities}),
            'sales_order_matches': reason_counts['sales_orders'],
            'customer_quote_matches': reason_counts['customer_quotes'],
        },
        'thresholds': {
            'lookback_months': lookback_months,
            'lookback_cutoff': lookback_cutoff.strftime('%Y-%m-%d'),
            'sell_discount_pct': 35,
            'minimum_sales_or_customer_quotes': 3,
        }
    }


@purchase_suggestions_bp.route('/upload-stock', methods=['POST'])
def upload_stock():
    """Store uploaded stock data temporarily in session"""
    try:
        print("DEBUG: upload_stock route called")
        data = request.get_json()
        print(f"DEBUG: Received data keys: {data.keys() if data else 'None'}")

        stock_data = data.get('stock_data', [])
        mapping = data.get('mapping', {})

        print(f"DEBUG: Stock data rows: {len(stock_data)}")
        print(f"DEBUG: Mapping: {mapping}")

        # Process and store the mapped stock data
        processed_stock = {}

        for i, row in enumerate(stock_data):
            # Get the part number based on mapping
            part_col = mapping.get('part_number')
            qty_col = mapping.get('quantity')
            price_col = mapping.get('unit_price')  # Optional unit price column

            if part_col is not None and qty_col is not None:
                try:
                    part_number = row[int(part_col)]
                    quantity = row[int(qty_col)]

                    # Get unit price if provided (optional)
                    unit_price = None
                    if price_col is not None:
                        try:
                            unit_price = float(row[int(price_col)])
                        except (ValueError, TypeError, IndexError):
                            unit_price = None

                    if part_number and quantity:
                        # Store with part number as key
                        part_key = str(part_number).strip()
                        qty_value = float(quantity)

                        if part_key in processed_stock:
                            # If part already exists, sum quantities and average prices
                            existing_qty = processed_stock[part_key]['quantity']
                            existing_price = processed_stock[part_key].get('unit_price')

                            new_qty = existing_qty + qty_value

                            # Calculate weighted average price if both have prices
                            # Guard against division by zero
                            if new_qty > 0 and existing_price is not None and unit_price is not None:
                                new_price = ((existing_price * existing_qty) + (unit_price * qty_value)) / new_qty
                            elif unit_price is not None:
                                new_price = unit_price
                            else:
                                new_price = existing_price

                            processed_stock[part_key] = {
                                'quantity': new_qty,
                                'unit_price': new_price
                            }
                        else:
                            processed_stock[part_key] = {
                                'quantity': qty_value,
                                'unit_price': unit_price
                            }
                except (ValueError, TypeError, IndexError) as e:
                    if i < 5:  # Only print first 5 errors
                        print(f"DEBUG: Error processing row {i}: {e}")
                    continue

        print(f"DEBUG: Processed {len(processed_stock)} parts")

        # Store in session
        session['uploaded_stock'] = processed_stock
        session.modified = True

        print(f"DEBUG: Session updated with {len(session['uploaded_stock'])} parts")

        for part in parts:
            part['last_sale_date'] = _stringify_date(part.get('last_sale_date'))
        return jsonify({
            'success': True,
            'parts_loaded': len(processed_stock)
        })

    except Exception as e:
        print(f"ERROR in upload_stock: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@purchase_suggestions_bp.route('/clear-stock', methods=['POST'])
def clear_stock():
    """Clear uploaded stock data from session"""
    if 'uploaded_stock' in session:
        del session['uploaded_stock']
        session.modified = True
    return jsonify({'success': True})


@purchase_suggestions_bp.route('/debug/part')
def debug_part():
    """Debug endpoint to check if a specific part exists in uploaded stock"""
    part_to_check = request.args.get('part', '')

    if not part_to_check:
        return jsonify({'success': False, 'message': 'Please provide a part number via ?part=XXX'}), 400

    uploaded_stock = session.get('uploaded_stock', {})

    if not uploaded_stock:
        return jsonify({'success': False, 'message': 'No stock data uploaded'}), 400

    # Try various formats
    results = {
        'searched_for': part_to_check,
        'total_parts_in_stock': len(uploaded_stock),
        'exact_match': part_to_check in uploaded_stock,
        'exact_match_data': uploaded_stock.get(part_to_check, 'N/A'),
        'stripped_match': part_to_check.strip() in uploaded_stock,
        'stripped_match_data': uploaded_stock.get(part_to_check.strip(), 'N/A'),
        'similar_keys': []
    }

    # Find similar keys
    search_lower = part_to_check.lower().strip()
    for key in uploaded_stock.keys():
        if search_lower in key.lower() or key.lower() in search_lower:
            results['similar_keys'].append({
                'key': key,
                'data': uploaded_stock[key]
            })

    return jsonify({'success': True, 'debug_info': results})


@purchase_suggestions_bp.route('/', methods=['GET'])
def purchase_suggestions():
    """Main page for purchase suggestions - shows parts being sold with low stock based on usage"""
    try:
        view_by = request.args.get('view_by', 'part')
        search_query = request.args.get('search', '')
        page = request.args.get('page', 1, type=int)
        per_page = 50

        # Get sorting parameters
        sort_column = request.args.get('sort', 'purchase_priority_score')
        sort_direction = request.args.get('dir', 'desc')

        # Validate sort_direction to prevent SQL injection
        if sort_direction not in ['asc', 'desc']:
            sort_direction = 'desc'

        # Usage-based low stock parameters (configurable)
        TIME_PERIOD_DAYS = 365  # Rolling period for sales data (e.g., last year)
        BUFFER_MONTHS = 2  # Months of buffer stock to trigger "low stock"
        MIN_SALES_FOR_THRESHOLD = 1  # Minimum units sold in period to apply dynamic threshold (otherwise fallback to 1)

        data = []

        with db_cursor() as cursor:
            if view_by == 'part':
                data = _load_part_view(cursor, sort_column, sort_direction, TIME_PERIOD_DAYS, BUFFER_MONTHS,
                                       MIN_SALES_FOR_THRESHOLD)

            elif view_by == 'customer':
                # TODO: Implement customer view with same stock approach
                pass

            elif view_by == 'bom':
                # TODO: Implement BOM view with same stock approach
                pass

        # Apply search filter if provided
        if search_query:
            search_lower = search_query.lower()
            if view_by == 'part':
                data = [p for p in data if
                        search_lower in str(p.get('part_number', '')).lower() or
                        search_lower in str(p.get('system_part_number', '')).lower() or
                        search_lower in str(p.get('base_part_number', '')).lower()]
            elif view_by == 'customer':
                data = [c for c in data if search_lower in str(c.get('customer_name', '')).lower()]
            elif view_by == 'bom':
                data = [b for b in data if
                        search_lower in str(b.get('bom_name', '')).lower() or
                        search_lower in str(b.get('description', '')).lower()]

        # Pagination
        total_items = len(data)
        total_pages = ceil(total_items / per_page)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_data = data[start_idx:end_idx]

        return render_template('purchase_stock_suggestions.html',
                               data=paginated_data,
                               total_parts=total_items,
                               page=page,
                               total_pages=total_pages,
                               per_page=per_page,
                               search_query=search_query,
                               view_by=view_by,
                               sort_column=sort_column,
                               sort_direction=sort_direction)

    except Exception as e:
        print(f"Error in purchase_suggestions: {str(e)}")
        import traceback
        traceback.print_exc()
        return render_template('purchase_stock_suggestions.html',
                               data=[],
                               total_parts=0,
                               page=1,
                               total_pages=0,
                               per_page=per_page,
                               search_query='',
                               view_by='part',
                               sort_column='purchase_priority_score',
                               sort_direction='desc',
                               error=str(e))


@purchase_suggestions_bp.route('/api/speculative-buy-report', methods=['POST'])
def speculative_buy_report():
    """Manual report for supplier quotes that look unusually attractive for stock buys."""
    try:
        payload = request.get_json(silent=True) or {}
        lookback_months = payload.get('lookback_months', 2)
        with db_cursor() as cursor:
            report = _load_speculative_buy_report(cursor, lookback_months=lookback_months)
        return jsonify({
            'success': True,
            **report,
        })
    except Exception as e:
        print(f"Error running speculative buy report: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
        }), 500


def _load_part_view(cursor, sort_column, sort_direction, time_period_days, buffer_months, min_sales_threshold):
    if _using_postgres():
        recent_sales_filter = f"AND so.date_entered >= CURRENT_DATE - INTERVAL '{time_period_days} days'"
    else:
        recent_sales_filter = f"AND so.date_entered >= date('now', '-{time_period_days} days')"

    if _using_postgres():
        customer_names_expr = "STRING_AGG(DISTINCT c.name, ', ')"
        bom_names_expr = "STRING_AGG(DISTINCT bh.name, ', ')"
    else:
        customer_names_expr = "GROUP_CONCAT(DISTINCT c.name)"
        bom_names_expr = "GROUP_CONCAT(DISTINCT bh.name)"

    query = f'''
        SELECT 
            pn.base_part_number,
            pn.part_number,
            pn.system_part_number,
            COUNT(DISTINCT so.id) as order_count,
            COUNT(DISTINCT so.customer_id) as customer_count,
            MAX(so.date_entered) as last_sale_date,
            SUM(sol.quantity) as total_quantity_sold,
            AVG(sol.price) as avg_sale_price,
            MIN(sol.price) as min_sale_price,
            MAX(sol.price) as max_sale_price,
            {customer_names_expr} as customer_names,
            {bom_names_expr} as bom_names
        FROM part_numbers pn
        LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
        LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
        LEFT JOIN customers c ON so.customer_id = c.id
        LEFT JOIN bom_lines bl ON pn.base_part_number = bl.base_part_number
        LEFT JOIN bom_headers bh ON bl.bom_header_id = bh.id
        WHERE sol.id IS NOT NULL {recent_sales_filter}
        GROUP BY pn.base_part_number
    '''

    base_parts = [dict(row) for row in (_execute_with_cursor(cursor, query, fetch='all') or [])]
    for part in base_parts:
        part['last_sale_date'] = _stringify_date(part.get('last_sale_date'))
    months_in_period = time_period_days / 30.4375
    parts = []

    for part in base_parts:
        base_part_number = part['base_part_number']

        stock_data = _execute_with_cursor(
            cursor,
            '''
            SELECT SUM(available_quantity) as total_stock
            FROM stock_movements
            WHERE base_part_number = ?
              AND movement_type = 'IN'
              AND available_quantity > 0
            ''',
            (base_part_number,),
            fetch='one'
        )

        stock_qty = float(stock_data['total_stock']) if (stock_data and stock_data['total_stock']) else 0.0
        part['stock_quantity'] = stock_qty

        qty_sold = float(part.get('total_quantity_sold') or 0)
        if qty_sold >= min_sales_threshold:
            avg_monthly_sales = qty_sold / months_in_period
            dynamic_threshold = avg_monthly_sales * buffer_months
            part['avg_monthly_sales'] = round(avg_monthly_sales, 2)
            part['suggested_reorder_point'] = round(dynamic_threshold, 2)
            threshold = dynamic_threshold
        else:
            threshold = 1
            part['avg_monthly_sales'] = 0
            part['suggested_reorder_point'] = 1

        if stock_qty < threshold:
            part['low_stock_threshold'] = threshold

            vqs = get_multiple_vqs(cursor, part['base_part_number'], limit=3)
            part['vq_available'] = vqs[0] if vqs else None
            part['all_vqs'] = vqs

            recency_factor = 1.0
            if part.get('last_sale_date'):
                try:
                    last_sale_value = part['last_sale_date']
                    if isinstance(last_sale_value, datetime):
                        last_sale = last_sale_value
                    elif isinstance(last_sale_value, date):
                        last_sale = datetime.combine(last_sale_value, datetime.min.time())
                    else:
                        last_sale = datetime.strptime(str(last_sale_value), '%Y-%m-%d')
                    days_since = (datetime.now() - last_sale).days
                    recency_factor = max(0, 1 - (days_since / time_period_days))
                except ValueError:
                    pass

            avg_price = float(part.get('avg_sale_price') or 0)
            customer_count = float(part.get('customer_count') or 0)
            order_count = float(part.get('order_count') or 0)

            economic_demand = (qty_sold * avg_price * 0.2) / 1000
            customer_breadth = (customer_count * 10 * 0.4)
            order_freq = (order_count * 5 * 0.4)

            pps_raw = (economic_demand + customer_breadth + order_freq) / time_period_days * recency_factor
            part['purchase_priority_score'] = min(50, pps_raw * 50)

            parts.append(part)

    sort_key_map = {
        'part_number': lambda x: (x.get('part_number') or '').lower(),
        'system_part_number': lambda x: (x.get('system_part_number') or '').lower(),
        'order_count': lambda x: x.get('order_count') or 0,
        'customer_count': lambda x: x.get('customer_count') or 0,
        'total_quantity_sold': lambda x: x.get('total_quantity_sold') or 0,
        'avg_sale_price': lambda x: x.get('avg_sale_price') or 0,
        'last_sale_date': lambda x: x.get('last_sale_date') or '',
        'bom_names': lambda x: (x.get('bom_names') or '').lower(),
        'vq_available': lambda x: (
            0 if not x.get('vq_available') else
            x.get('vq_available', {}).get('vendor_price_gbp', 0)
        ),
        'purchase_priority_score': lambda x: x.get('purchase_priority_score', 0),
        'stock_quantity': lambda x: x.get('stock_quantity', 0),
        'avg_monthly_sales': lambda x: x.get('avg_monthly_sales', 0),
        'suggested_reorder_point': lambda x: x.get('suggested_reorder_point', 0)
    }

    if sort_column in sort_key_map:
        parts.sort(
            key=sort_key_map[sort_column],
            reverse=(sort_direction == 'desc')
        )
    else:
        parts.sort(
            key=lambda x: x.get('purchase_priority_score', 0),
            reverse=True
        )

    return parts


def get_vq_availability(cursor, base_part_number):
    """Get the best VQ availability for a part with currency conversion to GBP and currency symbol"""
    vq = _execute_with_cursor(
        cursor,
        '''
        SELECT 
            vl.*,
            v.vq_number,
            v.status,
            v.entry_date,
            v.expiration_date,
            s.name as supplier_name,
            c.currency_code,
            c.symbol as currency_symbol,
            vl.foreign_currency,
            vl.quoted_date
        FROM vq_lines vl
        JOIN vqs v ON vl.vq_id = v.id
        LEFT JOIN suppliers s ON v.supplier_id = s.id
        LEFT JOIN currencies c ON v.currency_id = c.id
        WHERE vl.base_part_number = ?
        AND (v.expiration_date IS NULL OR v.expiration_date >= date('now'))
        AND v.status != 'Cancelled'
        ORDER BY vl.vendor_price ASC
        LIMIT 1
        ''',
        (base_part_number,),
        fetch='one',
    )

    if not vq:
        return None

    vq_dict = dict(vq)
    vq_dict['entry_date'] = _stringify_date(vq_dict.get('entry_date'))
    vq_dict['expiration_date'] = _stringify_date(vq_dict.get('expiration_date'))

    # Convert vendor price to GBP for comparison with sales prices (which are in GBP)
    original_price = vq_dict.get('vendor_price')
    currency_code = vq_dict.get('currency_code', 'GBP')
    currency_symbol = vq_dict.get('currency_symbol', '£')

    if original_price and currency_code:
        vq_dict['vendor_price_gbp'] = convert_vq_price_to_gbp(original_price, currency_code)
        vq_dict['vendor_price_original'] = original_price
        vq_dict['vendor_price_currency'] = currency_code
        vq_dict['currency_symbol'] = currency_symbol
    else:
        vq_dict['vendor_price_gbp'] = original_price
        vq_dict['vendor_price_original'] = original_price
        vq_dict['vendor_price_currency'] = 'GBP'
        vq_dict['currency_symbol'] = '£'

    return vq_dict


@purchase_suggestions_bp.route('/api/part-details/<base_part_number>')
def get_part_details(base_part_number):
    """Get detailed information about a specific part for the modal"""
    try:
        with db_cursor() as cursor:
            part_info = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    pn.part_number,
                    pn.system_part_number,
                    pn.base_part_number,
                    pc.category_name
                FROM part_numbers pn
                LEFT JOIN part_categories pc ON pn.category_id = pc.category_id
                WHERE pn.base_part_number = ?
                LIMIT 1
                ''',
                (base_part_number,),
                fetch='one'
            )

            if not part_info:
                return jsonify({'error': 'Part not found'}), 404

            part_dict = dict(part_info)

            sales_history_rows = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    so.id as order_id,
                    so.date_entered,
                    c.name as customer_name,
                    sol.quantity,
                    sol.price,
                    (sol.quantity * sol.price) as line_total
                FROM sales_order_lines sol
                INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                LEFT JOIN customers c ON so.customer_id = c.id
                WHERE sol.base_part_number = ?
                ORDER BY so.date_entered DESC
                LIMIT 20
                ''',
                (base_part_number,),
                fetch='all'
            ) or []
            sales_history = []
            for row in sales_history_rows:
                sale = dict(row)
                sale['price'] = _coerce_numeric(sale.get('price'))
                sale['quantity'] = _coerce_numeric(sale.get('quantity'))
                sale['line_total'] = _coerce_numeric(sale.get('line_total'))
                sale['date_entered'] = _stringify_date(sale.get('date_entered'))
                sales_history.append(sale)

            boms = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    bh.id,
                    bh.name,
                    bh.description,
                    bl.quantity as bom_quantity,
                    bl.guide_price
                FROM bom_lines bl
                INNER JOIN bom_headers bh ON bl.bom_header_id = bh.id
                WHERE bl.base_part_number = ?
                ORDER BY bh.name
                ''',
                (base_part_number,),
                fetch='all'
            ) or []

            sales_summary_raw = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    COUNT(DISTINCT so.id) as total_orders,
                    COUNT(DISTINCT so.customer_id) as total_customers,
                    SUM(sol.quantity) as total_quantity_sold,
                    AVG(sol.price) as avg_price,
                    MIN(sol.price) as min_price,
                    MAX(sol.price) as max_price,
                    MAX(so.date_entered) as last_sale_date
                FROM sales_order_lines sol
                INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                WHERE sol.base_part_number = ?
                ''',
                (base_part_number,),
                fetch='one'
            )

            sales_summary = dict(sales_summary_raw) if sales_summary_raw else {}
            numeric_summary_keys = [
                'total_orders',
                'total_customers',
                'total_quantity_sold',
                'avg_price',
                'min_price',
                'max_price'
            ]
            for key in numeric_summary_keys:
                if key in sales_summary:
                    sales_summary[key] = _coerce_numeric(sales_summary[key])

            vqs = []
            vqs_raw = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    vl.id as vq_line_id,
                    v.id as vq_id,
                    v.vq_number,
                    s.name as supplier_name,
                    vl.quantity_quoted,
                    vl.vendor_price,
                    vl.lead_days,
                    v.entry_date,
                    v.expiration_date,
                    v.status,
                    c.currency_code,
                    c.symbol as currency_symbol,
                    vl.quoted_date
                FROM vq_lines vl
                INNER JOIN vqs v ON vl.vq_id = v.id
                LEFT JOIN suppliers s ON v.supplier_id = s.id
                LEFT JOIN currencies c ON v.currency_id = c.id
                WHERE vl.base_part_number = ?
                ORDER BY v.entry_date DESC
                LIMIT 10
                ''',
                (base_part_number,),
                fetch='all'
            ) or []

            for vq_raw in vqs_raw:
                vq_dict = dict(vq_raw)
                original_price = _coerce_numeric(vq_dict.get('vendor_price'))
                currency_code = vq_dict.get('currency_code', 'GBP')
                currency_symbol = vq_dict.get('currency_symbol', '£')

                vq_dict['entry_date'] = _stringify_date(vq_dict.get('entry_date'))
                vq_dict['expiration_date'] = _stringify_date(vq_dict.get('expiration_date'))

                if original_price and currency_code:
                    vq_dict['vendor_price_gbp'] = convert_vq_price_to_gbp(original_price, currency_code)
                    vq_dict['vendor_price_original'] = original_price
                    vq_dict['vendor_price_currency'] = currency_code
                    vq_dict['currency_symbol'] = currency_symbol
                else:
                    fallback_price = _coerce_numeric(vq_dict.get('vendor_price'))
                    vq_dict['vendor_price_gbp'] = fallback_price
                    vq_dict['vendor_price_original'] = fallback_price
                    vq_dict['vendor_price_currency'] = 'GBP'
                    vq_dict['currency_symbol'] = '£'

                vqs.append(vq_dict)

        return jsonify({
            'success': True,
            'part': part_dict,
            'sales_history': sales_history,
            'boms': [dict(row) for row in boms],
            'sales_summary': dict(sales_summary) if sales_summary else {},
            'vqs': vqs
        })

    except Exception as e:
        print(f"Error getting part details: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/out-of-stock')
def get_out_of_stock_parts():
    """API endpoint to get parts being sold but not in stock"""
    try:
        with db_cursor() as cursor:
            query = '''
                SELECT 
                    pn.base_part_number,
                    pn.part_number,
                    pn.system_part_number,
                    COUNT(DISTINCT so.id) as order_count,
                    COUNT(DISTINCT so.customer_id) as customer_count,
                    SUM(sol.quantity) as total_quantity_sold
                FROM part_numbers pn
                LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
                WHERE sol.id IS NOT NULL
                GROUP BY pn.base_part_number
            '''

        all_sold_parts = [dict(row) for row in (_execute_with_cursor(cursor, query, fetch='all') or [])]
        for part in all_sold_parts:
            part['last_sale_date'] = _stringify_date(part.get('last_sale_date'))

        # Filter to only parts NOT in stock using same method as parts list
        out_of_stock = []
        for part in all_sold_parts:
            base_part_number = part['base_part_number']

            # Check stock using same query as parts list analyzer
            stock_data = _execute_with_cursor(
                cursor,
                '''
                SELECT SUM(available_quantity) as total_stock
                FROM stock_movements
                WHERE base_part_number = ?
                  AND movement_type = 'IN'
                  AND available_quantity > 0
                ''',
                (base_part_number,),
                fetch='one'
            )

            stock_qty = stock_data['total_stock'] if (stock_data and stock_data['total_stock']) else 0

            if stock_qty == 0:
                part['stock_quantity'] = stock_qty
                out_of_stock.append(part)

        # Sort by order count desc
        out_of_stock.sort(key=lambda x: x.get('order_count', 0), reverse=True)

        return jsonify({
            'success': True,
            'parts': out_of_stock[:100]  # Limit to 100
        })

    except Exception as e:
        print(f"Error getting out of stock parts: {str(e)}")
        return jsonify({'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/customer-parts/<int:customer_id>')
def get_customer_parts(customer_id):
    """Get parts not in stock for a specific customer"""
    try:
        with db_cursor() as cursor:
            use_uploaded_stock = 'uploaded_stock' in session
            uploaded_stock = session.get('uploaded_stock', {})

            # Get customer info
            customer = _execute_with_cursor(
                cursor,
                'SELECT id, name FROM customers WHERE id = ?',
                (customer_id,),
                fetch='one'
            )
            if not customer:
                return jsonify({'error': 'Customer not found'}), 404

            parts = []
            if use_uploaded_stock:
                # Get all parts sold to this customer
                query = '''
                    SELECT 
                        pn.base_part_number,
                        pn.part_number,
                        pn.system_part_number,
                        COUNT(DISTINCT so.id) as order_count,
                        SUM(sol.quantity) as total_quantity,
                        AVG(sol.price) as avg_price,
                        MAX(so.date_entered) as last_sale_date
                    FROM sales_order_lines sol
                    INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                    INNER JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                    WHERE so.customer_id = ?
                    GROUP BY pn.base_part_number
                '''
                all_parts = [dict(row) for row in (_execute_with_cursor(cursor, query, (customer_id,), fetch='all') or [])]

                # Filter to only parts NOT in stock
                parts = []
                for part in all_parts:
                    part_identifiers = [part.get('part_number'), part.get('system_part_number'),
                                        part.get('base_part_number')]
                    in_stock = False
                    for identifier in part_identifiers:
                        if identifier and str(identifier).strip() in uploaded_stock:
                            stock_info = uploaded_stock[str(identifier).strip()]
                            if isinstance(stock_info, dict):
                                stock_qty = stock_info.get('quantity', 0)
                            else:
                                stock_qty = stock_info
                            if stock_qty > 0:
                                in_stock = True
                                break
                    if not in_stock:
                        # Add VQ info
                        vqs = get_multiple_vqs(cursor, part['base_part_number'], limit=3)
                        part['vq_available'] = vqs[0] if vqs else None
                        part['all_vqs'] = vqs
                        parts.append(part)
            else:
                query = '''
                    SELECT 
                        pn.base_part_number,
                        pn.part_number,
                        pn.system_part_number,
                        COUNT(DISTINCT so.id) as order_count,
                        SUM(sol.quantity) as total_quantity,
                        AVG(sol.price) as avg_price,
                        MAX(so.date_entered) as last_sale_date
                    FROM sales_order_lines sol
                    INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                    INNER JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                    LEFT JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number
                        AND sm.movement_type = 'IN'
                        AND sm.available_quantity > 0
                    WHERE so.customer_id = ?
                    AND sm.id IS NULL
                    GROUP BY pn.base_part_number
                    ORDER BY order_count DESC
                '''
                parts = [dict(row) for row in (_execute_with_cursor(cursor, query, (customer_id,), fetch='all') or [])]

                # Add VQ info
                for part in parts:
                    vqs = get_multiple_vqs(cursor, part['base_part_number'], limit=3)
                    part['vq_available'] = vqs[0] if vqs else None
                    part['all_vqs'] = vqs
                    part['last_sale_date'] = _stringify_date(part.get('last_sale_date'))

            for part in parts:
                part['last_sale_date'] = _stringify_date(part.get('last_sale_date'))

            return jsonify({
                'success': True,
                'customer': {'id': customer['id'], 'name': customer['name']},
                'parts': parts,
                'total_parts': len(parts)
            })

    except Exception as e:
        print(f"Error getting customer parts: {str(e)}")
        return jsonify({'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/bom-parts/<int:bom_id>')
def get_bom_parts(bom_id):
    """Get parts not in stock for a specific BOM with price difference calculations"""
    try:
        with db_cursor() as cursor:
            use_uploaded_stock = 'uploaded_stock' in session
            uploaded_stock = session.get('uploaded_stock', {})

            bom = _execute_with_cursor(
                cursor,
                'SELECT id, name, description FROM bom_headers WHERE id = ?',
                (bom_id,),
                fetch='one'
            )
            if not bom:
                return jsonify({'error': 'BOM not found'}), 404

            parts = []
            if use_uploaded_stock:
                query = '''
                    SELECT 
                        pn.base_part_number,
                        pn.part_number,
                        pn.system_part_number,
                        bl.quantity as bom_quantity,
                        bl.guide_price,
                        COUNT(DISTINCT so.id) as order_count,
                        SUM(sol.quantity) as total_sold,
                        MAX(so.date_entered) as last_sale_date
                    FROM bom_lines bl
                    INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                    LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                    LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
                    WHERE bl.bom_header_id = ?
                    GROUP BY pn.base_part_number
                '''
                all_parts = [dict(row) for row in (_execute_with_cursor(cursor, query, (bom_id,), fetch='all') or [])]

                for part in all_parts:
                    if part.get('order_count', 0) > 0:
                        part_identifiers = [part.get('part_number'), part.get('system_part_number'),
                                            part.get('base_part_number')]
                        in_stock = False
                        for identifier in part_identifiers:
                            if identifier and str(identifier).strip() in uploaded_stock:
                                stock_info = uploaded_stock[str(identifier).strip()]
                                stock_qty = stock_info.get('quantity', 0) if isinstance(stock_info, dict) else stock_info
                                if stock_qty > 0:
                                    in_stock = True
                                    break
                        if not in_stock:
                            vq_info = get_vq_availability(cursor, part['base_part_number'])
                            part['vq_available'] = vq_info
                            part['all_vqs'] = get_multiple_vqs(cursor, part['base_part_number'], limit=3)

                            if vq_info and part.get('guide_price'):
                                guide_price = part['guide_price']
                                vq_price_gbp = vq_info.get('vendor_price_gbp', 0)
                                if guide_price > 0 and vq_price_gbp > 0:
                                    part['price_difference_pct'] = ((guide_price - vq_price_gbp) / guide_price) * 100
                                else:
                                    part['price_difference_pct'] = None
                            else:
                                part['price_difference_pct'] = None

                            parts.append(part)
            else:
                query = '''
                    SELECT 
                        pn.base_part_number,
                        pn.part_number,
                        pn.system_part_number,
                        bl.quantity as bom_quantity,
                        bl.guide_price,
                        COUNT(DISTINCT so.id) as order_count,
                        SUM(sol.quantity) as total_sold,
                        MAX(so.date_entered) as last_sale_date
                    FROM bom_lines bl
                    INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                    LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                    LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
                    LEFT JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number
                        AND sm.movement_type = 'IN'
                        AND sm.available_quantity > 0
                    WHERE bl.bom_header_id = ?
                    AND sol.id IS NOT NULL
                    AND sm.id IS NULL
                    GROUP BY pn.base_part_number
                    ORDER BY order_count DESC
                '''
                parts = [dict(row) for row in (_execute_with_cursor(cursor, query, (bom_id,), fetch='all') or [])]

                for part in parts:
                    vq_info = get_vq_availability(cursor, part['base_part_number'])
                    part['vq_available'] = vq_info
                    part['all_vqs'] = get_multiple_vqs(cursor, part['base_part_number'], limit=3)

                    if vq_info and part.get('guide_price'):
                        guide_price = part['guide_price']
                        vq_price_gbp = vq_info.get('vendor_price_gbp', 0)
                        if guide_price > 0 and vq_price_gbp > 0:
                            part['price_difference_pct'] = ((guide_price - vq_price_gbp) / guide_price) * 100
                        else:
                            part['price_difference_pct'] = None
                    else:
                        part['price_difference_pct'] = None

        return jsonify({
            'success': True,
            'bom': {'id': bom['id'], 'name': bom['name'], 'description': bom['description']},
            'parts': parts,
            'total_parts': len(parts)
        })

    except Exception as e:
        print(f"Error getting BOM parts: {str(e)}")
        return jsonify({'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/debug-bom/<int:bom_id>')
def debug_bom(bom_id):
    """Debug route to verify BOM filtering logic"""
    use_uploaded_stock = 'uploaded_stock' in session
    uploaded_stock = session.get('uploaded_stock', {})

    with db_cursor() as cursor:
        total_row = _execute_with_cursor(
            cursor,
            'SELECT COUNT(*) as count FROM bom_lines WHERE bom_header_id = ?',
            (bom_id,),
            fetch='one'
        )
        total = total_row['count'] if total_row else 0

        if use_uploaded_stock:
            with_sales_query = '''
                SELECT COUNT(DISTINCT pn.base_part_number) as count
                FROM bom_lines bl
                INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
                WHERE bl.bom_header_id = ?
                AND sol.id IS NOT NULL
            '''
            with_sales_row = _execute_with_cursor(cursor, with_sales_query, (bom_id,), fetch='one')
            with_sales = with_sales_row['count'] if with_sales_row else 0

            parts_query = '''
                SELECT DISTINCT 
                    pn.base_part_number,
                    pn.part_number,
                    pn.system_part_number
                FROM bom_lines bl
                INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                WHERE bl.bom_header_id = ?
                AND sol.id IS NOT NULL
            '''
            parts_with_sales = _execute_with_cursor(cursor, parts_query, (bom_id,), fetch='all') or []

            in_stock_count = 0
            for part in parts_with_sales:
                part_identifiers = [part[1], part[2], part[0]]
                for identifier in part_identifiers:
                    if identifier and str(identifier).strip() in uploaded_stock:
                        stock_info = uploaded_stock[str(identifier).strip()]
                        stock_qty = stock_info.get('quantity', 0) if isinstance(stock_info, dict) else stock_info
                        if stock_qty > 0:
                            in_stock_count += 1
                            break
        else:
            with_sales_row = _execute_with_cursor(
                cursor,
                '''
                SELECT COUNT(DISTINCT pn.base_part_number) as count
                FROM bom_lines bl
                INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                INNER JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                WHERE bl.bom_header_id = ?
                ''',
                (bom_id,),
                fetch='one'
            )
            with_sales = with_sales_row['count'] if with_sales_row else 0

            in_stock_row = _execute_with_cursor(
                cursor,
                '''
                SELECT COUNT(DISTINCT bl.base_part_number) as count
                FROM bom_lines bl
                INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                INNER JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                INNER JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number
                    AND sm.movement_type = 'IN'
                    AND sm.available_quantity > 0
                WHERE bl.bom_header_id = ?
                ''',
                (bom_id,),
                fetch='one'
            )
            in_stock_count = in_stock_row['count'] if in_stock_row else 0

        not_in_stock = with_sales - in_stock_count

    return jsonify({
        'total_parts_in_bom': total,
        'parts_with_sales_history': with_sales,
        'parts_with_sales_and_in_stock': in_stock_count,
        'parts_with_sales_not_in_stock': not_in_stock,
        'using_uploaded_stock': use_uploaded_stock
    })

def get_multiple_vqs(cursor, base_part_number, limit=3):
    """Get multiple VQs for a part, ordered by price"""
    vqs_raw = _execute_with_cursor(
        cursor,
        '''
        SELECT 
            vl.*,
            v.vq_number,
            v.status,
            v.entry_date,
            v.expiration_date,
            s.name as supplier_name,
            c.currency_code,
            c.symbol as currency_symbol,
            vl.foreign_currency,
            vl.quoted_date
        FROM vq_lines vl
        JOIN vqs v ON vl.vq_id = v.id
        LEFT JOIN suppliers s ON v.supplier_id = s.id
        LEFT JOIN currencies c ON v.currency_id = c.id
        WHERE vl.base_part_number = ?
        AND (v.expiration_date IS NULL OR v.expiration_date >= date('now'))
        AND v.status != 'Cancelled'
        ORDER BY vl.vendor_price ASC
        LIMIT ?
        ''',
        (base_part_number, limit),
        fetch='all',
    )

    vqs = []
    for vq_raw in vqs_raw:
        vq_dict = dict(vq_raw)
        vq_dict['entry_date'] = _stringify_date(vq_dict.get('entry_date'))
        vq_dict['expiration_date'] = _stringify_date(vq_dict.get('expiration_date'))
        original_price = vq_dict.get('vendor_price')
        currency_code = vq_dict.get('currency_code', 'GBP')
        currency_symbol = vq_dict.get('currency_symbol', '£')

        if original_price and currency_code:
            vq_dict['vendor_price_gbp'] = convert_vq_price_to_gbp(original_price, currency_code)
            vq_dict['vendor_price_original'] = original_price
            vq_dict['vendor_price_currency'] = currency_code
            vq_dict['currency_symbol'] = currency_symbol
        else:
            vq_dict['vendor_price_gbp'] = original_price
            vq_dict['vendor_price_original'] = original_price
            vq_dict['vendor_price_currency'] = 'GBP'
            vq_dict['currency_symbol'] = '£'

        vqs.append(vq_dict)

    return vqs
