from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, request, g, current_app
import logging
from db import execute as db_execute, db_cursor
from models import get_contact_status_by_id, get_contact_communications, get_customer_development_plan, update_customer_development_answer, delete_customer_development_answer, update_contact_status, add_contact_ajax, get_all_contacts_by_status, get_all_contact_statuses, get_all_contact_status_counts, get_customer_contacts, get_all_contacts_filtered, get_all_contact_lists, create_contact_list, delete_contact_list, get_contact_list_by_id, add_contacts_to_list, remove_contacts_from_list, get_lists_by_contact_id, update_contact_list_name, remove_contacts_from_list, get_contacts_by_ids, get_customer_domains, get_tag_description, Permission, get_all_company_types, get_available_company_types, abort, get_company_types_by_customer_id, remove_customer_company_type, insert_customer_company_type, get_rfqs_by_customer_id, get_sales_orders_by_customer_id, update_customer_apollo_id, get_customer_tags, get_templates_by_tags, update_contact, update_customer_enrichment, get_customer, get_customer_data, get_available_tags, get_customers_by_country, get_nested_tags, get_child_tags, get_customers_by_tag, get_customers_by_tags, get_customers_by_continent, get_available_countries, get_countries_by_continent, get_customer_statuses, get_continents, get_status_name, insert_customer_tag, get_all_customers, get_all_tags, get_customers_by_tag, get_latest_activity, get_customers_with_status_and_updates,  get_tag_description, insert_customer_tags, insert_customer_industry, delete_customer_industries, get_industries, get_customer_industry, update_customer_industry, delete_customer_tags, get_tags_by_customer_id, get_updates_by_customer_id, get_addresses_by_customer, insert_update, get_contact_by_id, get_customers, get_salespeople, get_currencies, get_salesperson_by_id, get_customer_by_id, get_contacts_by_customer, insert_customer, update_customer, insert_contact, get_call_list_contact_ids
from jinja2 import TemplateNotFound
from ai_helper import start_bulk_enrichment, start_perplexity_enrichment, enrich_customer_with_perplexity, apply_perplexity_enrichment, generate_industry_insights, generate_preview_prompt, enrich_customer_data, validate_enrichment_data, generate_industry_insights_with_custom_prompt
from http import HTTPStatus
import requests
from routes.auth import login_required, current_user
import time
from datetime import datetime, date, timedelta
import calendar
import threading
import re
import os
from email.header import decode_header
import email
import imaplib
import json
import pycountry
from dotenv import load_dotenv
from utils import parse_ai_suggestions
import utils
import openai
from routes.api import get_current_watched_tags

customers_bp = Blueprint('customers', __name__)
logger = logging.getLogger(__name__)

_DATABASE_URL = os.getenv('DATABASE_URL', '')


def _using_postgres():
    return bool(_DATABASE_URL and _DATABASE_URL.startswith(('postgres://', 'postgresql://')))


_NAMED_PLACEHOLDER_RE = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)")


def _prepare_query(query, params):
    """Translate SQLite/POSTGRES placeholders for the active DB."""
    if isinstance(params, dict):
        if _using_postgres():
            return _NAMED_PLACEHOLDER_RE.sub(lambda m: f"%({m.group(1)})s", query)
        return query

    if _using_postgres():
        return query.replace('?', '%s')

    return query


def _execute_with_cursor(cur, query, params=None):
    prepared = _prepare_query(query, params)
    payload = params if isinstance(params, dict) else (params or [])
    cur.execute(prepared, payload)
    return cur


def _reviewed_flag(value: bool):
    return value if _using_postgres() else int(value)


def _get_customer_permission_flags(customer_data):
    customer_salesperson_id = customer_data.get('salesperson_id')
    user_salesperson_id = current_user.get_salesperson_id()

    can_edit = (
        current_user.is_administrator() or
        current_user.can(Permission.EDIT_CUSTOMERS) or
        (user_salesperson_id and user_salesperson_id == customer_salesperson_id)
    )
    can_view = (
        can_edit or
        current_user.can(Permission.VIEW_CUSTOMERS) or
        (user_salesperson_id and user_salesperson_id == customer_salesperson_id)
    )
    return can_view, can_edit


def _get_all_suppliers_basic():
    """Return all suppliers for customer relationship management UI."""
    return db_execute(
        """
        SELECT id, name
        FROM suppliers
        ORDER BY name
        """,
        fetch='all'
    ) or []


def _get_customer_supplier_ids(customer_id):
    rows = db_execute(
        """
        SELECT supplier_id
        FROM customer_supplier_relationships
        WHERE customer_id = ?
        """,
        (customer_id,),
        fetch='all'
    ) or []
    return [int(row['supplier_id']) for row in rows if row.get('supplier_id') is not None]


def _replace_customer_supplier_relationships(customer_id, supplier_ids):
    with db_cursor(commit=True) as cur:
        _execute_with_cursor(
            cur,
            "DELETE FROM customer_supplier_relationships WHERE customer_id = ?",
            (customer_id,),
        )
        for supplier_id in supplier_ids:
            _execute_with_cursor(
                cur,
                """
                INSERT INTO customer_supplier_relationships (customer_id, supplier_id)
                VALUES (?, ?)
                ON CONFLICT (customer_id, supplier_id) DO NOTHING
                """,
                (customer_id, supplier_id),
            )


def _call_list_has_snoozed_until():
    try:
        with db_cursor() as cur:
            if _using_postgres():
                cur.execute(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'call_list'
                      AND column_name = 'snoozed_until'
                    """
                )
                return cur.fetchone() is not None
            cur.execute("PRAGMA table_info(call_list)")
            for row in cur.fetchall() or []:
                if isinstance(row, dict) and row.get('name') == 'snoozed_until':
                    return True
                if not isinstance(row, dict) and len(row) > 1 and row[1] == 'snoozed_until':
                    return True
    except Exception:
        return False
    return False


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ'):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _add_months(source: datetime, months: int):
    month = source.month - 1 + months
    year = source.year + month // 12
    month = month % 12 + 1
    day = min(source.day, calendar.monthrange(year, month)[1])
    return source.replace(year=year, month=month, day=day)


def generate_breadcrumbs(*crumbs):
    breadcrumbs = []
    for crumb, path in crumbs:
        breadcrumbs.append((crumb, path))
    return breadcrumbs


@customers_bp.route('/')
@login_required
def customers():
    if current_user.is_administrator() or current_user.can(Permission.VIEW_CUSTOMERS):
        # Admin or users with VIEW_CUSTOMERS permission see all salespeople
        salespeople = get_salespeople()
    else:
        # Other users only see their assigned salesperson
        salespeople = [get_salesperson_by_id(current_user.get_salesperson_id())] if current_user.get_salesperson_id() else []

    # Get company types for filtering
    company_types = get_all_company_types()

    breadcrumbs = generate_breadcrumbs(('Home', url_for('index')), ('Customers', url_for('customers.customers')))

    return render_template(
        'customers.html',
        salespeople=salespeople,
        company_types=company_types,
        breadcrumbs=breadcrumbs
    )


@customers_bp.route('/customers/new', methods=['POST'])
def create_customer():
    try:
        data = request.get_json()
        name = data.get('name')

        if not name:
            return jsonify({'success': False, 'error': 'Customer name is required'}), 400

        # Get the current user's salesperson_id
        salesperson_id = None
        if current_user.is_authenticated:
            salesperson_id = current_user.get_salesperson_id()
            # If no salesperson linked to user, check session as fallback
            if not salesperson_id:
                salesperson_id = session.get('selected_salesperson_id')

        # Debug logging
        print(f"Creating customer with salesperson_id: {salesperson_id}")

        customer_id = insert_customer(
            name=name,
            notes=data.get('notes'),
            primary_contact_id=None,
            salesperson_id=salesperson_id,  # This will now be properly set
            payment_terms=data.get('payment_terms', 'Pro-forma'),
            incoterms=data.get('incoterms', 'EXW'),
            country=data.get('country'),
            apollo_id=data.get('apollo_id'),
            website=data.get('website'),
            logo_url=data.get('logo_url')
        )

        return jsonify({
            'success': True,
            'customer_id': customer_id,
            'customer_name': name,
            'salesperson_id': salesperson_id  # Include this in response for debugging
        })
    except Exception as e:
        print(f"Error creating customer: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/<int:customer_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_customer(customer_id):
    # Check permissions first
    customer = get_customer_by_id(customer_id)
    if not customer:
        abort(404)

    # First, print what keys are available in the customer object
    print("Customer keys:", [key for key in customer.keys()] if hasattr(customer, 'keys') else "No keys method")

    # Convert to dictionary - avoid accessing non-existent keys directly
    customer_dict = {}
    for key in customer.keys() if hasattr(customer, 'keys') else []:
        try:
            customer_dict[key] = customer[key]
        except Exception as e:
            print(f"Error accessing key {key}: {str(e)}")

    # Print the resulting dictionary
    print("Customer dictionary:", customer_dict)

    # Get salesperson details
    if 'salesperson_id' in customer_dict and customer_dict['salesperson_id']:
        salesperson = get_salesperson_by_id(customer_dict['salesperson_id'])
        if salesperson:
            # Convert salesperson safely too
            salesperson_dict = {}
            for key in salesperson.keys() if hasattr(salesperson, 'keys') else []:
                try:
                    salesperson_dict[key] = salesperson[key]
                except Exception as e:
                    print(f"Error accessing salesperson key {key}: {str(e)}")

            customer_dict['salesperson_name'] = salesperson_dict.get('name', 'Unassigned')
        else:
            customer_dict['salesperson_name'] = 'Unassigned'

    can_view, can_edit = _get_customer_permission_flags(customer_dict)

    if not can_view:
        abort(403)  # Forbidden

    # Load countries from JSON file
    def load_countries():
        try:
            import json
            import os
            from flask import current_app

            # Try to find the JSON file in the app root
            json_path = os.path.join(current_app.root_path, '..', 'country_name_mapping.json')
            if not os.path.exists(json_path):
                json_path = os.path.join(current_app.root_path, 'country_name_mapping.json')

            with open(json_path, 'r') as f:
                country_mapping = json.load(f)

            # Convert to list of tuples (code, name) sorted by name
            countries = [(code, name) for code, name in country_mapping.items()]
            countries.sort(key=lambda x: x[1])  # Sort by country name
            return countries
        except Exception as e:
            print(f"Error loading countries: {e}")
            # Fallback to common countries if file not found
            return [
                ('US', 'United States'),
                ('GB', 'United Kingdom'),
                ('CA', 'Canada'),
                ('DE', 'Germany'),
                ('FR', 'France'),
                ('JP', 'Japan'),
                ('AU', 'Australia'),
            ]

    if request.method == 'POST':
        if not can_edit:
            abort(403)

        try:
            # Debug print statements
            print("Form data received:", flush=True)
            print(f"Name: {request.form.get('name')}", flush=True)
            print(f"Primary Contact ID: {request.form.get('primary_contact_id')}", flush=True)
            print(f"Salesperson ID: {request.form.get('salesperson_id')}", flush=True)
            print(f"Country: {request.form.get('country')}", flush=True)
            print(f"System Code: {request.form.get('system_code')}", flush=True)
            print(f"Currency ID: {request.form.get('currency_id')}", flush=True)

            name = request.form['name']
            primary_contact_id = request.form.get('primary_contact_id') or None
            salesperson_id = request.form.get('salesperson_id')
            if salesperson_id == '':
                salesperson_id = None
            payment_terms = request.form.get('payment_terms', '')
            incoterms = request.form.get('incoterms', '')
            watch = request.form.get('watch') == 'on'
            country = request.form.get('country', '').upper()  # Store as uppercase ISO code
            system_code = request.form.get('system_code', '').strip()  # Added system_code
            status_id_raw = request.form.get('status_id')
            try:
                status_id = int(status_id_raw) if status_id_raw and status_id_raw.strip() else None
            except (ValueError, TypeError):
                status_id = None
            if status_id is not None:
                valid_status = db_execute(
                    'SELECT 1 FROM customer_status WHERE id = ?',
                    (status_id,),
                    fetch='one',
                )
                if not valid_status:
                    raise ValueError('Invalid customer status selected')
            currency_raw = request.form.get('currency_id')
            try:
                currency_id = int(currency_raw) if currency_raw and currency_raw.strip() else None
            except (ValueError, TypeError):
                currency_id = None

            # Handle website
            website = request.form.get('website', '').strip()
            if website:
                if not website.startswith(('http://', 'https://')):
                    website = 'https://' + website
                website = website.lower().rstrip('/')
                if website.startswith(('http://www.', 'https://www.')):
                    website = website.replace('www.', '', 1)

            # Debug print before update
            print("About to update customer with:", flush=True)
            print(f"customer_id: {customer_id}", flush=True)
            print(f"name: {name}", flush=True)
            print(f"primary_contact_id: {primary_contact_id}", flush=True)
            print(f"salesperson_id: {salesperson_id}", flush=True)
            print(f"website: {website}", flush=True)
            print(f"country: {country}", flush=True)
            print(f"system_code: {system_code}", flush=True)

            notes = request.form.get('notes', '')

            # Print for debugging
            print(f"Notes: {notes}", flush=True)

            # You'll need to update the update_customer function to include system_code
            update_customer(customer_id, name, primary_contact_id, salesperson_id,
                            payment_terms, incoterms, watch, website, notes, country, system_code, currency_id)
            db_execute(
                'UPDATE customers SET status_id = ? WHERE id = ?',
                (status_id, customer_id),
                commit=True,
            )

            # Fix the indentation issue below - this code should be inside the try block
            # but before the return statement
            selected_industries = request.form.getlist('industries[]')
            tags = request.form.get('tags', '').split(',')
            company_types = request.form.getlist('company_types[]')
            raw_supplier_ids = request.form.getlist('used_supplier_ids[]')
            used_supplier_ids = []
            for supplier_id in raw_supplier_ids:
                try:
                    used_supplier_ids.append(int(supplier_id))
                except (TypeError, ValueError):
                    continue

            # Update industries
            delete_customer_industries(customer_id)
            for industry_id in selected_industries:
                insert_customer_industry(customer_id, industry_id)

            # Update tags
            delete_customer_tags(customer_id)
            insert_customer_tags(customer_id, tags)

            # Update company types
            current_company_types = get_company_types_by_customer_id(customer_id)
            for type_id in current_company_types:
                remove_customer_company_type(customer_id, type_id)
            for type_id in company_types:
                insert_customer_company_type(customer_id, type_id)

            _replace_customer_supplier_relationships(customer_id, used_supplier_ids)

            flash("Customer updated successfully", "success")
            return redirect(url_for('customers.edit_customer', customer_id=customer_id))

        except Exception as e:
            print(f"Error updating customer: {str(e)}", flush=True)
            flash(f"Error updating customer: {str(e)}", "error")
            return redirect(url_for('customers.edit_customer', customer_id=customer_id))

    # Fetch data for display

    # Get customer BOMs
    customer_boms = db_execute("""
             SELECT bh.id, bh.name 
             FROM bom_headers bh 
             JOIN customer_boms cb ON bh.id = cb.bom_header_id 
             WHERE cb.customer_id = ?
             ORDER BY bh.name
         """, (customer_id,), fetch='all')

    contacts = get_contacts_by_customer(customer_id)
    salespeople = get_salespeople()
    addresses = get_addresses_by_customer(customer_id)
    updates = get_updates_by_customer_id(customer_id)
    industries = get_industries()
    selected_industries = get_customer_industry(customer_id)
    customer_tags = get_tags_by_customer_id(customer_id)
    company_types = get_all_company_types()
    customer_company_types = get_company_types_by_customer_id(customer_id)

    page = request.args.get('page', 1, type=int)
    per_page = 10

    rfqs = get_rfqs_by_customer_id(customer_id, page, per_page)
    sales_orders = get_sales_orders_by_customer_id(customer_id, page, per_page)

    notes = request.form.get('notes', '')

    breadcrumbs = generate_breadcrumbs(('Home', url_for('index')),
                                       ('Customers', url_for('customers.customers')),
                                       (f'Edit Customer #{customer_id}',
                                        url_for('customers.edit_customer', customer_id=customer_id)))

    # Ensure 'watch' exists for template usage
    if 'watch' not in customer_dict:
        customer_dict['watch'] = 0

    # Load countries for the template
    countries = load_countries()
    currencies = get_currencies()
    customer_statuses = get_customer_statuses()
    suppliers = _get_all_suppliers_basic()
    selected_supplier_ids = _get_customer_supplier_ids(customer_id)

    development_plan = get_customer_development_plan(customer_id)

    return render_template('customer_edit.html',
                           customer=customer_dict,
                           customer_boms=customer_boms,
                           contacts=contacts,
                           salespeople=salespeople,
                           updates=updates,
                           addresses=addresses,
                           industries=industries,
                           development_plan=development_plan,
                           selected_industries=selected_industries,
                           customer_tags=customer_tags,
                           company_types=company_types,
                           customer_company_types=customer_company_types,
                           rfqs=rfqs,
                           customer_notes=get_customer_notes(customer_id),
                           sales_orders=sales_orders,
                           countries=countries,  # Add countries to template context
                           currencies=currencies,
                           customer_statuses=customer_statuses,
                           suppliers=suppliers,
                           selected_supplier_ids=selected_supplier_ids,
                           page=page,
                           per_page=per_page,
                           breadcrumbs=breadcrumbs,
                           can_edit=can_edit)


@customers_bp.route('/<int:customer_id>/status', methods=['POST'])
@login_required
def update_customer_status(customer_id):
    customer = get_customer_by_id(customer_id)
    if not customer:
        return jsonify({'success': False, 'error': 'Customer not found'}), 404

    customer_dict = dict(customer) if hasattr(customer, 'keys') else customer
    _, can_edit = _get_customer_permission_flags(customer_dict)
    if not can_edit:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403

    payload = request.get_json(silent=True) or request.form
    status_id_raw = payload.get('status_id')

    if status_id_raw in (None, ''):
        status_id = None
    else:
        try:
            status_id = int(status_id_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid status ID'}), 400

    status_name = ''
    if status_id is not None:
        status_row = db_execute(
            'SELECT id, status FROM customer_status WHERE id = ?',
            (status_id,),
            fetch='one',
        )
        if not status_row:
            return jsonify({'success': False, 'error': 'Status not found'}), 400
        status_name = status_row['status']

    db_execute(
        'UPDATE customers SET status_id = ? WHERE id = ?',
        (status_id, customer_id),
        commit=True,
    )

    return jsonify({
        'success': True,
        'status': {
            'id': status_id,
            'name': status_name,
        }
    })

def get_customer_notes(customer_id):
    result = db_execute('SELECT notes FROM customers WHERE id = ?', (customer_id,), fetch='one')
    if not result:
        return ''
    return result['notes'] if 'notes' in result else ''


@customers_bp.route('/<int:customer_id>/development', methods=['GET'])
@login_required
def customer_development(customer_id):
    """Display customer development plan"""

    # Check if customer exists and user has permission (reuse your existing logic)
    customer = get_customer_by_id(customer_id)
    if not customer:
        abort(404)

    # Convert customer to dictionary (reuse your existing logic)
    customer_dict = {}
    for key in customer.keys() if hasattr(customer, 'keys') else []:
        try:
            customer_dict[key] = customer[key]
        except Exception as e:
            print(f"Error accessing key {key}: {str(e)}")

    # Check permissions (reuse your existing permission logic)
    customer_salesperson_id = customer_dict.get('salesperson_id')
    user_salesperson_id = current_user.get_salesperson_id()

    can_view = (current_user.is_administrator() or
                current_user.can(Permission.VIEW_CUSTOMERS) or
                current_user.can(Permission.EDIT_CUSTOMERS) or
                (user_salesperson_id and user_salesperson_id == customer_salesperson_id))

    can_edit = (current_user.is_administrator() or
                current_user.can(Permission.EDIT_CUSTOMERS) or
                (user_salesperson_id and user_salesperson_id == customer_salesperson_id))

    if not can_view:
        abort(403)

    # Get development plan
    development_plan = get_customer_development_plan(customer_id)

    # Generate breadcrumbs
    breadcrumbs = generate_breadcrumbs(
        ('Home', url_for('index')),
        ('Customers', url_for('customers.customers')),
        (customer_dict.get('name', f'Customer #{customer_id}'),
         url_for('customers.edit_customer', customer_id=customer_id)),
        ('Development Plan', url_for('customers.customer_development', customer_id=customer_id))
    )

    return render_template('customer_development.html',
                           customer=customer_dict,
                           development_plan=development_plan,
                           breadcrumbs=breadcrumbs,
                           can_edit=can_edit)


@customers_bp.route('/<int:customer_id>/development/answer', methods=['POST'])
@login_required
def update_development_answer(customer_id):
    """Update a development answer via AJAX"""

    # Check permissions
    customer = get_customer_by_id(customer_id)
    if not customer:
        return jsonify({'success': False, 'error': 'Customer not found'}), 404

    customer_dict = {}
    for key in customer.keys() if hasattr(customer, 'keys') else []:
        try:
            customer_dict[key] = customer[key]
        except Exception as e:
            continue

    customer_salesperson_id = customer_dict.get('salesperson_id')
    user_salesperson_id = current_user.get_salesperson_id()

    can_edit = (current_user.is_administrator() or
                current_user.can(Permission.EDIT_CUSTOMERS) or
                (user_salesperson_id and user_salesperson_id == customer_salesperson_id))

    if not can_edit:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403

    try:
        data = request.get_json()
        development_point_id = data.get('development_point_id')
        answer = data.get('answer', '').strip()

        if not development_point_id:
            return jsonify({'success': False, 'error': 'Missing development point ID'}), 400

        # Update or delete answer
        if answer:
            success = update_customer_development_answer(
                customer_id, development_point_id, answer, current_user.id
            )
        else:
            success = delete_customer_development_answer(customer_id, development_point_id)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Database error'}), 500

    except Exception as e:
        print(f"Error in update_development_answer: {str(e)}")
        return jsonify({'success': False, 'error': 'Server error'}), 500

@customers_bp.route('/<int:customer_id>/update', methods=['POST'])
def update_customer_route(customer_id):
    existing_customer = get_customer_by_id(customer_id) or {}
    name = request.form['name']
    primary_contact_id = request.form['primary_contact_id'] or None
    salesperson_id = request.form.get('salesperson_id')
    payment_terms = request.form['payment_terms']
    incoterms = request.form['incoterms']
    currency_id_raw = request.form.get('currency_id')
    try:
        currency_id = int(currency_id_raw) if currency_id_raw else None
    except (ValueError, TypeError):
        currency_id = None
    watch_field = request.form.get('watch')
    if watch_field is not None:
        watch = watch_field == 'on'
    else:
        watch = bool(existing_customer.get('watch'))

    website = request.form.get('website')
    if website is None:
        website = existing_customer.get('website', '')

    notes = request.form.get('notes')
    if notes is None:
        notes = existing_customer.get('notes', '')

    country = request.form.get('country')
    if country is None:
        country = existing_customer.get('country')
    elif country:
        country = country.upper()

    system_code = request.form.get('system_code')
    if system_code is None:
        system_code = existing_customer.get('system_code', '')

    if currency_id is None:
        currency_id = existing_customer.get('currency_id')

    update_customer(customer_id, name, primary_contact_id, salesperson_id,
                    payment_terms, incoterms, watch, website, notes, country, system_code, currency_id)
    return redirect(url_for('customers.edit_customer', customer_id=customer_id))



@customers_bp.route('/contacts/add', methods=['POST'])
@login_required
def add_contact():
    """Add a new contact via AJAX"""
    try:
        data = request.get_json()

        # Validate required fields
        if not data.get('name') or not data.get('email') or not data.get('customer_id'):
            return jsonify({
                'success': False,
                'error': 'Name, email, and customer_id are required'
            })

        # Handle empty status_id
        status_id = data.get('status_id')
        if status_id == '' or status_id is None:
            status_id = None

        # Add the contact
        contact_id = add_contact_ajax(
            customer_id=data['customer_id'],
            name=data['name'],
            second_name=data.get('second_name', ''),
            email=data['email'],
            job_title=data.get('job_title', ''),
            phone=data.get('phone', ''),
            status_id=status_id
        )

        # Get the added contact with status info
        contact = get_contact_by_id(contact_id)

        return jsonify({
            'success': True,
            'contact': contact
        })

    except Exception as e:
        print(f"Error adding contact: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': 'Failed to add contact'
        }), 500


# Add this to your customers blueprint where other contact-related routes are defined
@customers_bp.route('/contacts/<int:contact_id>/update-notes', methods=['POST'])
def update_contact_notes(contact_id):
    try:
        data = request.get_json()
        notes = data.get('notes', '')

        contact = db_execute('SELECT id FROM contacts WHERE id = ?', (contact_id,), fetch='one')
        if contact is None:
            return jsonify({'success': False, 'error': 'Contact not found'}), 404

        # Update only the notes field and timestamp
        db_execute('''
            UPDATE contacts 
            SET notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (notes, contact_id), commit=True)

        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

def update_contact_customer_id(contact_id, customer_id):
    db_execute('UPDATE contacts SET customer_id = ? WHERE id = ?', (customer_id, contact_id), commit=True)


@customers_bp.route('/<int:customer_id>/add_update', methods=['POST'])
@login_required
def add_update(customer_id):
    print(f"DEBUG: add_update called for customer_id: {customer_id}")
    print(f"DEBUG: Request method: {request.method}")
    print(f"DEBUG: Request headers: {dict(request.headers)}")
    print(f"DEBUG: Form data: {dict(request.form)}")

    # Check for AJAX request early and log it
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    print(f"DEBUG: Is AJAX request: {is_ajax}")
    print(f"DEBUG: X-Requested-With header: '{request.headers.get('X-Requested-With')}'")

    try:
        # Get salesperson_id from link table
        print(f"DEBUG: Looking up salesperson for user_id: {current_user.id}")
        result = db_execute(
            'SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?',
            (current_user.id,),
            fetch='one'
        )
        print(f"DEBUG: Salesperson lookup result: {result}")

        if not result:
            error_msg = 'User not linked to a salesperson account'
            print(f"DEBUG: Error - {error_msg}")
            if is_ajax:
                return jsonify({'success': False, 'error': error_msg})
            flash(error_msg)
            return redirect(url_for('customers.edit_customer', customer_id=customer_id))

        salesperson_id = result['legacy_salesperson_id']
        print(f"DEBUG: Found salesperson_id: {salesperson_id}")

        # Get form data
        update_type = request.form.get('update_type', 'generic')
        update_text = request.form.get('update_text', '')
        contact_id = request.form.get('contact_id')
        update_date = request.form.get('update_date')
        update_time = request.form.get('update_time')  # NEW: Get the time input

        print(f"DEBUG: update_type: {update_type}")
        print(f"DEBUG: update_text: '{update_text}'")
        print(f"DEBUG: contact_id: {contact_id}")
        print(f"DEBUG: update_date: {update_date}")
        print(f"DEBUG: update_time: {update_time}")  # NEW: Log the time

        # NEW: Process the date and time together
        from datetime import datetime
        if update_date:
            try:
                if update_time:
                    # Combine date and time
                    datetime_str = f"{update_date} {update_time}"
                    parsed_datetime = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M')
                    print(f"DEBUG: Parsed datetime with time: {parsed_datetime}")
                else:
                    # Just date provided, use current time
                    date_obj = datetime.strptime(update_date, '%Y-%m-%d').date()
                    current_time = datetime.now().time()
                    parsed_datetime = datetime.combine(date_obj, current_time)
                    print(f"DEBUG: Parsed date with current time: {parsed_datetime}")
            except ValueError as e:
                error_msg = f'Invalid date/time format: {update_date} {update_time}'
                print(f"DEBUG: Date/time parsing error - {error_msg}")
                if is_ajax:
                    return jsonify({'success': False, 'error': error_msg})
                flash(error_msg, 'error')
                return redirect(url_for('customers.edit_customer', customer_id=customer_id))
        else:
            # Default to current date and time if none provided
            parsed_datetime = datetime.now()
            print(f"DEBUG: Using default datetime (now): {parsed_datetime}")

        # For email/phone updates, format the text if not provided
        if update_type in ['email', 'phone'] and not update_text:
            print(f"DEBUG: Auto-generating text for {update_type}")
            if contact_id:
                print(f"DEBUG: Looking up contact {contact_id}")
                contact = db_execute(
                    'SELECT name, email FROM contacts WHERE id = ?',
                    (contact_id,),
                    fetch='one'
                )
                print(f"DEBUG: Contact lookup result: {contact}")
                if contact:
                    if update_type == 'email':
                        update_text = f"Emailed {contact['name']} ({contact['email']})"
                    else:  # phone
                        update_text = f"Called {contact['name']}"
                    print(f"DEBUG: Generated update_text: '{update_text}'")
            else:
                # Generic update for the communication type without a specific contact
                update_text = "Emailed customer" if update_type == 'email' else "Called customer"
                print(f"DEBUG: Generated generic update_text: '{update_text}'")

        print(f"DEBUG: Final update_text: '{update_text}'")
        print(f"DEBUG: About to call insert_update with:")
        print(f"  - customer_id: {customer_id}")
        print(f"  - salesperson_id: {salesperson_id}")
        print(f"  - update_text: '{update_text}'")
        print(f"  - contact_id: {contact_id}")
        print(f"  - communication_type: {update_type if update_type != 'generic' else None}")
        print(f"  - update_date: {parsed_datetime}")  # CHANGED: Now using parsed_datetime

        # Insert the update and contact communication record if applicable
        # MODIFIED: Pass the datetime to insert_update function
        insert_result = insert_update(
            customer_id,
            salesperson_id,
            update_text,
            contact_id=contact_id,
            communication_type=update_type if update_type != 'generic' else None,
            update_date=parsed_datetime  # CHANGED: Now using parsed_datetime
        )
        print(f"DEBUG: insert_update returned: {insert_result}")

        # FIXED: Return JSON for AJAX requests immediately after success
        if is_ajax:
            print("DEBUG: Returning JSON success response")
            return jsonify({'success': True, 'message': 'Update added successfully'})

        # Only reach here for non-AJAX requests
        print("DEBUG: Flashing success message and redirecting")
        flash('Update added successfully!', 'success')
        return redirect(url_for('customers.edit_customer', customer_id=customer_id))

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"DEBUG: Exception occurred: {str(e)}")
        print(f"DEBUG: Full traceback:\n{error_details}")

        if is_ajax:
            return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500
        flash(f'Error adding update: {str(e)}', 'error')
        return redirect(url_for('customers.edit_customer', customer_id=customer_id))


@customers_bp.route('/update/<int:update_id>/edit', methods=['POST'])
@login_required
def edit_update(update_id):
    print(f"DEBUG: edit_update called for update_id: {update_id}")
    print(f"DEBUG: Request method: {request.method}")
    print(f"DEBUG: Form data: {dict(request.form)}")

    # Check for AJAX request
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    print(f"DEBUG: Is AJAX request: {is_ajax}")

    current_update = None
    try:
        with db_cursor(commit=True) as cursor:
            # Verify the update exists and get current data
            cursor.execute('''
                SELECT cu.id, cu.date, cu.customer_id, cu.salesperson_id, cu.update_text, cu.communication_type,
                       cc.contact_id, cc.id as communication_id
                FROM customer_updates cu
                LEFT JOIN contact_communications cc ON cu.id = cc.update_id
                WHERE cu.id = ?
            ''', (update_id,))

            current_update = cursor.fetchone()
            print(f"DEBUG: Current update data: {current_update}")

            if not current_update:
                error_msg = 'Update not found'
                print(f"DEBUG: Error - {error_msg}")
                if is_ajax:
                    return jsonify({'success': False, 'error': error_msg}), 404
                flash(error_msg, 'error')
                return redirect(url_for('customers.list_customers'))

            # Verify user has access to this update (through salesperson link)
            cursor.execute(
                'SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?',
                (current_user.id,)
            )
            user_salesperson = cursor.fetchone()

            if not user_salesperson or user_salesperson['legacy_salesperson_id'] != current_update['salesperson_id']:
                error_msg = 'Unauthorized to edit this update'
                print(f"DEBUG: Error - {error_msg}")
                if is_ajax:
                    return jsonify({'success': False, 'error': error_msg}), 403
                flash(error_msg, 'error')
                return redirect(url_for('customers.edit_customer', customer_id=current_update['customer_id']))

            # Get form data
            update_text = request.form.get('update_text', '').strip()
            update_date = request.form.get('update_date')
            communication_type = request.form.get('communication_type', 'generic')
            contact_id = request.form.get('contact_id')

            # Convert empty string to None for contact_id
            if contact_id == '' or contact_id == 'null':
                contact_id = None
            elif contact_id:
                contact_id = int(contact_id)

            print(f"DEBUG: New values:")
            print(f"  - update_text: '{update_text}'")
            print(f"  - update_date: {update_date}")
            print(f"  - communication_type: {communication_type}")
            print(f"  - contact_id: {contact_id}")

            # Validate required fields
            if not update_text:
                error_msg = 'Update text is required'
                if is_ajax:
                    return jsonify({'success': False, 'error': error_msg}), 400
                flash(error_msg, 'error')
                return redirect(url_for('customers.edit_customer', customer_id=current_update['customer_id']))

            # Process the date
            from datetime import datetime
            if update_date:
                try:
                    parsed_date = datetime.strptime(update_date, '%Y-%m-%d')
                    formatted_date = parsed_date.strftime('%Y-%m-%d %H:%M:%S')
                    print(f"DEBUG: Parsed date: {formatted_date}")
                except ValueError as e:
                    error_msg = f'Invalid date format: {update_date}'
                    print(f"DEBUG: Date parsing error - {error_msg}")
                    if is_ajax:
                        return jsonify({'success': False, 'error': error_msg}), 400
                    flash(error_msg, 'error')
                    return redirect(url_for('customers.edit_customer', customer_id=current_update['customer_id']))
            else:
                # Keep existing date if none provided
                formatted_date = current_update['date']
                print(f"DEBUG: Keeping existing date: {formatted_date}")

            # Handle contact change - if contact changes, update customer_id
            new_customer_id = current_update['customer_id']  # Default to current

            if contact_id:
                cursor.execute('SELECT customer_id, name FROM contacts WHERE id = ?', (contact_id,))
                contact_info = cursor.fetchone()

                if not contact_info:
                    error_msg = f'Contact with ID {contact_id} not found'
                    if is_ajax:
                        return jsonify({'success': False, 'error': error_msg}), 400
                    flash(error_msg, 'error')
                    return redirect(url_for('customers.edit_customer', customer_id=current_update['customer_id']))

                new_customer_id = contact_info['customer_id']
                print(f"DEBUG: Contact {contact_info['name']} belongs to customer {new_customer_id}")

                if communication_type in ['email', 'phone'] and (
                        not update_text or update_text in ['Emailed customer', 'Called customer']):
                    if communication_type == 'email':
                        cursor.execute('SELECT email FROM contacts WHERE id = ?', (contact_id,))
                        contact_email = cursor.fetchone()
                        if contact_email and contact_email['email']:
                            update_text = f"Emailed {contact_info['name']} ({contact_email['email']})"
                        else:
                            update_text = f"Emailed {contact_info['name']}"
                    else:
                        update_text = f"Called {contact_info['name']}"
                    print(f"DEBUG: Auto-generated update_text: '{update_text}'")

            if communication_type == 'generic':
                communication_type = None

            print(f"DEBUG: Final values:")
            print(f"  - new_customer_id: {new_customer_id}")
            print(f"  - formatted_date: {formatted_date}")
            print(f"  - update_text: '{update_text}'")
            print(f"  - communication_type: {communication_type}")
            print(f"  - contact_id: {contact_id}")

            cursor.execute('''
                UPDATE customer_updates 
                SET date = ?, customer_id = ?, update_text = ?, communication_type = ?
                WHERE id = ?
            ''', (formatted_date, new_customer_id, update_text, communication_type, update_id))

            print(f"DEBUG: Updated customer_updates table")

            if current_update['communication_id']:
                if contact_id and communication_type:
                    cursor.execute('''
                        UPDATE contact_communications 
                        SET date = ?, contact_id = ?, customer_id = ?, communication_type = ?, notes = ?
                        WHERE id = ?
                    ''', (formatted_date, contact_id, new_customer_id, communication_type, update_text,
                          current_update['communication_id']))
                    print(f"DEBUG: Updated existing contact_communications record")
                else:
                    cursor.execute('DELETE FROM contact_communications WHERE id = ?', (current_update['communication_id'],))
                    print(f"DEBUG: Deleted contact_communications record")
            elif contact_id and communication_type:
                cursor.execute('''
                    INSERT INTO contact_communications 
                    (date, contact_id, customer_id, salesperson_id, communication_type, notes, update_id) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (formatted_date, contact_id, new_customer_id, current_update['salesperson_id'], communication_type,
                      update_text, update_id))
                print(f"DEBUG: Created new contact_communications record")

        if is_ajax:
            return jsonify({
                'success': True,
                'message': 'Update modified successfully',
                'new_customer_id': new_customer_id
            })

        flash('Update modified successfully!', 'success')
        return redirect(url_for('customers.edit_customer', customer_id=new_customer_id))
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"DEBUG: Exception occurred: {str(e)}")
        print(f"DEBUG: Full traceback:\n{error_details}")

        if is_ajax:
            return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500
        flash(f'Error updating: {str(e)}', 'error')
        return redirect(url_for('customers.edit_customer',
                                customer_id=current_update['customer_id'] if current_update else 1))


@customers_bp.route('/update/<int:update_id>/delete', methods=['POST'])
@login_required
def delete_update(update_id):
    print(f"DEBUG: delete_update called for update_id: {update_id}")

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    update_info = None
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute('''
                SELECT cu.customer_id, cu.salesperson_id, cc.id as communication_id
                FROM customer_updates cu
                LEFT JOIN contact_communications cc ON cu.id = cc.update_id
                WHERE cu.id = ?
            ''', (update_id,))

            update_info = cursor.fetchone()

            if not update_info:
                error_msg = 'Update not found'
                if is_ajax:
                    return jsonify({'success': False, 'error': error_msg}), 404
                flash(error_msg, 'error')
                return redirect(url_for('customers.list_customers'))

            cursor.execute(
                'SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?',
                (current_user.id,)
            )
            user_salesperson = cursor.fetchone()

            if not user_salesperson or user_salesperson['legacy_salesperson_id'] != update_info['salesperson_id']:
                error_msg = 'Unauthorized to delete this update'
                if is_ajax:
                    return jsonify({'success': False, 'error': error_msg}), 403
                flash(error_msg, 'error')
                return redirect(url_for('customers.edit_customer', customer_id=update_info['customer_id']))

            if update_info['communication_id']:
                cursor.execute('DELETE FROM contact_communications WHERE id = ?', (update_info['communication_id'],))
                print(f"DEBUG: Deleted contact_communications record")

            cursor.execute('DELETE FROM customer_updates WHERE id = ?', (update_id,))
            print(f"DEBUG: Deleted customer_updates record")

        if is_ajax:
            return jsonify({'success': True, 'message': 'Update deleted successfully'})

        flash('Update deleted successfully!', 'success')
        return redirect(url_for('customers.edit_customer', customer_id=update_info['customer_id']))
    except Exception as e:
        print(f"DEBUG: Error deleting update: {str(e)}")

        if is_ajax:
            return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500
        flash(f'Error deleting update: {str(e)}', 'error')
        return redirect(url_for('customers.edit_customer',
                                customer_id=update_info['customer_id'] if update_info else 1))

@customers_bp.route('/<int:customer_id>/add_address', methods=['POST'])
def add_customer_address(customer_id):
    try:
        address = request.form['address']
        city = request.form['city']
        postal_code = request.form['postal_code']
        country = request.form['country']
        is_default_shipping = 'is_default_shipping' in request.form
        is_default_invoicing = 'is_default_invoicing' in request.form

        # Insert the new address into the database
        db_execute(
            '''INSERT INTO customer_addresses 
               (customer_id, address, city, postal_code, country, is_default_shipping, is_default_invoicing) 
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (customer_id, address, city, postal_code, country, is_default_shipping, is_default_invoicing),
            commit=True
        )

        return redirect(url_for('customers.edit_customer', customer_id=customer_id))
    except Exception as e:
        flash(f'Error adding address: {str(e)}')
        return redirect(url_for('customers.edit_customer', customer_id=customer_id))

@customers_bp.route('/latest_activity', methods=['GET'])
def customers_latest_activity_view():
    # Use the existing helper function to fetch all customers
    customers = get_customers()

    # Add latest activity for each customer
    customer_data = []
    for customer in customers:
        customer_id = customer['id']  # Assuming 'id' is the key for customer ID
        latest_activity = get_latest_activity(customer_id)

        # Debugging: print out the customer name and latest activity
        print(f"Customer: {customer['name']}, Latest Activity: {latest_activity}")

        customer_data.append({
            'id': customer_id,
            'name': customer['name'],
            'latest_activity': latest_activity
        })

    return render_template('customers_latest_activity.html', customers=customer_data)


@customers_bp.route('/prospecting', methods=['GET', 'POST'])
def prospecting():
    logging.debug("Entered the prospecting route")
    logging.debug(f"Request Method: {request.method}")
    logging.debug(f"Headers: {dict(request.headers)}")

    if request.method == 'POST':
        logging.debug(f"Form Data Keys: {list(request.form.keys())}")
        for key in request.form:
            logging.debug(f"Form Key: {key}, Value: {request.form[key]}")

    # Initialize all variables at the start
    selected_tag_id = None
    selected_continent = None
    selected_countries = []
    customers = []
    if selected_tag_id or selected_continent or selected_countries:
        customers = get_all_customers()
        logging.debug(f"Starting with {len(customers)} total customers")
    tag_description = None
    industry_insights = []
    ai_prompt = ""
    available_countries = []

    watched_tags_url = url_for("api.get_current_watched_tags", _external=True)
    watched_tags_response = requests.get(watched_tags_url)

    if watched_tags_response.status_code == 200:
        watched_tags_data = watched_tags_response.json()  # Convert API JSON response to Python list
        watched_tags = {tag['id'] for tag in watched_tags_data}  # Extract just the IDs
    else:
        watched_tags = set()  # Default to empty set if API fails

    print("Watched Tags:", watched_tags)

    # Get base data
    tags = get_nested_tags()
    salespeople = get_salespeople()
    continent_mapping = get_countries_by_continent()
    continents = list(continent_mapping.keys())

    # Get current filters from request
    if request.method == 'GET':
        selected_tag_id = request.args.get('tag')
        selected_continent = request.args.get('continent')
        selected_countries = request.args.getlist('countries')
        logging.debug(
            f"GET request filters - Tag: {selected_tag_id}, Continent: {selected_continent}, Countries: {selected_countries}")

    if request.method == 'POST':
        selected_tag_id = request.form.get('selected_tag_id')
        selected_continent = request.form.get('continent')
        selected_countries = request.form.getlist('countries')
        logging.debug(
            f"POST request filters - Tag: {selected_tag_id}, Continent: {selected_continent}, Countries: {selected_countries}")
        logging.debug(f"Form Data: {dict(request.form)}")

    # Check if it's an AJAX request
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    logging.debug(f"Is AJAX request: {is_ajax}")

    # Start with all customers and apply filters progressively
    customers = get_all_customers()
    logging.debug(f"Starting with {len(customers)} total customers")

    # Apply tag filter first
    if selected_tag_id:
        logging.debug(f"Applying tag filter for ID: {selected_tag_id}")
        tag_customers = get_customers_by_tag(selected_tag_id)
        tag_customer_ids = {c['id'] for c in tag_customers}
        customers = [c for c in customers if c['id'] in tag_customer_ids]
        logging.debug(f"After tag filter: {len(customers)} customers")
        tag_description = get_tag_description(selected_tag_id)

        # Generate preview prompt whenever a tag is selected
        customer_names = [customer['name'] for customer in customers]
        ai_prompt = generate_preview_prompt(
            customer_names,
            tag_description,
            continent=selected_continent,
            countries=selected_countries
        )
        logging.debug(f"Generated preview prompt: {ai_prompt}")

    # Then apply geography filters
    if selected_continent:
        logging.debug(f"Applying continent filter: {selected_continent}")
        continent_customers = get_customers_by_continent(selected_continent)
        continent_customer_ids = {c['id'] for c in continent_customers}
        customers = [c for c in customers if c['id'] in continent_customer_ids]
        logging.debug(f"After continent filter: {len(customers)} customers")

        if selected_countries and any(selected_countries):
            country_customer_ids = set()
            for country_code in selected_countries:
                if country_code:
                    country_customers = get_customers_by_country(country_code)
                    country_customer_ids.update(c['id'] for c in country_customers)
            customers = [c for c in customers if c['id'] in country_customer_ids]
            logging.debug(f"After country filter: {len(customers)} customers")

    # Get available countries for selected continent
    if selected_continent:
        available_countries = get_available_countries(selected_continent, selected_tag_id)

    # Handle AJAX requests
    if is_ajax:
        # Handle country list request
        if request.method == 'GET':
            return jsonify({
                'available_countries': available_countries
            })

        # Handle generate insights request
        if request.method == 'POST' and 'generate_insights' in request.form:
            try:
                customer_names = [customer['name'] for customer in customers]
                logging.debug("Processing AJAX generate insights request")
                logging.debug(f"Customer names: {customer_names}")

                # Use custom prompt if provided, otherwise generate default prompt
                custom_prompt = request.form.get('custom_prompt')
                if custom_prompt and custom_prompt.strip():
                    ai_prompt = custom_prompt.strip()
                else:
                    ai_prompt = generate_preview_prompt(
                        customer_names,
                        tag_description,
                        continent=selected_continent,
                        countries=selected_countries
                    )
                logging.debug(f"Using prompt: {ai_prompt}")

                industry_insights, _ = generate_industry_insights_with_custom_prompt(
                    ai_prompt,
                    customer_names
                )
                logging.debug(f"Generated {len(industry_insights)} insights")

                # Return just the insights section HTML
                insights_html = render_template(
                    'partials/industry_insights.html',
                    industry_insights=industry_insights
                )

                return jsonify({
                    'success': True,
                    'html': insights_html
                })

            except Exception as e:
                logging.error(f"Error in AJAX generate insights: {str(e)}")
                return jsonify({
                    'success': False,
                    'error': str(e)
                }), 500

    # Handle regular POST request for generating insights
    if request.method == 'POST' and 'generate_insights' in request.form:
        try:
            customer_names = [customer['name'] for customer in customers]
            logging.debug(
                f"Generating insights with geography - Continent: {selected_continent}, Countries: {selected_countries}")

            # Use custom prompt if provided, otherwise generate default prompt
            custom_prompt = request.form.get('custom_prompt')
            if custom_prompt and custom_prompt.strip():
                ai_prompt = custom_prompt.strip()
            else:
                ai_prompt = generate_preview_prompt(
                    customer_names,
                    tag_description,
                    continent=selected_continent,
                    countries=selected_countries
                )

            industry_insights, _ = generate_industry_insights_with_custom_prompt(
                ai_prompt,
                customer_names
            )
        except Exception as e:
            logging.error(f"Error generating industry insights: {str(e)}")
            flash("An error occurred while generating industry insights.", "danger")

    breadcrumbs = generate_breadcrumbs(
        ('Home', url_for('index')),
        ('Prospecting', url_for('customers.prospecting'))
    )

    try:
        # Replace the email query with our activity query
        activity_query = """
        WITH latest_activity AS (
            SELECT 
                customer_id,
                activity_type,
                activity_date,
                status,
                activity_id
            FROM (
                SELECT a.*, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY activity_date DESC) as rn
                FROM (
                    SELECT DISTINCT
                        c.customer_id,
                        'email' as activity_type,
                        e.sent_date as activity_date,
                        CASE 
                            WHEN LOWER(e.sender_email) IN (SELECT LOWER(email) FROM users) THEN 'outbound'
                            ELSE 'received'
                        END as status,
                        e.id as activity_id
                    FROM emails e
                    JOIN contacts c ON (LOWER(e.sender_email) = LOWER(c.email) OR LOWER(e.recipient_email) = LOWER(c.email))

                    UNION ALL

                    SELECT 
                        r.customer_id,
                        'rfq' as activity_type,
                        r.entered_date as activity_date,
                        r.status,
                        r.id as activity_id
                    FROM rfqs r

                    UNION ALL

                    SELECT 
                        so.customer_id,
                        'order' as activity_type,
                        so.date_entered as activity_date,
                        ss.status_name as status,
                        so.id as activity_id
                    FROM sales_orders so
                    LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
                ) a
            ) ranked
            WHERE rn = 1
        ),
        latest_comment AS (
            SELECT 
                customer_id,
                update_text,
                date as update_date,
                s.name as update_salesperson_name
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY date DESC) as rn
                FROM customer_updates
            ) cu
            LEFT JOIN salespeople s ON cu.salesperson_id = s.id
            WHERE rn = 1
        )
        SELECT 
            c.id as customer_id,
            c.country,
            la.activity_date as latest_activity,
            la.activity_type,
            la.status as activity_status,
            lc.update_text,
            lc.update_date,
            lc.update_salesperson_name,
            s.name as assigned_salesperson_name,
            s.id as assigned_salesperson_id
        FROM customers c
        LEFT JOIN latest_activity la ON c.id = la.customer_id
        LEFT JOIN latest_comment lc ON c.id = lc.customer_id
        LEFT JOIN salespeople s ON c.salesperson_id = s.id
        WHERE c.id IN ({})
        """

        customer_ids = [c['id'] for c in customers]

        if customer_ids:
            placeholders = ','.join('?' * len(customer_ids))
            query = activity_query.format(placeholders)
            activities = db_execute(query, customer_ids, fetch='all') or []
        else:
            activities = []

        activity_data = {}
        for row in activities:
            row_data = dict(row) if hasattr(row, 'keys') else row
            key = row_data.get('customer_id')
            if key is not None:
                activity_data[key] = row_data

        for customer in customers:
            data = activity_data.get(customer['id'])
            if not data:
                continue
            customer['country'] = data.get('country')
            customer['latest_activity'] = data.get('latest_activity')
            customer['activity_type'] = data.get('activity_type')
            customer['activity_status'] = data.get('activity_status')
            customer['update_text'] = data.get('update_text')
            customer['update_date'] = data.get('update_date')
            customer['update_salesperson_name'] = data.get('update_salesperson_name')
            customer['assigned_salesperson_name'] = data.get('assigned_salesperson_name')
            customer['assigned_salesperson_id'] = data.get('assigned_salesperson_id')
    except Exception as e:
        logging.error(f"Error fetching latest activity: {e}")

    # Return the rendered template
    return render_template(
        'prospecting.html',
        customers=customers,
        tags=tags,
        watched_tags=watched_tags,
        salespeople=salespeople,
        customer_statuses=get_customer_statuses(),
        selected_tag_id=selected_tag_id,
        breadcrumbs=breadcrumbs,
        industry_insights=industry_insights,
        get_status_name=get_status_name,
        ai_prompt=ai_prompt,
        continents=continents,
        selected_continent=selected_continent,
        available_countries=available_countries,
        selected_countries=selected_countries
    )


def get_filtered_customers(tag_id, continent, countries):
    """Helper function to get filtered customers based on criteria"""
    customers = get_all_customers()

    if tag_id:
        tag_customers = get_customers_by_tag(tag_id)
        tag_customer_ids = {c['id'] for c in tag_customers}
        customers = [c for c in customers if c['id'] in tag_customer_ids]

    if continent:
        continent_customers = get_customers_by_continent(continent)
        continent_customer_ids = {c['id'] for c in continent_customers}
        customers = [c for c in customers if c['id'] in continent_customer_ids]

        if countries and any(countries):
            country_customer_ids = set()
            for country_code in countries:
                if country_code:
                    country_customers = get_customers_by_country(country_code)
                    country_customer_ids.update(c['id'] for c in country_customers)
            customers = [c for c in customers if c['id'] in country_customer_ids]

    return customers

@customers_bp.route('/add_suggested', methods=['POST'])
def add_suggested_customer():
    try:
        data = request.json
        name = data.get('name')
        description = data.get('description')
        estimated_revenue = data.get('estimated_revenue')
        salesperson_id = data.get('salesperson_id')
        tag_id = data.get('tag_id')
        payment_terms = data.get('payment_terms', 'Pro-forma')
        incoterms = data.get('incoterms', 'EXW')
        country = data.get('country')  # Get the country code

        # Insert the customer
        customer_id = insert_customer(
            name=name,
            description=description,
            estimated_revenue=estimated_revenue,
            salesperson_id=salesperson_id,
            payment_terms=payment_terms,
            incoterms=incoterms,
            country=country  # Add country to the insert
        )

        # Add the tag
        if tag_id:
            insert_customer_tag(customer_id, tag_id)

        return jsonify({
            'success': True,
            'customer_id': customer_id,
            'message': f'Successfully added {name} to the customer database'
        })
    except Exception as e:
        logging.error(f"Error adding suggested customer: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@customers_bp.route('/enrich', methods=['POST'])
def enrich_customer():
    """Route to enrich customer data with AI-generated insights"""
    try:
        print("Starting enrich_customer route")
        data = request.get_json()
        print(f"Received data: {data}")

        if not data or 'customer_id' not in data:
            return jsonify({"error": "Missing customer_id"}), HTTPStatus.BAD_REQUEST

        # Updated to unpack three values
        customer, current_tags, current_company_types = get_customer_data(data['customer_id'])
        print(f"Retrieved customer data: {customer}")
        print(f"Retrieved current tags: {current_tags}")
        print(f"Retrieved current company types: {current_company_types}")

        if not customer:
            return jsonify({"error": "Customer not found"}), HTTPStatus.NOT_FOUND

        # Fetch available tags and company types
        available_tags = get_available_tags()
        available_company_types = get_all_company_types()  # If you have this function
        print(f"Available tags: {available_tags}")

        # Format customer data for enrichment
        customer_data = {
            "name": customer['name'],
            "description": customer['description'],
            "website": customer['website'],
            "current_tags": [tag['name'] for tag in (current_tags or [])],
            "current_company_types": [ct['name'] for ct in (current_company_types or [])]
        }

        # Get enrichment suggestions (your existing enrichment logic)
        enrichment_data = enrich_customer_data(customer_data, available_tags)
        print(f"Enrichment suggestions: {enrichment_data}")

        logging.debug(f"Enrichment data before validation: {enrichment_data}")

        # Validate and update
        validate_enrichment_data(enrichment_data, available_tags)
        update_customer_enrichment(data['customer_id'], enrichment_data)

        return jsonify({
            "message": "Customer enriched successfully",
            "enrichment_data": enrichment_data
        }), HTTPStatus.OK

    except Exception as e:
        print(f"Error in enrich_customer: {str(e)}")
        logging.error(f"Error in customer enrichment: {str(e)}")
        return jsonify({"error": str(e)}), HTTPStatus.INTERNAL_SERVER_ERROR

from flask import current_app, jsonify, request
import requests


@customers_bp.route('/<int:customer_id>/apollo_search', methods=['POST'])
def search_apollo_organization(customer_id):
    customer = get_customer(customer_id)
    if customer is None:
        return jsonify({'error': 'Customer not found'}), 404

    # Get search term from request
    data = request.get_json()
    search_term = data.get('q_organization_name', customer['name'])

    # Clean up the search term - remove common business suffixes
    clean_term = search_term.lower()
    for suffix in ['s.r.o.', 'sro', 'ltd', 'limited', 'inc', 'incorporated', 'llc', 'corp', 'corporation']:
        clean_term = clean_term.replace(suffix, '').strip()

    try:
        current_app.logger.info(f"Searching Apollo for company: {clean_term}")

        response = requests.post(
            "https://api.apollo.io/v1/organizations/search",
            headers={
                'X-API-KEY': current_app.config['APOLLO_API_KEY'],
                'Cache-Control': 'no-cache',
                'Content-Type': 'application/json'
            },
            json={
                'api_key': current_app.config['APOLLO_API_KEY'],
                'q_organization_name': clean_term,
                'page': 1,
                'per_page': 10
            }
        )

        current_app.logger.info(f"Apollo response status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            organizations = [{
                'id': org.get('id'),
                'name': org.get('name'),
                'website': org.get('website_url'),
                'linkedin_url': org.get('linkedin_url'),
                'domain': org.get('primary_domain'),
                'description': org.get('description'),
                'country': org.get('country'),
                # Logo URL
                'logo_url': org.get('logo_url'),
                # Industry information
                'primary_industry': org.get('industry'),
                'all_industries': org.get('industries', []),
                'secondary_industries': org.get('secondary_industries', []),
                # Employee information
                'employee_count': org.get('estimated_num_employees'),
                # Additional potentially useful fields
                'keywords': org.get('keywords', []),
                'raw_match': clean_term.lower() in org.get('name', '').lower()
            } for org in data.get('organizations', [])]

            organizations.sort(key=lambda x: (not x['raw_match'], x['name']))

            return jsonify({
                'organizations': organizations,
                'total_results': data.get('pagination', {}).get('total_entries', 0),
                'search_term': clean_term
            })
        else:
            current_app.logger.error(f"Apollo API error: {response.status_code} - {response.text}")
            return jsonify({'error': f'Apollo API error: {response.status_code}'}), response.status_code

    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Request exception: {str(e)}")
        return jsonify({'error': str(e)}), 500


@customers_bp.route('/<int:customer_id>/apollo_match', methods=['POST'])
def match_apollo_organization(customer_id):
    apollo_id = request.json.get('apollo_id')
    if not apollo_id:
        return jsonify({'error': 'Apollo ID is required'}), 400

    # Fetch organization details from Apollo
    try:
        response = requests.get(
            f"https://api.apollo.io/v1/organizations/{apollo_id}",
            headers={
                'X-API-KEY': current_app.config['APOLLO_API_KEY'],
                'Cache-Control': 'no-cache'
            },
            params={
                'api_key': current_app.config['APOLLO_API_KEY']
            }
        )

        if response.status_code == 200:
            org_data = response.json().get('organization', {})
            logo_url = org_data.get('logo_url')
            website = org_data.get('website_url')

            # Update customer with Apollo data
            try:
                db_execute(
                    'UPDATE customers SET apollo_id = ?, logo_url = ?, website = ? WHERE id = ?',
                    (apollo_id, logo_url, website, customer_id),
                    commit=True
                )
                return jsonify({'success': True, 'message': 'Successfully matched with Apollo'})
            except Exception as e:
                logger.exception("Failed to update customer with Apollo data")
                return jsonify({'error': 'Failed to update customer'}), 500
        else:
            return jsonify({'error': f'Apollo API error: {response.status_code}'}), response.status_code

    except Exception as e:
        print(f"Error fetching Apollo organization: {e}")
        return jsonify({'error': str(e)}), 500


# Backend: Modified get_customer_leads route with "all" option
@customers_bp.route('/<int:customer_id>/leads', methods=['GET'])
def get_customer_leads(customer_id):
    customer = get_customer(customer_id)
    if customer is None:
        return jsonify({'error': 'Customer not found'}), 404

    if not customer.get('apollo_id'):
        return jsonify({'error': 'Customer not matched with Apollo'}), 400

    try:
        current_app.logger.info(f"Searching Apollo for leads at company ID: {customer['apollo_id']}")

        # Get search type from query params, default to procurement
        search_type = request.args.get('type', 'procurement')

        # Base request payload
        try:
            page = int(request.args.get('page', 1))
        except (TypeError, ValueError):
            page = 1
        per_page = 10  # Increased from 5 for better overview

        payload = {
            'organization_ids': [customer['apollo_id']],
            'page': page,
            'per_page': per_page,
        }

        # Add filters based on search type
        if search_type == 'procurement':
            payload['person_titles'] = [
                'buyer',
                'purchasing',
                'procurement',
                'supply chain',
                'operations',
                'sourcing',
                'planning',
                'logistics',
                'material management',
                'vendor management',
                'supplier relations',
                'category management',
                'contract management',
                'spend analysis',
                'acheteur',
                'acheteuse',
                'achat',
                'inköpare',
                'inköp',
                'einkauf',
                'einkäufer',
                'einkäuferin',
                'indkøb',
                'indkøber',
                'inkopping',
                'beszerzés',
                'beszerző',
                'zamówienia',
                'zakupy',
                'acquisti',
                'responsabile acquisti',
                'compras',
                'gestão de compras',
                'gestión de compras',
                'purchase',
                'suministros',
                'maintenance'
            ]
        elif search_type == 'general':
            # For general search, filter by seniority
            payload['person_seniorities'] = ['director', 'executive', 'vp', 'owner']
        # If search_type == 'all', we don't add any filters - just organization_ids

        url = f"{current_app.config['APOLLO_BASE_URL']}/mixed_people/api_search"

        response = requests.post(
            url,
            headers={
                'X-API-KEY': current_app.config['APOLLO_API_KEY'],
                'Cache-Control': 'no-cache',
                'Content-Type': 'application/json'
            },
            json=payload
        )

        if response.status_code == 200:
            data = response.json()

            pagination = data.get('pagination') or {
                'page': data.get('page', page),
                'per_page': data.get('per_page', per_page),
                'total_entries': data.get('total_entries')
            }

            def has_email_available(person):
                if person.get('has_email') is True:
                    return True
                email = person.get('email')
                if email:
                    return True
                status = (person.get('email_status') or '').lower()
                return status in {'verified', 'unverified', 'guessed', 'likely', 'available'}
            
            def has_phone_available(person):
                direct = person.get('has_direct_phone')
                if isinstance(direct, str):
                    return direct.strip().lower() in {'yes', 'true', '1'}
                if direct is True:
                    return True
                return bool(person.get('phone_numbers'))

            leads = [{
                'id': person.get('id'),
                'name': f"{person.get('first_name', '')} {person.get('last_name', '')}".strip(),
                'title': person.get('title'),
                'email_status': person.get('email_status'),
                'email_available': has_email_available(person),
                'has_email': person.get('has_email'),
                'linkedin_url': person.get('linkedin_url'),
                'seniority': person.get('seniority'),
                'organization': person.get('organization', {}).get('name'),
                'department': person.get('department'),
                'city': person.get('city'),
                'state': person.get('state'),
                'phone_available': has_phone_available(person),
                'has_direct_phone': person.get('has_direct_phone')
            } for person in data.get('people', [])]

            return jsonify({
                'leads': leads,
                'pagination': pagination,
                'search_type': search_type,
                'has_results': bool(leads)
            })
        else:
            error_message = f"Apollo API error ({response.status_code}): {response.text}"
            current_app.logger.error(error_message)
            return jsonify({'error': error_message}), response.status_code

    except requests.exceptions.RequestException as e:
        error_message = f"Request failed: {str(e)}"
        current_app.logger.error(error_message)
        return jsonify({'error': error_message}), 500

@customers_bp.route('/<int:customer_id>', methods=['GET'])
@login_required
def get_customer_details(customer_id):
    customer = get_customer(customer_id)
    if customer is None:
        return jsonify({'success': False, 'error': 'Customer not found'}), 404
    return jsonify({'success': True, 'customer': customer})


@customers_bp.route('/enrich_person', methods=['POST'])
def enrich_person():
    data = request.get_json()
    apollo_id = data.get('apollo_id')

    if not apollo_id:
        return jsonify({'success': False, 'error': 'Apollo ID is required'}), 400

    url = f"https://api.apollo.io/api/v1/people/match"
    headers = {
        "accept": "application/json",
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        'X-API-KEY': current_app.config['APOLLO_API_KEY']
    }

    # Add parameters for enrichment
    params = {
        "id": apollo_id,
        "reveal_personal_emails": False,
        "reveal_phone_number": False
    }

    try:
        response = requests.post(url, headers=headers, params=params)
        response.raise_for_status()
        enriched_data = response.json()

        # Extract relevant information from the enriched data
        person = enriched_data.get('person', {})
        email = person.get('email')
        phone = person.get('sanitized_phone') or person.get('phone')
        if not phone:
            phone_numbers = person.get('phone_numbers') or []
            if phone_numbers:
                phone = phone_numbers[0].get('sanitized_number') or phone_numbers[0].get('raw_number')
        if not phone:
            contact = person.get('contact') or {}
            phone = contact.get('sanitized_phone') or contact.get('phone')
            if not phone:
                contact_numbers = contact.get('phone_numbers') or []
                if contact_numbers:
                    phone = contact_numbers[0].get('sanitized_number') or contact_numbers[0].get('raw_number')

        return jsonify({
            'success': True,
            'data': {
                'email': email,
                'name': person.get('name'),
                'title': person.get('title'),
                'phone': phone
            }
        })
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/list', methods=['GET'])
def list_customers():
    try:
        # Start with getting all customers
        customers = get_all_customers()

        # Get filter parameters
        tag_id = request.args.get('tag')
        continent = request.args.get('continent')
        countries = request.args.getlist('countries')

        # Apply tag filter if specified
        if tag_id:
            tag_customers = get_customers_by_tag(tag_id)
            tag_customer_ids = {c['id'] for c in tag_customers}
            customers = [c for c in customers if c['id'] in tag_customer_ids]

        # Apply continent filter if specified
        if continent:
            continent_customers = get_customers_by_continent(continent)
            continent_customer_ids = {c['id'] for c in continent_customers}
            customers = [c for c in customers if c['id'] in continent_customer_ids]

            # Apply country filter if specified
            if countries and any(countries):
                country_customer_ids = set()
                for country_code in countries:
                    if country_code:
                        country_customers = get_customers_by_country(country_code)
                        country_customer_ids.update(c['id'] for c in country_customers)
                customers = [c for c in customers if c['id'] in country_customer_ids]

        # Get last email dates for all filtered customers
        last_emails = {}
        for customer in customers:
            query = """
                SELECT MAX(e.sent_date) as last_email
                FROM emails e
                JOIN contacts c ON e.recipient_email = c.email
                WHERE c.customer_id = ?
            """
            result = db_execute(query, (customer['id'],), fetch='one')
            last_email = result['last_email'] if result and result['last_email'] else None
            last_emails[customer['id']] = last_email.isoformat() if last_email else None

        # Format the response data
        customer_data = [{
            'id': customer['id'],
            'name': customer['name'],
            'status_id': customer['status_id'],
            'estimated_revenue': customer['estimated_revenue'],
            'last_email': last_emails[customer['id']]
        } for customer in customers]

        return jsonify({
            'success': True,
            'customers': customer_data
        })

    except Exception as e:
        logging.error(f"Error in list_customers: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to fetch customers'
        }), 500

@customers_bp.route('/<int:customer_id>/matching_templates')
def get_matching_templates(customer_id):
    # Get customer's tags
    customer_tags = get_customer_tags(customer_id)

    # Get templates that match these tags
    matching_templates = get_templates_by_tags(customer_tags)

    return jsonify({
        'success': True,
        'templates': matching_templates
    })


@customers_bp.route('/<int:customer_id>/bump_status', methods=['POST'])
def bump_status(customer_id):
    """Increment customer status using actual available statuses"""
    try:
        # Get all available statuses in order from customer_status table
        available_statuses = db_execute(
            'SELECT id, status FROM customer_status ORDER BY id',
            fetch='all'
        )

        if not available_statuses:
            return jsonify({'success': False, 'error': 'No statuses configured'})

        # Get current customer status
        customer = db_execute(
            'SELECT status_id FROM customers WHERE id = ?',
            (customer_id,),
            fetch='one'
        )

        if not customer:
            return jsonify({'success': False, 'error': 'Customer not found'})

        current_status_id = customer['status_id']
        status_ids = [row['id'] for row in available_statuses]

        # Find next status
        if current_status_id is None or current_status_id not in status_ids:
            # If no status or invalid status, start with first
            next_status_id = status_ids[0]
        else:
            # Find current position and move to next (or cycle back to first)
            current_index = status_ids.index(current_status_id)
            next_index = (current_index + 1) % len(status_ids)
            next_status_id = status_ids[next_index]

        # Update customer
        db_execute(
            'UPDATE customers SET status_id = ? WHERE id = ?',
            (next_status_id, customer_id),
            commit=True
        )

        # Get the new status name
        new_status = next((s for s in available_statuses if s['id'] == next_status_id), None)

        return jsonify({
            'success': True,
            'new_status': {
                'id': next_status_id,
                'name': new_status['status'] if new_status else f'Status {next_status_id}'
            }
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@customers_bp.route('/<int:customer_id>/bump_priority', methods=['POST'])
def bump_priority(customer_id):
    """Increment customer priority using actual available priorities"""
    try:
        # Get all available priorities in order from priorities table
        available_priorities = db_execute(
            'SELECT id, name, color FROM priorities ORDER BY id',
            fetch='all'
        )

        if not available_priorities:
            return jsonify({'success': False, 'error': 'No priorities configured'})

        # Get current customer priority
        customer = db_execute(
            'SELECT priority FROM customers WHERE id = ?',
            (customer_id,),
            fetch='one'
        )

        if not customer:
            return jsonify({'success': False, 'error': 'Customer not found'})

        current_priority_id = customer['priority']
        priority_ids = [row['id'] for row in available_priorities]

        # Find next priority
        if current_priority_id is None or current_priority_id not in priority_ids:
            # If no priority or invalid priority, start with first
            next_priority_id = priority_ids[0]
        else:
            # Find current position and move to next (or cycle back to first)
            current_index = priority_ids.index(current_priority_id)
            next_index = (current_index + 1) % len(priority_ids)
            next_priority_id = priority_ids[next_index]

        # Update customer
        db_execute(
            'UPDATE customers SET priority = ? WHERE id = ?',
            (next_priority_id, customer_id),
            commit=True
        )

        # Get the new priority details
        new_priority = next((p for p in available_priorities if p['id'] == next_priority_id), None)

        return jsonify({
            'success': True,
            'new_priority': {
                'id': next_priority_id,
                'name': new_priority['name'] if new_priority else f'Priority {next_priority_id}',
                'color': new_priority['color'] if new_priority else '#f8f9fa'
            }
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@customers_bp.route('/<int:customer_id>/rfqs_orders', methods=['GET'])
def rfqs_orders(customer_id):
    # Get pagination parameters from the query string
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Number of items per page

    # Fetch sorted and paginated RFQs and sales orders
    rfqs = get_rfqs_by_customer_id(customer_id, page, per_page)
    sales_orders = get_sales_orders_by_customer_id(customer_id, page, per_page)

    return render_template('customer_rfqs_orders.html',
                           rfqs=rfqs,
                           sales_orders=sales_orders,
                           page=page,
                           per_page=per_page)


@customers_bp.route('/search')
def customer_search():
    # Support both 'query' and 'q' parameters for backward compatibility
    query = request.args.get('query', '') or request.args.get('q', '')
    limit = int(request.args.get('limit', 10))
    salesperson_filter_id = request.args.get('salesperson_id', type=int)
    filter_to_parts_lists = str(request.args.get('has_parts_list', '')).lower() not in ('', '0', 'false', 'none')

    if not query:
        return jsonify([])

    # Check if this is a request for the expanded format (has 'q' parameter or explicit limit)
    is_expanded_format = 'q' in request.args or 'limit' in request.args

    additional_where = ""
    additional_params = []
    if filter_to_parts_lists:
        additional_where = " AND EXISTS (SELECT 1 FROM parts_lists pl WHERE pl.customer_id = c.id"
        if salesperson_filter_id:
            additional_where += " AND pl.salesperson_id = ?"
            additional_params.append(salesperson_filter_id)
        additional_where += ")"

    if is_expanded_format:
        # Using actual column names from the customers table schema
        params = (
            f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%',
            f'{query}', f'{query}%', f'%{query}%', f'{query}%', f'%{query}%',
            *additional_params,
            limit
        )
        if _using_postgres():
            sql = f'''
            SELECT c.id, c.name, c.system_code, c.country, c.description,
                   c.website, c.estimated_revenue, c.fleet_size, c.priority,
                   c.status_id, c.salesperson_id, c.budget,
                   pc.email as primary_contact_email,
                   pc.name as primary_contact_name,
                   s.name as assigned_salesperson_name,
                   st.status as status_name,
                   p.name as priority_name,
                   p.color as priority_color
            FROM customers c
            LEFT JOIN contacts pc ON c.primary_contact_id = pc.id
            LEFT JOIN salespeople s ON c.salesperson_id = s.id
            LEFT JOIN customer_status st ON c.status_id = st.id
            LEFT JOIN priorities p ON c.priority = p.id
            WHERE (
                c.name ILIKE ?
                OR c.system_code ILIKE ?
                OR c.description ILIKE ?
                OR c.website ILIKE ?
                OR pc.email ILIKE ?
                OR pc.name ILIKE ?
            )
               {additional_where}
            ORDER BY
                CASE
                    WHEN c.name ILIKE ? THEN 1
                    WHEN c.name ILIKE ? THEN 2
                    WHEN c.name ILIKE ? THEN 3
                    WHEN c.system_code ILIKE ? THEN 4
                    WHEN c.system_code ILIKE ? THEN 5
                    ELSE 6
                END,
                c.name
            LIMIT ?
        '''
        else:
            sql = f'''
            SELECT c.id, c.name, c.system_code, c.country, c.description,
                   c.website, c.estimated_revenue, c.fleet_size, c.priority,
                   c.status_id, c.salesperson_id, c.budget,
                   pc.email as primary_contact_email,
                   pc.name as primary_contact_name,
                   s.name as assigned_salesperson_name,
                   st.status as status_name,
                   p.name as priority_name,
                   p.color as priority_color
            FROM customers c
            LEFT JOIN contacts pc ON c.primary_contact_id = pc.id
            LEFT JOIN salespeople s ON c.salesperson_id = s.id
            LEFT JOIN customer_status st ON c.status_id = st.id
            LEFT JOIN priorities p ON c.priority = p.id
            WHERE (
                LOWER(c.name) LIKE LOWER(?)
                OR LOWER(c.system_code) LIKE LOWER(?)
                OR LOWER(c.description) LIKE LOWER(?)
                OR LOWER(c.website) LIKE LOWER(?)
                OR LOWER(pc.email) LIKE LOWER(?)
                OR LOWER(pc.name) LIKE LOWER(?)
            )
               {additional_where}
            ORDER BY
                CASE
                    WHEN LOWER(c.name) LIKE LOWER(?) THEN 1
                    WHEN LOWER(c.name) LIKE LOWER(?) THEN 2
                    WHEN LOWER(c.name) LIKE LOWER(?) THEN 3
                    WHEN LOWER(c.system_code) LIKE LOWER(?) THEN 4
                    WHEN LOWER(c.system_code) LIKE LOWER(?) THEN 5
                    ELSE 6
                END,
                c.name
            LIMIT ?
        '''
        print("CUSTOMER SEARCH:", sql, params, flush=True)
        customers = db_execute(sql, params, fetch='all')

        return jsonify([dict(row) for row in customers])
    else:
        # Return simple format for backward compatibility
        params = [f'%{query}%', *additional_params, limit]
        if _using_postgres():
            sql = f'''
            SELECT id, name
            FROM customers
            WHERE name ILIKE ?
            {additional_where}
            ORDER BY name
            LIMIT ?
        '''
        else:
            sql = f'''
            SELECT id, name
            FROM customers
            WHERE LOWER(name) LIKE LOWER(?)
            {additional_where}
            ORDER BY name
            LIMIT ?
        '''
        print("CUSTOMER SEARCH (simple):", sql, params, flush=True)
        customers = db_execute(sql, params, fetch='all')

        return jsonify([{
            'id': customer['id'],
            'name': customer['name']
        } for customer in customers])

@customers_bp.route('/contacts/add', methods=['POST'])
def add_new_contact():
    data = request.get_json()
    print("Received data:", data)

    try:
        contact_id = insert_contact(
            customer_id=data['customer_id'],
            name=data['name'],
            email=data['email'],
            second_name=data.get('second_name'),
            job_title=None
        )
        print("Contact created with ID:", contact_id)

        updated = db_execute(
            '''
            UPDATE customers 
            SET primary_contact_id = ?
            WHERE id = ? AND (primary_contact_id IS NULL OR primary_contact_id = 0)
            RETURNING id, primary_contact_id
            ''',
            (contact_id, data['customer_id']),
            fetch='one',
            commit=True
        )
        print("Customer update result:", updated)

        contact = db_execute(
            '''
            SELECT c.*, cu.name as customer_name 
            FROM contacts c
            LEFT JOIN customers cu ON c.customer_id = cu.id
            WHERE c.id = ?
            ''',
            (contact_id,),
            fetch='one'
        )
        print("Final contact data:", dict(contact))

        return jsonify({'success': True, 'contact': dict(contact)})

    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 400

@customers_bp.route('/<int:customer_id>/activity/timeline', methods=['GET'])
def get_timeline(customer_id):
    import time
    start_time = time.time()
    current_app.logger.info(f"Starting timeline fetch for customer {customer_id}")

    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    try:
        with db_cursor() as cursor:
            # Contact emails query timing
            contact_start = time.time()
            current_app.logger.info(f"Fetching contact emails for customer {customer_id}")
            cursor.execute("""
                SELECT LOWER(email) FROM contacts 
                WHERE customer_id = ?
            """, (customer_id,))
            contact_emails = [row[0] for row in cursor.fetchall()]
            current_app.logger.info(f"Found {len(contact_emails)} contact emails in {time.time() - contact_start:.2f}s")
            current_app.logger.debug(f"Contact emails: {contact_emails}")

            # Handle case with no contacts
            if not contact_emails:
                return jsonify({
                    'success': True,
                    'activities': [],
                    'pagination': {
                        'total': 0,
                        'pages': 0,
                        'current_page': page,
                        'per_page': per_page
                    }
                })

            # Build email conditions for query
            sender_placeholders = ','.join(['?' for _ in contact_emails])
            params = contact_emails.copy()  # Add sender emails to params

            # For recipient check, we need LIKE conditions since recipients can be comma-separated
            recipient_conditions = []
            for email in contact_emails:
                recipient_conditions.append("LOWER(recipient_email) LIKE ?")
                params.append(f"%{email}%")

            # Build the complete condition
            email_conditions_sql = f"LOWER(sender_email) IN ({sender_placeholders}) OR ({' OR '.join(recipient_conditions)})"
            current_app.logger.debug(f"Email conditions: {email_conditions_sql}")
            current_app.logger.debug(f"Email parameters: {params}")

            # Timeline query execution timing
            timeline_start = time.time()

            # Execute email part separately
            email_query = f"""
                SELECT 
                    'email' as activity_type,
                    id,
                    sent_date as activity_date,
                    subject as description,
                    sender_email,
                    recipient_email,
                    direction as status,
                    NULL as value
                FROM emails 
                WHERE {email_conditions_sql}
            """
            cursor.execute(email_query, params)
            email_rows = cursor.fetchall()
            current_app.logger.info(f"Email query returned {len(email_rows)} rows")

            # Execute RFQ part separately
            rfq_query = """
                SELECT
                    'rfq' as activity_type,
                    id,
                    entered_date as activity_date,
                    customer_ref as description,
                    NULL as sender_email,
                    NULL as recipient_email,
                    status,
                    NULL as value
                FROM rfqs
                WHERE customer_id = ?
            """
            cursor.execute(rfq_query, (customer_id,))
            rfq_rows = cursor.fetchall()
            current_app.logger.info(f"RFQ query returned {len(rfq_rows)} rows")

            # Execute orders part separately
            order_query = """
                SELECT
                    'order' as activity_type,
                    so.id,
                    so.date_entered as activity_date,
                    so.sales_order_ref as description,
                    NULL as sender_email,
                    NULL as recipient_email,
                    ss.status_name as status,
                    so.total_value as value
                FROM sales_orders so
                LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
                WHERE so.customer_id = ?
            """
            cursor.execute(order_query, (customer_id,))
            order_rows = cursor.fetchall()
            current_app.logger.info(f"Order query returned {len(order_rows)} rows")

            # Execute projects part separately
            project_query = """
                SELECT
                    'project' as activity_type,
                    p.id,
                    p.next_stage_deadline as activity_date,
                    p.name as description,
                    NULL as sender_email,
                    NULL as recipient_email,
                    ps.status as status,
                    p.estimated_value as value
                FROM projects p
                LEFT JOIN project_statuses ps ON p.status_id = ps.id
                WHERE p.customer_id = ?
            """
            cursor.execute(project_query, (customer_id,))
            project_rows = cursor.fetchall()
            current_app.logger.info(f"Project query returned {len(project_rows)} rows")

            # Combine all results
            all_rows = email_rows + rfq_rows + order_rows + project_rows

            # Sort by activity_date (index 2) in descending order
            all_rows.sort(key=lambda x: x[2] if x[2] is not None else "", reverse=True)

            # Apply pagination
            rows = all_rows[offset:offset + per_page]

            current_app.logger.info(f"Timeline query returned {len(rows)} rows in {time.time() - timeline_start:.2f}s")

            # Continue with the rest of your original function
            # Results processing timing
            processing_start = time.time()
            activities = []
            for row in rows:
                activity = {
                    'type': row[0],
                    'id': row[1],
                    'date': row[2],
                    'description': row[3],
                    'status': row[6]
                }

                if row[0] == 'email':
                    activity['sender'] = row[4]
                    activity['recipient'] = row[5]
                elif row[0] in ('order', 'project'):
                    activity['value'] = row[7]

                activities.append(activity)
            current_app.logger.info(f"Processed results in {time.time() - processing_start:.2f}s")

            # Count query timing
            count_start = time.time()

            # Count emails
            cursor.execute(f"SELECT COUNT(*) FROM emails WHERE {email_conditions_sql}", params)
            email_count = cursor.fetchone()[0]

            # Count RFQs
            cursor.execute("SELECT COUNT(*) FROM rfqs WHERE customer_id = ?", (customer_id,))
            rfq_count = cursor.fetchone()[0]

            # Count orders
            cursor.execute("SELECT COUNT(*) FROM sales_orders WHERE customer_id = ?", (customer_id,))
            order_count = cursor.fetchone()[0]

            # Count projects
            cursor.execute("SELECT COUNT(*) FROM projects WHERE customer_id = ?", (customer_id,))
            project_count = cursor.fetchone()[0]

            # Total count
            total_count = email_count + rfq_count + order_count + project_count
            current_app.logger.info(f"Count query completed in {time.time() - count_start:.2f}s")
            current_app.logger.info(
                f"Counts - Emails: {email_count}, RFQs: {rfq_count}, Orders: {order_count}, Projects: {project_count}, Total: {total_count}")

            total_time = time.time() - start_time
            current_app.logger.info(f"Total timeline request completed in {total_time:.2f}s")

            timings = {
                'contact_query': time.time() - contact_start,
                'timeline_query': time.time() - timeline_start,
                'processing': time.time() - processing_start,
                'count_query': time.time() - count_start,
                'total': time.time() - start_time
            }

            return jsonify({
                'success': True,
                'activities': activities,
                'debug': {
                    'timings': timings,
                    'contact_count': len(contact_emails),
                    'activity_count': len(rows)
                },
                'pagination': {
                    'total': total_count,
                    'pages': (total_count + per_page - 1) // per_page,
                    'current_page': page,
                    'per_page': per_page
                }
            })

    except Exception as e:
        current_app.logger.error("Error fetching customer timeline", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal server error'}), 500


@customers_bp.route('/<int:customer_id>/activity/orders', methods=['GET'])
def get_orders(customer_id):
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    try:
        with db_cursor() as cursor:
            query = """
                SELECT 
                    so.id,
                    so.date_entered,
                    so.sales_order_ref,
                    so.customer_po_ref,
                    ss.status_name as status,
                    so.total_value,
                    so.currency_id
                FROM sales_orders so
                LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
                WHERE so.customer_id = ?
                ORDER BY so.date_entered DESC
                LIMIT ? OFFSET ?
            """

            cursor.execute(query, (customer_id, per_page, offset))
            rows = cursor.fetchall()

            orders = [{
                'id': row[0],
                'date': row[1],
                'reference': row[2],
                'po_reference': row[3],
                'status': row[4],
                'value': row[5],
                'currency_id': row[6]
            } for row in rows]

            # Count total for pagination
            cursor.execute(
                "SELECT COUNT(*) FROM sales_orders WHERE customer_id = ?",
                (customer_id,)
            )
            total_count = cursor.fetchone()[0]

        return jsonify({
            'success': True,
            'orders': orders,
            'pagination': {
                'total': total_count,
                'pages': (total_count + per_page - 1) // per_page,
                'current_page': page,
                'per_page': per_page
            }
        })

    except Exception as e:
        current_app.logger.error("Error fetching customer orders", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal server error'}), 500


@customers_bp.route('/<int:customer_id>/activity/emails', methods=['GET'])
def get_emails(customer_id):
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    current_app.logger.info(f"Starting email fetch for customer {customer_id}")
    start_time = time.time()

    try:
        with db_cursor() as cursor:
            current_app.logger.info(f"Fetching contact emails for customer {customer_id}")
            cursor.execute("""
                SELECT LOWER(email) FROM contacts 
                WHERE customer_id = ?
            """, (customer_id,))

            contact_emails = [row[0] for row in cursor.fetchall()]
            current_app.logger.info(f"Found {len(contact_emails)} contact emails in {time.time() - start_time:.2f}s")
            current_app.logger.debug(f"Contact emails: {contact_emails}")

            if not contact_emails:
                return jsonify({
                    'success': True,
                    'emails': [],
                    'pagination': {
                    'total': 0,
                    'pages': 0,
                    'current_page': page,
                    'per_page': per_page
                }
            })

        # Build the query - now focusing on email addresses instead of customer_id
        email_conditions = []
        params = []

        # Check for emails either sent by these contacts or received by these contacts
        sender_placeholders = ','.join(['?' for _ in contact_emails])
        params.extend(contact_emails)  # Add sender emails to params

        # For recipient check, we need LIKE conditions since recipients can be comma-separated
        recipient_conditions = []
        for email in contact_emails:
            recipient_conditions.append("LOWER(recipient_email) LIKE ?")
            params.append(f"%{email}%")

        # Build the complete condition
        email_conditions_sql = f"LOWER(sender_email) IN ({sender_placeholders}) OR ({' OR '.join(recipient_conditions)})"
        current_app.logger.debug(f"Email conditions: {email_conditions_sql}")
        current_app.logger.debug(f"Email parameters: {params}")

        # Main query for fetching emails
        timeline_query = f"""
            SELECT 
                id,
                sent_date,
                subject,
                sender_email,
                recipient_email,
                direction,
                folder
            FROM emails 
            WHERE {email_conditions_sql}
            ORDER BY sent_date DESC
            LIMIT ? OFFSET ?
        """

        timeline_params = params.copy()
        timeline_params.extend([per_page, offset])
        current_app.logger.debug(f"Timeline query params: {timeline_params}")

        query_start = time.time()
        cursor.execute(timeline_query, timeline_params)
        rows = cursor.fetchall()
        current_app.logger.info(f"Timeline query returned {len(rows)} rows in {time.time() - query_start:.2f}s")

        process_start = time.time()
        emails = [{
            'id': row[0],
            'date': row[1],
            'subject': row[2],
            'sender': row[3],
            'recipient': row[4],
            'direction': row[5],
            'folder': row[6]
        } for row in rows]
        current_app.logger.info(f"Processed results in {time.time() - process_start:.2f}s")

        # Count query for pagination
        count_query = f"""
            SELECT COUNT(*) FROM emails 
            WHERE {email_conditions_sql}
        """

        count_params = params.copy()
        current_app.logger.debug(f"Count query params: {count_params}")

        count_start = time.time()
        cursor.execute(count_query, count_params)
        total_count = cursor.fetchone()[0]
        current_app.logger.info(f"Count query completed in {time.time() - count_start:.2f}s")

        current_app.logger.info(f"Total email request completed in {time.time() - start_time:.2f}s")

        return jsonify({
            'success': True,
            'emails': emails,
            'pagination': {
                'total': total_count,
                'pages': (total_count + per_page - 1) // per_page,
                'current_page': page,
                'per_page': per_page
            }
        })

    except Exception as e:
        current_app.logger.error("Error fetching customer emails", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@customers_bp.route('/<int:customer_id>/activity/rfqs', methods=['GET'])
def get_rfqs(customer_id):
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    try:
        with db_cursor() as cursor:
            query = """
                SELECT 
                    id,
                    entered_date,
                    customer_ref,
                    status
                FROM rfqs 
                WHERE customer_id = ?
                ORDER BY entered_date DESC
                LIMIT ? OFFSET ?
            """

            cursor.execute(query, (customer_id, per_page, offset))
            rows = cursor.fetchall()

            rfqs = [{
                'id': row[0],
                'date': row[1],
                'reference': row[2],
                'status': row[3]
            } for row in rows]

            # Count total for pagination
            cursor.execute(
                "SELECT COUNT(*) FROM rfqs WHERE customer_id = ?",
                (customer_id,)
            )
            total_count = cursor.fetchone()[0]

        return jsonify({
            'success': True,
            'rfqs': rfqs,
            'pagination': {
                'total': total_count,
                'pages': (total_count + per_page - 1) // per_page,
                'current_page': page,
                'per_page': per_page
            }
        })

    except Exception as e:
        current_app.logger.error("Error fetching customer RFQs", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@customers_bp.route('/api/customers')
def get_customers_data():
    try:
        page = request.args.get('page', 1, type=int)
        search = request.args.get('search', '').strip()
        per_page = 20
        offset = (page - 1) * per_page

        # Prepare your search param (wildcard for LIKE)
        search_lower = search.lower()
        like_param = f"%{search_lower}%"

        # Main query: limit and offset for pagination, plus server-side search
        query = """
        WITH customer_contacts AS (
            SELECT DISTINCT customer_id, LOWER(email) as email 
            FROM contacts
        ),
        latest_activity AS (
            SELECT 
                customer_id,
                activity_type,
                activity_date,
                description,
                status,
                activity_id
            FROM (
                SELECT 
                    a.*,
                    ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY activity_date DESC) as rn
                FROM (
                    SELECT DISTINCT
                        cc.customer_id,
                        'email' as activity_type,
                        e.sent_date as activity_date,
                        e.subject as description,
                        e.direction as status,
                        e.id as activity_id
                    FROM emails e
                    JOIN customer_contacts cc ON (
                        LOWER(e.sender_email) = cc.email 
                        OR LOWER(e.recipient_email) = cc.email
                    )
        
                    UNION ALL
        
                    SELECT 
                        r.customer_id,
                        'rfq' as activity_type,
                        r.entered_date as activity_date,
                        r.customer_ref as description,
                        r.status,
                        r.id as activity_id
                    FROM rfqs r
        
                    UNION ALL
        
                    SELECT 
                        so.customer_id,
                        'order' as activity_type,
                        so.date_entered as activity_date,
                        so.sales_order_ref as description,
                        ss.status_name as status,
                        so.id as activity_id
                    FROM sales_orders so
                    LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
                ) a
            ) ranked
            WHERE rn = 1
        )
        SELECT DISTINCT
            c.id, 
            c.name, 
            c.primary_contact_id,
            c.salesperson_id,
            cs.status,
            s.name as salesperson_name,
            pc.name as primary_contact_name,
            pc.email as primary_contact_email,
            la.activity_date as latest_activity,
            la.activity_type,
            la.description as activity_description,
            la.status as activity_status,
            la.activity_id
        FROM customers c
        LEFT JOIN customer_status cs ON c.status_id = cs.id
        LEFT JOIN salespeople s ON c.salesperson_id = s.id
        LEFT JOIN contacts pc ON c.primary_contact_id = pc.id
        LEFT JOIN latest_activity la ON c.id = la.customer_id
        LEFT JOIN customer_industry_tags cit ON c.id = cit.customer_id
        LEFT JOIN industry_tags it ON it.id = cit.tag_id
        WHERE
            (
                ? = '' 
                OR LOWER(c.name) LIKE ?
                OR LOWER(it.tag) LIKE ?
            )
        ORDER BY (la.activity_date IS NULL), la.activity_date DESC
        LIMIT ? OFFSET ?
        """

        # Count query to get total results for pagination
        count_query = """
        SELECT COUNT(DISTINCT c.id) as total_customers
        FROM customers c 
        LEFT JOIN customer_industry_tags cit ON c.id = cit.customer_id
        LEFT JOIN industry_tags it ON it.id = cit.tag_id
        WHERE
            (
                ? = '' 
                OR LOWER(c.name) LIKE ?
                OR LOWER(it.tag) LIKE ?
            )
        """

        rows = db_execute(
            query,
            [
                search_lower,
                like_param,
                like_param,
                per_page,
                offset
            ],
            fetch='all',
        )

        count_row = db_execute(
            count_query,
            [search_lower, like_param, like_param],
            fetch='one',
        )

        total = count_row['total_customers'] if count_row else 0

        # Build a response array
        customers = []
        for row in rows:
            customer_dict = dict(row)
            print(f"Debug - Customer row: {customer_dict}")
            if row['primary_contact_name'] and row['primary_contact_email']:
                customer_dict['primary_contact'] = f"{row['primary_contact_name']} ({row['primary_contact_email']})"
            else:
                customer_dict['primary_contact'] = None

            # If you have this helper function, adapt as needed
            tags = get_tags_by_customer_id(customer_dict['id'])
            customer_dict['tags'] = tags

            customers.append(customer_dict)
            print(f"Debug post-conversion: {customer_dict}")

            # Calculate total pages
        pages = (total + per_page - 1) // per_page

        return jsonify({
            'customers': customers,
            'total': total,
            'pages': pages,
            'current_page': page
        })

    except Exception as e:
        print(f"Error in get_customers_data: {str(e)}")
        return jsonify({'error': str(e)}), 500

@customers_bp.route('/<int:customer_id>/activity/insights')
def get_customer_insights(customer_id):
    try:
        now = datetime.utcnow()
        current_month_start = now.replace(day=1)
        start_month = _add_months(current_month_start, -11)
        month_labels = [_add_months(start_month, idx).strftime('%Y-%m') for idx in range(12)]
        monthly_totals = {label: 0.0 for label in month_labels}
        year_labels = [str(now.year - 9 + idx) for idx in range(10)]
        yearly_totals = {year: 0.0 for year in year_labels}

        with db_cursor() as cur:
            _execute_with_cursor(
                cur,
                """
                SELECT date_entered, total_value
                FROM sales_orders
                WHERE customer_id = ? AND date_entered >= ?
                ORDER BY date_entered ASC
                """,
                (customer_id, start_month.strftime('%Y-%m-01'))
            )
            monthly_rows = cur.fetchall()

            _execute_with_cursor(
                cur,
                """
                SELECT
                    pn.base_part_number,
                    SUM(sol.quantity) as total_quantity
                FROM sales_order_lines sol
                JOIN sales_orders so ON sol.sales_order_id = so.id
                JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                WHERE so.customer_id = ?
                GROUP BY pn.base_part_number
                ORDER BY total_quantity DESC
                LIMIT 10
                """,
                (customer_id,)
            )
            top_products = cur.fetchall()

            _execute_with_cursor(
                cur,
                """
                SELECT 
                    m.name as manufacturer,
                    SUM(sol.price * sol.quantity) as total_value
                FROM sales_order_lines sol
                JOIN sales_orders so ON sol.sales_order_id = so.id
                JOIN part_manufacturers pm ON sol.base_part_number = pm.base_part_number
                JOIN manufacturers m ON pm.manufacturer_id = m.id
                WHERE so.customer_id = ?
                GROUP BY m.id
                ORDER BY total_value DESC
                LIMIT 10
                """,
                (customer_id,)
            )
            top_manufacturers = cur.fetchall()

            _execute_with_cursor(
                cur,
                """
                SELECT date_entered, total_value
                FROM sales_orders
                WHERE customer_id = ? AND date_entered >= ?
                ORDER BY date_entered ASC
                """,
                (customer_id, f"{year_labels[0]}-01-01")
            )
            yearly_rows = cur.fetchall()

        for row in monthly_rows:
            dt = _parse_datetime(row.get('date_entered'))
            if not dt:
                continue
            label = dt.strftime('%Y-%m')
            if label in monthly_totals:
                monthly_totals[label] += float(row.get('total_value') or 0)

        for row in yearly_rows:
            dt = _parse_datetime(row.get('date_entered'))
            if not dt:
                continue
            label = str(dt.year)
            if label in yearly_totals:
                yearly_totals[label] += float(row.get('total_value') or 0)

        monthly_sales = [{'month': label, 'total_value': monthly_totals[label]} for label in month_labels]
        yearly_sales = [{'year': year, 'total_value': yearly_totals[year]} for year in year_labels]

        response_data = {
            'success': True,
            'data': {
                'topProducts': {
                    'type': 'bar',
                    'data': {
                        'labels': [row['base_part_number'] for row in top_products],
                        'datasets': [{
                            'label': 'Quantity',
                            'data': [float(row.get('total_quantity') or 0) for row in top_products]
                        }]
                    }
                },
                'topManufacturers': {
                    'type': 'pie',
                    'data': {
                        'labels': [row['manufacturer'] for row in top_manufacturers],
                        'datasets': [{
                            'label': 'Value',
                            'data': [float(row.get('total_value') or 0) for row in top_manufacturers]
                        }]
                    }
                },
                'monthlySales': {
                    'type': 'line',
                    'data': {
                        'labels': [entry['month'] for entry in monthly_sales],
                        'datasets': [{
                            'label': 'Sales Value',
                            'data': [entry['total_value'] for entry in monthly_sales]
                        }]
                    }
                },
                'yearlySales': {
                    'type': 'bar',
                    'data': {
                        'labels': [entry['year'] for entry in yearly_sales],
                        'datasets': [{
                            'label': 'Annual Sales',
                            'data': [entry['total_value'] for entry in yearly_sales]
                        }]
                    }
                }
            }
        }
        return jsonify(response_data)
    except Exception as e:
        logger.exception(e)
        return jsonify({'error': str(e)}), 500

@customers_bp.route('/api/customers/filter')
def filter_customers():
    try:
        page = request.args.get('page', 1, type=int)
        search = request.args.get('search', '').strip()
        filter_id = request.args.get('filter_id', '').strip()
        filter_name = request.args.get('filter_name', '').strip()
        filter_contact = request.args.get('filter_contact', '').strip()
        filter_salesperson = request.args.get('filter_salesperson', '').strip()
        filter_status = request.args.get('filter_status', '').strip()
        filter_tags = request.args.get('filter_tags', '').strip()
        filter_comments = request.args.get('filter_comments', '').strip()
        countries = [country for country in request.args.getlist('countries') if country]
        sort_by = request.args.get('sort_by', 'latest_activity')
        order = request.args.get('order', 'DESC').upper()
        filter_watch = request.args.get('filter_watch', '').strip()


        per_page = 20
        offset = (page - 1) * per_page

        where_conditions = []
        params = {
            'search_str': search,
            'search_param': f"%{search}%",
            'limit': per_page,
            'offset': offset,
            'sort_by': sort_by,
            'order': order
        }

        if filter_id:
            where_conditions.append("CAST(c.id AS TEXT) LIKE :filter_id")
            params['filter_id'] = f"%{filter_id}%"

        if filter_watch:
            where_conditions.append("c.watch = :filter_watch")
            params['filter_watch'] = filter_watch.lower() == 'true'

        if search:
            where_conditions.append("""
                (:search_str = '' OR
                 LOWER(c.name) LIKE :search_param OR
                 LOWER(ct.tags) LIKE :search_param)
            """)

        if filter_name:
            where_conditions.append("LOWER(c.name) LIKE :filter_name")
            params['filter_name'] = f"%{filter_name.lower()}%"

        if filter_contact:
            where_conditions.append("LOWER(pc.name) LIKE :filter_contact")
            params['filter_contact'] = f"%{filter_contact.lower()}%"

        if filter_salesperson:
            salesperson_ids = filter_salesperson.split(',')
            if salesperson_ids:
                placeholders = ','.join(f':sp_id_{i}' for i in range(len(salesperson_ids)))
                where_conditions.append(f"s.id IN ({placeholders})")
                for i, sp_id in enumerate(salesperson_ids):
                    params[f'sp_id_{i}'] = int(sp_id)

        if filter_status:
            where_conditions.append("LOWER(cs.status) LIKE :filter_status")
            params['filter_status'] = f"%{filter_status.lower()}%"

        if filter_tags:
            where_conditions.append("LOWER(ct.tags) LIKE :filter_tags")
            params['filter_tags'] = f"%{filter_tags.lower()}%"

        if filter_comments:
            where_conditions.append("LOWER(cu.update_text) LIKE :filter_comments")
            params['filter_comments'] = f"%{filter_comments.lower()}%"

        if countries and any(countries):
            country_placeholders = []
            for idx, country in enumerate(countries):
                key = f'country_{idx}'
                country_placeholders.append(f":{key}")
                params[key] = country
            where_conditions.append(f"c.country IN ({', '.join(country_placeholders)})")

        where_clause = " AND ".join(where_conditions)
        where_fragment = f" WHERE {where_clause}" if where_clause else ""

        tags_agg_fn = "STRING_AGG(it.tag, ', ')" if _using_postgres() else "GROUP_CONCAT(it.tag)"

        if _using_postgres():
            email_date_expr = "e.sent_date::timestamp"
            rfq_date_expr = "r.entered_date::timestamp"
            order_date_expr = "so.date_entered::timestamp"
        else:
            email_date_expr = "e.sent_date"
            rfq_date_expr = "r.entered_date"
            order_date_expr = "so.date_entered"

        query = f"""
        WITH RECURSIVE latest_activity AS (
            SELECT customer_id, activity_type, activity_date, description, status, activity_id
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY activity_date DESC) as rn
                FROM (
                    SELECT
                        c.customer_id,
                        'email' as activity_type,
                        {email_date_expr} as activity_date,
                        e.subject as description,
                        CASE
                            WHEN LOWER(e.sender_email) IN (SELECT LOWER(email) FROM users) THEN 'outbound'
                            ELSE 'received'
                        END as status,
                        e.id as activity_id
                    FROM emails e
                    JOIN contacts c ON LOWER(e.sender_email) = LOWER(c.email) OR LOWER(e.recipient_email) = LOWER(c.email)

                    UNION ALL

                    SELECT
                        r.customer_id,
                        'rfq' as activity_type,
                        {rfq_date_expr} as activity_date,
                        r.customer_ref as description,
                        r.status,
                        r.id as activity_id
                    FROM rfqs r

                    UNION ALL

                    SELECT
                        so.customer_id,
                        'order' as activity_type,
                        {order_date_expr} as activity_date,
                        so.sales_order_ref as description,
                        ss.status_name as status,
                        so.id as activity_id
                    FROM sales_orders so
                    LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
                )
            ) ranked
            WHERE rn = 1
        ),
        customer_tags AS (
            SELECT cit.customer_id,
                   {tags_agg_fn} as tags
            FROM customer_industry_tags cit
            JOIN industry_tags it ON it.id = cit.tag_id
            GROUP BY cit.customer_id
        )
        SELECT DISTINCT
            c.id,
            c.watch,
            c.name,
            c.primary_contact_id,
            c.salesperson_id,
            c.budget,
            cs.status,
            s.name AS salesperson_name,
            pc.name AS primary_contact_name,
            pc.email AS primary_contact_email,
            la.activity_date AS latest_activity,
            la.activity_type,
            la.description AS activity_description,
            la.status AS activity_status,
            la.activity_id AS activity_id,
            cu.date as update_date,
            cu.update_text,
            cu.communication_type,
            s2.name as update_salesperson_name,
            ct.tags,
            cu.update_contact_id,
            cu.update_contact_name
        FROM customers c
        LEFT JOIN customer_status cs ON c.status_id = cs.id
        LEFT JOIN salespeople s ON c.salesperson_id = s.id
        LEFT JOIN contacts pc ON c.primary_contact_id = pc.id
        LEFT JOIN latest_activity la ON c.id = la.customer_id
        LEFT JOIN customer_tags ct ON c.id = ct.customer_id
        LEFT JOIN (
            SELECT
                cu.id,
                cu.customer_id,
                cu.update_text,
                cu.date,
                cu.salesperson_id,
                cu.communication_type,
                s2.name as update_salesperson_name,
                cc.contact_id as update_contact_id,
                c2.name as update_contact_name,
                ROW_NUMBER() OVER (PARTITION BY cu.customer_id ORDER BY cu.date DESC) as rn
            FROM customer_updates cu
            LEFT JOIN salespeople s2 ON cu.salesperson_id = s2.id
            LEFT JOIN contact_communications cc ON
                cc.customer_id = cu.customer_id AND
                cc.salesperson_id = cu.salesperson_id AND
                COALESCE(cc.communication_type,'') = COALESCE(cu.communication_type,'') AND
                cc.notes = cu.update_text AND
                DATE(cc.date) = DATE(cu.date)
            LEFT JOIN contacts c2 ON cc.contact_id = c2.id
        ) cu ON cu.customer_id = c.id AND cu.rn = 1
        LEFT JOIN salespeople s2 ON cu.salesperson_id = s2.id
        {where_fragment}
        ORDER BY
            CASE WHEN :sort_by = 'id' THEN c.id END
                """ + (" DESC" if order == 'DESC' else " ASC") + """,
            CASE WHEN :sort_by = 'budget' THEN CAST(COALESCE(c.budget, 0) AS DECIMAL) END
                """ + (" DESC" if order == 'DESC' else " ASC") + """,
            CASE WHEN :sort_by = 'latest_activity' THEN la.activity_date END
                """ + (" DESC" if order == 'DESC' else " ASC") + """,
            CASE WHEN :sort_by = 'name' THEN c.name END
                """ + (" DESC" if order == 'DESC' else " ASC") + """,
            CASE WHEN :sort_by = 'primary_contact' THEN pc.name END
                """ + (" DESC" if order == 'DESC' else " ASC") + """,
            CASE WHEN :sort_by = 'salesperson' THEN s.name END
                """ + (" DESC" if order == 'DESC' else " ASC") + """,
            CASE WHEN :sort_by = 'status' THEN cs.status END
                """ + (" DESC" if order == 'DESC' else " ASC") + """
        LIMIT :limit OFFSET :offset
        """

        count_query = f"""
        WITH customer_tags AS (
            SELECT cit.customer_id,
                   {tags_agg_fn} as tags
            FROM customer_industry_tags cit
            JOIN industry_tags it ON it.id = cit.tag_id
            GROUP BY cit.customer_id
        )
        SELECT COUNT(DISTINCT c.id)
        FROM customers c
        LEFT JOIN customer_status cs ON c.status_id = cs.id
        LEFT JOIN salespeople s ON c.salesperson_id = s.id
        LEFT JOIN contacts pc ON c.primary_contact_id = pc.id
        LEFT JOIN customer_tags ct ON c.id = ct.customer_id
        LEFT JOIN (
            SELECT
                cu.id,
                cu.customer_id,
                cu.update_text,
                cu.date,
                cu.salesperson_id,
                cu.communication_type,
                s2.name as update_salesperson_name,
                cc.contact_id as update_contact_id,
                c2.name as update_contact_name,
                ROW_NUMBER() OVER (PARTITION BY cu.customer_id ORDER BY cu.date DESC) as rn
            FROM customer_updates cu
            LEFT JOIN salespeople s2 ON cu.salesperson_id = s2.id
            LEFT JOIN contact_communications cc ON
                cc.customer_id = cu.customer_id AND
                cc.salesperson_id = cu.salesperson_id AND
                COALESCE(cc.communication_type,'') = COALESCE(cu.communication_type,'') AND
                cc.notes = cu.update_text AND
                DATE(cc.date) = DATE(cu.date)
            LEFT JOIN contacts c2 ON cc.contact_id = c2.id
        ) cu ON cu.customer_id = c.id AND cu.rn = 1
        {where_fragment}
        """

        ytd_sales = {}
        start_of_year = datetime.utcnow().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        start_of_year_str = start_of_year.strftime('%Y-%m-%d')

        with db_cursor() as cur:
            _execute_with_cursor(cur, query, params)
            rows = cur.fetchall()
            _execute_with_cursor(cur, count_query, params)
            count_row = cur.fetchone()
            total = count_row[0] if count_row else 0

            customer_ids = [row['id'] for row in rows]
            if customer_ids:
                placeholders = ','.join('?' for _ in customer_ids)
                ytd_query = f"""
                    SELECT
                        customer_id,
                        COALESCE(SUM(total_value), 0) as ytd_sales
                    FROM sales_orders
                    WHERE customer_id IN ({placeholders})
                    AND date_entered >= ?
                    GROUP BY customer_id
                """
                _execute_with_cursor(cur, ytd_query, customer_ids + [start_of_year_str])
                ytd_results = cur.fetchall()
                ytd_sales = {row['customer_id']: row['ytd_sales'] for row in ytd_results}

        customers = []
        for row in rows:
            customer_dict = dict(row)
            customer_dict['watch'] = bool(row['watch'])
            customer_dict['ytd_sales'] = ytd_sales.get(customer_dict['id'], 0)

            if row['primary_contact_name'] and row['primary_contact_email']:
                customer_dict['primary_contact'] = f"{row['primary_contact_name']} ({row['primary_contact_email']})"
            else:
                customer_dict['primary_contact'] = None

            if row['budget'] is not None:
                customer_dict['budget'] = {
                    'amount': row['budget'],
                    'currency': 'EUR'
                }
            else:
                customer_dict['budget'] = None

            tags_value = row['tags']
            if tags_value:
                customer_dict['tags'] = [tag.strip() for tag in tags_value.split(',') if tag.strip()]
            else:
                customer_dict['tags'] = []

            if row['update_date']:
                customer_dict['latest_update'] = {
                    'date': row['update_date'].isoformat() if isinstance(row['update_date'], datetime) else row[
                        'update_date'],
                    'update_text': row['update_text'],
                    'salesperson': row['update_salesperson_name'],
                    'contact_id': row['update_contact_id'],
                    'contact_name': row['update_contact_name'],
                    'communication_type': row['communication_type']
                }
            else:
                customer_dict['latest_update'] = None

            customers.append(customer_dict)

        return jsonify({
            'customers': customers,
            'total': total,
            'pages': (total + per_page - 1) // per_page,
            'current_page': page
        })

    except Exception as e:
        print(f"Error in filter_customers: {str(e)}")
        return jsonify({'error': str(e)}), 500


@customers_bp.route('/activity/<activity_type>/<int:activity_id>', methods=['GET'])
def get_activity_detail(activity_type, activity_id):
    try:
        with db_cursor() as cur:
            activity_data = {}

            if activity_type == 'email':
                query = """
                    SELECT
                        id,
                        sent_date,
                        subject,
                        sender_email,
                        recipient_email,
                        direction,
                        folder,
                        message_id
                    FROM emails
                    WHERE id = ?
                """
                row = _execute_with_cursor(cur, query, (activity_id,)).fetchone()
                if not row:
                    return jsonify({'success': False, 'error': 'Activity not found'}), 404

                activity_data = dict(row)

                message_id = activity_data.get('message_id', '')
                if not message_id:
                    return jsonify({'success': False, 'error': 'Email message_id not found'}), 404

                email_host = os.getenv('EMAIL_HOST')
                email_port = int(os.getenv('EMAIL_PORT', 993))
                email_user = os.getenv('EMAIL_USER')
                email_password = os.getenv('EMAIL_PASSWORD')

                mail = imaplib.IMAP4_SSL(email_host, email_port)
                mail.login(email_user, email_password)
                folder = activity_data.get('folder') or 'INBOX'
                mail.select(folder, readonly=True)

                search_message_id = f'<{message_id}>' if not (
                    message_id.startswith('<') and message_id.endswith('>')
                ) else message_id

                result, message_numbers = mail.search(None, f'HEADER Message-ID "{search_message_id}"')
                full_body = None
                if result == 'OK' and message_numbers and message_numbers[0]:
                    msg_num = message_numbers[0].split()[0]
                    result, msg_data = mail.fetch(msg_num, '(RFC822)')
                    if result == 'OK' and msg_data and len(msg_data) > 0:
                        raw_email = msg_data[0][1]
                        msg_obj = email.message_from_bytes(raw_email)
                        dec_subject, enc = decode_header(msg_obj['Subject'])[0]
                        if isinstance(dec_subject, bytes):
                            dec_subject = dec_subject.decode(enc or 'utf-8')
                        activity_data['subject'] = dec_subject
                        if msg_obj.is_multipart():
                            for part in msg_obj.walk():
                                content_type = part.get_content_type()
                                if content_type in ['text/plain', 'text/html']:
                                    full_body = part.get_payload(decode=True).decode(errors='replace')
                                    break
                        else:
                            full_body = msg_obj.get_payload(decode=True).decode(errors='replace')

                mail.logout()
                activity_data['full_body'] = full_body or ""

            elif activity_type == 'rfq':
                query = """
                    SELECT id, entered_date, customer_ref, status
                    FROM rfqs
                    WHERE id = ?
                """
                header_row = _execute_with_cursor(cur, query, (activity_id,)).fetchone()
                if not header_row:
                    return jsonify({'success': False, 'error': 'Activity not found'}), 404

                activity_data = dict(header_row)

                lines_query = """
                    SELECT
                        id,
                        line_number,
                        base_part_number,
                        quantity,
                        line_value
                    FROM rfq_lines
                    WHERE rfq_id = ?
                """
                line_rows = _execute_with_cursor(cur, lines_query, (activity_id,)).fetchall()
                activity_data['lines'] = [dict(line) for line in line_rows]

            elif activity_type == 'order':
                query = """
                    SELECT so.id, so.date_entered, so.sales_order_ref, so.customer_po_ref,
                           ss.status_name, so.total_value, so.currency_id
                    FROM sales_orders so
                    LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
                    WHERE so.id = ?
                """
                header_row = _execute_with_cursor(cur, query, (activity_id,)).fetchone()
                if not header_row:
                    return jsonify({'success': False, 'error': 'Activity not found'}), 404

                activity_data = dict(header_row)

                lines_query = """
                    SELECT
                        id,
                        line_number,
                        base_part_number,
                        quantity,
                        price
                    FROM sales_order_lines
                    WHERE sales_order_id = ?
                """
                line_rows = _execute_with_cursor(cur, lines_query, (activity_id,)).fetchall()
                activity_data['lines'] = [dict(line) for line in line_rows]

            else:
                return jsonify({'success': False, 'error': 'Unsupported activity type'}), 400

        return jsonify({
            'success': True,
            'activity': activity_data
        })

    except Exception as e:
        logger.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/enrich/dashboard', methods=['GET'])
def enrich_dashboard():
    """Show the enrichment dashboard"""
    with db_cursor() as cur:
        stats = _execute_with_cursor(
            cur,
            '''
            SELECT 
                (SELECT COUNT(*) FROM customers) as total_customers,
                COUNT(*) as processed,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) as in_progress
            FROM customer_enrichment_status
            '''
        ).fetchone()

        recent = _execute_with_cursor(
            cur,
            '''
            SELECT 
                ces.customer_id,
                c.name as customer_name,
                ces.status,
                ces.last_attempt,
                ces.error_message
            FROM customer_enrichment_status ces
            JOIN customers c ON ces.customer_id = c.id
            ORDER BY ces.last_attempt DESC
            LIMIT 10
            '''
        ).fetchall()

    return render_template(
        'customers/enrich/dashboard.html',
        stats=stats,
        recent=recent
    )


@customers_bp.route('/enrich/start', methods=['POST'])
def start_enrichment():
    """Start the enrichment process using Perplexity AI for live data"""
    try:
        # Start in a separate thread to not block
        # Using Perplexity-based enrichment for accurate, live data
        thread = threading.Thread(target=start_perplexity_enrichment, args=(20,))
        thread.daemon = True
        thread.start()

        return jsonify({
            'status': 'success',
            'message': 'Enrichment process started (using Perplexity AI)'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@customers_bp.route('/enrich/status', methods=['GET'])
def get_enrichment_status():
    """Get current enrichment status"""
    with db_cursor() as cur:
        reviewed_false = _reviewed_flag(False)
        status = _execute_with_cursor(
            cur,
            '''
            SELECT 
                (SELECT COUNT(*) FROM customers) as total_customers,
                COUNT(*) as processed,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) as in_progress,
                MAX(last_attempt) as last_update
            FROM customer_enrichment_status
            '''
        ).fetchone()

        suggestion_count = _execute_with_cursor(
            cur,
            '''
            SELECT COUNT(DISTINCT suggested_tag) as count
            FROM ai_tag_suggestions
            WHERE reviewed = ?
            ''',
            (reviewed_false,)
        ).fetchone().get('count', 0)

        recent_activity = _execute_with_cursor(
            cur,
            '''
            SELECT 
                ces.customer_id,
                c.name as customer_name,
                ces.status,
                ces.last_attempt,
                ces.error_message
            FROM customer_enrichment_status ces
            JOIN customers c ON ces.customer_id = c.id
            ORDER BY ces.last_attempt DESC
            LIMIT 10
            '''
        ).fetchall()

        industry_tags_agg = "STRING_AGG(DISTINCT it.tag, ', ')" if _using_postgres() else "GROUP_CONCAT(DISTINCT it.tag)"
        company_types_agg = "STRING_AGG(DISTINCT ct.type, ', ')" if _using_postgres() else "GROUP_CONCAT(DISTINCT ct.type)"
        recent_updates_query = f'''
            SELECT 
                c.name,
                c.estimated_revenue,
                c.country,
                c.updated_at,
                {industry_tags_agg} as industry_tags,
                {company_types_agg} as company_types
            FROM customers c
            LEFT JOIN customer_industry_tags cit ON c.id = cit.customer_id
            LEFT JOIN industry_tags it ON cit.tag_id = it.id
            LEFT JOIN customer_company_types cct ON c.id = cct.customer_id
            LEFT JOIN company_types ct ON cct.company_type_id = ct.id
            WHERE c.updated_at IS NOT NULL
            GROUP BY c.id, c.name, c.estimated_revenue, c.country, c.updated_at
            ORDER BY c.updated_at DESC
            LIMIT 10
            '''
        recent_updates = _execute_with_cursor(cur, recent_updates_query).fetchall()

    return jsonify({
        'status': 'success',
        'data': {
            'stats': dict(status),
            'suggestion_count': suggestion_count,
            'recent_activity': [dict(row) for row in recent_activity],
            'recent_updates': [dict(row) for row in recent_updates]
        }
    })


@customers_bp.route('/enrich/suggestions', methods=['GET'])
def view_suggestions():
    """View tag suggestions"""
    with db_cursor() as cur:
        reviewed_false = _reviewed_flag(False)
        companies_agg = "STRING_AGG(DISTINCT c.name, ', ')" if _using_postgres() else "GROUP_CONCAT(DISTINCT c.name)"
        suggestions_query = f'''
            WITH suggestion_counts AS (
                SELECT 
                    suggested_tag,
                    COUNT(*) as frequency,
                    {companies_agg} as companies,
                    MIN(ats.created_at) as first_suggested
                FROM ai_tag_suggestions ats
                JOIN customers c ON ats.customer_id = c.id
                WHERE reviewed = ?
                GROUP BY suggested_tag
            )
            SELECT *
            FROM suggestion_counts sc
            WHERE NOT EXISTS (
                SELECT 1 FROM industry_tags it 
                WHERE LOWER(it.tag) = LOWER(sc.suggested_tag)
                OR LOWER(it.tag) LIKE '%' || LOWER(sc.suggested_tag) || '%'
                OR LOWER(sc.suggested_tag) LIKE '%' || LOWER(it.tag) || '%'
            )
            ORDER BY frequency DESC
            '''
        suggestions = _execute_with_cursor(cur, suggestions_query, (reviewed_false,)).fetchall()

    return render_template('customers/enrich/suggestions.html', suggestions=suggestions)

@customers_bp.route('/enrich/stop', methods=['POST'])
def stop_enrichment():
    """Stop the enrichment process"""
    with db_cursor(commit=True) as cur:
        _execute_with_cursor(
            cur,
            '''
            UPDATE customer_enrichment_status 
            SET status = 'stopped'
            WHERE status = 'processing'
            '''
        )

    return jsonify({
        'status': 'success',
        'message': 'Enrichment process stopped'
    })


@customers_bp.route('/enrich/suggestions/approve', methods=['POST'])
def approve_tag_suggestion():
    """Approve a suggested tag and create it as a new industry tag"""
    try:
        data = request.get_json()
        if not data or 'tag' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Missing tag data'
            }), 400

        with db_cursor(commit=True) as cur:
            reviewed_false = _reviewed_flag(False)
            reviewed_true = _reviewed_flag(True)
            _execute_with_cursor(
                cur,
                '''
                INSERT INTO industry_tags (tag, description)
                VALUES (?, ?)
                ''',
                (data['tag'], f"AI suggested tag: {data['tag']}")
            )

            tag_row = _execute_with_cursor(
                cur,
                'SELECT id FROM industry_tags WHERE tag = ?',
                (data['tag'],)
            ).fetchone()
            new_tag_id = tag_row['id']

            customers = _execute_with_cursor(
                cur,
                '''
                SELECT DISTINCT customer_id 
                FROM ai_tag_suggestions 
                WHERE suggested_tag = ? AND reviewed = ?
                ''',
                (data['tag'], reviewed_false)
            ).fetchall()

            for customer in customers:
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO customer_industry_tags (customer_id, tag_id)
                    VALUES (?, ?)
                    ''',
                    (customer['customer_id'], new_tag_id)
                )

            _execute_with_cursor(
                cur,
                '''
                UPDATE ai_tag_suggestions 
                SET reviewed = ?
                WHERE suggested_tag = ? AND reviewed = ?
                ''',
                (reviewed_true, data['tag'], reviewed_false)
            )

        return jsonify({
            'status': 'success',
            'message': f'Tag "{data["tag"]}" approved and created',
            'tag_id': new_tag_id
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@customers_bp.route('/enrich/suggestions/reject', methods=['POST'])
def reject_tag_suggestion():
    """Reject a suggested tag"""
    try:
        data = request.get_json()
        if not data or 'tag' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Missing tag data'
            }), 400

        with db_cursor(commit=True) as cur:
            reviewed_false = _reviewed_flag(False)
            reviewed_true = _reviewed_flag(True)
            _execute_with_cursor(
                cur,
                '''
                UPDATE ai_tag_suggestions 
                SET reviewed = ?
                WHERE suggested_tag = ? AND reviewed = ?
                ''',
                (reviewed_true, data['tag'], reviewed_false)
            )

        return jsonify({
            'status': 'success',
            'message': f'Tag "{data["tag"]}" rejected'
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@customers_bp.route('/list_with_activity', methods=['GET'])
def list_customers_with_activity():
    try:
        tag_id = request.args.get('tag')
        continent = request.args.get('continent')
        countries = request.args.getlist('countries')

        params = {}
        where_conditions = []

        if tag_id:
            where_conditions.append("""
                c.id IN (
                    SELECT customer_id
                    FROM customer_industry_tags
                    WHERE tag_id = :tag_id
                )
            """)
            params['tag_id'] = tag_id

        if continent:
            where_conditions.append("""
                c.id IN (
                    SELECT id
                    FROM customers
                    WHERE country IN (
                        SELECT code
                        FROM countries
                        WHERE continent = :continent
                    )
                )
            """)
            params['continent'] = continent

        if countries and any(countries):
            where_fragments = []
            for idx, country in enumerate(countries):
                key = f'activity_country_{idx}'
                where_fragments.append(f":{key}")
                params[key] = country
            where_conditions.append(f"c.country IN ({', '.join(where_fragments)})")

        where_clause = " AND ".join(where_conditions)
        where_fragment = f" WHERE {where_clause}" if where_clause else ""

        base_query = f"""
        WITH latest_activity AS (
            SELECT 
                customer_id,
                activity_type,
                activity_date,
                description,
                status,
                activity_id
            FROM (
                SELECT 
                    a.*,
                    ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY activity_date DESC) as rn
                FROM (
                    -- Email activities
                    SELECT DISTINCT
                        c.customer_id,
                        'email' as activity_type,
                        e.sent_date as activity_date,
                        e.subject as description,
                        CASE
                            WHEN LOWER(e.sender_email) IN (SELECT LOWER(email) FROM users) THEN 'outbound'
                            ELSE 'received'
                        END as status,
                        e.id as activity_id
                    FROM emails e
                    JOIN contacts c ON (
                        (LOWER(e.sender_email) = LOWER(c.email))
                        OR
                        (LOWER(e.recipient_email) = LOWER(c.email))
                    )

                    UNION ALL

                    -- RFQ activities
                    SELECT
                        r.customer_id,
                        'rfq' as activity_type,
                        r.entered_date as activity_date,
                        r.customer_ref as description,
                        r.status,
                        r.id as activity_id
                    FROM rfqs r

                    UNION ALL

                    -- Sales order activities
                    SELECT
                        so.customer_id,
                        'order' as activity_type,
                        so.date_entered as activity_date,
                        so.sales_order_ref as description,
                        ss.status_name as status,
                        so.id as activity_id
                    FROM sales_orders so
                    LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
                ) a
            ) ranked
            WHERE rn = 1
        ),
        latest_update AS (
            SELECT
                cu.customer_id,
                cu.date,
                cu.update_text,
                s.name as salesperson_name,
                ROW_NUMBER() OVER (PARTITION BY cu.customer_id ORDER BY cu.date DESC) as rn
            FROM customer_updates cu
            LEFT JOIN salespeople s ON cu.salesperson_id = s.id
        )
        SELECT DISTINCT
            c.id,
            c.name,
            c.status_id,
            c.country,
            c.estimated_revenue,
            s.name as assigned_salesperson_name,
            la.activity_date as latest_activity,
            la.activity_type,
            la.status as activity_status,
            la.activity_id,
            lu.date as update_date,
            lu.update_text,
            lu.salesperson_name as update_salesperson
        FROM customers c
        LEFT JOIN latest_activity la ON c.id = la.customer_id
        LEFT JOIN latest_update lu ON c.id = lu.customer_id AND lu.rn = 1
        LEFT JOIN salespeople s ON c.salesperson_id = s.id
        {where_fragment}
        ORDER BY (la.activity_date IS NULL), la.activity_date DESC
        """

        with db_cursor() as cur:
            _execute_with_cursor(cur, base_query, params)
            rows = cur.fetchall()

        customer_data = []
        for row in rows:
            row_dict = dict(row)
            latest_activity = row_dict.get('latest_activity')
            update_date = row_dict.get('update_date')

            customer_data.append({
                'id': row_dict.get('id'),
                'name': row_dict.get('name'),
                'status_id': row_dict.get('status_id'),
                'country': row_dict.get('country'),
                'estimated_revenue': row_dict.get('estimated_revenue'),
                'assigned_salesperson_name': row_dict.get('assigned_salesperson_name'),
                'latest_activity': latest_activity.isoformat() if isinstance(latest_activity, datetime) else latest_activity,
                'activity_type': row_dict.get('activity_type'),
                'activity_status': row_dict.get('activity_status'),
                'activity_id': row_dict.get('activity_id'),
                'update_date': update_date.isoformat() if isinstance(update_date, datetime) else update_date,
                'update_text': row_dict.get('update_text'),
                'update_salesperson_name': row_dict.get('update_salesperson')
            })

        return jsonify({
            'success': True,
            'customers': customer_data
        })

    except Exception as e:
        logging.error(f"Error in list_customers_with_activity: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
@customers_bp.route('/api/salespeople')
def salespeople_endpoint():
    salespeople = get_salespeople()
    return jsonify([dict(sp) for sp in salespeople])

@customers_bp.route('/<int:customer_id>/activity/projects', methods=['GET'])
def get_projects(customer_id):
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    query = """
        SELECT 
            p.id,
            p.name,
            p.description,
            p.estimated_value,
            ps.status,
            p.next_stage_deadline,
            p.next_stage_id
        FROM projects p
        LEFT JOIN project_statuses ps ON p.status_id = ps.id
        WHERE p.customer_id = ?
        ORDER BY p.next_stage_deadline DESC
        LIMIT ? OFFSET ?
    """

    count_query = "SELECT COUNT(*) as total FROM projects WHERE customer_id = ?"

    with db_cursor() as cur:
        _execute_with_cursor(cur, query, (customer_id, per_page, offset))
        rows = cur.fetchall()

        _execute_with_cursor(cur, count_query, (customer_id,))
        total_count = cur.fetchone()['total']

    projects = [{
        'id': row['id'],
        'name': row['name'],
        'description': row['description'],
        'estimated_value': float(row['estimated_value']) if row['estimated_value'] is not None else None,
        'status': row['status'],
        'next_stage_deadline': row['next_stage_deadline'],
        'next_stage_id': row['next_stage_id']
    } for row in rows]

    return jsonify({
        'success': True,
        'projects': projects,
        'pagination': {
            'total': total_count,
            'pages': (total_count + per_page - 1) // per_page,
            'current_page': page,
            'per_page': per_page
        }
    })


@customers_bp.route('/<int:customer_id>/toggle_watch', methods=['POST'])
def toggle_watch(customer_id):
    try:
        data = request.get_json(silent=True) or {}
        watch_status = bool(data.get('watch', False))
        watch_value = watch_status if _using_postgres() else (1 if watch_status else 0)

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                "UPDATE customers SET watch = ? WHERE id = ?",
                (watch_value, customer_id)
            )

        return jsonify({'success': True, 'watch': watch_status})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def country_to_flag(country_code):
    if not country_code:
        return ''
    code = country_code.upper()
    return ''.join(chr(ord(c) + 127397) for c in code)

@customers_bp.app_template_filter('country_to_flag')
def country_to_flag_filter(country_code):
    if not country_code:
        return ''
    # Convert country code to flag emoji
    # Each country code letter is converted to a regional indicator symbol letter
    code = country_code.upper()
    return ''.join(chr(ord(c.upper()) + 127397) for c in code)


@customers_bp.app_template_filter('country_code_to_name')
def country_code_to_name(country_code):
    try:
        return pycountry.countries.get(alpha_2=country_code.upper()).name
    except (AttributeError, KeyError):
        return country_code

@customers_bp.route('/api/country_name_to_code/<name>')
def country_name_to_code(name):
    for country in pycountry.countries:
        if country.name == name:
            return jsonify({'code': country.alpha_2})
    return jsonify({'code': None})

@customers_bp.route('/market-coverage', methods=['GET'])
def market_coverage():
    countries_agg = "STRING_AGG(DISTINCT c.country, ', ')" if _using_postgres() else "GROUP_CONCAT(DISTINCT c.country)"
    sales_cutoff = "CURRENT_DATE - INTERVAL '1 year'" if _using_postgres() else "date('now', '-1 year')"
    with db_cursor() as cur:
        tag_data = _execute_with_cursor(
            cur,
            f"""
            SELECT 
                it.id, 
                it.tag,
                COUNT(DISTINCT c.id) as customer_count,
                {countries_agg} as countries,
                MAX(SUM(CASE 
                    WHEN so.date_entered >= {sales_cutoff} 
                    THEN so.total_value 
                    ELSE 0 
                END)) OVER () as max_sales_last_year,
                COALESCE(SUM(CASE 
                    WHEN so.date_entered >= {sales_cutoff} 
                    THEN so.total_value 
                    ELSE 0 
                END), 0) as sales_last_year
            FROM industry_tags it
            LEFT JOIN customer_industry_tags cit ON it.id = cit.tag_id 
            LEFT JOIN customers c ON cit.customer_id = c.id
            LEFT JOIN sales_orders so ON c.id = so.customer_id 
            GROUP BY it.id, it.tag
            ORDER BY customer_count DESC
            """
        ).fetchall()

    return render_template('customers/market_coverage.html', tag_data=tag_data)


@customers_bp.route('/api/tags/<int:tag_id>/countries', methods=['GET'])
def get_tag_countries(tag_id):
    with db_cursor() as cur:
        country_data = _execute_with_cursor(
            cur,
            """
            SELECT 
                c.country,
                COUNT(DISTINCT c.id) as customer_count,
                SUM(c.estimated_revenue) as revenue
            FROM industry_tags it
            JOIN customer_industry_tags cit ON it.id = cit.tag_id
            JOIN customers c ON cit.customer_id = c.id
            WHERE it.id = ? AND c.country IS NOT NULL
            GROUP BY c.country
            ORDER BY customer_count DESC
            """,
            (tag_id,)
        ).fetchall()

    return jsonify({
        'labels': [row['country'] for row in country_data],
        'values': [row['customer_count'] for row in country_data]
    })


from flask import jsonify
from openai import OpenAI


@customers_bp.route('/api/tags/<int:tag_id>/analysis', methods=['GET'])
def get_tag_analysis(tag_id):
    with db_cursor() as cur:
        tag_data = _execute_with_cursor(
            cur,
            """
            SELECT it.tag, it.description,
                   c.country,
                   COUNT(DISTINCT c.id) as customer_count,
                   (SELECT COUNT(DISTINCT customer_id) 
                    FROM customer_industry_tags 
                    WHERE tag_id = it.id) as total_customers
            FROM industry_tags it
            JOIN customer_industry_tags cit ON it.id = cit.tag_id
            JOIN customers c ON cit.customer_id = c.id
            WHERE it.id = ? AND c.country IS NOT NULL
            GROUP BY it.tag, it.description, c.country
            """,
            (tag_id,)
        ).fetchall()

    if not tag_data:
        return jsonify({"error": "Tag not found"}), 404

    # Format data for AI (customer count only)
    total_customers = tag_data[0]['total_customers']
    country_breakdown = {
        row['country']: {
            'percentage': (row['customer_count'] / total_customers * 100),
            'customer_count': row['customer_count']
        } for row in tag_data
    }

    # Strict system message to enforce industry-specific responses
    # In get_tag_analysis route
    system_message = (
        f"You are a market analyst evaluating **geographical distribution** for {tag_data[0]['tag']}."
        " Your task is to determine if the provided customer distribution aligns with known industry hubs for this equipment type."
        " You must answer **two** questions:\n"
        "1. **Is the current geographical distribution representative of the real-world market for this industry?**"
        " Answer 'Yes' or 'No' and justify it only in relation to the dataset and known industry hubs for this equipment.\n"
        "2. **Where are the main active areas for this market outside of the existing customer base?**"
        " For each key manufacturing hub in Europe not well represented in the dataset, provide:\n"
        " - Country name (use official ISO 3166-1 English short name)\n"
        " - At least one specific target customer with the following details:\n"
        "   * Company name\n"
        "   * Estimated annual revenue (in EUR millions)\n"
        "   * Primary product focus\n"
        "   * Brief justification (1-2 sentences)\n\n"
        f"Your response must be **specific to {tag_data[0]['tag']} and based on both the dataset and industry knowledge.**"
    )

    # User input formatted strictly for AI processing
    user_prompt = (
            f"Industry: {tag_data[0]['tag']}\n"
            f"Description: {tag_data[0]['description'] or 'Not provided'}\n\n"
            "**Sales Breakdown by Country (Customer Count Only):**\n"
            + "\n".join(f"- {country}: {data['percentage']:.1f}% ({data['customer_count']} customers)"
                        for country, data in country_breakdown.items())
    )

    # Initialize Perplexity client
    client = OpenAI(
        api_key=os.getenv("PERPLEXITY_API_KEY"),
        base_url="https://api.perplexity.ai"
    )

    # Call Perplexity API
    try:
        response = client.chat.completions.create(
            model="sonar-reasoning-pro",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1
        )

        analysis_text = response.choices[0].message.content.strip()
        # Remove content between <think> tags
        import re
        analysis_text = re.sub(r'<think>.*?</think>', '', analysis_text, flags=re.DOTALL)
        analysis_text = analysis_text.strip()


        suggestions = parse_ai_suggestions(analysis_text)

        # Format for the existing modal
        formatted_suggestions = []
        for suggestion in suggestions:
            # Convert country name to ISO code
            country_code = None
            for country in pycountry.countries:
                if country.name == suggestion.get('country'):
                    country_code = country.alpha_2
                    break

            formatted_suggestions.append({
                "company_name": suggestion.get('company_name', ''),
                "description": suggestion.get('justification', ''),
                "estimated_revenue": suggestion.get('estimated_revenue', 0),
                "country": suggestion.get('country', ''),
                "country_code": country_code or '',
                "tag_id": tag_id
            })

        return jsonify({
            "success": True,
            "analysis": analysis_text,
            "suggestions": formatted_suggestions,
            "html": render_template('partials/industry_insights.html',
                                    suggestions=formatted_suggestions,
                                    analysis=analysis_text)
        })

    except Exception as e:
        return jsonify({"error": f"API Error: {str(e)}"}), 500

@customers_bp.route('/api/tags/<int:tag_id>/parts', methods=['GET'])
def get_tag_parts(tag_id):
    with db_cursor() as cur:
        parts_data = _execute_with_cursor(
            cur,
            """
            SELECT 
                pn.base_part_number,
                COUNT(DISTINCT sol.id) as order_count,
                SUM(sol.quantity) as total_quantity
            FROM industry_tags it
            JOIN customer_industry_tags cit ON it.id = cit.tag_id
            JOIN customers c ON cit.customer_id = c.id
            JOIN sales_orders so ON c.id = so.customer_id
            JOIN sales_order_lines sol ON so.id = sol.sales_order_id
            JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
            WHERE it.id = ?
            GROUP BY pn.base_part_number
            ORDER BY total_quantity DESC
            LIMIT 10
            """,
            (tag_id,)
        ).fetchall()

    return jsonify({
        'labels': [row['base_part_number'] for row in parts_data],
        'values': [row['total_quantity'] for row in parts_data]
    })


@customers_bp.route('/api/tags/<tag_id>/customers')
def get_tag_customers(tag_id):
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 1000
        # Get base customer list for this tag
        base_query = """
            SELECT c.* 
            FROM customers c
            JOIN customer_industry_tags ct ON c.id = ct.customer_id
            WHERE ct.tag_id = ?
        """

        if _using_postgres():
            email_date_expr = "CAST(e.sent_date AS timestamp)"
            rfq_date_expr = "CAST(r.entered_date AS timestamp)"
            order_date_expr = "CAST(so.date_entered AS timestamp)"
        else:
            email_date_expr = "e.sent_date"
            rfq_date_expr = "r.entered_date"
            order_date_expr = "so.date_entered"

        # Your existing activity query from the prospecting route
        activity_query = f"""
        WITH latest_activity AS (
            SELECT 
                customer_id,
                activity_type,
                activity_date,
                status,
                activity_id
            FROM (
                SELECT a.*, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY activity_date DESC) as rn
                FROM (
                    SELECT DISTINCT
                        c.customer_id,
                        'email' as activity_type,
                        {email_date_expr} as activity_date,
                        CASE 
                            WHEN LOWER(e.sender_email) IN (SELECT LOWER(email) FROM users) THEN 'outbound'
                            ELSE 'received'
                        END as status,
                        e.id as activity_id
                    FROM emails e
                    JOIN contacts c ON (LOWER(e.sender_email) = LOWER(c.email) OR LOWER(e.recipient_email) = LOWER(c.email))

                    UNION ALL

                    SELECT 
                        r.customer_id,
                        'rfq' as activity_type,
                        {rfq_date_expr} as activity_date,
                        r.status,
                        r.id as activity_id
                    FROM rfqs r

                    UNION ALL

                    SELECT 
                        so.customer_id,
                        'order' as activity_type,
                        {order_date_expr} as activity_date,
                        ss.status_name as status,
                        so.id as activity_id
                    FROM sales_orders so
                    LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
                ) a
            ) ranked
            WHERE rn = 1
        ),
        latest_comment AS (
            SELECT 
                customer_id,
                update_text,
                date as update_date,
                s.name as update_salesperson_name
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY date DESC) as rn
                FROM customer_updates
            ) cu
            LEFT JOIN salespeople s ON cu.salesperson_id = s.id
            WHERE rn = 1
        )
        SELECT 
            c.*,
            la.activity_date as latest_activity,
            la.activity_type,
            la.status as activity_status,
            lc.update_text,
            lc.update_date,
            lc.update_salesperson_name,
            s.name as assigned_salesperson_name,
            s.id as assigned_salesperson_id
        FROM customers c
        LEFT JOIN latest_activity la ON c.id = la.customer_id
        LEFT JOIN latest_comment lc ON c.id = lc.customer_id
        LEFT JOIN salespeople s ON c.salesperson_id = s.id
        WHERE c.id IN (
            SELECT c2.id 
            FROM customers c2
            JOIN customer_industry_tags ct ON c2.id = ct.customer_id
            WHERE ct.tag_id = ?
        )
        ORDER BY c.name
        LIMIT ? OFFSET ?
        """

        offset = (page - 1) * per_page
        with db_cursor() as cur:
            _execute_with_cursor(cur, activity_query, (tag_id, per_page, offset))
            customers = cur.fetchall()
            print("First customer data from DB:", dict(customers[0]) if customers else None)

            count_query = """
                SELECT COUNT(DISTINCT c.id) as total
                FROM customers c
                JOIN customer_industry_tags ct ON c.id = ct.customer_id
                WHERE ct.tag_id = ?
            """
            _execute_with_cursor(cur, count_query, (tag_id,))
            total_count = cur.fetchone()['total']
        total_pages = (total_count + per_page - 1) // per_page

        return jsonify({
            'customers': [dict(row) for row in customers],
            'total_pages': total_pages
        })

    except Exception as e:
        logging.error(f"Error fetching customers for tag {tag_id}: {e}")
        return jsonify({'error': str(e)}), 500

@customers_bp.route('/api/continents-mapping')
def get_continents_mapping():
    return jsonify(get_countries_by_continent())

@customers_bp.route('api/tags/<int:tag_id>/details')
def tag_details(tag_id):
    description = get_tag_description(tag_id)
    return jsonify({
        'description': description
    })


@customers_bp.route('api/tags/<int:tag_id>', methods=['PUT'])
def update_tag(tag_id):
    if not request.json:
        return jsonify({'error': 'No JSON data provided'}), 400

    data = request.json
    if 'name' not in data or 'description' not in data:
        return jsonify({'error': 'Missing required fields: name and description'}), 400

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                "UPDATE industry_tags SET tag = ?, description = ? WHERE id = ?",
                (data['name'], data['description'], tag_id)
            )
            if cur.rowcount == 0:
                return jsonify({'error': f'No tag found with id {tag_id}'}), 404

        return jsonify({'success': True})
    except Exception as e:
        logger.exception("Failed to update industry tag")
        return jsonify({'error': 'Database error occurred'}), 500




@customers_bp.route('/api/customers/create-from-suggestion', methods=['POST'])
def create_customer_from_suggestion():
    suggestion_data = request.json

    # Create new customer record
    try:
        with db_cursor(commit=True) as cur:
            row = _execute_with_cursor(
                cur,
                """
                INSERT INTO customers (
                    name, country, status_id, estimated_revenue, 
                    industry_focus, creation_date
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                [
                    suggestion_data['company_name'],
                    suggestion_data['country'],
                    1,  # Status: Lead
                    suggestion_data.get('estimated_revenue', 0),
                    suggestion_data.get('primary_product_focus', '')
                ]
            ).fetchone()
            customer_id = row['id'] if row else getattr(cur, 'lastrowid', None)

            _execute_with_cursor(
                cur,
                """
                INSERT INTO customer_industry_tags (customer_id, tag_id)
                VALUES (?, ?)
                """,
                (customer_id, suggestion_data['tag_id'])
            )

        return jsonify({"success": True, "customer_id": customer_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@customers_bp.route('/api/tags/<int:tag_id>/suggestions', methods=['GET'])
def get_tag_suggestions(tag_id):
    try:
        refresh = str(request.args.get('refresh', '')).lower() in ('1', 'true', 'yes')
        prompt = (request.args.get('prompt') or '').strip()
        if prompt:
            refresh = True
        with db_cursor() as cur:
            existing_customers = _execute_with_cursor(
                cur,
                """
                SELECT DISTINCT c.id, c.name, c.country, c.estimated_revenue
                FROM customers c
                JOIN customer_industry_tags cit ON c.id = cit.customer_id
                WHERE cit.tag_id = ? AND c.country IS NOT NULL
                """,
                (tag_id,)
            ).fetchall()

            tag_info = _execute_with_cursor(
                cur,
                """
                SELECT tag, description
                FROM industry_tags
                WHERE id = ?
                """,
                (tag_id,)
            ).fetchone()

            cached = None
            if not refresh and not prompt:
                cached = _execute_with_cursor(
                    cur,
                    """
                    SELECT analysis, suggestions, updated_at
                    FROM market_intelligence_cache
                    WHERE tag_id = ?
                    """,
                    (tag_id,)
                ).fetchone()

        if not tag_info:
            return jsonify({"error": "Tag not found"}), 404

        if cached:
            cached_suggestions = cached.get('suggestions') or []
            if isinstance(cached_suggestions, str):
                try:
                    cached_suggestions = json.loads(cached_suggestions)
                except Exception:
                    cached_suggestions = []
            return jsonify({
                "success": True,
                "suggestions": cached_suggestions,
                "description": tag_info['description'],
                "tag": tag_info['tag'],
                "analysis": cached.get('analysis', ''),
                "cached": True,
                "cached_at": cached.get('updated_at')
            })

        # Format existing customer data for analysis
        country_breakdown = {}
        for customer in existing_customers:
            country = customer['country']
            if country not in country_breakdown:
                country_breakdown[country] = {
                    'customer_count': 0,
                    'customers': []
                }
            country_breakdown[country]['customer_count'] += 1
            country_breakdown[country]['customers'].append({
                'name': customer['name'],
                'estimated_revenue': customer['estimated_revenue']
            })

        total_customers = len(existing_customers)
        for country in country_breakdown:
            country_breakdown[country]['percentage'] = (
                    country_breakdown[country]['customer_count'] / total_customers * 100
            )

        # Prepare system message for Perplexity (overview only, no company recommendations)
        system_message = (
            f"You are a market analyst specializing in {tag_info['tag']}. "
            "Based on the current customer distribution data, assess whether the coverage matches the real-world landscape for this market. "
            "Then highlight the biggest geographic gaps, underrepresented regions, and notable concentration risks. "
            "Do NOT provide specific company recommendations.\n\n"
            "Include:\n"
            "   - Coverage assessment (aligned / partial / misaligned) with a short justification\n"
            "   - Top 3-6 geographic gaps or underrepresented regions (countries or subregions)\n"
            "   - Any over-concentration risks (if applicable)\n"
            "   - Suggested next steps for deeper research (no company names)\n"
            f"Focus solely on {tag_info['tag']}.\n"
            "Format your response using a structured narrative list, NOT tables.\n\n"
            "## Market Analysis\n"
            "[Assess alignment and explain the biggest gaps and risks.]\n"
            "DO NOT use tables under any circumstances.\n"
        )

        # Prepare user prompt with current distribution
        user_prompt = (
                f"Industry: {tag_info['tag']}\n"
                f"Description: {tag_info['description'] or 'Not provided'}\n\n"
                "Current Customer Distribution:\n"
                + "\n".join(
            f"- {country}: {data['percentage']:.1f}% ({data['customer_count']} customers)"
            for country, data in country_breakdown.items()
        )
                + "\n\nExisting customers by country:\n"
                + "\n".join(
            f"- {country}: {', '.join(c['name'] for c in data['customers'])}"
            for country, data in country_breakdown.items()
        )
        )
        if prompt:
            user_prompt += f"\n\nUser focus: {prompt}"

        # Initialize Perplexity client
        client = OpenAI(
            api_key=os.getenv("PERPLEXITY_API_KEY"),
            base_url="https://api.perplexity.ai"
        )

        # Get the AI analysis response from Perplexity
        response = client.chat.completions.create(
            model="sonar-reasoning-pro",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1
        )

        # Get raw analysis text
        raw_analysis = response.choices[0].message.content.strip()

        # Remove thinking tags from Perplexity response
        import re
        raw_analysis = re.sub(r'<think>.*?</think>', '', raw_analysis, flags=re.DOTALL)
        raw_analysis = raw_analysis.strip()

        # Check if OpenAI API key is available for post-processing
        import os
        if os.environ.get("OPENAI_API_KEY"):
            # Use OpenAI to format the response consistently
            try:
                # Import and use the formatting function
                from utils import format_with_openai
                formatted_analysis = format_with_openai(raw_analysis, tag_info, country_breakdown)
            except Exception as e:
                import logging
                logging.error(f"OpenAI formatting error: {str(e)}")
                formatted_analysis = raw_analysis  # Fallback to raw analysis
        else:
            formatted_analysis = raw_analysis  # No OpenAI key, use raw analysis

        # Strip any Growth Opportunities-style sections to avoid company recommendations.
        formatted_analysis = re.sub(
            r"\n?##?\s*Growth Opportunities\b[\s\S]*",
            "",
            formatted_analysis,
            flags=re.IGNORECASE
        ).strip()

        formatted_suggestions = []

        try:
            from psycopg2.extras import Json
            suggestions_payload = Json(formatted_suggestions)
        except Exception:
            suggestions_payload = json.dumps(formatted_suggestions)

        if not prompt:
            with db_cursor(commit=True) as cur:
                _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO market_intelligence_cache (tag_id, analysis, suggestions, updated_at, created_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (tag_id) DO UPDATE
                    SET analysis = EXCLUDED.analysis,
                        suggestions = EXCLUDED.suggestions,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (tag_id, formatted_analysis, suggestions_payload)
                )

        return jsonify({
            "success": True,
            "suggestions": formatted_suggestions,
            "description": tag_info['description'],
            "tag": tag_info['tag'],
            "analysis": formatted_analysis,
            "cached": False
        })

    except Exception as e:
        import logging
        logging.error(f"Tag suggestions API error: {str(e)}")
        return jsonify({"error": f"API Error: {str(e)}"}), 500


@customers_bp.route('/api/tags/<int:tag_id>/suggestions/refine', methods=['POST'])
def refine_tag_suggestions(tag_id):
    try:
        data = request.json
        feedback = data.get('feedback', '')
        excluded_companies = data.get('excluded_companies', [])

        with db_cursor() as cur:
            tag_info = _execute_with_cursor(
                cur,
                """
                SELECT tag, description
                FROM industry_tags
                WHERE id = ?
                """,
                (tag_id,)
            ).fetchone()

        if not tag_info:
            return jsonify({"error": "Tag not found"}), 404

        # Modify system message to include the feedback
        system_message = (
            f"You are a market analyst specializing in {tag_info['tag']}. "
            "A user has provided feedback on the previous suggestions. "
            f"They said: '{feedback}'. Based on this feedback, provide new, "
            "relevant company suggestions.\n\n"
            "Include:\n"
            "   - Target company name\n"
            "   - Company website URL\n"
            "   - Country (official name)\n"
            "   - Annual revenue (EUR raw number)\n"
            "   - Main product/industry focus\n\n"
            "DO NOT suggest any of these companies that were already suggested: "
            f"{', '.join(excluded_companies)}\n"
            "Format your response using a structured narrative list, NOT tables.\n\n"
            "## Market Analysis\n"
            "[Write a brief analysis of the feedback and what kind of companies would be more appropriate.]\n\n"
            "## Growth Opportunities\n"
            "Provide company suggestions in this format:\n\n"
            "**Company Name:** [Company Name]\n"
            "**Website:** <a href=\"[URL]\" target=\"_blank\">[Website]</a> (If no website exists, write 'N/A')\n"
            "**Country:** [Country]\n"
            "**Annual Revenue:** [Revenue in EUR]\n"
            "**Main Focus:** [Main industry focus]\n\n"
            "DO NOT use tables under any circumstances.\n"
            "When providing company suggestions, format website links as raw HTML instead of Markdown.\n"
        )

        # Create user prompt
        user_prompt = (
            f"Industry: {tag_info['tag']}\n"
            f"Description: {tag_info['description'] or 'Not provided'}\n\n"
            f"User feedback: {feedback}\n"
            f"Please provide new suggestions, excluding: {', '.join(excluded_companies)}"
        )

        # Get the refined AI analysis from Perplexity
        client = OpenAI(
            api_key=os.getenv("PERPLEXITY_API_KEY"),
            base_url="https://api.perplexity.ai"
        )

        response = client.chat.completions.create(
            model="sonar-reasoning-pro",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1
        )

        # Get raw analysis
        raw_analysis = response.choices[0].message.content.strip()

        # Remove thinking tags from Perplexity response
        import re
        raw_analysis = re.sub(r'<think>.*?</think>', '', raw_analysis, flags=re.DOTALL)
        raw_analysis = raw_analysis.strip()

        # For formatting, we need country distribution data (dummy for refinement)
        country_breakdown = {}  # Empty for refinement, doesn't need real data

        # Check if OpenAI API key is available for post-processing
        import os
        if os.environ.get("OPENAI_API_KEY"):
            # Use OpenAI to format the response consistently
            try:
                # Import and use the formatting function
                from utils import format_with_openai
                formatted_analysis = format_with_openai(raw_analysis, tag_info, country_breakdown)
            except Exception as e:
                import logging
                logging.error(f"OpenAI formatting error in refinement: {str(e)}")
                formatted_analysis = raw_analysis  # Fallback to raw analysis
        else:
            formatted_analysis = raw_analysis  # No OpenAI key, use raw analysis

        # Use the enhanced parse_ai_suggestions function
        from utils import parse_ai_suggestions
        suggestions = parse_ai_suggestions(formatted_analysis)

        # Format suggestions for the response
        formatted_suggestions = []
        for suggestion in suggestions:
            # Skip any companies that are in the excluded list
            if suggestion.get('company_name', '') in excluded_companies:
                continue

            country_code = None
            # Get country code if possible
            try:
                import pycountry
                for country in pycountry.countries:
                    if country.name == suggestion.get('country'):
                        country_code = country.alpha_2
                        break
            except:
                pass  # If pycountry fails, we'll have an empty country_code

            formatted_suggestions.append({
                "company_name": suggestion.get('company_name', ''),
                "description": suggestion.get('justification', ''),
                "estimated_revenue": suggestion.get('estimated_revenue', 0),
                "country": suggestion.get('country', ''),
                "country_code": country_code or '',
                "tag_id": tag_id,
                "product_focus": suggestion.get('product_focus', ''),
                "website": suggestion.get('website', '')
            })

        return jsonify({
            "success": True,
            "suggestions": formatted_suggestions,
            "description": tag_info['description'],
            "tag": tag_info['tag'],
            "analysis": formatted_analysis
        })

    except Exception as e:
        import logging
        logging.error(f"Refine suggestions API error: {str(e)}")
        return jsonify({"error": f"API Error: {str(e)}"}), 500


@customers_bp.route('/api/tags', methods=['POST'])
def create_tag():
    try:
        data = request.get_json()
        print("Received data:", data)

        tag_name = data.get('tag', '').strip()
        description = data.get('description')
        print("Description value:", description)

        if description is not None:
            description = description.strip() or None
        print("Processed description:", description)

        parent_tag_id = data.get('parent_tag_id')

        with db_cursor(commit=True) as cur:
            new_tag = _execute_with_cursor(
                cur,
                """
                INSERT INTO industry_tags (tag, description, parent_tag_id)
                VALUES (?, ?, ?)
                RETURNING id, tag, description, parent_tag_id
                """,
                (tag_name, description, parent_tag_id)
            ).fetchone()

        return jsonify({
            'id': new_tag['id'],
            'tag': new_tag['tag'],
            'description': new_tag['description'],
            'parent_tag_id': new_tag['parent_tag_id']
        }), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@customers_bp.route('/api/country-lookup', methods=['GET'])
def lookup_country_code():
    """Convert a country name to its ISO-3166 two-letter code using pycountry"""
    country_name = request.args.get('name', '')
    if not country_name:
        return jsonify({'error': 'No country name provided'}), 400

    try:
        # Try exact match first
        country = pycountry.countries.get(name=country_name)

        # If not found, try case-insensitive search
        if not country:
            countries = list(pycountry.countries)
            for c in countries:
                if c.name.lower() == country_name.lower():
                    country = c
                    break

        # If still not found, try searching in the official names
        if not country:
            for c in countries:
                if hasattr(c, 'official_name') and c.official_name.lower() == country_name.lower():
                    country = c
                    break

        # If still not found, try partial matching
        if not country:
            matches = []
            for c in countries:
                if country_name.lower() in c.name.lower():
                    matches.append(c)

            if len(matches) == 1:
                country = matches[0]
            elif len(matches) > 1:
                # If multiple matches, prefer exact word boundaries
                for c in matches:
                    name_parts = c.name.lower().split()
                    if country_name.lower() in name_parts:
                        country = c
                        break
                else:
                    # If still ambiguous, just take the first match
                    country = matches[0]

        if country:
            return jsonify({
                'code': country.alpha_2,
                'name': country.name
            })
        else:
            return jsonify({'error': 'Country not found'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500



def generate_structured_query(nl_query):
    """Convert natural language query to structured parameters"""
    # Example prompt for the LLM
    prompt = f"""Convert this natural language query into structured search parameters:
    Query: {nl_query}

    Extract relevant information for:
    - Industry/tag terms
    - Geographic locations (continents/countries)
    - Company attributes (size, revenue, status)
    - Activity filters (recent emails, orders, etc)

    Format as JSON with these possible keys:
    - tags: list of relevant industry tags
    - continents: list of continents
    - countries: list of country codes
    - min_revenue: minimum revenue amount
    - max_revenue: maximum revenue amount
    - status: customer status (lead, prospect, negotiating, active)
    - activity_type: type of activity to filter by
    - activity_period: timeframe for activity filter (in days)
    """

    # Use your LLM integration to process the prompt
    structured_params = process_with_llm(prompt)
    return structured_params


def execute_natural_language_query(structured_query):
    """Execute search based on structured parameters"""
    # Start with base query
    query_parts = ["SELECT DISTINCT c.* FROM customers c"]
    params = []

    # Build query conditionally based on structured parameters
    if structured_query.get('tags'):
        query_parts.append("""
            JOIN customer_tags ct ON c.id = ct.customer_id
            JOIN tags t ON ct.tag_id = t.id
            WHERE t.name IN ({})
        """.format(','.join('?' * len(structured_query['tags']))))
        params.extend(structured_query['tags'])

    if structured_query.get('continents'):
        if 'WHERE' not in ' '.join(query_parts):
            query_parts.append("WHERE")
        else:
            query_parts.append("AND")
        query_parts.append("c.continent IN ({})".format(
            ','.join('?' * len(structured_query['continents']))))
        params.extend(structured_query['continents'])

    # Add other filters (revenue, status, activity) similarly

    final_query = ' '.join(query_parts)
    with db_cursor() as cur:
        _execute_with_cursor(cur, final_query, tuple(params))
        return cur.fetchall()


def enrich_customers_with_activity(customers):
    """Add activity and update information to customer records"""
    if not customers:
        return []

    # Use the existing activity query from the prospecting route
    activity_query = """
    WITH latest_activity AS (
        -- Your existing activity query here
    )
    SELECT -- fields
    FROM customers c
    LEFT JOIN latest_activity la ON c.id = la.customer_id
    WHERE c.id IN ({})
    """.format(','.join('?' * len(customers)))

    with db_cursor() as cur:
        _execute_with_cursor(cur, activity_query, tuple([c['id'] for c in customers]))
        activities = cur.fetchall()

    # Update customer records with activity data
    activity_lookup = {row[0]: row for row in activities}
    for customer in customers:
        if customer['id'] in activity_lookup:
            data = activity_lookup[customer['id']]
            customer.update({
                'latest_activity': data[1],
                'activity_type': data[2],
                # Add other fields as needed
            })

    return customers


def process_with_llm(prompt):
    """Process a prompt with the OpenAI LLM and return structured data"""
    try:
        if client is None:
            raise ValueError("OPENAI_API_KEY is not configured.")

        logging.debug(f"Sending prompt to OpenAI: {prompt}")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a data parsing assistant. Return only valid JSON without any explanation or markdown formatting."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.2
        )

        # Get the response content
        response_content = response.choices[0].message.content.strip()
        logging.debug(f"Raw OpenAI response: {response_content}")

        # Clean the response content
        if response_content.startswith('```'):
            parts = response_content.split('```')
            if len(parts) >= 2:
                response_content = parts[1]
                if response_content.startswith('json'):
                    response_content = response_content[4:]
        response_content = response_content.strip()

        # Parse the JSON response
        try:
            structured_data = json.loads(response_content)
            logging.debug(f"Parsed structured data: {structured_data}")
            return structured_data

        except json.JSONDecodeError as e:
            logging.error(f"JSON parsing error: {str(e)}")
            logging.error(f"Failed to parse content: {response_content}")
            raise ValueError("Invalid JSON response from OpenAI")

    except Exception as e:
        logging.error(f"Error in LLM processing: {str(e)}")
        if hasattr(e, 'response'):
            logging.error(f"OpenAI Response: {e.response}")
        raise ValueError(f"LLM processing failed: {str(e)}")

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
if client is None:
    logging.warning("OPENAI_API_KEY not found in routes.customers. AI search parsing is disabled.")


@customers_bp.route('/natural_language_search', methods=['GET'])
def natural_language_search():
    """Use natural language to generate potential target companies"""
    query = request.args.get('query', '').strip()
    if not query:
        return redirect(url_for('customers.prospecting'))

    try:
        # Get existing customers for reference
        with db_cursor() as cur:
            existing_customers = [
                row['name']
                for row in _execute_with_cursor(cur, "SELECT name FROM customers").fetchall()
            ]

        # Generate the AI prompt based on the natural language query
        prompt = f"""Based on this search request: "{query}"
        Please suggest potential target companies that match this criteria. 
        These companies should be real but NOT any of our existing customers.
        Remember that we are a connector manufacturer and distributor.
        Only suggest companies that would need this service.

        Return a JSON array containing companies with this exact format:
        {{
            "name": "Company Name",
            "description": "Company description",
            "estimated_revenue": 1000000,
            "website": "https://www.example.com",
            "country": "DE"
        }}

        Important: 
        - Always provide complete website URLs including https://
        - Ensure these are real companies that match the search criteria
        - Do not include any of these existing customers: {', '.join(existing_customers)}
        """

        # Generate suggestions using your existing function
        industry_insights, ai_prompt = generate_industry_insights_with_custom_prompt(
            prompt,
            existing_customers
        )

        # Get base data for template
        tags = get_nested_tags()
        salespeople = get_salespeople()
        continent_mapping = get_countries_by_continent()
        continents = list(continent_mapping.keys())

        breadcrumbs = generate_breadcrumbs(
            ('Home', url_for('index')),
            ('Prospecting', url_for('customers.prospecting'))
        )

        return render_template(
            'prospecting.html',
            customers=[],  # Empty list since we're showing suggestions, not existing customers
            tags=tags,
            salespeople=salespeople,
            customer_statuses=get_customer_statuses(),
            breadcrumbs=breadcrumbs,
            industry_insights=industry_insights,
            get_status_name=get_status_name,
            continents=continents,
            nl_query=query
        )

    except Exception as e:
        logging.error(f"Error processing natural language search: {str(e)}")
        flash("Sorry, I couldn't process that search. Please try rephrasing it.", "warning")
        return redirect(url_for('customers.prospecting'))


@customers_bp.route('/api/tags/<int:tag_id>/categories', methods=['GET'])
def get_categories_for_tag(tag_id):
    try:
        with db_cursor() as cur:
            categories_data = _execute_with_cursor(
                cur,
                """
                SELECT 
                    CASE 
                        WHEN pc.category_name IS NULL THEN 'Uncategorized' 
                        ELSE pc.category_name 
                    END as category_name, 
                    COUNT(*) as part_count
                FROM customer_industry_tags cit
                JOIN sales_orders so ON cit.customer_id = so.customer_id
                JOIN sales_order_lines sol ON so.id = sol.sales_order_id
                JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                LEFT JOIN part_categories pc ON pn.category_id = pc.category_id
                WHERE cit.tag_id = ?
                GROUP BY category_name
                ORDER BY part_count DESC
                """,
                (tag_id,)
            ).fetchall()

        if not categories_data:
            return jsonify({"labels": [], "values": []}), 200

        labels = [row["category_name"] for row in categories_data]
        values = [row["part_count"] for row in categories_data]

        return jsonify({
            "labels": labels,
            "values": values
        })
    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500


@customers_bp.route('/<int:customer_id>/suggest-contacts', methods=['GET'])
def suggest_contacts(customer_id):
    """
    Suggests potential contacts for a customer based on email communication.
    Finds email addresses from the same domains as the customer but aren't yet in contacts.
    """
    try:
        domains = get_customer_domains(customer_id)
        if not domains:
            return jsonify({
                'success': True,
                'message': 'No domains found for this customer',
                'suggested_contacts': []
            })

        current_app.logger.info(f"Found domains for customer {customer_id}: {domains}")

        with db_cursor() as cur:
            existing_contacts = _execute_with_cursor(
                cur,
                'SELECT LOWER(email) as email FROM contacts WHERE customer_id = ?',
                (customer_id,)
            ).fetchall()
            existing_emails = set(contact['email'] for contact in existing_contacts)
            current_app.logger.info(f"Customer has {len(existing_emails)} existing contacts")

            suggestions = []
            for domain in domains:
                domain_lower = domain.lower()
                sender_query = """
                    SELECT 
                        LOWER(sender_email) as email, 
                        COUNT(*) as email_count,
                        MIN(sent_date) as first_seen,
                        MAX(sent_date) as last_seen
                    FROM emails 
                    WHERE LOWER(sender_email) LIKE ?
                    GROUP BY LOWER(sender_email)
                """
                sender_rows = _execute_with_cursor(
                    cur,
                    sender_query,
                    (f"%@{domain_lower}",)
                ).fetchall()

                recipient_query = """
                    SELECT 
                        LOWER(recipient_email) as email, 
                        COUNT(*) as email_count,
                        MIN(sent_date) as first_seen,
                        MAX(sent_date) as last_seen
                    FROM emails 
                    WHERE LOWER(recipient_email) LIKE ?
                    GROUP BY LOWER(recipient_email)
                """
                recipient_rows = _execute_with_cursor(
                    cur,
                    recipient_query,
                    (f"%@{domain_lower}",)
                ).fetchall()

                email_data = {}
                for row in list(sender_rows) + list(recipient_rows):
                    email = row['email'].lower()

                    if email in existing_emails:
                        continue

                    if email in email_data:
                        email_data[email]['email_count'] += row['email_count']
                        email_data[email]['first_seen'] = min(email_data[email]['first_seen'], row['first_seen'])
                        email_data[email]['last_seen'] = max(email_data[email]['last_seen'], row['last_seen'])
                    else:
                        email_data[email] = {
                            'email': email,
                            'email_count': row['email_count'],
                            'first_seen': row['first_seen'],
                            'last_seen': row['last_seen']
                        }

                for email, data in email_data.items():
                    recent_subjects = _execute_with_cursor(
                        cur,
                        'SELECT subject FROM emails WHERE LOWER(sender_email) = ? OR LOWER(recipient_email) = ? ORDER BY sent_date DESC LIMIT 3',
                        (email, email)
                    ).fetchall()

                    data['recent_subjects'] = [subj['subject'] for subj in recent_subjects]
                    suggestions.append(data)

            suggestions.sort(key=lambda x: (x['email_count'], x['last_seen']), reverse=True)

        return jsonify({
            'success': True,
            'suggested_contacts': suggestions
        })

    except Exception as e:
        current_app.logger.error("Error building suggested contacts", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@customers_bp.route('/customers/activity/<activity_type>/<int:activity_id>', methods=['GET'])
def get_customer_activity_detail(activity_type, activity_id):
    """Get detailed information for a specific activity"""
    return get_activity_detail(activity_type, activity_id)


@customers_bp.route('/<int:customer_id>/contact_communication', methods=['POST'])
@login_required
def add_contact_communication(customer_id):
    # Get salesperson_id from link table
    with db_cursor() as cur:
        result = _execute_with_cursor(
            cur,
            'SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?',
            (current_user.id,)
        ).fetchone()

        if not result:
            flash('User not linked to a salesperson account')
            return redirect(url_for('customers.edit_customer', customer_id=customer_id))

        salesperson_id = result['legacy_salesperson_id']

        # Get data from the form
        contact_id = request.form.get('contact_id')
        communication_type = request.form.get('communication_type')  # 'email', 'phone', etc.
        notes = request.form.get('notes', '')

        if not contact_id or not communication_type:
            flash('Contact and communication type are required')
            return redirect(url_for('customers.edit_customer', customer_id=customer_id))

        contact = _execute_with_cursor(
            cur,
            'SELECT name, email FROM contacts WHERE id = ?',
            (contact_id,)
        ).fetchone()

    if communication_type == 'email':
        update_text = f"Emailed {contact['name']} ({contact['email']})"
    elif communication_type == 'phone':
        update_text = f"Called {contact['name']}"
    else:
        update_text = f"{communication_type.capitalize()} with {contact['name']}"

    # Add custom notes if provided
    if notes:
        update_text += f": {notes}"

    # Insert the update with contact information
    insert_update(customer_id, salesperson_id, update_text, contact_id, communication_type)

    return redirect(url_for('customers.edit_customer', customer_id=customer_id))


# Add this function to your customers_bp routes
@customers_bp.route('/api/customer/<int:customer_id>/contacts', methods=['GET'])
@login_required
def get_customer_contacts_api(customer_id):
    """API endpoint to get contacts for a specific customer"""
    try:
        # Check if user has permission to view this customer
        if not current_user.is_administrator() and not current_user.can(Permission.VIEW_CUSTOMERS):
            # Check if the customer is assigned to the current user
            customer = get_customer_by_id(customer_id)
            if not customer or customer['salesperson_id'] != current_user.get_salesperson_id():
                return jsonify({'success': False, 'error': 'Unauthorized access'}), 403

        # Get contacts using the existing function
        contacts = get_contacts_by_customer(customer_id)

        # Convert database row objects to dictionaries
        serializable_contacts = []
        for contact in contacts:
            # If contact is a Row or similar database object, convert to dict
            if hasattr(contact, '_asdict'):  # For SQLAlchemy Row objects
                serializable_contacts.append(contact._asdict())
            elif hasattr(contact, '__dict__'):  # For ORM objects
                serializable_contacts.append({k: v for k, v in contact.__dict__.items() if not k.startswith('_')})
            elif isinstance(contact, dict):  # Already a dict
                serializable_contacts.append(contact)
            else:  # Fallback for other types
                serializable_contacts.append(dict(contact))

        return jsonify({
            'success': True,
            'contacts': serializable_contacts
        })
    except Exception as e:
        print(f"Error fetching contacts: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/salespeople/<int:salesperson_id>/contacts', methods=['GET'])
def contacts_page(salesperson_id):
    with db_cursor() as cur:
        contacts = _execute_with_cursor(
            cur,
            '''
            SELECT c.*, cu.name as customer_name
            FROM contacts c
            LEFT JOIN customers cu ON c.customer_id = cu.id
            ORDER BY c.name
            '''
        ).fetchall()

        customers = _execute_with_cursor(
            cur,
            'SELECT id, name FROM customers ORDER BY name'
        ).fetchall()

    breadcrumbs = generate_breadcrumbs(
        ('Home', url_for('index')),
        ('Customers', url_for('customers.customers')),
        ('Contacts', url_for('customers.contacts_page'))
    )

    return render_template('contacts.html', contacts=contacts, customers=customers, breadcrumbs=breadcrumbs)


# Additional routes needed for the contact page

@customers_bp.route('/api/contacts/<int:contact_id>/update_customer', methods=['POST'])
def update_contact_customer(contact_id):
    try:
        data = request.get_json()
        customer_id = data.get('customer_id')

        # Update the customer ID for this contact
        update_contact_customer_id(contact_id, customer_id)

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@customers_bp.route('/api/contacts/<int:contact_id>/delete', methods=['POST'])
def delete_contact(contact_id):
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(
                cur,
                'DELETE FROM contacts WHERE id = ?',
                (contact_id,)
            )

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# Helper function to get all contacts
def get_all_contacts():
    with db_cursor() as cur:
        contacts = _execute_with_cursor(
            cur,
            '''
            SELECT c.*, cu.name as customer_name
            FROM contacts c
            LEFT JOIN customers cu ON c.customer_id = cu.id
            ORDER BY c.name
            '''
        ).fetchall()
    return contacts if contacts else []


# Helper function to get contacts by customer
def get_contacts_by_customer(customer_id):
    with db_cursor() as cur:
        contacts = _execute_with_cursor(
            cur,
            '''
            SELECT c.*, cu.name as customer_name
            FROM contacts c
            LEFT JOIN customers cu ON c.customer_id = cu.id
            WHERE c.customer_id = ?
            ORDER BY c.name
            ''',
            (customer_id,)
        ).fetchall()
    return contacts if contacts else []


@customers_bp.route('/api/contact_lists', methods=['GET', 'POST'])
def handle_contact_lists():
    """API endpoint to handle getting all lists or creating a new list"""
    try:
        if request.method == 'GET':
            contact_lists = get_all_contact_lists()
            return jsonify(contact_lists)

        elif request.method == 'POST':
            data = request.get_json()
            name = data.get('name')
            contact_ids = data.get('contact_ids', [])

            if not name:
                return jsonify({'success': False, 'error': 'List name is required'}), 400

            # Ensure contact_ids is a list
            if contact_ids and not isinstance(contact_ids, list):
                contact_ids = [contact_ids]  # Convert single ID to list

            list_id = create_contact_list(name, contact_ids)
            return jsonify({'success': True, 'list_id': list_id})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@customers_bp.route('/api/contact_lists/<int:list_id>', methods=['GET', 'PUT', 'DELETE'])
def handle_contact_list(list_id):
    """API endpoint to handle operations on a specific list"""
    try:
        if request.method == 'GET':
            contact_list = get_contact_list_by_id(list_id)
            if not contact_list:
                return jsonify({'success': False, 'error': 'Contact list not found'}), 404
            return jsonify(contact_list)

        elif request.method == 'PUT':
            data = request.get_json()
            name = data.get('name')

            if name:
                success = update_contact_list_name(list_id, name)
                if not success:
                    return jsonify({'success': False, 'error': 'Contact list not found'}), 404

            # Handle adding or removing contacts if included in the request
            added = 0
            removed = 0

            add_contacts = data.get('add_contacts', [])
            if add_contacts:
                added = add_contacts_to_list(list_id, add_contacts)

            remove_contacts = data.get('remove_contacts', [])
            if remove_contacts:
                removed = remove_contacts_from_list(list_id, remove_contacts)

            return jsonify({
                'success': True,
                'contacts_added': added,
                'contacts_removed': removed
            })

        elif request.method == 'DELETE':
            success = delete_contact_list(list_id)
            if not success:
                return jsonify({'success': False, 'error': 'Contact list not found'}), 404
            return jsonify({'success': True})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@customers_bp.route('/api/contacts/<int:contact_id>/lists', methods=['GET'])
def get_lists_for_contact(contact_id):
    """API endpoint to get all lists that contain a specific contact"""
    try:
        lists = get_lists_by_contact_id(contact_id)
        return jsonify(lists)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@customers_bp.route('/api/contact_lists/<int:list_id>/contacts', methods=['POST', 'DELETE'])
def manage_list_contacts(list_id):
    """API endpoint to add or remove contacts from a list"""
    try:
        data = request.get_json()
        contact_ids = data.get('contact_ids', [])

        if not contact_ids:
            return jsonify({'success': False, 'error': 'No contacts specified'}), 400

        # Ensure contact_ids is a list
        if not isinstance(contact_ids, list):
            contact_ids = [contact_ids]

        if request.method == 'POST':
            # Add contacts to list
            count = add_contacts_to_list(list_id, contact_ids)
            return jsonify({'success': True, 'added': count})

        elif request.method == 'DELETE':
            # Remove contacts from list
            count = remove_contacts_from_list(list_id, contact_ids)
            return jsonify({'success': True, 'removed': count})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# Add this new API route for bulk operations on contacts
@customers_bp.route('/api/contacts', methods=['GET'])
def get_contacts_api():
    """API endpoint to get multiple contacts by ID"""
    try:
        contact_ids = request.args.getlist('ids')

        if not contact_ids:
            # Return all contacts if no IDs specified
            contacts = get_all_contacts()
            return jsonify(contacts)

        # Convert string IDs to integers
        contact_ids = [int(cid) for cid in contact_ids if cid.isdigit()]

        if not contact_ids:
            return jsonify([])

        # Get contacts by IDs
        contacts = get_contacts_by_ids(contact_ids)
        return jsonify(contacts)

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# Add this new route to your customers_bp blueprint

@customers_bp.route('/api/contacts/<int:contact_id>', methods=['GET'])
def get_contact_api(contact_id):
    """API endpoint to get contact details by ID"""
    try:
        contact = get_contact_by_id(contact_id)
        if contact is None:
            return jsonify({'success': False, 'error': 'Contact not found'}), 404

        # Convert contact to a dictionary if it's not already
        if not isinstance(contact, dict):
            contact = dict(contact)

        # Add customer name if not present and customer_id exists
        if 'customer_name' not in contact and contact.get('customer_id'):
            with db_cursor() as cur:
                customer = _execute_with_cursor(
                    cur,
                    'SELECT name FROM customers WHERE id = ?',
                    (contact['customer_id'],)
                ).fetchone()
                if customer:
                    contact['customer_name'] = customer['name']

        return jsonify(contact)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# Add these routes to your customers_bp blueprint

@customers_bp.route('/contacts/<int:contact_id>/status', methods=['POST'])
@login_required
def update_contact_status_route(contact_id):
    """Update a contact's status via AJAX"""
    try:
        data = request.get_json()
        status_id = data.get('status_id')

        if not status_id:
            return jsonify({'success': False, 'error': 'Status ID is required'}), 400

        # Verify the status exists
        status = get_contact_status_by_id(status_id)
        if not status:
            return jsonify({'success': False, 'error': 'Invalid status'}), 400

        # Update the contact status
        success = update_contact_status(contact_id, status_id)

        if success:
            return jsonify({
                'success': True,
                'status': status,
                'message': f'Contact status updated to {status["name"]}'
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to update status'}), 500

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/contacts/by-status/<int:status_id>')
@login_required
def contacts_by_status(status_id):
    """View contacts filtered by status"""
    try:
        # Get the status info
        status = get_contact_status_by_id(status_id)
        if not status:
            flash('Status not found!', 'error')
            return redirect(url_for('customers.list_customers'))

        # Get all contacts with this status (you'll need to create this function)
        contacts = get_all_contacts_by_status(status_id)

        # Get all statuses for the filter dropdown
        all_statuses = get_all_contact_statuses()

        return render_template(
            'customers/contacts_by_status.html',
            contacts=contacts,
            current_status=status,
            all_statuses=all_statuses
        )

    except Exception as e:
        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('customers.list_customers'))


@customers_bp.route('/contact-status-summary')
@login_required
def contact_status_summary():
    """Get status summary for dashboard widget"""
    try:
        status_counts = get_all_contact_status_counts()

        if request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': True, 'status_counts': status_counts})

        return render_template(
            'customers/partials/status_summary.html',
            status_counts=status_counts
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Add customer-specific contact routes
@customers_bp.route('/<int:customer_id>/contacts')
@login_required
def customer_contacts(customer_id):
    """View all contacts for a specific customer"""
    try:
        customer = get_customer_by_id(customer_id)
        if not customer:
            flash('Customer not found!', 'error')
            return redirect(url_for('customers.list_customers'))

        # Get contacts for this customer
        contacts = get_customer_contacts(customer_id)

        # Get all contact statuses for the filter dropdown
        contact_statuses = get_all_contact_statuses()

        return render_template(
            'customers/customer_contacts.html',
            customer=customer,
            contacts=contacts,
            contact_statuses=contact_statuses
        )

    except Exception as e:
        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('customers.list_customers'))


@customers_bp.route('/contacts')
@login_required
def all_contacts():
    """View all contacts across all customers"""
    try:
        search_term = request.args.get('search', '')
        customer_filter = request.args.get('customer', '')
        status_filter = request.args.get('status', '')

        # Get all customers for the dropdown filter
        customers = get_all_customers()

        # Get all contact statuses for the filter dropdown
        contact_statuses = get_all_contact_statuses()

        # Get contacts with filters
        contacts = get_all_contacts_filtered(search_term, customer_filter, status_filter)

        return render_template(
            'customers/all_contacts.html',
            contacts=contacts,
            customers=customers,
            contact_statuses=contact_statuses,
            search_term=search_term,
            customer_filter=customer_filter,
            status_filter=status_filter
        )

    except Exception as e:
        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('customers.list_customers'))


# Add this route to your customers blueprint (customers.py)

# Add this route to your customers blueprint (customers.py)
@customers_bp.route('/<int:customer_id>/contacts/api')
@login_required
def customer_contacts_api(customer_id):
    """API endpoint to get contacts for a customer - includes contacts from associated companies"""
    try:
        customer = get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Customer not found'}), 404

        # Get all related customer IDs (main customer + any associated children)
        related_customer_ids = [customer_id]

        # Check if this customer has associated companies
        associated_query = """
            SELECT associated_customer_id, c.name as associated_company_name
            FROM customer_associations ca
            LEFT JOIN customers c ON ca.associated_customer_id = c.id
            WHERE ca.main_customer_id = ?
        """
        with db_cursor() as cur:
            associated_results = _execute_with_cursor(
                cur,
                associated_query,
                (customer_id,)
            ).fetchall()

        associated_companies = {}
        if associated_results:
            for row in associated_results:
                child_id = row['associated_customer_id']
                related_customer_ids.append(child_id)
                associated_companies[child_id] = row['associated_company_name']

        # Get contacts for all related customers
        contacts = get_customer_contacts_consolidated(related_customer_ids)

        # Format contacts for JSON response
        contacts_data = []
        for contact in contacts:
            contact_customer_id = contact.get('customer_id')
            company_suffix = ""

            # Add company name suffix if this contact is from an associated company
            if contact_customer_id != customer_id and contact_customer_id in associated_companies:
                company_suffix = f" ({associated_companies[contact_customer_id]})"

            contacts_data.append({
                'id': contact.get('id'),
                'name': contact.get('name', '') + company_suffix,
                'email': contact.get('email', ''),
                'phone': contact.get('phone', ''),
                'job_title': contact.get('job_title', ''),
                'customer_id': contact_customer_id,
                'is_from_associated_company': contact_customer_id != customer_id
            })

        return jsonify({
            'success': True,
            'contacts': contacts_data,
            'has_associated_companies': len(associated_companies) > 0,
            'associated_companies': associated_companies
        })

    except Exception as e:
        print(f"ERROR: Error in customer_contacts_api: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


def get_customer_contacts_consolidated(customer_ids):
    """Get contacts from multiple customers consolidated"""
    if not customer_ids:
        return []

    placeholders = ','.join(['?' for _ in customer_ids])

    query = f"""
        SELECT c.*, cu.name as customer_name
        FROM contacts c
        LEFT JOIN customers cu ON c.customer_id = cu.id
        WHERE c.customer_id IN ({placeholders})
        ORDER BY c.name
    """

    with db_cursor() as cur:
        contacts = _execute_with_cursor(cur, query, tuple(customer_ids)).fetchall()

    return [dict(contact) for contact in contacts]

# Update your existing edit_contact function to include status
@customers_bp.route('/contacts/<int:contact_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_contact(contact_id):
    try:
        if request.method == 'GET':
            # Modify the query to include status information and timezone
            with db_cursor() as cur:
                contact = _execute_with_cursor(
                    cur,
                    '''
                    SELECT c.*, cu.name as customer_name, 
                           cs.name as status_name, cs.id as status_id, cs.color as status_color
                    FROM contacts c
                    LEFT JOIN customers cu ON c.customer_id = cu.id
                    LEFT JOIN contact_statuses cs ON c.status_id = cs.id
                    WHERE c.id = ?
                    ''',
                    (contact_id,)
                ).fetchone()

            if contact is None:
                return jsonify({'success': False, 'error': 'Contact not found'}), 404

            # Get all available statuses for the dropdown
            statuses = get_all_contact_statuses()

            return jsonify({
                'success': True,
                'contact': {
                    'id': contact['id'],
                    'name': contact['name'],
                    'second_name': contact.get('second_name'),
                    'email': contact['email'],
                    'company': contact.get('company'),
                    'job_title': contact.get('job_title'),
                    'customer_id': contact.get('customer_id'),
                    'customer_name': contact.get('customer_name'),
                    'notes': contact.get('notes'),
                    'status_id': contact.get('status_id'),
                    'status_name': contact.get('status_name'),
                    'status_color': contact.get('status_color'),
                    'timezone': contact.get('timezone', 'UTC')
                },
                'statuses': statuses
            })

        elif request.method == 'POST':
            data = request.get_json()
            name = data.get('name')
            email = data.get('email')
            second_name = data.get('second_name')
            company = data.get('company')
            job_title = data.get('job_title')
            customer_id = data.get('customer_id')
            notes = data.get('notes')
            status_id = data.get('status_id')
            timezone = data.get('timezone')  # Get timezone from form data

            # Update contact including status field and timezone
            with db_cursor(commit=True) as cur:
                _execute_with_cursor(
                    cur,
                    '''
                    UPDATE contacts 
                    SET name = ?, second_name = ?, email = ?, company = ?, job_title = ?, 
                        customer_id = ?, notes = COALESCE(?, notes), 
                        status_id = COALESCE(?, status_id),
                        timezone = COALESCE(?, timezone),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''',
                    (name, second_name, email, company, job_title, customer_id,
                     notes, status_id, timezone, contact_id)
                )

            return jsonify({'success': True})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@customers_bp.route('/contacts/<int:contact_id>/bump_status', methods=['POST'])
def bump_contact_status(contact_id):
    """Cycle through contact statuses (1->2->3->4->1)"""
    try:
        with db_cursor(commit=True) as cur:
            current_contact = _execute_with_cursor(
                cur,
                'SELECT status_id FROM contacts WHERE id = ?',
                (contact_id,)
            ).fetchone()

            if not current_contact:
                return jsonify({'success': False, 'error': 'Contact not found'})

            statuses = _execute_with_cursor(
                cur,
                'SELECT id, name, color FROM contact_statuses ORDER BY id'
            ).fetchall()

            if not statuses:
                return jsonify({'success': False, 'error': 'No contact statuses available'})

            current_status_id = current_contact['status_id']
            current_index = 0
            for i, status in enumerate(statuses):
                if status['id'] == current_status_id:
                    current_index = i
                    break

            next_index = (current_index + 1) % len(statuses)
            next_status = statuses[next_index]

            _execute_with_cursor(
                cur,
                'UPDATE contacts SET status_id = ? WHERE id = ?',
                (next_status['id'], contact_id)
            )

        return jsonify({
            'success': True,
            'new_status': {
                'id': next_status['id'],
                'name': next_status['name'],
                'color': next_status['color']
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@customers_bp.route('/contacts/<int:contact_id>', methods=['GET'])
@login_required
def get_contact_details(contact_id):
    """
    Unified route that handles both AJAX requests (returns JSON) and regular requests (returns HTML)
    This maintains backwards compatibility while supporting the new contact management system
    """
    print(f"DEBUG: ROUTE HIT! get_contact_details called with contact_id: {contact_id}")
    print(f"DEBUG: Request method: {request.method}")
    print(f"DEBUG: Request path: {request.path}")
    print(f"DEBUG: Request URL: {request.url}")
    print(f"DEBUG: Accept header: {request.headers.get('Accept', 'Not provided')}")
    print(f"DEBUG: Referer: {request.headers.get('Referer', 'Not provided')}")

    # Better AJAX detection:
    # 1. Check if called from the customer edit page (where our new contact management lives)
    # 2. Check for typical AJAX headers
    referer = request.headers.get('Referer', '')
    is_from_customer_edit = '/customers/' in referer and '/edit' in referer

    is_ajax_request = (
            request.headers.get('Content-Type') == 'application/json' or
            'application/json' in request.headers.get('Accept', '') or
            request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
            is_from_customer_edit  # If called from customer edit page, treat as AJAX
    )

    print(f"DEBUG: Is from customer edit page: {is_from_customer_edit}")
    print(f"DEBUG: Is AJAX request: {is_ajax_request}")

    try:
        print(f"DEBUG: Getting contact details for ID: {contact_id}")
        contact = get_contact_by_id(contact_id)

        print(f"DEBUG: Contact found: {contact}")

        if not contact:
            print(f"DEBUG: No contact found with ID {contact_id}")

            if is_ajax_request:
                # Return JSON for AJAX requests
                response_data = {
                    'success': False,
                    'error': 'Contact not found'
                }
                print(f"DEBUG: Returning JSON 404 with data: {response_data}")
                return jsonify(response_data), 404
            else:
                # Return HTML error for regular requests
                flash('Contact not found', 'danger')
                return redirect(url_for('customers.customers'))

        # If this is an AJAX request, return JSON
        if is_ajax_request:
            response_data = {
                'success': True,
                'contact': contact
            }
            print(f"DEBUG: Returning JSON success with data: {response_data}")
            return jsonify(response_data)

        # Otherwise, return HTML (backwards compatibility)
        print(f"DEBUG: Returning HTML template for contact: {contact_id}")

        # For HTML requests, only get data that we know exists in your system
        with db_cursor() as cur:
            customers = _execute_with_cursor(
                cur,
                'SELECT id, name FROM customers ORDER BY name'
            ).fetchall()

        # Convert contact to dict for template compatibility
        contact_dict = dict(contact) if hasattr(contact, 'keys') else contact

        # Use the most basic approach - redirect to customer edit page with the contact info
        # This avoids assumptions about which templates exist
        flash(f'Viewing contact: {contact_dict.get("name", "Unknown")} ({contact_dict.get("email", "")})', 'info')
        return redirect(url_for('customers.edit_customer', customer_id=contact_dict['customer_id']))

    except Exception as e:
        print(f"DEBUG: Error getting contact: {str(e)}")
        import traceback
        traceback.print_exc()

        if is_ajax_request:
            # Return JSON error for AJAX requests
            error_response = {
                'success': False,
                'error': 'Failed to get contact'
            }
            print(f"DEBUG: Returning JSON 500 with data: {error_response}")
            return jsonify(error_response), 500
        else:
            # Return HTML error for regular requests
            flash('Error loading contact details', 'danger')
            return redirect(url_for('customers.customers'))

@customers_bp.route('/contacts/<int:contact_id>/update', methods=['POST'])
@login_required
def update_contact_details(contact_id):
    """Update contact via AJAX"""
    try:
        data = request.get_json()

        # Validate required fields
        if not data.get('name') or not data.get('email'):
            return jsonify({
                'success': False,
                'error': 'Name and email are required'
            })

        # Handle empty status_id
        status_id = data.get('status_id')
        if status_id == '' or status_id is None:
            status_id = 1  # Default to active

        # Get customer_id from the request
        customer_id = data.get('customer_id')

        # Update the contact (you'll need to modify this function)
        update_contact(
            contact_id=contact_id,
            name=data['name'],
            second_name=data.get('second_name', ''),
            email=data['email'],
            job_title=data.get('job_title', ''),
            phone=data.get('phone', ''),
            status_id=status_id,
            customer_id=customer_id,
            timezone=data.get('timezone', '')  # Add timezone parameter
        )

        return jsonify({
            'success': True,
            'message': 'Contact updated successfully'
        })

    except Exception as e:
        print(f"Error updating contact: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': 'Failed to update contact'
        }), 500

@customers_bp.route('/contacts/<int:contact_id>/delete', methods=['POST'])
@login_required
def delete_contact_route(contact_id):
    """Delete (deactivate) contact"""
    try:
        delete_contact(contact_id)

        return jsonify({
            'success': True,
            'message': 'Contact deleted successfully'
        })

    except Exception as e:
        print(f"Error deleting contact: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to delete contact'
        }), 500


@customers_bp.route('/contact_statuses', methods=['GET'])
@login_required
def get_contact_statuses_route():
    """Get all contact statuses"""
    try:
        import time
        t0 = time.perf_counter()
        statuses = get_all_contact_statuses()
        total = time.perf_counter() - t0
        print(f"TIMING customers.contact_statuses total={total:.3f}s")
        return jsonify({
            'success': True,
            'statuses': statuses
        })
    except Exception as e:
        print(f"Error getting contact statuses: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to get contact statuses'
        }), 500


# Add this route to your contacts blueprint (or appropriate blueprint)
@customers_bp.route('/search_contact')
@login_required
def contact_search():
    query = request.args.get('query', '')
    query = ' '.join((query or '').strip().split())
    if not query:
        return jsonify([])

    op = 'ILIKE' if _using_postgres() else 'LIKE'
    full_name_expr = "concat_ws(' ', c.name, c.second_name)" if _using_postgres() else "(c.name || ' ' || COALESCE(c.second_name, ''))"

    salesperson_id = request.args.get('salesperson_id', type=int) or current_user.get_salesperson_id()

    # Case-insensitive search (SQLite LIKE is case-insensitive for ASCII; Postgres LIKE is case-sensitive)
    with db_cursor() as cur:
        contacts = _execute_with_cursor(
            cur,
            f'''
        SELECT 
            c.id,
            c.name,
            c.second_name,
            c.email,
            c.phone,
            c.job_title,
            c.customer_id,
            c.timezone,
            cust.name as customer_name,
            cs.name as status_name,
            cs.color as status_color
        FROM contacts c
        LEFT JOIN customers cust ON c.customer_id = cust.id
        LEFT JOIN contact_statuses cs ON c.status_id = cs.id
        WHERE (
            -- Search in concatenated full name (handles spaces)
            {full_name_expr} {op} ?
            OR c.email {op} ?
            OR c.phone {op} ?
            OR c.job_title {op} ?
            OR cust.name {op} ?
        )
        ORDER BY c.name, c.second_name
        LIMIT 10
        ''',
        (
            f'%{query}%',
            f'%{query}%',
            f'%{query}%',
            f'%{query}%',
            f'%{query}%'
        )
    ).fetchall()

    call_list_contact_ids = set()
    if salesperson_id:
        call_list_contact_ids = get_call_list_contact_ids(salesperson_id)

    return jsonify([{
        'id': contact['id'],
        'name': contact['name'],
        'second_name': contact['second_name'],
        'full_name': f"{contact['name']} {contact['second_name'] or ''}".strip(),
        'email': contact['email'],
        'phone': contact['phone'],
        'job_title': contact['job_title'],
        'customer_id': contact['customer_id'],
        'customer_name': contact['customer_name'],
        'status_name': contact['status_name'],
        'status_color': contact['status_color'],
        'timezone': contact['timezone'] or 'UTC',
        'is_on_call_list': contact['id'] in call_list_contact_ids
    } for contact in contacts])

# Add this route to your customers blueprint to return JSON data for the modal

@customers_bp.route('/<int:customer_id>/development/api', methods=['GET'])
@login_required
def customer_development_api(customer_id):
    """API endpoint to get customer development plan as JSON"""

    # Check if customer exists and user has permission (reuse your existing logic)
    customer = get_customer_by_id(customer_id)
    if not customer:
        return jsonify({'success': False, 'error': 'Customer not found'}), 404

    # Convert customer to dictionary (reuse your existing logic)
    customer_dict = {}
    for key in customer.keys() if hasattr(customer, 'keys') else []:
        try:
            customer_dict[key] = customer[key]
        except Exception as e:
            print(f"Error accessing key {key}: {str(e)}")

    # Check permissions (reuse your existing permission logic)
    customer_salesperson_id = customer_dict.get('salesperson_id')
    user_salesperson_id = current_user.get_salesperson_id()

    can_view = (current_user.is_administrator() or
                current_user.can(Permission.VIEW_CUSTOMERS) or
                current_user.can(Permission.EDIT_CUSTOMERS) or
                (user_salesperson_id and user_salesperson_id == customer_salesperson_id))

    if not can_view:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403

    try:
        # Get development plan
        development_plan = get_customer_development_plan(customer_id)

        # Convert to list of dicts for JSON serialization
        plan_data = []
        for point in development_plan:
            plan_data.append({
                'point_id': point['point_id'],
                'question': point['question'],
                'description': point['description'],
                'order_index': point['order_index'],
                'answer': point['answer'],
                'answered_at': point['answered_at'],
                'updated_at': point['updated_at'],
                'answered_by': point['answered_by']
            })

        # Calculate progress
        total_points = len(plan_data)
        completed_points = sum(1 for point in plan_data if point['answer'] and point['answer'].strip())

        return jsonify({
            'success': True,
            'development_plan': plan_data,
            'progress': {
                'total': total_points,
                'completed': completed_points
            },
            'customer': {
                'id': customer_id,
                'name': customer_dict.get('name', f'Customer #{customer_id}')
            }
        })

    except Exception as e:
        print(f"Error getting development plan API: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to load development plan'
        }), 500


@customers_bp.route('/contacts/<int:contact_id>/communications')
@login_required
def get_contact_communications_api(contact_id):
    """API endpoint to get communications for a contact"""
    try:
        # Check if user has permission to view this contact
        with db_cursor() as cur:
            contact = _execute_with_cursor(
                cur,
                'SELECT customer_id FROM contacts WHERE id = ?',
                (contact_id,)
            ).fetchone()

        if not contact:
            return jsonify({'error': 'Contact not found'}), 404

        # Check permissions (similar to your edit_customer logic)
        customer = get_customer_by_id(contact['customer_id'])
        if customer:
            customer_salesperson_id = customer.get('salesperson_id')
            user_salesperson_id = current_user.get_salesperson_id()

            can_view = (current_user.is_administrator() or
                        current_user.can(Permission.VIEW_CUSTOMERS) or
                        (user_salesperson_id and user_salesperson_id == customer_salesperson_id))

            if not can_view:
                return jsonify({'error': 'Permission denied'}), 403

        # Get communications
        communications = get_contact_communications(contact_id)

        # Convert to simple dictionaries for JSON
        result = []
        for comm in communications:
            result.append({
                'id': comm.get('id'),
                'communication_type': comm.get('type', comm.get('communication_type', 'Other')),
                'communication_date': comm['date'].isoformat() if isinstance(comm.get('date'), datetime) else comm.get(
                    'date', ''),
                'notes': comm.get('notes', ''),
                'salesperson_name': comm.get('salesperson_name', '')
            })

        return jsonify(result)

    except Exception as e:
        print(f"Error getting communications: {str(e)}")
        return jsonify({'error': str(e)}), 500


from datetime import datetime
import pytz


@customers_bp.route('/timezones', methods=['GET'])
@login_required
def get_timezones():
    """Get all timezones grouped by region"""
    import time
    t0 = time.perf_counter()
    timezones = []
    for tz in pytz.all_timezones:
        try:
            timezone = pytz.timezone(tz)
            # Get current UTC offset
            now = datetime.now(timezone)
            offset = now.strftime('%z')
            offset_hours = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"

            timezones.append({
                'value': tz,
                'label': f"{tz.replace('_', ' ')} ({offset_hours})",
                'offset': offset
            })
        except:
            continue

    total = time.perf_counter() - t0
    print(f"TIMING customers.timezones total={total:.3f}s")
    return jsonify({'success': True, 'timezones': timezones})


@customers_bp.route('/timezone/suggest/<country_code>', methods=['GET'])
@login_required
def suggest_timezone(country_code):
    """Suggest timezone based on ISO country code"""

    # Comprehensive country to timezone mapping
    country_timezone_map = {
        # Europe
        'GB': 'Europe/London',
        'IE': 'Europe/Dublin',
        'FR': 'Europe/Paris',
        'DE': 'Europe/Berlin',
        'IT': 'Europe/Rome',
        'ES': 'Europe/Madrid',
        'PT': 'Europe/Lisbon',
        'NL': 'Europe/Amsterdam',
        'BE': 'Europe/Brussels',
        'CH': 'Europe/Zurich',
        'AT': 'Europe/Vienna',
        'SE': 'Europe/Stockholm',
        'NO': 'Europe/Oslo',
        'DK': 'Europe/Copenhagen',
        'FI': 'Europe/Helsinki',
        'PL': 'Europe/Warsaw',
        'CZ': 'Europe/Prague',
        'GR': 'Europe/Athens',
        'RO': 'Europe/Bucharest',
        'HU': 'Europe/Budapest',

        # North America
        'US': 'America/New_York',  # Eastern as most populous
        'CA': 'America/Toronto',  # Eastern as most populous
        'MX': 'America/Mexico_City',

        # Asia
        'CN': 'Asia/Shanghai',
        'JP': 'Asia/Tokyo',
        'KR': 'Asia/Seoul',
        'IN': 'Asia/Kolkata',
        'SG': 'Asia/Singapore',
        'HK': 'Asia/Hong_Kong',
        'TW': 'Asia/Taipei',
        'TH': 'Asia/Bangkok',
        'MY': 'Asia/Kuala_Lumpur',
        'ID': 'Asia/Jakarta',
        'PH': 'Asia/Manila',
        'VN': 'Asia/Ho_Chi_Minh',
        'PK': 'Asia/Karachi',
        'BD': 'Asia/Dhaka',
        'AE': 'Asia/Dubai',
        'SA': 'Asia/Riyadh',
        'IL': 'Asia/Jerusalem',
        'TR': 'Europe/Istanbul',

        # Oceania
        'AU': 'Australia/Sydney',  # Eastern as most populous
        'NZ': 'Pacific/Auckland',

        # South America
        'BR': 'America/Sao_Paulo',
        'AR': 'America/Argentina/Buenos_Aires',
        'CL': 'America/Santiago',
        'CO': 'America/Bogota',
        'PE': 'America/Lima',
        'VE': 'America/Caracas',

        # Africa
        'ZA': 'Africa/Johannesburg',
        'EG': 'Africa/Cairo',
        'NG': 'Africa/Lagos',
        'KE': 'Africa/Nairobi',
        'MA': 'Africa/Casablanca',
        'GH': 'Africa/Accra',

        # Middle East
        'QA': 'Asia/Qatar',
        'KW': 'Asia/Kuwait',
        'OM': 'Asia/Muscat',
        'BH': 'Asia/Bahrain',
        'JO': 'Asia/Amman',
        'LB': 'Asia/Beirut',

        # Additional European countries
        'RU': 'Europe/Moscow',
        'UA': 'Europe/Kiev',
        'BY': 'Europe/Minsk',
        'BG': 'Europe/Sofia',
        'HR': 'Europe/Zagreb',
        'SI': 'Europe/Ljubljana',
        'SK': 'Europe/Bratislava',
        'LT': 'Europe/Vilnius',
        'LV': 'Europe/Riga',
        'EE': 'Europe/Tallinn',
        'IS': 'Atlantic/Reykjavik',
        'LU': 'Europe/Luxembourg',
        'MT': 'Europe/Malta',
        'CY': 'Asia/Nicosia',
    }

    # Multi-timezone countries with major cities
    multi_timezone_info = {
        'US': {
            'suggested': 'America/New_York',
            'options': [
                'America/New_York',  # Eastern - NYC, Miami, Atlanta
                'America/Chicago',  # Central - Chicago, Houston, Dallas
                'America/Denver',  # Mountain - Denver, Phoenix
                'America/Los_Angeles',  # Pacific - LA, SF, Seattle
                'America/Anchorage',  # Alaska
                'Pacific/Honolulu'  # Hawaii
            ],
            'hint': 'US has multiple timezones. Eastern selected (NYC, Miami, Atlanta). Other options: Central (Chicago, Houston), Pacific (LA, SF, Seattle), Mountain (Denver, Phoenix)'
        },
        'CA': {
            'suggested': 'America/Toronto',
            'options': [
                'America/Toronto',  # Eastern - Toronto, Ottawa, Montreal
                'America/Winnipeg',  # Central - Winnipeg
                'America/Edmonton',  # Mountain - Calgary, Edmonton
                'America/Vancouver',  # Pacific - Vancouver, Victoria
                'America/St_Johns'  # Newfoundland
            ],
            'hint': 'Canada has multiple timezones. Eastern selected (Toronto, Montreal, Ottawa). Other options: Pacific (Vancouver), Central (Winnipeg), Mountain (Calgary, Edmonton)'
        },
        'AU': {
            'suggested': 'Australia/Sydney',
            'options': [
                'Australia/Sydney',  # NSW, ACT, VIC, TAS
                'Australia/Brisbane',  # QLD
                'Australia/Adelaide',  # SA
                'Australia/Perth',  # WA
                'Australia/Darwin'  # NT
            ],
            'hint': 'Australia has multiple timezones. Eastern selected (Sydney, Melbourne, Canberra). Other options: Brisbane (QLD), Perth (WA), Adelaide (SA)'
        },
        'BR': {
            'suggested': 'America/Sao_Paulo',
            'options': [
                'America/Sao_Paulo',  # São Paulo, Rio
                'America/Manaus',  # Amazonas
                'America/Recife',  # Northeast
                'America/Noronha'  # Fernando de Noronha
            ],
            'hint': 'Brazil has multiple timezones. Brasília time selected (São Paulo, Rio). Other options available for different regions'
        },
        'RU': {
            'suggested': 'Europe/Moscow',
            'options': [
                'Europe/Moscow',
                'Asia/Yekaterinburg',
                'Asia/Novosibirsk',
                'Asia/Vladivostok'
            ],
            'hint': 'Russia has multiple timezones. Moscow time selected. Other options available for different regions'
        },
        'MX': {
            'suggested': 'America/Mexico_City',
            'options': [
                'America/Mexico_City',  # Central - Mexico City
                'America/Tijuana',  # Pacific - Tijuana
                'America/Cancun'  # Eastern - Cancún
            ],
            'hint': 'Mexico has multiple timezones. Central selected (Mexico City). Other options: Pacific (Tijuana), Eastern (Cancún)'
        }
    }

    country_code = country_code.upper()

    # Check if country has multiple timezones
    if country_code in multi_timezone_info:
        info = multi_timezone_info[country_code]
        return jsonify({
            'success': True,
            'timezone': info['suggested'],
            'options': info['options'],
            'hint': info['hint'],
            'multiple': True
        })

    # Single timezone country
    timezone = country_timezone_map.get(country_code, 'UTC')

    return jsonify({
        'success': True,
        'timezone': timezone,
        'multiple': False,
        'hint': f'Suggested based on country: {country_code}'
    })


# Add these routes to your customers blueprint

@customers_bp.route('/<int:customer_id>/associations/api', methods=['POST'])
@login_required
def add_customer_association_api(customer_id):
    """API endpoint to add a new association"""
    try:
        customer = get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Customer not found'}), 404

        data = request.get_json()
        associated_customer_id = data.get('associated_customer_id')
        notes = data.get('notes', '')

        if not associated_customer_id:
            return jsonify({'success': False, 'error': 'Associated customer ID is required'}), 400

        # Validate the associated customer exists
        associated_customer = get_customer_by_id(associated_customer_id)
        if not associated_customer:
            return jsonify({'success': False, 'error': 'Associated customer not found'}), 404

        # Check if association already exists
        existing = db_execute(
            "SELECT id FROM customer_associations WHERE main_customer_id = ? AND associated_customer_id = ?",
            (customer_id, associated_customer_id),
            fetch='one'
        )

        if existing:
            return jsonify({'success': False, 'error': 'This association already exists'}), 400

        # Prevent self-association
        if customer_id == associated_customer_id:
            return jsonify({'success': False, 'error': 'Cannot associate a customer with itself'}), 400

        # Create the association
        created_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        db_execute(
            """
            INSERT INTO customer_associations (main_customer_id, associated_customer_id, notes, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (customer_id, associated_customer_id, notes, created_at),
            commit=True
        )

        created_association = db_execute(
            """
            SELECT 
                ca.id,
                ca.associated_customer_id,
                ca.created_at,
                ca.notes,
                c.name as associated_customer_name
            FROM customer_associations ca
            LEFT JOIN customers c ON ca.associated_customer_id = c.id
            WHERE ca.main_customer_id = ? AND ca.associated_customer_id = ?
            ORDER BY ca.id DESC
            LIMIT 1
            """,
            (customer_id, associated_customer_id),
            fetch='one'
        )

        return jsonify({
            'success': True,
            'message': 'Association added successfully',
            'association': {
                'id': created_association['id'],
                'associated_customer_id': created_association['associated_customer_id'],
                'associated_customer_name': created_association['associated_customer_name'],
                'created_at': created_association['created_at'],
                'notes': created_association['notes'] or ''
            }
        })

    except Exception as e:
        print(f"ERROR: Error in add_customer_association_api: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/<int:customer_id>/associations/<int:association_id>/api', methods=['DELETE'])
@login_required
def delete_customer_association_api(customer_id, association_id):
    """API endpoint to delete an association"""
    try:
        customer = get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Customer not found'}), 404

        association = db_execute(
            "SELECT id FROM customer_associations WHERE id = ? AND main_customer_id = ?",
            (association_id, customer_id),
            fetch='one'
        )

        if not association:
            return jsonify({'success': False, 'error': 'Association not found'}), 404

        # Delete the association
        db_execute("DELETE FROM customer_associations WHERE id = ?", (association_id,), commit=True)

        return jsonify({
            'success': True,
            'message': 'Association deleted successfully'
        })

    except Exception as e:
        print(f"ERROR: Error in delete_customer_association_api: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/<int:customer_id>/associations/<int:association_id>/api', methods=['PUT'])
@login_required
def update_customer_association_api(customer_id, association_id):
    """API endpoint to update an association's notes"""
    try:
        customer = get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Customer not found'}), 404

        data = request.get_json()
        notes = data.get('notes', '')

        association = db_execute(
            "SELECT id FROM customer_associations WHERE id = ? AND main_customer_id = ?",
            (association_id, customer_id),
            fetch='one'
        )

        if not association:
            return jsonify({'success': False, 'error': 'Association not found'}), 404

        # Update the association
        db_execute(
            "UPDATE customer_associations SET notes = ? WHERE id = ?",
            (notes, association_id),
            commit=True
        )

        # Get updated association
        updated = db_execute(
            """
            SELECT 
                ca.id,
                ca.associated_customer_id,
                ca.created_at,
                ca.notes,
                c.name as associated_customer_name
            FROM customer_associations ca
            LEFT JOIN customers c ON ca.associated_customer_id = c.id
            WHERE ca.id = ?
            """,
            (association_id,),
            fetch='one'
        )

        return jsonify({
            'success': True,
            'message': 'Association updated successfully',
            'association': {
                'id': updated['id'],
                'associated_customer_id': updated['associated_customer_id'],
                'associated_customer_name': updated['associated_customer_name'],
                'created_at': updated['created_at'],
                'notes': updated['notes'] or ''
            }
        })

    except Exception as e:
        print(f"ERROR: Error in update_customer_association_api: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/<int:customer_id>/associations/api', methods=['GET'])
@login_required
def get_customer_associations_api(customer_id):
    """API endpoint to get associations for a customer"""
    try:
        start_time = time.perf_counter()
        customer = get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Customer not found'}), 404

        # Get associated customers (this customer is the PARENT - these are subsidiaries)
        query = """
            SELECT 
                ca.id,
                ca.associated_customer_id,
                ca.created_at,
                ca.notes,
                c.name as associated_customer_name
            FROM customer_associations ca
            LEFT JOIN customers c ON ca.associated_customer_id = c.id
            WHERE ca.main_customer_id = ?
            ORDER BY c.name
        """
        print("CUSTOMER ASSOCIATIONS (subsidiaries):", query, (customer_id,), flush=True)
        associations = db_execute(query, (customer_id,), fetch='all')

        # Get parent companies (where this customer is the SUBSIDIARY)
        parent_query = """
            SELECT 
                ca.id,
                ca.main_customer_id as parent_customer_id,
                ca.created_at,
                ca.notes,
                c.name as parent_customer_name
            FROM customer_associations ca
            LEFT JOIN customers c ON ca.main_customer_id = c.id
            WHERE ca.associated_customer_id = ?
            ORDER BY c.name
        """
        print("CUSTOMER ASSOCIATIONS (parents):", parent_query, (customer_id,), flush=True)
        parents = db_execute(parent_query, (customer_id,), fetch='all')
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        print(
            f"CUSTOMER ASSOCIATIONS timing: customer_id={customer_id} "
            f"subsidiaries={len(associations)} parents={len(parents)} "
            f"elapsed_ms={elapsed_ms:.2f}",
            flush=True
        )

        associations_data = []
        for assoc in associations:
            associations_data.append({
                'id': assoc['id'],
                'associated_customer_id': assoc['associated_customer_id'],
                'associated_customer_name': assoc['associated_customer_name'],
                'created_at': assoc['created_at'],
                'notes': assoc['notes'] or ''
            })

        parents_data = []
        for parent in parents:
            parents_data.append({
                'id': parent['id'],
                'parent_customer_id': parent['parent_customer_id'],
                'parent_customer_name': parent['parent_customer_name'],
                'created_at': parent['created_at'],
                'notes': parent['notes'] or ''
            })

        response = jsonify({
            'success': True,
            'associations': associations_data,
            'parents': parents_data,
            'main_customer_id': customer_id,
            'main_customer_name': customer['name'],
            'has_subsidiaries': len(associations_data) > 0,
            'has_parent': len(parents_data) > 0
        })
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        return response

    except Exception as e:
        print(f"ERROR: Error in get_customer_associations_api: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/associations/bulk', methods=['GET'])
@login_required
def get_customer_associations_bulk():
    """Return association counts for a list of customer IDs."""
    try:
        raw_ids = request.args.get('ids', '')
        if not raw_ids:
            return jsonify({})

        ids = []
        for item in raw_ids.split(','):
            item = item.strip()
            if not item:
                continue
            try:
                ids.append(int(item))
            except ValueError:
                continue

        if not ids:
            return jsonify({})

        # Cap to keep queries bounded for the quick search UI.
        ids = ids[:50]

        placeholders = ','.join('?' for _ in ids)
        sub_query = f"""
            SELECT main_customer_id, COUNT(*) as assoc_count
            FROM customer_associations
            WHERE main_customer_id IN ({placeholders})
            GROUP BY main_customer_id
        """
        parent_query = f"""
            SELECT associated_customer_id, COUNT(*) as parent_count
            FROM customer_associations
            WHERE associated_customer_id IN ({placeholders})
            GROUP BY associated_customer_id
        """

        sub_rows = db_execute(sub_query, ids, fetch='all') or []
        parent_rows = db_execute(parent_query, ids, fetch='all') or []

        result = {cid: {'associations_count': 0, 'parents_count': 0} for cid in ids}
        for row in sub_rows:
            result[row['main_customer_id']]['associations_count'] = row['assoc_count']
        for row in parent_rows:
            result[row['associated_customer_id']]['parents_count'] = row['parent_count']

        return jsonify(result)
    except Exception as e:
        print(f"ERROR: Error in get_customer_associations_bulk: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/<int:customer_id>/overview', methods=['GET'])
@login_required
def customer_overview(customer_id):
    """
    API endpoint for customer overview modal
    Returns summary business data including:
    - Last purchase info
    - Lifetime value
    - Average order value
    - Last contact
    - Yearly spending with monthly breakdown
    - Contacts
    - Recent activity (updates and orders)
    """
    try:
        salesperson_id = request.args.get('salesperson_id', type=int)
        if not salesperson_id and current_user.is_authenticated:
            salesperson_id = current_user.get_salesperson_id()
        # Check permissions
        customer = get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Customer not found'}), 404

        # Convert to dict safely
        customer_dict = {}
        for key in customer.keys() if hasattr(customer, 'keys') else []:
            try:
                customer_dict[key] = customer[key]
            except Exception as e:
                print(f"Error accessing key {key}: {str(e)}")

        # Check if user has permission to view this customer
        customer_salesperson_id = customer_dict.get('salesperson_id')
        user_salesperson_id = current_user.get_salesperson_id()

        can_view = (current_user.is_administrator() or
                    current_user.can(Permission.VIEW_CUSTOMERS) or
                    current_user.can(Permission.EDIT_CUSTOMERS) or
                    (user_salesperson_id and user_salesperson_id == customer_salesperson_id))

        if not can_view:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        five_years_ago = datetime.utcnow() - timedelta(days=5 * 365)
        recent_activity = []
        try:
            with db_cursor() as cur:
                last_purchase = _execute_with_cursor(
                    cur,
                    """
                    SELECT
                        date_entered as date,
                        total_value as value
                    FROM sales_orders
                    WHERE customer_id = ?
                    ORDER BY date_entered DESC
                    LIMIT 1
                    """,
                    (customer_id,)
                ).fetchone()

                lifetime_stats = _execute_with_cursor(
                    cur,
                    """
                    SELECT 
                        COUNT(*) as total_orders,
                        COALESCE(SUM(total_value), 0) as lifetime_value,
                        COALESCE(AVG(total_value), 0) as avg_order_value
                    FROM sales_orders
                    WHERE customer_id = ?
                    """,
                    (customer_id,)
                ).fetchone()

                yearly_rows = _execute_with_cursor(
                    cur,
                    """
                    SELECT 
                        date_entered,
                        total_value
                    FROM sales_orders
                    WHERE customer_id = ? AND date_entered >= ?
                    ORDER BY date_entered ASC
                    """,
                    (customer_id, five_years_ago.strftime('%Y-%m-%d'))
                ).fetchall()

                last_contact = _execute_with_cursor(
                    cur,
                    """
                    SELECT 
                        date,
                        communication_type as type
                    FROM customer_updates
                    WHERE customer_id = ?
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    (customer_id,)
                ).fetchone()

                contacts = _execute_with_cursor(
                    cur,
                    """
                    SELECT 
                        id,
                        name,
                        email,
                        phone
                    FROM contacts
                    WHERE customer_id = ?
                    ORDER BY name ASC
                    """,
                    (customer_id,)
                ).fetchall()

                recent_updates = _execute_with_cursor(
                    cur,
                    """
                    SELECT 
                        date,
                        update_text as description,
                        communication_type
                    FROM customer_updates
                        WHERE customer_id = ?
                    ORDER BY date DESC
                    LIMIT 3
                    """,
                    (customer_id,)
                ).fetchall()

                recent_orders = _execute_with_cursor(
                    cur,
                    """
                    SELECT 
                        date_entered as date,
                        sales_order_ref as reference,
                        total_value
                    FROM sales_orders
                    WHERE customer_id = ?
                    ORDER BY date_entered DESC
                    LIMIT 2
                    """,
                    (customer_id,)
                ).fetchall()

        except Exception as exc:
            logger.exception(exc)
            return jsonify({'success': False, 'error': str(exc)}), 500

        yearly_spend_dict = {}
        for row in yearly_rows:
            dt = _parse_datetime(row.get('date_entered'))
            if not dt:
                continue
            year = str(dt.year)
            month_idx = dt.month - 1
            if year not in yearly_spend_dict:
                yearly_spend_dict[year] = {
                    'year': year,
                    'total': 0.0,
                    'monthly_breakdown': [0.0] * 12
                }
            amount = float(row.get('total_value') or 0)
            yearly_spend_dict[year]['total'] += amount
            yearly_spend_dict[year]['monthly_breakdown'][month_idx] = amount

        yearly_spend = sorted(yearly_spend_dict.values(), key=lambda item: int(item['year']))

        for update in recent_updates:
            activity_type = update['communication_type'] if update['communication_type'] else 'update'
            description_text = update.get('description') or ''
            shortened = (description_text[:100] + '...') if len(description_text) > 100 else description_text
            recent_activity.append({
                'date': update['date'],
                'type': activity_type,
                'description': shortened,
                'details': None
            })

        for order in recent_orders:
            total_value = float(order.get('total_value') or 0)
            recent_activity.append({
                'date': order['date'],
                'type': 'order',
                'description': f"Order {order['reference']}",
                'details': f"Value: ${total_value:,.2f}"
            })

        for entry in recent_activity:
            entry['_sort_date'] = _parse_datetime(entry['date']) or datetime.min

        recent_activity.sort(key=lambda x: x['_sort_date'], reverse=True)
        for entry in recent_activity:
            entry.pop('_sort_date', None)

        recent_activity = recent_activity[:5]

        call_list_contact_ids = set()
        call_list_snoozed_contact_ids = set()
        if salesperson_id and contacts:
            contact_ids = [contact['id'] for contact in contacts]
            placeholders = ','.join('?' for _ in contact_ids)
            call_list_fields = "contact_id"
            if _call_list_has_snoozed_until():
                call_list_fields += ", snoozed_until"

            call_list_rows = db_execute(
                f"""
                SELECT {call_list_fields}
                FROM call_list
                WHERE salesperson_id = ?
                  AND is_active = TRUE
                  AND contact_id IN ({placeholders})
                """,
                [salesperson_id] + contact_ids,
                fetch='all'
            ) or []

            for row in call_list_rows:
                contact_id = row['contact_id']
                call_list_contact_ids.add(contact_id)
                if row.get('snoozed_until'):
                    snoozed_until = _parse_datetime(row['snoozed_until'])
                    if snoozed_until and snoozed_until > datetime.utcnow():
                        call_list_snoozed_contact_ids.add(contact_id)

        overview_data = {
            'customer_name': customer_dict.get('name', 'Unknown'),
            'last_purchase': {
                'date': last_purchase['date'] if last_purchase else None,
                'value': float(last_purchase['value']) if last_purchase and last_purchase['value'] else 0
            } if last_purchase else None,
            'lifetime_value': float(lifetime_stats['lifetime_value']) if lifetime_stats else 0,
            'total_orders': int(lifetime_stats['total_orders']) if lifetime_stats else 0,
            'avg_order_value': float(lifetime_stats['avg_order_value']) if lifetime_stats else 0,
            'last_contact': {
                'date': last_contact['date'] if last_contact else None,
                'type': last_contact['type'] if last_contact else None
            } if last_contact else None,
            'yearly_spend': yearly_spend,
            'contacts': [
                {
                    'id': contact['id'],
                    'name': contact['name'],
                    'email': contact['email'],
                    'phone': contact['phone'],
                    'is_on_call_list': contact['id'] in call_list_contact_ids,
                    'is_snoozed': contact['id'] in call_list_snoozed_contact_ids
                } for contact in contacts
            ],
            'recent_activity': recent_activity
        }

        return jsonify({
            'success': True,
            'data': overview_data
        })

    except Exception as e:
        logger.exception(e)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# =============================================================================
# Company Type Management API
# =============================================================================

@customers_bp.route('/api/company-types', methods=['GET'])
@login_required
def get_company_types_api():
    """Get all available company types"""
    try:
        types = get_all_company_types()
        return jsonify({
            'success': True,
            'types': [{'id': t['id'], 'type': t['name'], 'description': t.get('description')} for t in types]
        })
    except Exception as e:
        logger.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/<int:customer_id>/company-type/<int:type_id>', methods=['POST'])
@login_required
def add_customer_company_type(customer_id, type_id):
    """Add a company type to a customer"""
    try:
        insert_customer_company_type(customer_id, type_id)
        return jsonify({'success': True})
    except Exception as e:
        logger.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/<int:customer_id>/company-type/<int:type_id>', methods=['DELETE'])
@login_required
def delete_customer_company_type(customer_id, type_id):
    """Remove a company type from a customer"""
    try:
        remove_customer_company_type(customer_id, type_id)
        return jsonify({'success': True})
    except Exception as e:
        logger.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@customers_bp.route('/<int:customer_id>/enrich-single', methods=['POST'])
@login_required
def enrich_single_customer(customer_id):
    """Enrich a single customer using Perplexity AI"""
    try:
        # Get customer data
        customer = get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Customer not found'}), 404

        customer_dict = dict(customer)

        # Get available tags and company types
        tags_rows = db_execute('SELECT id, tag as name, description FROM industry_tags', fetch='all') or []
        tags = [dict(row) for row in tags_rows]

        types_rows = db_execute('SELECT id, type as name FROM company_types', fetch='all') or []
        company_types = [dict(row) for row in types_rows]

        # Run Perplexity enrichment
        enrichment_data = enrich_customer_with_perplexity(customer_dict, tags, company_types)

        # Apply the enrichment
        apply_perplexity_enrichment(customer_id, enrichment_data)

        # Build response with enrichment summary
        response_data = {
            'success': True,
            'enrichment': {
                'estimated_revenue': enrichment_data.get('estimated_revenue'),
                'fleet_size': enrichment_data.get('fleet_size'),
                'mro_score': enrichment_data.get('mro_score'),
                'country_code': enrichment_data.get('country_code'),
                'company_types': [],
                'summary': enrichment_data.get('summary', '')
            }
        }

        # Get company type names for the response
        if enrichment_data.get('matched_company_type_ids'):
            type_names = [ct['name'] for ct in company_types
                         if ct['id'] in enrichment_data['matched_company_type_ids']]
            response_data['enrichment']['company_types'] = type_names

        return jsonify(response_data)

    except Exception as e:
        logger.exception(e)
        return jsonify({'success': False, 'error': str(e)}), 500
