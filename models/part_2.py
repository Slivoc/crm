import json
import logging
import time
import re
import requests
from flask import current_app, g, render_template, abort
from db import get_db_connection, execute as db_execute, db_cursor
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
DATABASE_URL = _os.getenv('DATABASE_URL', 'sqlite:///database.db')

# Set up an engine for SQLAlchemy to use for reflection
engine = create_engine(DATABASE_URL)
metadata = MetaData()

# PostgreSQL compatibility helper

def get_max_line_number(sales_order_id):
    query = '''
        SELECT MAX(line_number) AS max_line_number
        FROM sales_order_lines
        WHERE sales_order_id = ?
    '''
    result = db_execute(query, (sales_order_id,), fetch='one')
    return result['max_line_number'] if result and result['max_line_number'] else 0


def get_purchase_orders(limit=None, offset=0):
    """
    Get purchase orders with pagination support

    Args:
        limit (int, optional): Number of records to return
        offset (int, optional): Number of records to skip

    Returns:
        list: List of purchase order dictionaries
    """
    query = """
        SELECT po.id, po.purchase_order_ref, s.name as supplier_name, 
               po.date_issued, po.total_value, pos.name as status_name
        FROM purchase_orders po
        JOIN suppliers s ON po.supplier_id = s.id
        JOIN purchase_order_statuses pos ON po.purchase_status_id = pos.id
        ORDER BY po.date_issued DESC
    """

    if limit is not None:
        query += f" LIMIT {limit} OFFSET {offset}"

    rows = db_execute(query, fetch='all') or []
    return [dict(row) for row in rows]


def get_purchase_orders_count():
    """
    Get the total count of purchase orders

    Returns:
        int: Total number of purchase orders
    """
    row = query_one("SELECT COUNT(*) as count FROM purchase_orders")
    return row['count'] if row else 0


def get_purchase_orders_total_value():
    """
    Get the total value of all purchase orders

    Returns:
        float: Total value of all purchase orders
    """
    row = query_one("SELECT SUM(total_value) as total_value FROM purchase_orders")
    total = row['total_value'] if row else None
    return total if total is not None else 0

def get_purchase_order_by_id(order_id):
    query = '''
        SELECT po.*, s.name AS supplier_name, pos.name AS status_name,
               c.currency_code, c.symbol AS currency_symbol
        FROM purchase_orders po
        JOIN suppliers s ON po.supplier_id = s.id
        JOIN purchase_order_statuses pos ON po.purchase_status_id = pos.id
        JOIN currencies c ON po.currency_id = c.id
        WHERE po.id = ?
    '''
    purchase_order = query_one(query, (order_id,))
    return dict(purchase_order) if purchase_order else None

def insert_purchase_order(supplier_id):
    supplier = query_one('SELECT * FROM suppliers WHERE id = ?', (supplier_id,))

    purchase_order_ref = get_next_purchase_order_ref()
    date_issued = datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    insert_query = '''
        INSERT INTO purchase_orders (
            supplier_id, purchase_order_ref, date_issued, 
            incoterms, payment_terms, purchase_status_id, currency_id, 
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    '''

    row = db_execute(
        insert_query + " RETURNING id",
        (
            supplier_id,
            purchase_order_ref,
            date_issued,
            supplier['incoterms'] if supplier and 'incoterms' in supplier else None,
            supplier['payment_terms'] if supplier and 'payment_terms' in supplier else None,
            1,
            supplier['currency'] if supplier and 'currency' in supplier else None,
            timestamp,
            timestamp,
        ),
        fetch='one',
        commit=True,
    )
    if row is None:
        raise RuntimeError("Failed to insert purchase order")
    return row.get('id', list(row.values())[0])

def update_purchase_order_line_field(line_id, field, value):
    query = f"UPDATE purchase_order_lines SET {field} = ? WHERE id = ?"
    db_execute(query, (value, line_id), commit=True)


def update_purchase_order(purchase_order_id, supplier_id, purchase_order_ref, purchase_status_id, date_issued, incoterms, payment_terms):
    db_execute('''
        UPDATE purchase_orders
        SET supplier_id = ?, purchase_order_ref = ?, purchase_status_id = ?, 
            date_issued = ?, incoterms = ?, payment_terms = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (supplier_id, purchase_order_ref, purchase_status_id, date_issued, incoterms, payment_terms, purchase_order_id), commit=True)

def get_purchase_order_lines(purchase_order_id):
    query = '''
        SELECT pol.*, pn.part_number, ss.status_name
        FROM purchase_order_lines pol
        JOIN part_numbers pn ON pol.base_part_number = pn.base_part_number
        JOIN sales_statuses ss ON pol.status_id = ss.id
        WHERE pol.purchase_order_id = ?
    '''
    rows = db_execute(query, (purchase_order_id,), fetch='all') or []
    return [dict(line) for line in rows]


def insert_purchase_order_line(purchase_order_id, line_number, base_part_number, quantity, price, ship_date, promised_date):
    row = db_execute('''
        INSERT INTO purchase_order_lines (
            purchase_order_id, line_number, base_part_number, quantity, price, 
            ship_date, promised_date, status_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        RETURNING id
    ''', (purchase_order_id, line_number, base_part_number, quantity, price, ship_date, promised_date, 1), fetch='one', commit=True)
    return row.get('id', list(row.values())[0]) if row else None

def update_purchase_order_line(line_id, line_number, base_part_number, quantity, price, ship_date, promised_date, status_id):
    db_execute('''
        UPDATE purchase_order_lines
        SET line_number = ?, base_part_number = ?, quantity = ?, price = ?, 
            ship_date = ?, promised_date = ?, status_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (line_number, base_part_number, quantity, price, ship_date, promised_date, status_id, line_id), commit=True)

def get_all_sales_statuses():
    rows = db_execute('SELECT * FROM sales_statuses ORDER BY id', fetch='all') or []
    return [dict(status) for status in rows]



def update_sales_order(sales_order_id, customer_id, customer_po_ref, sales_status_id):
    db_execute('''
        UPDATE sales_orders
        SET customer_id = ?, customer_po_ref = ?, sales_status_id = ?
        WHERE id = ?
    ''', (customer_id, customer_po_ref, sales_status_id, sales_order_id), commit=True)



def delete_purchase_order_line(line_id):
    db_execute('DELETE FROM purchase_order_lines WHERE id = ?', (line_id,), commit=True)

def get_next_purchase_order_ref():
    year = date.today().year
    row = db_execute('''
        SELECT purchase_order_ref FROM purchase_orders 
        WHERE purchase_order_ref LIKE ?
        ORDER BY id DESC LIMIT 1
    ''', (f'PO{year}-%',), fetch='one')

    if row and row.get('purchase_order_ref'):
        last_number = int(row['purchase_order_ref'].split('-')[-1])
        new_order_number = last_number + 1
    else:
        new_order_number = 1

    return f"PO{year}-{new_order_number:03d}"



def get_open_sales_order_lines(base_part_number):
    query = '''
        SELECT sol.id, sol.price, sol.quantity, c.name AS customer_name
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        JOIN customers c ON so.customer_id = c.id
        WHERE sol.base_part_number = ? AND sol.sales_status_id = ?
    '''
    rows = db_execute(query, (base_part_number, 1), fetch='all') or []
    return [dict(row) for row in rows]



def get_purchase_order_id_from_line(line_id):
    row = db_execute('SELECT purchase_order_id FROM purchase_order_lines WHERE id = ?', (line_id,), fetch='one')
    return row['purchase_order_id'] if row else None



def update_sales_order_line_ship_date(so_line_id, new_ship_date):
    db_execute('''
        UPDATE sales_order_lines
        SET ship_date = ?
        WHERE id = ?
    ''', (new_ship_date.strftime('%Y-%m-%d'), so_line_id), commit=True)


def get_sales_order_lines_with_po(sales_order_id):
    query = '''
        SELECT sol.*, po.id AS purchase_order_id, s.name AS supplier_name, 
               sol.ship_date AS ship_date
        FROM sales_order_lines sol
        LEFT JOIN purchase_order_lines pol ON pol.sales_order_line_id = sol.id
        LEFT JOIN purchase_orders po ON po.id = pol.purchase_order_id
        LEFT JOIN suppliers s ON po.supplier_id = s.id
        WHERE sol.sales_order_id = ?
    '''

    rows = db_execute(query, (sales_order_id,), fetch='all') or []
    logging.debug("Fetched %s sales order lines with PO", len(rows))

    result = []
    for row in rows:
        d = dict(row)
        ship_date = d.get('ship_date')
        if ship_date is None:
            d['ship_date'] = ''
        elif isinstance(ship_date, (datetime.datetime, datetime.date)):
            d['ship_date'] = ship_date.strftime('%Y-%m-%d')
        else:
            # already a string
            d['ship_date'] = str(ship_date)
        result.append(d)

    return result


from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from flask import make_response


def generate_sales_order_acknowledgment(sales_order):
    # Create a response object to serve the PDF
    response = make_response()
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=sales_order_{sales_order["id"]}_acknowledgment.pdf'

    # Create the PDF object
    pdf_canvas = canvas.Canvas(response, pagesize=A4)

    # Start adding content to the PDF
    pdf_canvas.setFont("Helvetica", 12)

    # Example: Add a title
    pdf_canvas.drawString(100, 800, f"Sales Order Acknowledgment - #{sales_order['id']}")

    # Example: Add Customer Information
    pdf_canvas.drawString(100, 780, f"Customer: {sales_order['customer_name']}")
    pdf_canvas.drawString(100, 760, f"Order Date: {sales_order['date_entered']}")
    pdf_canvas.drawString(100, 740, f"Total Value: ${sales_order['total_value']}")

    # Add footer
    pdf_canvas.drawString(100, 100, "Thank you for your business!")

    # Finalize the PDF
    pdf_canvas.showPage()
    pdf_canvas.save()

    return response


import os
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


import os
import pdfkit
from flask import render_template

import os
import pdfkit
from flask import render_template

def generate_sales_order_acknowledgment_file(sales_order):
    acknowledgment_row = db_execute('SELECT COUNT(*) as count FROM acknowledgments WHERE sales_order_id = ?', (sales_order['id'],), fetch='one')
    acknowledgment_count = acknowledgment_row['count'] if acknowledgment_row else 0

    version = acknowledgment_count + 1
    file_path = f"./static/pdfs/sales_order_{sales_order['id']}_acknowledgment_v{version}.pdf"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    delivery_address = db_execute('''
        SELECT address, city, postal_code, country 
        FROM customer_addresses
        WHERE customer_id = ? AND is_default_shipping = 1
    ''', (sales_order['customer_id'],), fetch='one')

    invoicing_address = db_execute('''
        SELECT address, city, postal_code, country 
        FROM customer_addresses
        WHERE customer_id = ? AND is_default_invoicing = 1
    ''', (sales_order['customer_id'],), fetch='one')

    logging.debug("Delivery Address: %s", delivery_address)
    logging.debug("Invoicing Address: %s", invoicing_address)

    rendered_html = render_template('acknowledgment.html',
                                    sales_order=sales_order,
                                    order_lines=sales_order['sales_order_lines'],
                                    delivery_address=delivery_address,
                                    invoicing_address=invoicing_address,
                                    seller_info={
                                        'name': 'Your Company Name',
                                        'address': '123 Business Road',
                                        'city': 'City',
                                        'postal_code': 'Postal Code',
                                        'country': 'Country',
                                        'email': 'info@yourcompany.com',
                                        'phone': '+44 1234 567890'
                                    })

    path_to_wkhtmltopdf = r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe'
    config = pdfkit.configuration(wkhtmltopdf=path_to_wkhtmltopdf)
    pdfkit.from_string(rendered_html, file_path, configuration=config)
    return file_path



def dupdate_sales_order_line_status(line_id, status_id):
    try:
        db_execute('''
            UPDATE sales_order_lines
            SET sales_status_id = ?
            WHERE id = ?
        ''', (status_id, line_id), commit=True)
        return True
    except Exception as e:
        logging.error("Error updating sales order line status: %s", e)
        return False


def get_sales_order_lines_with_status(sales_order_id):
    query = '''
        SELECT sol.*, ss.status_name
        FROM sales_order_lines sol
        LEFT JOIN sales_statuses ss ON sol.sales_status_id = ss.id
        WHERE sol.sales_order_id = ?
    '''
    rows = db_execute(query, (sales_order_id,), fetch='all') or []
    return [dict(line) for line in rows]


def get_sales_order_lines_with_status_and_po(sales_order_id):
    query = '''
        SELECT sol.*, ss.status_name, po.purchase_order_ref, po.supplier_id, s.name as supplier_name
        FROM sales_order_lines sol
        LEFT JOIN sales_statuses ss ON sol.sales_status_id = ss.id
        LEFT JOIN purchase_order_lines pol ON sol.id = pol.purchase_order_line_id
        LEFT JOIN purchase_orders po ON pol.purchase_order_id = po.id
        LEFT JOIN suppliers s ON po.supplier_id = s.id
        WHERE sol.sales_order_id = ?
    '''
    rows = db_execute(query, (sales_order_id,), fetch='all') or []
    return [dict(line) for line in rows]

def get_purchase_suggestions():
    query = '''
        SELECT 
            sol.id as sales_order_line_id,
            sol.base_part_number,
            sol.quantity,
            rfq.chosen_supplier,
            COALESCE(s.name, 'Unknown Supplier') as supplier_name,
            o.supplier_reference,
            so.sales_order_ref,
            c.name as customer_name
        FROM sales_order_lines sol
        LEFT JOIN rfq_lines rfq ON sol.rfq_line_id = rfq.id
        LEFT JOIN offers o ON rfq.offer_id = o.id
        LEFT JOIN suppliers s ON rfq.chosen_supplier = s.id
        LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
        LEFT JOIN customers c ON so.customer_id = c.id
        LEFT JOIN purchase_order_lines pol ON sol.id = pol.sales_order_line_id
        WHERE pol.id IS NULL  -- No purchase order assigned
        AND rfq.chosen_supplier IS NOT NULL  -- Only include lines with a known supplier
        ORDER BY s.name, sol.base_part_number
        LIMIT 50
    '''

    rows = db_execute(query, fetch='all') or []
    return [dict(line) for line in rows]

def get_rfq_lines_for_part_and_customer(base_part_number, customer_id):
    query = '''
        SELECT rl.id, rl.base_part_number, rl.quantity, rl.cost, rl.supplier_lead_time, s.name as supplier_name
        FROM rfq_lines rl
        JOIN rfqs rf ON rl.rfq_id = rf.id
        JOIN suppliers s ON rl.chosen_supplier = s.id
        WHERE rl.base_part_number = ? AND rf.customer_id = ?
    '''
    rows = db_execute(query, (base_part_number, customer_id), fetch='all') or []
    return [dict(line) for line in rows]

def get_sales_order_lines_with_rfq_options(sales_order_id):
    query = '''
        SELECT sol.*, ss.status_name, po.purchase_order_ref, po.supplier_id, s.name as supplier_name, 
               rfq.id as rfq_line_id, rfq.cost, rfq.supplier_lead_time, s.name as supplier_name
        FROM sales_order_lines sol
        LEFT JOIN sales_statuses ss ON sol.sales_status_id = ss.id
        LEFT JOIN purchase_order_lines pol ON sol.id = pol.purchase_order_line_id
        LEFT JOIN purchase_orders po ON pol.purchase_order_id = po.id
        LEFT JOIN rfq_lines rfq ON sol.rfq_line_id = rfq.id
        LEFT JOIN suppliers s ON rfq.chosen_supplier = s.id
        WHERE sol.sales_order_id = ?
    '''
    rows = db_execute(query, (sales_order_id,), fetch='all') or []
    result = []
    for row in rows:
        line = dict(row)
        line['rfq_options'] = get_rfq_lines_for_part_and_customer(line['base_part_number'], line['customer_id'])
        result.append(line)
    return result

def insert_purchase_order_line_from_suggestion(purchase_order_id, sales_order_line_id):
    # Fetch the necessary data from the RFQ, offer, and offer lines for the sales order line
    query = '''
        SELECT 
            sol.base_part_number, 
            sol.quantity, 
            ol.price, 
            ol.lead_time
        FROM sales_order_lines sol
        LEFT JOIN rfq_lines rl ON sol.rfq_line_id = rl.id
        LEFT JOIN offers o ON rl.offer_id = o.id
        LEFT JOIN offer_lines ol ON (o.id = ol.offer_id AND ol.base_part_number = sol.base_part_number)
        WHERE sol.id = ?
    '''

    rfq_line_data = db_execute(query, (sales_order_line_id,), fetch='one')

    if not rfq_line_data:
        raise ValueError(f"No RFQ line data found for sales order line ID: {sales_order_line_id}")

    if rfq_line_data['price'] is None:
        price_query = '''
            SELECT ol.price
            FROM sales_order_lines sol
            JOIN rfq_lines rl ON sol.rfq_line_id = rl.id
            JOIN offers o ON rl.offer_id = o.id
            JOIN offer_lines ol ON (o.id = ol.offer_id AND ol.base_part_number = sol.base_part_number)
            WHERE sol.id = ?
        '''
        price_result = db_execute(price_query, (sales_order_line_id,), fetch='one')

        if not price_result or price_result['price'] is None:
            raise ValueError(f"No price found for sales order line ID: {sales_order_line_id}")

        price = price_result['price']
    else:
        price = rfq_line_data['price']

    base_part_number = rfq_line_data['base_part_number']
    quantity = rfq_line_data['quantity']

    line_number_query = '''
        SELECT COALESCE(MAX(line_number), 0) + 1 as next_line_number
        FROM purchase_order_lines
        WHERE purchase_order_id = ?
    '''
    next_line_number_row = db_execute(line_number_query, (purchase_order_id,), fetch='one')
    next_line_number = next_line_number_row['next_line_number'] if next_line_number_row else 1

    insert_query = '''
        INSERT INTO purchase_order_lines (
            purchase_order_id, line_number, base_part_number, quantity, price, sales_order_line_id, 
            status_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        RETURNING id
    '''
    row = db_execute(
        insert_query,
        (purchase_order_id, next_line_number, base_part_number, quantity, price, sales_order_line_id, 1),
        fetch='one',
        commit=True,
    )
    if not row:
        raise RuntimeError("Failed to insert purchase order line from suggestion")
    return row.get('id', list(row.values())[0])

def get_addresses_by_customer(customer_id):
    return query_all('SELECT * FROM customer_addresses WHERE customer_id = ?', (customer_id,))

def generate_breadcrumbs(*crumbs):
    breadcrumbs = []
    for crumb, path in crumbs:
        breadcrumbs.append((crumb, path))
    return breadcrumbs

def insert_project(customer_id, salesperson_id, name, description, status_id=1):
    row = db_execute(
        'INSERT INTO projects (customer_id, salesperson_id, name, description, status_id) VALUES (?, ?, ?, ?, ?) RETURNING id',
        (customer_id, salesperson_id, name, description, status_id),
        fetch='one',
        commit=True,
    )
    if row is None:
        raise RuntimeError("Failed to insert project")
    return row.get('id', list(row.values())[0])


def update_project(project_id, customer_id, salesperson_id, name, description, status_id):
    db_execute(
        '''
        UPDATE projects
        SET name = ?, description = ?, customer_id = ?, salesperson_id = ?, status_id = ?
        WHERE id = ?
        ''',
        (name, description, customer_id, salesperson_id, status_id, project_id),
        commit=True,
    )

def get_project_by_id(project_id):
    project = query_one('''
        SELECT p.id, p.name, p.description, p.customer_id, c.name AS customer_name, 
               p.salesperson_id, s.name AS salesperson_name, 
               p.status_id, ps.status AS status_name
        FROM projects p
        JOIN customers c ON p.customer_id = c.id
        JOIN salespeople s ON p.salesperson_id = s.id
        JOIN project_statuses ps ON p.status_id = ps.id
        WHERE p.id = ?
    ''', (project_id,))
    return dict(project) if project else None

def get_projects(salesperson_id=None):
    query = '''
        SELECT p.id, p.name, p.customer_id, c.name AS customer_name, 
               p.salesperson_id, s.name AS salesperson_name, 
               p.status_id, ps.status AS status_name,
               p.next_stage_id, stage.name AS next_stage_name,
               p.next_stage_deadline, p.estimated_value,
               p.description
        FROM projects p
        JOIN customers c ON p.customer_id = c.id
        LEFT JOIN salespeople s ON p.salesperson_id = s.id
        JOIN project_statuses ps ON p.status_id = ps.id
        LEFT JOIN project_stages stage ON p.next_stage_id = stage.id
        {}
    '''.format('WHERE p.salesperson_id = ?' if salesperson_id else '')

    projects = query_all(query, (salesperson_id,) if salesperson_id else ())

    projects_with_updates = []
    for project in projects:
        project_dict = dict(project)
        project_dict['stages'] = get_project_stages(project['id'])
        updates = get_project_updates(project['id'])
        project_dict['updates'] = updates
        project_dict['most_recent_update'] = updates[0] if updates else None
        projects_with_updates.append(project_dict)

    return projects_with_updates

# Project status functions
def get_project_statuses():
    statuses = query_all('SELECT * FROM project_statuses')
    return [dict(status) for status in statuses]

# Project updates functions
def insert_project_update(project_id, salesperson_id, comment):
    db_execute(
        'INSERT INTO project_updates (project_id, salesperson_id, comment, date_created) VALUES (?, ?, ?, CURRENT_TIMESTAMP)',
        (project_id, salesperson_id, comment),
        commit=True,
    )

def get_project_updates(project_id):
    updates = query_all('''
        SELECT pu.id, pu.comment, pu.date_created, pu.stage_id, s.name AS salesperson_name
        FROM project_updates pu
        LEFT JOIN salespeople s ON pu.salesperson_id = s.id
        WHERE pu.project_id = ?
        ORDER BY pu.date_created DESC
    ''', (project_id,))
    return [dict(update) for update in updates]



def link_rfq_to_project(project_id, rfq_id):
    """
    Creates a link between an RFQ and a project.
    """
    try:
        db_execute('INSERT INTO project_rfqs (project_id, rfq_id) VALUES (?, ?)', (project_id, rfq_id), commit=True)
        return True
    except Exception as e:
        print(f"Error linking RFQ to project: {e}")
        return False

# File to Project relationship functions
def link_file_to_project(project_id, file_id):
    db_execute('INSERT INTO project_files (project_id, file_id) VALUES (?, ?)', (project_id, file_id), commit=True)

def get_files_for_project(project_id):
    # Join the `project_files` table with the `files` table to get full file information, including description
    files = query_all('''
        SELECT f.id, f.filename, f.filepath, f.upload_date, f.description
        FROM project_files pf
        JOIN files f ON pf.file_id = f.id
        WHERE pf.project_id = ?
    ''', (project_id,))
    return [dict(file) for file in files]



# Insert file for project use case, excluding rfq_id
def insert_file_for_project(filename, filepath, upload_date):
    row = db_execute(
        'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?) RETURNING id',
        (filename, filepath, upload_date),
        fetch='one',
        commit=True,
    )
    if row is None:
        raise RuntimeError("Failed to insert file")
    return row.get('id', list(row.values())[0])


def insert_file_for_project_stage(stage_id, filename, filepath, upload_date):
    with db_cursor(commit=True) as cur:
        cur.execute(
            'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?) RETURNING id',
            (filename, filepath, upload_date),
        )
        row = cur.fetchone()
        file_id = row['id'] if row and isinstance(row, dict) and 'id' in row else (list(row.values())[0] if row else None)

        cur.execute(
            'INSERT INTO stage_files (stage_id, file_id) VALUES (?, ?)',
            (stage_id, file_id),
        )
    return file_id


def insert_project_stage(project_id, name, description=None, parent_stage_id=None, status_id=1, due_date=None, recurrence_id=None):
    row = db_execute(
        '''
        INSERT INTO project_stages (project_id, name, description, parent_stage_id, status_id, date_created, due_date, recurrence_id)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        RETURNING id
        ''',
        (project_id, name, description, parent_stage_id, status_id, due_date, recurrence_id),
        fetch='one',
        commit=True,
    )
    if row is None:
        raise RuntimeError("Failed to insert project stage")
    return row.get('id', list(row.values())[0])

def get_project_stages(project_id):
    stages = query_all('''
        WITH RECURSIVE stage_hierarchy AS (
            -- Fetch top-level stages that are not deleted
            SELECT id, name, description, parent_stage_id, status_id, due_date
            FROM project_stages
            WHERE project_id = ? AND parent_stage_id IS NULL AND status_id != 3
            UNION ALL
            -- Fetch sub-stages that are not deleted and join with their parent stages
            SELECT ps.id, ps.name, ps.description, ps.parent_stage_id, ps.status_id, ps.due_date
            FROM project_stages ps
            JOIN stage_hierarchy sh ON ps.parent_stage_id = sh.id
            WHERE ps.status_id != 3  -- Exclude deleted sub-stages
        )
        -- Final selection of stages with status names
        SELECT sh.id, sh.name, sh.description, sh.parent_stage_id, sh.status_id, sh.due_date, ps.status AS status_name
        FROM stage_hierarchy sh
        LEFT JOIN project_statuses ps ON sh.status_id = ps.id
        ORDER BY sh.parent_stage_id, sh.id
    ''', (project_id,))

    # Convert result rows to dictionaries
    stages = [dict(stage) for stage in stages]

    # Clean up the data - ensure None values are actually None, not the string 'None'
    for stage in stages:
        # Handle the case where description might be the string 'None' instead of actual None
        if stage.get('description') == 'None' or stage.get('description') == '':
            stage['description'] = None
        # Also ensure empty strings are treated as None
        elif stage.get('description') is not None and stage['description'].strip() == '':
            stage['description'] = None

    # Organize stages into a hierarchy (parent-child relationships)
    stage_map = {stage['id']: stage for stage in stages}
    root_stages = []

    for stage in stages:
        parent_id = stage.get('parent_stage_id')
        if parent_id:
            parent = stage_map.get(parent_id)
            if 'substages' not in parent:
                parent['substages'] = []
            parent['substages'].append(stage)
        else:
            root_stages.append(stage)  # Top-level stages

    return root_stages


def update_project_stage(stage_id, name, description, status_id):
    try:
        db_execute(
            '''
            UPDATE project_stages 
            SET name = ?, description = ?, status_id = ?
            WHERE id = ?
            ''',
            (name, description, status_id, stage_id),
            commit=True,
        )
        return True
    except Exception as e:
        logging.error("Error updating stage: %s", e)
        return False


def delete_project_stage(stage_id):
    db_execute('DELETE FROM project_stages WHERE id = ? OR parent_stage_id = ?', (stage_id, stage_id), commit=True)

def get_project_stage_by_id(stage_id):
    row = db_execute('SELECT * FROM project_stages WHERE id = ?', (stage_id,), fetch='one')
    return dict_from_row(row) if row else None

def has_substages(stage_id):
    row = db_execute('SELECT COUNT(*) as count FROM project_stages WHERE parent_stage_id = ?', (stage_id,), fetch='one')
    return bool(row and row['count'] > 0)


def get_file_by_id(file_id):
    row = db_execute('SELECT * FROM files WHERE id = ?', (file_id,), fetch='one')
    return dict_from_row(row) if row else None

def get_industries():
    rows = db_execute('SELECT id, name FROM industries', fetch='all') or []
    return [dict(row) for row in rows]


def get_customers_with_tags(salesperson_id, search_term=None, status_filter=None, sort_column='name', sort_order='asc'):
    """
    Get customers with their tags included
    """
    # Base query to get customer data
    query = '''
        SELECT DISTINCT 
            c.id,
            c.name,
            c.customer_status,
            c.historical_spend,
            c.estimated_revenue,
            c.fleet_size,
            c.latest_update,
            c.latest_update_date,
            c.active_rfqs,
            c.most_recent_order_number,
            c.most_recent_order_date,
            c.most_recent_order_value
        FROM customers c
        WHERE c.salesperson_id = ?
    '''

    params = [salesperson_id]

    # Add search filter
    if search_term:
        query += ' AND c.name LIKE ?'
        params.append(f'%{search_term}%')

    # Add status filter
    if status_filter:
        query += ' AND c.customer_status = ?'
        params.append(status_filter)

    # Add sorting
    valid_sort_columns = ['name', 'customer_status', 'historical_spend', 'estimated_revenue', 'fleet_size',
                          'active_rfqs', 'most_recent_order']
    if sort_column in valid_sort_columns:
        sort_order = 'ASC' if sort_order.lower() == 'asc' else 'DESC'

        if sort_column == 'most_recent_order':
            query += f' ORDER BY c.most_recent_order_date {sort_order}'
        else:
            query += f' ORDER BY c.{sort_column} {sort_order}'

    rows = db_execute(query, tuple(params), fetch='all') or []

    # Convert to list of dictionaries and add tags
    customers_list = []
    for customer in rows:
        customer_dict = dict(customer)
        # Get tags for this customer
        customer_dict['tags'] = get_tags_by_customer_id(customer['id'])
        customers_list.append(customer_dict)
    return customers_list


# Alternative approach if you want to do it in a single query (more efficient)
def get_customers_with_tags_single_query(salesperson_id, search_term=None, status_filter=None, sort_column='name',
                                         sort_order='asc'):
    """
    Get customers with their tags in a single query using GROUP_CONCAT
    """
    query = '''
        SELECT 
            c.id,
            c.name,
            c.customer_status,
            c.historical_spend,
            c.estimated_revenue,
            c.fleet_size,
            c.latest_update,
            c.latest_update_date,
            c.active_rfqs,
            c.most_recent_order_number,
            c.most_recent_order_date,
            c.most_recent_order_value,
            GROUP_CONCAT(it.tag, ',') as tags_string
        FROM customers c
        LEFT JOIN customer_industry_tags cit ON c.id = cit.customer_id
        LEFT JOIN industry_tags it ON cit.tag_id = it.id
        WHERE c.salesperson_id = ?
    '''

    params = [salesperson_id]

    # Add search filter
    if search_term:
        query += ' AND c.name LIKE ?'
        params.append(f'%{search_term}%')

    # Add status filter
    if status_filter:
        query += ' AND c.customer_status = ?'
        params.append(status_filter)

    # Group by customer
    query += ' GROUP BY c.id'

    # Add sorting
    valid_sort_columns = ['name', 'customer_status', 'historical_spend', 'estimated_revenue', 'fleet_size',
                          'active_rfqs', 'most_recent_order']
    if sort_column in valid_sort_columns:
        sort_order = 'ASC' if sort_order.lower() == 'asc' else 'DESC'

        if sort_column == 'most_recent_order':
            query += f' ORDER BY c.most_recent_order_date {sort_order}'
        else:
            query += f' ORDER BY c.{sort_column} {sort_order}'

    rows = db_execute(query, tuple(params), fetch='all') or []

    # Convert to list of dictionaries and parse tags
    customers_list = []
    for customer in rows:
        customer_dict = dict(customer)
        # Parse the tags string into a list
        if customer_dict['tags_string']:
            customer_dict['tags'] = customer_dict['tags_string'].split(',')
        else:
            customer_dict['tags'] = []
        # Remove the tags_string field as we don't need it
        del customer_dict['tags_string']
        customers_list.append(customer_dict)

    return customers_list

def get_tags_by_customer_id(customer_id):
    tags = query_all(
        '''
        SELECT industry_tags.tag 
        FROM industry_tags
        JOIN customer_industry_tags ON industry_tags.id = customer_industry_tags.tag_id
        WHERE customer_industry_tags.customer_id = ?
        ''',
        (customer_id,),
    )
    tag_list = [tag['tag'] for tag in tags]
    print(f"Retrieved tags for customer {customer_id}: {tag_list}")  # Added debug print
    return tag_list

def insert_customer_industry(customer_id, industry_id):
    db_execute('INSERT INTO customer_industries (customer_id, industry_id) VALUES (?, ?)', (customer_id, industry_id), commit=True)

    # Debugging: Print a confirmation message
    print(f"Inserted industry {industry_id} for customer {customer_id}")



def insert_customer_tags(customer_id, tags):
    with db_cursor(commit=True) as cur:
        for tag in tags:
            tag = tag.strip()
            if not tag:
                continue

            tag_row = cur.execute('SELECT id FROM industry_tags WHERE tag = ?', (tag,)).fetchone()
            if not tag_row:
                cur.execute('INSERT INTO industry_tags (tag) VALUES (?) RETURNING id', (tag,))
                inserted = cur.fetchone()
                tag_id = inserted['id'] if inserted and isinstance(inserted, dict) and 'id' in inserted else (list(inserted.values())[0] if inserted else None)
            else:
                tag_id = tag_row['id'] if isinstance(tag_row, dict) else tag_row[0]

            cur.execute('INSERT INTO customer_industry_tags (customer_id, tag_id) VALUES (?, ?)', (customer_id, tag_id))

def insert_customer_tag(customer_id, tag_id):
    db_execute(
        'INSERT INTO customer_industry_tags (customer_id, tag_id) VALUES (?, ?)',
        (customer_id, tag_id),
        commit=True,
    )

def delete_customer_tags(customer_id):
    db_execute('DELETE FROM customer_industry_tags WHERE customer_id = ?', (customer_id,), commit=True)

def delete_customer_tag(customer_id, tag_id):
    db_execute(
        'DELETE FROM customer_industry_tags WHERE customer_id = ? AND tag_id = ?',
        (customer_id, tag_id),
        commit=True,
    )

def update_customer_industry(customer_id, industry_id):
    with db_cursor(commit=True) as cur:
        cur.execute('DELETE FROM customer_industries WHERE customer_id = ?', (customer_id,))
        cur.execute('INSERT INTO customer_industries (customer_id, industry_id) VALUES (?, ?)', (customer_id, industry_id))

def get_customer_industry(customer_id):
    industries = query_all('''
        SELECT i.id, i.name
        FROM industries i
        JOIN customer_industries ci ON i.id = ci.industry_id
        WHERE ci.customer_id = ?
    ''', (customer_id,))
    industry_list = [dict(industry) for industry in industries]
    print(f"Retrieved industries for customer {customer_id}: {industry_list}")
    return industry_list

def get_customer_industries(customer_id):
    industries = query_all('''
        SELECT industries.id, industries.name 
        FROM industries 
        JOIN customer_industries ON industries.id = customer_industries.industry_id 
        WHERE customer_industries.customer_id = ?
    ''', (customer_id,))
    return industries

def delete_customer_industries(customer_id):
    db_execute('DELETE FROM customer_industries WHERE customer_id = ?', (customer_id,), commit=True)


def insert_rfq_from_macro(customer_id, contact_id, customer_ref, currency, status, email_content=None):
    try:
        row = db_execute(
            'INSERT INTO rfqs (entered_date, customer_id, contact_id, customer_ref, currency, status, email) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id',
            (date.today().isoformat(), customer_id, contact_id, customer_ref, currency, status, email_content),
            fetch='one',
            commit=True,
        )
        rfq_id = row.get('id', list(row.values())[0]) if row else None
        logging.info(f"Inserted new RFQ with ID: {rfq_id}")
        return rfq_id
    except Exception as e:
        logging.error(f"Error inserting RFQ: {e}")
        raise

# Fetch part number by base_part_number
def get_part_number_by_id(base_part_number):
    part_number = query_one('SELECT * FROM part_numbers WHERE base_part_number = ?', (base_part_number,))
    return dict(part_number) if part_number else None

# Fetch RFQ lines by base_part_number with customer and price information
def get_rfq_lines_by_part_number(base_part_number):
    rfq_lines = query_all('''
        SELECT rfq.id, rfq.entered_date, rfq_lines.line_number, rfq_lines.quantity,
               customers.name as customer, rfq_lines.price
        FROM rfq_lines
        JOIN rfqs rfq ON rfq_lines.rfq_id = rfq.id
        JOIN customers ON rfq.customer_id = customers.id
        WHERE rfq_lines.base_part_number = ?
    ''', (base_part_number,))
    return [dict(line) for line in rfq_lines]


# Fetch sales order lines by base_part_number with customer information
def get_sales_order_lines_by_part_number(base_part_number):
    sales_order_lines = query_all('''
        SELECT sol.id, sol.line_number, sol.quantity, sol.price, customers.name as customer
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        JOIN customers ON so.customer_id = customers.id
        WHERE sol.base_part_number = ?
    ''', (base_part_number,))
    return [dict(line) for line in sales_order_lines]


# Fetch requisitions by base_part_number with supplier information
def get_requisitions_by_part_number(base_part_number):
    rows = db_execute('''
        SELECT requisitions.id, requisitions.date, requisitions.quantity,
               suppliers.name as supplier
        FROM requisitions
        JOIN suppliers ON requisitions.supplier_id = suppliers.id
        WHERE requisitions.base_part_number = ?
    ''', (base_part_number,), fetch='all') or []
    return [dict(row) for row in rows]


# Fetch PO lines by base_part_number with supplier information
def get_po_lines_by_part_number(base_part_number):
    rows = db_execute('''
        SELECT po.id, po.purchase_order_ref, po_lines.line_number, po_lines.quantity, po_lines.price, suppliers.name as supplier
        FROM purchase_order_lines po_lines
        JOIN purchase_orders po ON po_lines.purchase_order_id = po.id
        JOIN suppliers ON po.supplier_id = suppliers.id
        WHERE po_lines.base_part_number = ?
    ''', (base_part_number,), fetch='all') or []
    return [dict(line) for line in rows]


# Fetch parts list lines that contain this part number
def get_parts_list_lines_by_part_number(base_part_number):
    rows = db_execute('''
        SELECT
            pll.id as line_id,
            pll.line_number,
            pll.quantity,
            pll.chosen_cost,
            pll.chosen_price,
            pl.id as parts_list_id,
            pl.name as parts_list_name,
            pl.date_created,
            c.name as customer_name,
            c.id as customer_id,
            pls.name as status_name
        FROM parts_list_lines pll
        JOIN parts_lists pl ON pll.parts_list_id = pl.id
        LEFT JOIN customers c ON pl.customer_id = c.id
        LEFT JOIN parts_list_statuses pls ON pl.status_id = pls.id
        WHERE pll.base_part_number = ?
        ORDER BY pl.date_created DESC
    ''', (base_part_number,), fetch='all') or []
    return [dict(row) for row in rows]


# Fetch supplier quotes for this part number
def get_supplier_quotes_by_part_number(base_part_number):
    rows = db_execute('''
        SELECT
            psql.id as quote_line_id,
            psql.quoted_part_number,
            psql.manufacturer,
            psql.quantity_quoted,
            psql.unit_price,
            psql.lead_time_days,
            psql.condition_code,
            psql.is_no_bid,
            psq.id as quote_id,
            psq.quote_reference,
            psq.quote_date,
            s.name as supplier_name,
            s.id as supplier_id,
            pl.id as parts_list_id,
            pl.name as parts_list_name,
            c.name as customer_name
        FROM parts_list_supplier_quote_lines psql
        JOIN parts_list_supplier_quotes psq ON psql.supplier_quote_id = psq.id
        JOIN suppliers s ON psq.supplier_id = s.id
        JOIN parts_list_lines pll ON psql.parts_list_line_id = pll.id
        JOIN parts_lists pl ON pll.parts_list_id = pl.id
        LEFT JOIN customers c ON pl.customer_id = c.id
        WHERE pll.base_part_number = ?
        ORDER BY psq.quote_date DESC
    ''', (base_part_number,), fetch='all') or []
    return [dict(row) for row in rows]


# Fetch BOM lines that contain this part number
def get_bom_lines_by_part_number(base_part_number):
    rows = db_execute('''
        SELECT
            bl.id as bom_line_id,
            bl.quantity,
            bl.reference_designator,
            bl.notes as line_notes,
            bh.id as bom_id,
            bh.name as bom_name,
            bh.description as bom_description,
            bh.type as bom_type,
            bh.base_part_number as assembly_part_number
        FROM bom_lines bl
        JOIN bom_headers bh ON bl.bom_header_id = bh.id
        WHERE bl.base_part_number = ?
        ORDER BY bh.name
    ''', (base_part_number,), fetch='all') or []
    return [dict(row) for row in rows]


# Fetch excess stock lines for this part number
def get_excess_lines_by_part_number(base_part_number):
    rows = db_execute('''
        SELECT
            esl.id as line_id,
            esl.quantity,
            esl.date_code,
            esl.manufacturer,
            el.id as excess_list_id,
            el.entered_date,
            el.status,
            COALESCE(c.name, s.name) as source_name,
            CASE
                WHEN el.customer_id IS NOT NULL THEN 'customer'
                WHEN el.supplier_id IS NOT NULL THEN 'supplier'
                ELSE 'unknown'
            END as source_type
        FROM excess_stock_lines esl
        JOIN excess_stock_lists el ON esl.excess_stock_list_id = el.id
        LEFT JOIN customers c ON el.customer_id = c.id
        LEFT JOIN suppliers s ON el.supplier_id = s.id
        WHERE esl.base_part_number = ?
        ORDER BY el.entered_date DESC
    ''', (base_part_number,), fetch='all') or []
    return [dict(row) for row in rows]


# Fetch manufacturer approvals for this part number
def get_manufacturer_approvals_by_part_number(base_part_number):
    # Query using normalized part numbers for matching
    rows = db_execute('''
        SELECT
            id,
            manufacturer_name,
            manufacturer_code,
            cage_code,
            location,
            country,
            approval_status,
            standard,
            airbus_material,
            manufacturer_part_number,
            data_type,
            p_status,
            p_status_text
        FROM manufacturer_approvals
        WHERE airbus_material_base = ? OR manufacturer_part_number_base = ?
        ORDER BY manufacturer_name
    ''', (base_part_number, base_part_number), fetch='all') or []
    return [dict(row) for row in rows]


def save_email_file_and_create_entries(file, rfq_id=None):
    """
    Handles saving the email file, parsing it, and creating appropriate entries in the database.
    Optionally associates the file with an RFQ if rfq_id is provided.
    """
    filename = secure_filename(file.filename)
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

    file.save(file_path)
    email_content = parse_email(file_path)

    with db_cursor(commit=True) as cur:
        cur.execute('INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?)',
                    (filename, file_path, datetime.now()))
        row = cur.fetchone()
        file_id = row['id'] if row and isinstance(row, dict) and 'id' in row else (list(row.values())[0] if row else None)

        if rfq_id:
            cur.execute('INSERT INTO rfq_files (rfq_id, file_id) VALUES (?, ?)', (rfq_id, file_id))

        if email_content and rfq_id:
            cur.execute('UPDATE rfqs SET email = ? WHERE id = ?', (email_content, rfq_id))


def get_latest_activity(customer_id):
    rfq_row = db_execute("""
        SELECT entered_date FROM rfqs WHERE customer_id = ?
        ORDER BY entered_date DESC LIMIT 1
    """, (customer_id,), fetch='one')
    logging.debug("Customer %s Latest RFQ: %s", customer_id, rfq_row)

    sales_order_row = db_execute("""
        SELECT date_entered FROM sales_orders WHERE customer_id = ?
        ORDER BY date_entered DESC LIMIT 1
    """, (customer_id,), fetch='one')
    logging.debug("Customer %s Latest Sales Order: %s", customer_id, sales_order_row)

    update_row = db_execute("""
        SELECT date FROM customer_updates WHERE customer_id = ?
        ORDER BY date DESC LIMIT 1
    """, (customer_id,), fetch='one')
    logging.debug("Customer %s Latest Update: %s", customer_id, update_row)

    latest_values = [_first_value_from_row(row) for row in (rfq_row, sales_order_row, update_row) if row]
    latest_activity_date = max(latest_values) if latest_values else None

    if latest_activity_date:
        logging.debug("Customer %s Final Latest Activity: %s", customer_id, latest_activity_date)
        return latest_activity_date
    return None


def get_breadcrumbs(stage):
    breadcrumbs = []
    current_stage = stage
    while current_stage:
        breadcrumbs.insert(0, current_stage)
        current_stage = current_stage.parent_stage  # Assuming 'parent_stage' is a reference to the parent stage
    return breadcrumbs

def get_stage(stage_id):
    try:
        stage = db_execute('''
            SELECT s.*, p.name as parent_stage_name, p.id as parent_stage_id
            FROM project_stages s
            LEFT JOIN project_stages p ON s.parent_stage_id = p.id
            WHERE s.id = ?
        ''', (stage_id,), fetch='one')
        if not stage:
            return None

        files = db_execute('''
            SELECT f.*
            FROM files f
            INNER JOIN stage_files sf ON f.id = sf.file_id
            WHERE sf.stage_id = ?
        ''', (stage_id,), fetch='all') or []

        updates = db_execute('SELECT * FROM stage_updates WHERE stage_id = ?', (stage_id,), fetch='all') or []

        return {
            'id': stage['id'],
            'name': stage['name'],
            'description': stage['description'],
            'status_id': stage['status_id'],
            'files': [dict(f) for f in files],
            'updates': [dict(u) for u in updates],
        }
    except Exception as e:
        logging.error("Error getting stage: %s", e)
        return None


def get_stage_by_id(stage_id):
    row = db_execute('''
        SELECT s.*, p.name as parent_stage_name, p.id as parent_stage_id
        FROM project_stages s
        LEFT JOIN project_stages p ON s.parent_stage_id = p.id
        WHERE s.id = ?
    ''', (stage_id,), fetch='one')
    if not row:
        return None

    return {
        'id': row['id'],
        'name': row['name'],
        'description': row['description'],
        'status_id': row['status_id'],
        'due_date': row['due_date'],
        'parent_stage': {
            'id': row['parent_stage_id'],
            'name': row['parent_stage_name']
        } if row.get('parent_stage_id') else None,
    }

def update_stage_name_in_db(stage_id, new_name):
    try:
        db_execute(
            """
            UPDATE project_stages
            SET name = ?
            WHERE id = ?
            """,
            (new_name, stage_id),
            commit=True,
        )
    except Exception as e:
        logging.error("Error updating stage name: %s", e)

# Helper function to handle the email upload and processing
def process_email_and_attachments(record_id, table_name, file):
    try:
        filename = secure_filename(file.filename)
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

        # Save the file
        file.save(file_path)

        # Process the .msg file
        msg = extract_msg.Message(file_path)

        # Extract email content (HTML body or plain text)
        email_content = msg.htmlBody if msg.htmlBody else msg.body
        if msg.htmlBody:
            email_content = msg.htmlBody.decode('utf-8', errors='ignore') if isinstance(msg.htmlBody, bytes) else msg.htmlBody
        elif msg.body:
            email_content = msg.body.replace('\n', '<br>')  # Plain text to HTML

        # Insert the file into the files table
        attachments = []
        with db_cursor(commit=True) as cur:
            cur.execute('INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?) RETURNING id',
                        (filename, file_path, datetime.now()))
            row = cur.fetchone()
            file_id = (row['id'] if isinstance(row, dict) and 'id' in row else (row[0] if row else None))
            if not file_id:
                file_id = getattr(cur, 'lastrowid', None)

            cur.execute(f'INSERT INTO {table_name}_files ({table_name}_id, file_id) VALUES (?, ?)', (record_id, file_id))

            cur.execute(f'UPDATE {table_name} SET email = ? WHERE id = ?', (email_content, record_id))

            for attachment in msg.attachments:
                attachment_filename = secure_filename(attachment.longFilename or attachment.shortFilename)
                attachment_path = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment_filename)

                with open(attachment_path, 'wb') as f:
                    f.write(attachment.data)

                cur.execute('INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?) RETURNING id',
                            (attachment_filename, attachment_path, datetime.now()))
                attachment_row = cur.fetchone()
                attachment_file_id = (attachment_row['id'] if isinstance(attachment_row, dict) and 'id' in attachment_row else (attachment_row[0] if attachment_row else None))
                if not attachment_file_id:
                    attachment_file_id = getattr(cur, 'lastrowid', None)

                cur.execute(f'INSERT INTO {table_name}_files ({table_name}_id, file_id) VALUES (?, ?)', (record_id, attachment_file_id))
                attachments.append({
                    'filename': attachment_filename,
                    'filepath': attachment_path
                })
        return email_content, attachments, None  # Returning attachments for UI use
    except Exception as e:
        return None, None, str(e)

def get_excess_list_by_id1(list_id):
    row = db_execute('SELECT * FROM excess_stock_lists WHERE id = ?', (list_id,), fetch='one')
    return dict(row) if row else None

def get_excess_stock_list_by_id(excess_list_id):
    row = db_execute('''
        SELECT id, email, customer_id, supplier_id, entered_date, status, upload_date,
               mapping, mapping_header_row
        FROM excess_stock_lists 
        WHERE id = ?
    ''', (excess_list_id,), fetch='one')
    if not row:
        return None
    return {
        'id': row['id'],
        'email': row['email'],
        'customer_id': row['customer_id'],
        'supplier_id': row['supplier_id'],
        'entered_date': row['entered_date'],
        'status': row['status'],
        'upload_date': row['upload_date'],
        'mapping': row.get('mapping') if isinstance(row, dict) else row['mapping'],
        'mapping_header_row': row.get('mapping_header_row') if isinstance(row, dict) else row['mapping_header_row'],
    }


def get_excess_list_by_id221(excess_list_id):
    rows = db_execute('''
        SELECT base_part_number, quantity, date_code, manufacturer 
        FROM excess_stock_lines 
        WHERE excess_stock_list_id = ?
    ''', (excess_list_id,), fetch='all') or []

    return [
        {
            'base_part_number': row['base_part_number'],
            'quantity': row['quantity'],
            'date_code': row['date_code'],
            'manufacturer': row['manufacturer'],
        }
        for row in rows
    ]


def insert_excess_stock_line(excess_stock_list_id, base_part_number, quantity, date_code=None, manufacturer=None, unit_price=None, unit_price_currency_id=None, part_number=None):
    db_execute('''
        INSERT INTO excess_stock_lines (
            excess_stock_list_id, base_part_number, quantity, date_code, manufacturer,
            unit_price, unit_price_currency_id, part_number
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (excess_stock_list_id, base_part_number, quantity, date_code, manufacturer, unit_price, unit_price_currency_id, part_number), commit=True)


def get_excess_stock_list_id_by_file(file_id):
    try:
        row = db_execute('''
            SELECT excess_stock_list_id 
            FROM excess_stock_files 
            WHERE file_id = ?
        ''', (file_id,), fetch='one')
        return row['excess_stock_list_id'] if row else None
    except Exception as e:
        logging.error("Error fetching excess_stock_list_id for file_id %s: %s", file_id, e)
        return None

def get_excess_list_line_by_id(line_id):
    row = db_execute('SELECT * FROM excess_list_lines WHERE id = ?', (line_id,), fetch='one')
    return dict(row) if row else None

def match_rfq_lines(excess_stock_list_id):
    # Fetch all excess stock lines for the given excess stock list
    excess_lines = get_excess_stock_lines(excess_stock_list_id)

    matches = []
    for line in excess_lines:
        base_part_number = line['base_part_number']

        # Find matching RFQ lines
        rfq_lines = find_rfq_lines_by_part_number(base_part_number)

        if rfq_lines:
            matches.append({
                'excess_line': line,
                'rfq_lines': rfq_lines
            })

    return matches

def match_sales_order_lines(excess_stock_list_id):
    # Fetch all excess stock lines for the given excess stock list
    excess_lines = get_excess_stock_lines(excess_stock_list_id)

    matches = []
    for line in excess_lines:
        base_part_number = line['base_part_number']

        # Find matching Sales Order lines
        sales_order_lines = find_sales_order_lines_by_part_number(base_part_number)

        if sales_order_lines:
            matches.append({
                'excess_line': line,
                'sales_order_lines': sales_order_lines
            })

    return matches

def get_excess_stock_lines(excess_stock_list_id):
    rows = db_execute('''
        SELECT id, base_part_number, quantity, date_code, manufacturer 
        FROM excess_stock_lines 
        WHERE excess_stock_list_id = ?
    ''', (excess_stock_list_id,), fetch='all') or []
    return [dict(row) for row in rows]

def find_rfq_lines_by_part_number(base_part_number):
    query = """
    SELECT rfq_lines.id, rfq_lines.base_part_number, rfq_lines.quantity, 
           rfq_lines.lead_time, rfqs.customer_id
    FROM rfq_lines
    JOIN rfqs ON rfq_lines.rfq_id = rfqs.id
    WHERE rfq_lines.base_part_number = ?
    """
    rows = db_execute(query, (base_part_number,), fetch='all') or []
    return [dict(row) for row in rows]

def find_sales_order_lines_by_part_number(base_part_number):
    query = """
    SELECT sales_order_lines.id, sales_order_lines.base_part_number, 
           sales_order_lines.quantity, sales_order_lines.delivery_date, 
           sales_orders.customer_id
    FROM sales_order_lines
    JOIN sales_orders ON sales_order_lines.sales_order_id = sales_orders.id
    WHERE sales_order_lines.base_part_number = ?
    """
    rows = db_execute(query, (base_part_number,), fetch='all') or []
    return [dict(row) for row in rows]

def get_supplier_by_email(email):
    row = db_execute("SELECT * FROM suppliers WHERE contact_email = ?", (email,), fetch='one')
    return dict(row) if row else None


def get_nested_tags():
    results = db_execute("""
        WITH RECURSIVE nested_tags AS (
            SELECT 
                id, 
                tag as name, 
                parent_tag_id, 
                0 as level,
                tag as path
            FROM industry_tags 
            WHERE parent_tag_id IS NULL

            UNION ALL

            SELECT 
                t.id, 
                t.tag as name, 
                t.parent_tag_id, 
                nt.level + 1,
                nt.path || ' > ' || t.tag
            FROM industry_tags t
            JOIN nested_tags nt ON t.parent_tag_id = nt.id
        )
        SELECT n.id, n.name, n.parent_tag_id, n.level, n.path,
               COUNT(DISTINCT cit.customer_id) as customer_count
        FROM nested_tags n
        LEFT JOIN customer_industry_tags cit ON cit.tag_id = n.id
        GROUP BY n.id, n.name, n.parent_tag_id, n.level, n.path
        ORDER BY n.path;
    """, fetch='all') or []

    # Convert the flat list to a hierarchical structure
    tags_dict = {}
    for row in results:
        tags_dict[row['id']] = {
            'id': row['id'],
            'name': row['name'],
            'level': row['level'],
            'parent_id': row['parent_tag_id'],
            'customer_count': row['customer_count'],
            'children': []
        }

    # Build the hierarchy
    root_tags = []
    for tag in tags_dict.values():
        if tag['parent_id'] is None:
            root_tags.append(tag)
        else:
            parent = tags_dict.get(tag['parent_id'])
            if parent:
                parent['children'].append(tag)

    return root_tags

def get_child_tags(parent_id):
    return db_execute("""
        WITH RECURSIVE child_tags AS (
            SELECT id, tag as name, parent_tag_id
            FROM industry_tags
            WHERE parent_tag_id = ?

            UNION ALL

            SELECT t.id, t.tag as name, t.parent_tag_id
            FROM industry_tags t
            INNER JOIN child_tags ct ON t.parent_tag_id = ct.id
        )
        SELECT * FROM child_tags;
    """, (parent_id,), fetch='all') or []


def get_customers_by_tags(tag_ids):
    placeholders = ','.join(['?' * len(tag_ids)])
    rows = db_execute(f"""
        SELECT DISTINCT c.id, c.name, c.status_id, c.estimated_revenue
        FROM customers c
        JOIN customer_industry_tags ct ON c.id = ct.customer_id
        WHERE ct.tag_id IN ({placeholders})
        ORDER BY c.estimated_revenue DESC
    """, tuple(tag_ids), fetch='all') or []
    return [dict(row) for row in rows]


def get_customers_by_tag(tag_id):
    """Get customers for a specific tag ID, including child tags"""
    logging.debug("get_customers_by_tag called with tag_id: %s", tag_id)

    child_tags_query = '''
        WITH RECURSIVE child_tags AS (
            SELECT id, tag, parent_tag_id
            FROM industry_tags
            WHERE id = ?

            UNION ALL

            SELECT t.id, t.tag, t.parent_tag_id
            FROM industry_tags t
            JOIN child_tags ct ON t.parent_tag_id = ct.id
        )
        SELECT DISTINCT id FROM child_tags
    '''

    tags = db_execute(child_tags_query, (tag_id,), fetch='all') or []
    tag_ids = [row['id'] for row in tags]
    if tag_id not in tag_ids:
        tag_ids.append(tag_id)

    logging.debug("Found tag IDs including children: %s", tag_ids)

    placeholders = ','.join(['?' for _ in tag_ids])
    customers_query = f'''
        SELECT DISTINCT c.id, c.name, c.status_id, c.estimated_revenue
        FROM customers c
        JOIN customer_industry_tags ct ON c.id = ct.customer_id
        WHERE ct.tag_id IN ({placeholders})
        ORDER BY c.estimated_revenue DESC
    '''

    logging.debug("Executing customer query with placeholders: %s", customers_query)
    logging.debug("Query parameters: %s", tag_ids)

    rows = db_execute(customers_query, tuple(tag_ids), fetch='all') or []
    logging.debug("Found %s customers", len(rows))
    return [dict(row) for row in rows]

def get_all_customers():
    rows = db_execute(
        '''
        SELECT id, name, status_id, estimated_revenue 
        FROM customers 
        ORDER BY estimated_revenue DESC
        ''',
        fetch='all',
    ) or []
    return [dict(row) for row in rows]

def get_tag_description(tag_id):
    row = db_execute(
        "SELECT description FROM industry_tags WHERE id = ?",
        (tag_id,),
        fetch='one',
    )
    return row['description'] if row else None

def get_customer_statuses():
    rows = db_execute('SELECT id, status FROM customer_status ORDER BY id', fetch='all') or []
    return [dict(row) for row in rows]

def get_status_name(status_id):
    """Get status name from customer_status table"""
    row = db_execute(
        'SELECT status FROM customer_status WHERE id = ?',
        (status_id,),
        fetch='one',
    )
    return row['status'] if row else 'Unknown'


with open('country_name_mapping.json', 'r') as f:
    country_name_mapping = json.load(f)


def get_all_countries():
    """Get all countries with full country names, sorted alphabetically by name"""
    # Assuming you have access to your country_name_mapping
    countries = []
    for country_code, country_name in country_name_mapping.items():
        countries.append({
            'code': country_code,
            'name': country_name,
            'display_name': f"{country_name} ({country_code})" if country_name != country_code else country_code
        })

    # Sort by country name
    countries.sort(key=lambda x: x['name'])
    return countries

def get_countries_by_continent() -> Dict[str, List[str]]:
    """
    Returns a dictionary mapping continents to their country codes
    """
    # Your existing continent-to-country mapping
    continent_mapping = {
        'Europe': [
            'AL', 'AD', 'AT', 'BY', 'BE', 'BA', 'BG', 'HR', 'CZ', 'DK',
            'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IS', 'IE', 'IT', 'LV',
            'LI', 'LT', 'LU', 'MT', 'MD', 'MC', 'ME', 'NL', 'NO', 'PL',
            'PT', 'RO', 'RU', 'SM', 'RS', 'SK', 'SI', 'ES', 'SE', 'CH',
            'UA', 'GB', 'VA'
        ],
        'North America': [
            'CA', 'US', 'MX', 'GL', 'BM', 'PM'
        ],
        'South America': [
            'AR', 'BO', 'BR', 'CL', 'CO', 'EC', 'GF', 'GY', 'PY', 'PE',
            'SR', 'UY', 'VE'
        ],
        'Asia': [
            'AF', 'AM', 'AZ', 'BH', 'BD', 'BT', 'BN', 'KH', 'CN', 'CY',
            'GE', 'IN', 'ID', 'IR', 'IQ', 'IL', 'JP', 'JO', 'KZ', 'KW',
            'KG', 'LA', 'LB', 'MY', 'MV', 'MN', 'MM', 'NP', 'OM', 'PK',
            'PH', 'QA', 'SA', 'SG', 'KR', 'LK', 'SY', 'TW', 'TJ', 'TH',
            'TR', 'TM', 'AE', 'UZ', 'VN', 'YE'
        ],
        'Africa': [
            'DZ', 'AO', 'BJ', 'BW', 'BF', 'BI', 'CM', 'CV', 'CF', 'TD',
            'KM', 'CG', 'CD', 'DJ', 'EG', 'GQ', 'ER', 'ET', 'GA', 'GM',
            'GH', 'GN', 'GW', 'CI', 'KE', 'LS', 'LR', 'LY', 'MG', 'MW',
            'ML', 'MR', 'MU', 'MA', 'MZ', 'NA', 'NE', 'NG', 'RW', 'ST',
            'SN', 'SC', 'SL', 'SO', 'ZA', 'SS', 'SD', 'SZ', 'TZ', 'TG',
            'TN', 'UG', 'ZM', 'ZW'
        ],
        'Oceania': [
            'AU', 'FJ', 'KI', 'MH', 'FM', 'NR', 'NZ', 'PW', 'PG', 'WS',
            'SB', 'TO', 'TV', 'VU'
        ]
    }
    return continent_mapping


def get_country_name(country_code: str) -> str:
    """
    Convert country code to full name using the pre-generated mapping
    """
    return country_name_mapping.get(country_code, country_code)


def get_available_countries(continent: str, tag_id: str = None) -> List[Dict[str, str]]:
    """
    Get list of countries for a given continent with customer counts, optionally filtered by tag

    Args:
        continent: The continent to get countries for
        tag_id: Optional tag ID to filter customer counts
    """
    # Get the country mapping
    continent_mapping = get_countries_by_continent()
    if continent not in continent_mapping:
        return []

    # Build the query based on whether we have a tag filter
    if tag_id:
        query = """
            SELECT c.country, COUNT(*) as count
            FROM customers c
            JOIN customer_industry_tags ct ON c.id = ct.customer_id
            WHERE ct.tag_id = ?
            GROUP BY c.country
        """
        params = (tag_id,)
    else:
        query = """
            SELECT country, COUNT(*) as count
            FROM customers
            GROUP BY country
        """
        params = ()

    # Get customer counts
    rows = db_execute(query, params, fetch='all') or []
    country_counts = {}
    for row in rows:
        if not row:
            continue
        keys = list(row.keys())
        country_key = 'country' if 'country' in row else keys[0]
        count_key = 'count' if 'count' in row else keys[1] if len(keys) > 1 else keys[0]
        country_counts[row[country_key]] = row[count_key]


    # Combine the mapping with counts
    return [
        {
            'code': code,
            'name': get_country_name(code),
            'customer_count': country_counts.get(code, 0)
        }
        for code in continent_mapping[continent]
    ]

def get_continents() -> List[str]:
    """
    Get list of all continents
    """
    return ['Europe', 'North America', 'South America',
            'Asia', 'Africa', 'Oceania']
