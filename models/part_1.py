import json
import logging
import time
import re
import requests
from flask import current_app, g, render_template, abort
from db import CURRENCY_RATE_COLUMN, get_db_connection, execute as db_execute, db_cursor
from sqlalchemy import Column, String, Integer, Float, ForeignKey, create_engine, inspect, MetaData, Date, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import date, datetime
from collections import Counter
from typing import List, Dict, Tuple, Optional, Any
import pdfkit
import datetime
import os
from werkzeug.utils import secure_filename
from datetime import datetime
from routes.upload import parse_email
import extract_msg
from flask_login import UserMixin, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from models import *



import os as _os
DATABASE_URL = _os.getenv('DATABASE_URL')

# Set up an engine for SQLAlchemy to use for reflection
# Default to SQLite if DATABASE_URL is not set
if not DATABASE_URL:
    DATABASE_URL = 'sqlite:///database.db'
engine = create_engine(DATABASE_URL)
metadata = MetaData()

# PostgreSQL compatibility helper

def _get_returning_id(cursor, result=None):
    """Get last inserted ID - works with both SQLite (lastrowid) and Postgres (RETURNING)"""
    if result and isinstance(result, dict) and 'id' in result:
        return result['id']
    if hasattr(cursor, 'lastrowid'):
        return cursor.lastrowid
    return None


def get_dynamic_mapping_fields():
    # Initialize mapping_fields with the "ignore" option
    mapping_fields = {'ignore': [{'value': 'ignore', 'label': 'Ignore Column'}]}

    # Use inspector to retrieve tables and columns dynamically
    inspector = inspect(engine)
    for table_name in inspector.get_table_names():
        # Fetch columns for each table
        columns = inspector.get_columns(table_name)
        # Populate mapping_fields with table and columns
        mapping_fields[table_name] = [
            {'value': column['name'], 'label': column['name'].replace('_', ' ').title()}
            for column in columns
        ]

    return mapping_fields

Base = declarative_base()
def get_db():
    # Use the shared connection factory so Postgres works too.
    return get_db_connection()

def dict_from_row(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return dict(zip(row.keys(), row))


def _first_value_from_row(row):
    if row is None:
        return None
    row_dict = dict(row)
    return next(iter(row_dict.values()))


def _as_dict(row):
    if row is None:
        return None
    return dict(row)


def query_all(query: str, params=None):
    rows = db_execute(query, params or [], fetch='all')
    return rows or []


def query_one(query: str, params=None):
    return db_execute(query, params or [], fetch='one')


def _using_postgres():
    url = os.getenv('DATABASE_URL', '')
    return url.startswith(('postgres://', 'postgresql://'))


def _prepare_placeholders(query: str) -> str:
    if _using_postgres():
        return query.replace('?', '%s')
    return query


def _execute_with_cursor(cur, query: str, params=None):
    cur.execute(_prepare_placeholders(query), params or [])
    return cur


def get_table_columns(table_name: str):
    """
    Cross-database helper to fetch column names for a table.
    Uses PRAGMA on SQLite and information_schema on Postgres.
    """
    if _using_postgres():
        rows = db_execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
            fetch='all',
        )
        return [row['column_name'] if isinstance(row, dict) else row[0] for row in rows or []]

    rows = db_execute(f"PRAGMA table_info({table_name})", fetch='all')
    return [row['name'] if hasattr(row, 'keys') else row[1] for row in rows or []]

# Customer related functions
def get_customers():
    customers = query_all('SELECT * FROM customers')
    return [dict(customer) for customer in customers]


def insert_customer(name, notes=None, estimated_revenue=None, primary_contact_id=None, salesperson_id=None,
                   payment_terms='Pro-forma', incoterms='EXW', status_id=1, country=None, apollo_id=None,
                   website=None, logo_url=None):
    row = db_execute(
        '''INSERT INTO customers 
           (name, notes, estimated_revenue, primary_contact_id, salesperson_id, 
            payment_terms, incoterms, status_id, country, apollo_id, website, logo_url) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           RETURNING id''',
        (name, notes, estimated_revenue, primary_contact_id, salesperson_id,
         payment_terms, incoterms, status_id, country, apollo_id, website, logo_url),
        fetch='one',
        commit=True,
    )
    if row is None:
        raise RuntimeError("Failed to insert customer")
    return row.get('id', list(row.values())[0])

def update_customer(customer_id, name, primary_contact_id, salesperson_id, payment_terms, incoterms, watch, website, notes=None, country=None, system_code=None):
    db_execute(
        """
        UPDATE customers 
        SET name = ?, 
            primary_contact_id = ?, 
            salesperson_id = ?, 
            payment_terms = ?, 
            incoterms = ?,
            watch = ?,
            website = ?,
            notes = ?,
            country = ?,
            system_code = ?
        WHERE id = ?
        """,
        (name, primary_contact_id, salesperson_id, payment_terms, incoterms, watch, website, notes, country, system_code, customer_id),
        commit=True,
    )

# Contact related functions
def get_contacts():
    contacts = query_all('SELECT * FROM contacts')
    return [dict(contact) for contact in contacts]


def get_contacts_by_customer(customer_id, limit=None, offset=None):
    print("=" * 50)
    print(f"GET_CONTACTS_BY_CUSTOMER CALLED WITH ID: {customer_id}")
    print("=" * 50)

    column_names = get_table_columns('contacts')

    print(f"DEBUG: Available columns: {column_names}")
    print(f"DEBUG: 'status_id' in columns: {'status_id' in column_names}")

    # Construct query with only existing columns and join with contact_statuses
    base_query = """
        SELECT c.id, c.name, c.second_name, c.email, c.customer_id
    """

    if "job_title" in column_names:
        base_query += ", c.job_title"
    if "phone" in column_names:
        base_query += ", c.phone"
    if "status_id" in column_names:
        base_query += ", c.status_id"
    if "notes" in column_names:
        base_query += ", c.notes"
    if "timezone" in column_names:
        base_query += ", c.timezone"
    if "updated_at" in column_names:
        base_query += ", c.updated_at"

    # Add status information from contact_statuses table
    base_query += """
        , cs.name as status_name, cs.color as status_color
        FROM contacts c
        LEFT JOIN contact_statuses cs ON c.status_id = cs.id
        WHERE c.customer_id = ?
    """

    params = [customer_id]

    if limit is not None and offset is not None:
        base_query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

    print(f"DEBUG: Final query: {base_query}")

    contacts = query_all(base_query, params)
    print(f"DEBUG: Found {len(contacts)} contacts")

    contact_list = []
    for contact in contacts:
        contact_dict = dict(contact)
        print(f"DEBUG: Raw contact dict keys: {list(contact_dict.keys())}")
        print(f"DEBUG: Raw contact dict: {contact_dict}")

        # Ensure missing fields default to empty values
        contact_dict['second_name'] = contact_dict.get('second_name', '')
        contact_dict['job_title'] = contact_dict.get('job_title', '')
        contact_dict['phone'] = contact_dict.get('phone', '')
        contact_dict['status_id'] = contact_dict.get('status_id', 1)
        contact_dict['notes'] = contact_dict.get('notes', '')
        contact_dict['timezone'] = contact_dict.get('timezone', 'UTC')

        # Handle status information
        contact_dict['status_name'] = contact_dict.get('status_name', 'Active')
        contact_dict['status_color'] = contact_dict.get('status_color', '#28a745')

        # If no status found, set defaults
        if not contact_dict['status_name']:
            contact_dict['status_name'] = 'Active'
            contact_dict['status_color'] = '#28a745'

        contact_list.append(contact_dict)

    return contact_list

def get_contact_by_id(contact_id):
    print(f"DEBUG: get_contact_by_id called with ID: {contact_id}")

    query = """
        SELECT c.id, c.name, c.second_name, c.email, c.job_title, c.customer_id, c.notes, c.phone, c.status_id, c.timezone,
               cu.name as customer_name, cs.name as status_name, cs.color as status_color
        FROM contacts c
        LEFT JOIN customers cu ON c.customer_id = cu.id
        LEFT JOIN contact_statuses cs ON c.status_id = cs.id
        WHERE c.id = ?
    """

    print(f"DEBUG: Executing query: {query}")
    print(f"DEBUG: With contact_id: {contact_id}")

    try:
        result = query_one(query, (contact_id,))

        print(f"DEBUG: Raw query result: {result}")

        if result:
            if hasattr(result, 'keys'):
                contact_dict = dict(result)
            else:
                contact_dict = {
                    'id': result[0],
                    'name': result[1],
                    'second_name': result[2] or '',
                    'email': result[3],
                    'job_title': result[4] or '',
                    'customer_id': result[5],
                    'notes': result[6] or '',
                    'phone': result[7] or '',
                    'status_id': result[8] or 1,
                    'timezone': result[9] or 'UTC',
                    'customer_name': result[10],
                    'status_name': result[11] or 'Active',
                    'status_color': result[12] or '#28a745'
                }

            print(f"DEBUG: Final contact_dict: {contact_dict}")
            return contact_dict
        else:
            print(f"DEBUG: No result found for contact_id {contact_id}")
            return None
    except Exception as e:
        print(f"DEBUG: Database error in get_contact_by_id: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def insert_contact(customer_id, name, email, job_title=None, second_name=None):
    """
    Insert a new contact into the database and update customer_domains.

    Args:
        customer_id: ID of the associated customer (can be None)
        name: First name of the contact
        email: Email address of the contact
        job_title: Optional job title
        second_name: Optional second name/surname
    """
    row = db_execute(
        'INSERT INTO contacts (customer_id, name, second_name, email, job_title) VALUES (?, ?, ?, ?, ?) RETURNING id',
        (customer_id, name, second_name, email, job_title),
        fetch='one',
        commit=True,
    )
    if row is None:
        raise RuntimeError("Failed to insert contact")
    contact_id = row.get('id', list(row.values())[0])

    # Extract domain from email and add to customer_domains if it doesn't exist
    if email and '@' in email and customer_id:
        domain = email.split('@')[-1].lower()

        existing_domain = query_one(
            'SELECT id FROM customer_domains WHERE customer_id = ? AND domain = ?',
            (customer_id, domain),
        )

        if not existing_domain:
            db_execute(
                'INSERT INTO customer_domains (customer_id, domain) VALUES (?, ?)',
                (customer_id, domain),
                commit=True,
            )

    return contact_id

# RFQ related functions
def get_rfqs():
    rfqs = query_all(
        '''
        SELECT r.*, c.name AS customer_name, s.name AS salesperson_name, s.id AS salesperson_id,
               CASE WHEN EXISTS (
                   SELECT 1 
                   FROM rfq_lines rl
                   JOIN offer_lines ol ON rl.base_part_number = ol.base_part_number
                   WHERE rl.rfq_id = r.id
               ) THEN 1 ELSE 0 END AS has_offers
        FROM rfqs r
        JOIN customers c ON r.customer_id = c.id
        LEFT JOIN salespeople s ON r.salesperson_id = s.id
        '''
    )
    return [dict(row) for row in rfqs]


def get_rfq_by_id(rfq_id):
    print(f"get_rfq_by_id called with rfq_id: {rfq_id}")  # Debug print

    rfq = query_one(
        '''
        SELECT r.*, c.name AS customer_name, s.name AS salesperson_name, s.id AS salesperson_id
        FROM rfqs r
        JOIN customers c ON r.customer_id = c.id
        LEFT JOIN salespeople s ON r.salesperson_id = s.id
        WHERE r.id = ?
        ''',
        (rfq_id,),
    )

    result = dict(rfq) if rfq else None
    print(f"get_rfq_by_id result: {result}")  # Debug print
    return result

def insert_rfq(entered_date, customer_id, customer_ref, salesperson_id=None):
    logging.info(f"Inserting new RFQ: {entered_date}, {customer_id}, {customer_ref}, {salesperson_id}")
    if salesperson_id is None:
        customer = get_customer_by_id(customer_id)
        if customer:
            salesperson_id = customer.get('salesperson_id')
    row = db_execute(
        'INSERT INTO rfqs (entered_date, customer_id, customer_ref, salesperson_id) VALUES (?, ?, ?, ?) RETURNING id',
        (entered_date, customer_id, customer_ref, salesperson_id),
        fetch='one',
        commit=True,
    )
    if row is None:
        raise RuntimeError("Failed to insert RFQ")
    rfq_id = row.get('id', list(row.values())[0])
    logging.info(f"Inserted new RFQ with ID: {rfq_id}")
    return rfq_id

def update_rfq(rfq_id, entered_date, customer_id, contact_id, customer_ref, status, currency, salesperson_id):
    db_execute(
        'UPDATE rfqs SET entered_date = ?, customer_id = ?, contact_id = ?, customer_ref = ?, status = ?, currency = ?, salesperson_id = ? WHERE id = ?',
        (entered_date, customer_id, contact_id, customer_ref, status, currency, salesperson_id, rfq_id),
        commit=True,
    )


def delete_rfq(rfq_id):
    try:
        with db_cursor(commit=True) as cur:
            cur.execute('UPDATE rfqs SET status = ? WHERE id = ?', ('deleted', rfq_id))
            cur.execute('UPDATE rfq_lines SET status_id = 8 WHERE rfq_id = ?', (rfq_id,))
    except Exception as e:
        print(f"An error occurred: {e}")


def get_rfq_lines(rfq_id):
    query = '''
SELECT 
    rl.*, 
    r.customer_ref,
    r.status as rfq_status,
    c.name as customer_name,
    ol.offer_id AS actual_offer_id,
    o.supplier_id AS chosen_supplier,
    s.name AS chosen_supplier_name, 
    s.currency AS chosen_supplier_currency,
    s.fornitore,
    pn.part_number,
    pn.system_part_number,
    pn.stock,
    f.id AS file_id,
    f.filepath,
    m.name as manufacturer_name,  -- Add manufacturer name
    CASE WHEN pli.id IS NOT NULL THEN 1 ELSE 0 END as has_price_list_item
FROM rfq_lines rl
JOIN rfqs r ON rl.rfq_id = r.id
JOIN customers c ON r.customer_id = c.id
LEFT JOIN offer_lines ol ON rl.offer_id = ol.id
LEFT JOIN offers o ON ol.offer_id = o.id
LEFT JOIN suppliers s ON o.supplier_id = s.id
LEFT JOIN part_numbers pn ON rl.base_part_number = pn.base_part_number
LEFT JOIN offer_files of ON o.id = of.offer_id
LEFT JOIN files f ON of.file_id = f.id
LEFT JOIN manufacturers m ON rl.manufacturer_id = m.id  -- Add join for manufacturers
LEFT JOIN requisitions req ON rl.id = req.rfq_line_id
LEFT JOIN suppliers req_s ON req.supplier_id = req_s.id
LEFT JOIN price_list_items pli ON rl.base_part_number = pli.base_part_number
WHERE rl.rfq_id = ?
GROUP BY rl.id, pn.stock;
'''
    raw_rows = db_execute(query, (rfq_id,), fetch='all') or []

    # Build the sent_suppliers string in Python (cross-DB; avoids GROUP_CONCAT)
    supplier_rows = db_execute(
        """
        SELECT rl.id as rfq_line_id, s.name as supplier_name
        FROM rfq_lines rl
        JOIN requisitions req ON rl.id = req.rfq_line_id
        JOIN suppliers s ON req.supplier_id = s.id
        WHERE rl.rfq_id = ?
        ORDER BY rl.id
        """,
        (rfq_id,),
        fetch="all",
    ) or []

    suppliers_by_line: Dict[int, List[str]] = {}
    for r in supplier_rows:
        d = dict(r)
        suppliers_by_line.setdefault(int(d["rfq_line_id"]), []).append(d["supplier_name"])

    rfq_lines = []
    for line in raw_rows:
        d = dict(line)
        line_id = int(d.get("id"))
        names = suppliers_by_line.get(line_id, [])
        # Keep unique while preserving order
        seen = set()
        uniq = []
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            uniq.append(n)
        d["sent_suppliers"] = ",".join(uniq)
        rfq_lines.append(d)

    rfq_lines.sort(key=line_number_key)

    for line in rfq_lines:
        logging.debug(
            f"RFQ Line: id={line['id']}, base_part_number={line['base_part_number']}, stock={line.get('stock')}, has_price_list_item={line['has_price_list_item']}")

    return rfq_lines


def get_rfq_line_by_id(line_id):
    query = '''
        SELECT id, rfq_id, line_number, base_part_number, quantity, manufacturer_id, cost, supplier_lead_time, margin, price, lead_time, line_value, note, status_id
        FROM rfq_lines
        WHERE id = ?
    '''
    row = db_execute(query, (line_id,), fetch='one')
    return dict(row) if row else None

def insert_rfq_line(**line_data):
    logging.debug("Inserting new RFQ line with data: %s", line_data)
    query = '''
        INSERT INTO rfq_lines (
            rfq_id, line_number, base_part_number, quantity, manufacturer_id,
            cost, supplier_lead_time, margin, price, lead_time, line_value,
            note, suggested_suppliers, chosen_supplier, status_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    '''
    params = (
        line_data['rfq_id'], line_data['line_number'], line_data['base_part_number'],
        line_data['quantity'], line_data.get('manufacturer_id'),
        line_data['cost'], line_data['supplier_lead_time'], line_data['margin'],
        line_data['price'], line_data['lead_time'], line_data['line_value'],
        line_data.get('note'), line_data.get('suggested_suppliers'),
        line_data.get('chosen_supplier'), line_data.get('status_id', 1)
    )
    try:
        row = db_execute(query, params, fetch='one', commit=True)
        if not row:
            logging.error("RFQ line insert did not return an ID")
            return None
        new_id = row.get('id', list(row.values())[0])
        logging.info(f"New RFQ line inserted with ID: {new_id}")
        return new_id
    except Exception:
        logging.exception("Error inserting RFQ line")
        return None

def update_rfq_line(line_id, line_number, base_part_number, quantity, manufacturer, status_id, suggested_suppliers,
                    chosen_supplier, offer_price, supplier_lead_time, margin, price, line_value, note):
    logging.debug(f"Updating RFQ line with id={line_id}: offer_price={offer_price}")

    # Get the supplier buffer
    supplier = get_supplier_by_id(chosen_supplier)
    supplier_buffer = supplier['buffer'] if supplier else 0

    # Calculate the lead time
    lead_time = supplier_lead_time + supplier_buffer

    db_execute('''
        UPDATE rfq_lines
        SET line_number = ?, base_part_number = ?, quantity = ?, manufacturer = ?, status_id = ?, suggested_suppliers = ?, chosen_supplier = ?, cost = ?, supplier_lead_time = ?, margin = ?, price = ?, lead_time = ?, line_value = ?, note = ?
        WHERE id = ?
    ''', (
        line_number, base_part_number, quantity, manufacturer, status_id, suggested_suppliers, chosen_supplier, offer_price,
        supplier_lead_time, margin, price, lead_time, line_value, note, line_id
    ), commit=True)


# Suppliers related functions
def get_suppliers():
    try:
        rows = db_execute('''
            SELECT s.*, c.currency_code 
            FROM suppliers s
            LEFT JOIN currencies c ON s.currency = c.id
        ''', fetch='all') or []
        return [dict(row) for row in rows]
    except Exception as e:
        logging.error(f"Error fetching suppliers: {e}")
        return []


def get_supplier_by_id(supplier_id):
    row = db_execute('SELECT * FROM suppliers WHERE id = ?', (supplier_id,), fetch='one')
    return dict_from_row(row) if row else None


def get_supplier_buffer(supplier_id):
    try:
        query = 'SELECT buffer FROM suppliers WHERE id = ?'
        result = db_execute(query, (supplier_id,), fetch='one')
        if result:
            buffer_value = int(result.get('buffer', 0))
            logging.debug(f"Buffer retrieved for supplier {supplier_id}: {buffer_value}")
            return buffer_value
        logging.debug(f"No buffer found for supplier {supplier_id}. Returning 0.")
        return 0
    except Exception as e:
        logging.error(f"Error fetching buffer for supplier {supplier_id}: {e}")
        return 0

def insert_supplier(name, contact_name, contact_email, contact_phone, buffer, currency, fornitore,
                   standard_condition=None, standard_certs=None):
    query = '''
        INSERT INTO suppliers (
            name, contact_name, contact_email, contact_phone, buffer, currency,
            fornitore, standard_condition, standard_certs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    '''
    params = (
        name, contact_name, contact_email, contact_phone, buffer, currency, fornitore,
        standard_condition, standard_certs
    )
    row = db_execute(query, params, fetch='one', commit=True)
    return row.get('id', list(row.values())[0]) if row else None


def update_supplier(supplier_id, name, contact_name, contact_email, contact_phone, buffer, currency, fornitore,
                    standard_condition=None, standard_certs=None):
    logging.info(f"Executing database update for supplier ID: {supplier_id}")
    db_execute(
        '''
        UPDATE suppliers
        SET name = ?, contact_name = ?, contact_email = ?, contact_phone = ?,
            buffer = ?, currency = ?, fornitore = ?, standard_condition = ?, standard_certs = ?
        WHERE id = ?
        ''',
        (
            name, contact_name, contact_email, contact_phone, buffer, currency,
            fornitore, standard_condition, standard_certs, supplier_id
        ),
        commit=True,
    )
    logging.info(f"Database update complete for supplier ID: {supplier_id}")


# Part Numbers related functions
def get_part_numbers():
    rows = db_execute('SELECT * FROM part_numbers', fetch='all') or []
    return [dict_from_row(row) for row in rows]

def get_part_number_by_id(part_number_id):
    row = db_execute('SELECT * FROM part_numbers WHERE id = ?', (part_number_id,), fetch='one')
    return dict_from_row(row) if row else None


def insert_part_number(part_number, base_part_number, system_part_number=None, manufacturer_ids=[]):
    with db_cursor(commit=True) as cur:
        cur.execute(
            '''
            INSERT INTO part_numbers (part_number, base_part_number, system_part_number)
            VALUES (?, ?, ?)
            ''',
            (part_number, base_part_number, system_part_number),
        )

        for manufacturer_id in manufacturer_ids:
            cur.execute(
                '''
                INSERT INTO part_manufacturers (base_part_number, manufacturer_id)
                VALUES (?, ?)
                ''',
                (base_part_number, manufacturer_id),
            )


def update_part_number(base_part_number, part_number, system_part_number=None):
    if system_part_number:
        db_execute(
            '''
            UPDATE part_numbers
            SET part_number = ?, system_part_number = ?
            WHERE base_part_number = ?
            ''',
            (part_number, system_part_number, base_part_number),
            commit=True,
        )
    else:
        db_execute(
            '''
            UPDATE part_numbers
            SET part_number = ?
            WHERE base_part_number = ?
            ''',
            (part_number, base_part_number),
            commit=True,
        )

def delete_part_number(part_number_id):
    db_execute('DELETE FROM part_numbers WHERE id = ?', (part_number_id,), commit=True)

# Alternative Part Numbers related functions
def get_alternative_part_numbers():
    alternative_part_numbers = query_all('SELECT * FROM alternative_part_numbers')
    return [dict(alternative_part_number) for alternative_part_number in alternative_part_numbers]

def get_alternative_part_number_by_id(alternative_part_number_id):
    alternative_part_number = query_one(
        'SELECT * FROM alternative_part_numbers WHERE id = ?',
        (alternative_part_number_id,),
    )
    return dict(alternative_part_number) if alternative_part_number else None

def insert_alternative_part_number(part_number_id, customer, customer_part_number):
    db_execute(
        'INSERT INTO alternative_part_numbers (part_number_id, customer, customer_part_number) VALUES (?, ?, ?)',
        (part_number_id, customer, customer_part_number),
        commit=True,
    )

def update_alternative_part_number(alternative_part_number_id, part_number_id, customer, customer_part_number):
    db_execute(
        'UPDATE alternative_part_numbers SET part_number_id = ?, customer = ?, customer_part_number = ? WHERE id = ?',
        (part_number_id, customer, customer_part_number, alternative_part_number_id),
        commit=True,
    )

def delete_alternative_part_number(alternative_part_number_id):
    db_execute('DELETE FROM alternative_part_numbers WHERE id = ?', (alternative_part_number_id,), commit=True)


def insert_salesperson(name):
    row = db_execute(
        'INSERT INTO salespeople (name) VALUES (?) RETURNING id',
        (name,),
        fetch='one',
        commit=True,
    )
    if row is None:
        raise RuntimeError("Failed to insert salesperson")
    return row.get('id', list(row.values())[0])

def get_salesperson_by_id(salesperson_id):
    salesperson = query_one('SELECT * FROM salespeople WHERE id = ?', (salesperson_id,))
    return dict(salesperson) if salesperson else None

def update_salesperson(salesperson_id, name):
    db_execute(
        'UPDATE salespeople SET name = ? WHERE id = ?',
        (name, salesperson_id),
        commit=True,
    )

def delete_salesperson(salesperson_id):
    db_execute('DELETE FROM salespeople WHERE id = ?', (salesperson_id,), commit=True)

# File related functions
def get_files_by_rfq(rfq_id):
    files = query_all('SELECT * FROM files WHERE rfq_id = ?', (rfq_id,))
    return [dict(file) for file in files]

def insert_file(rfq_id, filename, filepath, upload_date):
    db_execute(
        'INSERT INTO files (rfq_id, filename, filepath, upload_date) VALUES (?, ?, ?, ?)',
        (rfq_id, filename, filepath, upload_date),
        commit=True,
    )

def get_all_manufacturers_with_association(part_id):
    query = '''
        SELECT m.id, m.name,
               CASE WHEN pm.part_id IS NOT NULL THEN 1 ELSE 0 END AS associated
        FROM manufacturers m
        LEFT JOIN part_manufacturers pm ON m.id = pm.manufacturer_id AND pm.part_id = ?
    '''
    rows = db_execute(query, (part_id,), fetch='all') or []
    return [dict(row) for row in rows]


def get_associated_manufacturers(base_part_number):
    query = '''
        SELECT m.id, m.name
        FROM manufacturers m
        JOIN part_manufacturers pm ON m.id = pm.manufacturer_id
        WHERE pm.base_part_number = ?
    '''
    rows = db_execute(query, (base_part_number,), fetch='all') or []
    return [dict(row) for row in rows]


def get_all_manufacturers(include_merged=False):
    if include_merged:
        query = '''
            SELECT m.id, m.name, m.merged_into,
                   m2.name as merged_into_name
            FROM manufacturers m
            LEFT JOIN manufacturers m2 ON m.merged_into = m2.id
            ORDER BY m.name
        '''
    else:
        query = '''
            SELECT id, name 
            FROM manufacturers 
            WHERE merged_into IS NULL
            ORDER BY name
        '''
    rows = db_execute(query, fetch='all') or []
    return [dict(row) for row in rows]


def insert_manufacturer(name):
    existing = db_execute('''
        SELECT m.id, m.name, m.merged_into, m2.name as merged_into_name 
        FROM manufacturers m
        LEFT JOIN manufacturers m2 ON m.merged_into = m2.id
        WHERE LOWER(m.name) = LOWER(?)
    ''', (name,), fetch='one')

    if existing:
        if existing.get('merged_into'):
            return {'error': f"'{name}' was merged into '{existing['merged_into_name']}'"}
        return {'error': f"Manufacturer '{name}' already exists"}

    row = db_execute(
        'INSERT INTO manufacturers (name) VALUES (?) RETURNING id',
        (name,),
        fetch='one',
        commit=True,
    )
    if not row:
        return {'error': 'Failed to insert manufacturer'}
    new_id = row.get('id', list(row.values())[0])
    return {'success': True, 'id': new_id}

def delete_manufacturer(manufacturer_id):
    db_execute('DELETE FROM manufacturers WHERE id = ?', (manufacturer_id,), commit=True)

def create_base_part_number(part_number):
    import re
    base_part_number = re.sub(r'[^a-zA-Z0-9]', '', part_number).upper()
    return base_part_number

class Status(Base):
    __tablename__ = 'statuses'
    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String, nullable=False, unique=True)

class RFQLine(Base):
    __tablename__ = 'rfq_lines'
    id = Column(Integer, primary_key=True, autoincrement=True)
    rfq_id = Column(Integer, nullable=False)
    base_part_number = Column(String, ForeignKey('part_numbers.base_part_number'))
    quantity = Column(Integer, nullable=False)
    cost = Column(Float, nullable=False)
    status_id = Column(Integer, ForeignKey('statuses.id'), nullable=False, default=1)
    status = relationship("Status")
    part_number = relationship("PartNumber")

class PartNumber(Base):
    __tablename__ = 'part_numbers'
    base_part_number = Column(String, primary_key=True)
    part_number = Column(String, nullable=False)
    system_part_number = Column(String)
    created_at = Column(String)

    def __repr__(self):
        return f"<PartNumber(base_part_number={self.base_part_number}, part_number={self.part_number})>"


def get_all_part_numbers_with_manufacturers():
    rows = db_execute(
        '''
        SELECT
            pn.base_part_number,
            pn.part_number
        FROM part_numbers pn
        ''',
        fetch='all',
    ) or []

    base_part_numbers = [dict(r).get('base_part_number') for r in rows]
    manufacturers_by_base: Dict[str, List[str]] = {}

    if base_part_numbers:
        placeholders = ','.join(['?'] * len(base_part_numbers))
        m_rows = db_execute(
            f'''
            SELECT pm.base_part_number, m.name
            FROM part_manufacturers pm
            JOIN manufacturers m ON pm.manufacturer_id = m.id
            WHERE pm.base_part_number IN ({placeholders})
            ORDER BY pm.base_part_number, m.name
            ''',
            base_part_numbers,
            fetch='all',
        ) or []

        for r in m_rows:
            d = dict(r)
            manufacturers_by_base.setdefault(d['base_part_number'], []).append(d['name'])

    result = []
    for r in rows:
        d = dict(r)
        names = manufacturers_by_base.get(d['base_part_number'], [])
        # de-dupe while preserving order
        seen = set()
        uniq = []
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            uniq.append(n)
        d['manufacturers'] = ', '.join(uniq)
        result.append(d)

    return result

import re
import logging

def line_number_key(line):
    line_num = line['line_number']
    if isinstance(line_num, (int, float)):
        return (line_num, 0)
    elif isinstance(line_num, str):
        parts = re.findall(r'\d+', line_num)
        if len(parts) == 0:
            return (0, 0)
        elif len(parts) == 1:
            return (int(parts[0]), 0)
        else:
            return (int(parts[0]), int(parts[1]))
    else:
        return (0, 0)

from collections import Counter


def get_all_rfq_lines():
    query = '''
        SELECT 
            rl.*, 
            rl.datecode,
            rl.taret_price,
            rl.spq,
            rl.packaging,
            rl.rohs,
            r.customer_ref,
            r.status as rfq_status, 
            r.customer_ref,
            r.status as rfq_status,
            c.name as customer_name,
            ol.offer_id AS actual_offer_id,
            o.supplier_id AS chosen_supplier,
            s.name AS chosen_supplier_name, 
            s.currency AS chosen_supplier_currency,
            s.fornitore,
            pn.part_number,
            pn.system_part_number,
            f.id AS file_id,
            f.filepath
        FROM rfq_lines rl
        JOIN rfqs r ON rl.rfq_id = r.id
        JOIN customers c ON r.customer_id = c.id
        LEFT JOIN offer_lines ol ON rl.offer_id = ol.id
        LEFT JOIN offers o ON ol.offer_id = o.id
        LEFT JOIN suppliers s ON o.supplier_id = s.id
        LEFT JOIN part_numbers pn ON rl.base_part_number = pn.base_part_number
        LEFT JOIN offer_files of ON o.id = of.offer_id
        LEFT JOIN files f ON of.file_id = f.id
        WHERE r.status != 'deleted'
        GROUP BY rl.id
    '''
    rows = db_execute(query, fetch='all') or []

    rfq_lines = [dict(row) for row in rows]

    # Cross-DB aggregation for sent suppliers (avoid GROUP_CONCAT)
    supplier_rows = db_execute(
        """
        SELECT rl.id as rfq_line_id, s.name as supplier_name
        FROM rfq_lines rl
        JOIN requisitions req ON rl.id = req.rfq_line_id
        JOIN suppliers s ON req.supplier_id = s.id
        JOIN rfqs r ON rl.rfq_id = r.id
        WHERE r.status != 'deleted'
        ORDER BY rl.id
        """,
        fetch='all',
    ) or []

    suppliers_by_line: Dict[int, List[str]] = {}
    for r in supplier_rows:
        d = dict(r)
        suppliers_by_line.setdefault(int(d['rfq_line_id']), []).append(d['supplier_name'])

    for line in rfq_lines:
        line_id = int(line.get('id'))
        names = suppliers_by_line.get(line_id, [])
        seen = set()
        uniq = []
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            uniq.append(n)
        line['sent_suppliers'] = ','.join(uniq)

    if rfq_lines:
        logging.debug(f"Keys in RFQ line: {list(rfq_lines[0].keys())}")
    else:
        logging.debug("No RFQ lines retrieved")

    logging.debug(f"Number of RFQ lines: {len(rfq_lines)}")

    status_count = Counter(line['rfq_status'] for line in rfq_lines)
    logging.debug(f"RFQ status distribution: {dict(status_count)}")

    rfq_lines.sort(key=line_number_key)

    return rfq_lines

def get_all_rfqs():
    rows = db_execute('''
        SELECT id, status
        FROM rfqs
    ''', fetch='all') or []
    return [dict(row) for row in rows]

def insert_requisition(rfq_id, supplier_id, date, base_part_number, quantity, rfq_line_id):
    reference = f"REQ-{rfq_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    row = db_execute(
        'INSERT INTO requisitions (rfq_id, supplier_id, date, base_part_number, quantity, rfq_line_id, reference) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id',
        (rfq_id, supplier_id, date, base_part_number, quantity, rfq_line_id, reference),
        fetch='one',
        commit=True,
    )
    if not row:
        raise RuntimeError("Failed to insert requisition")
    requisition_id = row.get('id', list(row.values())[0])
    return requisition_id, reference

def get_requisitions():
    rows = db_execute('''
        SELECT id, rfq_id, supplier_id, date, base_part_number, quantity, rfq_line_id
        FROM requisitions
    ''', fetch='all') or []
    return [dict(row) for row in rows]

def safe_int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def get_rfq_lines_with_offers(rfq_id):
    logging.debug("get_rfq_lines_with_offers called with RFQ ID: %s", rfq_id)
    try:
        rfq_lines_query = '''
            SELECT l.*, 
                   l.datecode,
                   l.taret_price,
                   l.spq,
                   l.packaging,
                   l.rohs,
                   l.offered_base_part_number,
                   o.supplier_id AS chosen_supplier,
                   s.name AS chosen_supplier_name, 
                   s.currency AS chosen_supplier_currency,
                   pn_requested.part_number as requested_part_number,
                   pn_offered.part_number as offered_part_number,
                   l.base_part_number as requested_base_part_number,
                   m.name as manufacturer_name,  -- Add this line
                   CASE WHEN pli.id IS NOT NULL THEN 1 ELSE 0 END as has_price_list_item
            FROM rfq_lines l
            LEFT JOIN offers o ON l.offer_id = o.id
            LEFT JOIN suppliers s ON o.supplier_id = s.id
            LEFT JOIN part_numbers pn_requested ON l.base_part_number = pn_requested.base_part_number
            LEFT JOIN part_numbers pn_offered ON l.offered_base_part_number = pn_offered.base_part_number
            LEFT JOIN manufacturers m ON l.manufacturer_id = m.id  -- Add this line
            LEFT JOIN price_list_items pli ON l.base_part_number = pli.base_part_number
            WHERE l.rfq_id = ?
        '''
        rfq_lines = db_execute(rfq_lines_query, (rfq_id,), fetch='all') or []
        if not rfq_lines:
            logging.debug("No RFQ lines found")

        rfq_lines_dict = []

        for line in rfq_lines:
            line_dict = dict(line)
            logging.debug("Processing line: %s", line_dict)

            offers_query = '''
                SELECT ol.id as offer_line_id, 
                   o.id as offer_id, 
                   ol.price, 
                   ol.lead_time, 
                   s.name as supplier_name, 
                   s.id as supplier_id, 
                   s.currency as supplier_currency,
                   o.valid_to as valid_to,
                   c.currency_code,
                   ol.quantity,
                   ol.base_part_number as offered_base_part_number,
                   ol.requested_base_part_number,
                   ol.internal_notes,
                   ol.datecode,
                   ol.spq,
                   ol.packaging,
                   ol.rohs,
                   ol.coc,
                   pn_offered.part_number as offered_part_number,
                   pn_requested.part_number as requested_part_number
                FROM offer_lines ol
                JOIN offers o ON ol.offer_id = o.id
                JOIN suppliers s ON o.supplier_id = s.id
                LEFT JOIN currencies c ON s.currency = c.id
                LEFT JOIN part_numbers pn_offered ON ol.base_part_number = pn_offered.base_part_number
                LEFT JOIN part_numbers pn_requested ON ol.requested_base_part_number = pn_requested.base_part_number
                WHERE ol.requested_base_part_number = ?
            '''
            offers = db_execute(offers_query, (line_dict['base_part_number'],), fetch='all') or []
            if offers:
                first_offer = dict(offers[0])
                logging.debug("First offer data for %s: %s", line_dict['base_part_number'], first_offer)
            else:
                logging.debug("No offers found for %s", line_dict['base_part_number'])

            line_dict['offers'] = [dict(offer) for offer in offers]
            rfq_lines_dict.append(line_dict)

        logging.debug("Returning RFQ lines with offers: %s", rfq_lines_dict)
        return rfq_lines_dict
    except Exception as e:
        logging.error("Error occurred in get_rfq_lines_with_offers: %s", e)
        return []

def row_to_dict(obj):
    """
    Convert SQLAlchemy ORM object to dictionary, excluding private fields and relationships.
    """
    if isinstance(obj, dict):
        return obj
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns}


def get_rfq_lines_with_suppliers(rfq_id):
    logging.basicConfig(level=logging.DEBUG)  # Ensure the logging level is set to debug

    try:
        logging.debug("Fetching RFQ lines for RFQ ID: %s", rfq_id)
        query = '''
            SELECT rl.id, rl.line_number, rl.base_part_number, rl.quantity, rl.cost, rl.supplier_lead_time, rl.margin, 
                   rl.price, rl.lead_time, rl.line_value, rl.note, rl.suggested_suppliers, rl.chosen_supplier, 
                   m.id as manufacturer_id, m.name as manufacturer, pn.part_number,
                   (SELECT customer_part_number 
                    FROM customer_part_numbers 
                    WHERE base_part_number = rl.base_part_number 
                    AND customer_id = r.customer_id) AS customer_part_number
            FROM rfq_lines rl
            LEFT JOIN manufacturers m ON rl.manufacturer_id = m.id
            LEFT JOIN part_numbers pn ON rl.base_part_number = pn.base_part_number
            JOIN rfqs r ON rl.rfq_id = r.id
            WHERE rl.rfq_id = ?
        '''
        rfq_lines = db_execute(query, (rfq_id,), fetch='all') or []
        logging.debug("Fetched %s RFQ lines", len(rfq_lines))

        # Fetch requisitions for these RFQ lines
        logging.debug("Fetching requisitions for RFQ ID: %s", rfq_id)
        requisitions_query = '''
            SELECT r.rfq_line_id, r.supplier_id
            FROM requisitions r
            WHERE r.rfq_id = ?
        '''
        requisitions = db_execute(requisitions_query, (rfq_id,), fetch='all') or []
        logging.debug("Fetched %s requisitions", len(requisitions))

        # Create a map of RFQ line IDs to the suppliers that have received requisitions
        requisitioned_suppliers = {}
        for req in requisitions:
            rfq_line_id = req['rfq_line_id']
            if rfq_line_id not in requisitioned_suppliers:
                requisitioned_suppliers[rfq_line_id] = set()
            requisitioned_suppliers[rfq_line_id].add(req['supplier_id'])
        logging.debug(f"Requisitioned suppliers mapping: {requisitioned_suppliers}")

        # Convert RFQ lines to dictionary format
        rfq_lines_dict = [dict(row) for row in rfq_lines]

        # Get all suppliers
        logging.debug("Fetching all suppliers")
        suppliers = get_suppliers()
        logging.debug(f"Fetched {len(suppliers)} suppliers")

        for line in rfq_lines_dict:
            line_id = line['id']
            logging.debug(f"Processing RFQ Line ID: {line_id}")

            # Suppliers suggested by default (populates the tick box)
            suggested_suppliers = line.get('suggested_suppliers', '').split(',')
            suggested_suppliers = [int(s) for s in suggested_suppliers if s.isdigit()]
            logging.debug(f"Suggested suppliers for line {line_id}: {suggested_suppliers}")

            # Suppliers that have received requisitions
            logging.debug(f"Processing suppliers for line {line_id} in get_rfq_lines_with_suppliers")
            received_suppliers = [supplier for supplier in suppliers if
                                  supplier['id'] in requisitioned_suppliers.get(line_id, set())]
            logging.debug(
                f"Received requisitions for line {line_id}: {[supplier['id'] for supplier in received_suppliers]}")

            # Suppliers that have not received requisitions
            not_received_suppliers = [supplier for supplier in suppliers if
                                      supplier['id'] not in requisitioned_suppliers.get(line_id, set())]

            # Combine them with received suppliers first
            line['sorted_suppliers'] = received_suppliers + not_received_suppliers
            logging.debug(
                f"Sorted suppliers for line {line_id}: {[supplier['id'] for supplier in line['sorted_suppliers']]}")

        logging.debug(f"Final RFQ Lines with sorted suppliers: {rfq_lines_dict}")

        return rfq_lines_dict

    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        return []


def get_customer_id_by_rfq(rfq_id):
    row = db_execute('SELECT customer_id FROM rfqs WHERE id = ?', (rfq_id,), fetch='one')
    return row['customer_id'] if row else None

def update_currency_rate(currency_code, exchange_rate_to_eur):
    rate_col = CURRENCY_RATE_COLUMN  # 'exchange_rate_to_eur' (SQLite) or 'exchange_rate_to_base' (Postgres)
    db_execute(
        f'''
        INSERT INTO currencies (currency_code, {rate_col})
        VALUES (?, ?)
        ON CONFLICT(currency_code)
        DO UPDATE SET {rate_col} = excluded.{rate_col}
        ''',
        (currency_code, exchange_rate_to_eur),
        commit=True,
    )

def get_exchange_rate(currency_code):
    rate_col = CURRENCY_RATE_COLUMN
    row = db_execute(f'SELECT {rate_col} AS rate FROM currencies WHERE currency_code = ?', (currency_code,), fetch='one')
    return row['rate'] if row else None


def convert_to_eur(amount, currency_code):
    if currency_code == 'EUR':
        return amount
    exchange_rate = get_exchange_rate(currency_code)
    if exchange_rate is None:
        raise ValueError(f"Exchange rate not found for {currency_code}")
    return amount / exchange_rate

def update_rfq_line_base_cost(cursor, line_id, cost, cost_currency, exchange_rate_to_eur):
    if cost is None or cost_currency is None or exchange_rate_to_eur is None:
        logging.warning(f"Cost, currency, or exchange rate is None for line_id {line_id}. Skipping base cost update.")
        return

    try:
        base_cost = cost / exchange_rate_to_eur
        logging.debug(f"Updating base cost for line_id {line_id}: cost {cost} {cost_currency} -> base_cost {base_cost} EUR (exchange rate: {exchange_rate_to_eur})")

        cursor.execute('''
            UPDATE rfq_lines
            SET base_cost = ?, cost_currency = ?
            WHERE id = ?
        ''', (base_cost, cost_currency, line_id))
        logging.debug(f"Base cost for line_id {line_id} updated to {base_cost} EUR")
    except ValueError as e:
        logging.error(f'Error updating base cost for line_id {line_id}: {e}')
    except Exception as e:
        logging.error(f'Database error updating base cost for line_id {line_id}: {e}')


def clean_rfq_lines_base_part_numbers():
    with db_cursor(commit=True) as cur:
        cur.execute('SELECT id, base_part_number FROM rfq_lines')
        lines = cur.fetchall()

        for line in lines:
            line_id = line['id']
            old_base_part_number = line['base_part_number']
            new_base_part_number = create_base_part_number(old_base_part_number)

            if new_base_part_number != old_base_part_number:
                cur.execute(
                    'UPDATE rfq_lines SET base_part_number = ? WHERE id = ?',
                    (new_base_part_number, line_id),
                )

            cur.execute('SELECT 1 FROM part_numbers WHERE base_part_number = ?', (new_base_part_number,))
            if not cur.fetchone():
                cur.execute(
                    'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                    (new_base_part_number, new_base_part_number),
                )

def get_all_statuses():
    rows = db_execute('SELECT * FROM statuses ORDER BY id', fetch='all') or []
    return [dict(row) for row in rows]


def get_part_number_by_base(base_part_number):
    try:
        row = db_execute(
            '''
            SELECT part_number
            FROM part_numbers
            WHERE base_part_number = ?
            ''',
            (base_part_number,),
            fetch='one',
        )
        if row:
            return row['part_number']
    except Exception as e:
        logging.error("Error in get_part_number_by_base: %s", e)
    return base_part_number


def create_requisition(rfq_id, supplier_id):
    reference = f"REQ-{rfq_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    row = db_execute(
        '''
        INSERT INTO requisitions (rfq_id, supplier_id, date, reference)
        VALUES (?, ?, ?, ?)
        RETURNING id
        ''',
        (rfq_id, supplier_id, datetime.now().strftime('%Y-%m-%d'), reference),
        fetch='one',
        commit=True,
    )

    if not row:
        raise RuntimeError("Failed to create requisition")
    requisition_id = row.get('id', list(row.values())[0])
    return requisition_id, reference


def add_requisition_line(requisition_id, rfq_line_id, part_number, quantity, cost, supplier_lead_time, note):
    db_execute(
        '''
        INSERT INTO requisition_lines (requisition_id, rfq_line_id, part_number, quantity, cost, supplier_lead_time, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (requisition_id, rfq_line_id, part_number, quantity, cost, supplier_lead_time, note),
        commit=True,
    )


def get_rfq_line_currency(line_id):
    logging.debug(f"Entering get_rfq_line_currency function with line_id: {line_id}")
    try:
        # Get all needed currency info in one query
        query = '''
            SELECT 
                rl.cost_currency, 
                cost_curr.currency_code AS cost_currency_code, 
                cost_curr.exchange_rate_to_eur AS cost_exchange_rate,
                r.currency AS rfq_currency_id,
                rfq_curr.currency_code AS rfq_currency_code,
                rfq_curr.exchange_rate_to_eur AS rfq_exchange_rate
            FROM rfq_lines rl
            JOIN rfqs r ON rl.rfq_id = r.id
            LEFT JOIN currencies cost_curr ON rl.cost_currency = cost_curr.id
            LEFT JOIN currencies rfq_curr ON r.currency = rfq_curr.id
            WHERE rl.id = ?
        '''
        logging.debug(f"Executing query: {query} with line_id: {line_id}")
        result = db_execute(query, (line_id,), fetch='one')

        if result:
            logging.debug(f"Query result: {dict(result)}")  # Convert to dict for logging
            return {
                "success": True,
                "cost_currency": result['cost_currency'],
                "exchange_rate_to_eur": result['cost_exchange_rate'],
                "rfq_currency_id": result['rfq_currency_id'],
                "rfq_exchange_rate_to_eur": result['rfq_exchange_rate']
            }
        else:
            logging.warning(f"No result found for line_id: {line_id}")
            return {
                "success": False,
                "error": "RFQ line not found or currency information missing"
            }
    except Exception as e:
        logging.error(f"Error in get_rfq_line_currency: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }

def update_supplier_fornitore(supplier_id, new_fornitore):
    db_execute(
        'UPDATE suppliers SET fornitore = ? WHERE id = ?',
        (new_fornitore, supplier_id),
        commit=True,
    )

def update_supplier_field(supplier_id, field_name, new_value):
    query = f'UPDATE suppliers SET {field_name} = ? WHERE id = ?'
    db_execute(query, (new_value, supplier_id), commit=True)


def get_currency_symbol(currency_id):
    if not currency_id:
        return ''

    row = db_execute('SELECT id, currency_code, symbol FROM currencies WHERE id = ?', (currency_id,), fetch='one')
    logging.debug("get_currency_symbol input: %s, result: %s", currency_id, row)

    if row and row.get('symbol'):
        return row['symbol']
    if row and row.get('currency_code'):
        return row['currency_code']
    return str(currency_id)

def calculate_base_cost(cost, exchange_rate):
    if exchange_rate <= 0:
        raise ValueError("Invalid exchange rate")
    return cost / exchange_rate


def check_price_list_items(base_part_numbers):
    if not base_part_numbers:
        logging.warning("No base part numbers provided to check_price_list_items")
        return []

    placeholders = ','.join('?' * len(base_part_numbers))
    query = f"""
        SELECT * FROM price_list_items 
        WHERE base_part_number IN ({placeholders})
    """

    rows = db_execute(query, tuple(base_part_numbers), fetch='all') or []

    for row in rows:
        logging.info("Matching Price List Item: %s", dict(row))

    if not rows:
        logging.warning("No matching price list items found.")

    return rows


def verify_rfq_and_lines(rfq_id):
    rfq = db_execute("SELECT * FROM rfqs WHERE id = ?", (rfq_id,), fetch='one')

    if not rfq:
        logging.error(f"RFQ with ID {rfq_id} does not exist.")
        return None

    lines = db_execute("SELECT * FROM rfq_lines WHERE rfq_id = ?", (rfq_id,), fetch='all') or []

    if not lines:
        logging.error(f"No RFQ lines found for RFQ ID {rfq_id}.")
        return None

    logging.info(f"Found RFQ ID {rfq_id} with {len(lines)} lines.")

    base_part_numbers = db_execute(
        "SELECT DISTINCT base_part_number FROM rfq_lines WHERE rfq_id = ?",
        (rfq_id,),
        fetch='all',
    ) or []

    for bpn in base_part_numbers:
        logging.info(f"Base Part Number in RFQ: {bpn['base_part_number']}")

    return [dict(line) for line in lines]


def get_price_list_price(base_part_number: str, quantity: int) -> Optional[Dict[str, Any]]:
    rows = db_execute('''
        SELECT pli.id, pli.price_list_id, pli.lead_time, 
               pb.quantity, pb.price,
               pl.supplier_id, s.name as supplier_name
        FROM price_list_items pli
        JOIN price_breaks pb ON pli.id = pb.price_list_item_id
        JOIN price_lists pl ON pli.price_list_id = pl.id
        JOIN suppliers s ON pl.supplier_id = s.id
        WHERE pli.base_part_number = ?
        ORDER BY pb.quantity ASC
    ''', (base_part_number,), fetch='all') or []

    if not rows:
        return None

    selected_price_break = None
    selected_index = -1
    for index, price_break in enumerate(rows):
        if price_break['quantity'] > quantity:
            break
        selected_price_break = price_break
        selected_index = index

    if not selected_price_break:
        return None

    next_break_quantity = (
        rows[selected_index + 1]['quantity'] if selected_index + 1 < len(rows) else None
    )

    return {
        'price': selected_price_break['price'],
        'quantity': selected_price_break['quantity'],
        'lead_time': selected_price_break['lead_time'],
        'supplier_id': selected_price_break['supplier_id'],
        'supplier_name': selected_price_break['supplier_name'],
        'price_list_item_id': selected_price_break['id'],
        'price_list_id': selected_price_break['price_list_id'],
        'next_break_quantity': next_break_quantity,
    }

def update_rfq_line_db(line_id, update_data):
    db_execute(
        '''
        UPDATE rfq_lines 
        SET chosen_supplier = ?, price = ?, supplier_lead_time = ?, line_value = ?
        WHERE id = ?
        ''',
        (
            update_data['chosen_supplier'],
            update_data['price'],
            update_data['supplier_lead_time'],
            update_data['line_value'],
            line_id,
        ),
        commit=True,
    )


def insert_update(customer_id, salesperson_id, update_text, contact_id=None, communication_type=None, update_date=None):
    # Use provided date or default to current timestamp
    if update_date:
        if isinstance(update_date, date) and not isinstance(update_date, datetime):
            update_datetime = datetime.combine(update_date, datetime.min.time())
        elif isinstance(update_date, datetime):
            update_datetime = update_date
        else:
            try:
                update_datetime = datetime.strptime(str(update_date), '%Y-%m-%d')
            except ValueError:
                update_datetime = datetime.strptime(str(update_date), '%Y-%m-%d %H:%M:%S')
        current_time = update_datetime.strftime('%Y-%m-%d %H:%M:%S')
    else:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    print(f"DEBUG: Using timestamp: {current_time}")

    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                'INSERT INTO customer_updates (date, customer_id, salesperson_id, update_text, communication_type) VALUES (?, ?, ?, ?, ?) RETURNING id',
                (current_time, customer_id, salesperson_id, update_text, communication_type),
            )
            inserted = cur.fetchone()
            update_id = inserted['id'] if inserted and 'id' in inserted else (list(inserted.values())[0] if inserted else None)

            if contact_id and communication_type:
                pk_clause = 'SERIAL PRIMARY KEY' if _using_postgres() else 'INTEGER PRIMARY KEY AUTOINCREMENT'
                cur.execute(f'''
                    CREATE TABLE IF NOT EXISTS contact_communications (
                        id {pk_clause},
                        date TEXT NOT NULL,
                        contact_id INTEGER NOT NULL,
                        customer_id INTEGER NOT NULL,
                        salesperson_id INTEGER NOT NULL,
                        communication_type TEXT NOT NULL,
                        notes TEXT,
                        email_message_id TEXT,
                        email_direction TEXT,
                        update_id INTEGER,
                        FOREIGN KEY (contact_id) REFERENCES contacts(id),
                        FOREIGN KEY (customer_id) REFERENCES customers(id),
                        FOREIGN KEY (update_id) REFERENCES customer_updates(id)
                    )
                    ''')
                cur.execute(
                    'INSERT INTO contact_communications (date, contact_id, customer_id, salesperson_id, communication_type, notes, update_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (current_time, contact_id, customer_id, salesperson_id, communication_type, update_text, update_id),
                )
        return update_id
    except Exception as e:
        print(f"Error inserting update: {e}")
        raise

def get_updates_by_customer_id(customer_id):
    updates = query_all('''
        SELECT 
            cu.*,
            s.name as salesperson_name,
            cc.contact_id,
            c.name as contact_name  -- Added contact name
        FROM customer_updates cu
        LEFT JOIN salespeople s ON cu.salesperson_id = s.id 
        LEFT JOIN contact_communications cc ON (
            cc.customer_id = cu.customer_id AND 
            cc.salesperson_id = cu.salesperson_id AND 
            cc.communication_type = cu.communication_type AND
            cc.notes = cu.update_text AND
            DATE(cc.date) = DATE(cu.date)
        )
        LEFT JOIN contacts c ON cc.contact_id = c.id  -- Added join to contacts table
        WHERE cu.customer_id = ? 
        ORDER BY cu.date DESC
    ''', (customer_id,))

    # Convert dates and ensure each update is a dictionary
    result = []
    for update in updates:
        update_dict = dict(update)

        # Parse the date string to a datetime object
        try:
            # Try standard format first
            if update_dict['date']:
                # Handle different possible date formats
                date_str = update_dict['date']

                # Check for 'T' format (ISO format)
                if 'T' in date_str:
                    try:
                        update_dict['date'] = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    except ValueError:
                        # Try other formats
                        try:
                            update_dict['date'] = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')
                        except ValueError:
                            update_dict['date'] = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S.%f')
                else:
                    # Try standard SQLite format
                    try:
                        update_dict['date'] = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        # Last resort - just use current time
                        update_dict['date'] = datetime.now()
            else:
                # If date is None or empty, use current time
                update_dict['date'] = datetime.now()
        except Exception as e:
            print(f"Error parsing date: {e}")
            # Default to current time if date parsing fails
            update_dict['date'] = datetime.now()

        result.append(update_dict)

    return result


def get_customers_with_status_and_updates(search_mode=False):
    query = """
    WITH latest_activity AS (
        SELECT 
            customer_id,
            activity_type,
            activity_date as latest_activity,
            description,
            status,
            ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY activity_date DESC) as rn
        FROM (
            -- Emails through contacts
            SELECT 
                c.customer_id,
                'email' as activity_type,
                e.sent_date as activity_date,
                e.subject as description,
                e.direction as status
            FROM emails e
            JOIN contacts c ON LOWER(e.sender_email) = LOWER(c.email)

            UNION ALL

            SELECT 
                c.customer_id,
                'email' as activity_type,
                e.sent_date as activity_date,
                e.subject as description,
                e.direction as status
            FROM emails e
            JOIN contacts c ON LOWER(e.recipient_email) LIKE '%' || LOWER(c.email) || '%'

            UNION ALL

            -- RFQs
            SELECT 
                customer_id,
                'rfq' as activity_type,
                entered_date as activity_date,
                customer_ref as description,
                status
            FROM rfqs

            UNION ALL

            -- Sales Orders
            SELECT 
                customer_id,
                'order' as activity_type,
                date_entered as activity_date,
                sales_order_ref as description,
                ss.status_name as status
            FROM sales_orders so
            LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
        )
    )
    SELECT 
        c.id, c.name, c.payment_terms, c.incoterms, c.primary_contact_id, c.salesperson_id,
        cs.status AS customer_status,
        s.name AS salesperson_name,
        la.latest_activity,
        la.activity_type,
        la.description as activity_description,
        la.status as activity_status
    FROM customers c
    LEFT JOIN customer_status cs ON c.status_id = cs.id
    LEFT JOIN salespeople s ON c.salesperson_id = s.id
    LEFT JOIN latest_activity la ON c.id = la.customer_id AND la.rn = 1
    """

    if not search_mode:
        query += " ORDER BY la.latest_activity DESC NULLS LAST LIMIT 20"
    else:
        query += " ORDER BY c.name"

    rows = db_execute(query, fetch='all') or []
    return [dict(row) for row in rows]

def get_open_rfq_lines_by_base_part_numbers(base_part_numbers):
    """
    Retrieve RFQ lines that are open (not deleted) and match the provided base_part_numbers.
    Returns a list of dictionaries with RFQ line details.
    """
    if not base_part_numbers:
        return []

    placeholders = ','.join('?' for _ in base_part_numbers)
    query = f'''
        SELECT rl.*, r.id AS rfq_id
        FROM rfq_lines rl
        JOIN rfqs r ON rl.rfq_id = r.id
        WHERE rl.base_part_number IN ({placeholders})
          AND r.status != 'deleted'
    '''
    rows = db_execute(query, tuple(base_part_numbers), fetch='all') or []
    return [dict(rfq_line) for rfq_line in rows]

def get_offer_by_id(offer_id):
    row = db_execute('SELECT * FROM offers WHERE id = ?', (offer_id,), fetch='one')
    return dict_from_row(row) if row else None

# Fetch all sales orders
def get_sales_orders(limit=None):
    """
    Fetch sales orders from the database.

    Args:
        limit (int, optional): Maximum number of orders to return. If None, returns all orders.

    Returns:
        list: A list of sales orders, with newest orders first.
    """
    # Include both order salesperson and customer's default salesperson
    query = '''
        SELECT so.*, 
               c.name AS customer_name, 
               c.salesperson_id AS customer_salesperson_id,
               ss.status_name, 
               sp.id AS salesperson_id, 
               sp.name AS salesperson_name,
               csp.name AS customer_salesperson_name
        FROM sales_orders so
        LEFT JOIN customers c ON so.customer_id = c.id
        LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
        LEFT JOIN salespeople sp ON so.salesperson_id = sp.id
        LEFT JOIN salespeople csp ON c.salesperson_id = csp.id
        ORDER BY so.id DESC
    '''

    if limit is not None:
        query += f' LIMIT {limit}'

    rows = db_execute(query, fetch='all') or []
    return [dict(order) for order in rows]

def get_sales_order_by_id(sales_order_id):
    # Fetch sales order details including the date_entered field
    query = '''
        SELECT so.id, so.customer_id, so.date_entered, so.total_value, c.name AS customer_name
        FROM sales_orders so
        JOIN customers c ON so.customer_id = c.id
        WHERE so.id = ?
    '''

    logging.debug("Fetching sales order with ID: %s", sales_order_id)
    row = db_execute(query, (sales_order_id,), fetch='one')
    return dict(row) if row else None


def insert_sales_order(customer_id, customer_po_ref):
    customer = db_execute('''
        SELECT primary_contact_id, payment_terms, incoterms, salesperson_id, currency_id 
        FROM customers WHERE id = ?
    ''', (customer_id,), fetch='one')

    if not customer:
        return None

    contact_name = customer['primary_contact_id']
    salesperson_id = customer['salesperson_id']
    payment_terms = customer['payment_terms']
    incoterms = customer['incoterms']
    currency_id = customer['currency_id']

    today = date.today()
    year = today.year

    last_order = db_execute(
        'SELECT sales_order_ref FROM sales_orders ORDER BY id DESC LIMIT 1',
        fetch='one',
    )

    if last_order:
        last_number = int(last_order['sales_order_ref'].split('-')[-1])
        new_order_number = last_number + 1
    else:
        new_order_number = 1

    sales_order_ref = f"SO{year}-{new_order_number:03d}"

    db_execute('''
        INSERT INTO sales_orders (sales_order_ref, customer_id, customer_po_ref, contact_name, salesperson_id, payment_terms, incoterms, currency_id, date_entered, sales_status_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_DATE, 1)
    ''', (sales_order_ref, customer_id, customer_po_ref, contact_name, salesperson_id, payment_terms, incoterms, currency_id), commit=True)



def get_sales_order_lines(sales_order_id):
    query = '''
        SELECT sol.*, ss.status_name, po.purchase_order_ref, po.supplier_id, s.name as supplier_name,
               rfq.id as rfq_line_id, rfq.cost, rfq.supplier_lead_time, s.name as supplier_name
        FROM sales_order_lines sol
        LEFT JOIN sales_statuses ss ON sol.sales_status_id = ss.id
        LEFT JOIN purchase_order_lines pol ON sol.id = pol.sales_order_line_id
        LEFT JOIN purchase_orders po ON pol.purchase_order_id = po.id
        LEFT JOIN rfq_lines rfq ON sol.rfq_line_id = rfq.id
        LEFT JOIN suppliers s ON rfq.chosen_supplier = s.id
        WHERE sol.sales_order_id = ?
    '''
    rows = db_execute(query, (sales_order_id,), fetch='all') or []
    return [dict(line) for line in rows]


def update_sales_order_line(line_id, quantity, price, promise_date, ship_date, requested_date, rfq_line_id=None, shipped_quantity=None):
    # Prepare the base query without the rfq_line_id and shipped_quantity updates
    query = '''
        UPDATE sales_order_lines
        SET quantity = ?, price = ?, promise_date = ?, ship_date = ?, requested_date = ?
    '''

    params = [quantity, price, promise_date, ship_date, requested_date]

    # Only include rfq_line_id in the query if it's provided
    if rfq_line_id is not None:
        query += ', rfq_line_id = ?'
        params.append(rfq_line_id)

    # Only include shipped_quantity in the query if it's provided
    if shipped_quantity is not None:
        query += ', shipped_quantity = ?'
        params.append(shipped_quantity)

    query += ' WHERE id = ?'
    params.append(line_id)

    db_execute(query, params, commit=True)

def update_sales_order_lines(sales_order_id, updated_lines):
    with db_cursor(commit=True) as cur:
        for line in updated_lines:
            cur.execute('''
                UPDATE sales_order_lines
                SET line_number = ?, part_number = ?, quantity = ?, price = ?, delivery_date = ?
                WHERE id = ? AND sales_order_id = ?
            ''', (
                line['line_number'], line['part_number'], line['quantity'], line['price'], line['delivery_date'], line['id'],
                sales_order_id
            ))

def insert_sales_order_line(sales_order_id, line_number, part_number, quantity, price, delivery_date):
    base_part_number = create_base_part_number(part_number)
    sales_status_id = 1
    db_execute('''
        INSERT INTO sales_order_lines (sales_order_id, line_number, base_part_number, quantity, price, delivery_date, sales_status_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (sales_order_id, line_number, base_part_number, quantity, price, delivery_date, sales_status_id), commit=True)


def get_sales_statuses():
    query = '''
        SELECT * FROM sales_statuses
    '''
    rows = db_execute(query, fetch='all') or []
    return [dict(status) for status in rows]
