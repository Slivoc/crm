# In routes/salespeople.py
import json
from collections import defaultdict
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, Response, stream_with_context
from routes.auth import login_required, current_user
from ai_helper import get_cached_news, get_top_customers_for_news, get_watched_customers_for_news, get_cache_key, cleanup_old_cache_files, fetch_customer_news_perplexity, process_customer_news_chatgpt, cache_news
from routes.news_email import get_news_email_addresses, send_news_email
from models import (get_salespeople, get_all_salespeople_with_contact_counts, get_call_list_contact_ids, add_to_call_list, remove_from_call_list,
    get_call_list_with_communication_status, update_call_list_priority, update_call_list_notes, bulk_add_to_call_list, get_salesperson_recent_communications, get_communication_types_for_salesperson, delete_customer_tag, insert_customer_tags, get_all_tags, insert_customer_tag, get_engagement_settings, get_all_salespeople_with_customer_counts, get_priorities, save_engagement_settings, insert_salesperson, get_active_salespeople, get_engagement_metrics, toggle_salesperson_active, get_customer_contacts_with_communications, update_customer_field_value, get_all_contact_statuses, get_status_counts_for_salesperson, get_tags_by_customer_id, get_salesperson_customers_with_spend, get_salesperson_by_id, get_salesperson_contacts, get_contact_communications, get_salesperson_sales_by_date_range, get_salesperson_monthly_sales, get_accounts_monthly_sales,
                    update_salesperson, delete_salesperson,
                    get_customers_with_status_and_updates, get_customer_status_options, get_consolidated_customer_orders, get_consolidated_customer_ids,
                    add_customer_status_update, get_customer_updates, get_customer_rfqs, get_customer_rfqs_by_date_range, get_customer_orders_by_date_range, get_customer_active_rfqs_count, get_customer_active_orders_count,
                    get_customer_orders, Permission, get_salespeople_with_stats, get_total_customers, get_total_orders, get_total_active_orders, get_total_active_rfqs, get_salesperson_recent_activities, get_salesperson_active_rfqs, get_salesperson_customers, get_salesperson_pending_orders, get_customer_by_id)
from db import get_db_connection, execute as db_execute, db_cursor, _using_postgres, _execute_with_cursor

from dateutil.relativedelta import relativedelta
import calendar

salespeople_bp = Blueprint('salespeople', __name__)

# PostgreSQL migration helpers
def _execute_with_cursor(cursor, query, params=None):
    """Execute a query with automatic placeholder translation for Postgres"""
    if _using_postgres():
        # Translate ? placeholders to %s for Postgres
        query = query.replace('?', '%s')
    return cursor.execute(query, params or ())

def is_mobile():
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_keywords = ['mobile', 'android', 'iphone', 'ipad']
    return any(keyword in user_agent for keyword in mobile_keywords)

@salespeople_bp.route('/')
@login_required
def salespeople():
    salespeople = get_salespeople()
    return render_template('salespeople/index.html', salespeople=salespeople)

@salespeople_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_salesperson():
    if request.method == 'POST':
        name = request.form['name']
        insert_salesperson(name)
        flash('Salesperson successfully added!', 'success')
        return redirect(url_for('salespeople.salespeople'))
    return render_template('salespeople/create_salesperson.html')


@salespeople_bp.route('/<int:salesperson_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_salesperson(salesperson_id):
    salesperson = get_salesperson_by_id(salesperson_id)
    if not salesperson:
        flash('Salesperson not found!', 'error')
        return redirect(url_for('salespeople.salespeople'))

    if request.method == 'POST':
        name = request.form['name']
        update_salesperson(salesperson_id, name)
        flash('Salesperson updated successfully!', 'success')
        return redirect(url_for('salespeople.salespeople'))

    return render_template('salespeople/edit_salesperson.html', salesperson=salesperson)

@salespeople_bp.route('/<int:salesperson_id>/delete', methods=['POST'])
@login_required
def delete_salesperson_route(salesperson_id):
    delete_salesperson(salesperson_id)
    flash('Salesperson deleted successfully!', 'success')
    return redirect(url_for('salespeople.salespeople'))


# In routes/salespeople.py

# Update the existing dashboard route
@salespeople_bp.route('/dashboard')
@login_required
def dashboard():
    """Main engagement dashboard for active salespeople"""
    try:
        # Get only active salespeople for engagement panels
        selected_salespeople = get_active_salespeople()

        # Get all salespeople with stats for the table
        all_salespeople_with_stats = get_salespeople_with_stats()

        # Get all salespeople (basic) for management
        all_salespeople = get_salespeople()

        # Get status options for future filtering
        try:
            customer_statuses = get_customer_status_options()
        except:
            customer_statuses = []

        try:
            contact_statuses = get_all_contact_statuses()
        except:
            contact_statuses = []

        return render_template(
            'salespeople/dashboard.html',
            selected_salespeople=selected_salespeople,
            all_salespeople=all_salespeople_with_stats,  # For the table
            all_salespeople_basic=all_salespeople,  # For the management panel
            customer_statuses=customer_statuses,
            contact_statuses=contact_statuses,
            current_selected_salespeople=[sp['id'] for sp in selected_salespeople],
            current_customer_statuses=[],  # For future use
            current_contact_statuses=[],  # For future use
            current_overdue_threshold=14,
            current_critical_threshold=30
        )

    except Exception as e:
        print(f"Error loading engagement dashboard: {str(e)}")
        import traceback
        print(traceback.format_exc())
        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('index'))

@salespeople_bp.route('/<int:salesperson_id>/activity')
@login_required
def activity(salesperson_id):
    """Individual salesperson activity page"""
    try:
        import time
        from collections import OrderedDict
        import traceback
        timings = OrderedDict()
        t0 = time.perf_counter()
        print(f"DEBUG: Starting activity view for salesperson {salesperson_id}")

        # Get salesperson info
        try:
            t_step = time.perf_counter()
            salesperson = get_salesperson_by_id(salesperson_id)
            if not salesperson:
                print(f"DEBUG: Salesperson {salesperson_id} not found")
                flash('Salesperson not found!', 'error')
                return redirect(url_for('salespeople.dashboard'))
            timings['salesperson'] = time.perf_counter() - t_step
        except Exception as e:
            print(f"DEBUG: Error getting salesperson: {str(e)}")
            flash(f"Error retrieving salesperson data: {str(e)}", 'error')
            return redirect(url_for('salespeople.dashboard'))

        # ADD THIS: Get all salespeople for dropdown
        try:
            t_step = time.perf_counter()
            all_salespeople = get_all_salespeople_with_contact_counts()
            print(f"DEBUG: Found {len(all_salespeople)} salespeople for dropdown")
            timings['salespeople_dropdown'] = time.perf_counter() - t_step
        except Exception as e:
            print(f"DEBUG: Error getting all salespeople: {e}")
            all_salespeople = []

        # Get existing data (only what we need for the stats cards)
        try:
            t_step = time.perf_counter()
            assigned_customers = get_salesperson_customers(salesperson_id)
            print(f"DEBUG: Retrieved {len(assigned_customers) if assigned_customers else 0} assigned customers")
            timings['assigned_customers'] = time.perf_counter() - t_step

            # NEW CODE: Get consolidated customer IDs for all associated customers
            t_step = time.perf_counter()
            consolidated_data = get_consolidated_customer_ids(salesperson_id)
            timings['consolidated_customers'] = time.perf_counter() - t_step

            # Extract ALL customer IDs (main + associated)
            all_customer_ids = []
            for customer_data in consolidated_data.values():
                all_customer_ids.extend(customer_data['all_customer_ids'])

            # Remove duplicates
            all_customer_ids = list(set(all_customer_ids))
            print(f"DEBUG: Found {len(all_customer_ids)} total customer IDs (including associated)")

            # Now get orders using ALL customer IDs (not just salesperson_id)
            if all_customer_ids:
                t_step = time.perf_counter()
                placeholders = ','.join('?' for _ in all_customer_ids)
                query = f"SELECT * FROM sales_orders WHERE customer_id IN ({placeholders})"
                salesperson_orders = db_execute(query, all_customer_ids, fetch='all') or []
                print(
                    f"DEBUG: Retrieved {len(salesperson_orders)} orders using consolidated customer IDs")
                timings['salesperson_orders'] = time.perf_counter() - t_step
            else:
                salesperson_orders = []

            print(f"DEBUG: Final count of orders: {len(salesperson_orders) if salesperson_orders else 0}")

        except Exception as e:
            print(f"DEBUG: Error getting existing data: {str(e)}")
            # If we can't get this data, we'll use empty lists as fallback
            assigned_customers = []
            salesperson_orders = []

        # Note: We no longer need recent_activities since we replaced that section
        # with top customers data that comes from the AJAX call
        template = 'salespeople/activity_mobile.html' if is_mobile() else 'salespeople/activity.html'

        call_list_prefill = None
        try:
            call_list_raw = get_call_list_with_communication_status(salesperson_id)

            def _parse_date(value):
                if not value:
                    return None
                if isinstance(value, datetime):
                    return value
                if isinstance(value, date):
                    return datetime.combine(value, datetime.min.time())
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
                    try:
                        return datetime.strptime(str(value), fmt)
                    except ValueError:
                        continue
                try:
                    return datetime.fromisoformat(str(value))
                except ValueError:
                    return None

            def _comm_icon(comm_type):
                icon_map = {
                    'Phone': 'telephone',
                    'Email': 'envelope',
                    'Meeting': 'calendar-event',
                    'Video Call': 'camera-video',
                    'Other': 'chat-dots'
                }
                return icon_map.get(comm_type, 'chat-dots')

            def _decorate(contact):
                added_dt = _parse_date(contact.get('added_date'))
                if added_dt:
                    days_waiting = max((date.today() - added_dt.date()).days, 0)
                    added_display = added_dt.strftime('%Y-%m-%d')
                else:
                    days_waiting = 0
                    added_display = ''

                latest_dt = _parse_date(contact.get('latest_communication_since_added'))
                latest_display = latest_dt.strftime('%Y-%m-%d') if latest_dt else ''
                contact['days_waiting'] = days_waiting
                contact['added_date_display'] = added_display
                contact['latest_communication_since_added_display'] = latest_display
                contact['comm_icon'] = _comm_icon(contact.get('latest_communication_type'))
                return contact

            call_list_prefill = {
                'no_communications': [_decorate(c) for c in (call_list_raw.get('no_communications') or [])],
                'has_communications': [_decorate(c) for c in (call_list_raw.get('has_communications') or [])],
                'total_count': call_list_raw.get('total_count', 0)
            }
        except Exception as e:
            print(f"DEBUG: Error preloading call list: {e}")
            call_list_prefill = None

        t_render = time.perf_counter()
        response = render_template(template,
                               salesperson=salesperson,
            all_salespeople=all_salespeople,  # ADD THIS LINE
            assigned_customers=assigned_customers,
            pending_orders=salesperson_orders,  # Keep the variable name for compatibility
            call_list_prefill=call_list_prefill
        )
        timings['render_template'] = time.perf_counter() - t_render
        return response
    except Exception as e:
        import traceback
        print(f"DEBUG: Unhandled exception in activity view: {str(e)}")
        print(traceback.format_exc())
        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('salespeople.dashboard'))
    finally:
        try:
            total = time.perf_counter() - t0
            timing_summary = ", ".join(f"{k}={v:.3f}s" for k, v in timings.items())
            print(f"TIMING salespeople.activity total={total:.3f}s {timing_summary}")
        except Exception:
            pass

@salespeople_bp.route('/<int:salesperson_id>/customers')
@login_required
def customers(salesperson_id):
    try:
        print(f"Looking up salesperson with ID {salesperson_id}")
        salesperson = get_salesperson_by_id(salesperson_id)
        if not salesperson:
            print(f"Salesperson with ID {salesperson_id} not found")
            flash('Salesperson not found!', 'error')
            return redirect(url_for('salespeople.dashboard'))

        # ADD THIS: Get all salespeople for dropdown
        try:
            all_salespeople = get_all_salespeople_with_customer_counts()
            print(f"Found {len(all_salespeople)} salespeople for dropdown")
        except Exception as e:
            print(f"Error getting all salespeople: {e}")
            all_salespeople = []

        # Get filter parameters
        search_term = request.args.get('search', '')
        status_filter = request.args.get('status', '')
        priority_filter = request.args.get('priority', '')  # NEW: Priority filter

        # Get sort parameters with validation
        sort_by = request.args.get('sort', 'name')
        sort_order = request.args.get('order', 'asc')

        # Validate sort parameters - UPDATED to include contacts_count and priority
        valid_sort_columns = ['name', 'status', 'country', 'historical_spend',
                              'estimated_revenue', 'fleet_size', 'latest_update',
                              'most_recent_order', 'contacts_count', 'priority']  # NEW: Added priority

        if sort_by not in valid_sort_columns:
            sort_by = 'name'

        if sort_order not in ['asc', 'desc']:
            sort_order = 'asc'

        print(f"Fetching customers for salesperson {salesperson_id} with sort: {sort_by} {sort_order}")

        # Get customers with historical spend data, contacts count, priorities, and sorting
        customers = get_salesperson_customers_with_spend(salesperson_id, search_term, status_filter, priority_filter, sort_by, sort_order)  # UPDATED: Added priority_filter
        customer_statuses = get_customer_status_options()
        priorities = get_priorities()  # NEW: Get priority options

        # Check if this is an AJAX request for table refresh
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # Return only the table body content for AJAX requests
            return render_template(
                'salespeople/customers_table_body.html',
                customers=customers,
                current_sort=sort_by,
                current_order=sort_order
            )

        # Generate breadcrumbs for full page requests
        breadcrumbs = generate_breadcrumbs(
            ('Home', url_for('index')),
            ('Salespeople', url_for('salespeople.dashboard')),
            (salesperson['name'], url_for('salespeople.activity', salesperson_id=salesperson_id)),
            ('Customers', url_for('salespeople.customers', salesperson_id=salesperson_id))
        )

        return render_template(
            'salespeople/customers.html',
            salesperson=salesperson,
            customers=customers,
            all_salespeople=all_salespeople,  # ADD THIS LINE
            customer_statuses=customer_statuses,
            priorities=priorities,
            search_term=search_term,
            status_filter=status_filter,
            priority_filter=priority_filter,
            current_sort=sort_by,
            current_order=sort_order,
            breadcrumbs=breadcrumbs
        )
    except Exception as e:

        print(f"Exception in customers route: {str(e)}")

        # Handle AJAX requests with JSON error response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': str(e)}), 500

        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('salespeople.dashboard'))

@salespeople_bp.route('/<int:salesperson_id>/add_customer_update', methods=['POST'])
@login_required
def add_customer_update(salesperson_id):
    """Add a status update for a customer"""
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        update_text = request.form.get('update_text')

        success = add_customer_status_update(customer_id, salesperson_id, update_text)

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # Return JSON response for AJAX requests
            return jsonify({'success': success})

        if success:
            flash('Customer update added successfully!', 'success')
        else:
            flash('Failed to add customer update.', 'error')

        return redirect(url_for('salespeople.customers', salesperson_id=salesperson_id))


@salespeople_bp.route('/customer_details/<int:customer_id>')
@login_required
def customer_details(customer_id):
    """Get customer details for AJAX loading - includes data from associated companies"""
    try:
        print(f"DEBUG: Fetching customer details for ID {customer_id}")
        customer = get_customer_by_id(customer_id)
        print(f"DEBUG: Customer found: {customer['name'] if customer else 'None'}")

        related_customer_ids = [customer_id]

        # Check if this customer has associated companies
        associated_query = """
            SELECT associated_customer_id 
            FROM customer_associations 
            WHERE main_customer_id = ?
        """
        associated_results = db_execute(associated_query, (customer_id,), fetch='all') or []
        if associated_results:
            child_ids = [row['associated_customer_id'] for row in associated_results]
            related_customer_ids.extend(child_ids)
            print(f"DEBUG: Found {len(child_ids)} associated companies for customer {customer_id}")
        print(f"DEBUG: Total related customer IDs: {related_customer_ids}")

        # Get consolidated contacts data
        contacts = get_customer_contacts_with_communications_consolidated(related_customer_ids)
        print(f"DEBUG: Retrieved {len(contacts) if contacts else 0} consolidated contacts")

        # NEW: Check if this is a request for contacts JSON only
        format_param = request.args.get('format')
        if format_param == 'contacts_json':
            # Return just the contacts data in JSON format for the edit modal
            contacts_data = []
            if contacts:
                for contact in contacts:
                    contacts_data.append({
                        'id': contact.get('id'),
                        'name': f"{contact.get('name', '')}{' ' + contact.get('second_name', '') if contact.get('second_name') else ''}".strip()
                    })

            return jsonify({
                'success': True,
                'contacts': contacts_data
            })

        # Continue with existing logic for full customer details
        customer_updates = get_customer_updates_consolidated(related_customer_ids)
        print(f"DEBUG: Retrieved {len(customer_updates) if customer_updates else 0} consolidated customer updates")

        customer_rfqs = get_customer_rfqs_consolidated(related_customer_ids)
        print(f"DEBUG: Retrieved {len(customer_rfqs) if customer_rfqs else 0} consolidated RFQs")

        customer_orders = get_customer_orders_consolidated(related_customer_ids)
        print(f"DEBUG: Retrieved {len(customer_orders) if customer_orders else 0} consolidated orders")

        # Get customer tags (only from main customer)
        customer_tags = get_tags_by_customer_id(customer_id)
        print(f"DEBUG: Retrieved {len(customer_tags)} tags for customer {customer_id}")

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # For AJAX requests, render a partial template
            html = render_template(
                'salespeople/customer_details.html',
                customer=customer,
                updates=customer_updates,
                rfqs=customer_rfqs,
                orders=customer_orders,
                tags=customer_tags,
                contacts=contacts
            )
            return html

        # Fallback to full page template
        return render_template(
            'salespeople/customer_detail_page.html',
            customer=customer,
            updates=customer_updates,
            rfqs=customer_rfqs,
            orders=customer_orders,
            tags=customer_tags,
            contacts=contacts
        )
    except Exception as e:
        print(f"DEBUG: Exception in customer_details: {str(e)}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': str(e)}), 500

        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('salespeople.dashboard'))


@salespeople_bp.route('/customer_data/<int:customer_id>')
@login_required
def customer_data(customer_id):
    """Get time-filtered customer data for AJAX requests - includes data from associated companies"""
    try:
        from datetime import datetime, timedelta, date
        from collections import defaultdict

        time_period = request.args.get('time_period', 'last_30_days')
        print(f"DEBUG customer_data: Request for customer_id {customer_id}, time_period {time_period}")

        customer = get_customer_by_id(customer_id)
        print(f"DEBUG customer_data: Customer found: {customer['name'] if customer else 'None'}")

        related_customer_ids = [customer_id]

        associated_query = """
            SELECT associated_customer_id 
            FROM customer_associations 
            WHERE main_customer_id = ?
        """
        associated_results = db_execute(associated_query, (customer_id,), fetch='all') or []
        if associated_results:
            child_ids = [row['associated_customer_id'] for row in associated_results]
            related_customer_ids.extend(child_ids)
            print(f"DEBUG customer_data: Found {len(child_ids)} associated companies")

        today = datetime.now().date()

        # Determine date range based on time period
        if time_period == 'yearly':
            # All years - handled by the existing yearly aggregation
            start_date = end_date = None
        elif time_period == 'last_12_months':
            # Last 12 months
            start_date = today - timedelta(days=365)
            end_date = today
        else:  # Default to last 30 days
            # Last 30 days
            start_date = today - timedelta(days=30)
            end_date = today

        print(f"DEBUG customer_data: Date range: {start_date} to {end_date}")

        try:
            rfqs = get_customer_rfqs_by_date_range_consolidated(related_customer_ids, start_date, end_date, time_period)
            print(f"DEBUG customer_data: Retrieved {len(rfqs)} consolidated RFQs")
        except Exception as e:
            print(f"DEBUG customer_data: Error getting RFQs: {str(e)}")
            rfqs = []

        try:
            orders = get_customer_orders_by_date_range_consolidated(related_customer_ids, start_date, end_date,
                                                                    time_period)
            print(f"DEBUG customer_data: Retrieved {len(orders)} consolidated orders")
        except Exception as e:
            print(f"DEBUG customer_data: Error getting orders: {str(e)}")
            orders = []

        # Add this line right here:
        total_sales = sum(float(order.get('total_value', 0) or 0) for order in orders)
        print(f"DEBUG customer_data: Total sales calculated: {total_sales}")

        try:
            active_rfqs = get_customer_active_rfqs_count_consolidated(related_customer_ids)
            active_orders = get_customer_active_orders_count_consolidated(related_customer_ids)
        except Exception as e:
            print(f"DEBUG customer_data: Error getting active counts: {str(e)}")
            active_rfqs = active_orders = 0

        # Get customer tags for the response (only from main customer)
        customer_tags = get_tags_by_customer_id(customer_id)

        # Chart data
        if time_period == 'yearly':
            # For yearly data, use the existing approach - data is already aggregated by year
            years = []
            rfq_counts = []
            order_values = []

            # Extract data from orders which are already grouped by year
            for order in orders:
                year = order['date_entered'].split('-')[0]
                years.append(year)
                order_values.append(float(order['total_value'] or 0))

                # Try to match RFQs to the same years if possible
                year_rfqs = sum(1 for r in rfqs if r.get('entered_date', '').startswith(year))
                rfq_counts.append(year_rfqs)

            chart_data = {
                'labels': years,
                'rfqs': rfq_counts,
                'orders': order_values
            }

        elif time_period == 'last_12_months':
            # Last 12 months - group by month
            month_data = defaultdict(lambda: {'rfqs': 0, 'orders': 0})

            # Generate all 12 month labels
            labels = []
            for i in range(12):
                month_date = today.replace(day=1) - timedelta(days=i * 30)  # Approximate
                month_label = month_date.strftime('%b %Y')
                labels.insert(0, month_label)
                month_data[month_label] = {'rfqs': 0, 'orders': 0}

            # Process RFQs
            for rfq in rfqs:
                try:
                    date_obj = datetime.strptime(rfq['entered_date'], "%Y-%m-%d")
                    month_label = date_obj.strftime('%b %Y')
                    if month_label in month_data:
                        month_data[month_label]['rfqs'] += 1
                except Exception as e:
                    print(f"DEBUG: Error processing RFQ date: {e}")

            # Process Orders
            for order in orders:
                try:
                    date_obj = datetime.strptime(order['date_entered'], "%Y-%m-%d")
                    month_label = date_obj.strftime('%b %Y')
                    if month_label in month_data:
                        month_data[month_label]['orders'] += float(order['total_value'] or 0)
                except Exception as e:
                    print(f"DEBUG: Error processing order date: {e}")

            # Create series data for chart
            rfqs_series = [month_data[label]['rfqs'] for label in labels]
            orders_series = [month_data[label]['orders'] for label in labels]

            chart_data = {
                'labels': labels,
                'rfqs': rfqs_series,
                'orders': orders_series
            }

        else:  # last_30_days
            # Last 30 days - group by day
            day_data = defaultdict(lambda: {'rfqs': 0, 'orders': 0})

            # Generate all 30 day labels
            labels = []
            for i in range(30):
                day_date = today - timedelta(days=i)
                day_label = day_date.strftime('%d %b')
                labels.insert(0, day_label)
                day_data[day_label] = {'rfqs': 0, 'orders': 0}

            # Process RFQs
            for rfq in rfqs:
                try:
                    date_obj = datetime.strptime(rfq['entered_date'], "%Y-%m-%d")
                    day_label = date_obj.strftime('%d %b')
                    if day_label in day_data:
                        day_data[day_label]['rfqs'] += 1
                except Exception as e:
                    print(f"DEBUG: Error processing RFQ date: {e}")

            # Process Orders
            for order in orders:
                try:
                    date_obj = datetime.strptime(order['date_entered'], "%Y-%m-%d")
                    day_label = date_obj.strftime('%d %b')
                    if day_label in day_data:
                        day_data[day_label]['orders'] += float(order['total_value'] or 0)
                except Exception as e:
                    print(f"DEBUG: Error processing order date: {e}")

            # Create series data for chart
            rfqs_series = [day_data[label]['rfqs'] for label in labels]
            orders_series = [day_data[label]['orders'] for label in labels]

            chart_data = {
                'labels': labels,
                'rfqs': rfqs_series,
                'orders': orders_series
            }

        return jsonify({
            'rfqs': rfqs,
            'orders': orders,
            'active_rfqs': active_rfqs,
            'active_orders': active_orders,
            'total_sales': total_sales,
            'tags': customer_tags,
            'chart': chart_data
        })

    except Exception as e:
        print(f"DEBUG customer_data: Main exception: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Helper functions to get consolidated data from multiple customers
def get_customer_updates_consolidated(customer_ids):
    """Get customer updates from multiple customers consolidated"""
    if not customer_ids:
        return []

    placeholders = ','.join('?' for _ in customer_ids)
    query = f"""
        SELECT cu.*, c.name as customer_name
        FROM customer_updates cu
        LEFT JOIN customers c ON cu.customer_id = c.id
        WHERE cu.customer_id IN ({placeholders})
        ORDER BY cu.date DESC
    """

    updates = db_execute(query, customer_ids, fetch='all') or []
    return [dict(update) for update in updates]


def get_customer_rfqs_consolidated(customer_ids):
    """Get RFQs from multiple customers consolidated"""
    if not customer_ids:
        return []

    placeholders = ','.join('?' for _ in customer_ids)
    query = f"""
        SELECT r.*, c.name as customer_name
        FROM rfqs r
        LEFT JOIN customers c ON r.customer_id = c.id
        WHERE r.customer_id IN ({placeholders})
        ORDER BY r.entered_date DESC
    """

    rfqs = db_execute(query, customer_ids, fetch='all') or []
    return [dict(rfq) for rfq in rfqs]


def get_customer_orders_consolidated(customer_ids):
    """Get orders from multiple customers consolidated"""
    if not customer_ids:
        return []

    placeholders = ','.join('?' for _ in customer_ids)
    query = f"""
        SELECT so.*, c.name as customer_name, ss.status_name
        FROM sales_orders so
        LEFT JOIN customers c ON so.customer_id = c.id
        LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
        WHERE so.customer_id IN ({placeholders})
        ORDER BY so.date_entered DESC
    """

    orders = db_execute(query, customer_ids, fetch='all') or []
    return [dict(order) for order in orders]


def get_customer_contacts_with_communications_consolidated(customer_ids):
    """Get contacts with communications from multiple customers consolidated"""
    if not customer_ids:
        return []

    placeholders = ','.join('?' for _ in customer_ids)

    query = f"""
        SELECT c.*, cu.name as customer_name
        FROM contacts c
        LEFT JOIN customers cu ON c.customer_id = cu.id
        WHERE c.customer_id IN ({placeholders})
        ORDER BY c.name
    """

    contacts = db_execute(query, customer_ids, fetch='all') or []
    return [dict(contact) for contact in contacts]


def get_customer_rfqs_by_date_range_consolidated(customer_ids, start_date, end_date, time_period):
    """Get RFQs for multiple customers within a given date range"""
    if not customer_ids:
        return []

    placeholders = ','.join('?' for _ in customer_ids)
    query = f"""
        SELECT r.*, c.name as customer_name
        FROM rfqs r
        LEFT JOIN customers c ON r.customer_id = c.id
        WHERE r.customer_id IN ({placeholders})
    """

    params = customer_ids.copy()
    if start_date and end_date:
        query += " AND r.entered_date BETWEEN ? AND ?"
        params.extend([start_date, end_date])

    query += " ORDER BY r.entered_date DESC LIMIT 200"
    rfqs = db_execute(query, params, fetch='all') or []
    return [dict(rfq) for rfq in rfqs]


def get_customer_orders_by_date_range_consolidated(customer_ids, start_date, end_date, time_period):
    """Get orders for multiple customers within a given date range"""
    if not customer_ids:
        return []

    placeholders = ','.join('?' for _ in customer_ids)
    base_query = f"""
        SELECT so.*, c.name as customer_name, ss.status_name
        FROM sales_orders so
        LEFT JOIN customers c ON so.customer_id = c.id
        LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
        WHERE so.customer_id IN ({placeholders})
    """

    params = customer_ids.copy()

    if time_period != 'yearly' and start_date and end_date:
        base_query += " AND so.date_entered BETWEEN ? AND ?"
        params.extend([start_date, end_date])

    base_query += " ORDER BY so.date_entered DESC"
    orders_rows = db_execute(base_query, params, fetch='all') or []

    if time_period == 'yearly':
        from collections import defaultdict
        year_map = defaultdict(lambda: {'order_count': 0, 'total_value': 0, 'latest_date': None})

        for row in orders_rows:
            date_value = row.get('date_entered')
            if not date_value:
                continue
            if isinstance(date_value, str):
                try:
                    date_obj = datetime.fromisoformat(date_value)
                except ValueError:
                    try:
                        date_obj = datetime.strptime(date_value, '%Y-%m-%d')
                    except Exception:
                        continue
            else:
                date_obj = date_value

            year = date_obj.year
            entry = year_map[year]
            entry['order_count'] += 1
            entry['total_value'] += float(row.get('total_value') or 0)
            if not entry['latest_date'] or date_obj > entry['latest_date']:
                entry['latest_date'] = date_obj

        orders = []
        for year in sorted(year_map):
            entry = year_map[year]
            orders.append({
                'sales_order_ref': f"{year} Summary",
                'date_entered': entry['latest_date'].strftime('%Y-%m-%d') if entry['latest_date'] else f"{year}-12-31",
                'status_name': f"Orders: {entry['order_count']}",
                'total_value': entry['total_value'],
                'customer_name': 'All Associated'
            })
        return orders

    return [dict(order) for order in orders_rows]


def get_customer_active_rfqs_count_consolidated(customer_ids):
    """Get active RFQ count from multiple customers"""
    if not customer_ids:
        return 0

    placeholders = ','.join('?' for _ in customer_ids)
    query = f"""
        SELECT COUNT(*) as count
        FROM rfqs 
        WHERE customer_id IN ({placeholders}) AND status = 'open'
    """

    result = db_execute(query, customer_ids, fetch='one')
    return result['count'] if result else 0


def get_customer_active_orders_count_consolidated(customer_ids):
    """Get active order count from multiple customers"""
    if not customer_ids:
        return 0

    placeholders = ','.join('?' for _ in customer_ids)
    query = f"""
        SELECT COUNT(*) as count
        FROM sales_orders 
        WHERE customer_id IN ({placeholders}) AND sales_status_id IN (
            SELECT id FROM sales_statuses WHERE status_name LIKE '%active%' OR status_name LIKE '%pending%'
        )
    """

    result = db_execute(query, customer_ids, fetch='one')
    return result['count'] if result else 0

@salespeople_bp.route('/debug_customer_data/<int:customer_id>')
@login_required
def debug_customer_data(customer_id):
    """Debug endpoint to check what's wrong with the 2024 data"""
    try:
        from collections import defaultdict

        all_orders_query = """
            SELECT 
                id, 
                order_number, 
                date_entered, 
                total_value,
                CASE 
                    WHEN date_entered IS NULL THEN 'NULL'
                    WHEN date_entered = '' THEN 'EMPTY'
                    WHEN date_entered LIKE '____-__-__' THEN 'VALID_FORMAT'
                    ELSE 'INVALID_FORMAT' 
                END AS date_format
            FROM sales_orders
            WHERE customer_id = ?
            ORDER BY date_entered DESC
        """
        all_orders = db_execute(all_orders_query, (customer_id,), fetch='all') or []

        date_format_counts = defaultdict(int)
        years_found = defaultdict(int)
        total_by_year = defaultdict(float)

        for order in all_orders:
            date_format_counts[order['date_format']] += 1

            if order['date_entered'] and order['date_format'] == 'VALID_FORMAT':
                try:
                    year = str(order['date_entered']).split('-')[0]
                    years_found[year] += 1
                    total_by_year[year] += float(order.get('total_value') or 0)
                except Exception as e:
                    years_found['ERROR'] += 1

        schema = []
        try:
            schema = db_execute('PRAGMA table_info(sales_orders)', fetch='all') or []
        except Exception:
            pass

        customer = get_customer_by_id(customer_id)
        sample_orders = [dict(order) for order in all_orders[:5]]

        result = {
            'customer': customer['name'] if customer else 'Unknown',
            'order_count': len(all_orders),
            'date_format_counts': dict(date_format_counts),
            'years_found': dict(years_found),
            'total_by_year': dict(total_by_year),
            'schema': [dict(col) for col in schema],
            'sample_orders': sample_orders,
            'current_year': datetime.now().year
        }

        time_periods = ['this_month', 'this_year', 'yearly']
        period_results = {}

        for period in time_periods:
            try:
                orders = get_customer_orders_by_date_range(customer_id, None, None, period)
                period_results[period] = {
                    'count': len(orders),
                    'years_present': defaultdict(int)
                }

                for order in orders:
                    if order.get('date_entered'):
                        try:
                            year = str(order['date_entered']).split('-')[0]
                            period_results[period]['years_present'][year] += 1
                        except Exception:
                            pass

                period_results[period]['years_present'] = dict(period_results[period]['years_present'])
            except Exception as e:
                period_results[period] = {'error': str(e)}

        result['period_results'] = period_results
        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@salespeople_bp.route('/save_customer_notes', methods=['POST'])
@login_required
def save_customer_notes():
    """Save customer notes via AJAX"""
    try:
        data = request.get_json()
        customer_id = data.get('customer_id')
        notes = data.get('notes', '')

        # Validate input
        if not customer_id:
            return jsonify({'success': False, 'error': 'Customer ID is required'}), 400

        # Update notes in database
        db_execute("UPDATE customers SET notes = ? WHERE id = ?", (notes, customer_id), commit=True)

        print(f"DEBUG: Updated notes for customer ID {customer_id}")
        return jsonify({'success': True})

    except Exception as e:
        print(f"DEBUG: Error saving customer notes: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/sales_data')
@login_required
def sales_data(salesperson_id):
    """API endpoint to get sales data for charts and top customers"""
    try:
        from datetime import datetime, timedelta, date
        import time
        import traceback

        timings = {}
        t0 = time.perf_counter()
        print(f"DEBUG: Getting sales data for salesperson {salesperson_id}")

        # Current date references
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        first_day_of_month = today.replace(day=1)

        # Calculate start of current week (Monday)
        days_since_monday = today.weekday()
        start_of_week = today - timedelta(days=days_since_monday)

        # Calculate previous months
        current_month_start = today.replace(day=1)
        prev_month_start = (current_month_start - timedelta(days=1)).replace(day=1)
        prev_prev_month_start = (prev_month_start - timedelta(days=1)).replace(day=1)

        # End dates for each month
        current_month_end = today
        prev_month_end = current_month_start - timedelta(days=1)
        prev_prev_month_end = prev_month_start - timedelta(days=1)

        # Initialize result with empty structures
        result = {
            'yesterday_sales': {'order_count': 0, 'total_value': 0},
            'month_sales': {'order_count': 0, 'total_value': 0},
            'personal_sales': {'labels': [], 'values': [], 'monthly_customers': {}},
            'account_sales': {'labels': [], 'values': [], 'monthly_customers': {}},
            'top_customers_week': [],
            'top_customers_three_months': [],
            'top_customers_all_time': []
        }

        db = None

        try:
            # 1. Get yesterday's sales (unchanged)
            yesterday_str = yesterday.strftime('%Y-%m-%d')
            query = """
                SELECT 
                    COUNT(id) as order_count,
                    SUM(CASE WHEN total_value IS NULL OR total_value::text = '' THEN 0 ELSE total_value END) as total_value
                FROM 
                    sales_orders
                WHERE 
                    salesperson_id = ? AND
                    date_entered = ?
            """

            t_step = time.perf_counter()
            row = db_execute(query, (salesperson_id, yesterday_str), fetch="one")
            if row:
                result['yesterday_sales'] = {
                    'order_count': row['order_count'] if row['order_count'] is not None else 0,
                    'total_value': float(row['total_value'] if row['total_value'] is not None else 0)
                }
            timings['yesterday_sales'] = time.perf_counter() - t_step

            # 2. Get this month's sales (unchanged)
            month_start_str = first_day_of_month.strftime('%Y-%m-%d')
            today_str = today.strftime('%Y-%m-%d')
            query = """
                SELECT 
                    COUNT(id) as order_count,
                    SUM(CASE WHEN total_value IS NULL OR total_value::text = '' THEN 0 ELSE total_value END) as total_value
                FROM 
                    sales_orders
                WHERE 
                    salesperson_id = ? AND
                    date_entered BETWEEN ? AND ?
            """

            t_step = time.perf_counter()
            row = db_execute(query, (salesperson_id, month_start_str, today_str), fetch="one")
            if row:
                result['month_sales'] = {
                    'order_count': row['order_count'] if row['order_count'] is not None else 0,
                    'total_value': float(row['total_value'] if row['total_value'] is not None else 0)
                }
            timings['month_sales'] = time.perf_counter() - t_step

            # 3. Generate month labels for the past 12 months (unchanged)
            # In your Python route, replace the chart data generation section with this:

            # 3. Generate month labels for the past 24 months (changed from 12)
            chart_labels = []
            month_dict = {}  # For mapping month strings to positions

            for i in range(24):  # Changed from 12 to 24
                # Calculate month date (go backwards from current month)
                month_date = (today.replace(day=1) - timedelta(days=30 * i)).replace(day=1)
                month_label = month_date.strftime('%b %Y')  # e.g. "Jan 2025"
                month_key = month_date.strftime('%Y-%m')  # e.g. "2025-01"

                # Add to the start of the lists (to get chronological order)
                chart_labels.insert(0, month_label)
                month_dict[month_key] = 23 - i  # Map SQL month format to array position (changed from 11-i)

            # 4. Personal sales data with customer breakdown
            start_date = (today.replace(day=1) - timedelta(days=730)).strftime(
                '%Y-%m-%d')  # Changed from 365 to 730 days

            # Get monthly totals
            query = """
                SELECT 
                    SUBSTRING(date_entered::text, 1, 7) as month,
                    SUM(CASE WHEN total_value IS NULL OR total_value::text = '' THEN 0 ELSE total_value END) as monthly_value
                FROM 
                    sales_orders
                WHERE 
                    salesperson_id = ? AND
                    date_entered BETWEEN ? AND ?
                GROUP BY 
                    SUBSTRING(date_entered::text, 1, 7)
                ORDER BY 
                    month ASC
            """

            personal_values = [0] * 24  # Changed from 12 to 24
            db = get_db_connection()
            t_step = time.perf_counter()
            rows = db.execute(query, (salesperson_id, start_date, today_str)).fetchall()
            timings['personal_totals'] = time.perf_counter() - t_step

            for row in rows:
                month_key = row['month']
                if month_key in month_dict:
                    idx = month_dict[month_key]
                    try:
                        personal_values[idx] = float(row['monthly_value']) if row['monthly_value'] else 0
                    except (ValueError, TypeError):
                        pass

            # NEW: Get customer breakdown for personal sales
            customer_query = """
                SELECT 
                    SUBSTRING(so.date_entered::text, 1, 7) as month,
                    c.id as customer_id,
                    c.name as customer_name,
                    SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) as total_value,
                    COUNT(so.id) as order_count
                FROM 
                    sales_orders so
                JOIN 
                    customers c ON so.customer_id = c.id
                WHERE 
                    so.salesperson_id = ? AND
                    so.date_entered BETWEEN ? AND ?
                GROUP BY 
                    SUBSTRING(so.date_entered::text, 1, 7), c.id, c.name
                HAVING 
                    SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) > 0
                ORDER BY 
                    month ASC, total_value DESC
            """

            personal_monthly_customers = {}
            t_step = time.perf_counter()
            customer_rows = db.execute(customer_query, (salesperson_id, start_date, today_str)).fetchall()
            timings['personal_customers'] = time.perf_counter() - t_step

            for row in customer_rows:
                month_key = row['month']
                if month_key in month_dict:
                    idx = month_dict[month_key]
                    if idx not in personal_monthly_customers:
                        personal_monthly_customers[idx] = []

                    personal_monthly_customers[idx].append({
                        'customer_id': row['customer_id'],
                        'customer_name': row['customer_name'] or 'Unknown Customer',
                        'total_value': float(row['total_value']) if row['total_value'] else 0,
                        'order_count': int(row['order_count']) if row['order_count'] else 0
                    })

            # Sort customers by value for each month
            for idx in personal_monthly_customers:
                personal_monthly_customers[idx].sort(key=lambda x: x['total_value'], reverse=True)

            result['personal_sales'] = {
                'labels': chart_labels,
                'values': personal_values,
                'monthly_customers': personal_monthly_customers
            }

            # 5. Account sales data with customer breakdown
            customer_query = """
                SELECT id FROM customers 
                WHERE salesperson_id = ?
            """
            t_step = time.perf_counter()
            customer_rows = db.execute(customer_query, (salesperson_id,)).fetchall()
            timings['account_customer_ids'] = time.perf_counter() - t_step

            if customer_rows:
                customer_ids = [row['id'] for row in customer_rows]
                account_values = [0] * 24  # Changed from 12 to 24

                # Build query with placeholders for totals
                placeholders = ','.join(['?'] * len(customer_ids))
                query = f"""
                    SELECT 
                        SUBSTRING(date_entered::text, 1, 7) as month,
                        SUM(CASE WHEN total_value IS NULL OR total_value::text = '' THEN 0 ELSE total_value END) as monthly_value
                    FROM 
                        sales_orders
                    WHERE 
                        customer_id IN ({placeholders}) AND
                        date_entered BETWEEN ? AND ?
                    GROUP BY 
                        SUBSTRING(date_entered::text, 1, 7)
                    ORDER BY 
                        month ASC
                """

                params = customer_ids + [start_date, today_str]
                t_step = time.perf_counter()
                rows = db_execute(query, params, fetch='all')
                timings['account_totals'] = time.perf_counter() - t_step

                for row in rows:
                    month_key = row['month']
                    if month_key in month_dict:
                        idx = month_dict[month_key]
                        try:
                            account_values[idx] = float(row['monthly_value']) if row['monthly_value'] else 0
                        except (ValueError, TypeError):
                            pass

                # NEW: Get customer breakdown for account sales
                customer_breakdown_query = f"""
                    SELECT 
                        SUBSTRING(so.date_entered::text, 1, 7) as month,
                        c.id as customer_id,
                        c.name as customer_name,
                        SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) as total_value,
                        COUNT(so.id) as order_count
                    FROM 
                        sales_orders so
                    JOIN 
                        customers c ON so.customer_id = c.id
                    WHERE 
                        so.customer_id IN ({placeholders}) AND
                        so.date_entered BETWEEN ? AND ?
                GROUP BY 
                    SUBSTRING(so.date_entered::text, 1, 7), c.id, c.name
                HAVING 
                    SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) > 0
                    ORDER BY 
                        month ASC, total_value DESC
                """

                account_monthly_customers = {}
                t_step = time.perf_counter()
                customer_breakdown_rows = db_execute(customer_breakdown_query, params, fetch='all')
                timings['account_customers'] = time.perf_counter() - t_step

                for row in customer_breakdown_rows:
                    month_key = row['month']
                    if month_key in month_dict:
                        idx = month_dict[month_key]
                        if idx not in account_monthly_customers:
                            account_monthly_customers[idx] = []

                        account_monthly_customers[idx].append({
                            'customer_id': row['customer_id'],
                            'customer_name': row['customer_name'] or 'Unknown Customer',
                            'total_value': float(row['total_value']) if row['total_value'] else 0,
                            'order_count': int(row['order_count']) if row['order_count'] else 0
                        })

                # Sort customers by value for each month
                for idx in account_monthly_customers:
                    account_monthly_customers[idx].sort(key=lambda x: x['total_value'], reverse=True)

                result['account_sales'] = {
                    'labels': chart_labels,
                    'values': account_values,
                    'monthly_customers': account_monthly_customers
                }

            # 6-8. Top customers sections (unchanged from your original code)
            # Top customers this week
            week_start_str = start_of_week.strftime('%Y-%m-%d')
            query = """
                SELECT 
                    c.name as customer_name,
                    c.id as customer_id,
                    COUNT(so.id) as order_count,
                    SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) as total_value
                FROM 
                    sales_orders so
                JOIN 
                    customers c ON so.customer_id = c.id
                WHERE 
                    so.salesperson_id = ? AND
                    so.date_entered BETWEEN ? AND ?
                GROUP BY 
                    c.id, c.name
                HAVING 
                    SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) > 0
                ORDER BY 
                    total_value DESC
                LIMIT 5
            """

            t_step = time.perf_counter()
            rows = db.execute(query, (salesperson_id, week_start_str, today_str)).fetchall()
            timings['top_week'] = time.perf_counter() - t_step
            result['top_customers_week'] = [
                {
                    'customer_name': row['customer_name'],
                    'customer_id': row['customer_id'],
                    'order_count': row['order_count'],
                    'total_value': float(row['total_value']) if row['total_value'] else 0
                }
                for row in rows
            ]

            # All-time top customers for this salesperson's accounts
            if customer_rows:
                placeholders = ','.join(['?'] * len(customer_ids))
                query = f"""
                    SELECT 
                        c.name as customer_name,
                        c.id as customer_id,
                        COUNT(so.id) as order_count,
                        SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) as total_value
                    FROM 
                        customers c
                    LEFT JOIN 
                        sales_orders so ON c.id = so.customer_id
                    WHERE 
                        c.salesperson_id = ?
                    GROUP BY 
                        c.id, c.name
                    HAVING 
                        SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) > 0
                    ORDER BY 
                        total_value DESC
                    LIMIT 15
                """

                t_step = time.perf_counter()
                rows = db.execute(query, (salesperson_id,)).fetchall()
                timings['top_all_time'] = time.perf_counter() - t_step
                result['top_customers_all_time'] = [
                    {
                        'customer_name': row['customer_name'],
                        'customer_id': row['customer_id'],
                        'order_count': row['order_count'] if row['order_count'] else 0,
                        'total_value': float(row['total_value']) if row['total_value'] else 0
                    }
                    for row in rows
                ]

            # Top customers for three months with percentage changes (unchanged)
            def get_customer_data_for_month(start_date, end_date):
                """Helper function to get customer sales data for a specific month"""
                query = """
                    SELECT 
                        c.name as customer_name,
                        c.id as customer_id,
                        COUNT(so.id) as order_count,
                        SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) as total_value
                    FROM 
                        sales_orders so
                    JOIN 
                        customers c ON so.customer_id = c.id
                    WHERE 
                        so.salesperson_id = ? AND
                        so.date_entered BETWEEN ? AND ?
                    GROUP BY 
                        c.id, c.name
                    ORDER BY 
                        total_value DESC
                """

                rows = db.execute(query, (
                    salesperson_id, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))).fetchall()

                # Convert to dictionary for easier lookup
                customer_data = {}
                total_all_customers = 0

                for i, row in enumerate(rows):
                    value = float(row['total_value']) if row['total_value'] else 0
                    total_all_customers += value

                    if i < 10:  # Top 10
                        customer_data[row['customer_id']] = {
                            'customer_name': row['customer_name'],
                            'customer_id': row['customer_id'],
                            'order_count': row['order_count'],
                            'total_value': value,
                            'rank': i + 1
                        }
                    else:  # Bundle into "Other"
                        if 'other' not in customer_data:
                            customer_data['other'] = {
                                'customer_name': 'Other',
                                'customer_id': 'other',
                                'order_count': 0,
                                'total_value': 0,
                                'rank': 11
                            }
                        customer_data['other']['order_count'] += row['order_count']
                        customer_data['other']['total_value'] += value

                return customer_data, total_all_customers

            # Get data for all three months
            t_step = time.perf_counter()
            current_data, current_total = get_customer_data_for_month(current_month_start, current_month_end)
            prev_data, prev_total = get_customer_data_for_month(prev_month_start, prev_month_end)
            prev_prev_data, prev_prev_total = get_customer_data_for_month(prev_prev_month_start, prev_prev_month_end)
            timings['top_three_months'] = time.perf_counter() - t_step

            # Combine all customers from all months to get comprehensive list
            all_customer_ids = set()
            all_customer_ids.update(current_data.keys())
            all_customer_ids.update(prev_data.keys())
            all_customer_ids.update(prev_prev_data.keys())

            # Build the result structure
            three_month_result = []

            for customer_id in all_customer_ids:
                current_customer = current_data.get(customer_id,
                                                    {'customer_name': '', 'total_value': 0, 'order_count': 0})
                prev_customer = prev_data.get(customer_id, {'total_value': 0, 'order_count': 0})
                prev_prev_customer = prev_prev_data.get(customer_id, {'total_value': 0, 'order_count': 0})

                # Calculate percentage changes
                current_vs_prev = None
                prev_vs_prev_prev = None

                if prev_customer['total_value'] > 0:
                    current_vs_prev = ((current_customer['total_value'] - prev_customer['total_value']) / prev_customer[
                        'total_value']) * 100
                elif current_customer['total_value'] > 0:
                    current_vs_prev = 100  # New customer or went from 0 to something

                if prev_prev_customer['total_value'] > 0:
                    prev_vs_prev_prev = ((prev_customer['total_value'] - prev_prev_customer['total_value']) /
                                         prev_prev_customer['total_value']) * 100
                elif prev_customer['total_value'] > 0:
                    prev_vs_prev_prev = 100  # New customer or went from 0 to something

                # Get customer name (prioritize current month, then previous months)
                customer_name = current_customer['customer_name']
                if not customer_name:
                    customer_name = prev_customer.get('customer_name', '')
                if not customer_name:
                    customer_name = prev_prev_customer.get('customer_name', 'Unknown')

                three_month_result.append({
                    'customer_id': customer_id,
                    'customer_name': customer_name,
                    'current_month': {
                        'total_value': current_customer['total_value'],
                        'order_count': current_customer['order_count'],
                        'change_percent': current_vs_prev
                    },
                    'prev_month': {
                        'total_value': prev_customer['total_value'],
                        'order_count': prev_customer['order_count'],
                        'change_percent': prev_vs_prev_prev
                    },
                    'prev_prev_month': {
                        'total_value': prev_prev_customer['total_value'],
                        'order_count': prev_prev_customer['order_count']
                    }
                })

            # Sort by current month value and take top entries
            three_month_result.sort(key=lambda x: x['current_month']['total_value'], reverse=True)

            # Add month labels for the frontend
            result['top_customers_three_months'] = {
                'months': {
                    'current': current_month_start.strftime('%b %Y'),
                    'prev': prev_month_start.strftime('%b %Y'),
                    'prev_prev': prev_prev_month_start.strftime('%b %Y')
                },
                'customers': three_month_result[:11]  # Top 10 + Other if it exists
            }

        except Exception as e:
            print(f"DEBUG: Error generating sales data: {str(e)}")
            print(traceback.format_exc())

        finally:
            if db:
                db.close()
            total = time.perf_counter() - t0
            timing_summary = ", ".join(f"{k}={v:.3f}s" for k, v in timings.items())
            print(f"TIMING salespeople.sales_data total={total:.3f}s {timing_summary}")

        print(f"DEBUG: Returning sales data for charts and top customers")
        return jsonify(result)

    except Exception as e:
        print(f"DEBUG: Unhandled exception in sales_data: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@salespeople_bp.route('/contact_details/<int:contact_id>')
@login_required
def contact_details(contact_id):
    """Get details for a specific contact for the offcanvas/modal view"""
    try:
        with db_cursor() as db:
            # Updated query to include status information
            contact = _execute_with_cursor(db, '''
                SELECT c.*, 
                       cu.name as customer_name,
                       cs.name as status_name,
                       cs.color as status_color
                FROM contacts c
                LEFT JOIN customers cu ON c.customer_id = cu.id
                LEFT JOIN contact_statuses cs ON c.status_id = cs.id
                WHERE c.id = ?
            ''', (contact_id,)).fetchone()

            if not contact:
                return jsonify({'success': False, 'error': 'Contact not found'})

            # Get the communications for this contact
            salesperson_id = request.args.get('salesperson_id')
            communications = get_contact_communications(contact_id, salesperson_id)

            # Check if this is a mobile request (you can detect this various ways)
            user_agent = request.headers.get('User-Agent', '').lower()
            is_mobile = any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])

            # If this is an AJAX request, return the appropriate HTML partial
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                template = 'salespeople/contact_details_mobile.html' if is_mobile else 'salespeople/contact_details.html'
                return render_template(
                    template,
                    contact=contact,
                    communications=communications,
                    communication_types=["Email", "Phone", "Meeting", "Video Call", "Other"]
                )
            else:
                # Otherwise redirect back to the contacts page
                return redirect(url_for('salespeople.contacts', salesperson_id=salesperson_id))

    except Exception as e:
        print(f"Error getting contact details: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@salespeople_bp.route('/save_contact_notes', methods=['POST'])
@login_required
def save_contact_notes():
    """Save notes for a contact"""
    try:
        data = request.get_json()
        contact_id = data.get('contact_id')
        notes = data.get('notes')

        if not contact_id:
            return jsonify({'success': False, 'error': 'Contact ID is required'})

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, '''
                UPDATE contacts 
                SET notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (notes, contact_id))

        return jsonify({'success': True})
    except Exception as e:
        print(f"Error saving contact notes: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@salespeople_bp.route('/add_contact_communication/<int:salesperson_id>', methods=['POST'])
@login_required
def add_contact_communication(salesperson_id):
    """Add a new communication record for a contact"""
    try:
        contact_id = request.form.get('contact_id')
        customer_id = request.form.get('customer_id')
        communication_type = request.form.get('communication_type')
        notes = request.form.get('notes')

        if not all([contact_id, customer_id, communication_type, notes]):
            return jsonify({'success': False, 'error': 'Missing required fields'})

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, '''
                INSERT INTO contact_communications 
                (contact_id, customer_id, salesperson_id, communication_type, notes, date)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (contact_id, customer_id, salesperson_id, communication_type, notes))

        return jsonify({'success': True})
    except Exception as e:
        print(f"Error adding contact communication: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@salespeople_bp.route('/update_customer_field', methods=['POST'])
@login_required
def update_customer_field():
    """Update a single customer field via AJAX"""
    try:
        customer_id = request.form.get('customer_id')
        field = request.form.get('field')
        value = request.form.get('value')

        if not customer_id or not field:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        # Validate field name for security
        allowed_fields = ['fleet_size', 'estimated_revenue']
        if field not in allowed_fields:
            return jsonify({'success': False, 'error': 'Invalid field'}), 400

        # Validate and convert value
        try:
            numeric_value = int(float(value)) if value else None
            if numeric_value is not None and numeric_value < 0:
                return jsonify({'success': False, 'error': 'Value must be positive'}), 400
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Invalid numeric value'}), 400

        # Update the customer field in database
        success = update_customer_field_value(customer_id, field, numeric_value)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to update field'}), 500

    except Exception as e:
        print(f"Error updating customer field: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

def generate_breadcrumbs(*crumbs):
    breadcrumbs = []
    for crumb, path in crumbs:
        breadcrumbs.append((crumb, path))
    return breadcrumbs

@salespeople_bp.route('/<int:salesperson_id>/contacts/by-status/<int:status_id>')
@login_required
def contacts_by_status_redirect(salesperson_id, status_id):
    """Redirect to the customer-centric contacts by status view"""
    return redirect(url_for('customers.contacts_by_status', status_id=status_id, salesperson_id=salesperson_id))


@salespeople_bp.route('/<int:salesperson_id>/contacts')
@login_required
def contacts(salesperson_id):
    """View contacts assigned to this salesperson through their customers"""
    try:
        print(f"Looking up salesperson with ID {salesperson_id}")
        salesperson = get_salesperson_by_id(salesperson_id)
        if not salesperson:
            print(f"Salesperson with ID {salesperson_id} not found")
            flash('Salesperson not found!', 'error')
            return redirect(url_for('salespeople.dashboard'))

        # Get all salespeople for dropdown
        try:
            all_salespeople = get_all_salespeople_with_contact_counts()
            print(f"Found {len(all_salespeople)} salespeople for dropdown")
        except Exception as e:
            print(f"Error getting all salespeople: {e}")
            all_salespeople = []

        # Check if this is a first visit (no filter parameters at all in URL)
        is_first_visit = not any(key in request.args for key in [
            'search', 'customer_filter', 'status_filter', 'customer_status_filter',
            'name_filter', 'job_title_filter', 'my_communications_only',
            'call_list_only', 'sort', 'order'
        ])

        # REDIRECT on first visit with default filters in URL
        if is_first_visit:
            print("First visit - redirecting with default filters")
            return redirect(url_for('salespeople.contacts',
                                    salesperson_id=salesperson_id,
                                    customer_status_filter=['target', 'contact identified', 'active customer'],
                                    status_filter=['new', 'active', 'no status'],
                                    sort='days_since_contact',
                                    order='desc'))

        # NOW get the filters from URL (after redirect they'll be there)
        status_filter = request.args.getlist('status_filter')
        customer_status_filter = request.args.getlist('customer_status_filter')

        print(f"Status filter from URL: {status_filter}")
        print(f"Customer status filter from URL: {customer_status_filter}")

        # Other filters
        search_term = request.args.get('search', '')
        customer_filter = request.args.get('customer_filter', '')
        name_filter = request.args.get('name_filter', '')
        job_title_filter = request.args.get('job_title_filter', '')
        my_communications_only = request.args.get('my_communications_only', '') == 'true'
        call_list_only = request.args.get('call_list_only', '') == 'true'

        # Get sort parameters
        current_sort = request.args.get('sort', 'days_since_contact')
        current_order = request.args.get('order', 'desc')

        # Get the customers assigned to this salesperson for the dropdown filter
        customers = get_salesperson_customers(salesperson_id)

        # Get all contact statuses for the filter dropdown
        contact_statuses = get_all_contact_statuses()

        # Get customer statuses for the filter dropdown
        customer_statuses = get_customer_status_options()

        print(f"About to call get_salesperson_contacts with filters:")
        print(f"  - status_filter: {status_filter}")
        print(f"  - customer_status_filter: {customer_status_filter}")

        # Get contacts for this salesperson with filters and sorting
        contacts = get_salesperson_contacts(
            salesperson_id,
            search_term,
            customer_filter,
            status_filter,
            customer_status_filter,
            current_sort,
            current_order,
            name_filter=name_filter,
            job_title_filter=job_title_filter,
            my_communications_only=my_communications_only,
            call_list_only=call_list_only
        )

        print(f"get_salesperson_contacts returned {len(contacts)} contacts")

        # Get communication types for new communication form
        communication_types = ["Email", "Phone", "Meeting", "Video Call", "Other"]

        # Get status summary for dashboard widget
        status_counts = get_status_counts_for_salesperson(salesperson_id)

        # Generate breadcrumbs
        breadcrumbs = generate_breadcrumbs(
            ('Home', url_for('index')),
            ('Salespeople', url_for('salespeople.dashboard')),
            (salesperson['name'], url_for('salespeople.activity', salesperson_id=salesperson_id)),
            ('Contacts', url_for('salespeople.contacts', salesperson_id=salesperson_id))
        )

        # Get call list contact IDs for this salesperson
        call_list_contact_ids = get_call_list_contact_ids(salesperson_id)

        # Mark which contacts are on the call list
        for contact in contacts:
            contact['is_on_call_list'] = contact['id'] in call_list_contact_ids

        return render_template(
            'salespeople/contacts.html',
            salesperson=salesperson,
            contacts=contacts,
            all_salespeople=all_salespeople,
            customers=customers,
            contact_statuses=contact_statuses,
            customer_statuses=customer_statuses,
            communication_types=communication_types,
            status_counts=status_counts,
            customer_status_counts=[],
            search_term=search_term,
            customer_filter=customer_filter,
            status_filter=status_filter,
            customer_status_filter=customer_status_filter,
            name_filter=name_filter,
            job_title_filter=job_title_filter,
            my_communications_only=my_communications_only,
            current_sort=current_sort,
            current_order=current_order,
            call_list_only=call_list_only,
            breadcrumbs=breadcrumbs
        )

    except Exception as e:
        print(f"Exception in contacts route: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('salespeople.dashboard'))

@salespeople_bp.route('/<int:salesperson_id>/contact-status-summary')
@login_required
def contact_status_summary(salesperson_id):
    """Get status summary for salesperson dashboard widget"""
    try:
        salesperson = get_salesperson_by_id(salesperson_id)
        if not salesperson:
            return jsonify({'error': 'Salesperson not found'}), 404

        status_counts = get_status_counts_for_salesperson(salesperson_id)

        if request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': True, 'status_counts': status_counts})

        return render_template(
            'salespeople/partials/status_summary.html',
            status_counts=status_counts
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/engagement-metrics')
@login_required
def engagement_metrics(salesperson_id):
    """API endpoint to get engagement metrics for a salesperson"""
    try:
        print(f"DEBUG: Getting engagement metrics for salesperson {salesperson_id}")

        # Verify salesperson exists
        salesperson = get_salesperson_by_id(salesperson_id)
        if not salesperson:
            print(f"DEBUG: Salesperson {salesperson_id} not found")
            return jsonify({'success': False, 'error': 'Salesperson not found'}), 404

        print(f"DEBUG: Found salesperson: {salesperson['name']}")

        # Get filter parameters and settings
        customer_status_filter = request.args.getlist('customer_status')
        contact_status_filter = request.args.getlist('contact_status')
        overdue_threshold = request.args.get('overdue_threshold', type=int)

        # Convert to integers if provided
        customer_status_filter = [int(x) for x in customer_status_filter if
                                  x.isdigit()] if customer_status_filter else None
        contact_status_filter = [int(x) for x in contact_status_filter if
                                 x.isdigit()] if contact_status_filter else None

        print(
            f"DEBUG: Filters - customer_status: {customer_status_filter}, contact_status: {contact_status_filter}, overdue_threshold: {overdue_threshold}")

        # Get engagement metrics
        print("DEBUG: Calling get_engagement_metrics...")
        metrics = get_engagement_metrics(salesperson_id, customer_status_filter, contact_status_filter,
                                         overdue_threshold)
        print(f"DEBUG: Got metrics: {metrics}")

        # Format overdue contacts list for display
        overdue_contacts_html = ""
        if metrics['overdue_contacts_list']:
            print(f"DEBUG: Processing {len(metrics['overdue_contacts_list'])} overdue contacts")
            for contact in metrics['overdue_contacts_list']:
                days_display = f"{contact['days_ago']} days ago" if contact['days_ago'] else "Never contacted"
                contact_info = f"{contact['contact_name']} ({contact['customer_name']})"

                overdue_contacts_html += f'''
                <div class="d-flex justify-content-between align-items-center mb-2 p-2 bg-light rounded">
                    <div>
                        <small><strong>{contact_info}</strong></small>
                        <br>
                        <small class="text-muted">Last contact: {contact['last_contact_date']}</small>
                    </div>
                    <div class="text-end">
                        <small class="text-danger"><strong>{days_display}</strong></small>
                        <br>
                    </div>
                </div>
                '''

        if not overdue_contacts_html:
            overdue_contacts_html = '<small class="text-muted">No overdue contacts</small>'

        # Generate urgency alerts
        urgency_html = ""
        if metrics['overdue_contacts'] > 0:
            urgency_html += f'''
            <div class="alert alert-warning alert-sm py-2 mb-2">
                <i class="bi bi-exclamation-triangle"></i> 
                <strong>{metrics['overdue_contacts']}</strong> contacts overdue (>{metrics['settings']['overdue_threshold_days']} days)
            </div>
            '''

        if metrics['days_since_last'] and metrics['days_since_last'] > 7:
            urgency_html += f'''
            <div class="alert alert-info alert-sm py-2 mb-2">
                <i class="bi bi-clock"></i> 
                Last contact was <strong>{metrics['days_since_last']} days</strong> ago
            </div>
            '''

        result = {
            'success': True,
            'days_since_last': metrics['days_since_last'],
            'avg_contact_frequency': metrics['avg_contact_frequency'],
            'contacts_this_week': metrics['contacts_this_week'],
            'overdue_contacts': metrics['overdue_contacts'],
            'total_customers': metrics['total_customers'],
            'overdue_contacts_list': overdue_contacts_html,
            'urgency_alerts': urgency_html,
            'settings': metrics['settings']
        }

        print(f"DEBUG: Returning result: {result}")
        return jsonify(result)

    except Exception as e:
        print(f"ERROR: Exception in engagement_metrics: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/engagement-settings', methods=['POST'])
@login_required
def save_engagement_settings_endpoint(salesperson_id):
    """Save engagement settings for a salesperson"""
    try:
        data = request.get_json()

        overdue_threshold = data.get('overdue_threshold_days', 14)
        customer_status_filter = data.get('customer_status_filter')
        contact_status_filter = data.get('contact_status_filter')

        # Save settings
        save_engagement_settings(salesperson_id, overdue_threshold, customer_status_filter, contact_status_filter)

        return jsonify({'success': True})

    except Exception as e:
        print(f"ERROR: Exception in save_engagement_settings: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/engagement-settings', methods=['GET'])
@login_required
def get_engagement_settings_endpoint(salesperson_id):
    """Get engagement settings for a salesperson"""
    try:
        settings = get_engagement_settings(salesperson_id)
        return jsonify({'success': True, 'settings': settings})

    except Exception as e:
        print(f"ERROR: Exception in get_engagement_settings: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@salespeople_bp.route('/toggle-active', methods=['POST'])
@login_required
def toggle_active():
    """Toggle salesperson active status"""
    try:
        data = request.get_json()
        salesperson_id = data.get('salesperson_id')
        is_active = data.get('is_active')

        if salesperson_id is None or is_active is None:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        success = toggle_salesperson_active(salesperson_id, is_active)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to update status'}), 500

    except Exception as e:
        print(f"Error toggling salesperson active status: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Add these two endpoints to your routes/salespeople.py file
# Place them anywhere in the file, perhaps after the engagement settings endpoints

@salespeople_bp.route('/customer-statuses')
@login_required
def get_customer_statuses():
    """Get all customer statuses for filtering"""
    try:
        results = db_execute('''
            SELECT id, status 
            FROM customer_status 
            ORDER BY status
        ''', fetch='all')

        statuses = []
        for row in results:
            statuses.append({
                'id': row['id'],
                'name': row['status'],  # 'status' column contains the name
                'color': '#6c757d'  # Default color since no color column
            })

        return jsonify({'success': True, 'statuses': statuses})

    except Exception as e:
        print(f"ERROR: Exception in get_customer_statuses: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/contact-statuses')
@login_required
def get_contact_statuses():
    """Get all contact statuses for filtering"""
    try:
        db = get_db_connection()

        # contact_statuses table has: id, name, color, is_active, sort_order
        results = db_execute('''
            SELECT id, name, color 
            FROM contact_statuses 
            WHERE is_active = TRUE
            ORDER BY sort_order, name
        ''', fetch='all')

        db.close()

        statuses = []
        for row in results:
            statuses.append({
                'id': row['id'],
                'name': row['name'],
                'color': row['color'] or '#6c757d'  # Use default if color is null
            })

        return jsonify({'success': True, 'statuses': statuses})

    except Exception as e:
        print(f"ERROR: Exception in get_contact_statuses: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Create a simple test route to debug this issue
# Add this as a new route in your salespeople blueprint

@salespeople_bp.route('/<int:salesperson_id>/debug_communications')
@login_required
def debug_communications(salesperson_id):
    """Debug route to test communication queries"""

    db = get_db_connection()

    # Test contact ID that you know has communications from other salespeople
    test_contact_id = 136

    results = {}

    # 1. Test the individual subqueries that are used in your main query
    try:
        # Communication count
        count_result = db.execute(
            "SELECT COUNT(*) as count FROM contact_communications WHERE contact_id = ?",
            (test_contact_id,)
        ).fetchone()
        results['count_subquery'] = count_result['count'] if count_result else 0

        # Latest date
        date_result = db.execute(
            "SELECT MAX(date) as latest_date FROM contact_communications WHERE contact_id = ?",
            (test_contact_id,)
        ).fetchone()
        results['date_subquery'] = date_result['latest_date'] if date_result else None

        # Latest notes
        notes_result = db.execute(
            "SELECT notes FROM contact_communications WHERE contact_id = ? ORDER BY date DESC, id DESC LIMIT 1",
            (test_contact_id,)
        ).fetchone()
        results['notes_subquery'] = notes_result['notes'] if notes_result else None

        # Latest type
        type_result = db.execute(
            "SELECT communication_type FROM contact_communications WHERE contact_id = ? ORDER BY date DESC, id DESC LIMIT 1",
            (test_contact_id,)
        ).fetchone()
        results['type_subquery'] = type_result['communication_type'] if type_result else None

    except Exception as e:
        results['subquery_error'] = str(e)

    # 2. Test your actual main query for just this contact
    try:
        main_query = """
            SELECT 
                c.id, 
                c.name, 
                (
                    SELECT COUNT(*) 
                    FROM contact_communications 
                    WHERE contact_id = c.id
                ) as communication_count,
                (
                    SELECT MAX(date) 
                    FROM contact_communications 
                    WHERE contact_id = c.id
                ) as latest_communication_date,
                (
                    SELECT notes 
                    FROM contact_communications 
                    WHERE contact_id = c.id 
                    ORDER BY date DESC, id DESC LIMIT 1
                ) as latest_update,
                (
                    SELECT communication_type 
                    FROM contact_communications 
                    WHERE contact_id = c.id 
                    ORDER BY date DESC, id DESC LIMIT 1
                ) as latest_communication_type
            FROM contacts c
            JOIN customers cu ON c.customer_id = cu.id
            WHERE c.id = ? AND cu.salesperson_id = ?
        """

        main_result = db.execute(main_query, (test_contact_id, salesperson_id)).fetchone()
        if main_result:
            results['main_query'] = dict(main_result)
        else:
            results['main_query'] = "No results - contact not found or doesn't belong to this salesperson"

    except Exception as e:
        results['main_query_error'] = str(e)

    # 3. Get all communications for this contact
    try:
        all_comms = db.execute(
            "SELECT id, date, salesperson_id, communication_type, notes FROM contact_communications WHERE contact_id = ? ORDER BY date DESC",
            (test_contact_id,)
        ).fetchall()
        results['all_communications'] = [dict(comm) for comm in all_comms]

    except Exception as e:
        results['communications_error'] = str(e)

    # 4. Check contact and customer relationship
    try:
        contact_info = db.execute(
            "SELECT c.id, c.name, cu.name as customer_name, cu.salesperson_id FROM contacts c JOIN customers cu ON c.customer_id = cu.id WHERE c.id = ?",
            (test_contact_id,)
        ).fetchone()
        results['contact_info'] = dict(contact_info) if contact_info else "Contact not found"

    except Exception as e:
        results['contact_info_error'] = str(e)

    db.close()

    # Return results as JSON for easy viewing
    from flask import jsonify
    return jsonify(results)


# Add these routes to your routes/salespeople.py file

@salespeople_bp.route('/<int:salesperson_id>/bulk-change-status', methods=['POST'])
@login_required
def bulk_change_status(salesperson_id):
    """Bulk change contact status"""
    try:
        data = request.get_json()
        contact_ids = data.get('contact_ids', [])
        new_status_id = data.get('status_id')

        if not contact_ids or not new_status_id:
            return jsonify({'success': False, 'error': 'Missing contact IDs or status ID'}), 400

        # Validate that contacts belong to this salesperson's customers
        db = get_db_connection()

        # Check permissions
        placeholders = ','.join(['?' for _ in contact_ids])
        check_query = f"""
            SELECT c.id 
            FROM contacts c
            JOIN customers cu ON c.customer_id = cu.id
            WHERE c.id IN ({placeholders}) AND cu.salesperson_id = ?
        """

        valid_contacts = db_execute(check_query, contact_ids + [salesperson_id], fetch='all')
        valid_contact_ids = [row['id'] for row in valid_contacts]

        if len(valid_contact_ids) != len(contact_ids):
            return jsonify({'success': False, 'error': 'Some contacts do not belong to this salesperson'}), 403

        # Update the status for all valid contacts
        placeholders = ','.join(['?' for _ in valid_contact_ids])
        update_query = f"""
            UPDATE contacts 
            SET status_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
        """

        cursor = db.execute(update_query, [new_status_id] + valid_contact_ids)
        updated_count = cursor.rowcount

        db.commit()
        db.close()

        return jsonify({
            'success': True,
            'message': f'Successfully updated status for {updated_count} contacts'
        })

    except Exception as e:
        print(f"Error in bulk_change_status: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/bulk-add-communication', methods=['POST'])
@login_required
def bulk_add_communication(salesperson_id):
    """Bulk add communication records"""
    try:
        data = request.get_json()
        contact_ids = data.get('contact_ids', [])
        communication_type = data.get('communication_type')
        notes = data.get('notes', '')

        if not contact_ids or not communication_type:
            return jsonify({'success': False, 'error': 'Missing contact IDs or communication type'}), 400

        db = get_db_connection()

        # Get contact and customer info for validation
        placeholders = ','.join(['?' for _ in contact_ids])
        contact_query = f"""
            SELECT c.id, c.customer_id, cu.salesperson_id
            FROM contacts c
            JOIN customers cu ON c.customer_id = cu.id
            WHERE c.id IN ({placeholders}) AND cu.salesperson_id = ?
        """

        valid_contacts = db_execute(contact_query, contact_ids + [salesperson_id], fetch='all')

        if len(valid_contacts) != len(contact_ids):
            return jsonify({'success': False, 'error': 'Some contacts do not belong to this salesperson'}), 403

        # Insert communication records
        insert_count = 0
        for contact in valid_contacts:
            db.execute('''
                INSERT INTO contact_communications 
                (contact_id, customer_id, salesperson_id, communication_type, notes, date)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (contact['id'], contact['customer_id'], salesperson_id, communication_type, notes))
            insert_count += 1

        db.commit()
        db.close()

        return jsonify({
            'success': True,
            'message': f'Successfully logged {communication_type} communication for {insert_count} contacts'
        })

    except Exception as e:
        print(f"Error in bulk_add_communication: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/bulk-add-notes', methods=['POST'])
@login_required
def bulk_add_notes(salesperson_id):
    """Bulk add or append notes to contacts"""
    try:
        data = request.get_json()
        contact_ids = data.get('contact_ids', [])
        notes = data.get('notes', '')
        append_mode = data.get('append', False)  # Whether to append or replace

        if not contact_ids or not notes:
            return jsonify({'success': False, 'error': 'Missing contact IDs or notes'}), 400

        db = get_db_connection()

        # Validate contacts belong to this salesperson
        placeholders = ','.join(['?' for _ in contact_ids])
        check_query = f"""
            SELECT c.id, c.notes
            FROM contacts c
            JOIN customers cu ON c.customer_id = cu.id
            WHERE c.id IN ({placeholders}) AND cu.salesperson_id = ?
        """

        valid_contacts = db_execute(check_query, contact_ids + [salesperson_id], fetch='all')

        if len(valid_contacts) != len(contact_ids):
            return jsonify({'success': False, 'error': 'Some contacts do not belong to this salesperson'}), 403

        # Update notes for each contact
        updated_count = 0
        for contact in valid_contacts:
            if append_mode and contact['notes']:
                # Append to existing notes
                new_notes = f"{contact['notes']}\n\n{notes}"
            else:
                # Replace notes
                new_notes = notes

            db.execute('''
                UPDATE contacts 
                SET notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (new_notes, contact['id']))
            updated_count += 1

        db.commit()
        db.close()

        action = "appended to" if append_mode else "updated"
        return jsonify({
            'success': True,
            'message': f'Successfully {action} notes for {updated_count} contacts'
        })

    except Exception as e:
        print(f"Error in bulk_add_notes: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/bulk-assign-user', methods=['POST'])
@login_required
def bulk_assign_user(salesperson_id):
    """Bulk assign contacts to a different user/salesperson"""
    try:
        data = request.get_json()
        contact_ids = data.get('contact_ids', [])
        new_salesperson_id = data.get('new_salesperson_id')

        if not contact_ids or not new_salesperson_id:
            return jsonify({'success': False, 'error': 'Missing contact IDs or new salesperson ID'}), 400

        # Validate new salesperson exists
        new_salesperson = get_salesperson_by_id(new_salesperson_id)
        if not new_salesperson:
            return jsonify({'success': False, 'error': 'Invalid salesperson ID'}), 400

        db = get_db_connection()

        # Get customer IDs for the contacts that belong to current salesperson
        placeholders = ','.join(['?' for _ in contact_ids])
        customer_query = f"""
            SELECT DISTINCT c.customer_id
            FROM contacts c
            JOIN customers cu ON c.customer_id = cu.id
            WHERE c.id IN ({placeholders}) AND cu.salesperson_id = ?
        """

        customer_results = db_execute(customer_query, contact_ids + [salesperson_id], fetch='all')
        customer_ids = [row['customer_id'] for row in customer_results]

        if not customer_ids:
            return jsonify({'success': False, 'error': 'No valid contacts found'}), 403

        # Update the customers to be assigned to the new salesperson
        # This will automatically reassign all contacts under those customers
        customer_placeholders = ','.join(['?' for _ in customer_ids])
        update_query = f"""
            UPDATE customers 
            SET salesperson_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({customer_placeholders})
        """

        cursor = db.execute(update_query, [new_salesperson_id] + customer_ids)
        updated_customers = cursor.rowcount

        db.commit()
        db.close()

        return jsonify({
            'success': True,
            'message': f'Successfully reassigned {updated_customers} customers (and their contacts) to {new_salesperson["name"]}'
        })

    except Exception as e:
        print(f"Error in bulk_assign_user: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/bulk-export', methods=['POST'])
@login_required
def bulk_export_contacts(salesperson_id):
    """Export selected contacts to CSV"""
    try:
        data = request.get_json()
        contact_ids = data.get('contact_ids', [])

        if not contact_ids:
            return jsonify({'success': False, 'error': 'No contacts selected'}), 400

        db = get_db_connection()

        # Get detailed contact information
        placeholders = ','.join(['?' for _ in contact_ids])
        export_query = f"""
            SELECT 
                c.name,
                c.second_name,
                c.email,
                c.phone,
                c.job_title,
                c.notes,
                cu.name as customer_name,
                cs.status as customer_status,
                st.name as contact_status,
                st.color as status_color,
                (
                    SELECT COUNT(*) 
                    FROM contact_communications 
                    WHERE contact_id = c.id
                ) as communication_count,
                (
                    SELECT MAX(date) 
                    FROM contact_communications 
                    WHERE contact_id = c.id
                ) as latest_communication_date,
                (
                    SELECT communication_type 
                    FROM contact_communications 
                    WHERE contact_id = c.id 
                    ORDER BY date DESC, id DESC LIMIT 1
                ) as latest_communication_type
            FROM contacts c
            JOIN customers cu ON c.customer_id = cu.id
            LEFT JOIN customer_status cs ON cu.status = cs.id
            LEFT JOIN contact_statuses st ON c.status_id = st.id
            WHERE c.id IN ({placeholders}) AND cu.salesperson_id = ?
            ORDER BY c.name
        """

        contacts = db_execute(export_query, contact_ids + [salesperson_id], fetch='all')
        db.close()

        if not contacts:
            return jsonify({'success': False, 'error': 'No valid contacts found for export'}), 404

        # Create CSV data
        import csv
        import io
        from datetime import datetime

        output = io.StringIO()
        writer = csv.writer(output)

        # Write headers
        headers = [
            'Full Name', 'Email', 'Phone', 'Job Title', 'Customer', 'Customer Status',
            'Contact Status', 'Communication Count', 'Latest Communication Date',
            'Latest Communication Type', 'Notes'
        ]
        writer.writerow(headers)

        # Write data rows
        for contact in contacts:
            full_name = f"{contact['name']}"
            if contact['second_name']:
                full_name += f" {contact['second_name']}"

            writer.writerow([
                full_name,
                contact['email'] or '',
                contact['phone'] or '',
                contact['job_title'] or '',
                contact['customer_name'] or '',
                contact['customer_status'] or '',
                contact['contact_status'] or '',
                contact['communication_count'] or 0,
                contact['latest_communication_date'] or '',
                contact['latest_communication_type'] or '',
                contact['notes'] or ''
            ])

        csv_content = output.getvalue()
        output.close()

        # Return CSV data that can be downloaded by frontend
        return jsonify({
            'success': True,
            'csv_data': csv_content,
            'filename': f'contacts_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv',
            'contact_count': len(contacts)
        })

    except Exception as e:
        print(f"Error in bulk_export_contacts: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Helper function to get all salespeople for assignment dropdown
@salespeople_bp.route('/all-salespeople')
@login_required
def get_all_salespeople_for_assignment():
    """Get all salespeople for bulk assignment dropdown"""
    try:
        salespeople = get_salespeople()
        return jsonify({
            'success': True,
            'salespeople': [{'id': sp['id'], 'name': sp['name']} for sp in salespeople]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# Updated Flask routes that use your existing functions

# Updated Flask routes that use your existing functions

@salespeople_bp.route('/bulk_tag_action', methods=['POST'])
@login_required
def bulk_tag_action():
    """Handle bulk tag operations (add/remove tags from multiple customers)"""
    try:
        data = request.get_json()

        action = data.get('action')  # 'add' or 'remove'
        tag_name = data.get('tag_name', '').strip()
        customer_ids = data.get('customer_ids', [])
        salesperson_id = data.get('salesperson_id')

        if not action or action not in ['add', 'remove']:
            return jsonify({'success': False, 'error': 'Invalid action'}), 400

        if not tag_name:
            return jsonify({'success': False, 'error': 'Tag name is required'}), 400

        if not customer_ids:
            return jsonify({'success': False, 'error': 'No customers selected'}), 400

        # Validate that all customers belong to the salesperson
        if salesperson_id:
            salesperson = get_salesperson_by_id(salesperson_id)
            if not salesperson:
                return jsonify({'success': False, 'error': 'Invalid salesperson'}), 400

        affected_count = 0

        if action == 'add':
            # Use your existing insert_customer_tags function for each customer
            for customer_id in customer_ids:
                try:
                    # Check if customer already has this tag
                    db = get_db_connection()
                    existing = db.execute('''
                        SELECT 1 
                        FROM customer_industry_tags cit
                        JOIN industry_tags it ON cit.tag_id = it.id
                        WHERE cit.customer_id = ? AND LOWER(it.tag) = LOWER(?)
                    ''', (customer_id, tag_name)).fetchone()
                    db.close()

                    if not existing:
                        insert_customer_tags(customer_id, [tag_name])
                        affected_count += 1

                except Exception as e:
                    print(f"Error adding tag to customer {customer_id}: {e}")
                    continue

        else:  # remove
            # For remove, we need to find the tag ID and use delete_customer_tag
            db = get_db_connection()
            try:
                tag_row = db.execute('SELECT id FROM industry_tags WHERE LOWER(tag) = LOWER(?)', (tag_name,)).fetchone()
                if tag_row:
                    tag_id = tag_row['id']

                    for customer_id in customer_ids:
                        try:
                            # Check if customer has this tag
                            existing = db.execute(
                                'SELECT id FROM customer_industry_tags WHERE customer_id = ? AND tag_id = ?',
                                (customer_id, tag_id)
                            ).fetchone()

                            if existing:
                                delete_customer_tag(customer_id, tag_id)
                                affected_count += 1

                        except Exception as e:
                            print(f"Error removing tag from customer {customer_id}: {e}")
                            continue
            finally:
                db.close()

        return jsonify({
            'success': True,
            'affected_count': affected_count,
            'action': action,
            'tag_name': tag_name
        })

    except Exception as e:
        print(f"Error in bulk_tag_action: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/available_tags')
@login_required
def available_tags():
    """Get all available tags in a flat list for autocomplete"""
    try:
        # Use your existing get_all_tags function and flatten the hierarchy
        tag_tree = get_all_tags()

        def flatten_tags(tag_list):
            """Recursively flatten the hierarchical tag structure"""
            flat_tags = []
            for tag in tag_list:
                flat_tags.append(tag['name'])
                if tag.get('children'):
                    flat_tags.extend(flatten_tags(tag['children']))
            return flat_tags

        flat_tags = flatten_tags(tag_tree)
        # Remove duplicates and sort
        unique_tags = sorted(list(set(flat_tags)))

        return jsonify({
            'success': True,
            'tags': unique_tags
        })
    except Exception as e:
        print(f"Error getting available tags: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/tag_statistics')
@login_required
def tag_statistics():
    """Get tag usage statistics using existing hierarchy"""
    try:
        # Use your existing get_all_tags function
        tag_tree = get_all_tags()

        def flatten_with_counts(tag_list, level=0):
            """Flatten tags but preserve hierarchy info and counts"""
            stats = []
            for tag in tag_list:
                # Only include tags that have customers
                if tag['customer_count'] > 0:
                    stats.append({
                        'tag': '  ' * level + tag['name'],  # Indent based on level
                        'count': tag['customer_count'],
                        'level': level,
                        'has_children': len(tag.get('children', [])) > 0
                    })

                # Add children
                if tag.get('children'):
                    stats.extend(flatten_with_counts(tag['children'], level + 1))

            return stats

        stats = flatten_with_counts(tag_tree)

        # Get total customer count
        db = get_db_connection()
        try:
            total_result = db.execute('SELECT COUNT(*) as count FROM customers').fetchone()
            total_customers = total_result['count'] if total_result else 0
        finally:
            db.close()

        return jsonify({
            'success': True,
            'statistics': stats,
            'total_customers': total_customers
        })
    except Exception as e:
        print(f"Error getting tag statistics: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Add this route to your routes/salespeople.py file

@salespeople_bp.route('/<int:salesperson_id>/recent_communications')
@login_required
def recent_communications(salesperson_id):
    """API endpoint to get recent communications for the activity dashboard, grouped by company"""
    try:
        import time
        t0 = time.perf_counter()
        print(f"DEBUG: Getting recent communications for salesperson {salesperson_id}")

        # Verify salesperson exists
        t_step = time.perf_counter()
        salesperson = get_salesperson_by_id(salesperson_id)
        if not salesperson:
            print(f"DEBUG: Salesperson {salesperson_id} not found")
            return jsonify({'success': False, 'error': 'Salesperson not found'}), 404
        t_salesperson = time.perf_counter() - t_step

        # Get target date from query parameter, default to None (which will use business day logic)
        target_date_str = request.args.get('date')
        print(f"DEBUG: Target date from request: {target_date_str}")

        # Get recent communications (now grouped by company)
        t_step = time.perf_counter()
        communications_data = get_salesperson_recent_communications(salesperson_id, target_date_str)
        t_data = time.perf_counter() - t_step

        print(f"DEBUG: Retrieved communications for {communications_data['target_date_formatted']}")
        print(f"DEBUG: Total communications: {communications_data['total_count']}")
        print(f"DEBUG: Companies with communications: {list(communications_data['communications'].keys())}")

        # Get list of companies that had communications (for reference)
        companies_with_comms = list(communications_data['communications'].keys())

        result = {
            'success': True,
            'communications': communications_data['communications'],
            'target_date': communications_data['target_date'],
            'target_date_formatted': communications_data['target_date_formatted'],
            'total_count': communications_data['total_count'],
            'companies_with_communications': companies_with_comms,
            'company_counts': communications_data.get('company_counts', {})
        }

        if 'error' in communications_data:
            result['warning'] = communications_data['error']

        print(f"DEBUG: Returning communications data with {len(companies_with_comms)} companies")
        total = time.perf_counter() - t0
        print(f"TIMING salespeople.recent_communications total={total:.3f}s salesperson={t_salesperson:.3f}s data={t_data:.3f}s")
        return jsonify(result)

    except Exception as e:
        print(f"DEBUG: Exception in recent_communications: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/monthly_breakdown/<int:month_index>')
@login_required
def monthly_breakdown(salesperson_id, month_index):
    """API endpoint to get detailed part-level breakdown for a specific month"""
    try:
        from datetime import datetime, timedelta
        import traceback

        # Get view type and customer filter from query parameters
        view_type = request.args.get('view', 'personal')  # 'personal' or 'account'
        customer_id = request.args.get('customer_id', None)  # Optional customer filter

        print(
            f"DEBUG: Getting monthly breakdown for salesperson {salesperson_id}, month {month_index}, view {view_type}, customer {customer_id}")

        # Calculate the target month based on month_index (0 = 23 months ago, 23 = current month)
        today = datetime.now().date()
        months_back = 23 - month_index
        target_month_start = (today.replace(day=1) - timedelta(days=30 * months_back)).replace(day=1)

        if months_back == 0:
            target_month_end = today
        else:
            next_month = target_month_start + timedelta(days=32)
            target_month_end = next_month.replace(day=1) - timedelta(days=1)

        month_label = target_month_start.strftime('%B %Y')

        print(f"DEBUG: Target month: {target_month_start} to {target_month_end} ({month_label})")

        db = get_db_connection()

        # NEW: If customer_id is provided, get consolidated IDs
        if customer_id:
            consolidated_customers = get_consolidated_customer_ids(salesperson_id)

            # Find the consolidated group for this customer
            all_customer_ids = [customer_id]  # Default

            for main_id, customer_group in consolidated_customers.items():
                if int(customer_id) in customer_group['all_customer_ids']:
                    all_customer_ids = customer_group['all_customer_ids']
                    print(f"DEBUG: Found consolidated group with {len(all_customer_ids)} customers: {all_customer_ids}")
                    break

            # Query using ALL consolidated customer IDs
            placeholders = ','.join(['?' for _ in all_customer_ids])
            query = f"""
                SELECT 
                    c.id as customer_id,
                    c.name as customer_name,
                    sol.base_part_number,
                    SUM(sol.quantity) as total_quantity,
                    AVG(sol.price) as avg_unit_price,
                    SUM(sol.quantity * sol.price) as total_value
                FROM 
                    sales_orders so
                JOIN 
                    customers c ON so.customer_id = c.id
                JOIN 
                    sales_order_lines sol ON so.id = sol.sales_order_id
                WHERE 
                    so.customer_id IN ({placeholders}) AND
                    so.date_entered BETWEEN ? AND ?
                GROUP BY 
                    c.id, c.name, sol.base_part_number
                HAVING 
                    SUM(sol.quantity * sol.price) > 0
                ORDER BY 
                    c.name ASC, total_value DESC
            """

            params = all_customer_ids + [
                target_month_start.strftime('%Y-%m-%d'),
                target_month_end.strftime('%Y-%m-%d')
            ]

        elif view_type == 'personal':
            # Personal sales - only orders directly made by this salesperson
            query = """
                SELECT 
                    c.id as customer_id,
                    c.name as customer_name,
                    sol.base_part_number,
                    SUM(sol.quantity) as total_quantity,
                    AVG(sol.price) as avg_unit_price,
                    SUM(sol.quantity * sol.price) as total_value
                FROM 
                    sales_orders so
                JOIN 
                    customers c ON so.customer_id = c.id
                JOIN 
                    sales_order_lines sol ON so.id = sol.sales_order_id
                WHERE 
                    so.salesperson_id = ? AND
                    so.date_entered BETWEEN ? AND ?
                GROUP BY 
                    c.id, c.name, sol.base_part_number
                HAVING 
                    SUM(sol.quantity * sol.price) > 0
                ORDER BY 
                    c.name ASC, total_value DESC
            """

            params = (salesperson_id, target_month_start.strftime('%Y-%m-%d'), target_month_end.strftime('%Y-%m-%d'))

        else:  # account view
            # Account sales - all sales for customers assigned to this salesperson
            customer_query = "SELECT id FROM customers WHERE salesperson_id = ?"
            customer_rows = db.execute(customer_query, (salesperson_id,)).fetchall()

            if not customer_rows:
                return jsonify({
                    'month_label': month_label,
                    'customers': []
                })

            customer_ids = [row['id'] for row in customer_rows]
            placeholders = ','.join(['?' for _ in customer_ids])

            query = f"""
                SELECT 
                    c.id as customer_id,
                    c.name as customer_name,
                    sol.base_part_number,
                    SUM(sol.quantity) as total_quantity,
                    AVG(sol.price) as avg_unit_price,
                    SUM(sol.quantity * sol.price) as total_value
                FROM 
                    sales_orders so
                JOIN 
                    customers c ON so.customer_id = c.id
                JOIN 
                    sales_order_lines sol ON so.id = sol.sales_order_id
                WHERE 
                    so.customer_id IN ({placeholders}) AND
                    so.date_entered BETWEEN ? AND ?
                GROUP BY 
                    c.id, c.name, sol.base_part_number
                HAVING 
                    SUM(sol.quantity * sol.price) > 0
                ORDER BY 
                    c.name ASC, total_value DESC
            """

            params = customer_ids + [target_month_start.strftime('%Y-%m-%d'), target_month_end.strftime('%Y-%m-%d')]

        rows = db_execute(query, params, fetch='all')

        # Organize data by customer
        customers_data = {}

        for row in rows:
            customer_id = row['customer_id']
            customer_name = row['customer_name'] or 'Unknown Customer'

            if customer_id not in customers_data:
                customers_data[customer_id] = {
                    'customer_id': customer_id,
                    'customer_name': customer_name,
                    'total_value': 0,
                    'total_parts': 0,
                    'parts': []
                }

            part_data = {
                'part_number': row['base_part_number'] or 'N/A',
                'quantity': int(row['total_quantity']) if row['total_quantity'] else 0,
                'unit_price': float(row['avg_unit_price']) if row['avg_unit_price'] else 0,
                'total_value': float(row['total_value']) if row['total_value'] else 0
            }

            customers_data[customer_id]['parts'].append(part_data)
            customers_data[customer_id]['total_value'] += part_data['total_value']
            customers_data[customer_id]['total_parts'] += 1

        # Convert to list and sort customers by total value (highest first)
        customers_list = list(customers_data.values())
        customers_list.sort(key=lambda x: x['total_value'], reverse=True)

        # Sort parts within each customer by total value (highest first)
        for customer in customers_list:
            customer['parts'].sort(key=lambda x: x['total_value'], reverse=True)

        # Limit to top 15 customers to keep modal manageable (unless filtering by specific customer)
        if not customer_id:
            customers_list = customers_list[:15]

        db.close()

        result = {
            'month_label': month_label,
            'view_type': view_type,
            'customer_filter': customer_id,
            'customers': customers_list
        }

        print(f"DEBUG: Returning breakdown for {len(customers_list)} customers")
        return jsonify(result)

    except Exception as e:
        print(f"DEBUG: Error in monthly_breakdown: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Add these new routes to your Flask salespeople blueprint
@salespeople_bp.route('/<int:salesperson_id>/customer_sales_data/<int:customer_id>')
@login_required
def customer_sales_data(salesperson_id, customer_id):
    """API endpoint to get sales data for a specific customer"""
    try:
        from datetime import datetime, timedelta

        print(f"DEBUG: Getting customer sales data for salesperson {salesperson_id}, customer {customer_id}")

        # Current date references
        today = datetime.now().date()

        db = get_db_connection()

        # NEW: Get consolidated customer IDs
        consolidated_customers = get_consolidated_customer_ids(salesperson_id)

        # Find which group this customer belongs to
        all_customer_ids = [customer_id]  # Default to just this customer
        customer_name = f"Customer #{customer_id}"

        for main_id, customer_group in consolidated_customers.items():
            if customer_id in customer_group['all_customer_ids']:
                all_customer_ids = customer_group['all_customer_ids']
                customer_name = customer_group['main_customer_name']
                break

        print(f"DEBUG: Using consolidated customer IDs: {all_customer_ids}")

        # Generate month labels for the past 24 months
        chart_labels = []
        month_dict = {}

        for i in range(24):
            month_date = (today.replace(day=1) - timedelta(days=30 * i)).replace(day=1)
            month_label = month_date.strftime('%b %Y')
            month_key = month_date.strftime('%Y-%m')

            chart_labels.insert(0, month_label)
            month_dict[month_key] = 23 - i

        # Get sales data for ALL associated customers
        start_date = (today.replace(day=1) - timedelta(days=730)).strftime('%Y-%m-%d')
        today_str = today.strftime('%Y-%m-%d')

        # UPDATED: Query for all associated customers' monthly sales
        placeholders = ','.join(['?' for _ in all_customer_ids])
        query = f"""
            SELECT 
                SUBSTRING(so.date_entered::text, 1, 7) as month,
                SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) as monthly_value,
                COUNT(so.id) as order_count
            FROM 
                sales_orders so
            WHERE 
                so.customer_id IN ({placeholders}) AND
                so.date_entered BETWEEN ? AND ?
            GROUP BY 
                SUBSTRING(so.date_entered::text, 1, 7)
            ORDER BY 
                month ASC
        """

        customer_values = [0] * 24
        params = all_customer_ids + [start_date, today_str]
        rows = db_execute(query, params, fetch='all')

        for row in rows:
            month_key = row['month']
            if month_key in month_dict:
                idx = month_dict[month_key]
                try:
                    customer_values[idx] = float(row['monthly_value']) if row['monthly_value'] else 0
                except (ValueError, TypeError):
                    pass

        # Get monthly breakdown details for tooltips - UPDATED
        monthly_details = {}
        detail_query = f"""
            SELECT 
                SUBSTRING(so.date_entered::text, 1, 7) as month,
                COUNT(so.id) as order_count,
                COUNT(DISTINCT sol.base_part_number) as part_count,
                SUM(sol.quantity) as total_quantity
            FROM 
                sales_orders so
            LEFT JOIN 
                sales_order_lines sol ON so.id = sol.sales_order_id
            WHERE 
                so.customer_id IN ({placeholders}) AND
                so.date_entered BETWEEN ? AND ?
            GROUP BY 
                SUBSTRING(so.date_entered::text, 1, 7)
            ORDER BY 
                month ASC
        """

        detail_rows = db_execute(detail_query, params, fetch='all')

        for row in detail_rows:
            month_key = row['month']
            if month_key in month_dict:
                idx = month_dict[month_key]
                monthly_details[idx] = {
                    'order_count': row['order_count'] or 0,
                    'part_count': row['part_count'] or 0,
                    'total_quantity': row['total_quantity'] or 0
                }

        db.close()

        result = {
            'customer_id': customer_id,
            'customer_name': customer_name,
            'labels': chart_labels,
            'values': customer_values,
            'monthly_details': monthly_details,
            'consolidated_customer_ids': all_customer_ids  # Optional: for debugging
        }

        print(
            f"DEBUG: Returning customer sales data for {customer_name} (consolidated: {len(all_customer_ids)} customers)")
        return jsonify(result)

    except Exception as e:
        print(f"DEBUG: Error in customer_sales_data: {str(e)}")
        return jsonify({'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/customer_list')
@login_required
def customer_list(salesperson_id):
    """API endpoint to get list of customers for dropdown filter - with consolidation"""
    try:
        import time
        t0 = time.perf_counter()
        # Get consolidated customer groups
        t_step = time.perf_counter()
        consolidated_customers = get_consolidated_customer_ids(salesperson_id)
        t_consolidated = time.perf_counter() - t_step

        if not consolidated_customers:
            return jsonify({'customers': []})

        customers = []

        def _to_datetime(value):
            if not value:
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, date):
                return datetime(value.year, value.month, value.day)
            if isinstance(value, str):
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ'):
                    try:
                        return datetime.strptime(value, fmt)
                    except Exception:
                        continue
                try:
                    return datetime.fromisoformat(value)
                except Exception:
                    return None
            return None

        # Track which customer IDs we've already processed
        processed_ids = set()
        groups = []
        all_customer_ids = set()

        for main_customer_id, customer_group in consolidated_customers.items():
            if main_customer_id in processed_ids:
                continue
            group_ids = customer_group['all_customer_ids']
            processed_ids.update(group_ids)
            groups.append((main_customer_id, customer_group['main_customer_name'], group_ids))
            all_customer_ids.update(group_ids)

        # Pull sales order aggregates for all IDs in one query (avoids N+1 queries)
        per_customer = {}
        t_step = time.perf_counter()
        if all_customer_ids:
            all_ids_list = sorted(all_customer_ids)
            placeholders = ','.join(['?' for _ in all_ids_list])
            query = f"""
                SELECT
                    so.customer_id,
                    COUNT(so.id) as order_count,
                    SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE so.total_value END) as total_value,
                    MAX(so.date_entered) as last_order_date
                FROM sales_orders so
                WHERE so.customer_id IN ({placeholders})
                GROUP BY so.customer_id
            """
            rows = db_execute(query, all_ids_list, fetch='all') or []
            per_customer = {row['customer_id']: row for row in rows}
        t_aggregates = time.perf_counter() - t_step

        for main_customer_id, customer_name, group_ids in groups:
            order_count = 0
            total_value = 0.0
            last_order_date = None
            last_order_dt = None

            for cid in group_ids:
                row = per_customer.get(cid)
                if not row:
                    continue
                order_count += int(row.get('order_count') or 0)
                total_value += float(row.get('total_value') or 0)

                dt = _to_datetime(row.get('last_order_date'))
                if dt and (last_order_dt is None or dt > last_order_dt):
                    last_order_dt = dt
                    last_order_date = row.get('last_order_date')

            if total_value > 0:
                customers.append({
                    'id': main_customer_id,  # Use main customer ID for filtering
                    'name': customer_name,
                    'order_count': order_count,
                    'total_value': total_value,
                    'last_order_date': last_order_date,
                    'associated_count': len(group_ids)  # Show how many are consolidated
                })

        # Sort by total value descending
        customers.sort(key=lambda x: x['total_value'], reverse=True)

        # Limit to top 100
        customers = customers[:100]

        print(
            f"DEBUG: Returning {len(customers)} consolidated customers (from {len(processed_ids)} total customer IDs)")

        total = time.perf_counter() - t0
        print(f"TIMING salespeople.customer_list total={total:.3f}s consolidated={t_consolidated:.3f}s aggregates={t_aggregates:.3f}s")
        return jsonify({'customers': customers})

    except Exception as e:
        print(f"DEBUG: Error in customer_list: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/customer_losses')
@login_required
def customer_losses(salesperson_id):
    """Simplified API to identify declining and lost customers"""
    try:
        from datetime import datetime, timedelta
        import traceback

        print(f"DEBUG: Getting customer losses for salesperson {salesperson_id}")

        today = datetime.now().date()

        result = {
            'month_decliners': [],  # Down vs last month
            'quarter_lost': [],  # Lost in last 3 months
            'year_dormant': [],  # Silent for 12+ months, high value
            'analysis_date': today.strftime('%Y-%m-%d')
        }

        try:
            consolidated_customers = get_consolidated_customer_ids(salesperson_id)

            # Track which customer IDs we've already processed to avoid duplicates
            processed_ids = set()

            for main_customer_id, customer_group in consolidated_customers.items():
                # Skip if we've already processed this customer as part of another group
                if main_customer_id in processed_ids:
                    continue

                all_customer_ids = customer_group['all_customer_ids']
                customer_name = customer_group['main_customer_name']

                # Mark all IDs in this group as processed
                processed_ids.update(all_customer_ids)

                # Get orders from last 18 months
                all_orders = get_consolidated_customer_orders(
                    all_customer_ids,
                    (today - timedelta(days=540)).strftime('%Y-%m-%d')
                )

                if len(all_orders) < 2:
                    continue

                # Parse dates
                for order in all_orders:
                    order['date_obj'] = order['date_entered'] if isinstance(order['date_entered'], date) else datetime.strptime(order['date_entered'], '%Y-%m-%d').date()

                # This month vs last month comparison
                this_month_start = today.replace(day=1)
                last_month_end = this_month_start - timedelta(days=1)
                last_month_start = last_month_end.replace(day=1)
                two_months_ago = (last_month_start - timedelta(days=1)).replace(day=1)

                this_month_orders = [o for o in all_orders if o['date_obj'] >= this_month_start]
                last_month_orders = [o for o in all_orders if last_month_start <= o['date_obj'] < this_month_start]
                prev_month_orders = [o for o in all_orders if two_months_ago <= o['date_obj'] < last_month_start]

                this_month_value = sum(o['total_value'] for o in this_month_orders)
                last_month_value = sum(o['total_value'] for o in last_month_orders)
                prev_month_value = sum(o['total_value'] for o in prev_month_orders)

                # Month decliner: significant drop from last month
                if last_month_value >= 500 and this_month_value < last_month_value * 0.5:
                    decline_pct = ((last_month_value - this_month_value) / last_month_value) * 100
                    result['month_decliners'].append({
                        'customer_id': main_customer_id,
                        'customer_name': customer_name,
                        'last_month_value': float(last_month_value),
                        'this_month_value': float(this_month_value),
                        'decline_percent': float(decline_pct),
                        'decline_amount': float(last_month_value - this_month_value),
                        'associated_companies': len(all_customer_ids) - 1
                    })

                # Quarter lost: had orders 3-6 months ago, none in last 3 months
                last_quarter = today - timedelta(days=90)
                prev_quarter_start = today - timedelta(days=180)

                recent_orders = [o for o in all_orders if o['date_obj'] >= last_quarter]
                prev_quarter_orders = [o for o in all_orders if prev_quarter_start <= o['date_obj'] < last_quarter]

                if len(recent_orders) == 0 and len(prev_quarter_orders) >= 2:
                    prev_quarter_value = sum(o['total_value'] for o in prev_quarter_orders)
                    last_order = max(o['date_obj'] for o in all_orders)
                    result['quarter_lost'].append({
                        'customer_id': main_customer_id,
                        'customer_name': customer_name,
                        'previous_quarter_value': float(prev_quarter_value),
                        'last_order_date': last_order.strftime('%Y-%m-%d'),
                        'days_since_order': (today - last_order).days,
                        'associated_companies': len(all_customer_ids) - 1
                    })

                # Year dormant: high lifetime value, silent 12+ months
                one_year_ago = today - timedelta(days=365)
                total_value = sum(o['total_value'] for o in all_orders)
                last_order_date = max(o['date_obj'] for o in all_orders)

                if total_value >= 5000 and last_order_date < one_year_ago:
                    result['year_dormant'].append({
                        'customer_id': main_customer_id,
                        'customer_name': customer_name,
                        'total_lifetime_value': float(total_value),
                        'last_order_date': last_order_date.strftime('%Y-%m-%d'),
                        'months_silent': int((today - last_order_date).days / 30),
                        'total_orders': len(all_orders),
                        'associated_companies': len(all_customer_ids) - 1
                    })

            # Sort and limit
            result['month_decliners'] = sorted(result['month_decliners'],
                                               key=lambda x: x['decline_amount'],
                                               reverse=True)[:10]
            result['quarter_lost'] = sorted(result['quarter_lost'],
                                            key=lambda x: x['previous_quarter_value'],
                                            reverse=True)[:10]
            result['year_dormant'] = sorted(result['year_dormant'],
                                            key=lambda x: x['total_lifetime_value'],
                                            reverse=True)[:10]

            print(f"DEBUG: Found {len(result['month_decliners'])} month decliners, "
                  f"{len(result['quarter_lost'])} quarter lost, "
                  f"{len(result['year_dormant'])} year dormant")
            print(f"DEBUG: Processed {len(processed_ids)} total customer IDs")

        except Exception as e:
            print(f"DEBUG: Error in customer losses: {str(e)}")
            print(traceback.format_exc())

        return jsonify(result)

    except Exception as e:
        print(f"DEBUG: Unhandled exception: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Replace the customer_risk_analysis route with this consolidated version
@salespeople_bp.route('/<int:salesperson_id>/customer_risk_analysis')
@login_required
def customer_risk_analysis(salesperson_id):
    """API endpoint to analyze customers at risk of churning or declining - with consolidated customer support"""
    try:
        from datetime import datetime, timedelta
        import traceback

        print(f"DEBUG: Getting consolidated customer risk analysis for salesperson {salesperson_id}")

        # Current date references
        today = datetime.now().date()

        # Analysis periods
        baseline_start = today - timedelta(days=540)  # 18 months for baseline
        analysis_start = today - timedelta(days=180)  # 6 months for recent analysis
        immediate_risk_days = 45
        medium_risk_days = 90

        result = {
            'immediate_risk': [],
            'medium_risk': [],
            'high_risk': [],
            'recovering': [],
            'analysis_date': today.strftime('%Y-%m-%d')
        }

        try:
            # Get consolidated customer groups
            consolidated_customers = get_consolidated_customer_ids(salesperson_id)
            print(f"DEBUG: Found {len(consolidated_customers)} consolidated customer groups")

            for main_customer_id, customer_group in consolidated_customers.items():
                all_customer_ids = customer_group['all_customer_ids']
                customer_name = customer_group['main_customer_name']

                print(f"DEBUG: Analyzing customer group '{customer_name}' with IDs: {all_customer_ids}")

                # Get all orders for this customer group
                all_orders = get_consolidated_customer_orders(
                    all_customer_ids,
                    baseline_start.strftime('%Y-%m-%d')
                )

                if len(all_orders) < 2:  # Need at least 2 orders for pattern analysis
                    continue

                # Calculate consolidated metrics
                total_orders = len(all_orders)
                total_lifetime_value = sum(order['total_value'] for order in all_orders)
                avg_order_value = total_lifetime_value / total_orders if total_orders > 0 else 0

                # Get date information
                order_dates = [datetime.strptime(order['date_entered'], '%Y-%m-%d').date() for order in all_orders]
                first_order_date = min(order_dates)
                last_order_date = max(order_dates)
                days_since_last_order = (today - last_order_date).days

                # Calculate average days between orders
                if total_orders > 1:
                    total_days = (last_order_date - first_order_date).days
                    avg_days_between_orders = total_days / (total_orders - 1)
                else:
                    avg_days_between_orders = None

                # Get recent orders (last 6 months)
                recent_orders = [
                    order for order in all_orders
                    if datetime.strptime(order['date_entered'], '%Y-%m-%d').date() >= analysis_start
                ]

                recent_order_count = len(recent_orders)
                recent_total_value = sum(order['total_value'] for order in recent_orders)
                recent_avg_value = recent_total_value / recent_order_count if recent_order_count > 0 else 0

                customer_data = {
                    'customer_id': main_customer_id,
                    'customer_name': customer_name,
                    'total_lifetime_value': float(total_lifetime_value),
                    'avg_order_value': float(avg_order_value),
                    'total_orders': total_orders,
                    'recent_orders': recent_order_count,
                    'days_since_last_order': days_since_last_order,
                    'avg_days_between_orders': int(avg_days_between_orders) if avg_days_between_orders else None,
                    'last_order_date': last_order_date.strftime('%Y-%m-%d'),
                    'recent_avg_value': float(recent_avg_value),
                    'value_decline_percent': 0,
                    'order_frequency_decline': False,
                    'risk_score': 0,
                    'risk_factors': [],
                    'associated_companies': len(all_customer_ids) - 1  # Number of associated companies
                }

                # Calculate value decline percentage
                if avg_order_value > 0 and recent_avg_value > 0:
                    customer_data['value_decline_percent'] = (
                                                                     (
                                                                                 avg_order_value - recent_avg_value) / avg_order_value
                                                             ) * 100

                # Determine risk factors and calculate risk score
                risk_score = 0
                risk_factors = []

                # Factor 1: Days overdue based on their typical cycle
                if avg_days_between_orders and days_since_last_order:
                    days_overdue = days_since_last_order - avg_days_between_orders

                    if days_overdue > 0:
                        risk_factors.append(f"{int(days_overdue)} days past typical reorder cycle")
                        risk_score += min(days_overdue / 10, 50)

                # Factor 2: Recent order frequency decline
                if avg_days_between_orders:
                    expected_recent_orders = max(1, 180 / avg_days_between_orders)
                    if recent_order_count < (expected_recent_orders * 0.7):
                        risk_factors.append("Order frequency has declined")
                        customer_data['order_frequency_decline'] = True
                        risk_score += 25

                # Factor 3: Order value decline
                if customer_data['value_decline_percent'] > 30:
                    risk_factors.append(f"Order values down {customer_data['value_decline_percent']:.0f}%")
                    risk_score += customer_data['value_decline_percent'] / 2

                # Factor 4: Long periods without orders
                if days_since_last_order > 180:
                    risk_factors.append(f"{days_since_last_order} days since last order")
                    risk_score += 30

                # Factor 5: No recent orders at all
                if recent_order_count == 0:
                    risk_factors.append("No orders in past 6 months")
                    risk_score += 40

                # Weight by customer value
                if total_lifetime_value > 50000:
                    risk_score *= 1.5
                elif total_lifetime_value > 20000:
                    risk_score *= 1.2

                customer_data['risk_score'] = risk_score
                customer_data['risk_factors'] = risk_factors

                # Categorize customers based on risk analysis
                if days_since_last_order <= immediate_risk_days and len(risk_factors) > 0:
                    result['immediate_risk'].append(customer_data)
                elif days_since_last_order <= medium_risk_days and len(risk_factors) > 1:
                    result['medium_risk'].append(customer_data)
                elif days_since_last_order > medium_risk_days and total_lifetime_value > 5000:
                    result['high_risk'].append(customer_data)
                elif (customer_data['value_decline_percent'] < -20 or  # Order values increasing
                      (recent_order_count > 0 and days_since_last_order < 30)):  # Recent activity
                    if len(risk_factors) == 0:
                        result['recovering'].append(customer_data)

            # Sort each category by risk score and limit results
            for category in ['immediate_risk', 'medium_risk', 'high_risk']:
                result[category] = sorted(result[category], key=lambda x: x['risk_score'], reverse=True)[:15]

            result['recovering'] = sorted(result['recovering'],
                                          key=lambda x: (x['recent_orders'], -x['value_decline_percent']),
                                          reverse=True)[:10]

            print(f"DEBUG: Consolidated risk analysis complete - Immediate: {len(result['immediate_risk'])}, "
                  f"Medium: {len(result['medium_risk'])}, High: {len(result['high_risk'])}, "
                  f"Recovering: {len(result['recovering'])}")

        except Exception as e:
            print(f"DEBUG: Error in consolidated customer risk analysis: {str(e)}")
            print(traceback.format_exc())

        return jsonify(result)

    except Exception as e:
        print(f"DEBUG: Unhandled exception in customer_risk_analysis: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/lifetime_anomalies')
@login_required
def lifetime_anomalies(salesperson_id):
    """API endpoint to analyze lifetime value anomalies and dormant customers"""
    try:
        from datetime import datetime, timedelta
        import traceback

        print(f"DEBUG: Getting lifetime anomalies for salesperson {salesperson_id}")

        # Current date references
        today = datetime.now().date()

        # Extended analysis periods for lifetime data
        lifetime_start = today - timedelta(days=1825)  # 5 years back for lifetime analysis
        dormant_threshold_months = 12  # 12+ months for dormant classification
        dormant_threshold_date = today - timedelta(days=365)  # 12 months ago
        high_value_threshold = 5000  # £5k+ for high-value classification
        large_order_threshold = 2000  # £2k+ for "large" single orders

        result = {
            'dormant_high_value': [],  # High-value customers gone silent 12+ months
            'one_time_large': [],  # Customers with single large orders
            'pattern_breakers': [],  # Customers who broke established patterns
            'analysis_date': today.strftime('%Y-%m-%d'),
            'thresholds': {
                'high_value': high_value_threshold,
                'large_order': large_order_threshold,
                'dormant_months': dormant_threshold_months
            }
        }

        try:
            db = get_db_connection()

            # 1. DORMANT HIGH-VALUE CUSTOMERS
            dormant_query = """
                SELECT 
                    c.id as customer_id,
                    c.name as customer_name,
                    COUNT(so.id) as total_orders,
                    SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE CAST(so.total_value AS REAL) END) as total_lifetime_value,
                    MIN(so.date_entered) as first_order_date,
                    MAX(so.date_entered) as last_order_date,
                    AVG(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE CAST(so.total_value AS REAL) END) as avg_order_value,
                    CAST((julianday(MAX(so.date_entered)) - julianday(MIN(so.date_entered))) / 365.25 AS REAL) as years_active,
                    CAST((julianday(?) - julianday(MAX(so.date_entered))) / 30.44 AS REAL) as months_since_last_order
                FROM 
                    customers c
                LEFT JOIN 
                    sales_orders so ON c.id = so.customer_id
                WHERE 
                    c.salesperson_id = ? AND
                    so.date_entered >= ? AND
                    (so.total_value IS NOT NULL AND so.total_value::text != '' AND CAST(so.total_value AS REAL) > 0)
                GROUP BY 
                    c.id, c.name
                HAVING 
                    total_lifetime_value >= ? AND
                    last_order_date <= ?
                ORDER BY 
                    total_lifetime_value DESC, months_since_last_order DESC
                LIMIT 20
            """

            dormant_customers = db.execute(dormant_query, (
                today.strftime('%Y-%m-%d'),
                salesperson_id,
                lifetime_start.strftime('%Y-%m-%d'),
                high_value_threshold,
                dormant_threshold_date.strftime('%Y-%m-%d')
            )).fetchall()

            print(f"DEBUG: Found {len(dormant_customers)} dormant high-value customers")

            for customer in dormant_customers:
                result['dormant_high_value'].append({
                    'customer_id': customer['customer_id'],
                    'customer_name': customer['customer_name'],
                    'total_lifetime_value': float(customer['total_lifetime_value']) if customer[
                        'total_lifetime_value'] else 0,
                    'total_orders': customer['total_orders'],
                    'avg_order_value': float(customer['avg_order_value']) if customer['avg_order_value'] else 0,
                    'last_order_date': customer['last_order_date'],
                    'first_order_date': customer['first_order_date'],
                    'months_since_last_order': float(customer['months_since_last_order']) if customer[
                        'months_since_last_order'] else 0,
                    'years_active': float(customer['years_active']) if customer['years_active'] else 0
                })

            # 2. ONE-TIME LARGE ORDERS
            one_time_query = """
                SELECT 
                    c.id as customer_id,
                    c.name as customer_name,
                    COUNT(so.id) as total_orders,
                    MAX(CAST(so.total_value AS REAL)) as single_order_value,
                    MAX(so.date_entered) as single_order_date,
                    CAST((julianday(?) - julianday(MAX(so.date_entered))) / 30.44 AS REAL) as months_since_order
                FROM 
                    customers c
                LEFT JOIN 
                    sales_orders so ON c.id = so.customer_id
                WHERE 
                    c.salesperson_id = ? AND
                    so.date_entered >= ? AND
                    (so.total_value IS NOT NULL AND so.total_value::text != '' AND CAST(so.total_value AS REAL) > 0)
                GROUP BY 
                    c.id, c.name
                HAVING 
                    total_orders = 1 AND
                    single_order_value >= ?
                ORDER BY 
                    single_order_value DESC
                LIMIT 15
            """

            one_time_customers = db.execute(one_time_query, (
                today.strftime('%Y-%m-%d'),
                salesperson_id,
                lifetime_start.strftime('%Y-%m-%d'),
                large_order_threshold
            )).fetchall()

            print(f"DEBUG: Found {len(one_time_customers)} one-time large order customers")

            for customer in one_time_customers:
                result['one_time_large'].append({
                    'customer_id': customer['customer_id'],
                    'customer_name': customer['customer_name'],
                    'total_lifetime_value': float(customer['single_order_value']) if customer[
                        'single_order_value'] else 0,
                    'single_order_date': customer['single_order_date'],
                    'months_since_order': float(customer['months_since_order']) if customer['months_since_order'] else 0
                })

            # 3. PATTERN BREAKERS - Simplified query
            pattern_breakers_query = """
                SELECT 
                    c.id as customer_id,
                    c.name as customer_name,
                    COUNT(so.id) as total_orders,
                    SUM(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE CAST(so.total_value AS REAL) END) as total_lifetime_value,
                    MIN(so.date_entered) as first_order_date,
                    MAX(so.date_entered) as last_order_date,
                    AVG(CASE WHEN so.total_value IS NULL OR so.total_value::text = '' THEN 0 ELSE CAST(so.total_value AS REAL) END) as avg_order_value,
                    CASE 
                        WHEN COUNT(so.id) > 2 THEN 
                            CAST((julianday(MAX(so.date_entered)) - julianday(MIN(so.date_entered))) / (COUNT(so.id) - 1) AS INTEGER)
                        ELSE NULL 
                    END as avg_days_between_orders,
                    CAST((julianday(?) - julianday(MAX(so.date_entered))) / 30.44 AS REAL) as months_since_last_order,
                    CAST((julianday(MAX(so.date_entered)) - julianday(MIN(so.date_entered))) / 365.25 AS REAL) as years_active
                FROM 
                    customers c
                LEFT JOIN 
                    sales_orders so ON c.id = so.customer_id
                WHERE 
                    c.salesperson_id = ? AND
                    so.date_entered >= ? AND
                    (so.total_value IS NOT NULL AND so.total_value::text != '' AND CAST(so.total_value AS REAL) > 0)
                GROUP BY 
                    c.id, c.name
                HAVING 
                    total_orders >= 3 AND
                    total_lifetime_value >= 3000 AND
                    avg_days_between_orders IS NOT NULL AND
                    avg_days_between_orders <= 365 AND
                    months_since_last_order > (avg_days_between_orders / 30.44) * 1.5
                ORDER BY 
                    total_lifetime_value DESC, months_since_last_order DESC
                LIMIT 15
            """

            pattern_breakers = db.execute(pattern_breakers_query, (
                today.strftime('%Y-%m-%d'),
                salesperson_id,
                lifetime_start.strftime('%Y-%m-%d')
            )).fetchall()

            print(f"DEBUG: Found {len(pattern_breakers)} pattern-breaking customers")

            for customer in pattern_breakers:
                # Get recent activity for this customer
                recent_query = """
                    SELECT COUNT(*) as recent_orders
                    FROM sales_orders
                    WHERE customer_id = ? AND date_entered >= ?
                """
                recent_data = db.execute(recent_query, (
                    customer['customer_id'],
                    (today - timedelta(days=180)).strftime('%Y-%m-%d')
                )).fetchone()

                # Generate pattern descriptions
                if customer['avg_days_between_orders']:
                    if customer['avg_days_between_orders'] <= 45:
                        usual_pattern = 'Monthly orders'
                    elif customer['avg_days_between_orders'] <= 120:
                        usual_pattern = 'Quarterly orders'
                    elif customer['avg_days_between_orders'] <= 200:
                        usual_pattern = 'Semi-annual orders'
                    else:
                        usual_pattern = 'Annual orders'

                    expected_months = customer['avg_days_between_orders'] / 30.44
                    overdue_months = customer['months_since_last_order'] - expected_months

                    if overdue_months > 0:
                        pattern_break_description = f"Overdue: {overdue_months:.0f} months past expected"
                    else:
                        pattern_break_description = "Pattern change detected"
                else:
                    usual_pattern = 'Unknown pattern'
                    pattern_break_description = 'Pattern analysis failed'

                result['pattern_breakers'].append({
                    'customer_id': customer['customer_id'],
                    'customer_name': customer['customer_name'],
                    'total_lifetime_value': float(customer['total_lifetime_value']) if customer[
                        'total_lifetime_value'] else 0,
                    'total_orders': customer['total_orders'],
                    'avg_order_value': float(customer['avg_order_value']) if customer['avg_order_value'] else 0,
                    'last_order_date': customer['last_order_date'],
                    'first_order_date': customer['first_order_date'],
                    'months_since_last_order': float(customer['months_since_last_order']) if customer[
                        'months_since_last_order'] else 0,
                    'years_active': float(customer['years_active']) if customer['years_active'] else 0,
                    'avg_days_between_orders': customer['avg_days_between_orders'],
                    'recent_orders': recent_data['recent_orders'] if recent_data else 0,
                    'pattern_break_description': pattern_break_description,
                    'usual_pattern': usual_pattern
                })

            db.close()

            print(f"DEBUG: Lifetime anomalies analysis complete - Dormant: {len(result['dormant_high_value'])}, "
                  f"One-time: {len(result['one_time_large'])}, Pattern breakers: {len(result['pattern_breakers'])}")

        except Exception as e:
            print(f"DEBUG: Error in lifetime anomalies analysis: {str(e)}")
            print(traceback.format_exc())

        return jsonify(result)

    except Exception as e:
        print(f"DEBUG: Unhandled exception in lifetime_anomalies: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/customer_news')
@login_required
def customer_news(salesperson_id):
    print(f"\n=== customer_news route called for salesperson {salesperson_id} ===")

    if request.args.get('stream') == 'true':
        print("Stream request detected, starting SSE")
        return Response(
            stream_with_context(generate_news_stream(salesperson_id)),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'
            }
        )

    force_refresh = request.args.get('force_refresh') == 'true'
    print(f"force_refresh = {force_refresh}")

    if force_refresh:
        result = collect_customer_news(salesperson_id)
        salesperson = get_salesperson_by_id(salesperson_id)
        send_news_email(salesperson_id, salesperson.get('name') if salesperson else None, result)
        return jsonify({
            'success': True,
            **result
        })

    if not force_refresh:
        server_software = (request.environ.get('SERVER_SOFTWARE') or '').lower()
        supports_streaming = 'waitress' not in server_software
        cache_key = get_cache_key(salesperson_id)
        print(f"cache_key = {cache_key}")

        cached_result = get_cached_news(cache_key)
        print(f"cached_result = {cached_result}")
        print(f"cached_result type = {type(cached_result)}")

        if cached_result:
            print(f"Returning cached data with {len(cached_result.get('news_items', []))} items")
            return jsonify({
                'success': True,
                'cached': True,
                'supports_streaming': supports_streaming,
                **cached_result
            })
        else:
            print("No cached result found")

    print("Returning requires_streaming")
    return jsonify({
        'success': True,
        'requires_streaming': True,
        'supports_streaming': supports_streaming
    })


@salespeople_bp.route('/<int:salesperson_id>/customer_news/send_email', methods=['POST'])
@login_required
def customer_news_send_email(salesperson_id):
    """Send cached news email without refreshing (testing helper)."""
    cache_key = get_cache_key(salesperson_id)
    cached_result = get_cached_news(cache_key)
    if not cached_result:
        return jsonify({
            'success': True,
            'email_sent': False,
            'cached': False,
            'message': 'No cached news available'
        })

    salesperson = get_salesperson_by_id(salesperson_id)
    sent = send_news_email(salesperson_id, salesperson.get('name') if salesperson else None, cached_result)
    addresses = get_news_email_addresses(salesperson_id)
    return jsonify({
        'success': True,
        'email_sent': bool(sent),
        'cached': True,
        'from_email': addresses.get('from_email'),
        'to_email': addresses.get('to_email'),
        **cached_result
    })

def collect_customer_news(salesperson_id):
    """Collect customer news synchronously (non-streaming fallback)."""
    # Verify salesperson exists
    salesperson = get_salesperson_by_id(salesperson_id)
    if not salesperson:
        return {
            'news_items': [],
            'last_updated': datetime.now().isoformat(),
            'total_customers_checked': 0,
            'successful_customers': 0,
            'total_news_items': 0
        }

    top_customers = get_watched_customers_for_news(salesperson_id, limit=25)
    if not top_customers:
        result = {
            'news_items': [],
            'last_updated': datetime.now().isoformat(),
            'total_customers_checked': 0,
            'successful_customers': 0,
            'total_news_items': 0
        }
        cache_key = get_cache_key(salesperson_id)
        cache_news(cache_key, result)
        return result

    all_news_items = []
    successful_customers = 0

    for customer in top_customers:
        try:
            raw_news = fetch_customer_news_perplexity(customer)

            if raw_news:
                processed_news = process_customer_news_chatgpt(customer, raw_news)

                if processed_news and processed_news.get('news_items'):
                    all_news_items.extend(processed_news['news_items'])
                    successful_customers += 1

            import time
            time.sleep(0.5)
        except Exception:
            continue

    all_news_items.sort(
        key=lambda x: (x.get('relevance_score', 0), x.get('published_date', '')),
        reverse=True
    )
    final_news_items = all_news_items[:20]

    result = {
        'news_items': final_news_items,
        'last_updated': datetime.now().isoformat(),
        'total_customers_checked': len(top_customers),
        'successful_customers': successful_customers,
        'total_news_items': len(final_news_items)
    }

    cache_key = get_cache_key(salesperson_id)
    cache_news(cache_key, result)
    return result

def generate_news_stream(salesperson_id):
    """Generator for server-sent events during news collection"""
    try:
        # Verify salesperson exists
        salesperson = get_salesperson_by_id(salesperson_id)
        if not salesperson:
            yield f"data: {json.dumps({'error': 'Salesperson not found'})}\n\n"
            return

        # Get top customers
        top_customers = get_watched_customers_for_news(salesperson_id, limit=25)

        if not top_customers:
            result = {
                'news_items': [],
                'last_updated': datetime.now().isoformat(),
                'total_customers_checked': 0,
                'successful_customers': 0,
                'total_news_items': 0
            }
            cache_key = get_cache_key(salesperson_id)
            cache_news(cache_key, result)
            yield f"data: {json.dumps({'status': 'completed', **result})}\n\n"
            return

        # Send initial progress
        yield f"data: {json.dumps({'status': 'starting', 'total_customers': len(top_customers), 'customers': [c['name'] for c in top_customers]})}\n\n"

        all_news_items = []
        processed_customers = 0
        successful_customers = 0

        for i, customer in enumerate(top_customers):
            try:
                # Send progress update
                yield f"data: {json.dumps({'status': 'processing', 'current_customer': customer['name'], 'customer_index': i, 'completed_customers': processed_customers})}\n\n"

                # Get raw news from Perplexity
                raw_news = fetch_customer_news_perplexity(customer)

                if raw_news:
                    # Send processing update
                    yield f"data: {json.dumps({'status': 'analyzing', 'current_customer': customer['name'], 'customer_index': i})}\n\n"

                    # Process with ChatGPT
                    processed_news = process_customer_news_chatgpt(customer, raw_news)

                    if processed_news and processed_news.get('news_items'):
                        news_count = len(processed_news['news_items'])
                        all_news_items.extend(processed_news['news_items'])
                        successful_customers += 1

                        # Send success update
                        yield f"data: {json.dumps({'status': 'found_news', 'current_customer': customer['name'], 'customer_index': i, 'news_count': news_count})}\n\n"
                    else:
                        # Send no news update
                        yield f"data: {json.dumps({'status': 'no_news', 'current_customer': customer['name'], 'customer_index': i})}\n\n"
                else:
                    # Send no data update
                    yield f"data: {json.dumps({'status': 'no_data', 'current_customer': customer['name'], 'customer_index': i})}\n\n"

                processed_customers += 1

                # Add delay to avoid API rate limits
                import time
                time.sleep(0.5)

            except Exception as e:
                # Send error update
                yield f"data: {json.dumps({'status': 'error', 'current_customer': customer['name'], 'customer_index': i, 'error': str(e)})}\n\n"
                processed_customers += 1
                continue

        # Sort and limit results
        all_news_items.sort(
            key=lambda x: (x.get('relevance_score', 0), x.get('published_date', '')),
            reverse=True
        )
        final_news_items = all_news_items[:20]

        # Cache the results
        result = {
            'news_items': final_news_items,
            'last_updated': datetime.now().isoformat(),
            'total_customers_checked': len(top_customers),
            'successful_customers': successful_customers,
            'total_news_items': len(final_news_items)
        }

        cache_key = get_cache_key(salesperson_id)  # Changed from get_daily_cache_key
        cache_news(cache_key, result)

        # Send completion
        send_news_email(salesperson_id, salesperson.get('name') if salesperson else None, result)
        yield f"data: {json.dumps({'status': 'completed', **result})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"

@salespeople_bp.route('/<int:salesperson_id>/customer_nfffews')
@login_required
def customer_news_debug(salesperson_id):
    """Debug version - test if route is accessible"""
    try:
        print(f"DEBUG: customer_news route called for salesperson {salesperson_id}")

        # Basic test response
        return jsonify({
            'success': True,
            'debug': True,
            'salesperson_id': salesperson_id,
            'message': 'Route is working',
            'news_items': [
                {
                    'customer_id': 1,
                    'customer_name': 'Test Customer',
                    'headline': 'Test news headline',
                    'summary': 'This is a test news summary',
                    'source': 'Test Source',
                    'published_date': '2024-01-15',
                    'business_impact': 'Medium',
                    'relevance_score': 7
                }
            ],
            'last_updated': '2024-01-15T10:00:00',
            'total_customers_checked': 1
        })

    except Exception as e:
        print(f"ERROR in customer_news route: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e),
            'debug': True
        }), 500


# Add this helper function to check dependencies
def check_news_dependencies():
    """Check if required environment variables and modules are available"""
    import os

    missing_deps = []

    # Check environment variables
    if not os.environ.get("PERPLEXITY_API_KEY"):
        missing_deps.append("PERPLEXITY_API_KEY environment variable")

    if not os.environ.get("OPENAI_API_KEY"):
        missing_deps.append("OPENAI_API_KEY environment variable")

    # Check if OpenAI module is available
    try:
        from openai import OpenAI
    except ImportError:
        missing_deps.append("openai module")

    return missing_deps


# Add this test route to check environment setup
@salespeople_bp.route('/<int:salesperson_id>/test_news_setup')
@login_required
def test_news_setup(salesperson_id):
    """Test route to check if news functionality can work"""
    try:
        missing_deps = check_news_dependencies()

        if missing_deps:
            return jsonify({
                'success': False,
                'error': 'Missing dependencies',
                'missing': missing_deps,
                'setup_instructions': {
                    'perplexity_key': 'Set PERPLEXITY_API_KEY environment variable',
                    'openai_key': 'Set OPENAI_API_KEY environment variable',
                    'openai_module': 'pip install openai'
                }
            })

        # Test customer data availability
        customers = get_top_customers_for_news(salesperson_id, limit=3)

        return jsonify({
            'success': True,
            'message': 'News setup looks good',
            'test_customers': len(customers),
            'sample_customers': [c['name'] for c in customers[:3]] if customers else []
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@salespeople_bp.route('/<int:salesperson_id>/call-list')
@login_required
def call_list(salesperson_id):
    """View the call list for a salesperson"""
    try:
        salesperson = get_salesperson_by_id(salesperson_id)
        if not salesperson:
            flash('Salesperson not found!', 'error')
            return redirect(url_for('salespeople.dashboard'))

        # Get call list data divided by communication status
        call_list_data = get_call_list_with_communication_status(salesperson_id)

        # Get communication types for quick logging
        communication_types = ["Email", "Phone", "Meeting", "Video Call", "Other"]

        breadcrumbs = generate_breadcrumbs(
            ('Home', url_for('index')),
            ('Salespeople', url_for('salespeople.dashboard')),
            (salesperson['name'], url_for('salespeople.activity', salesperson_id=salesperson_id)),
            ('Call List', url_for('salespeople.call_list', salesperson_id=salesperson_id))
        )

        return render_template(
            'salespeople/call_list.html',
            salesperson=salesperson,
            no_communications=call_list_data['no_communications'],
            has_communications=call_list_data['has_communications'],
            total_count=call_list_data['total_count'],
            communication_types=communication_types,
            breadcrumbs=breadcrumbs
        )
    except Exception as e:
        print(f"Error in call_list route: {str(e)}")
        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('salespeople.dashboard'))


@salespeople_bp.route('/<int:salesperson_id>/add-to-call-list', methods=['POST'])
@login_required
def add_to_call_list_route(salesperson_id):
    """Add a contact to the call list"""
    try:
        data = request.get_json()
        contact_id = data.get('contact_id')
        notes = data.get('notes', '')
        priority = data.get('priority', 0)

        if not contact_id:
            return jsonify({'success': False, 'error': 'Contact ID required'}), 400

        result = add_to_call_list(contact_id, salesperson_id, notes, priority)

        # Return the call_list_id so it can be removed later
        if result['success']:
            return jsonify({
                'success': True,
                'call_list_id': result.get('call_list_id')  # Make sure your add_to_call_list function returns this
            })

        return jsonify(result)

    except Exception as e:
        print(f"Error adding to call list: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/remove-from-call-list', methods=['POST'])
@login_required
def remove_from_call_list_route(salesperson_id):
    """Remove a contact from the call list"""
    try:
        data = request.get_json()
        call_list_id = data.get('call_list_id')
        contact_id = data.get('contact_id')

        # If we don't have call_list_id, look it up by contact_id
        if not call_list_id and contact_id:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM call_list 
                WHERE contact_id = ? AND salesperson_id = ?
            """, (contact_id, salesperson_id))
            row = cursor.fetchone()
            conn.close()

            if row:
                call_list_id = row['id']
            else:
                return jsonify({'success': False, 'error': 'Contact not found in call list'}), 404

        if not call_list_id:
            return jsonify({'success': False, 'error': 'Call list ID required'}), 400

        result = remove_from_call_list(call_list_id)
        return jsonify(result)

    except Exception as e:
        print(f"Error removing from call list: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@salespeople_bp.route('/<int:salesperson_id>/bulk-add-to-call-list', methods=['POST'])
@login_required
def bulk_add_to_call_list_route(salesperson_id):
    """Bulk add contacts to call list"""
    try:
        data = request.get_json()
        contact_ids = data.get('contact_ids', [])
        notes = data.get('notes', '')
        priority = data.get('priority', 0)

        if not contact_ids:
            return jsonify({'success': False, 'error': 'No contacts selected'}), 400

        result = bulk_add_to_call_list(contact_ids, salesperson_id, notes, priority)
        return jsonify(result)

    except Exception as e:
        print(f"Error in bulk add to call list: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/update-call-list-priority', methods=['POST'])
@login_required
def update_call_list_priority_route(salesperson_id):
    """Update priority of a call list item"""
    try:
        data = request.get_json()
        call_list_id = data.get('call_list_id')
        priority = data.get('priority', 0)

        result = update_call_list_priority(call_list_id, priority)
        return jsonify(result)

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/update-call-list-notes', methods=['POST'])
@login_required
def update_call_list_notes_route(salesperson_id):
    """Update notes for a call list item"""
    try:
        data = request.get_json()
        call_list_id = data.get('call_list_id')
        notes = data.get('notes', '')

        result = update_call_list_notes(call_list_id, notes)
        return jsonify(result)

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
# Add this route to routes/salespeople.py

@salespeople_bp.route('/<int:salesperson_id>/call-list-data')
@login_required
def call_list_data(salesperson_id):
    """API endpoint to get call list data for the activity dashboard"""
    try:
        import time
        t0 = time.perf_counter()
        call_list_data = get_call_list_with_communication_status(salesperson_id)
        elapsed = time.perf_counter() - t0
        print(f"TIMING salespeople.call_list_data total={elapsed:.3f}s")
        return jsonify({
            'success': True,
            'no_communications': call_list_data['no_communications'],
            'has_communications': call_list_data['has_communications'],
            'total_count': call_list_data['total_count']
        })
    except Exception as e:
        print(f"Error getting call list data: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# -----------------------------------------------------------------------------
# MONTHLY TARGET PLANNING ROUTES
# -----------------------------------------------------------------------------
@salespeople_bp.route('/<int:salesperson_id>/planner')
@login_required
def planner_index(salesperson_id):
    """Renders the planner page skeleton"""
    salesperson = get_salesperson_by_id(salesperson_id)
    if not salesperson:
        return redirect(url_for('salespeople.dashboard'))

    # Default to next month
    next_month = datetime.now().date() + relativedelta(months=1)
    default_month = next_month.strftime('%Y-%m')

    return render_template(
        'salespeople/planner.html',
        salesperson=salesperson,
        default_month=default_month
    )


# In routes/salespeople.py

@salespeople_bp.route('/<int:salesperson_id>/planner/data')
@login_required
def get_planner_data(salesperson_id):
    try:
        # 1. Setup Dates
        target_month_str = request.args.get('month')
        if not target_month_str:
            target_month_str = (datetime.now() + relativedelta(months=1)).strftime('%Y-%m')

        target_date = datetime.strptime(target_month_str, '%Y-%m').date()
        today = datetime.now().date()
        three_months_ago = today - relativedelta(months=3)

        # 2. Connections
        consolidated_customers = get_consolidated_customer_ids(salesperson_id)
        db = get_db_connection()

        # 3. Fetch Goals & Targets
        goal_row = db.execute(
            "SELECT goal_amount FROM salesperson_monthly_goals WHERE salesperson_id = ? AND target_month = ?",
            (salesperson_id, target_month_str)
        ).fetchone()
        user_defined_goal = float(goal_row['goal_amount'] or 0) if goal_row else 0

        # Saved targets
        saved_targets_query = "SELECT customer_id, target_amount, notes, is_locked FROM customer_monthly_targets WHERE salesperson_id = ? AND target_month = ?"
        saved_targets = {
            str(row['customer_id']): dict(row)
            for row in db.execute(saved_targets_query, (salesperson_id, target_month_str)).fetchall()
        }

        # 4. Helpers (First Order Date)
        consolidated_ids = set()
        for group in consolidated_customers.values():
            consolidated_ids.update(group['all_customer_ids'])

        saved_target_ids = set()
        for key in saved_targets.keys():
            try:
                saved_target_ids.add(int(key))
            except (TypeError, ValueError):
                continue

        relevant_customer_ids = consolidated_ids | saved_target_ids
        first_order_map = {}
        if relevant_customer_ids:
            placeholders = ','.join(['?' for _ in relevant_customer_ids])
            first_order_query = f"""
                SELECT customer_id, MIN(date_entered) as first_date
                FROM sales_orders
                WHERE customer_id IN ({placeholders})
                GROUP BY customer_id
            """
            first_order_rows = db_execute(first_order_query, list(relevant_customer_ids), fetch='all') or []
        else:
            first_order_rows = []

        for row in first_order_rows:
            raw_date = row['first_date']
            if not raw_date:
                continue
            if isinstance(raw_date, datetime):
                normalized_date = raw_date.date()
            elif isinstance(raw_date, date):
                normalized_date = raw_date
            else:
                try:
                    normalized_date = datetime.strptime(str(raw_date), '%Y-%m-%d').date()
                except ValueError:
                    continue
            first_order_map[str(row['customer_id'])] = normalized_date

        opportunities = []
        recovery_list = []
        new_customers = []
        total_actuals_sum = 0

        # ... existing code ...

        chart_labels = [(today - relativedelta(months=i)).strftime('%b %y') for i in range(24, 0, -1)]

        if _using_postgres():
            chart_date_expr = "to_char(date_entered, 'YYYY-MM')"
            chart_cutoff = "current_date - interval '24 months'"
        else:
            chart_date_expr = "strftime('%Y-%m', date_entered)"
            chart_cutoff = "date('now', '-24 months')"

        customer_month_map = {}
        if relevant_customer_ids:
            placeholders = ','.join(['?' for _ in relevant_customer_ids])
            aggregated_history_query = f"""
                        SELECT customer_id, {chart_date_expr} as yyyy_mm,
                        SUM(CASE WHEN total_value IS NULL OR total_value::text = '' THEN 0 ELSE total_value END) as val
                        FROM sales_orders
                        WHERE customer_id IN ({placeholders}) AND date_entered >= {chart_cutoff}
                        GROUP BY customer_id, yyyy_mm
                        ORDER BY customer_id, yyyy_mm ASC
                    """
            aggregated_rows = db_execute(aggregated_history_query, list(relevant_customer_ids), fetch='all') or []
            for row in aggregated_rows:
                cust_id = row['customer_id']
                customer_month_map.setdefault(cust_id, {})[row['yyyy_mm']] = float(row['val'] or 0)

        # --- REPLACEMENT LOGIC START ---

        # 1. Sort groups by size (descending)
        # This ensures we process "Parent" groups (containing multiple IDs)
        # BEFORE we hit the "Child" entries effectively swallowing them up.
        sorted_customers = sorted(
            consolidated_customers.items(),
            key=lambda item: len(item[1]['all_customer_ids']),
            reverse=True
        )

        # 2. Track processed IDs to prevent duplicates and handle Orphans later
        processed_ids = set()

        # --- PHASE 1: Loop through Consolidated (Active) Customers ---
        # Note: We iterate through 'sorted_customers' instead of consolidated_customers.items()
        for main_id, group in sorted_customers:
            str_main_id = str(main_id)

            # CRITICAL CHECK: If this ID has already been handled (either as a main ID
            # or as a child of a previous group), SKIP IT completely.
            if str_main_id in processed_ids:
                continue

            # Mark this Main ID AND all its Children as processed immediately
            processed_ids.add(str_main_id)
            for sub_id in group['all_customer_ids']:
                processed_ids.add(str(sub_id))

            # ... (Existing Logic continues unchanged) ...
            all_ids = group['all_customer_ids']
            sales_map = defaultdict(float)
            for sub_id in all_ids:
                month_map = customer_month_map.get(sub_id, {})
                for month_key, month_val in month_map.items():
                    sales_map[month_key] += month_val

            actual_sales = sales_map.get(target_month_str, 0)
            total_actuals_sum += actual_sales

            chart_data = []
            recent_total = 0
            previous_active_total = 0

            for i in range(24, 0, -1):
                d = today - relativedelta(months=i)
                key = d.strftime('%Y-%m')
                val = sales_map.get(key, 0)
                chart_data.append(val)

                if i <= 3:
                    recent_total += val
                elif i <= 12:
                    previous_active_total += val

            recent_average = recent_total / 3
            last_year_key = (target_date - relativedelta(years=1)).strftime('%Y-%m')
            val_last_year = sales_map.get(last_year_key, 0)

            # Check for Saved Target on the MAIN ID
            is_saved = str_main_id in saved_targets
            saved_data = saved_targets.get(str_main_id, {})

            # Check New Business Logic (Earliest date in the group)
            group_earliest_date = None
            for sub_id in all_ids:
                s_date = first_order_map.get(str(sub_id))
                if s_date:
                    if group_earliest_date is None or s_date < group_earliest_date:
                        group_earliest_date = s_date

            is_new_business = False
            if group_earliest_date and group_earliest_date >= three_months_ago:
                is_new_business = True

            # Calculate Targets
            if is_saved:
                suggested_target = float(saved_data.get('target_amount') or 0)
                calc_method = "Manual Override" if saved_data.get('is_locked') else "Saved Plan"
                is_locked = True
            else:
                is_locked = False
                if is_new_business:
                    suggested_target = round(recent_average, -1)
                    calc_method = "New Business"
                elif recent_average > 0:
                    suggested_target = round(recent_average * 1.1, -1)
                    calc_method = "Momentum (+10%)"
                elif previous_active_total > 0:
                    suggested_target = round((previous_active_total / 9), -1)
                    calc_method = "Re-engagement"
                else:
                    suggested_target = 0
                    calc_method = "No Activity"

            # Filter Logic
            if not is_saved and suggested_target < 100 and actual_sales < 100 and previous_active_total < 500:
                continue

            customer_obj = {
                'id': main_id,
                'name': group['main_customer_name'],
                'target': suggested_target,
                'actual_sales': actual_sales,
                'recent_average': round(recent_average),
                'last_year_same_month': val_last_year,
                'chart_data': chart_data,
                'notes': saved_data.get('notes') or '',
                'calc_method': calc_method,
                'is_locked': is_locked,
                'associated_count': len(all_ids)
            }

            # Categorize
            if is_new_business:
                new_customers.append(customer_obj)
            elif is_locked:
                opportunities.append(customer_obj)
            elif recent_average > 0:
                opportunities.append(customer_obj)
            else:
                if previous_active_total > 500:
                    customer_obj['risk_alert'] = f"Dropped off: Spent £{previous_active_total:,.0f} previously"
                    recovery_list.append(customer_obj)
                elif val_last_year > 1000:
                    customer_obj['risk_alert'] = f"Seasonal: Spent £{val_last_year:,.0f} last year"
                    recovery_list.append(customer_obj)

        # --- PHASE 2: Orphans (Saved Targets not in Consolidated List) ---
        missing_ids = set(saved_targets.keys()) - processed_ids

        if missing_ids:
            try:
                placeholders = ','.join(['?' for _ in missing_ids])
                name_query = f"SELECT id, name FROM customers WHERE id IN ({placeholders})"
                name_rows = db.execute(name_query, list(missing_ids)).fetchall()
                name_map = {str(r['id']): r['name'] for r in name_rows}
            except:
                name_map = {mid: f"Customer #{mid}" for mid in missing_ids}

            for miss_id in missing_ids:
                s_data = saved_targets[miss_id]

                try:
                    miss_int = int(miss_id)
                except (TypeError, ValueError):
                    miss_int = None
                c_map = customer_month_map.get(miss_int, {})

                c_data = []
                for i in range(24, 0, -1):
                    d = today - relativedelta(months=i)
                    c_data.append(c_map.get(d.strftime('%Y-%m'), 0))

                orph_obj = {
                    'id': miss_id,
                    'name': name_map.get(miss_id, f"Customer {miss_id}"),
                    'target': float(s_data.get('target_amount') or 0),
                    'actual_sales': c_map.get(target_month_str, 0),
                    'recent_average': 0,
                    'chart_data': c_data,
                    'notes': s_data.get('notes', ''),
                    'calc_method': 'Manual Target',
                    'is_locked': True,
                    'associated_count': 1
                }

                opportunities.append(orph_obj)
                total_actuals_sum += orph_obj['actual_sales']

        db.close()

        # 5. Final Sort
        new_customers.sort(key=lambda x: x['target'], reverse=True)
        opportunities.sort(key=lambda x: x['target'], reverse=True)
        recovery_list.sort(key=lambda x: x['last_year_same_month'], reverse=True)

        return jsonify({
            'success': True,
            'month_label': target_date.strftime('%B %Y'),
            'chart_labels': chart_labels,
            'new_customers': new_customers,
            'top_opportunities': opportunities,
            'recovery_candidates': recovery_list,
            'monthly_goal': user_defined_goal,
            'totals': {
                'new_business': sum(n['target'] for n in new_customers),
                'opportunity_target': sum(o['target'] for o in opportunities),
                'recovery_potential': sum(r['target'] for r in recovery_list),
                'total_actuals': total_actuals_sum
            }
        })

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@salespeople_bp.route('/save_monthly_target', methods=['POST'])
@login_required
def save_monthly_target():
    """
    Saves the target safely, converting empty inputs to 0.
    """
    try:
        data = request.get_json()

        salesperson_id = data.get('salesperson_id')
        customer_id = data.get('customer_id')
        target_month = data.get('month')

        notes = data.get('notes', '')

        db = get_db_connection()

        # Decide whether an amount was explicitly provided (e.g. updating the target input)
        # or if we're only saving notes. If notes-only, keep the existing amount instead of
        # overwriting it with 0.
        amount_provided = 'amount' in data
        raw_amount = data.get('amount')
        if amount_provided:
            if raw_amount == '' or raw_amount is None:
                amount = 0
            else:
                amount = float(raw_amount)
        else:
            existing = db.execute(
                """
                SELECT target_amount FROM customer_monthly_targets
                WHERE salesperson_id = ? AND customer_id = ? AND target_month = ?
                """,
                (salesperson_id, customer_id, target_month)
            ).fetchone()
            amount = existing['target_amount'] if existing else 0

        query = """
            INSERT INTO customer_monthly_targets
            (salesperson_id, customer_id, target_month, target_amount, notes, is_locked, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(salesperson_id, customer_id, target_month)
            DO UPDATE SET 
                target_amount = excluded.target_amount,
                notes = excluded.notes,
                is_locked = 1,
                updated_at = CURRENT_TIMESTAMP
        """
        db.execute(query, (salesperson_id, customer_id, target_month, amount, notes))
        db.commit()
        db.close()

        return jsonify({'success': True})
    except Exception as e:
        print(f"Error saving target: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@salespeople_bp.route('/<int:salesperson_id>/planner/unassigned_customers')
@login_required
def get_unassigned_customers(salesperson_id):
    """
    Fetches a list of customers NOT already included in the main planner sections,
    sorted by total historical spend.
    """
    try:
        month_str = request.args.get('month')
        if not month_str:
            return jsonify({'success': False, 'error': 'Month parameter missing'}), 400

        # 1. Get list of all IDs already in the current planner view to exclude them
        excluded_ids = set()
        raw_exclude_ids = request.args.get('exclude_ids', '')
        if raw_exclude_ids:
            for item in raw_exclude_ids.split(','):
                item = item.strip()
                if not item:
                    continue
                try:
                    excluded_ids.add(int(item))
                except ValueError:
                    continue
        else:
            planner_data = get_planner_data(salesperson_id).json
            if not planner_data.get('success'):
                return jsonify({'success': False, 'error': 'Could not pre-fetch planner data'}), 500

            # Collect IDs from all three sections
            for section in ['new_customers', 'top_opportunities', 'recovery_candidates']:
                if section in planner_data:
                    for item in planner_data[section]:
                        excluded_ids.add(item['id'])

        db = get_db_connection()

        # 2. Get the consolidated mapping (This contains the correct Names)
        consolidated_customers = get_consolidated_customer_ids(salesperson_id)

        if not consolidated_customers:
            db.close()
            return jsonify({'success': True, 'customers': []})

        # Prepare helper maps
        child_to_main_map = {}
        child_main_ids = set()
        all_relevant_ids = set()
        # Track spend per main so we can include zero-spend customers too
        main_group_spend = {}

        for main_id, group in consolidated_customers.items():
            main_group_spend[main_id] = 0
            for sub_id in group['all_customer_ids']:
                if sub_id != main_id:
                    child_main_ids.add(sub_id)
                # Keep the first mapping we see so children do not overwrite their parent
                if sub_id not in child_to_main_map:
                    child_to_main_map[sub_id] = main_id
                all_relevant_ids.add(sub_id)

        # 3. Fetch historic spend only (Removed 'customer_name' from query)
        all_customer_rows = []
        if all_relevant_ids:
            placeholders = ','.join(['?' for _ in all_relevant_ids])
            all_customers_query = f"""
                SELECT customer_id, SUM(total_value) as total_spend 
                FROM sales_orders 
                WHERE customer_id IN ({placeholders})
                GROUP BY customer_id
            """
            all_customer_rows = db.execute(all_customers_query, list(all_relevant_ids)).fetchall()

        # 4. Aggregate spend by Main Customer Group (children roll into parent)
        for row in all_customer_rows:
            c_id = row['customer_id']
            spend = float(row['total_spend'] or 0)

            main_id = child_to_main_map.get(c_id)
            if not main_id:
                continue
            if main_id in excluded_ids:
                continue

            main_group_spend[main_id] = main_group_spend.get(main_id, 0) + spend

        db.close()

        # 5. Convert to list, sort by spend, and format (include zero-spend mains)
        unassigned_list = []
        for main_id, spend in main_group_spend.items():
            if main_id in excluded_ids:
                continue
            # Hide child customers from grouped sets; surface only the parent/main
            if main_id in child_main_ids:
                continue
            unassigned_list.append({
                'id': main_id,
                'name': consolidated_customers[main_id]['main_customer_name'],
                'historic_spend': spend
            })

        unassigned_list.sort(key=lambda x: x['historic_spend'], reverse=True)

        formatted_list = [
            {
                'id': c['id'],
                'name': f"{c['name']} (Hist. Spend: £{c['historic_spend']:,.0f})"
            } for c in unassigned_list[:50]
        ]

        return jsonify({'success': True, 'customers': formatted_list})
    except Exception as e:
        print(f"Error fetching unassigned customers: {e}")
        import traceback;
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# Add this NEW route
@salespeople_bp.route('/save_monthly_goal', methods=['POST'])
@login_required
def save_monthly_goal():
    """Saves the high-level monthly goal for the salesperson"""
    try:
        data = request.get_json()
        salesperson_id = data.get('salesperson_id')
        target_month = data.get('month')
        goal_amount = data.get('goal_amount')

        db = get_db_connection()
        query = """
            INSERT INTO salesperson_monthly_goals 
            (salesperson_id, target_month, goal_amount, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(salesperson_id, target_month) 
            DO UPDATE SET 
                goal_amount = excluded.goal_amount,
                updated_at = CURRENT_TIMESTAMP
        """
        db.execute(query, (salesperson_id, target_month, goal_amount))
        db.commit()
        db.close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
