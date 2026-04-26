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

def get_latest_rfq_update(rfq_id):
    """
    Get the latest update for an RFQ

    Args:
        rfq_id: The ID of the RFQ

    Returns:
        The latest update with user information or None if no updates
    """
    db = get_db_connection()
    try:
        cursor = db.execute('''
            SELECT ru.id, ru.rfq_id, ru.user_id, ru.update_text, ru.update_type, 
                   ru.created_at, u.username as user_name
            FROM rfq_updates ru
            JOIN users u ON ru.user_id = u.id
            WHERE ru.rfq_id = ?
            ORDER BY ru.created_at DESC
            LIMIT 1
        ''', (rfq_id,))
        update = cursor.fetchall()
        return [dict(u) for u in update]
    except Exception as e:
        logging.error(f"Error getting latest RFQ update: {e}")
        return None
    finally:
        db.close()


def get_update_types():
    """
    Get available update types

    Returns:
        List of available update types
    """
    return [
        {"id": "comment", "name": "Comment"},
        {"id": "chased", "name": "Chased"},
        {"id": "email", "name": "Email Sent"},
        {"id": "call", "name": "Call Made"}
    ]


# Create a new contact list
def create_contact_list(name, contact_ids=None):
    """
    Create a new contact list with the given name and optionally add contacts.

    Args:
        name (str): The name of the contact list
        contact_ids (list, optional): List of contact IDs to add to the list

    Returns:
        int: The ID of the newly created list
    """
    db = get_db()
    try:
        # Create the list
        cur = db.cursor()
        cur.execute("INSERT INTO contact_lists (name) VALUES (?)", (name,))
        list_id = cur.lastrowid

        # Add contacts if provided
        if contact_ids and isinstance(contact_ids, list) and len(contact_ids) > 0:
            values = [(list_id, contact_id) for contact_id in contact_ids]
            cur.executemany(
                "INSERT INTO contact_list_members (list_id, contact_id) VALUES (?, ?)",
                values
            )

        db.commit()
        return list_id
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


# NOTE: get_table_columns is defined near the top of this file as a cross-database helper.
# The old SQLite-only PRAGMA implementation that used to live here has been removed to
# avoid shadowing the Postgres-ready version.


# Improved get_contact_list_by_id function that handles schema differences
def get_contact_list_by_id(list_id):
    """
    Get a contact list by ID with all its members.

    Args:
        list_id (int): The ID of the contact list

    Returns:
        dict: Contact list information including members
    """
    db = get_db()
    try:
        # First, check if the list exists and get basic info
        cur = db.cursor()
        cur.execute("SELECT id, name FROM contact_lists WHERE id = ?", (list_id,))
        list_data = cur.fetchone()

        if not list_data:
            return None

        # Create the basic list object
        contact_list = {
            'id': list_data[0],
            'name': list_data[1],
            'contacts': [],
            'contact_count': 0
        }

        # Check contact_lists table structure
        contact_lists_columns = get_table_columns('contact_lists')

        # Check contacts table structure
        contacts_columns = get_table_columns('contacts')

        # Check if contact_list_members table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='contact_list_members'")
        contact_list_members_exists = cur.fetchone() is not None

        if contact_list_members_exists:
            # Build dynamic query based on available columns
            select_fields = ['c.id', 'c.name', 'c.email']

            # Add optional fields if they exist
            if 'title' in contacts_columns:
                select_fields.append('c.title')
            else:
                select_fields.append("'' as title")

            # Get customer name if relationship exists
            try:
                cur.execute("SELECT * FROM customers LIMIT 1")
                has_customers = True
            except:
                has_customers = False

            if has_customers and 'customer_id' in contacts_columns:
                select_fields.append('cu.name as customer_name')
                join_customers = "LEFT JOIN customers cu ON c.customer_id = cu.id"
            else:
                select_fields.append("'' as customer_name")
                join_customers = ""

            # Build and execute the query
            query = f"""
                SELECT {', '.join(select_fields)}
                FROM contact_list_members clm
                JOIN contacts c ON clm.contact_id = c.id
                {join_customers}
                WHERE clm.list_id = ?
            """

            cur.execute(query, (list_id,))

            members = []
            for member in cur.fetchall():
                member_dict = {
                    'id': member[0],
                    'name': member[1],
                    'email': member[2]
                }

                # Add title if available
                if 'title' in contacts_columns:
                    member_dict['title'] = member[3]
                    if has_customers:
                        member_dict['customer_name'] = member[4]
                else:
                    if has_customers:
                        member_dict['customer_name'] = member[3]

                members.append(member_dict)

            contact_list['contacts'] = members
            contact_list['contact_count'] = len(members)

        elif 'contact_id' in contact_lists_columns:
            # Use old structure - single contact per list
            cur.execute("SELECT contact_id FROM contact_lists WHERE id = ?", (list_id,))
            result = cur.fetchone()

            if result and result[0]:
                contact_id = result[0]

                # Build dynamic query based on available columns
                select_fields = ['c.id', 'c.name', 'c.email']

                # Add optional fields if they exist
                if 'title' in contacts_columns:
                    select_fields.append('c.title')
                else:
                    select_fields.append("'' as title")

                # Get customer name if relationship exists
                try:
                    cur.execute("SELECT * FROM customers LIMIT 1")
                    has_customers = True
                except:
                    has_customers = False

                if has_customers and 'customer_id' in contacts_columns:
                    select_fields.append('cu.name as customer_name')
                    join_customers = "LEFT JOIN customers cu ON c.customer_id = cu.id"
                else:
                    select_fields.append("'' as customer_name")
                    join_customers = ""

                # Build and execute the query
                query = f"""
                    SELECT {', '.join(select_fields)}
                    FROM contacts c
                    {join_customers}
                    WHERE c.id = ?
                """

                cur.execute(query, (contact_id,))
                contact = cur.fetchone()

                if contact:
                    contact_dict = {
                        'id': contact[0],
                        'name': contact[1],
                        'email': contact[2]
                    }

                    # Add title if available
                    if 'title' in contacts_columns:
                        contact_dict['title'] = contact[3]
                        if has_customers:
                            contact_dict['customer_name'] = contact[4]
                    else:
                        if has_customers:
                            contact_dict['customer_name'] = contact[3]

                    contact_list['contacts'] = [contact_dict]
                    contact_list['contact_count'] = 1
                    contact_list['contact_id'] = contact_id  # For backwards compatibility

        return contact_list
    except Exception as e:
        print(f"Error in get_contact_list_by_id: {str(e)}")
        # Return a minimal valid object even on error
        return {
            'id': list_id,
            'name': 'List unavailable',
            'contacts': [],
            'contact_count': 0,
            'error': str(e)
        }
    finally:
        db.close()

# Get all contact lists with count of members
def get_all_contact_lists():
    """
    Get all contact lists with the count of members in each.

    Returns:
        list: All contact lists with member counts
    """
    db = get_db()
    try:
        cur = db.cursor()
        cur.execute("""
            SELECT cl.id, cl.name, COUNT(clm.contact_id) as contact_count
            FROM contact_lists cl
            LEFT JOIN contact_list_members clm ON cl.id = clm.list_id
            GROUP BY cl.id
            ORDER BY cl.name
        """)

        contact_lists = []
        for row in cur.fetchall():
            contact_lists.append({
                'id': row[0],
                'name': row[1],
                'contact_count': row[2]
            })

        return contact_lists
    finally:
        db.close()


# Add contacts to a list
def add_contacts_to_list(list_id, contact_ids):
    """
    Add multiple contacts to a list.

    Args:
        list_id (int): The ID of the contact list
        contact_ids (list): List of contact IDs to add

    Returns:
        int: Number of contacts added
    """
    if not isinstance(contact_ids, list) or len(contact_ids) == 0:
        return 0

    db = get_db()
    try:
        cur = db.cursor()
        values = [(list_id, contact_id) for contact_id in contact_ids]

        # Use INSERT OR IGNORE to handle duplicates
        cur.executemany(
            "INSERT OR IGNORE INTO contact_list_members (list_id, contact_id) VALUES (?, ?)",
            values
        )

        db.commit()
        return cur.rowcount
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


# Remove contacts from a list
def remove_contacts_from_list(list_id, contact_ids):
    """
    Remove contacts from a list.

    Args:
        list_id (int): The ID of the contact list
        contact_ids (list): List of contact IDs to remove

    Returns:
        int: Number of contacts removed
    """
    if not isinstance(contact_ids, list) or len(contact_ids) == 0:
        return 0

    placeholders = ','.join(['?'] * len(contact_ids))
    query = f"DELETE FROM contact_list_members WHERE list_id = ? AND contact_id IN ({placeholders})"

    db = get_db()
    try:
        cur = db.cursor()
        cur.execute(query, [list_id] + contact_ids)
        db.commit()
        return cur.rowcount
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


# Delete a contact list and all its members
def delete_contact_list(list_id):
    """
    Delete a contact list and all its members.

    Args:
        list_id (int): The ID of the contact list

    Returns:
        bool: True if successful, False otherwise
    """
    db = get_db()
    try:
        cur = db.cursor()
        # With CASCADE, this will also delete all entries in contact_list_members
        cur.execute("DELETE FROM contact_lists WHERE id = ?", (list_id,))
        db.commit()
        return cur.rowcount > 0
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


# Get lists containing a specific contact
def get_lists_by_contact_id(contact_id):
    """
    Get all lists that include a specific contact.

    Args:
        contact_id (int): The contact ID to search for

    Returns:
        list: Lists containing this contact
    """
    db = get_db()
    try:
        cur = db.cursor()
        cur.execute("""
            SELECT cl.id, cl.name, COUNT(clm.contact_id) as member_count
            FROM contact_lists cl
            JOIN contact_list_members clm ON cl.id = clm.list_id
            WHERE clm.list_id IN (
                SELECT list_id FROM contact_list_members WHERE contact_id = ?
            )
            GROUP BY cl.id
            ORDER BY cl.name
        """, (contact_id,))

        lists = []
        for row in cur.fetchall():
            lists.append({
                'id': row[0],
                'name': row[1],
                'member_count': row[2]
            })

        return lists
    finally:
        db.close()


# Update contact list name
def update_contact_list_name(list_id, name):
    """
    Update the name of a contact list.

    Args:
        list_id (int): The ID of the contact list
        name (str): The new name

    Returns:
        bool: True if successful, False otherwise
    """
    db = get_db()
    try:
        cur = db.cursor()
        cur.execute("UPDATE contact_lists SET name = ? WHERE id = ?", (name, list_id))
        db.commit()
        return cur.rowcount > 0
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def get_contacts_by_ids(contact_ids):
    """
    Get multiple contacts by their IDs

    Args:
        contact_ids (list): List of contact IDs

    Returns:
        list: List of contact objects
    """
    if not contact_ids:
        return []

    placeholders = ','.join(['?'] * len(contact_ids))
    query = f"""
        SELECT c.*, cu.name as customer_name
        FROM contacts c
        LEFT JOIN customers cu ON c.customer_id = cu.id
        WHERE c.id IN ({placeholders})
        ORDER BY c.name
    """

    with get_db_connection() as db:
        contacts = db_execute(query, contact_ids, fetch="all")

    return contacts if contacts else []


# Remove contacts from a list
def remove_contacts_from_list(list_id, contact_ids):
    """
    Remove contacts from a list.

    Args:
        list_id (int): The ID of the contact list
        contact_ids (list): List of contact IDs to remove

    Returns:
        int: Number of contacts removed
    """
    if not isinstance(contact_ids, list) or len(contact_ids) == 0:
        return 0

    placeholders = ','.join(['?'] * len(contact_ids))
    query = f"DELETE FROM contact_list_members WHERE list_id = ? AND contact_id IN ({placeholders})"

    db = get_db()
    try:
        cur = db.cursor()
        cur.execute(query, [list_id] + contact_ids)
        db.commit()
        return cur.rowcount
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()



def get_total_orders():
    """Get the total number of active orders"""
    db = get_db_connection()
    query = """
    SELECT COUNT(*) as count 
    FROM sales_orders so
    JOIN sales_statuses ss ON so.sales_status_id = ss.id
    WHERE ss.status_name NOT IN ('Completed', 'Cancelled')
    """
    count = db_execute(query, fetch="one")['count']
    db.close()
    return count


def get_salespeople():
    """Get all salespeople with additional stats"""
    db = get_db_connection()

    query = """
    SELECT 
        s.id, 
        s.name,
        (SELECT COUNT(*) FROM customers WHERE salesperson_id = s.id) AS customer_count,
        (
            SELECT COUNT(*) 
            FROM rfqs 
            WHERE salesperson_id = s.id AND status = 'open'
        ) AS rfq_count,
        (
            SELECT COUNT(*) 
            FROM sales_orders so
            JOIN sales_statuses ss ON so.sales_status_id = ss.id
            WHERE so.salesperson_id = s.id AND ss.status_name NOT IN ('Completed', 'Cancelled')
        ) AS order_count,
        (
            SELECT MAX(activity_date) FROM (
                SELECT CAST(cu.date AS TEXT) AS activity_date
                FROM customer_updates cu
                WHERE cu.salesperson_id = s.id

                UNION ALL

                SELECT CAST(r.entered_date AS TEXT) AS activity_date
                FROM rfqs r
                WHERE r.salesperson_id = s.id

                UNION ALL

                SELECT CAST(so.date_entered AS TEXT) AS activity_date
                FROM sales_orders so
                WHERE so.salesperson_id = s.id

                UNION ALL

                SELECT CAST(pu.date_created AS TEXT) AS activity_date
                FROM project_updates pu
                WHERE pu.salesperson_id = s.id
            )
        ) AS last_activity
    FROM salespeople s
    ORDER BY s.name
    """

    salespeople = db_execute(query, fetch="all")
    db.close()

    return salespeople


# Add to models.py

def get_salesperson_recent_activities(salesperson_id, limit=10):
    """Get recent activities for a salesperson across all their customers"""
    db = get_db_connection()

    query = """
    WITH salesperson_activities AS (
        SELECT 
            'customer_update' AS activity_type,
            cu.date AS activity_date,
            c.name AS customer_name,
            c.id AS customer_id,
            cu.update_text AS description
        FROM customer_updates cu
        JOIN customers c ON cu.customer_id = c.id
        WHERE cu.salesperson_id = ?

        UNION ALL

        SELECT 
            'rfq' AS activity_type,
            r.entered_date AS activity_date,
            c.name AS customer_name,
            c.id AS customer_id,
            'RFQ: ' || r.customer_ref AS description
        FROM rfqs r
        JOIN customers c ON r.customer_id = c.id
        WHERE r.salesperson_id = ?

        UNION ALL

        SELECT 
            'order' AS activity_type,
            so.date_entered AS activity_date,
            c.name AS customer_name,
            c.id AS customer_id,
            'Order: ' || so.sales_order_ref AS description
        FROM sales_orders so
        JOIN customers c ON so.customer_id = c.id
        WHERE so.salesperson_id = ?

        UNION ALL

        SELECT 
            'project_update' AS activity_type,
            pu.date_created AS activity_date,
            c.name AS customer_name,
            c.id AS customer_id,
            pu.comment AS description
        FROM project_updates pu
        JOIN projects p ON pu.project_id = p.id
        JOIN customers c ON p.customer_id = c.id
        WHERE pu.salesperson_id = ?
    )

    SELECT * FROM salesperson_activities
    ORDER BY activity_date DESC
    LIMIT ?
    """

    activities = db.execute(
        query,
        (salesperson_id, salesperson_id, salesperson_id, salesperson_id, limit)
    ).fetchall()

    db.close()
    return activities


# Update your get_salesperson_customers function to add latest_update sorting
def get_salesperson_customers(salesperson_id, search_term='', status_filter='', sort_by='name', sort_order='asc'):
    """Get customers assigned to a salesperson with filtering and sorting options"""
    db = get_db_connection()

    params = [salesperson_id]
    query = """
    WITH latest_update AS (
        SELECT 
            cu.customer_id,
            cu.update_text,
            cu.date,
            cu.id as update_id,
            ROW_NUMBER() OVER (PARTITION BY cu.customer_id ORDER BY cu.date DESC) as rn
        FROM customer_updates cu
    ),
    latest_order AS (
        SELECT 
            customer_id,
            sales_order_ref,
            date_entered,
            total_value,
            ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY date_entered DESC) as rn
        FROM sales_orders
    )

    SELECT
    c.id,
    c.name,
    c.payment_terms,
    c.incoterms,
    c.estimated_revenue,
    c.fleet_size,
    c.mro_score,
    c.logo_url,
    c.website,
    c.country,
    cs.status AS customer_status,
    lu.update_text AS latest_update,
    lu.date AS latest_update_date,
    -- Contact information for the latest update
    cont.name AS latest_update_contact_name,
    (SELECT COUNT(*) FROM rfqs WHERE customer_id = c.id AND status = 'open') AS active_rfqs,
    lo.sales_order_ref AS most_recent_order_number,
    lo.date_entered AS most_recent_order_date,
    lo.total_value AS most_recent_order_value,
    c.notes

    FROM customers c
    LEFT JOIN customer_status cs ON c.status_id = cs.id
    LEFT JOIN latest_update lu ON c.id = lu.customer_id AND lu.rn = 1
    LEFT JOIN contact_communications cc ON lu.update_id = cc.update_id
    LEFT JOIN contacts cont ON cc.contact_id = cont.id
    LEFT JOIN latest_order lo ON c.id = lo.customer_id AND lo.rn = 1
    WHERE c.salesperson_id = ?
    """

    if search_term:
        query += " AND (c.name LIKE ? OR c.description LIKE ?)"
        params.extend([f'%{search_term}%', f'%{search_term}%'])

    if status_filter:
        query += " AND cs.status = ?"
        params.append(status_filter)

    # Add sorting - UPDATED to include latest_update
    valid_sort_columns = {
        'name': 'c.name',
        'status': 'cs.status',
        'country': 'c.country',
        'fleet_size': 'c.fleet_size',
        'estimated_revenue': 'c.estimated_revenue',
        'active_rfqs': 'active_rfqs',
        'most_recent_order': 'lo.date_entered',
        'latest_update': 'lu.date'  # ADD THIS LINE
    }

    sort_column = valid_sort_columns.get(sort_by, 'c.name')
    sort_direction = 'DESC' if sort_order.lower() == 'desc' else 'ASC'

    # Handle NULL values for columns by putting them last - UPDATED to include latest_update
    if sort_by in ['fleet_size', 'estimated_revenue', 'most_recent_order', 'country', 'latest_update']:
        query += f" ORDER BY {sort_column} IS NULL, {sort_column} {sort_direction}"
    else:
        query += f" ORDER BY {sort_column} {sort_direction}"

    customers = db_execute(query, params, fetch="all")
    db.close()

    return customers


def get_salesperson_customers_with_spend(salesperson_id, search_term='', status_filter='', priority_filter='',
                                         sort_by='name', sort_order='asc'):
    """Get customers with their historical spend, contacts count, priority data, and development data with sorting support
    Excludes child companies and consolidates their data into parent companies"""
    try:
        db = get_db_connection()

        # First, get all customer associations to identify parent-child relationships
        associations_query = """
            SELECT main_customer_id, associated_customer_id 
            FROM customer_associations
        """
        associations = db_execute(associations_query, fetch="all")

        # Build sets for quick lookup
        child_customer_ids = set(assoc['associated_customer_id'] for assoc in associations)

        print(f"DEBUG: Found {len(child_customer_ids)} child customer IDs: {child_customer_ids}")

        # Get the existing customer data using the working function
        all_customers = get_salesperson_customers(salesperson_id, search_term, status_filter, sort_by, sort_order)

        if not all_customers:
            db.close()
            return all_customers

        print(f"DEBUG: Total customers before filtering: {len(all_customers)}")
        print(f"DEBUG: Customer IDs: {[c['id'] for c in all_customers]}")

        # Filter out child companies from the main list FIRST
        main_customers = [customer for customer in all_customers if customer['id'] not in child_customer_ids]
        print(f"DEBUG: Main customers after filtering: {len(main_customers)}")
        print(f"DEBUG: Main customer IDs: {[c['id'] for c in main_customers]}")

        # Build parent-to-children mapping
        parent_to_children = {}
        for assoc in associations:
            parent_id = assoc['main_customer_id']
            if parent_id not in parent_to_children:
                parent_to_children[parent_id] = []
            parent_to_children[parent_id].append(assoc['associated_customer_id'])

        print(f"DEBUG: Parent-to-children mapping: {parent_to_children}")

        # Filter out child companies from the main list
        main_customers = [customer for customer in all_customers if customer['id'] not in child_customer_ids]

        enhanced_customers = []
        for customer in main_customers:
            customer_id = customer['id']

            # Get all related customer IDs (main customer + any associated children)
            related_customer_ids = [customer_id]
            if customer_id in parent_to_children:
                related_customer_ids.extend(parent_to_children[customer_id])

            # Calculate consolidated historical spend for this customer and all associated
            spend_query = f"""
                SELECT COALESCE(SUM(CASE 
                    WHEN total_value IS NULL OR total_value::text = '' THEN 0 
                    ELSE CAST(total_value AS REAL) 
                END), 0) as historical_spend
                FROM sales_orders 
                WHERE customer_id IN ({','.join(['?' for _ in related_customer_ids])})
            """

            result = db_execute(spend_query, related_customer_ids, fetch="one")
            historical_spend = result['historical_spend'] if result else 0

            # Calculate consolidated contacts count for this customer and all associated
            contacts_query = f"""
                SELECT COUNT(*) as contacts_count
                FROM contacts 
                WHERE customer_id IN ({','.join(['?' for _ in related_customer_ids])})
            """

            contacts_result = db_execute(contacts_query, related_customer_ids, fetch="one")
            contacts_count = contacts_result['contacts_count'] if contacts_result else 0

            # Get priority data for the main customer (keep parent's attributes)
            priority_query = """
                SELECT p.name as priority_name, p.color as priority_color, p.id as priority_id
                FROM customers c
                LEFT JOIN priorities p ON c.priority = p.id
                WHERE c.id = ?
            """

            priority_result = db.execute(priority_query, (customer_id,)).fetchone()
            priority_name = priority_result['priority_name'] if priority_result and priority_result[
                'priority_name'] else None
            priority_color = priority_result['priority_color'] if priority_result and priority_result[
                'priority_color'] else None
            priority_id = priority_result['priority_id'] if priority_result and priority_result['priority_id'] else None

            # Get consolidated development data for this customer and all associated
            # Get total development points count (this is global, same for all)
            total_points_query = """
                SELECT COUNT(*) as total_points FROM development_points
            """
            total_points_result = db_execute(total_points_query, fetch="one")
            total_points = total_points_result['total_points'] if total_points_result else 0

            # Get consolidated answered development points count
            answered_points_query = f"""
                SELECT COUNT(*) as answered_points 
                FROM customer_development_answers 
                WHERE customer_id IN ({','.join(['?' for _ in related_customer_ids])}) 
                AND answer IS NOT NULL AND answer != ''
            """
            answered_points_result = db_execute(answered_points_query, related_customer_ids, fetch="one")
            answered_points = answered_points_result['answered_points'] if answered_points_result else 0

            # Get FIRST development point from main customer (keep parent's attributes)
            first_point_query = """
                SELECT 
                    dp.question,
                    dp.description,
                    cda.answer,
                    cda.updated_at
                FROM development_points dp
                LEFT JOIN customer_development_answers cda 
                    ON dp.id = cda.development_point_id 
                    AND cda.customer_id = ?
                ORDER BY dp.order_index ASC, dp.id ASC
                LIMIT 1
            """
            first_point_result = db.execute(first_point_query, (customer_id,)).fetchone()

            # Get child company details for frontend indication
            child_companies = []
            if customer_id in parent_to_children:
                child_ids = parent_to_children[customer_id]
                child_query = f"""
                    SELECT id, name, system_code, country,
                           (SELECT COALESCE(SUM(CASE 
                               WHEN total_value IS NULL OR total_value::text = '' THEN 0 
                               ELSE CAST(total_value AS REAL) 
                           END), 0) FROM sales_orders WHERE customer_id = c.id) as individual_spend,
                           (SELECT COUNT(*) FROM contacts WHERE customer_id = c.id) as individual_contacts
                    FROM customers c 
                    WHERE c.id IN ({','.join(['?' for _ in child_ids])})
                """
                child_results = db_execute(child_query, child_ids, fetch="all")
                child_companies = [dict(child) for child in child_results]

            # Convert customer row to dict and add all the enhanced data
            customer_dict = dict(customer)
            customer_dict['historical_spend'] = historical_spend
            customer_dict['contacts_count'] = contacts_count
            customer_dict['priority_name'] = priority_name
            customer_dict['priority_color'] = priority_color
            customer_dict['priority_id'] = priority_id

            # Add development data
            customer_dict['development_total_count'] = total_points
            customer_dict['development_answered_count'] = answered_points

            # Create first development point structure
            if first_point_result:
                customer_dict['first_development_point'] = {
                    'question': first_point_result['question'],
                    'description': first_point_result['description'],
                    'answer': first_point_result['answer'],
                    'updated_at': first_point_result['updated_at']
                }
            else:
                customer_dict['first_development_point'] = None

            # Recalculate latest update from all related customers (parent + children)
            latest_update_query = f"""
                WITH latest_update AS (
                    SELECT 
                        cu.customer_id,
                        cu.update_text,
                        cu.date,
                        cu.id as update_id,
                        ROW_NUMBER() OVER (ORDER BY cu.date DESC) as rn
                    FROM customer_updates cu
                    WHERE cu.customer_id IN ({','.join(['?' for _ in related_customer_ids])})
                )
                SELECT 
                    lu.update_text,
                    lu.date as latest_update_date,
                    cont.name as latest_update_contact_name
                FROM latest_update lu
                LEFT JOIN contact_communications cc ON lu.update_id = cc.update_id
                LEFT JOIN contacts cont ON cc.contact_id = cont.id
                WHERE lu.rn = 1
            """

            latest_update_result = db_execute(latest_update_query, related_customer_ids, fetch="one")
            if latest_update_result:
                customer_dict['latest_update'] = latest_update_result['update_text']
                customer_dict['latest_update_date'] = latest_update_result['latest_update_date']
                customer_dict['latest_update_contact_name'] = latest_update_result['latest_update_contact_name']

            # Recalculate most recent order from all related customers (parent + children)
            latest_order_query = f"""
                SELECT 
                    sales_order_ref as most_recent_order_number,
                    date_entered as most_recent_order_date,
                    total_value as most_recent_order_value
                FROM sales_orders
                WHERE customer_id IN ({','.join(['?' for _ in related_customer_ids])})
                ORDER BY date_entered DESC
                LIMIT 1
            """

            latest_order_result = db_execute(latest_order_query, related_customer_ids, fetch="one")
            if latest_order_result:
                customer_dict['most_recent_order_number'] = latest_order_result['most_recent_order_number']
                customer_dict['most_recent_order_date'] = latest_order_result['most_recent_order_date']
                customer_dict['most_recent_order_value'] = latest_order_result['most_recent_order_value']

            # Add tags to the main customer
            customer_dict['tags'] = get_tags_by_customer_id(customer_id)

            # Add company types to the main customer
            company_types_query = """
                SELECT ct.id, ct.type
                FROM company_types ct
                JOIN customer_company_types cct ON ct.id = cct.company_type_id
                WHERE cct.customer_id = ?
            """
            company_types_result = db_execute(company_types_query, (customer_id,), fetch="all")
            customer_dict['company_types'] = [dict(ct) for ct in company_types_result] if company_types_result else []

            # Add child company information for frontend
            customer_dict['child_companies'] = child_companies
            customer_dict['has_associated_companies'] = len(child_companies) > 0
            customer_dict['related_customer_ids'] = related_customer_ids

            enhanced_customers.append(customer_dict)

        db.close()

        # Apply priority filtering after getting all the data
        if priority_filter:
            enhanced_customers = [c for c in enhanced_customers if
                                  str(c.get('priority_id', '')) == str(priority_filter)]

        # Apply sorting after adding enhanced data
        if sort_by == 'historical_spend':
            reverse_sort = sort_order.lower() == 'desc'
            enhanced_customers.sort(key=lambda x: x['historical_spend'], reverse=reverse_sort)
        elif sort_by == 'most_recent_order':
            # Sort by most recent order date, handling None values
            reverse_sort = sort_order.lower() == 'desc'
            enhanced_customers.sort(
                key=lambda x: x['most_recent_order_date'] or '1900-01-01',
                reverse=reverse_sort
            )
        elif sort_by == 'contacts_count':
            # Sort by contacts count
            reverse_sort = sort_order.lower() == 'desc'
            enhanced_customers.sort(key=lambda x: x['contacts_count'], reverse=reverse_sort)
        elif sort_by == 'priority':
            # Sort by priority name, handling None values (put None/null priorities at the end)
            reverse_sort = sort_order.lower() == 'desc'
            enhanced_customers.sort(
                key=lambda x: x['priority_name'] or 'zzz_no_priority',
                reverse=reverse_sort
            )
        elif sort_by == 'development':
            # Sort by development progress (answered/total ratio)
            reverse_sort = sort_order.lower() == 'desc'
            enhanced_customers.sort(
                key=lambda x: (x['development_answered_count'] / max(x['development_total_count'], 1)) * 100,
                reverse=reverse_sort
            )
        # For other sorts including latest_update, the data is already sorted by the underlying query

        return enhanced_customers

    except Exception as e:
        print(f"Error adding spend, contacts, priority, and development data: {str(e)}")
        # Return original customers without enhanced data if this fails
        return get_salesperson_customers(salesperson_id, search_term, status_filter, sort_by, sort_order)

def get_all_salespeople_with_customer_counts():
    """Get all salespeople with their customer counts for dropdown"""
    try:
        query = """
        SELECT s.id, s.name, COUNT(c.id) as customer_count
        FROM salespeople s
        LEFT JOIN customers c ON s.id = c.salesperson_id
        GROUP BY s.id, s.name
        ORDER BY s.name
        """
        rows = db_execute(query, fetch="all") or []
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error getting salespeople with customer counts: {e}")
        return []

def get_all_salespeople_with_contact_counts():
    """Get all salespeople with their contact counts for dropdown"""
    try:
        query = """
        SELECT s.id, s.name, COUNT(c.id) as contact_count
        FROM salespeople s
        LEFT JOIN customers cust ON s.id = cust.salesperson_id
        LEFT JOIN contacts c ON cust.id = c.customer_id
        GROUP BY s.id, s.name
        ORDER BY s.name
        """
        rows = db_execute(query, fetch="all") or []
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error getting salespeople with contact counts: {e}")
        return []


def get_development_points():
    """Get all development points ordered by order_index"""
    db = get_db_connection()
    points = db_execute("""
        SELECT id, question, description, order_index
        FROM development_points 
        ORDER BY order_index ASC, id ASC
    """, fetch="all")
    db.close()
    return points


def get_customer_development_plan(customer_id):
    """Get development plan for a specific customer with all points and answers"""
    db = get_db_connection()

    # Get all development points with customer's answers (if any)
    plan = db_execute("""
        SELECT 
            dp.id as point_id,
            dp.question,
            dp.description,
            dp.order_index,
            cda.answer,
            cda.answered_at,
            cda.updated_at,
            cda.answered_by
        FROM development_points dp
        LEFT JOIN customer_development_answers cda 
            ON dp.id = cda.development_point_id 
            AND cda.customer_id = ?
        ORDER BY dp.order_index ASC, dp.id ASC
    """, [customer_id], fetch="all")

    db.close()
    return plan


def get_customer_development_answer(customer_id, development_point_id):
    """Get specific answer for a customer and development point"""
    db = get_db_connection()
    answer = db_execute("""
        SELECT answer, answered_at, updated_at, answered_by
        FROM customer_development_answers 
        WHERE customer_id = ? AND development_point_id = ?
    """, [customer_id, development_point_id], fetch="one")
    db.close()
    return answer


def update_customer_development_answer(customer_id, development_point_id, answer, user_id):
    """Update or insert customer development answer"""
    db = get_db_connection()

    try:
        # Check if answer already exists
        existing = db_execute("""
            SELECT id FROM customer_development_answers 
            WHERE customer_id = ? AND development_point_id = ?
        """, [customer_id, development_point_id], fetch="one")

        if existing:
            # Update existing answer
            db.execute("""
                UPDATE customer_development_answers 
                SET answer = ?, answered_by = ?, updated_at = CURRENT_TIMESTAMP
                WHERE customer_id = ? AND development_point_id = ?
            """, [answer, user_id, customer_id, development_point_id])
        else:
            # Insert new answer
            db.execute("""
                INSERT INTO customer_development_answers 
                (customer_id, development_point_id, answer, answered_by)
                VALUES (?, ?, ?, ?)
            """, [customer_id, development_point_id, answer, user_id])

        db.commit()
        return True

    except Exception as e:
        db.rollback()
        print(f"Error updating development answer: {str(e)}")
        return False
    finally:
        db.close()


def delete_customer_development_answer(customer_id, development_point_id):
    """Delete a customer development answer"""
    db = get_db_connection()
    try:
        db.execute("""
            DELETE FROM customer_development_answers 
            WHERE customer_id = ? AND development_point_id = ?
        """, [customer_id, development_point_id])
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error deleting development answer: {str(e)}")
        return False
    finally:
        db.close()


def get_customers_with_development_progress():
    """Get all customers with their development progress summary"""
    db = get_db_connection()

    customers = db.execute("""
        SELECT 
            c.id,
            c.name,
            COUNT(dp.id) as total_points,
            COUNT(CASE WHEN cda.answer IS NOT NULL AND cda.answer != '' THEN 1 END) as answered_points
        FROM customers c
        CROSS JOIN development_points dp
        LEFT JOIN customer_development_answers cda 
            ON c.id = cda.customer_id AND dp.id = cda.development_point_id
        GROUP BY c.id, c.name
        ORDER BY c.name
    """).fetchall()

    db.close()
    return customers

def get_priorities():
    """Get all priority options with their colors"""
    try:
        db = get_db_connection()
        priorities = db_execute("""
            SELECT id, name, color 
            FROM priorities 
            ORDER BY id
        """, fetch="all")
        db.close()

        return [dict(priority) for priority in priorities]
    except Exception as e:
        print(f"Error getting priorities: {str(e)}")
        return []

def get_salesperson_pending_orders(salesperson_id, limit=5):
    """Get pending sales orders for a salesperson"""
    db = get_db_connection()

    query = """
    SELECT 
        so.id,
        so.sales_order_ref,
        so.date_entered,
        c.name AS customer_name,
        c.id AS customer_id,
        ss.status_name,
        so.total_value,
        curr.currency_code
    FROM sales_orders so
    JOIN customers c ON so.customer_id = c.id
    JOIN sales_statuses ss ON so.sales_status_id = ss.id
    JOIN currencies curr ON so.currency_id = curr.id
    WHERE so.salesperson_id = ? AND ss.status_name NOT IN ('Completed', 'Cancelled')
    ORDER BY so.date_entered DESC
    LIMIT ?
    """

    orders = db.execute(query, (salesperson_id, limit)).fetchall()
    db.close()

    return orders


def get_salespeople_with_stats():
    """Get all salespeople with additional stats"""
    db = get_db_connection()

    query = """
    SELECT 
        s.id, 
        s.name,
        (SELECT COUNT(*) FROM customers WHERE salesperson_id = s.id) AS customer_count,
        (
            SELECT COUNT(*) 
            FROM rfqs 
            WHERE salesperson_id = s.id AND status = 'open'
        ) AS rfq_count,
        (
            SELECT COUNT(*) 
            FROM sales_orders so
            JOIN sales_statuses ss ON so.sales_status_id = ss.id
            WHERE so.salesperson_id = s.id AND ss.status_name NOT IN ('Completed', 'Cancelled')
        ) AS order_count,
        (
            SELECT MAX(activity_date) FROM (
                SELECT CAST(cu.date AS TEXT) AS activity_date
                FROM customer_updates cu
                WHERE cu.salesperson_id = s.id

                UNION ALL

                SELECT CAST(r.entered_date AS TEXT) AS activity_date
                FROM rfqs r
                WHERE r.salesperson_id = s.id

                UNION ALL

                SELECT CAST(so.date_entered AS TEXT) AS activity_date
                FROM sales_orders so
                WHERE so.salesperson_id = s.id

                UNION ALL

                SELECT CAST(pu.date_created AS TEXT) AS activity_date
                FROM project_updates pu
                WHERE pu.salesperson_id = s.id
            )
        ) AS last_activity
    FROM salespeople s
    ORDER BY s.name
    """

    salespeople = db_execute(query, fetch="all")
    db.close()

    return salespeople


def get_total_customers():
    """Get the total number of customers"""
    db = get_db_connection()
    count = db.execute("SELECT COUNT(*) as count FROM customers").fetchone()['count']
    db.close()
    return count


def get_total_active_orders():
    """Get the total number of active orders"""
    db = get_db_connection()
    query = """
    SELECT COUNT(*) as count 
    FROM sales_orders so
    JOIN sales_statuses ss ON so.sales_status_id = ss.id
    WHERE ss.status_name NOT IN ('Completed', 'Cancelled')
    """
    count = db_execute(query, fetch="one")['count']
    db.close()
    return count


def get_customer_status_options():
    """Get all available customer statuses"""
    db = get_db_connection()
    statuses = db_execute("SELECT id, status FROM customer_status", fetch="all")
    db.close()
    return statuses


def add_customer_status_update(customer_id, salesperson_id, update_text):
    """Add a new status update for a customer"""
    db = get_db_connection()

    try:
        db.execute(
            "INSERT INTO customer_updates (date, customer_id, salesperson_id, update_text) VALUES (CURRENT_TIMESTAMP, ?, ?, ?)",
            (customer_id, salesperson_id, update_text)
        )

        db.commit()
        success = True
    except Exception as e:
        db.rollback()
        print(f"Error adding customer update: {e}")
        success = False
    finally:
        db.close()

    return success

def get_customer_by_id(customer_id):
    """Get customer details by ID"""
    db = get_db_connection()

    query = """
    SELECT 
        c.id, 
        c.name, 
        c.status_id,
        c.payment_terms, 
        c.incoterms,
        c.salesperson_id,
        c.notes,
        c.website,        -- Added website column
        c.country,        -- Added country column
        c.watch,          -- Added watch column
        c.logo_url,       -- Added logo_url column
        c.system_code,    -- Added system_code column
        cs.status AS customer_status,
        (SELECT COUNT(*) FROM rfqs WHERE customer_id = c.id AND status = 'open') AS active_rfqs,
        (SELECT COUNT(*) FROM sales_orders WHERE customer_id = c.id AND sales_status_id IN 
            (SELECT id FROM sales_statuses WHERE status_name NOT IN ('Completed', 'Cancelled'))
        ) AS active_orders
    FROM customers c
    LEFT JOIN customer_status cs ON c.status_id = cs.id
    WHERE c.id = ?
    """

    customer = db.execute(query, (customer_id,)).fetchone()
    db.close()

    return customer

def get_customer_updates(customer_id, limit=5):
    """Get recent updates for a specific customer"""
    db = get_db_connection()

    query = """
    SELECT 
        cu.id,
        cu.date,
        cu.update_text,
        s.name AS salesperson_name
    FROM customer_updates cu
    JOIN salespeople s ON cu.salesperson_id = s.id
    WHERE cu.customer_id = ?
    ORDER BY cu.date DESC
    LIMIT ?
    """

    updates = db.execute(query, (customer_id, limit)).fetchall()
    db.close()

    return updates


def get_customer_orders(customer_id, limit=5):
    """Get recent sales orders for a specific customer"""
    db = get_db_connection()
    print(f"DEBUG get_customer_orders: Starting for customer_id {customer_id}")

    # First, let's check if the orders exist without any JOINs
    check_query = "SELECT id, sales_order_ref FROM sales_orders WHERE customer_id = ?"
    orders_exist = db.execute(check_query, (customer_id,)).fetchall()
    print(f"DEBUG: Found {len(orders_exist)} basic orders for customer {customer_id}")

    # Modified query with LEFT JOINs to ensure records don't get filtered out if joins fail
    query = """
    SELECT 
        so.id,
        so.sales_order_ref,
        so.date_entered,
        so.customer_po_ref,
        so.total_value,
        so.sales_status_id,
        so.currency_id,
        s.name AS salesperson_name,
        COALESCE(ss.status_name, 'Unknown') AS status_name,
        COALESCE(curr.currency_code, 'Unknown') AS currency_code
    FROM sales_orders so
    LEFT JOIN salespeople s ON so.salesperson_id = s.id
    LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
    LEFT JOIN currencies curr ON so.currency_id = curr.id
    WHERE so.customer_id = ?
    ORDER BY so.date_entered DESC
    LIMIT ?
    """

    try:
        print(f"DEBUG get_customer_orders: Executing query with params ({customer_id}, {limit})")
        orders = db.execute(query, (customer_id, limit)).fetchall()
        print(f"DEBUG get_customer_orders: Retrieved {len(orders)} orders")
        if orders:
            order_keys = orders[0].keys() if orders else []
            print(f"DEBUG get_customer_orders: Order columns: {list(order_keys)}")
            print(f"DEBUG get_customer_orders: First order data: {dict(orders[0])}")
    except Exception as e:
        print(f"DEBUG get_customer_orders: Error executing query: {str(e)}")
        orders = []
    finally:
        db.close()

    return orders

def get_customer_orders_by_date_range(customer_id, start_date, end_date, time_period):
    """Get orders for a customer within a given date range, including orders from associated companies"""
    import sqlite3
    db = get_db_connection()
    db.row_factory = sqlite3.Row

    # Get all related customer IDs (main customer + any associated children)
    related_customer_ids = [customer_id]

    # Check if this customer has associated companies
    associated_query = """
        SELECT associated_customer_id 
        FROM customer_associations 
        WHERE main_customer_id = ?
    """
    associated_results = db.execute(associated_query, (customer_id,)).fetchall()
    if associated_results:
        related_customer_ids.extend([row['associated_customer_id'] for row in associated_results])

    # Create placeholders for the IN clause
    placeholders = ','.join(['?' for _ in related_customer_ids])

    if time_period == 'yearly':
        # Group by year and sum total_value for yearly view across all related customers
        query = f"""
            SELECT 
                substr(CAST(date_entered AS TEXT), 1, 4) AS year,
                SUM(total_value) AS yearly_value,
                COUNT(*) AS order_count,
                MAX(date_entered) AS latest_date
            FROM sales_orders
            WHERE customer_id IN ({placeholders})
            GROUP BY substr(CAST(date_entered AS TEXT), 1, 4)
            ORDER BY year ASC
        """

        yearly_data = db_execute(query, related_customer_ids, fetch="all")

        print(
            f"DEBUG: Retrieved {len(yearly_data)} years of data for customer {customer_id} and {len(related_customer_ids) - 1} associated companies")
        for year_data in yearly_data:
            print(
                f"DEBUG: Year {year_data['year']}: {year_data['order_count']} orders, value: {year_data['yearly_value']}")

        orders = []
        for year_data in yearly_data:
            orders.append({
                'sales_order_ref': f"{year_data['year']} Summary",  # Use correct column name
                'date_entered': f"{year_data['year']}-12-31",  # Use last day of year for consistency
                'status_name': f"Orders: {year_data['order_count']}",
                'total_value': year_data['yearly_value'] or 0
            })

        db.close()
        return orders

    # For specific date ranges, include orders from all related customers
    query = f"""
        SELECT 
            so.*, 
            ss.status_name, 
            c.name as customer_name,
            CASE WHEN so.customer_id = ? THEN 0 ELSE 1 END as from_associated_company,
            COALESCE(so.total_value, 0) AS total_value
        FROM sales_orders so
        LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
        LEFT JOIN customers c ON so.customer_id = c.id
        WHERE so.customer_id IN ({placeholders})
    """

    params = [customer_id] + related_customer_ids

    if start_date and end_date:
        query += " AND so.date_entered BETWEEN ? AND ?"
        params.extend([start_date, end_date])

    query += " ORDER BY so.date_entered DESC LIMIT 200"

    orders = db_execute(query, params, fetch="all")
    print(f"DEBUG: Retrieved {len(orders)} individual orders for customer {customer_id} and associated companies")

    result = []
    for order in orders:
        order_dict = dict(order)
        # Ensure total_value is available
        if 'total_value' not in order_dict or order_dict['total_value'] is None:
            order_dict['total_value'] = 0

        result.append(order_dict)

    db.close()
    return result



def get_customer_active_orders_count(customer_id, start_date=None, end_date=None):
    """Get count of active orders for a customer within date range"""
    db = get_db_connection()

    query = """
        SELECT COUNT(*) AS count
        FROM sales_orders
        WHERE customer_id = ? 
        AND sales_status_id IN (SELECT id FROM sales_statuses WHERE status_name != 'Completed' AND status_name != 'Cancelled')
    """

    params = [customer_id]

    if start_date and end_date:
        query += " AND date_entered BETWEEN ? AND ?"
        params.extend([start_date, end_date])

    result = db_execute(query, params, fetch="one")
    db.close()

    return result['count'] if result else 0


def get_salesperson_sales_by_date_range(salesperson_id, start_date, end_date):
    """
    Get sales statistics for a salesperson within a specified date range
    """
    try:
        print(f"DEBUG: Getting sales for salesperson {salesperson_id} from {start_date} to {end_date}")

        db = get_db_connection()

        # First check if the sales_orders table has the expected structure
        try:
            # Try to get column names for debugging
            cursor = db.execute("PRAGMA table_info(sales_orders)")
            columns = cursor.fetchall()
            column_names = [col['name'] for col in columns]
            print(f"DEBUG: Found columns in sales_orders table: {column_names}")
        except Exception as e:
            print(f"DEBUG: Error inspecting table structure: {str(e)}")

        # Modify the query based on available columns
        # This makes it more robust in case the schema is different
        query = """
            SELECT 
                COUNT(id) as order_count,
                SUM(CASE WHEN total_value IS NULL OR total_value::text = '' THEN 0 ELSE total_value END) as total_value
            FROM 
                sales_orders
            WHERE 
                salesperson_id = ?
        """

        # Add date filtering if both dates are provided
        params = [salesperson_id]
        if start_date and end_date:
            # Format dates as strings for SQLite
            start_date_str = start_date.strftime('%Y-%m-%d')
            end_date_str = end_date.strftime('%Y-%m-%d')

            query += " AND date_entered BETWEEN ? AND ?"
            params.extend([start_date_str, end_date_str])

        print(f"DEBUG: Executing query: {query} with params: {params}")
        result = db_execute(query, params, fetch="one")
        db.close()

        if result:
            print(f"DEBUG: Query result: {dict(result)}")
            return {
                'order_count': result['order_count'] if result['order_count'] is not None else 0,
                'total_value': float(result['total_value']) if result['total_value'] is not None else 0
            }
        else:
            print("DEBUG: No results found")
            return {'order_count': 0, 'total_value': 0}

    except Exception as e:
        import traceback
        print(f"DEBUG: Error getting sales by date range: {str(e)}")
        print(traceback.format_exc())
        return {'order_count': 0, 'total_value': 0}


def get_salesperson_monthly_sales(salesperson_id, start_date, end_date):
    """
    Get monthly sales data for the past 12 months for a salesperson
    (only orders where this salesperson is directly assigned)
    """
    try:
        print(f"DEBUG: Getting monthly sales for salesperson {salesperson_id}")
        db = get_db_connection()

        # Generate all 12 month labels for the chart
        from datetime import datetime, timedelta

        labels = []
        values = []
        month_data = {}

        # Create a dictionary with all 12 months initialized to zero
        current_date = datetime.now()
        for i in range(12):
            # Calculate month date (go backwards from current month)
            month_date = (current_date - timedelta(days=30 * i)).replace(day=1)
            month_label = month_date.strftime('%b %Y')  # e.g. "Jan 2025"
            month_key = month_date.strftime('%Y-%m')  # e.g. "2025-01"

            # Add to the start of the lists (to get chronological order)
            labels.insert(0, month_label)
            month_data[month_key] = 0

        print(f"DEBUG: Generated month labels: {labels}")

        try:
            # Format dates as strings for SQLite
            start_date_str = start_date.strftime('%Y-%m-%d') if start_date else None
            end_date_str = end_date.strftime('%Y-%m-%d') if end_date else None

            # Basic query that should work in most SQLite versions
            query = """
                SELECT 
                    substr(date_entered, 1, 7) as month,
                    SUM(CASE WHEN total_value IS NULL OR total_value::text = '' THEN 0 ELSE total_value END) as monthly_value
                FROM 
                    sales_orders
                WHERE 
                    salesperson_id = ?
            """

            params = [salesperson_id]

            # Add date filtering if both dates are provided
            if start_date_str and end_date_str:
                query += " AND date_entered BETWEEN ? AND ?"
                params.extend([start_date_str, end_date_str])

            query += """
                GROUP BY 
                    substr(date_entered, 1, 7)
                ORDER BY 
                    month ASC
            """

            print(f"DEBUG: Executing monthly sales query: {query} with params: {params}")
            results = db_execute(query, params, fetch="all")
            print(f"DEBUG: Monthly sales query returned {len(results)} rows")

            # Debug the results
            for row in results:
                print(f"DEBUG: Month: {row['month']}, Value: {row['monthly_value']}")
                if row['month'] in month_data:
                    try:
                        month_data[row['month']] = float(row['monthly_value']) if row['monthly_value'] else 0
                    except (ValueError, TypeError) as e:
                        print(f"DEBUG: Error converting value '{row['monthly_value']}' to float: {str(e)}")
                        month_data[row['month']] = 0
                else:
                    print(f"DEBUG: Month key {row['month']} not found in prepared months dict")

        except Exception as e:
            import traceback
            print(f"DEBUG: Error executing monthly sales query: {str(e)}")
            print(traceback.format_exc())
            # Continue with empty data

        # Convert the dictionary to a list in the same order as the labels
        for i in range(12):
            month_date = (current_date - timedelta(days=30 * (11 - i))).replace(day=1)
            month_key = month_date.strftime('%Y-%m')
            values.append(month_data.get(month_key, 0))

        db.close()
        print(f"DEBUG: Returning monthly sales data with {len(labels)} labels and {len(values)} values")

        return {
            'labels': labels,
            'values': values
        }
    except Exception as e:
        import traceback
        print(f"DEBUG: Error in get_salesperson_monthly_sales: {str(e)}")
        print(traceback.format_exc())
        # Return empty data on error
        return {'labels': [], 'values': []}



def get_accounts_monthly_sales(salesperson_id, start_date, end_date):
    """
    Get monthly sales data for all accounts currently assigned to this salesperson,
    regardless of which salesperson is on the historical orders
    """
    try:
        db = get_db_connection()

        # First, get all customers assigned to this salesperson
        customer_query = """
            SELECT id FROM customers 
            WHERE salesperson_id = ?
        """
        customer_results = db.execute(customer_query, (salesperson_id,)).fetchall()
        customer_ids = [row['id'] for row in customer_results]

        if not customer_ids:
            db.close()
            return {'labels': [], 'values': []}

        # Then get sales data for these customers
        query = """
            SELECT 
                strftime('%Y-%m', date_entered) as month,
                SUM(total_value) as monthly_value
            FROM 
                sales_orders
            WHERE 
                customer_id IN ({placeholders}) AND
                date_entered BETWEEN ? AND ?
            GROUP BY 
                strftime('%Y-%m', date_entered)
            ORDER BY 
                month ASC
        """

        # Create placeholders for the IN clause
        placeholders = ','.join(['?'] * len(customer_ids))
        query = query.format(placeholders=placeholders)

        # Format dates as strings for SQLite
        start_date_str = start_date.strftime('%Y-%m-%d')
        end_date_str = end_date.strftime('%Y-%m-%d')

        # Combine parameters
        params = customer_ids + [start_date_str, end_date_str]

        results = db_execute(query, params, fetch="all")
        db.close()

        # Generate all 12 month labels for the chart
        from datetime import datetime, timedelta

        labels = []
        values = []
        month_data = {}

        # Create a dictionary with all 12 months initialized to zero
        for i in range(12):
            # Calculate month date (go backwards from current month)
            month_date = (datetime.now() - timedelta(days=30 * i)).replace(day=1)
            month_label = month_date.strftime('%b %Y')  # e.g. "Jan 2025"
            month_key = month_date.strftime('%Y-%m')  # e.g. "2025-01"

            # Add to the start of the lists (to get chronological order)
            labels.insert(0, month_label)
            month_data[month_key] = 0

        # Fill in the actual values from the database query
        for row in results:
            if row['month'] in month_data:
                month_data[row['month']] = float(row['monthly_value'] or 0)

        # Convert the dictionary to a list in the same order as the labels
        for i in range(12):
            month_date = (datetime.now() - timedelta(days=30 * (11 - i))).replace(day=1)
            month_key = month_date.strftime('%Y-%m')
            values.append(month_data.get(month_key, 0))

        return {
            'labels': labels,
            'values': values
        }
    except Exception as e:
        print(f"Error getting account monthly sales: {str(e)}")
        # Return empty data on error
        return {'labels': [], 'values': []}


def get_contact_communications(contact_id, salesperson_id=None):
    """Get all communications for a specific contact."""
    db = get_db_connection()

    query = """
        SELECT 
            cc.*,
            s.name as salesperson_name
        FROM contact_communications cc
        LEFT JOIN salespeople s ON cc.salesperson_id = s.id
        WHERE cc.contact_id = ?
    """

    params = [contact_id]

    # REMOVED: The salesperson_id filtering that was causing the issue
    # if salesperson_id:
    #     query += " AND cc.salesperson_id = ?"
    #     params.append(salesperson_id)

    query += " ORDER BY cc.date DESC"

    communications = db_execute(query, params, fetch="all")
    db.close()

    # Convert to list of dictionaries and format dates
    result = []
    for comm in communications:
        comm_dict = dict(comm)

        # Parse the date into a datetime object for display/editing
        try:
            raw_date = comm_dict.get('date')

            if isinstance(raw_date, datetime):
                parsed_date = raw_date
            elif isinstance(raw_date, date):
                parsed_date = datetime.combine(raw_date, datetime.min.time())
            elif isinstance(raw_date, bytes):
                parsed_date = None
                date_str = raw_date.decode('utf-8', errors='ignore')
            elif raw_date:
                parsed_date = None
                date_str = str(raw_date)
            else:
                parsed_date = None
                date_str = None

            if parsed_date is None and date_str:
                try:
                    parsed_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                except ValueError:
                    for fmt in (
                        '%Y-%m-%d %H:%M:%S',
                        '%Y-%m-%d %H:%M:%S.%f',
                        '%Y-%m-%dT%H:%M:%S',
                        '%Y-%m-%dT%H:%M:%S.%f',
                        '%Y-%m-%d %H:%M'
                    ):
                        try:
                            parsed_date = datetime.strptime(date_str, fmt)
                            break
                        except ValueError:
                            continue

            if parsed_date:
                comm_dict['date'] = parsed_date
                comm_dict['date_formatted'] = parsed_date.strftime('%b %d, %Y %I:%M %p')
            elif date_str:
                comm_dict['date'] = None
                comm_dict['date_formatted'] = date_str
            else:
                comm_dict['date'] = None
                comm_dict['date_formatted'] = "Unknown date"
        except Exception as e:
            print(f"Error parsing date: {e}")
            comm_dict['date'] = None
            comm_dict['date_formatted'] = "Unknown date"

        result.append(comm_dict)

    return result

def update_customer_field_value(customer_id, field, value):
    """Update a specific field for a customer in the database"""
    try:
        db = get_db_connection()

        # Validate field name for security (double-check)
        allowed_fields = ['fleet_size', 'estimated_revenue']
        if field not in allowed_fields:
            return False

        # Use parameterized query for security
        query = f"UPDATE customers SET {field} = ? WHERE id = ?"
        db.execute(query, (value, customer_id))

        # Commit the change
        db.commit()
        db.close()

        return True
    except Exception as e:
        print(f"Database error updating {field} for customer {customer_id}: {str(e)}")
        return False


def get_all_contact_statuses():
    """Get all active contact statuses ordered by sort_order"""
    db = get_db_connection()
    try:
        statuses = db_execute('''
            SELECT id, name, description, color, sort_order
            FROM contact_statuses 
            WHERE is_active = TRUE 
            ORDER BY sort_order, name
        ''', fetch="all")
        return [dict(status) for status in statuses]
    finally:
        db.close()


def get_contact_status_by_id(status_id):
    """Get a specific contact status by ID"""
    db = get_db_connection()
    try:
        status = db.execute('''
            SELECT id, name, description, color, sort_order
            FROM contact_statuses 
            WHERE id = ? AND is_active = TRUE
        ''', (status_id,)).fetchone()
        return dict(status) if status else None
    finally:
        db.close()


def update_contact_status(contact_id, status_id):
    """Update a contact's status"""
    db = get_db_connection()
    try:
        db.execute('''
            UPDATE contacts 
            SET status_id = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (status_id, contact_id))
        db.commit()
        return True
    except Exception as e:
        print(f"Error updating contact status: {e}")
        return False
    finally:
        db.close()


def get_customer_contacts(customer_id, status_filter=''):
    """Get all contacts for a specific customer"""
    db = get_db_connection()
    try:
        query_parts = ["""
            SELECT 
                c.id, c.name, c.email, c.job_title, c.notes,
                c.customer_id, cu.name as customer_name,
                cs.name as status_name, cs.color as status_color, cs.id as status_id,
                c.updated_at
            FROM contacts c
            JOIN customers cu ON c.customer_id = cu.id
            LEFT JOIN contact_statuses cs ON c.status_id = cs.id
            WHERE c.customer_id = ?
        """]

        params = [customer_id]

        if status_filter:
            query_parts.append("AND c.status_id = ?")
            params.append(status_filter)

        query_parts.append("ORDER BY c.name")

        query = " ".join(query_parts)
        contacts = db_execute(query, params, fetch="all")
        return [dict(contact) for contact in contacts]
    finally:
        db.close()


def get_all_contacts_filtered(search_term='', customer_filter='', status_filter=''):
    """Get all contacts with optional filters"""
    db = get_db_connection()
    try:
        query_parts = ["""
            SELECT 
                c.id, c.name, c.email, c.job_title, c.notes,
                c.customer_id, cu.name as customer_name,
                cs.name as status_name, cs.color as status_color, cs.id as status_id,
                c.updated_at
            FROM contacts c
            JOIN customers cu ON c.customer_id = cu.id
            LEFT JOIN contact_statuses cs ON c.status_id = cs.id
            WHERE 1=1
        """]

        params = []

        if search_term:
            query_parts.append(
                "AND (c.name LIKE ? OR c.email LIKE ? OR c.job_title LIKE ? OR c.notes LIKE ? OR cs.name LIKE ?)")
            search_pattern = f"%{search_term}%"
            params.extend([search_pattern] * 5)

        if customer_filter:
            query_parts.append("AND c.customer_id = ?")
            params.append(customer_filter)

        if status_filter:
            query_parts.append("AND c.status_id = ?")
            params.append(status_filter)

        query_parts.append("ORDER BY cu.name, c.name")

        query = " ".join(query_parts)
        contacts = db_execute(query, params, fetch="all")
        return [dict(contact) for contact in contacts]
    finally:
        db.close()


def get_all_contacts_by_status(status_id):
    """Get all contacts with a specific status"""
    db = get_db_connection()
    try:
        contacts = db.execute('''
            SELECT 
                c.id, c.name, c.email, c.job_title, c.notes,
                c.customer_id, cu.name as customer_name,
                cs.name as status_name, cs.color as status_color,
                c.updated_at
            FROM contacts c
            JOIN customers cu ON c.customer_id = cu.id
            LEFT JOIN contact_statuses cs ON c.status_id = cs.id
            WHERE c.status_id = ?
            ORDER BY cu.name, c.name
        ''', (status_id,)).fetchall()
        return [dict(contact) for contact in contacts]
    finally:
        db.close()


def get_all_contact_status_counts():
    """Get count of contacts by status across all customers"""
    db = get_db_connection()
    try:
        status_counts = db.execute('''
            SELECT 
                cs.id, cs.name, cs.color, cs.sort_order,
                COUNT(c.id) as contact_count
            FROM contact_statuses cs
            LEFT JOIN contacts c ON cs.id = c.status_id
            WHERE cs.is_active = TRUE
            GROUP BY cs.id, cs.name, cs.color, cs.sort_order
            ORDER BY cs.sort_order, cs.name
        ''').fetchall()
        return [dict(status) for status in status_counts]
    finally:
        db.close()


def get_status_counts_for_salesperson(salesperson_id):
    """Get count of contacts by status for a salesperson"""
    db = get_db_connection()
    try:
        # First, get the customers assigned to this salesperson
        customers_query = """
            SELECT id FROM customers 
            WHERE salesperson_id = ?
        """
        customers = db.execute(customers_query, (salesperson_id,)).fetchall()

        if not customers:
            return []

        customer_ids = [c['id'] for c in customers]
        placeholders = ','.join(['?'] * len(customer_ids))

        query = f"""
            SELECT 
                cs.id, cs.name, cs.color, cs.sort_order,
                COUNT(c.id) as contact_count
            FROM contact_statuses cs
            LEFT JOIN contacts c ON cs.id = c.status_id 
                AND c.customer_id IN ({placeholders})
            WHERE cs.is_active = TRUE
            GROUP BY cs.id, cs.name, cs.color, cs.sort_order
            ORDER BY cs.sort_order, cs.name
        """

        status_counts = db_execute(query, customer_ids, fetch="all")
        return [dict(status) for status in status_counts]
    finally:
        db.close()


def get_customer_by_id(customer_id):
    """Get customer details by ID"""
    db = get_db_connection()
    try:
        customer = db.execute('''
            SELECT * FROM customers WHERE id = ?
        ''', (customer_id,)).fetchone()
        return dict(customer) if customer else None
    finally:
        db.close()


def get_all_customers():
    """Get all customers"""
    db = get_db_connection()
    try:
        customers = db_execute('''
            SELECT id, name FROM customers ORDER BY name
        ''', fetch="all")
        return [dict(customer) for customer in customers]
    finally:
        db.close()


def get_customer_contacts_with_communications(customer_id):
    """Get all contacts for a customer with their latest communication info"""
    try:
        db = get_db_connection()

        # Corrected query with proper column names
        query = """
            SELECT 
                c.id,
                c.name,
                c.email,
                c.phone,
                c.job_title,
                c.second_name,
                c.status_id,
                cs.name as status_name,
                cs.color as status_color,
                c.notes,
                c.updated_at,
                cc.date as last_communication_date,
                cc.communication_type as last_communication_type,
                cc.notes as last_communication_notes
            FROM contacts c
            LEFT JOIN contact_statuses cs ON c.status_id = cs.id
            LEFT JOIN (
                SELECT 
                    contact_id,
                    MAX(date) as latest_date
                FROM contact_communications 
                WHERE customer_id = ?
                GROUP BY contact_id
            ) latest_comm ON c.id = latest_comm.contact_id
            LEFT JOIN contact_communications cc ON (
                c.id = cc.contact_id 
                AND cc.date = latest_comm.latest_date
                AND cc.customer_id = ?
            )
            WHERE c.customer_id = ?
            ORDER BY c.name ASC
        """

        contacts = db.execute(query, (customer_id, customer_id, customer_id)).fetchall()
        db.close()

        print(f"DEBUG: Raw contacts query returned {len(contacts)} rows")

        # Convert to list of dicts for easier template handling
        result = []
        for contact in contacts:
            contact_dict = dict(contact)

            # Use status_name from the joined table
            contact_dict['status'] = contact_dict.get('status_name')

            # Format the last communication date if it exists
            if contact_dict['last_communication_date']:
                try:
                    from datetime import datetime
                    date_str = contact_dict['last_communication_date']

                    print(f"DEBUG: Raw date string: '{date_str}'")

                    # Try different date formats
                    formats_to_try = [
                        '%Y-%m-%d %H:%M:%S',
                        '%Y-%m-%d %H:%M:%S.%f',
                        '%Y-%m-%d',
                        '%d/%m/%Y %H:%M:%S',
                        '%d/%m/%Y'
                    ]

                    date_obj = None
                    for fmt in formats_to_try:
                        try:
                            date_obj = datetime.strptime(date_str, fmt)
                            break
                        except ValueError:
                            continue

                    if date_obj:
                        contact_dict['last_communication_date'] = date_obj.strftime('%d %b %Y')
                    else:
                        # If no format works, just use the original string
                        contact_dict['last_communication_date'] = date_str

                except Exception as e:
                    print(f"Date parsing error for contact {contact_dict.get('name', 'Unknown')}: {e}")
                    # Keep original date string if parsing fails
                    pass

            result.append(contact_dict)
            print(f"DEBUG: Processed contact: {contact_dict['name']} - {contact_dict.get('email', 'No email')}")

        return result

    except Exception as e:
        print(f"DEBUG: Error getting customer contacts: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return []


# Add these functions to your models.py file

def get_active_salespeople():
    """Get only active salespeople"""
    db = get_db_connection()
    salespeople = db_execute('''
        SELECT id, name, system_ref
        FROM salespeople 
        WHERE is_active = TRUE
        ORDER BY name
    ''', fetch="all")
    db.close()
    return salespeople


def toggle_salesperson_active(salesperson_id, is_active):
    """Toggle salesperson active status"""
    try:
        db = get_db_connection()
        db.execute('''
            UPDATE salespeople 
            SET is_active = ?
            WHERE id = ?
        ''', (is_active, salesperson_id))
        db.commit()
        db.close()
        return True
    except Exception as e:
        print(f"Error toggling salesperson active status: {str(e)}")
        return False


def _parse_date_value(date_value):
    """
    Parse a date value that could be a string or datetime object.
    Returns a date object or None if parsing fails.
    Handles PostgreSQL and SQLite date formats.
    """
    from datetime import datetime, date

    if date_value is None:
        return None

    # Already a date object
    if isinstance(date_value, date) and not isinstance(date_value, datetime):
        return date_value

    # Already a datetime object
    if isinstance(date_value, datetime):
        return date_value.date()

    # String - try various formats
    if isinstance(date_value, str):
        date_str = date_value.strip()
        if not date_str:
            return None

        # Try common formats
        formats = [
            '%Y-%m-%d %H:%M:%S.%f%z',  # PostgreSQL with timezone and microseconds
            '%Y-%m-%d %H:%M:%S.%f',     # PostgreSQL with microseconds
            '%Y-%m-%d %H:%M:%S%z',      # PostgreSQL with timezone
            '%Y-%m-%d %H:%M:%S',        # Standard datetime
            '%Y-%m-%d',                  # Date only
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue

        # Try parsing just the date part if there's a space
        if ' ' in date_str:
            try:
                date_part = date_str.split(' ')[0]
                return datetime.strptime(date_part, '%Y-%m-%d').date()
            except ValueError:
                pass

        # Try parsing with timezone offset like +00
        if '+' in date_str or (date_str.count('-') > 2):
            try:
                # Remove timezone for simpler parsing
                clean_str = date_str.split('+')[0].split('.')[0]
                return datetime.strptime(clean_str, '%Y-%m-%d %H:%M:%S').date()
            except ValueError:
                pass

    return None


def get_last_contact_date(salesperson_id, customer_status_filter=None, contact_status_filter=None):
    """Get the most recent contact date for a salesperson"""
    # Base query
    query = '''
        SELECT MAX(cc.date) as last_contact_date
        FROM contact_communications cc
        JOIN contacts c ON cc.contact_id = c.id
        JOIN customers cu ON cc.customer_id = cu.id
        WHERE cc.salesperson_id = ?
    '''
    params = [salesperson_id]

    # Add filters if provided
    if customer_status_filter:
        query += ' AND cu.status_id IN ({})'.format(','.join(['?'] * len(customer_status_filter)))
        params.extend(customer_status_filter)

    if contact_status_filter:
        query += ' AND c.status_id IN ({})'.format(','.join(['?'] * len(contact_status_filter)))
        params.extend(contact_status_filter)

    result = db_execute(query, params, fetch="one")

    return result['last_contact_date'] if result and result['last_contact_date'] else None


def get_average_contact_frequency(salesperson_id, customer_status_filter=None, contact_status_filter=None):
    """Calculate average days between contacts for a salesperson"""
    # Get all contact dates for this salesperson, ordered by date
    query = '''
        SELECT cc.date
        FROM contact_communications cc
        JOIN contacts c ON cc.contact_id = c.id
        JOIN customers cu ON cc.customer_id = cu.id
        WHERE cc.salesperson_id = ?
    '''
    params = [salesperson_id]

    # Add filters if provided
    if customer_status_filter:
        query += ' AND cu.status_id IN ({})'.format(','.join(['?'] * len(customer_status_filter)))
        params.extend(customer_status_filter)

    if contact_status_filter:
        query += ' AND c.status_id IN ({})'.format(','.join(['?'] * len(contact_status_filter)))
        params.extend(contact_status_filter)

    query += ' ORDER BY cc.date'

    results = db_execute(query, params, fetch="all")

    if not results or len(results) < 2:
        return None

    # Calculate differences between consecutive dates
    dates = []
    for row in results:
        date_obj = _parse_date_value(row['date'])
        if date_obj:
            dates.append(date_obj)

    if len(dates) < 2:
        return None

    # Calculate differences in days
    differences = []
    for i in range(1, len(dates)):
        diff = (dates[i] - dates[i - 1]).days
        if diff > 0:  # Only count positive differences
            differences.append(diff)

    if not differences:
        return None

    return sum(differences) / len(differences)


def get_overdue_contacts_count(salesperson_id, threshold_days=14, customer_status_filter=None,
                               contact_status_filter=None):
    """Count contacts that haven't been contacted in threshold_days"""
    from datetime import datetime, timedelta

    threshold_date = datetime.now().date() - timedelta(days=threshold_days)

    # Get unique contacts for this salesperson and their last contact date
    query = '''
        SELECT c.id,
               MAX(cc.date) as last_contact_date
        FROM contacts c
        JOIN customers cu ON c.customer_id = cu.id
        LEFT JOIN contact_communications cc
            ON c.id = cc.contact_id AND cc.salesperson_id = ?
        WHERE cu.salesperson_id = ?
    '''
    params = [salesperson_id, salesperson_id]

    # Add filters if provided
    if customer_status_filter:
        query += ' AND cu.status_id IN ({})'.format(','.join(['?'] * len(customer_status_filter)))
        params.extend(customer_status_filter)

    if contact_status_filter:
        query += ' AND c.status_id IN ({})'.format(','.join(['?'] * len(contact_status_filter)))
        params.extend(contact_status_filter)

    query += ' GROUP BY c.id'

    results = db_execute(query, params, fetch="all")

    overdue_count = 0
    for row in results:
        last_contact = row['last_contact_date']
        if not last_contact:
            overdue_count += 1
        else:
            last_contact_date = _parse_date_value(last_contact)
            if last_contact_date and last_contact_date < threshold_date:
                overdue_count += 1

    return overdue_count


def get_contacts_this_week_count(salesperson_id, customer_status_filter=None, contact_status_filter=None):
    """Count contacts made this week by salesperson"""
    from datetime import datetime, timedelta

    # Calculate start of this week (Monday)
    today = datetime.now().date()
    days_since_monday = today.weekday()
    start_of_week = today - timedelta(days=days_since_monday)

    # Use CAST for PostgreSQL compatibility - cc.date is TEXT so compare as text
    query = '''
        SELECT COUNT(*) as count
        FROM contact_communications cc
        JOIN contacts c ON cc.contact_id = c.id
        JOIN customers cu ON cc.customer_id = cu.id
        WHERE cc.salesperson_id = ?
        AND cc.date >= ?
    '''
    params = [salesperson_id, start_of_week.strftime('%Y-%m-%d')]

    # Add filters if provided
    if customer_status_filter:
        query += ' AND cu.status_id IN ({})'.format(','.join(['?'] * len(customer_status_filter)))
        params.extend(customer_status_filter)

    if contact_status_filter:
        query += ' AND c.status_id IN ({})'.format(','.join(['?'] * len(contact_status_filter)))
        params.extend(contact_status_filter)

    result = db_execute(query, params, fetch="one")

    return result['count'] if result else 0


def get_salesperson_customer_count(salesperson_id, customer_status_filter=None):
    """Get count of customers assigned to salesperson"""
    query = '''
        SELECT COUNT(*) as count
        FROM customers
        WHERE salesperson_id = ?
    '''
    params = [salesperson_id]

    # Add filters if provided
    if customer_status_filter:
        query += ' AND status_id IN ({})'.format(','.join(['?'] * len(customer_status_filter)))
        params.extend(customer_status_filter)

    result = db_execute(query, params, fetch="one")

    return result['count'] if result else 0


def get_recent_contact_timeline(salesperson_id, limit=5, customer_status_filter=None, contact_status_filter=None):
    """Get recent contact timeline for salesperson"""
    query = '''
        SELECT cc.date, c.name as contact_name, cu.name as customer_name,
               cc.communication_type, cc.notes
        FROM contact_communications cc
        JOIN contacts c ON cc.contact_id = c.id
        JOIN customers cu ON cc.customer_id = cu.id
        WHERE cc.salesperson_id = ?
    '''
    params = [salesperson_id]

    # Add filters if provided
    if customer_status_filter:
        query += ' AND cu.status_id IN ({})'.format(','.join(['?'] * len(customer_status_filter)))
        params.extend(customer_status_filter)

    if contact_status_filter:
        query += ' AND c.status_id IN ({})'.format(','.join(['?'] * len(contact_status_filter)))
        params.extend(contact_status_filter)

    query += ' ORDER BY cc.date DESC LIMIT ?'
    params.append(limit)

    results = db_execute(query, params, fetch="all")

    return results


def get_engagement_metrics(salesperson_id, customer_status_filter=None, contact_status_filter=None,
                           overdue_threshold_days=None):
    """Get all engagement metrics for a salesperson with configurable settings"""
    from datetime import datetime, timedelta

    # Get settings if not provided
    if overdue_threshold_days is None:
        settings = get_engagement_settings(salesperson_id)
        overdue_threshold_days = settings['overdue_threshold_days']
        if customer_status_filter is None:
            customer_status_filter = settings['customer_status_filter']
        if contact_status_filter is None:
            contact_status_filter = settings['contact_status_filter']

    # Calculate days since last contact
    last_contact_date = get_last_contact_date(salesperson_id, customer_status_filter, contact_status_filter)
    days_since_last = None
    if last_contact_date:
        last_date = _parse_date_value(last_contact_date)
        if last_date:
            days_since_last = (datetime.now().date() - last_date).days

    # Get other metrics
    avg_frequency = get_average_contact_frequency(salesperson_id, customer_status_filter, contact_status_filter)
    contacts_this_week = get_contacts_this_week_count(salesperson_id, customer_status_filter, contact_status_filter)
    overdue_contacts = get_overdue_contacts_count(salesperson_id, overdue_threshold_days, customer_status_filter,
                                                  contact_status_filter)
    total_customers = get_salesperson_customer_count(salesperson_id, customer_status_filter)

    # Get overdue contacts list instead of recent timeline
    overdue_contacts_list = get_overdue_contacts_list(salesperson_id, overdue_threshold_days, customer_status_filter,
                                                      contact_status_filter, limit=5)

    return {
        'days_since_last': days_since_last,
        'avg_contact_frequency': round(avg_frequency, 1) if avg_frequency else None,
        'contacts_this_week': contacts_this_week,
        'overdue_contacts': overdue_contacts,
        'total_customers': total_customers,
        'overdue_contacts_list': overdue_contacts_list,
        'settings': {
            'overdue_threshold_days': overdue_threshold_days,
            'customer_status_filter': customer_status_filter,
            'contact_status_filter': contact_status_filter
        }
    }


def get_overdue_contacts_list(salesperson_id, threshold_days=14, customer_status_filter=None,
                              contact_status_filter=None, limit=10):
    """Get list of contacts that haven't been contacted in threshold_days, ordered by oldest contact first"""
    from datetime import datetime, timedelta

    threshold_date = (datetime.now().date() - timedelta(days=threshold_days)).strftime('%Y-%m-%d')

    # Get contacts with their last contact date, ordered by oldest first
    # PostgreSQL requires all non-aggregated columns in GROUP BY
    query = '''
        SELECT c.id,
               c.name as contact_name,
               c.email,
               c.phone,
               cu.name as customer_name,
               cu.id as customer_id,
               MAX(cc.date) as last_contact_date
        FROM contacts c
        JOIN customers cu ON c.customer_id = cu.id
        LEFT JOIN contact_communications cc ON c.id = cc.contact_id AND cc.salesperson_id = ?
        WHERE cu.salesperson_id = ?
    '''
    params = [salesperson_id, salesperson_id]

    # Add filters if provided
    if customer_status_filter:
        query += ' AND cu.status_id IN ({})'.format(','.join(['?'] * len(customer_status_filter)))
        params.extend(customer_status_filter)

    if contact_status_filter:
        query += ' AND c.status_id IN ({})'.format(','.join(['?'] * len(contact_status_filter)))
        params.extend(contact_status_filter)

    query += '''
        GROUP BY c.id, c.name, c.email, c.phone, cu.name, cu.id
        HAVING MAX(cc.date) IS NULL OR MAX(cc.date) < ?
        ORDER BY last_contact_date ASC NULLS FIRST, c.name ASC
        LIMIT ?
    '''
    params.extend([threshold_date, limit])

    results = db_execute(query, params, fetch="all")

    # Format the results
    overdue_contacts = []
    for row in results:
        last_contact = row['last_contact_date']
        if last_contact:
            last_date = _parse_date_value(last_contact)
            if last_date:
                days_ago = (datetime.now().date() - last_date).days
                formatted_date = last_date.strftime('%m/%d/%Y')
            else:
                days_ago = None
                formatted_date = "Invalid date"
        else:
            days_ago = None
            formatted_date = "Never contacted"

        overdue_contacts.append({
            'contact_id': row['id'],
            'contact_name': row['contact_name'],
            'customer_name': row['customer_name'],
            'customer_id': row['customer_id'],
            'email': row['email'],
            'phone': row['phone'],
            'last_contact_date': formatted_date,
            'days_ago': days_ago
        })

    return overdue_contacts


def get_engagement_settings(salesperson_id):
    """Get persistent engagement settings for a salesperson"""
    result = db_execute('''
        SELECT overdue_threshold_days, customer_status_filter, contact_status_filter
        FROM salesperson_engagement_settings
        WHERE salesperson_id = ?
    ''', [salesperson_id], fetch="one")

    if result:
        import json
        return {
            'overdue_threshold_days': result['overdue_threshold_days'],
            'customer_status_filter': json.loads(result['customer_status_filter']) if result[
                'customer_status_filter'] else None,
            'contact_status_filter': json.loads(result['contact_status_filter']) if result[
                'contact_status_filter'] else None
        }
    else:
        # Return defaults
        return {
            'overdue_threshold_days': 14,
            'customer_status_filter': None,
            'contact_status_filter': None
        }


def save_engagement_settings(salesperson_id, overdue_threshold_days, customer_status_filter=None,
                             contact_status_filter=None):
    """Save persistent engagement settings for a salesperson"""
    import json

    # Convert filters to JSON
    customer_filter_json = json.dumps(customer_status_filter) if customer_status_filter else None
    contact_filter_json = json.dumps(contact_status_filter) if contact_status_filter else None

    db_execute(
        """
        INSERT INTO salesperson_engagement_settings
        (salesperson_id, overdue_threshold_days, customer_status_filter, contact_status_filter, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(salesperson_id) DO UPDATE SET
            overdue_threshold_days = EXCLUDED.overdue_threshold_days,
            customer_status_filter = EXCLUDED.customer_status_filter,
            contact_status_filter = EXCLUDED.contact_status_filter,
            updated_at = CURRENT_TIMESTAMP
        """,
        [salesperson_id, overdue_threshold_days, customer_filter_json, contact_filter_json],
        commit=True,
    )


# Add this function to your database helper functions
def get_contacts_count_by_customer_id(customer_id):
    """Get the count of contacts for a specific customer"""
    try:
        result = query_one(
            """
            SELECT COUNT(*) as contact_count
            FROM contacts 
            WHERE customer_id = ?
            """,
            (customer_id,),
        )
        return result['contact_count'] if result else 0
    except Exception as e:
        print(f"Error getting contacts count for customer {customer_id}: {str(e)}")
        return 0


def bulk_add_tag_to_customers(customer_ids, tag_name):
    """Add a tag to multiple customers, return count of affected customers"""
    affected_count = 0

    try:
        with db_cursor(commit=True) as cur:
            tag_row = cur.execute('SELECT id FROM industry_tags WHERE tag = ?', (tag_name,)).fetchone()
            if not tag_row:
                cur.execute('INSERT INTO industry_tags (tag) VALUES (?) RETURNING id', (tag_name,))
                row = cur.fetchone()
                tag_id = row['id'] if row and isinstance(row, dict) and 'id' in row else (list(row.values())[0] if row else None)
            else:
                tag_id = tag_row['id'] if isinstance(tag_row, dict) else tag_row[0]

            for customer_id in customer_ids:
                existing = cur.execute(
                    'SELECT id FROM customer_industry_tags WHERE customer_id = ? AND tag_id = ?',
                    (customer_id, tag_id),
                ).fetchone()

                if not existing:
                    cur.execute(
                        'INSERT INTO customer_industry_tags (customer_id, tag_id) VALUES (?, ?)',
                        (customer_id, tag_id),
                    )
                    affected_count += 1

        return affected_count

    except Exception as e:
        raise e


def bulk_remove_tag_from_customers(customer_ids, tag_name):
    """Remove a tag from multiple customers, return count of affected customers"""
    affected_count = 0

    try:
        with db_cursor(commit=True) as cur:
            tag_row = cur.execute('SELECT id FROM industry_tags WHERE tag = ?', (tag_name,)).fetchone()
            if not tag_row:
                return 0  # Tag doesn't exist, nothing to remove

            tag_id = tag_row['id'] if isinstance(tag_row, dict) else tag_row[0]

            for customer_id in customer_ids:
                result = cur.execute(
                    'DELETE FROM customer_industry_tags WHERE customer_id = ? AND tag_id = ?',
                    (customer_id, tag_id),
                )
                if getattr(result, 'rowcount', 0) > 0:
                    affected_count += 1

        return affected_count

    except Exception as e:
        raise e


def get_tag_usage_statistics():
    """Get statistics about tag usage"""
    stats = query_all('''
            SELECT 
                it.tag,
                COUNT(cit.customer_id) as count
            FROM industry_tags it
            LEFT JOIN customer_industry_tags cit ON it.id = cit.tag_id
            GROUP BY it.id, it.tag
            HAVING COUNT(cit.customer_id) > 0
            ORDER BY count DESC, it.tag
        ''')

    return [dict(stat) for stat in stats]


def get_total_customer_count():
    """Get total number of customers in the system"""
    conn = get_db_connection()
    try:
        result = conn.execute('SELECT COUNT(*) as count FROM customers').fetchone()
        return result['count'] if result else 0
    finally:
        conn.close()
def get_salesperson_recent_communications(salesperson_id, target_date_str=None, days_back=1, skip_weekends=True):
    """
    Get recent communications for a salesperson, grouped by communication type first, then by company

    Args:
        salesperson_id: ID of the salesperson
        target_date_str: Specific date string (YYYY-MM-DD) or None for automatic calculation
        days_back: How many days to look back (default 1 for yesterday) - only used if target_date_str is None
        skip_weekends: If True, on Monday show Friday's data instead of Sunday's - only used if target_date_str is None

    Returns:
        Dict with communication types as keys, and companies as sub-keys
    """
    from datetime import datetime, timedelta

    db = get_db_connection()

    if target_date_str:
        # Use the provided date
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
            print(f"DEBUG: Using provided target date: {target_date}")
        except ValueError:
            print(f"DEBUG: Invalid date format: {target_date_str}, falling back to business day logic")
            target_date_str = None  # Fall back to business day logic

    if not target_date_str:
        # Calculate target date with business day logic
        today = datetime.now().date()

        # If today is Monday, show Friday's communications (3 days back)
        if today.weekday() == 0 and skip_weekends:
            target_date = today - timedelta(days=3)
            print(f"DEBUG: Monday detected, showing Friday's data: {target_date}")
        # If today is Sunday, show Friday's communications (2 days back)
        elif today.weekday() == 6 and skip_weekends:
            target_date = today - timedelta(days=2)
            print(f"DEBUG: Sunday detected, showing Friday's data: {target_date}")
        else:
            # For all other days, show previous day
            target_date = today - timedelta(days=days_back)
            print(f"DEBUG: Regular day, showing {days_back} day(s) back: {target_date}")

    target_date_str = target_date.strftime('%Y-%m-%d')
    print(f"DEBUG: Final target date: {target_date_str}")

    query = """
        SELECT 
            cc.id,
            cc.communication_type,
            cc.notes,
            cc.date,
            CAST(cc.date AS TIME) as time_only,
            c.name as contact_name,
            c.second_name as contact_second_name,
            c.job_title,
            cu.name as customer_name,
            cc.contact_id,
            cc.customer_id,
            cl.id as call_list_id
        FROM contact_communications cc
        JOIN contacts c ON cc.contact_id = c.id
        JOIN customers cu ON cc.customer_id = cu.id
        LEFT JOIN call_list cl ON cl.contact_id = c.id 
            AND cl.salesperson_id = ?
        WHERE cc.salesperson_id = ? 
        AND DATE(cc.date) = ?
        ORDER BY cc.communication_type ASC, cu.name ASC, cc.date DESC
    """

    try:
        communications = db.execute(query, (salesperson_id, salesperson_id, target_date_str)).fetchall()
        print(f"DEBUG: Found {len(communications)} communications")

        # Group by communication type FIRST, then by company within each type
        grouped_by_type = {}
        type_counts = {}

        for comm in communications:
            # Standardize communication type names
            raw_type = (comm['communication_type'] or '').strip()
            comm_type = standardize_communication_type(raw_type) if raw_type else 'Other'
            company_name = comm['customer_name']

            print(f"DEBUG: Raw type '{raw_type}' -> Standardized type '{comm_type}', Company: '{company_name}'")

            # Initialize type if not exists
            if comm_type not in grouped_by_type:
                grouped_by_type[comm_type] = {}
                type_counts[comm_type] = 0

            # Initialize company within type if not exists
            if company_name not in grouped_by_type[comm_type]:
                grouped_by_type[comm_type][company_name] = []

            type_counts[comm_type] += 1

            # Build full contact name with second_name
            full_contact_name = comm['contact_name']
            if comm['contact_second_name']:
                full_contact_name += f" {comm['contact_second_name']}"

            grouped_by_type[comm_type][company_name].append({
                'id': comm['id'],
                'contact_name': full_contact_name,
                'contact_id': comm['contact_id'],
                'customer_name': comm['customer_name'],
                'customer_id': comm['customer_id'],
                'job_title': comm['job_title'],
                'notes': comm['notes'],
                'time': comm['time_only'],
                'full_datetime': comm['date'],
                'communication_type': comm_type,
                'call_list_id': comm['call_list_id']  # ADD THIS LINE
            })

        print(f"DEBUG: Final grouped types: {list(grouped_by_type.keys())}")
        print(f"DEBUG: Type counts: {type_counts}")

        # Sort each company's communications within each type by time (most recent first)
        for comm_type in grouped_by_type:
            for company in grouped_by_type[comm_type]:
                grouped_by_type[comm_type][company].sort(key=lambda x: x['full_datetime'], reverse=True)

        # Ensure time/date fields are JSON-serializable for Postgres (datetime objects)
        def _serialize_time(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            try:
                return value.strftime('%H:%M:%S')
            except Exception:
                return str(value)

        def _serialize_datetime(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            try:
                return value.isoformat()
            except Exception:
                return str(value)

        for comm_type in grouped_by_type:
            for company in grouped_by_type[comm_type]:
                for comm in grouped_by_type[comm_type][company]:
                    comm['time'] = _serialize_time(comm.get('time'))
                    comm['full_datetime'] = _serialize_datetime(comm.get('full_datetime'))

        # Sort the types by count (most frequent first)
        sorted_grouped = {}
        for comm_type in sorted(type_counts.keys(), key=lambda x: type_counts[x], reverse=True):
            sorted_grouped[comm_type] = grouped_by_type[comm_type]

        db.close()

        return {
            'communications': sorted_grouped,
            'target_date': target_date_str,
            'target_date_formatted': target_date.strftime('%A, %B %d, %Y'),
            'total_count': len(communications),
            'type_counts': type_counts
        }

    except Exception as e:
        print(f"Error getting recent communications: {e}")
        import traceback
        print(traceback.format_exc())
        db.close()
        return {
            'communications': {},
            'target_date': target_date_str if 'target_date' in locals() else datetime.now().strftime('%Y-%m-%d'),
            'target_date_formatted': target_date.strftime(
                '%A, %B %d, %Y') if 'target_date' in locals() else 'Unknown Date',
            'total_count': 0,
            'type_counts': {},
            'error': str(e)
        }


def standardize_communication_type(raw_type):
    """Standardize communication type names"""
    type_mapping = {
        'phone': 'Phone',
        'call': 'Phone',
        'telephone': 'Phone',
        'email': 'Email',
        'e-mail': 'Email',
        'meeting': 'Meeting',
        'video call': 'Video Call',
        'video': 'Video Call',
        'text': 'Text',
        'sms': 'Text',
        'visit': 'Visit',
        'other': 'Other'
    }

    return type_mapping.get(raw_type.lower(), raw_type.title())

def get_communication_types_for_salesperson(salesperson_id):
    """
    Get all communication types used by a salesperson (for dynamic tab creation)
    Returns standardized type names

    Args:
        salesperson_id: ID of the salesperson

    Returns:
        List of standardized communication types used by this salesperson
    """
    db = get_db_connection()

    query = """
        SELECT DISTINCT communication_type, COUNT(*) as usage_count
        FROM contact_communications
        WHERE salesperson_id = ?
        GROUP BY communication_type
        ORDER BY usage_count DESC, communication_type
    """

    try:
        types = db.execute(query, (salesperson_id,)).fetchall()
        db.close()

        # Standardize and deduplicate types
        standardized_types = []
        seen_types = set()

        for row in types:
            standardized = standardize_communication_type(row['communication_type'])
            if standardized not in seen_types:
                standardized_types.append(standardized)
                seen_types.add(standardized)

        return standardized_types

    except Exception as e:
        print(f"Error getting communication types: {e}")
        db.close()
        return []


def get_communication_stats_for_salesperson(salesperson_id, days=30):
    """
    Get communication statistics for a salesperson over a specified period

    Args:
        salesperson_id: ID of the salesperson
        days: Number of days to look back (default 30)

    Returns:
        Dict with communication statistics
    """
    from datetime import datetime, timedelta

    db = get_db_connection()

    start_date = (datetime.now().date() - timedelta(days=days)).strftime('%Y-%m-%d')

    query = """
        SELECT 
            communication_type,
            COUNT(*) as count,
            COUNT(DISTINCT customer_id) as unique_customers,
            COUNT(DISTINCT DATE(date)) as active_days
        FROM contact_communications
        WHERE salesperson_id = ? 
        AND date >= ?
        GROUP BY communication_type
        ORDER BY count DESC
    """

    try:
        stats = db.execute(query, (salesperson_id, start_date)).fetchall()
        db.close()

        # Process and standardize the results
        processed_stats = {}
        for row in stats:
            standardized_type = standardize_communication_type(row['communication_type'])
            if standardized_type not in processed_stats:
                processed_stats[standardized_type] = {
                    'count': 0,
                    'unique_customers': set(),
                    'active_days': set()
                }

            processed_stats[standardized_type]['count'] += row['count']
            processed_stats[standardized_type]['unique_customers'].add(row['unique_customers'])
            processed_stats[standardized_type]['active_days'].add(row['active_days'])

        # Convert sets to counts
        final_stats = {}
        for comm_type, data in processed_stats.items():
            final_stats[comm_type] = {
                'count': data['count'],
                'unique_customers': len(data['unique_customers']),
                'active_days': len(data['active_days'])
            }

        return {
            'period_days': days,
            'start_date': start_date,
            'stats': final_stats
        }

    except Exception as e:
        print(f"Error getting communication stats: {e}")
        db.close()
        return {'period_days': days, 'start_date': start_date, 'stats': {}}

# Add to models.py
def get_consolidated_customer_ids(salesperson_id):
    """Get main customer IDs with their associated companies consolidated"""
    db = get_db_connection()

    # Get all customers assigned to this salesperson
    main_customers_query = """
        SELECT id, name 
        FROM customers 
        WHERE salesperson_id = ?
    """
    main_customers = db.execute(main_customers_query, (salesperson_id,)).fetchall()

    consolidated_customers = {}

    for customer in main_customers:
        customer_id = customer['id']
        customer_name = customer['name']

        # Get associated customer IDs for this main customer
        associated_ids = [customer_id]  # Always include the main customer

        # Check if this customer has associated companies
        associated_query = """
            SELECT associated_customer_id 
            FROM customer_associations 
            WHERE main_customer_id = ?
        """
        associated_results = db.execute(associated_query, (customer_id,)).fetchall()

        if associated_results:
            child_ids = [row['associated_customer_id'] for row in associated_results]
            associated_ids.extend(child_ids)

        consolidated_customers[customer_id] = {
            'main_customer_id': customer_id,
            'main_customer_name': customer_name,
            'all_customer_ids': associated_ids
        }

    db.close()
    return consolidated_customers


def get_consolidated_customer_orders(customer_ids_list, start_date=None, end_date=None):
    """Get all orders for a list of customer IDs"""
    if not customer_ids_list:
        return []

    db = get_db_connection()
    placeholders = ','.join(['?' for _ in customer_ids_list])

    query = f"""
        SELECT 
            date_entered,
            CASE WHEN total_value IS NULL OR total_value::text = '' THEN 0 ELSE CAST(total_value AS REAL) END as total_value
        FROM sales_orders
        WHERE customer_id IN ({placeholders})
        AND (total_value IS NOT NULL AND total_value::text != '' AND CAST(total_value AS REAL) > 0)
    """

    params = customer_ids_list.copy()

    if start_date:
        query += " AND date_entered >= ?"
        params.append(start_date)

    if end_date:
        query += " AND date_entered <= ?"
        params.append(end_date)

    query += " ORDER BY date_entered DESC"

    orders = db_execute(query, params, fetch="all")
    db.close()

    return [dict(order) for order in orders]


# Add these functions to models.py

def get_all_deepdives():
    """Get all geographic deep dives with tag descriptions"""
    db = get_db_connection()
    try:
        query = """
            SELECT gd.*, it.tag as tag_description
            FROM geographic_deepdives gd
            LEFT JOIN industry_tags it ON gd.tag_id = it.id
            ORDER BY gd.updated_at DESC
        """
        return db_execute(query, fetch="all")
    except Exception as e:
        print(f"Error fetching deepdives: {str(e)}")
        return []
    finally:
        db.close()


def get_deepdive_by_id(deepdive_id):
    """Get a specific geographic deep dive"""
    db = get_db_connection()
    try:
        query = """
            SELECT gd.*, it.tag as tag_description
            FROM geographic_deepdives gd
            LEFT JOIN industry_tags it ON gd.tag_id = it.id
            WHERE gd.id = ?
        """
        return db_execute(query, [deepdive_id], fetch="one")
    except Exception as e:
        print(f"Error fetching deepdive {deepdive_id}: {str(e)}")
        return None
    finally:
        db.close()


def create_deepdive(country, tag_id, title, content):
    """Create a new geographic deep dive"""
    db = get_db_connection()
    try:
        # Check if deepdive already exists for this country/tag
        existing = db_execute("""
            SELECT id FROM geographic_deepdives 
            WHERE country = ? AND tag_id = ?
        """, [country, tag_id], fetch="one")

        if existing:
            return None, "Deep dive already exists for this country and tag combination"

        cursor = db.execute("""
            INSERT INTO geographic_deepdives (country, tag_id, title, content, created_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, [country, tag_id, title, content])

        db.commit()
        return cursor.lastrowid, None
    except Exception as e:
        db.rollback()
        print(f"Error creating deepdive: {str(e)}")
        return None, str(e)
    finally:
        db.close()


def update_deepdive(deepdive_id, title, content):
    """Update an existing geographic deep dive"""
    db = get_db_connection()
    try:
        db.execute("""
            UPDATE geographic_deepdives 
            SET title = ?, content = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [title, content, deepdive_id])
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error updating deepdive {deepdive_id}: {str(e)}")
        return False
    finally:
        db.close()


def delete_deepdive(deepdive_id):
    """Delete a geographic deep dive"""
    db = get_db_connection()
    try:
        db.execute("DELETE FROM geographic_deepdives WHERE id = ?", [deepdive_id])
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error deleting deepdive {deepdive_id}: {str(e)}")
        return False
    finally:
        db.close()


def get_all_tags_flat():
    """Get all industry tags as a flat list for dropdowns"""
    db = get_db_connection()
    try:
        return db_execute("SELECT id, tag as description FROM industry_tags ORDER BY tag", fetch="all")
    except Exception as e:
        print(f"Error fetching tags: {str(e)}")
        return []
    finally:
        db.close()


def get_countries_with_customers():
    """Get all countries that have customers with full country names"""
    db = get_db_connection()
    try:
        query = """
            SELECT DISTINCT country 
            FROM customers 
            WHERE country IS NOT NULL AND country != ''
            ORDER BY country
        """
        country_rows = db_execute(query, fetch="all")

        # Convert to list of dictionaries with both code and name
        countries = []
        for row in country_rows:
            country_code = row['country']
            country_name = get_country_name(country_code)
            countries.append({
                'code': country_code,
                'name': country_name,
                'display_name': f"{country_name} ({country_code})" if country_name != country_code else country_code
            })

        return countries
    except Exception as e:
        print(f"Error fetching countries: {str(e)}")
        return []
    finally:
        db.close()

def get_country_customers_by_tag(country, tag_id):
    """Get customers in a specific country with a specific tag"""
    db = get_db_connection()
    try:
        query = """
            SELECT DISTINCT c.*, s.name as assigned_salesperson_name
            FROM customers c
            LEFT JOIN salespeople s ON c.salesperson_id = s.id
            JOIN customer_industry_tags ct ON c.id = ct.customer_id
            WHERE LOWER(c.country) = LOWER(?) AND ct.tag_id = ?
            ORDER BY c.name
        """
        customers = db_execute(query, [country, tag_id], fetch="all")
        return [dict(row) for row in customers]
    except Exception as e:
        print(f"Error fetching customers for {country}, tag {tag_id}: {str(e)}")
        return []
    finally:
        db.close()


def extract_company_names_from_markdown(content):
    """Extract company names from markdown content using various patterns"""
    import re

    companies = set()

    # Pattern 1: Bold company names (**Company Name**)
    bold_pattern = r'\*\*([A-Z][A-Za-z\s&.-]{2,40}?)\*\*'
    companies.update(re.findall(bold_pattern, content))

    # Pattern 2: Lines starting with bullet points and company names
    bullet_pattern = r'^\s*[-*]\s+\*\*([A-Z][A-Za-z\s&.-]{2,40}?)\*\*'
    companies.update(re.findall(bullet_pattern, content, re.MULTILINE))

    # Pattern 3: After dashes (common in your example)
    dash_pattern = r'–\s+([A-Z][A-Za-z\s&.-]{2,40}?)(?:\s+–|\s+\(|$)'
    companies.update(re.findall(dash_pattern, content))

    # Clean up extracted names
    cleaned_companies = []
    for company in companies:
        # Remove common suffixes/prefixes that aren't part of company names
        cleaned = re.sub(r'\s+(via|at|in|from|with|and|or|the)$', '', company.strip())
        if len(cleaned) > 2 and not cleaned.lower() in ['hems', 'sar', 'mro', 'part']:
            cleaned_companies.append(cleaned)

    return list(set(cleaned_companies))


def match_companies_to_customers(company_names, country_customers):
    """Match extracted company names to actual customers in database"""
    matches = []
    unmatched = []

    for company_name in company_names:
        best_match = None
        best_score = 0

        for customer in country_customers:
            customer_name = customer['name'].lower()
            company_lower = company_name.lower()

            # Exact match
            if customer_name == company_lower:
                best_match = customer
                best_score = 100
                break

            # Partial matches
            if company_lower in customer_name or customer_name in company_lower:
                score = 80
                if score > best_score:
                    best_match = customer
                    best_score = score

            # Word overlap match
            company_words = set(company_lower.split())
            customer_words = set(customer_name.split())
            overlap = len(company_words.intersection(customer_words))
            if overlap > 0:
                score = (overlap / max(len(company_words), len(customer_words))) * 60
                if score > best_score and score > 30:
                    best_match = customer
                    best_score = score

        if best_match and best_score > 50:
            matches.append({
                'extracted_name': company_name,
                'customer': best_match,
                'match_confidence': best_score,
                'status': 'matched'
            })
        else:
            unmatched.append({
                'extracted_name': company_name,
                'customer': None,
                'match_confidence': 0,
                'status': 'unmatched'
            })

    return matches + unmatched


# Add these functions to your models.py file
def get_curated_customers_for_deepdive(deepdive_id):
    """Get curated customers for a specific deepdive with full status/priority info"""
    db = get_db_connection()
    try:
        query = """
            SELECT c.*, 
                   s.name as assigned_salesperson_name, 
                   dcc.notes as deepdive_notes,
                   dcc.order_index,
                   cs.status as status,
                   p.name as priority_name,
                   p.color as priority_color
            FROM deepdive_curated_customers dcc
            JOIN customers c ON dcc.customer_id = c.id
            LEFT JOIN salespeople s ON c.salesperson_id = s.id
            LEFT JOIN customer_status cs ON c.status_id = cs.id
            LEFT JOIN priorities p ON c.priority = p.id
            WHERE dcc.deepdive_id = ?
            ORDER BY dcc.order_index ASC, c.name ASC
        """
        customers = db_execute(query, [deepdive_id], fetch="all")
        return [dict(row) for row in customers]
    except Exception as e:
        print(f"Error fetching curated customers for deepdive {deepdive_id}: {str(e)}")
        return []
    finally:
        db.close()

def add_customer_to_deepdive(deepdive_id, customer_id, notes=None):
    """Add a customer to a deepdive's curated list"""
    db = get_db_connection()
    try:
        # Check if already exists
        existing = db_execute("""
            SELECT id FROM deepdive_curated_customers 
            WHERE deepdive_id = ? AND customer_id = ?
        """, [deepdive_id, customer_id], fetch="one")

        if existing:
            return False, "Customer already in curated list"

        # Get next order index
        max_order = db.execute("""
            SELECT COALESCE(MAX(order_index), 0) + 1 
            FROM deepdive_curated_customers 
            WHERE deepdive_id = ?
        """, [deepdive_id]).fetchone()[0]

        db.execute("""
            INSERT INTO deepdive_curated_customers (deepdive_id, customer_id, notes, order_index)
            VALUES (?, ?, ?, ?)
        """, [deepdive_id, customer_id, notes, max_order])

        db.commit()
        return True, None
    except Exception as e:
        db.rollback()
        print(f"Error adding customer to deepdive: {str(e)}")
        return False, str(e)
    finally:
        db.close()


def remove_customer_from_deepdive(deepdive_id, customer_id):
    """Remove a customer from a deepdive's curated list"""
    db = get_db_connection()
    try:
        db.execute("""
            DELETE FROM deepdive_curated_customers 
            WHERE deepdive_id = ? AND customer_id = ?
        """, [deepdive_id, customer_id])
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error removing customer from deepdive: {str(e)}")
        return False
    finally:
        db.close()


def update_customer_notes_in_deepdive(deepdive_id, customer_id, notes):
    """Update notes for a customer in a deepdive"""
    db = get_db_connection()
    try:
        db.execute("""
            UPDATE deepdive_curated_customers 
            SET notes = ? 
            WHERE deepdive_id = ? AND customer_id = ?
        """, [notes, deepdive_id, customer_id])
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error updating customer notes: {str(e)}")
        return False
    finally:
        db.close()


def search_customers_for_deepdive(search_term, country=None, tag_id=None, limit=10):
    """Search customers that can be added to a deepdive"""
    db = get_db_connection()
    try:
        base_query = """
            SELECT DISTINCT c.*, s.name as assigned_salesperson_name
            FROM customers c
            LEFT JOIN salespeople s ON c.salesperson_id = s.id
            LEFT JOIN customer_industry_tags ct ON c.id = ct.customer_id
            WHERE c.name LIKE ?
        """
        params = [f"%{search_term}%"]

        if country:
            base_query += " AND LOWER(c.country) = LOWER(?)"
            params.append(country)

        if tag_id:
            base_query += " AND ct.tag_id = ?"
            params.append(tag_id)

        base_query += " ORDER BY c.name LIMIT ?"
        params.append(limit)

        customers = db_execute(base_query, params, fetch="all")
        return [dict(row) for row in customers]
    except Exception as e:
        print(f"Error searching customers: {str(e)}")
        return []
    finally:
        db.close()


def add_customer_link_to_deepdive(deepdive_id, customer_id, linked_text):
    """Add a text-to-customer link for a deepdive and ensure the customer is curated."""
    db = get_db_connection()
    try:
        cursor = db.cursor()
        existing_link = cursor.execute(
            """
            SELECT id
            FROM deepdive_customer_links
            WHERE deepdive_id = ? AND customer_id = ? AND linked_text = ?
            """,
            (deepdive_id, customer_id, linked_text),
        ).fetchone()
        if existing_link:
            return False, 'This customer is already linked to that text'

        cursor.execute(
            """
            INSERT INTO deepdive_customer_links
            (deepdive_id, customer_id, linked_text)
            VALUES (?, ?, ?)
            """,
            (deepdive_id, customer_id, linked_text),
        )

        existing_curated = cursor.execute(
            """
            SELECT id
            FROM deepdive_curated_customers
            WHERE deepdive_id = ? AND customer_id = ?
            """,
            (deepdive_id, customer_id),
        ).fetchone()

        if not existing_curated:
            next_order_row = cursor.execute(
                """
                SELECT COALESCE(MAX(order_index), 0) + 1 AS next_order
                FROM deepdive_curated_customers
                WHERE deepdive_id = ?
                """,
                (deepdive_id,),
            ).fetchone()
            next_order = next_order_row['next_order'] if next_order_row else 1

            cursor.execute(
                """
                INSERT INTO deepdive_curated_customers (deepdive_id, customer_id, notes, order_index)
                VALUES (?, ?, ?, ?)
                """,
                (deepdive_id, customer_id, None, next_order),
            )

        db.commit()
        return True, None
    except Exception as e:
        db.rollback()
        return False, str(e)
    finally:
        db.close()


def get_customer_links_for_deepdive(deepdive_id):
    """Get all customer links for a deepdive with customer details"""
    try:
        rows = db_execute(
            """
            SELECT 
                dcl.linked_text,
                dcl.customer_id,
                c.name as customer_name,
                c.country,
                cs.status as customer_status,
                cs.id as status_id
            FROM deepdive_customer_links dcl
            JOIN customers c ON dcl.customer_id = c.id
            LEFT JOIN customer_status cs ON c.status_id = cs.id
            WHERE dcl.deepdive_id = ?
            ORDER BY dcl.linked_text
            """,
            (deepdive_id,),
            fetch='all',
        )

        return {
            row['linked_text']: {
                'customer_id': row['customer_id'],
                'customer_name': row['customer_name'],
                'country': row['country'],
                'status': row['customer_status'],
                'status_id': row['status_id']
            } for row in rows or []
        }

    except Exception as e:
        print(f"Error getting customer links: {e}")
        return {}


def remove_customer_link_from_deepdive(deepdive_id, customer_id, linked_text):
    """Remove a specific text-to-customer link"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM deepdive_customer_links 
            WHERE deepdive_id = ? AND customer_id = ? AND linked_text = ?
        """, (deepdive_id, customer_id, linked_text))

        conn.commit()
        conn.close()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"Error removing customer link: {e}")
        return False


def get_sales_orders_paginated(page=1, per_page=25, customer_id=None, salesperson_id=None,
                               status_id=None, search=None, sort_by='id', sort_order='desc',
                               show_mismatches_only=False, date_from=None, date_to=None):
    """
    Fetch sales orders with pagination, filtering, and sorting.

    Returns a dictionary with 'orders' and 'pagination' keys.
    """
    db = get_db_connection()

    # Build WHERE clause based on filters
    where_clauses = []
    params = []

    if customer_id:
        where_clauses.append('so.customer_id = ?')
        params.append(customer_id)

    if salesperson_id:
        where_clauses.append('so.salesperson_id = ?')
        params.append(salesperson_id)

    if status_id:
        where_clauses.append('so.sales_status_id = ?')
        params.append(status_id)

    if search:
        where_clauses.append('''(
            so.sales_order_ref LIKE ? OR 
            so.customer_po_ref LIKE ? OR 
            c.name LIKE ?
        )''')
        search_param = f'%{search}%'
        params.extend([search_param, search_param, search_param])

    # Filter for salesperson mismatches
    if show_mismatches_only:
        where_clauses.append('''(
            c.salesperson_id IS NOT NULL 
            AND (so.salesperson_id IS NULL OR so.salesperson_id != c.salesperson_id)
        )''')

    # Date range filters
    if date_from:
        where_clauses.append('so.date_entered >= ?')
        params.append(date_from)

    if date_to:
        where_clauses.append('so.date_entered <= ?')
        params.append(date_to)

    where_sql = 'WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''

    # Validate and sanitize sort parameters
    valid_sort_columns = {
        'id': 'so.id',
        'sales_order_ref': 'so.sales_order_ref',
        'customer_name': 'c.name',
        'customer_po_ref': 'so.customer_po_ref',
        'date_entered': 'so.date_entered',
        'total_value': 'so.total_value',
        'status_name': 'ss.status_name',
        'salesperson_name': 'sp.name'
    }

    sort_column = valid_sort_columns.get(sort_by, 'so.id')
    sort_direction = 'ASC' if sort_order.lower() == 'asc' else 'DESC'

    # Count total records
    count_query = f'''
        SELECT COUNT(*) 
        FROM sales_orders so
        LEFT JOIN customers c ON so.customer_id = c.id
        LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
        LEFT JOIN salespeople sp ON so.salesperson_id = sp.id
        {where_sql}
    '''

    count_result = db_execute(count_query, params, fetch="one")
    total_records = count_result.get("count", 0) if count_result else 0
    total_pages = (total_records + per_page - 1) // per_page  # Ceiling division

    # Get paginated records
    offset = (page - 1) * per_page

    query = f'''
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
        {where_sql}
        ORDER BY {sort_column} {sort_direction}
        LIMIT ? OFFSET ?
    '''

    params.extend([per_page, offset])
    sales_orders = db_execute(query, params, fetch="all")
    db.close()

    # Calculate pagination info
    pagination = {
        'page': page,
        'per_page': per_page,
        'total_records': total_records,
        'total_pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if page < total_pages else None,
        'start_record': offset + 1 if total_records > 0 else 0,
        'end_record': min(offset + per_page, total_records)
    }

    return {
        'orders': [dict(order) for order in sales_orders],
        'pagination': pagination
    }
