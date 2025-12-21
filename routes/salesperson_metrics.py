"""
Salesperson Metrics Module

This module provides routes and functionality for displaying salesperson metrics
in the customer edit page.
"""

from flask import Blueprint, jsonify, request, current_app, render_template
from routes.auth import login_required, current_user
from datetime import datetime, timedelta
import calendar
from db import execute as db_execute, db_cursor, _using_postgres


# Create blueprint
salesperson_metrics_bp = Blueprint('salesperson_metrics', __name__)


# -----------------------------
# Postgres compatibility helpers
# -----------------------------
def _execute_with_cursor(cursor, query, params=None):
    """
    Execute a query with automatic placeholder translation for Postgres.
    Translates '?' to '%s' when using Postgres.
    """
    if params is None:
        params = []
    
    if _using_postgres():
        # Translate '?' to '%s' for psycopg2
        query = query.replace('?', '%s')
    
    cursor.execute(query, params)
    return cursor

def get_date_range(period):
    """
    Calculate start and end dates based on the specified period
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if period == 'today':
        start_date = today
        end_date = today + timedelta(days=1) - timedelta(microseconds=1)
    elif period == 'yesterday':
        start_date = today - timedelta(days=1)
        end_date = today - timedelta(microseconds=1)
    elif period == 'week':
        # Start of current week (Monday)
        start_date = today - timedelta(days=today.weekday())
        end_date = today + timedelta(days=1) - timedelta(microseconds=1)
    elif period == 'last_week':
        # Last week
        start_date = today - timedelta(days=today.weekday() + 7)
        end_date = start_date + timedelta(days=7) - timedelta(microseconds=1)
    elif period == 'month':
        # Start of current month
        start_date = today.replace(day=1)
        end_date = today + timedelta(days=1) - timedelta(microseconds=1)
    elif period == 'last_month':
        # Last month
        last_month = today.month - 1 if today.month > 1 else 12
        last_month_year = today.year if today.month > 1 else today.year - 1
        last_month_days = calendar.monthrange(last_month_year, last_month)[1]
        start_date = today.replace(year=last_month_year, month=last_month, day=1)
        end_date = start_date.replace(day=last_month_days, hour=23, minute=59, second=59)
    elif period == 'year':
        # Start of current year
        start_date = today.replace(month=1, day=1)
        end_date = today + timedelta(days=1) - timedelta(microseconds=1)
    else:
        # Default to today
        start_date = today
        end_date = today + timedelta(days=1) - timedelta(microseconds=1)

    return start_date, end_date

def get_previous_period_range(period, start_date, end_date):
    """
    Calculate the previous period range for comparison
    """
    period_length = end_date - start_date

    prev_end_date = start_date - timedelta(microseconds=1)
    prev_start_date = prev_end_date - period_length

    return prev_start_date, prev_end_date

def get_salesperson_metrics_summary(db, salesperson_id, start_date, end_date, customer_id=None):
    """
    Get summary metrics for a salesperson in the given period
    """
    cursor = db.cursor()

    # Get previous period for comparison
    prev_start_date, prev_end_date = get_previous_period_range(None, start_date, end_date)

    # Format dates for SQL queries
    start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
    end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')
    prev_start_date_str = prev_start_date.strftime('%Y-%m-%d %H:%M:%S')
    prev_end_date_str = prev_end_date.strftime('%Y-%m-%d %H:%M:%S')

    # Customer filter for queries if specified
    customer_filter = 'AND customer_id = ?' if customer_id else ''
    customer_params = [customer_id] if customer_id else []

    # ORDERS METRICS
    # Current period
    _execute_with_cursor(cursor,
        f'''
        SELECT COUNT(*) as count, COALESCE(SUM(total_value), 0) as value
        FROM sales_orders
        WHERE salesperson_id = ? 
        AND date_entered BETWEEN ? AND ?
        {customer_filter}
        ''',
        [salesperson_id, start_date_str, end_date_str] + customer_params
    )
    orders_current = cursor.fetchone()

    # Previous period
    _execute_with_cursor(cursor,
        f'''
        SELECT COUNT(*) as count, COALESCE(SUM(total_value), 0) as value
        FROM sales_orders
        WHERE salesperson_id = ? 
        AND date_entered BETWEEN ? AND ?
        {customer_filter}
        ''',
        [salesperson_id, prev_start_date_str, prev_end_date_str] + customer_params
    )
    orders_prev = cursor.fetchone()

    # Calculate percentage change
    orders_count_current = orders_current['count'] or 0
    orders_count_prev = orders_prev['count'] or 1  # Avoid division by zero
    orders_change = round(((orders_count_current - orders_count_prev) / orders_count_prev) * 100) if orders_count_prev > 0 else 100

    # QUOTES/RFQs METRICS
    # Current period
    _execute_with_cursor(cursor,
        f'''
        SELECT 
            COUNT(DISTINCT r.id) as count, 
            COALESCE(SUM(rl.line_value), 0) as value
        FROM rfqs r
        LEFT JOIN rfq_lines rl ON r.id = rl.rfq_id
        WHERE r.salesperson_id = ? 
        AND r.entered_date BETWEEN ? AND ?
        {customer_filter}
        ''',
        [salesperson_id, start_date_str, end_date_str] + customer_params
    )
    quotes_current = cursor.fetchone()

    # Previous period
    _execute_with_cursor(cursor,
        f'''
        SELECT 
            COUNT(DISTINCT r.id) as count, 
            COALESCE(SUM(rl.line_value), 0) as value
        FROM rfqs r
        LEFT JOIN rfq_lines rl ON r.id = rl.rfq_id
        WHERE r.salesperson_id = ? 
        AND r.entered_date BETWEEN ? AND ?
        {customer_filter}
        ''',
        [salesperson_id, prev_start_date_str, prev_end_date_str] + customer_params
    )
    quotes_prev = cursor.fetchone()

    # Calculate percentage change
    quotes_count_current = quotes_current['count'] or 0
    quotes_count_prev = quotes_prev['count'] or 1
    quotes_change = round(((quotes_count_current - quotes_count_prev) / quotes_count_prev) * 100) if quotes_count_prev > 0 else 100

    # COMMUNICATIONS METRICS
    # Current period
    _execute_with_cursor(cursor,
        f'''
        SELECT 
            COUNT(*) as count,
            SUM(CASE WHEN communication_type = 'email' THEN 1 ELSE 0 END) as emails,
            SUM(CASE WHEN communication_type = 'phone' THEN 1 ELSE 0 END) as calls
        FROM contact_communications
        WHERE salesperson_id = ? 
        AND date BETWEEN ? AND ?
        {customer_filter}
        ''',
        [salesperson_id, start_date_str, end_date_str] + customer_params
    )
    comms_current = cursor.fetchone()

    # Previous period
    _execute_with_cursor(cursor,
        f'''
        SELECT COUNT(*) as count
        FROM contact_communications
        WHERE salesperson_id = ? 
        AND date BETWEEN ? AND ?
        {customer_filter}
        ''',
        [salesperson_id, prev_start_date_str, prev_end_date_str] + customer_params
    )
    comms_prev = cursor.fetchone()

    # Calculate percentage change
    comms_count_current = comms_current['count'] or 0
    comms_count_prev = comms_prev['count'] or 1
    comms_change = round(((comms_count_current - comms_count_prev) / comms_count_prev) * 100) if comms_count_prev > 0 else 100

    # CUSTOMER ENGAGEMENT METRICS
    # Current period - unique customers and contacts engaged
    _execute_with_cursor(cursor,
        f'''
        SELECT 
            COUNT(DISTINCT contact_id) as contacts,
            COUNT(*) as total_communications
        FROM contact_communications
        WHERE salesperson_id = ? 
        AND date BETWEEN ? AND ?
        {customer_filter}
        ''',
        [salesperson_id, start_date_str, end_date_str] + customer_params
    )
    contacts_current = cursor.fetchone()

    # Previous period - contacts
    _execute_with_cursor(cursor,
        f'''
        SELECT COUNT(DISTINCT contact_id) as contacts
        FROM contact_communications
        WHERE salesperson_id = ? 
        AND date BETWEEN ? AND ?
        {customer_filter}
        ''',
        [salesperson_id, prev_start_date_str, prev_end_date_str] + customer_params
    )
    contacts_prev = cursor.fetchone()

    # Calculate percentage change for contacts
    contacts_count_current = contacts_current['contacts'] or 0
    contacts_count_prev = contacts_prev['contacts'] or 1  # Avoid division by zero
    contacts_change = round(((
                                         contacts_count_current - contacts_count_prev) / contacts_count_prev) * 100) if contacts_count_prev > 0 else 100

    # For backward compatibility, keep original customer metrics if this is not a customer-specific view
    if not customer_id:
        # Original customers count code - only use when showing all customers
        _execute_with_cursor(cursor,
            f'''
            SELECT COUNT(DISTINCT customer_id) as customers
            FROM contact_communications
            WHERE salesperson_id = ? 
            AND date BETWEEN ? AND ?
            ''',
            [salesperson_id, start_date_str, end_date_str]
        )
        customers_current = cursor.fetchone()

        _execute_with_cursor(cursor,
            f'''
            SELECT COUNT(DISTINCT customer_id) as customers
            FROM contact_communications
            WHERE salesperson_id = ? 
            AND date BETWEEN ? AND ?
            ''',
            [salesperson_id, prev_start_date_str, prev_end_date_str]
        )
        customers_prev = cursor.fetchone()

        customers_count_current = customers_current['customers'] or 0
        customers_count_prev = customers_prev['customers'] or 1
        customers_change = round(((
                                              customers_count_current - customers_count_prev) / customers_count_prev) * 100) if customers_count_prev > 0 else 100
    else:
        # For customer-specific view, these values aren't meaningful (would be 0 or 1)
        # but keep them for backward compatibility
        customers_count_current = 1
        customers_change = 0

    # Build the summary object
    summary = {
        'orders': {
            'count': orders_count_current,
            'value': orders_current['value'] or 0,
            'change': orders_change
        },
        'quotes': {
            'count': quotes_count_current,
            'value': quotes_current['value'] or 0,
            'change': quotes_change
        },
        'communications': {
            'count': comms_count_current,
            'emails': comms_current['emails'] or 0,
            'calls': comms_current['calls'] or 0,
            'change': comms_change
        },
        'customers': {
            # For backward compatibility
            'count': customers_count_current,
            'change': customers_change,
            # New fields focused on contacts
            'contacts': contacts_count_current,
            'contactsChange': contacts_change,
            'totalCommunications': contacts_current['total_communications'] or 0
        }
    }

    return summary

def get_activities(db, salesperson_id, start_date, end_date, customer_id=None, limit=50):
    """
    Get recent activities for the salesperson
    """
    cursor = db.cursor()

    # Format dates for SQL queries
    start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
    end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')

    activities = []

    # Customer filter for queries if specified - WITH table alias for each query
    order_customer_filter = 'AND so.customer_id = ?' if customer_id else ''
    quote_customer_filter = 'AND r.customer_id = ?' if customer_id else ''
    comm_customer_filter = 'AND cc.customer_id = ?' if customer_id else ''
    customer_params = [customer_id] if customer_id else []

    # Get recent orders - using sales_status_id with correct join to sales_statuses
    _execute_with_cursor(cursor,
        f'''
        SELECT 
            so.id, so.date_entered as date, 'order' as type,
            so.sales_order_ref as reference, 
            ss.status_name as status, -- Correct column name from sales_statuses
            so.total_value as value, c.name as customer, 
            'New Sales Order' as title,
            'Order #' || so.sales_order_ref || ' - ' || COALESCE(ss.status_name, 'Unknown') as description,
            PRIMARY_CONTACT.name as contact
        FROM sales_orders so
        LEFT JOIN customers c ON so.customer_id = c.id
        LEFT JOIN contacts PRIMARY_CONTACT ON c.primary_contact_id = PRIMARY_CONTACT.id
        LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
        WHERE so.salesperson_id = ? 
        AND so.date_entered BETWEEN ? AND ?
        {order_customer_filter}
        ORDER BY so.date_entered DESC
        LIMIT {limit}
        ''',
        [salesperson_id, start_date_str, end_date_str] + customer_params
    )
    # Convert rows to dictionaries
    orders = []
    for row in cursor.fetchall():
        orders.append({key: row[key] for key in row.keys()})
    activities.extend(orders)

    # Get recent quotes/RFQs
    _execute_with_cursor(cursor,
        f'''
        SELECT 
            r.id, r.entered_date as date, 'quote' as type,
            r.customer_ref as reference, r.status as status,
            (SELECT COALESCE(SUM(line_value), 0) FROM rfq_lines WHERE rfq_id = r.id) as value, 
            c.name as customer,
            'New Quote Request' as title,
            'Quote #' || r.customer_ref || ' - ' || r.status as description,
            PRIMARY_CONTACT.name as contact
        FROM rfqs r
        LEFT JOIN customers c ON r.customer_id = c.id
        LEFT JOIN contacts PRIMARY_CONTACT ON c.primary_contact_id = PRIMARY_CONTACT.id
        WHERE r.salesperson_id = ? 
        AND r.entered_date BETWEEN ? AND ?
        {quote_customer_filter}
        ORDER BY r.entered_date DESC
        LIMIT {limit}
        ''',
        [salesperson_id, start_date_str, end_date_str] + customer_params
    )
    # Convert rows to dictionaries
    quotes = []
    for row in cursor.fetchall():
        quotes.append({key: row[key] for key in row.keys()})
    activities.extend(quotes)

    # Get recent communications
    _execute_with_cursor(cursor,
        f'''
        SELECT 
            cc.id, cc.date, cc.communication_type as type,
            '' as reference, '' as status, 0 as value,
            c.name as customer, con.name as contact,
            CASE 
                WHEN cc.communication_type = 'email' THEN 'Email Communication'
                WHEN cc.communication_type = 'phone' THEN 'Phone Call'
                ELSE 'Communication'
            END as title,
            cc.notes as description
        FROM contact_communications cc
        LEFT JOIN customers c ON cc.customer_id = c.id
        LEFT JOIN contacts con ON cc.contact_id = con.id
        WHERE cc.salesperson_id = ? 
        AND cc.date BETWEEN ? AND ?
        {comm_customer_filter}
        ORDER BY cc.date DESC
        LIMIT {limit}
        ''',
        [salesperson_id, start_date_str, end_date_str] + customer_params
    )
    # Convert rows to dictionaries
    communications = []
    for row in cursor.fetchall():
        communications.append({key: row[key] for key in row.keys()})
    activities.extend(communications)

    # Sort all activities by date, newest first
    activities.sort(key=lambda x: x['date'], reverse=True)

    # Limit the combined result
    return activities[:limit]

def get_funnel_data(db, salesperson_id, start_date, end_date, customer_id=None):
    """
    Get conversion funnel data
    """
    cursor = db.cursor()

    # Format dates for SQL queries
    start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
    end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')

    # Customer filter for queries if specified - without table alias
    customer_filter = 'AND customer_id = ?' if customer_id else ''
    customer_params = [customer_id] if customer_id else []

    # Get leads (new customer updates or first communications)
    _execute_with_cursor(cursor,
        f'''
        SELECT COUNT(DISTINCT customer_id) as leads
        FROM (
            SELECT customer_id, MIN(date) as first_contact
            FROM contact_communications
            WHERE salesperson_id = ?
            {customer_filter}
            GROUP BY customer_id
            HAVING first_contact BETWEEN ? AND ?
        )
        ''',
        [salesperson_id] + customer_params + [start_date_str, end_date_str]
    )
    leads_result = cursor.fetchone()
    leads = leads_result['leads'] if leads_result else 0

    # Get quotes
    _execute_with_cursor(cursor,
        f'''
        SELECT COUNT(*) as quotes
        FROM rfqs
        WHERE salesperson_id = ? 
        AND entered_date BETWEEN ? AND ?
        {customer_filter}
        ''',
        [salesperson_id, start_date_str, end_date_str] + customer_params
    )
    quotes_result = cursor.fetchone()
    quotes = quotes_result['quotes'] if quotes_result else 0

    # Get orders
    _execute_with_cursor(cursor,
        f'''
        SELECT COUNT(*) as orders
        FROM sales_orders
        WHERE salesperson_id = ? 
        AND date_entered BETWEEN ? AND ?
        {customer_filter}
        ''',
        [salesperson_id, start_date_str, end_date_str] + customer_params
    )
    orders_result = cursor.fetchone()
    orders = orders_result['orders'] if orders_result else 0

    return {
        'leads': leads,
        'quotes': quotes,
        'orders': orders
    }

def get_communication_breakdown(db, salesperson_id, start_date, end_date, customer_id=None):
    """
    Get breakdown of communication methods
    """
    cursor = db.cursor()

    # Format dates for SQL queries
    start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
    end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')

    # Customer filter for queries if specified - without table alias
    customer_filter = 'AND customer_id = ?' if customer_id else ''
    customer_params = [customer_id] if customer_id else []

    _execute_with_cursor(cursor,
        f'''
        SELECT 
            communication_type as type,
            COUNT(*) as count
        FROM contact_communications
        WHERE salesperson_id = ? 
        AND date BETWEEN ? AND ?
        {customer_filter}
        GROUP BY communication_type
        ''',
        [salesperson_id, start_date_str, end_date_str] + customer_params
    )
    results = cursor.fetchall()

    communication_data = {
        'email': 0,
        'call': 0
    }

    for row in results:
        comm_type = row['type']
        if comm_type == 'email':
            communication_data['email'] = row['count']
        elif comm_type == 'phone':
            communication_data['call'] = row['count']

    return communication_data

def row_to_dict(row):
    """
    Convert a SQLite Row object to a dictionary
    """
    return {key: row[key] for key in row.keys()} if row else {}


@salesperson_metrics_bp.route('/customers/<int:customer_id>', methods=['GET'])
def get_salesperson_metrics(customer_id):
    """API endpoint to get salesperson metrics data"""
    current_app.logger.info(f"Fetching metrics for customer: {customer_id}")
    try:
        with db_cursor() as db:
            # Get parameters
            period = request.args.get('period', 'today')
            salesperson_id = request.args.get('salesperson_id')

            if not salesperson_id:
                current_app.logger.error("Salesperson ID missing")
                return jsonify({
                    'success': False,
                    'error': 'Salesperson ID is required'
                }), 400

            current_app.logger.info(f"Metrics request: period={period}, salesperson_id={salesperson_id}")

            # Calculate date range based on period
            start_date, end_date = get_date_range(period)

            # Add better error handling with try/except blocks for each function call

            # Get summary metrics
            try:
                summary = get_salesperson_metrics_summary(
                    db, salesperson_id, start_date, end_date, customer_id
                )
                # Convert Row objects to dictionaries
                if isinstance(summary, dict):
                    for key in summary:
                        if hasattr(summary[key], 'keys'):
                            summary[key] = row_to_dict(summary[key])
                current_app.logger.info("Summary metrics generated successfully")
            except Exception as e:
                current_app.logger.error(f"Error in get_salesperson_metrics_summary: {str(e)}")
                return jsonify({
                    'success': False,
                    'error': f"Error in summary metrics: {str(e)}"
                }), 500

            # Get recent activities
            try:
                activities = get_activities(
                    db, salesperson_id, start_date, end_date, customer_id
                )
                # Convert Row objects to dictionaries
                activities = [row_to_dict(row) for row in activities]
                current_app.logger.info(f"Activities generated successfully. Count: {len(activities)}")
            except Exception as e:
                current_app.logger.error(f"Error in get_activities: {str(e)}")
                return jsonify({
                    'success': False,
                    'error': f"Error in activities: {str(e)}"
                }), 500

            # Get funnel data
            try:
                funnel = get_funnel_data(
                    db, salesperson_id, start_date, end_date, customer_id
                )
                # Convert Row object to dictionary
                funnel = row_to_dict(funnel) if hasattr(funnel, 'keys') else funnel
                current_app.logger.info("Funnel data generated successfully")
            except Exception as e:
                current_app.logger.error(f"Error in get_funnel_data: {str(e)}")
                return jsonify({
                    'success': False,
                    'error': f"Error in funnel data: {str(e)}"
                }), 500

            # Get communication breakdown
            try:
                communications = get_communication_breakdown(
                    db, salesperson_id, start_date, end_date, customer_id
                )
                # No conversion needed if it's already a dict
                current_app.logger.info("Communication breakdown generated successfully")
            except Exception as e:
                current_app.logger.error(f"Error in get_communication_breakdown: {str(e)}")
                return jsonify({
                    'success': False,
                    'error': f"Error in communication breakdown: {str(e)}"
                }), 500

            return jsonify({
                'success': True,
                'data': {
                    'summary': summary,
                    'activities': activities,
                    'funnel': funnel,
                    'communications': communications,
                    'period': {
                        'name': period,
                        'start_date': start_date.strftime('%Y-%m-%d'),
                        'end_date': end_date.strftime('%Y-%m-%d')
                    }
                }
            })

    except Exception as e:
        current_app.logger.error(f"Error fetching metrics: {str(e)}")
        return jsonify({
            'success': False,
            'error': f"Error fetching metrics: {str(e)}"
        }), 500

def init_app(app, customers_bp):
    """
    Initialize the salesperson metrics functionality.
    This function registers the blueprint and routes.
    """
    # Register the blueprint with the app
    app.register_blueprint(salesperson_metrics_bp)

    # Add an entry point on the customers blueprint if needed
    @customers_bp.route('/<int:customer_id>/salesperson_metrics')
    @login_required
    def customer_salesperson_metrics(customer_id):
        """View for salesperson metrics tab"""
        # This route just serves as a placeholder for the tab
        # The actual data will be loaded via AJAX
        return render_template('customers/edit.html', customer_id=customer_id)
