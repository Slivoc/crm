import json
import logging
import time
import re
import requests
from flask import current_app, g, render_template, abort
from db import get_db_connection, execute as db_execute, db_cursor, _using_postgres
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

def get_customers_by_country(country_code: str) -> List[Dict]:
    """
    Get customers from a specific country
    """
    query = """
        SELECT c.*, cs.status as status_name 
        FROM customers c
        LEFT JOIN customer_status cs ON c.status_id = cs.id
        WHERE c.country = ?
    """
    rows = db_execute(query, (country_code,), fetch='all') or []
    return [dict(row) for row in rows]


def get_customers_by_continent(continent: str) -> List[Dict]:
    """
    Get customers from a specific continent
    """
    # Get all country codes for this continent
    continent_mapping = get_countries_by_continent()
    continent_countries = continent_mapping.get(continent, [])

    if not continent_countries:
        return []

    placeholders = ','.join(['?' for _ in continent_countries])
    query = f"""
        SELECT c.*, cs.status as status_name 
        FROM customers c
        LEFT JOIN customer_status cs ON c.status_id = cs.id
        WHERE c.country IN ({placeholders})
    """

    rows = db_execute(query, tuple(continent_countries), fetch='all') or []
    return [dict(row) for row in rows]


def get_all_tags():
    """Get all tags in a hierarchical structure with customer counts"""
    try:
        customer_counts = db_execute('''
            SELECT t.id, COUNT(DISTINCT cit.customer_id) as count
            FROM industry_tags t
            LEFT JOIN customer_industry_tags cit ON t.id = cit.tag_id
            GROUP BY t.id
        ''', fetch='all') or []
        count_dict = {row['id']: row['count'] for row in customer_counts}

        tags = db_execute('''
            WITH RECURSIVE nested_tags AS (
                -- Base case: get root tags (no parent)
                SELECT 
                    id, 
                    tag as name, 
                    parent_tag_id, 
                    0 as level,
                    CAST(id as TEXT) as path_ids,
                    CAST(tag as TEXT) as path_names
                FROM industry_tags 
                WHERE parent_tag_id IS NULL

                UNION ALL

                -- Recursive case: get child tags
                SELECT 
                    t.id, 
                    t.tag as name, 
                    t.parent_tag_id, 
                    nt.level + 1,
                    nt.path_ids || ',' || CAST(t.id as TEXT),
                    nt.path_names || ' > ' || t.tag
                FROM industry_tags t
                JOIN nested_tags nt ON t.parent_tag_id = nt.id
            )
            SELECT 
                id,
                name,
                parent_tag_id,
                level,
                path_ids,
                path_names
            FROM nested_tags
            ORDER BY path_names;
        ''', fetch='all') or []

        def build_tag_tree(tags_list, parent_id=None):
            """Recursively build the tag tree"""
            tree = []
            for tag in tags_list:
                if tag['parent_tag_id'] == parent_id:
                    total_count = count_dict.get(tag['id'], 0)
                    children = build_tag_tree(tags_list, tag['id'])
                    for child in children:
                        total_count += child.get('customer_count', 0)
                    node = {
                        'id': tag['id'],
                        'name': tag['name'],
                        'level': tag['level'],
                        'customer_count': total_count,
                        'children': children
                    }
                    tree.append(node)
            return tree

        return build_tag_tree(tags)
    except Exception as e:
        logging.error("Error in get_all_tags: %s", e)
        raise

def get_customer_data(customer_id):
    """Get customer data needed for enrichment"""
    try:
        customer = db_execute('''
            SELECT 
                id,
                name,
                description,
                website,
                country,
                estimated_revenue
            FROM customers 
            WHERE id = ?
        ''', (customer_id,), fetch='one')

        current_tags = db_execute('''
            SELECT it.id, it.tag as name
            FROM customer_industry_tags cit
            JOIN industry_tags it ON cit.tag_id = it.id
            WHERE cit.customer_id = ?
        ''', (customer_id,), fetch='all') or []

        current_company_types = db_execute('''
            SELECT ct.id, ct.type as name
            FROM customer_company_types cct
            JOIN company_types ct ON cct.company_type_id = ct.id
            WHERE cct.customer_id = ?
        ''', (customer_id,), fetch='all') or []

        return customer, current_tags, current_company_types
    except Exception as e:
        logging.error("Error in get_customer_data: %s", e)
        raise

def get_available_tags():
    """Get all available tags for enrichment"""
    rows = db_execute('''
        SELECT id, tag as name, parent_tag_id
        FROM industry_tags
        ORDER BY tag
    ''', fetch='all') or []
    return [dict(row) for row in rows]

def get_available_company_types():
    """Get all available company types"""
    rows = db_execute('''
        SELECT id, type, description, parent_type_id
        FROM company_types
        ORDER BY type
    ''', fetch='all') or []
    return [dict(row) for row in rows]

def get_customer(customer_id):
    customer = db_execute('SELECT * FROM customers WHERE id = ?', (customer_id,), fetch='one')
    return dict(customer) if customer else None

def update_customer_apollo_id(customer_id, apollo_id):
    try:
        db_execute(
            'UPDATE customers SET apollo_id = ? WHERE id = ?',
            (apollo_id, customer_id),
            commit=True
        )
        return True
    except Exception:
        return False


def get_all_templates():
    """Fetch all email templates with simple aggregation metadata.

    Note: avoids SQLite-only GROUP_CONCAT by fetching tags in a second query and
    assembling them in Python.
    """
    templates = db_execute(
        "SELECT * FROM email_templates ORDER BY updated_at DESC",
        fetch="all",
    ) or []

    template_ids = [t["id"] if isinstance(t, dict) else t[0] for t in templates]
    tags_by_template: Dict[int, List[str]] = {}

    if template_ids:
        placeholders = ",".join(["?"] * len(template_ids))
        tag_rows = db_execute(
            f"""
            SELECT tit.template_id, it.tag
            FROM template_industry_tags tit
            JOIN industry_tags it ON tit.industry_tag_id = it.id
            WHERE tit.template_id IN ({placeholders})
            ORDER BY tit.template_id, it.tag
            """,
            template_ids,
            fetch="all",
        ) or []

        for r in tag_rows:
            template_id = r["template_id"] if isinstance(r, dict) else r[0]
            tag = r["tag"] if isinstance(r, dict) else r[1]
            tags_by_template.setdefault(int(template_id), []).append(tag)

    result = []
    for t in templates:
        td = dict(t)
        tags = tags_by_template.get(int(td.get("id")), [])
        td["industry_tags"] = ",".join(tags)
        td["tag_count"] = len(tags)
        result.append(td)

    return result


def get_template_by_id(template_id):
    template_row = db_execute(
        "SELECT * FROM email_templates WHERE id = ?",
        (template_id,),
        fetch="one",
    )

    if not template_row:
        return None

    template = dict(template_row)
    tags = db_execute(
        """
        SELECT it.id, it.tag as name
        FROM industry_tags it
        JOIN template_industry_tags tit ON it.id = tit.industry_tag_id
        WHERE tit.template_id = ?
        ORDER BY it.tag
        """,
        (template_id,),
        fetch="all",
    ) or []

    template["industry_tags"] = [dict(r) for r in tags]
    return template


def get_all_placeholders():
    placeholders = db_execute(
        "SELECT * FROM template_placeholders ORDER BY placeholder_key",
        fetch="all",
    ) or []
    return [dict(p) for p in placeholders]

def get_all_template_tags():
    """Get all tags in a hierarchical structure for templates"""
    # Get the hierarchical tag structure without customer counts
    tags = db_execute('''
        WITH RECURSIVE nested_tags AS (
            -- Base case: get root tags (no parent)
            SELECT 
                id, 
                tag as name, 
                parent_tag_id, 
                0 as level,
                CAST(id as TEXT) as path_ids,
                CAST(tag as TEXT) as path_names
            FROM industry_tags 
            WHERE parent_tag_id IS NULL

            UNION ALL

            -- Recursive case: get child tags
            SELECT 
                t.id, 
                t.tag as name, 
                t.parent_tag_id, 
                nt.level + 1,
                nt.path_ids || ',' || CAST(t.id as TEXT),
                nt.path_names || ' > ' || t.tag
            FROM industry_tags t
            JOIN nested_tags nt ON t.parent_tag_id = nt.id
        )
        SELECT 
            id,
            name,
            parent_tag_id,
            level,
            path_ids,
            path_names
        FROM nested_tags
        ORDER BY path_names;
    ''', fetch='all')
    return tags or []


def get_all_contacts():
    """Get all contacts with their basic information"""
    contacts = db_execute('''
        SELECT 
            id,
            customer_id,
            name,
            email,
            job_title
        FROM contacts
        ORDER BY name
    ''', fetch='all')
    return contacts or []


def save_email_log(email_data: Dict) -> Optional[int]:
    """Save an email log row and return its ID."""
    try:
        row = db_execute(
            """
            INSERT INTO email_logs (
                template_id,
                contact_id,
                customer_id,
                subject,
                recipient_email,
                status,
                error_message,
                sent_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                email_data["template_id"],
                email_data["contact_id"],
                email_data.get("customer_id"),
                email_data["subject"],
                email_data["recipient_email"],
                email_data.get("status", "sent"),
                email_data.get("error_message"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
            fetch="one",
            commit=True,
        )
        if not row:
            return None
        return row.get("id", list(row.values())[0])
    except Exception as e:
        print(f"Error saving email log: {str(e)}")
        return None


def get_email_logs(limit: int = 50, offset: int = 0) -> list:
    """Retrieve email logs with pagination."""
    try:
        rows = db_execute(
            """
            SELECT
                el.*,
                t.name as template_name,
                c.name as contact_name,
                cu.name as customer_name
            FROM email_logs el
            LEFT JOIN email_templates t ON el.template_id = t.id
            LEFT JOIN contacts c ON el.contact_id = c.id
            LEFT JOIN customers cu ON el.customer_id = cu.id
            ORDER BY el.sent_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
            fetch="all",
        ) or []
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error retrieving email logs: {str(e)}")
        return []


def get_email_log_by_id(log_id: int) -> Optional[Dict]:
    """Retrieve a specific email log by ID."""
    try:
        row = db_execute(
            """
            SELECT
                el.*,
                t.name as template_name,
                c.name as contact_name,
                cu.name as customer_name
            FROM email_logs el
            LEFT JOIN email_templates t ON el.template_id = t.id
            LEFT JOIN contacts c ON el.contact_id = c.id
            LEFT JOIN customers cu ON el.customer_id = cu.id
            WHERE el.id = ?
            """,
            (log_id,),
            fetch="one",
        )
        return dict(row) if row else None
    except Exception as e:
        print(f"Error retrieving email log: {str(e)}")
        return None

def get_email_signature_by_id(signature_id):
    signature = db_execute("SELECT * FROM email_signatures WHERE id = ?", (signature_id,), fetch='one')

    if signature:
        return {
            'id': signature['id'],
            'name': signature['name'],
            'signature_html': signature['signature_html'],
            'created_at': signature['created_at']
        }
    return None


def update_contact(contact_id, name, second_name, email, job_title, phone=None, status_id=1, customer_id=None, timezone=None):
    """
    Update a contact with all editable fields

    Args:
        contact_id: ID of the contact to update
        name: First name
        second_name: Last name/surname
        email: Email address
        job_title: Job title
        phone: Phone number (optional)
        status_id: Status ID (defaults to 1 - active)
        customer_id: Customer ID (optional - allows moving contact to different customer)
        timezone: Timezone (optional)
    """
    if customer_id is not None:
        # Update contact including customer_id (allows moving contact between customers)
        query = """
            UPDATE contacts
            SET name = ?, second_name = ?, email = ?, job_title = ?, phone = ?, status_id = ?, customer_id = ?, timezone = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """
        params = (name, second_name, email, job_title, phone, status_id, customer_id, timezone, contact_id)
    else:
        # Update contact without changing customer_id (maintains existing customer relationship)
        query = """
            UPDATE contacts
            SET name = ?, second_name = ?, email = ?, job_title = ?, phone = ?, status_id = ?, timezone = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """
        params = (name, second_name, email, job_title, phone, status_id, timezone, contact_id)

    db_execute(query, params, commit=True)


def get_salesperson_contacts(salesperson_id, search_term='', customer_filter='', status_filter='',
                             customer_status_filter='', sort_by='', sort_order='asc',
                             name_filter='', job_title_filter='', my_communications_only=False,
                             call_list_only=False):
    """Get all contacts from customers assigned to a specific salesperson."""
    db = get_db_connection()

    try:
        # Get customers assigned to this salesperson
        customers_query = """
            SELECT DISTINCT id
            FROM customers 
            WHERE salesperson_id = ?
        """

        customers = db.execute(customers_query, (salesperson_id,)).fetchall()

        print(f"Found {len(customers)} customers for salesperson {salesperson_id}")

        if not customers:
            db.close()
            return []

        # Create a list of customer IDs
        customer_ids = [c['id'] for c in customers]
        placeholders = ','.join(['?'] * len(customer_ids))

        # Build the communication filter condition
        comm_filter = "WHERE cc.salesperson_id = ?" if my_communications_only else ""

        if _using_postgres():
            latest_comm_expr = "ca.latest_communication_date"
            latest_comm_order_expr = "COALESCE(ca.latest_communication_date, TIMESTAMP '1900-01-01')"
            # Updated query to include user information for communications
            query_parts = [
                f"""
                WITH comm_base AS (
                    SELECT cc.id,
                           cc.contact_id,
                           cc.date::timestamp as date,
                           cc.notes,
                           cc.communication_type,
                           cc.salesperson_id
                    FROM contact_communications cc
                    {comm_filter}
                ),
                comm_agg AS (
                    SELECT contact_id,
                           COUNT(*) as communication_count,
                           MAX(date) as latest_communication_date
                    FROM comm_base
                    GROUP BY contact_id
                ),
                comm_latest AS (
                    SELECT contact_id, notes, communication_type, salesperson_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY contact_id
                               ORDER BY date DESC, id DESC
                           ) as rn
                    FROM comm_base
                )
                SELECT 
                    c.id, 
                    c.name, 
                    c.second_name,
                    c.email,
                    c.phone,
                    c.job_title,
                    c.notes,
                    c.status_id,
                    c.customer_id, 
                    cu.name as customer_name,
                    cu.status_id as customer_status_id,
                    cus.status as customer_status_name,
                    cs.name as status_name,
                    cs.color as status_color,
                    c.updated_at,
                    COALESCE(ca.communication_count, 0) as communication_count,
                    ca.latest_communication_date as latest_communication_date,
                    cl.notes as latest_update,
                    cl.communication_type as latest_communication_type,
                    s.name as latest_communication_user
                FROM contacts c
                JOIN customers cu ON c.customer_id = cu.id
                LEFT JOIN contact_statuses cs ON c.status_id = cs.id
                LEFT JOIN customer_status cus ON cu.status_id = cus.id
                LEFT JOIN comm_agg ca ON ca.contact_id = c.id
                LEFT JOIN comm_latest cl ON cl.contact_id = c.id AND cl.rn = 1
                LEFT JOIN salespeople s ON cl.salesperson_id = s.id
                """
            ]
        else:
            latest_comm_subquery = f"""
                        SELECT MAX(cc.date) 
                        FROM contact_communications cc
                        WHERE cc.contact_id = c.id
                        {"AND cc.salesperson_id = ?" if my_communications_only else ""}
                    """
            latest_comm_expr = f"({latest_comm_subquery.strip()})"
            latest_comm_order_expr = f"COALESCE({latest_comm_expr}, '1900-01-01')"

            # Updated query to include user information for communications
            query_parts = [
                f"""
                SELECT 
                    c.id, 
                    c.name, 
                    c.second_name,
                    c.email,
                    c.phone,
                    c.job_title,
                    c.notes,
                    c.status_id,
                    c.customer_id, 
                    cu.name as customer_name,
                    cu.status_id as customer_status_id,
                    cus.status as customer_status_name,
                    cs.name as status_name,
                    cs.color as status_color,
                    c.updated_at,
                    (
                        SELECT COUNT(*) 
                        FROM contact_communications cc
                        WHERE cc.contact_id = c.id
                        {"AND cc.salesperson_id = ?" if my_communications_only else ""}
                    ) as communication_count,
                    {latest_comm_expr} as latest_communication_date,
                    (
                        SELECT cc.notes 
                        FROM contact_communications cc
                        WHERE cc.contact_id = c.id 
                        {"AND cc.salesperson_id = ?" if my_communications_only else ""}
                        ORDER BY cc.date DESC, cc.id DESC LIMIT 1
                    ) as latest_update,
                    (
                        SELECT cc.communication_type 
                        FROM contact_communications cc
                        WHERE cc.contact_id = c.id 
                        {"AND cc.salesperson_id = ?" if my_communications_only else ""}
                        ORDER BY cc.date DESC, cc.id DESC LIMIT 1
                    ) as latest_communication_type,
                    (
                        SELECT s.name 
                        FROM contact_communications cc
                        JOIN salespeople s ON cc.salesperson_id = s.id
                        WHERE cc.contact_id = c.id 
                        {"AND cc.salesperson_id = ?" if my_communications_only else ""}
                        ORDER BY cc.date DESC, cc.id DESC LIMIT 1
                    ) as latest_communication_user
                FROM contacts c
                JOIN customers cu ON c.customer_id = cu.id
                LEFT JOIN contact_statuses cs ON c.status_id = cs.id
                LEFT JOIN customer_status cus ON cu.status_id = cus.id
                """
            ]

        # Left join with call_list table if we need to filter by it
        if call_list_only:
            query_parts.append("""
            JOIN call_list cl ON c.id = cl.contact_id 
                AND cl.salesperson_id = ?
                AND cl.is_active = TRUE
            """)

        # Continue with WHERE clause
        query_parts.append(f"WHERE c.customer_id IN ({placeholders})")

        # Build parameters in the correct order
        params = []

        # Add salesperson_id parameter for communication CTE/subqueries if needed
        if my_communications_only:
            if _using_postgres():
                params.append(salesperson_id)
            else:
                params.extend([salesperson_id] * 5)  # 5 subqueries that need the salesperson_id

        # Add salesperson_id for call_list JOIN if needed
        if call_list_only:
            params.append(salesperson_id)

        # Add customer_ids for the WHERE clause
        params.extend(customer_ids)

        # Add search filters if provided
        if search_term:
            query_parts.append(
                "AND (c.name LIKE ? OR c.second_name LIKE ? OR c.email LIKE ? OR c.phone LIKE ? OR c.job_title LIKE ? OR c.notes LIKE ? OR cs.name LIKE ? OR cus.status LIKE ?)")
            search_pattern = f"%{search_term}%"
            params.extend(
                [search_pattern, search_pattern, search_pattern, search_pattern, search_pattern, search_pattern,
                 search_pattern, search_pattern])

        # Add individual filter parameters
        if name_filter:
            query_parts.append("AND (c.name LIKE ? OR c.second_name LIKE ?)")
            name_pattern = f"%{name_filter}%"
            params.extend([name_pattern, name_pattern])

        if customer_filter:
            query_parts.append("AND cu.name LIKE ?")
            params.append(f"%{customer_filter}%")

        if job_title_filter:
            query_parts.append("AND c.job_title LIKE ?")
            params.append(f"%{job_title_filter}%")

        # FIXED: Add status filter - handle both string and list with CASE-INSENSITIVE comparison
        if status_filter:
            # Convert to list if it's a string
            if isinstance(status_filter, str):
                status_filter = [status_filter] if status_filter else []

            if status_filter:  # If list has items
                if 'no status' in [s.lower() for s in status_filter]:
                    # Handle "no status" option
                    other_statuses = [s for s in status_filter if s.lower() != 'no status']
                    if other_statuses:
                        # Include both specific statuses and NULL - CASE INSENSITIVE
                        placeholders_status = ','.join(['?'] * len(other_statuses))
                        query_parts.append(f"AND (LOWER(cs.name) IN ({placeholders_status}) OR c.status_id IS NULL)")
                        params.extend([s.lower() for s in other_statuses])  # Convert to lowercase
                    else:
                        # Only "no status" selected
                        query_parts.append("AND c.status_id IS NULL")
                else:
                    # Only specific statuses (no "no status") - CASE INSENSITIVE
                    placeholders_status = ','.join(['?'] * len(status_filter))
                    query_parts.append(f"AND LOWER(cs.name) IN ({placeholders_status})")
                    params.extend([s.lower() for s in status_filter])  # Convert to lowercase

        # FIXED: Add customer status filter - handle both string and list with CASE-INSENSITIVE comparison
        if customer_status_filter:
            # Convert to list if it's a string
            if isinstance(customer_status_filter, str):
                customer_status_filter = [customer_status_filter] if customer_status_filter else []

            if customer_status_filter:  # If list has items
                if 'no status' in [s.lower() for s in customer_status_filter]:
                    # Handle "no status" option
                    other_statuses = [s for s in customer_status_filter if s.lower() != 'no status']
                    if other_statuses:
                        # Include both specific statuses and NULL - CASE INSENSITIVE
                        placeholders_cust_status = ','.join(['?'] * len(other_statuses))
                        query_parts.append(
                            f"AND (LOWER(cus.status) IN ({placeholders_cust_status}) OR cu.status_id IS NULL)")
                        params.extend([s.lower() for s in other_statuses])  # Convert to lowercase
                    else:
                        # Only "no status" selected
                        query_parts.append("AND cu.status_id IS NULL")
                else:
                    # Only specific statuses (no "no status") - CASE INSENSITIVE
                    placeholders_cust_status = ','.join(['?'] * len(customer_status_filter))
                    query_parts.append(f"AND LOWER(cus.status) IN ({placeholders_cust_status})")
                    params.extend([s.lower() for s in customer_status_filter])  # Convert to lowercase

        # Add sorting based on sort_by parameter
        if sort_by and sort_order:
            order_direction = "ASC" if sort_order.lower() == 'asc' else "DESC"

            if sort_by == 'name':
                query_parts.append(f"ORDER BY c.name {order_direction}, c.second_name {order_direction}")
            elif sort_by == 'customer':
                query_parts.append(f"ORDER BY cu.name {order_direction}")
            elif sort_by == 'job_title':
                query_parts.append(f"ORDER BY c.job_title {order_direction}")
            elif sort_by == 'status':
                query_parts.append(f"ORDER BY cs.name {order_direction}")
            elif sort_by == 'latest_communication':
                if order_direction == 'ASC':
                    query_parts.append(f"ORDER BY {latest_comm_order_expr} ASC, c.name, c.second_name")
                else:
                    query_parts.append(f"ORDER BY {latest_comm_order_expr} DESC, c.name, c.second_name")
            elif sort_by == 'days_since_contact':
                if order_direction == 'ASC':
                    query_parts.append(f"ORDER BY {latest_comm_order_expr} DESC, c.name, c.second_name")
                else:
                    query_parts.append(f"ORDER BY {latest_comm_order_expr} ASC, c.name, c.second_name")
            elif sort_by == 'communication_count':
                query_parts.append(f"ORDER BY communication_count {order_direction}, c.name, c.second_name")
            else:
                query_parts.append(f"ORDER BY {latest_comm_order_expr} DESC, c.name, c.second_name")
        else:
            query_parts.append(f"ORDER BY {latest_comm_order_expr} DESC, c.name, c.second_name")

        query = " ".join(query_parts)

        # Debug: Print the query and parameters
        print(f"Query: {query}")
        print(f"Parameters: {params}")
        print(f"My communications only: {my_communications_only}")
        print(f"Call list only: {call_list_only}")
        print(f"Salesperson ID: {salesperson_id}")

        contacts = db_execute(query, params, fetch="all")

        print(f"Raw contacts from database: {len(contacts)}")

        # Convert to list of dictionaries and format dates
        from datetime import datetime, date
        result = []

        for contact in contacts:
            contact_dict = dict(contact)

            # Format the latest communication date and calculate days since last contact
            if contact_dict['latest_communication_date']:
                try:
                    raw_date = contact_dict['latest_communication_date']
                    parsed_date = None
                    date_str = None

                    if isinstance(raw_date, datetime):
                        parsed_date = raw_date
                    elif isinstance(raw_date, date):
                        parsed_date = datetime.combine(raw_date, datetime.min.time())
                    elif isinstance(raw_date, bytes):
                        date_str = raw_date.decode('utf-8', errors='ignore')
                    else:
                        date_str = str(raw_date)

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
                        contact_dict['latest_communication_date_formatted'] = parsed_date.strftime('%b %d, %Y')
                        days_since = (datetime.now() - parsed_date).days
                        contact_dict['days_since_contact'] = days_since
                    elif date_str:
                        contact_dict['latest_communication_date_formatted'] = date_str
                        contact_dict['days_since_contact'] = None
                    else:
                        contact_dict['latest_communication_date_formatted'] = "Unknown date"
                        contact_dict['days_since_contact'] = None
                except Exception as e:
                    print(f"Date parsing error for contact {contact_dict.get('name', 'Unknown')}: {e}")
                    contact_dict['latest_communication_date_formatted'] = "Unknown date"
                    contact_dict['days_since_contact'] = None
            else:
                contact_dict['latest_communication_date_formatted'] = "No communication"
                contact_dict['days_since_contact'] = None

            result.append(contact_dict)

        # Handle days_since_contact sorting in Python if needed
        if sort_by == 'days_since_contact' and sort_order:
            def sort_key(contact):
                days = contact.get('days_since_contact')
                if days is None:
                    return 999999 if sort_order.lower() == 'asc' else -1
                return days

            result.sort(key=sort_key, reverse=(sort_order.lower() == 'desc'))

        print(f"Returning {len(result)} contacts")
        return result

    except Exception as e:
        print(f"Error in get_salesperson_contacts: {str(e)}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        if db:
            db.close()

def get_contact_statuses():
    """Get all available contact statuses"""
    return get_all_contact_statuses()


def delete_contact(contact_id):
    """Soft delete a contact by setting status to inactive"""
    # Get the "inactive" status ID from contact_statuses table
    inactive_status = None
    db = get_db_connection()
    try:
        # Find an inactive status (look for common inactive status names)
        inactive_status_row = db.execute('''
            SELECT id FROM contact_statuses 
            WHERE LOWER(name) IN ('inactive', 'disabled', 'deleted') 
            AND is_active = TRUE 
            ORDER BY sort_order 
            LIMIT 1
        ''').fetchone()

        if inactive_status_row:
            inactive_status = inactive_status_row[0]
        else:
            # If no inactive status found, use status ID 2 as fallback
            inactive_status = 2

        db.execute("UPDATE contacts SET status_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (inactive_status, contact_id))
        db.commit()
    finally:
        db.close()

def add_contact_ajax(customer_id, name, second_name, email, job_title=None, phone=None, status_id=None):
    """
    Add a contact via AJAX with improved error handling
    Returns contact_id if successful
    """
    # If no status_id provided, get the default "active" status
    if status_id is None:
        statuses = get_all_contact_statuses()
        # Look for an "active" status or use the first one
        status_id = 1  # fallback
        for status in statuses:
            if status['name'].lower() in ['active', 'enabled']:
                status_id = status['id']
                break
        if not statuses:
            # If no statuses exist, use 1
            status_id = 1
        elif status_id == 1 and statuses:
            # Use the first available status
            status_id = statuses[0]['id']

    db = get_db_connection()
    try:
        # Begin transaction
        db.execute('BEGIN TRANSACTION')

        # Build insert query with RETURNING for Postgres
        insert_query = 'INSERT INTO contacts (customer_id, name, second_name, email, job_title, phone, status_id) VALUES (?, ?, ?, ?, ?, ?, ?)'
        params = (customer_id, name, second_name, email, job_title, phone, status_id)

        if _using_postgres():
            insert_query += ' RETURNING id'

        cursor = db.execute(insert_query, params)
        if _using_postgres():
            row = cursor.fetchone()
            contact_id = row['id'] if row else None
        else:
            contact_id = cursor.lastrowid

        # Extract domain from email and add to customer_domains if it doesn't exist
        if email and '@' in email and customer_id:
            domain = email.split('@')[-1].lower()

            # Check if domain already exists for this customer
            existing_domain = db.execute(
                'SELECT id FROM customer_domains WHERE customer_id = ? AND domain = ?',
                (customer_id, domain)
            ).fetchone()

            # If domain doesn't exist, insert it
            if not existing_domain:
                db.execute(
                    'INSERT INTO customer_domains (customer_id, domain) VALUES (?, ?)',
                    (customer_id, domain)
                )

        db.commit()
        return contact_id

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

def get_customer_tags(customer_id):
    db = get_db_connection()
    tags = db.execute('''
        WITH RECURSIVE customer_tag_hierarchy AS (
            SELECT t.id, t.tag as name, t.parent_tag_id
            FROM industry_tags t
            JOIN customer_industries ci ON t.id = ci.industry_id
            WHERE ci.customer_id = ?

            UNION

            SELECT t.id, t.tag as name, t.parent_tag_id
            FROM industry_tags t
            JOIN customer_tag_hierarchy cth ON t.id = cth.parent_tag_id
        )
        SELECT DISTINCT id, name
        FROM customer_tag_hierarchy;
    ''', [customer_id]).fetchall()
    db.close()
    return tags


def get_templates_by_tags(customer_tags):
    """Get email templates that match the customer's industry tags"""
    if not customer_tags:
        return []

    db = get_db_connection()

    # Convert tags to list of IDs
    tag_ids = [tag['id'] for tag in customer_tags]
    placeholders = ','.join('?' * len(tag_ids))

    # Updated SQL with correct column name (industry_tag_id instead of tag_id)
    templates = db.execute(f'''
        WITH template_matches AS (
            SELECT 
                et.id,
                et.name,
                et.subject,
                et.description,
                COUNT(tit.industry_tag_id) as matching_tags
            FROM email_templates et
            JOIN template_industry_tags tit ON et.id = tit.template_id
            WHERE tit.industry_tag_id IN ({placeholders})
            GROUP BY et.id
        )
        SELECT 
            t.id,
            t.name,
            t.subject,
            t.description,
            t.matching_tags
        FROM template_matches t
        ORDER BY t.matching_tags DESC, t.name ASC
    ''', tag_ids).fetchall()

    # Fetch tags separately (cross-DB; avoids GROUP_CONCAT)
    tags_by_template: Dict[int, List[str]] = {}
    tag_rows = db.execute(f'''
        SELECT tit.template_id, it.tag
        FROM template_industry_tags tit
        JOIN industry_tags it ON tit.industry_tag_id = it.id
        WHERE tit.industry_tag_id IN ({placeholders})
        ORDER BY tit.template_id, it.tag
    ''', tag_ids).fetchall()

    for r in tag_rows:
        tags_by_template.setdefault(int(r['template_id']), []).append(r['tag'])

    # Convert to list of dicts with all necessary information
    result = []
    for template in templates:
        tags = tags_by_template.get(int(template['id']), [])
        # de-dupe
        seen = set()
        uniq = []
        for t in tags:
            if t in seen:
                continue
            seen.add(t)
            uniq.append(t)
        result.append({
            'id': template['id'],
            'name': template['name'],
            'subject': template['subject'],
            'description': template['description'],
            'matching_tag_count': template['matching_tags'],
            'tags': uniq
        })

    db.close()
    return result

# Helper function to get paginated RFQs with customer_ref
def get_rfqs_by_customer_id(customer_id, page, per_page):
    db = get_db_connection()
    offset = (page - 1) * per_page
    query = '''
        SELECT id, status, entered_date, customer_ref
        FROM rfqs
        WHERE customer_id = ?
        ORDER BY entered_date DESC
        LIMIT ? OFFSET ?
    '''
    rfqs = db.execute(query, (customer_id, per_page, offset)).fetchall()
    db.close()
    return [dict(rfq) for rfq in rfqs]

# Helper function to get paginated Sales Orders
def get_sales_orders_by_customer_id(customer_id, page, per_page):
    db = get_db_connection()
    offset = (page - 1) * per_page
    query = '''
        SELECT so.id, so.date_entered, so.created_at, c.name AS customer_name, ss.status_name
        FROM sales_orders so
        LEFT JOIN customers c ON so.customer_id = c.id
        LEFT JOIN sales_statuses ss ON so.sales_status_id = ss.id
        WHERE so.customer_id = ?
        ORDER BY so.date_entered DESC, so.created_at DESC
        LIMIT ? OFFSET ?
    '''
    sales_orders = db.execute(query, (customer_id, per_page, offset)).fetchall()
    db.close()
    return [dict(order) for order in sales_orders]


def get_customer_apollo_id(customer_id):
    """Get Apollo ID for a customer if it exists"""
    try:
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute('''
            SELECT apollo_id 
            FROM customers 
            WHERE id = ?
        ''', (customer_id,))

        result = cursor.fetchone()
        db.close()

        return result['apollo_id'] if result else None

    except Exception as e:
        print(f"Error getting customer Apollo ID: {str(e)}")
        if 'db' in locals():
            db.close()
        return None

def filter_tags_by_search(tags, search_term):
    """Recursively filter tags by search term"""
    filtered_tags = []

    for tag in tags:
        # Check if the tag name matches the search term
        if search_term in tag['name'].lower():
            # If it matches, include the tag and all its children
            filtered_tags.append(tag)
        else:
            # Otherwise, check if any of the children match
            filtered_children = filter_tags_by_search(tag['children'], search_term)
            if filtered_children:
                # Include the tag with only the matching children
                filtered_tags.append({
                    **tag,
                    'children': filtered_children
                })

    return filtered_tags

def get_contact_by_email(email):
    """Check if contact exists and return it"""
    db = get_db_connection()
    contact = db.execute(
        'SELECT c.*, cu.name as customer_name '
        'FROM contacts c '
        'LEFT JOIN customers cu ON c.customer_id = cu.id '
        'WHERE c.email = ?',
        (email,)
    ).fetchone()
    db.close()
    return contact




def get_supplier_contact_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Look up a supplier contact by email address."""
    with get_db() as db:
        cursor = db.cursor()
        cursor.execute("""
            SELECT sc.*, s.name as supplier_name 
            FROM supplier_contacts sc
            LEFT JOIN suppliers s ON s.id = sc.supplier_id
            WHERE sc.email_address = ?
        """, (email,))
        result = cursor.fetchone()
        return dict(result) if result else None

def get_supplier_contacts_by_domain(domain: str) -> List[Dict[str, Any]]:
    """Get all supplier contacts from a specific email domain."""
    with get_db() as db:
        cursor = db.cursor()
        cursor.execute("""
            SELECT sc.*, s.name as supplier_name
            FROM supplier_contacts sc
            LEFT JOIN suppliers s ON s.id = sc.supplier_id
            WHERE sc.email_address LIKE ?
        """, (f"%@{domain}",))
        return [dict(row) for row in cursor.fetchall()]

def insert_new_supplier_contact(
    first_name: str,
    second_name: str,
    email_address: str,
    customer_id: int
) -> int:
    """Insert a new supplier contact and return its ID."""
    with get_db() as db:
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO supplier_contacts 
            (first_name, second_name, email_address, customer_id)
            VALUES (?, ?, ?, ?)
        """, (first_name, second_name, email_address, customer_id))
        db.commit()
        return cursor.lastrowid

def get_all_supplier_contacts() -> List[Dict[str, Any]]:
    """Get all supplier contacts with their associated supplier names."""
    with get_db() as db:
        cursor = db.cursor()
        cursor.execute("""
            SELECT sc.*, s.name as supplier_name
            FROM supplier_contacts sc
            LEFT JOIN suppliers s ON s.id = sc.supplier_id
        """)
        return [dict(row) for row in cursor.fetchall()]

def insert_stage_update(stage_id, salesperson_id, comment):
    db = get_db_connection()
    db.execute('''
        INSERT INTO stage_updates 
        (stage_id, salesperson_id, comment, date_created) 
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (stage_id, salesperson_id, comment))
    db.commit()
    db.close()

def get_stage_updates(stage_id):
    db = get_db_connection()
    updates = db.execute('''
        SELECT su.id, su.comment, su.date_created,
               s.name AS salesperson_name
        FROM stage_updates su
        LEFT JOIN salespeople s ON su.salesperson_id = s.id
        WHERE su.stage_id = ?
        ORDER BY su.date_created DESC
    ''', (stage_id,)).fetchall()
    db.close()
    return [dict_from_row(update) for update in updates]

def get_all_projects():
    connection = get_db_connection()
    try:
        query = """
            SELECT id, name
            FROM projects
            ORDER BY name
        """
        result = connection.execute(query).fetchall()
        return [{"id": row["id"], "name": row["name"]} for row in result]
    finally:
        connection.close()


class User(UserMixin):
    def __init__(self, id, username, password_hash=None, user_type='normal'):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.user_type = user_type

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_salesperson_id(self):
        db = get_db_connection()
        result = db.execute("""
            SELECT legacy_salesperson_id 
            FROM salesperson_user_link 
            WHERE user_id = ?
        """, (self.id,)).fetchone()
        db.close()
        return result['legacy_salesperson_id'] if result else None

    @staticmethod
    def get(user_id):
        db = get_db_connection()
        user_data = db.execute('SELECT * FROM users WHERE id = ?',
                               (user_id,)).fetchone()
        db.close()

        if user_data:
            user = User(
                id=user_data['id'],
                username=user_data['username'],
                password_hash=user_data['password_hash'],
                user_type=user_data['user_type']  # Remove the conditional here
            )
            print(f"DEBUG - get() - Created user type: {user.user_type}")
            return user
        return None


def create_user_tables():
    """Create the necessary tables for user authentication"""
    db = get_db_connection()

    # Create users table
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create link table between users and existing salespeople
    db.execute('''
        CREATE TABLE IF NOT EXISTS salesperson_user_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            legacy_salesperson_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (legacy_salesperson_id) REFERENCES salespeople (id)
        )
    ''')

    db.commit()
    db.close()


def get_user_by_username(username):
    db = get_db_connection()
    user_data = db.execute('SELECT * FROM users WHERE username = ?',
                           (username,)).fetchone()
    db.close()

    if user_data:
        user = User(
            id=user_data['id'],
            username=user_data['username'],
            password_hash=user_data['password_hash'],
            user_type=user_data['user_type']  # Remove the conditional here
        )
        return user
    return None


def create_user(username, password, salesperson_id=None):
    """Create a new user, optionally linking to a salesperson"""
    db = get_db_connection()

    user = User(id=None, username=username)
    user.set_password(password)

    try:
        cursor = db.execute(
            'INSERT INTO users (username, password_hash) VALUES (?, ?)',
            (username, user.password_hash)
        )
        user_id = cursor.lastrowid

        if salesperson_id:
            db.execute(
                'INSERT INTO salesperson_user_link (user_id, legacy_salesperson_id) VALUES (?, ?)',
                (user_id, salesperson_id)
            )

        db.commit()
        return user_id
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


class Permission:
    READ = 1
    WRITE = 2
    ADMIN = 4
    VIEW_CUSTOMERS = 8    # New permission for viewing others' customers
    EDIT_CUSTOMERS = 16   # New permission for editing others' customers


class UserType:
    NORMAL = 'normal'
    ADMIN = 'admin'
    VIEW_ONLY = 'view_only'

    PERMISSIONS = {
        NORMAL: Permission.READ | Permission.WRITE | Permission.VIEW_CUSTOMERS,  # Can view but not edit others' customers
        ADMIN: Permission.READ | Permission.WRITE | Permission.ADMIN | Permission.VIEW_CUSTOMERS | Permission.EDIT_CUSTOMERS,  # Full access
        VIEW_ONLY: Permission.READ | Permission.VIEW_CUSTOMERS  # Can only view
    }


class User(UserMixin):
    def __init__(self, id, username, password_hash=None, user_type='normal', permissions=None):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.user_type = user_type
        self.permissions = permissions if permissions is not None else UserType.PERMISSIONS.get(user_type, 0)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_salesperson_id(self):
        db = get_db_connection()
        result = db.execute("""
            SELECT legacy_salesperson_id 
            FROM salesperson_user_link 
            WHERE user_id = ?
        """, (self.id,)).fetchone()
        db.close()
        return result['legacy_salesperson_id'] if result else None

    def can(self, permission):
        return self.permissions & permission == permission

    def is_administrator(self):
        return self.can(Permission.ADMIN)

    @staticmethod
    def get(user_id):
        db = get_db_connection()
        user_data = db_execute('''
            SELECT users.*, user_permissions.permissions
            FROM users
            LEFT JOIN user_permissions ON users.id = user_permissions.user_id
            WHERE users.id = ?
        ''', (user_id,), fetch="one")
        db.close()

        if user_data:
            user = User(
                id=user_data['id'],
                username=user_data['username'],
                password_hash=user_data['password_hash'],
                user_type=user_data['user_type'],
                permissions=user_data['permissions']
            )
            return user
        return None


def permission_required(permission):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.can(permission):
                abort(403)
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def admin_required(f):
    return permission_required(Permission.ADMIN)(f)


# Database setup functions
def create_user_tables():
    db = get_db_connection()

    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            user_type TEXT NOT NULL DEFAULT 'normal',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS user_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            permissions INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id),
            UNIQUE(user_id)
        )
    ''')

    db.commit()
    db.close()


def set_user_permissions(user_id, permissions):
    db = get_db_connection()
    try:
        db.execute('''
            INSERT INTO user_permissions (user_id, permissions) 
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET permissions = ?
        ''', (user_id, permissions, permissions))
        db.commit()
    finally:
        db.close()


def get_all_company_types():
    """Get all company types"""
    db = get_db_connection()
    try:
        # Using 'name' as the alias for 'type' to match the tag structure
        types = db_execute('''
            SELECT id, type as name 
            FROM company_types
            ORDER BY type
        ''', fetch="all")
        return types
    finally:
        db.close()

def get_company_types_by_customer_id(customer_id):
    """Get all company types for a specific customer"""
    conn = get_db_connection()
    types = conn.execute('''
        SELECT company_types.type 
        FROM company_types
        JOIN customer_company_types ON company_types.id = customer_company_types.company_type_id
        WHERE customer_company_types.customer_id = ?
    ''', (customer_id,)).fetchall()
    conn.close()
    type_list = [type_item['type'] for type_item in types]
    print(f"Retrieved company types for customer {customer_id}: {type_list}")  # Debug print
    return type_list


def insert_customer_company_type(customer_id, company_type_id):
    """Associate a company type with a customer"""
    db = get_db_connection()
    try:
        db.execute(
            'INSERT INTO customer_company_types (customer_id, company_type_id) VALUES (?, ?)',
            (customer_id, company_type_id)
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def remove_customer_company_type(customer_id, company_type_id):
    """Remove a company type association from a customer"""
    db = get_db_connection()
    try:
        db.execute(
            'DELETE FROM customer_company_types WHERE customer_id = ? AND company_type_id = ?',
            (customer_id, company_type_id)
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def get_company_type_by_id(company_type_id):
    """Get a single company type by its ID"""
    conn = get_db_connection()
    type_data = conn.execute('''
        SELECT id, type, description, parent_type_id
        FROM company_types
        WHERE id = ?
    ''', (company_type_id,)).fetchone()
    conn.close()
    return type_data


def create_company_type(type_name, description=None, parent_type_id=None):
    """Create a new company type"""
    db = get_db_connection()
    try:
        cursor = db.execute(
            '''INSERT INTO company_types (type, description, parent_type_id)
               VALUES (?, ?, ?)''',
            (type_name, description, parent_type_id)
        )
        new_id = cursor.lastrowid
        db.commit()
        return new_id
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def update_company_type(type_id, type_name=None, description=None, parent_type_id=None):
    """Update an existing company type"""
    db = get_db_connection()
    try:
        updates = []
        params = []
        if type_name is not None:
            updates.append("type = ?")
            params.append(type_name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if parent_type_id is not None:
            updates.append("parent_type_id = ?")
            params.append(parent_type_id)

        if updates:
            query = f'''UPDATE company_types 
                       SET {", ".join(updates)}
                       WHERE id = ?'''
            params.append(type_id)
            db.execute(query, params)
            db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def update_customer_enrichment(customer_id, enrichment_data):
    """Update customer with enriched data"""
    db = get_db_connection()
    try:
        # Handle industry tags
        if 'suggested_tag_ids' in enrichment_data:
            db.execute('DELETE FROM customer_industry_tags WHERE customer_id = ?',
                       (customer_id,))

            for tag_id in enrichment_data['suggested_tag_ids']:
                db.execute('''
                    INSERT INTO customer_industry_tags (customer_id, tag_id)
                    VALUES (?, ?)
                ''', (customer_id, tag_id))

        # Handle company types
        if 'suggested_company_type_ids' in enrichment_data:
            db.execute('DELETE FROM customer_company_types WHERE customer_id = ?',
                       (customer_id,))

            for type_id in enrichment_data['suggested_company_type_ids']:
                db.execute('''
                    INSERT INTO customer_company_types (customer_id, company_type_id)
                    VALUES (?, ?)
                ''', (customer_id, type_id))

        # Update other fields
        if 'estimated_revenue' in enrichment_data:
            db.execute('''
                UPDATE customers 
                SET estimated_revenue = ?
                WHERE id = ?
            ''', (enrichment_data['estimated_revenue'], customer_id))

        if 'country_code' in enrichment_data:
            db.execute('''
                UPDATE customers 
                SET country = ?
                WHERE id = ?
            ''', (enrichment_data['country_code'], customer_id))

        if 'fleet_size' in enrichment_data:
            db.execute('''
                UPDATE customers 
                SET fleet_size = ?
                WHERE id = ?
            ''', (enrichment_data['fleet_size'], customer_id))

        db.commit()
    except Exception as e:
        db.rollback()
        raise
    finally:
        db.close()


def start_enrichment_process(batch_size=20):
    """
    Main function to start/control the enrichment process
    """
    db = get_db_connection()
    try:
        # Get pending customers
        customers = db.execute('''
            SELECT c.id, c.name, c.description, c.website 
            FROM customers c
            LEFT JOIN customer_enrichment_status ces ON c.id = ces.customer_id
            WHERE ces.status IS NULL 
               OR ces.status = 'pending'
            LIMIT ?
        ''', (batch_size,)).fetchall()

        # Get all existing tags once
        tags = db_execute('''
            SELECT id, tag as name, description 
            FROM industry_tags
        ''', fetch="all")

        # Get all company types once
        company_types = db_execute('''
            SELECT id, type as name, description 
            FROM company_types
        ''', fetch="all")

        for customer in customers:
            try:
                process_customer(customer, tags, company_types)

                # Update status
                db.execute('''
                    INSERT INTO customer_enrichment_status (customer_id, status, last_attempt, attempts)
                    VALUES (?, 'completed', ?, 1)
                    ON CONFLICT(customer_id) 
                    DO UPDATE SET status = 'completed',
                                 last_attempt = ?,
                                 attempts = attempts + 1
                ''', (customer['id'], datetime.now(), datetime.now()))
                db.commit()

            except Exception as e:
                logging.error(f"Error processing customer {customer['id']}: {str(e)}")
                db.execute('''
                    INSERT INTO customer_enrichment_status (customer_id, status, last_attempt, error_message, attempts)
                    VALUES (?, 'failed', ?, ?, 1)
                    ON CONFLICT(customer_id) 
                    DO UPDATE SET status = 'failed',
                                 last_attempt = ?,
                                 error_message = ?,
                                 attempts = attempts + 1
                ''', (customer['id'], datetime.now(), str(e), datetime.now(), str(e)))
                db.commit()
                continue

    finally:
        db.close()


def process_customer(customer, existing_tags, company_types):
    """Process a single customer"""
    try:
        # Call OpenAI API with the customer data
        enrichment_data = enrich_customer_data(customer, existing_tags, company_types)

        db = get_db_connection()
        try:
            # Update core customer data
            db.execute('''
                UPDATE customers 
                SET estimated_revenue = ?,
                    country = ?,
                    updated_at = ?
                WHERE id = ?
            ''', (
                enrichment_data['estimated_revenue'],
                enrichment_data['country_code'],
                datetime.now(),
                customer['id']
            ))

            # Clear existing company types and add new ones
            db.execute('DELETE FROM customer_company_types WHERE customer_id = ?',
                       (customer['id'],))
            for type_id in enrichment_data['suggested_company_type_ids']:
                db.execute('''
                    INSERT INTO customer_company_types (customer_id, company_type_id)
                    VALUES (?, ?)
                ''', (customer['id'], type_id))

            # Store any new tag suggestions
            for tag in enrichment_data['suggested_new_tags']:
                db.execute('''
                    INSERT INTO ai_tag_suggestions 
                    (customer_id, suggested_tag, frequency)
                    VALUES (?, ?, 1)
                    ON CONFLICT (customer_id, suggested_tag)
                    DO UPDATE SET frequency = frequency + 1
                ''', (customer['id'], tag))

            db.commit()

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error in process_customer for {customer['id']}: {str(e)}")
        raise

def extract_domain(email):
    """Extract domain from email address"""
    if not email or '@' not in email:
        return None
    return email.split('@')[1].lower()

def add_customer_domain(customer_id, domain):
    """Add a domain for a customer"""
    db = get_db_connection()
    try:
        db.execute(
            'INSERT INTO customer_domains (customer_id, domain) VALUES (?, ?)',
            (customer_id, domain.lower())
        )
        db.commit()
        return True
    except Exception:
        return False
    finally:
        db.close()

def add_supplier_domain(supplier_id, domain):
    """Add a domain for a supplier"""
    db = get_db_connection()
    try:
        db.execute(
            'INSERT INTO supplier_domains (supplier_id, domain) VALUES (?, ?)',
            (supplier_id, domain.lower())
        )
        db.commit()
        return True
    except Exception:
        return False
    finally:
        db.close()

def get_customer_by_domain(domain):
    """Look up customer by email domain"""
    db = get_db_connection()
    customer = db.execute(
        'SELECT c.id, c.name, c.primary_contact_id, c.salesperson_id, c.country, '
        'c.type, c.website, c.payment_terms, c.incoterms, c.watch, c.logo_url, '
        'c.annual_revenue, c.rating, c.notes FROM customers c '
        'JOIN customer_domains cd ON c.id = cd.customer_id '
        'WHERE cd.domain = ?',
        (domain.lower(),)
    ).fetchone()
    db.close()
    return customer

def get_supplier_by_domain(domain):
    """Look up supplier by email domain"""
    db = get_db_connection()
    supplier = db.execute(
        'SELECT s.* FROM suppliers s '
        'JOIN supplier_domains sd ON s.id = sd.supplier_id '
        'WHERE sd.domain = ?',
        (domain.lower(),)
    ).fetchone()
    db.close()
    return supplier

def get_customer_domains(customer_id):
    """Get all domains for a customer"""
    db = get_db_connection()
    domains = db.execute(
        'SELECT domain FROM customer_domains WHERE customer_id = ? ORDER BY domain',
        (customer_id,)
    ).fetchall()
    db.close()
    return [domain[0] for domain in domains]

def get_supplier_domains(supplier_id):
    """Get all domains for a supplier"""
    db = get_db_connection()
    domains = db.execute(
        'SELECT domain FROM supplier_domains WHERE supplier_id = ? ORDER BY domain',
        (supplier_id,)
    ).fetchall()
    db.close()
    return [domain[0] for domain in domains]

def remove_customer_domain(customer_id, domain):
    """Remove a domain from a customer"""
    db = get_db_connection()
    db.execute(
        'DELETE FROM customer_domains WHERE customer_id = ? AND domain = ?',
        (customer_id, domain.lower())
    )
    db.commit()
    db.close()

def remove_supplier_domain(supplier_id, domain):
    """Remove a domain from a supplier"""
    db = get_db_connection()
    db.execute(
        'DELETE FROM supplier_domains WHERE supplier_id = ? AND domain = ?',
        (supplier_id, domain.lower())
    )
    db.commit()
    db.close()

def get_customer_by_email(email):
    """Look up customer by email address"""
    domain = extract_domain(email)
    if not domain:
        return None
    return get_customer_by_domain(domain)

def get_supplier_by_email(email):
    """Look up supplier by email address"""
    domain = extract_domain(email)
    if not domain:
        return None
    return get_supplier_by_domain(domain)


# Part alternative helpers (Postgres-ready, still works on SQLite)


def create_part_alternative(
    rfq_line_id: int,
    primary_base_part_number: str,
    alternative_base_part_number: str,
) -> bool:
    """Create an RFQ line part alternative.

    Note: uses shared db helpers so '?' placeholders translate for Postgres.
    """
    try:
        db_execute(
            """
            INSERT INTO rfq_line_part_alternatives
            (rfq_line_id, primary_base_part_number, alternative_base_part_number)
            VALUES (?, ?, ?)
            """,
            (rfq_line_id, primary_base_part_number, alternative_base_part_number),
            commit=True,
        )
        return True
    except Exception as e:
        logging.error("Error creating part alternative: %s", e)
        return False


def get_part_alternatives(base_part_number: str) -> List[Dict]:
    """Return part alternatives for a given base part number."""
    try:
        rows = db_execute(
            """
            SELECT rfq_line_id, search_part, related_part, relationship
            FROM part_relationships
            WHERE search_part = ?
            """,
            (base_part_number,),
            fetch="all",
        ) or []
        return [dict(row) for row in rows]
    except Exception as e:
        logging.error("Error getting part alternatives: %s", e)
        return []


# ---- Invoice Functions ----


def create_invoice(
    sales_order_id,
    customer_id,
    billing_address_id,
    invoice_date,
    due_date,
    currency_id,
    total_amount,
    status,
):
    """Creates a new invoice entry in the database.

    Uses db helpers so it works on SQLite today and Postgres later.
    """
    row = db_execute(
        """
        INSERT INTO invoices (
            sales_order_id,
            customer_id,
            billing_address_id,
            invoice_date,
            due_date,
            currency_id,
            total_amount,
            status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        RETURNING id
        """,
        (
            sales_order_id,
            customer_id,
            billing_address_id,
            invoice_date,
            due_date,
            currency_id,
            total_amount,
            status,
        ),
        fetch="one",
        commit=True,
    )

    if not row:
        raise RuntimeError("Failed to create invoice")

    return row.get("id", list(row.values())[0])


def get_invoice_by_id(invoice_id):
    """Retrieves an invoice by ID."""
    row = db_execute(
        "SELECT * FROM invoices WHERE id = ?",
        (invoice_id,),
        fetch="one",
    )
    return dict(row) if row else None


def get_all_invoices():
    """Retrieves all invoices with customer names."""
    rows = db_execute(
        """
        SELECT i.*, c.name AS customer_name
        FROM invoices i
        LEFT JOIN customers c ON i.customer_id = c.id
        """,
        fetch="all",
    ) or []
    return [dict(r) for r in rows]


def update_invoice_status(invoice_id, new_status):
    """Updates the status of an invoice."""
    db_execute(
        """
        UPDATE invoices
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_status, invoice_id),
        commit=True,
    )


def delete_invoice(invoice_id):
    """Deletes an invoice from the database."""
    db_execute(
        "DELETE FROM invoices WHERE id = ?",
        (invoice_id,),
        commit=True,
    )


# Sales-order line status helpers
#
# IMPORTANT:
# - Keep placeholders as '?' so db_execute can translate to '%s' for Postgres.
# - Keep the code SQLite-compatible today.


def update_sales_order_line_status(line_id, status_id, ship_date=None):
    """Update the status of a single sales order line."""
    if ship_date:
        db_execute(
            """
            UPDATE sales_order_lines
            SET sales_status_id = ?, ship_date = ?
            WHERE id = ?
            """,
            (status_id, ship_date, line_id),
            commit=True,
        )
    else:
        db_execute(
            """
            UPDATE sales_order_lines
            SET sales_status_id = ?
            WHERE id = ?
            """,
            (status_id, line_id),
            commit=True,
        )
    return True


def update_multiple_sales_order_lines_status(line_ids, status_id, ship_date=None):
    """Update the status of multiple sales order lines."""
    if not line_ids:
        return False

    with db_cursor(commit=True) as cur:
        for line_id in line_ids:
            if ship_date:
                _execute_with_cursor(
                    cur,
                    """
                    UPDATE sales_order_lines
                    SET sales_status_id = ?, ship_date = ?
                    WHERE id = ?
                    """,
                    (status_id, ship_date, line_id),
                )
            else:
                _execute_with_cursor(
                    cur,
                    """
                    UPDATE sales_order_lines
                    SET sales_status_id = ?
                    WHERE id = ?
                    """,
                    (status_id, line_id),
                )

    return True


def update_all_sales_order_lines_status(sales_order_id, status_id, ship_date=None):
    """Update the status of all lines for a sales order. Returns number of updated rows."""
    with db_cursor(commit=True) as cur:
        if ship_date:
            _execute_with_cursor(
                cur,
                """
                UPDATE sales_order_lines
                SET sales_status_id = ?, ship_date = ?
                WHERE sales_order_id = ?
                """,
                (status_id, ship_date, sales_order_id),
            )
        else:
            _execute_with_cursor(
                cur,
                """
                UPDATE sales_order_lines
                SET sales_status_id = ?
                WHERE sales_order_id = ?
                """,
                (status_id, sales_order_id),
            )
        return getattr(cur, "rowcount", 0)


def validate_line_ids_for_sales_order(line_ids, sales_order_id):
    """Validate that all line IDs belong to the specified sales order."""
    if not line_ids:
        return False

    placeholders = ",".join(["?"] * len(line_ids))
    row = db_execute(
        f"""
        SELECT COUNT(*) as cnt
        FROM sales_order_lines
        WHERE sales_order_id = ?
          AND id IN ({placeholders})
        """,
        [sales_order_id] + list(line_ids),
        fetch="one",
    )
    return bool(row and int(row.get("cnt", list(row.values())[0])) == len(line_ids))


def count_non_shipped_lines(sales_order_id):
    """Count how many lines in the sales order are not shipped."""
    row = db_execute(
        """
        SELECT COUNT(*) as cnt
        FROM sales_order_lines
        WHERE sales_order_id = ? AND sales_status_id != 3
        """,
        (sales_order_id,),
        fetch="one",
    )
    return int(row.get("cnt", list(row.values())[0])) if row else 0


def is_line_in_sales_order(line_id, sales_order_id):
    """Check if a sales order line belongs to a specific sales order."""
    row = db_execute(
        """
        SELECT COUNT(*) as cnt
        FROM sales_order_lines
        WHERE id = ? AND sales_order_id = ?
        """,
        (line_id, sales_order_id),
        fetch="one",
    )
    return bool(row and int(row.get("cnt", list(row.values())[0])) > 0)


from datetime import datetime


def format_timestamp():
    """
    Get current timestamp in formatted string

    Returns:
        str: Formatted timestamp
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



def set_conversion_mode(mode):
    """
    Set the conversion mode

    Args:
        mode (str): 'live' or 'manual'
    """
    if mode not in ['live', 'manual']:
        raise ValueError("Mode must be 'live' or 'manual'")

    db_execute(
        """
        INSERT INTO app_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
        """,
        ('conversion_mode', mode),
        commit=True,
    )




def create_invoice_line(
    invoice_id,
    sales_order_line_id,
    base_part_number,
    quantity,
    unit_price,
    currency_id,
):
    """Create a new invoice line with the specified currency.

    Uses db helpers so it works on SQLite today and Postgres later.
    """
    line_total = quantity * unit_price

    row = db_execute(
        """
        INSERT INTO invoice_lines (
            invoice_id,
            sales_order_line_id,
            base_part_number,
            quantity,
            unit_price,
            line_total,
            currency_id,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        RETURNING id
        """,
        (
            invoice_id,
            sales_order_line_id,
            base_part_number,
            quantity,
            unit_price,
            line_total,
            currency_id,
        ),
        fetch="one",
        commit=True,
    )

    if not row:
        raise RuntimeError("Failed to create invoice line")

    return row.get("id", list(row.values())[0])



def convert_amount(amount, from_currency_id, to_currency_id):
    """Convert an amount from one currency to another.

    All conversions go through EUR as the base currency.
    Uses db helpers so it works on SQLite today and Postgres later.
    """
    if from_currency_id == to_currency_id:
        return amount

    from_row = db_execute(
        "SELECT exchange_rate_to_eur FROM currencies WHERE id = ?",
        (from_currency_id,),
        fetch="one",
    )
    to_row = db_execute(
        "SELECT exchange_rate_to_eur FROM currencies WHERE id = ?",
        (to_currency_id,),
        fetch="one",
    )

    if not from_row or not to_row:
        return amount

    from_rate = from_row.get("exchange_rate_to_eur")
    to_rate = to_row.get("exchange_rate_to_eur")

    if not from_rate or not to_rate:
        return amount

    eur_amount = amount / from_rate
    target_amount = eur_amount * to_rate
    return round(target_amount, 2)


def get_currency_by_id(currency_id):
    """Get currency details by ID."""
    row = db_execute(
        "SELECT * FROM currencies WHERE id = ?",
        (currency_id,),
        fetch="one",
    )
    return dict(row) if row else None

def get_orders_for_calendar(start_date, end_date, date_type='ship_date', customer_id=None):
    """
    Get sales orders for the calendar view within the specified date range.
    Only includes orders where the selected date_type has a non-NULL value.

    Args:
        start_date: Start date for the calendar view
        end_date: End date for the calendar view
        date_type: Type of date to filter on (ship_date, delivery_date, etc.)
        customer_id: Optional customer ID to filter orders

    Returns:
        List of order objects with required details for calendar display
    """
    db = get_db_connection()

    # Build the query with the correct date field and include shipped status
    # Add a condition to exclude NULL dates
    query = f"""
        SELECT 
            sol.id,
            sol.sales_order_id,
            sol.line_number,
            sol.base_part_number,
            sol.quantity,
            sol.shipped_quantity,
            sol.ship_date,
            sol.delivery_date,
            sol.promise_date,
            sol.requested_date,
            sol.shipped,  -- Include the shipped column
            so.sales_order_ref,
            c.name as customer_name
        FROM 
            sales_order_lines sol
        JOIN 
            sales_orders so ON sol.sales_order_id = so.id
        JOIN 
            customers c ON so.customer_id = c.id
        WHERE
            sol.{date_type} IS NOT NULL
            AND sol.{date_type} BETWEEN ? AND ?
    """

    # Add customer filter if provided
    params = [start_date, end_date]
    if customer_id:
        query += " AND so.customer_id = ?"
        params.append(customer_id)

    # Execute the query
    cursor = db.execute(query, params)

    # Convert the rows to a list of custom objects for easier template handling
    class OrderObject:
        pass

    orders = []
    for row in cursor.fetchall():
        order = OrderObject()

        # Set all the attributes from the row
        for key in row.keys():
            setattr(order, key, row[key])

        orders.append(order)

    db.close()
    return orders

def get_rfqs_for_project(project_id):
    """
    Retrieves all RFQs linked to a specific project.
    """
    try:
        db = get_db_connection()
        rfqs = db.execute('''
            SELECT r.* 
            FROM rfqs r
            JOIN project_rfqs pr ON r.id = pr.rfq_id
            WHERE pr.project_id = ?
            ORDER BY r.entered_date DESC
        ''', (project_id,)).fetchall()

        # Convert row objects to dictionaries to ensure JSON serialization works
        result = []
        for rfq in rfqs:
            rfq_dict = dict_from_row(rfq)
            # Ensure dates are properly formatted as strings
            if 'entered_date' in rfq_dict and rfq_dict['entered_date']:
                if not isinstance(rfq_dict['entered_date'], str):
                    rfq_dict['entered_date'] = rfq_dict['entered_date'].isoformat()
            result.append(rfq_dict)

        db.close()
        return result
    except Exception as e:
        print(f"Error getting RFQs for project: {e}")
        return []


def get_projects_for_rfq(rfq_id):
    """
    Retrieves all projects linked to a specific RFQ.
    """
    try:
        db = get_db_connection()
        projects = db.execute('''
            SELECT p.* 
            FROM projects p
            JOIN project_rfqs pr ON p.id = pr.project_id
            WHERE pr.rfq_id = ?
        ''', (rfq_id,)).fetchall()

        result = [dict_from_row(project) for project in projects]
        db.close()
        return result
    except Exception as e:
        print(f"Error getting projects for RFQ: {e}")
        return []


def remove_rfq_from_project(project_id, rfq_id):
    """
    Removes the link between an RFQ and a project.
    """
    try:
        db = get_db_connection()
        db.execute('DELETE FROM project_rfqs WHERE project_id = ? AND rfq_id = ?', (project_id, rfq_id))
        db.commit()
        db.close()
        return True
    except Exception as e:
        print(f"Error removing RFQ from project: {e}")
        return False


def recalculate_invoice_taxes(invoice_id, conn=None, cursor=None):
    """Recalculate tax amounts for an invoice.

    Postgres-friendly:
    - Prefer shared `db_execute` helper.
    - If an external caller passes a DB-API cursor/connection, we still support it.

    Args:
        invoice_id: Invoice ID
        conn/cursor: Optional existing connection+cursor (SQLite-style). If provided,
                     updates are executed on that cursor and caller controls commit.

    Returns:
        True
    """
    if cursor is not None:
        # Legacy path: operate on the passed cursor (keeps backwards compatibility)
        cursor.execute("SELECT total_amount FROM invoices WHERE id = ?", (invoice_id,))
        invoice = cursor.fetchone()
        if not invoice:
            raise ValueError(f"Invoice with ID {invoice_id} not found")

        invoice_total = invoice["total_amount"] if isinstance(invoice, dict) else invoice[0]

        cursor.execute(
            """
            SELECT it.id, it.tax_rate_id, tr.tax_percentage
            FROM invoice_taxes it
            JOIN tax_rates tr ON it.tax_rate_id = tr.id
            WHERE it.invoice_id = ?
            """,
            (invoice_id,),
        )
        taxes = cursor.fetchall() or []

        for tax in taxes:
            tax_id = tax["id"] if isinstance(tax, dict) else tax[0]
            tax_percentage = tax["tax_percentage"] if isinstance(tax, dict) else tax[2]
            new_tax_amount = round(invoice_total * (tax_percentage / 100), 2)
            cursor.execute(
                "UPDATE invoice_taxes SET tax_amount = ? WHERE id = ?",
                (new_tax_amount, tax_id),
            )
        return True

    # Preferred path: use db helpers (auto placeholder translation for Postgres)
    invoice = db_execute(
        "SELECT total_amount FROM invoices WHERE id = ?",
        (invoice_id,),
        fetch="one",
    )
    if not invoice:
        raise ValueError(f"Invoice with ID {invoice_id} not found")

    invoice_total = invoice.get("total_amount")

    taxes = db_execute(
        """
        SELECT it.id, it.tax_rate_id, tr.tax_percentage
        FROM invoice_taxes it
        JOIN tax_rates tr ON it.tax_rate_id = tr.id
        WHERE it.invoice_id = ?
        """,
        (invoice_id,),
        fetch="all",
    ) or []

    with db_cursor(commit=True) as cur:
        for tax in taxes:
            tax_id = tax.get("id") if isinstance(tax, dict) else tax[0]
            tax_percentage = tax.get("tax_percentage") if isinstance(tax, dict) else tax[2]
            new_tax_amount = round(invoice_total * (tax_percentage / 100), 2)
            _execute_with_cursor(
                cur,
                "UPDATE invoice_taxes SET tax_amount = ? WHERE id = ?",
                (new_tax_amount, tax_id),
            )

    return True


def calculate_invoice_total(conn, cursor, invoice_id):
    """
    Calculate the full invoice total including taxes and discounts.

    Args:
        conn: Database connection
        cursor: Database cursor
        invoice_id: ID of the invoice to calculate

    Returns:
        dict: Contains subtotal, tax_total, discount_total, and invoice_total
    """
    # Get invoice subtotal
    cursor.execute("SELECT total_amount FROM invoices WHERE id = ?", (invoice_id,))
    invoice = cursor.fetchone()

    if not invoice:
        return {
            'subtotal': 0,
            'tax_total': 0,
            'discount_total': 0,
            'invoice_total': 0
        }

    subtotal = invoice['total_amount']

    # Calculate tax total
    cursor.execute("""
        SELECT SUM(tax_amount) as total_taxes
        FROM invoice_taxes
        WHERE invoice_id = ?
    """, (invoice_id,))

    tax_result = cursor.fetchone()
    tax_total = tax_result['total_taxes'] if tax_result['total_taxes'] is not None else 0

    # Calculate discount total
    cursor.execute("""
        SELECT discount_type, discount_value
        FROM invoice_discounts
        WHERE invoice_id = ?
    """, (invoice_id,))

    discounts = cursor.fetchall()
    discount_total = 0

    for discount in discounts:
        if discount['discount_type'] == 'percentage':
            discount_amount = round(subtotal * (discount['discount_value'] / 100), 2)
        else:
            discount_amount = discount['discount_value']
        discount_total += discount_amount

    # Calculate final invoice total
    invoice_total = subtotal + tax_total - discount_total

    return {
        'subtotal': subtotal,
        'tax_total': tax_total,
        'discount_total': discount_total,
        'invoice_total': invoice_total
    }


from datetime import datetime, timedelta


def check_order_stock_availability(db, order):
    """
    Check if a sales order has sufficient stock or incoming POs to be fulfilled
    based on the remaining quantity (not the total order quantity).

    Args:
        db: Database connection
        order: Sales order object

    Returns:
        Dictionary with status and details about stock availability
    """
    result = {
        "at_risk": False,
        "status": "ok",
        "available_quantity": 0,
        "shortage": 0,
        "next_delivery_date": None,
        "next_delivery_quantity": 0,
        "details": ""
    }

    try:
        # Skip if no part number or quantity defined
        if not hasattr(order, 'base_part_number') or not hasattr(order, 'quantity'):
            result["status"] = "unknown"
            result["details"] = "Missing part number or quantity"
            return result

        # Calculate remaining quantity (ordered - received)
        remaining_quantity = order.quantity
        if hasattr(order, 'received_quantity') and order.received_quantity is not None:
            remaining_quantity = order.quantity - order.received_quantity

        # If fully received, no need to check stock
        if remaining_quantity <= 0:
            result["status"] = "fulfilled"
            result["details"] = "Order fully received"
            return result

        # Get current available stock
        stock_query = """
            SELECT SUM(available_quantity) as total_available
            FROM stock_movements
            WHERE base_part_number = ? AND movement_type = 'IN' AND available_quantity > 0
        """
        stock = db.execute(stock_query, (order.base_part_number,)).fetchone()
        current_stock = stock['total_available'] if stock and stock['total_available'] else 0
        result["available_quantity"] = current_stock

        # Calculate if there's a shortage based on REMAINING quantity, not total quantity
        shortage = remaining_quantity - current_stock
        result["shortage"] = max(0, shortage)

        # If shortage exists, check for incoming purchase orders
        if shortage > 0:
            # Find upcoming POs for this part number that will arrive before ship/delivery date
            order_date = getattr(order, 'ship_date', None) or getattr(order, 'delivery_date', None)

            if not order_date:
                result["at_risk"] = True
                result["status"] = "no_date"
                result["details"] = "Missing ship/delivery date"
                return result

            # Convert order_date to datetime if it's a string
            if isinstance(order_date, str):
                try:
                    order_date = datetime.strptime(order_date, '%Y-%m-%d')
                except ValueError:
                    result["at_risk"] = True
                    result["status"] = "invalid_date"
                    result["details"] = "Invalid date format"
                    return result

            # Convert order_date to string format for SQL query if it's a datetime
            order_date_str = order_date
            if isinstance(order_date, datetime):
                order_date_str = order_date.strftime('%Y-%m-%d')

            # Find incoming POs for this part, considering REMAINING quantities on POs
            po_query = """
                SELECT 
                    pol.purchase_order_id, 
                    pol.promised_date, 
                    pol.quantity,
                    COALESCE(pol.received_quantity, 0) as received_quantity,
                    (pol.quantity - COALESCE(pol.received_quantity, 0)) as remaining_po_quantity,
                    po.supplier_id, 
                    s.name as supplier_name
                FROM purchase_order_lines pol
                JOIN purchase_orders po ON pol.purchase_order_id = po.id
                JOIN suppliers s ON po.supplier_id = s.id  
                WHERE pol.base_part_number = ? 
                  AND pol.promised_date <= ?
                  AND pol.promised_date >= CURRENT_DATE
                  AND (pol.quantity - COALESCE(pol.received_quantity, 0)) > 0
                ORDER BY pol.promised_date ASC
            """

            upcoming_pos = db.execute(po_query, (order.base_part_number, order_date_str)).fetchall()

            # If no upcoming POs and we have a shortage, the order is at risk
            if not upcoming_pos:
                result["at_risk"] = True
                result["status"] = "no_po"
                result["details"] = f"Shortage of {shortage} units with no incoming POs before ship date"
                return result

            # Check if the upcoming POs will cover the shortage
            total_incoming = 0
            for po in upcoming_pos:
                # Use the remaining quantity on the PO, not the total quantity
                remaining_po_quantity = po['remaining_po_quantity']
                total_incoming += remaining_po_quantity

                # Remember the first incoming delivery
                if result["next_delivery_date"] is None:
                    # Format the date as a string if it's a datetime
                    if isinstance(po['promised_date'], datetime):
                        result["next_delivery_date"] = po['promised_date'].strftime('%d %b %Y')
                    else:
                        # Try to parse and format string dates
                        try:
                            date_obj = datetime.strptime(po['promised_date'], '%Y-%m-%d')
                            result["next_delivery_date"] = date_obj.strftime('%d %b %Y')
                        except (ValueError, TypeError):
                            # If parsing fails, use as is
                            result["next_delivery_date"] = po['promised_date']

                    result["next_delivery_quantity"] = remaining_po_quantity

            # If the total incoming quantity is still not enough, mark as at risk
            if total_incoming < shortage:
                result["at_risk"] = True
                result["status"] = "insufficient_po"
                remaining_shortage = shortage - total_incoming
                result["details"] = f"Shortage of {remaining_shortage} units after considering incoming POs"
                return result

            # Determine if the timing of POs is cutting it too close
            # For example, if delivery is due on the same day as the PO arrival
            if upcoming_pos:
                # Convert promised_date to datetime if it's a string
                promised_date = upcoming_pos[0]['promised_date']
                if isinstance(promised_date, str):
                    try:
                        promised_date = datetime.strptime(promised_date, '%Y-%m-%d')
                    except ValueError:
                        # Skip this check if the date format is invalid
                        pass

                # Compare the dates - compare date objects, not datetime objects
                if isinstance(promised_date, datetime) and isinstance(order_date, datetime):
                    if promised_date.date() == order_date.date():
                        result["at_risk"] = True
                        result["status"] = "tight_timeline"
                        result["details"] = "PO delivery scheduled same day as order ship date"
                        return result

        # If we reach here, the order has enough stock or incoming POs
        return result

    except Exception as e:
        # Handle any other unexpected errors
        print(f"Error in check_order_stock_availability: {e}")
        result["status"] = "error"
        result["details"] = "Error checking stock availability"
        return result

def get_calendar_with_stock_alerts(start_date, end_date, date_type, customer_id=None):
    """
    Get orders for the calendar with stock availability alerts.

    Args:
        start_date: Start date for the calendar view
        end_date: End date for the calendar view
        date_type: Type of date to filter on (ship_date, delivery_date, etc.)
        customer_id: Optional customer ID to filter orders

    Returns:
        List of orders with stock availability information
    """
    db = get_db_connection()

    # First, get the base orders like the original function
    orders = get_orders_for_calendar(start_date, end_date, date_type, customer_id)

    # Enhance each order with stock availability information
    for order in orders:
        order.stock_status = check_order_stock_availability(db, order)

    db.close()
    return orders


def calculate_ship_dates_for_open_orders(debug=False, debug_info=None, avoid_weekends=False):
    """
    Calculate and update ship dates for all open sales order lines
    based on customer order date priority and available stock.

    Uses remaining quantity (quantity - shipped_quantity) for calculations.
    Respects requested_date: will not schedule shipments before the requested date.
    Also updates line status: 8 for partially shipped, 3 for fully shipped.

    Parameters:
    debug (bool): Enable debug mode to track detailed execution information
    debug_info (dict): Dictionary to store debug information
    avoid_weekends (bool): If True, adjusts ship dates to avoid falling on weekends
    """
    if debug_info is None:
        debug_info = {}

    db = get_db_connection()
    updated_orders = set()  # Track unique order IDs that have been updated

    try:
        # Get all open order lines sorted by requested_date (oldest first)
        # Include shipped_quantity in the query
        query = """
            SELECT sol.id, sol.base_part_number, sol.quantity, sol.shipped_quantity,
                   sol.requested_date, so.date_entered, sol.sales_order_id, 
                   so.sales_order_ref, sol.ship_date, sol.sales_status_id
            FROM sales_order_lines sol
            JOIN sales_orders so ON sol.sales_order_id = so.id
            WHERE (sol.ship_date IS NULL OR sol.ship_date > CURRENT_DATE)
                AND sol.sales_status_id NOT IN (5, 6) -- Exclude cancelled/completed statuses
                AND sol.base_part_number IS NOT NULL
            ORDER BY sol.requested_date, so.date_entered, so.id, sol.line_number
        """

        open_lines = db_execute(query, fetch="all")

        if debug:
            debug_info["query"] = query
            debug_info["open_lines_count"] = len(open_lines)
            debug_info["sample_lines"] = [dict(line) for line in open_lines[:5]] if open_lines else []
            debug_info["parts_analysis"] = {}
            if avoid_weekends:
                debug_info["avoid_weekends"] = True

        # Group by part number for processing
        part_orders = {}
        for line in open_lines:
            part_number = line['base_part_number']
            if part_number not in part_orders:
                part_orders[part_number] = []
            part_orders[part_number].append(line)

        if debug:
            debug_info["unique_parts"] = list(part_orders.keys())
            debug_info["parts_count"] = len(part_orders)

        # Process each part number separately
        for part_number, orders in part_orders.items():
            if debug:
                part_debug = {
                    "orders_count": len(orders),
                    "sample_order": dict(orders[0]) if orders else None,
                    "stock_info": {}
                }
                debug_info["parts_analysis"][part_number] = part_debug

            # Get current stock level
            stock_query = """
                SELECT SUM(available_quantity) as current_stock
                FROM stock_movements
                WHERE base_part_number = ? 
                  AND movement_type = 'IN' 
                  AND available_quantity > 0
            """

            stock_result = db.execute(stock_query, (part_number,)).fetchone()
            available_stock = stock_result['current_stock'] if stock_result and stock_result['current_stock'] else 0

            if debug:
                part_debug["stock_info"]["query"] = stock_query
                part_debug["stock_info"]["available_stock"] = available_stock

            # Get future incoming stock (purchase orders), sorted by date
            po_query = """
                SELECT promised_date, quantity 
                FROM purchase_order_lines 
                WHERE base_part_number = ? 
                  AND promised_date >= CURRENT_DATE
                ORDER BY promised_date
            """

            incoming_pos = db.execute(po_query, (part_number,)).fetchall()

            if debug:
                part_debug["po_query"] = po_query
                part_debug["incoming_pos_count"] = len(incoming_pos)
                part_debug["sample_pos"] = [dict(po) for po in incoming_pos[:3]] if incoming_pos else []
                part_debug["order_processing"] = []

            # Calculate ship dates based on available stock and incoming POs
            current_date = datetime.now().date()
            processing_days = 2  # Standard processing time after stock is available

            for order in orders:
                # Calculate remaining quantity to ship - fixed for sqlite3.Row
                total_qty = order['quantity']
                shipped_qty = order['shipped_quantity'] if order['shipped_quantity'] is not None else 0
                remaining_qty = total_qty - shipped_qty

                # Determine the line status
                new_status_id = None
                if shipped_qty > 0:
                    if shipped_qty >= total_qty:  # Fully shipped
                        new_status_id = 3
                    else:  # Partially shipped
                        new_status_id = 8

                # Parse the requested date
                requested_date = None
                if order['requested_date']:
                    if isinstance(order['requested_date'], str):
                        requested_date = datetime.strptime(order['requested_date'], '%Y-%m-%d').date()
                    else:
                        requested_date = order['requested_date']

                order_debug = {
                    "id": order['id'],
                    "total_quantity": total_qty,
                    "shipped_quantity": shipped_qty,
                    "remaining_quantity": remaining_qty,
                    "current_stock": available_stock,
                    "requested_date": requested_date.isoformat() if requested_date else None,
                    "action": "none",
                    "new_status_id": new_status_id
                }

                # Skip if all quantity has been shipped or the line has zero/negative quantity
                if remaining_qty <= 0:
                    # Update status to fully shipped if needed
                    if new_status_id and new_status_id != order['sales_status_id']:
                        db.execute("""
                            UPDATE sales_order_lines
                            SET sales_status_id = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (new_status_id, order['id']))
                        updated_orders.add(order['id'])

                    order_debug["action"] = "updated_status_fully_shipped"
                    if debug:
                        part_debug["order_processing"].append(order_debug)
                    continue

                # Check if requested_date is missing
                if not requested_date:
                    # Clear ship date for orders with no requested date
                    # Update status if needed
                    if new_status_id and new_status_id != order['sales_status_id']:
                        db.execute("""
                            UPDATE sales_order_lines
                            SET ship_date = NULL, sales_status_id = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (new_status_id, order['id']))
                    else:
                        db.execute("""
                            UPDATE sales_order_lines
                            SET ship_date = NULL, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (order['id'],))
                    updated_orders.add(order['id'])

                    order_debug["action"] = "cleared_missing_request_date"
                    if debug:
                        part_debug["order_processing"].append(order_debug)
                    continue

                # Determine earliest possible ship date based on stock
                earliest_ship_date = None

                # If we have enough stock, ship on requested date or processing days after current date
                # (whichever is later)
                if available_stock >= remaining_qty:
                    earliest_possible_date = current_date + timedelta(days=processing_days)
                    # Respect the requested date - don't ship before the customer wants it
                    ship_date = max(earliest_possible_date, requested_date)

                    # Adjust for weekends if needed
                    if avoid_weekends:
                        ship_date = adjust_for_weekends(ship_date)
                        if debug:
                            order_debug["weekend_adjusted"] = True

                    # Update ship date and status if needed
                    if new_status_id and new_status_id != order['sales_status_id']:
                        db.execute("""
                            UPDATE sales_order_lines
                            SET ship_date = ?, sales_status_id = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (ship_date, new_status_id, order['id']))
                    else:
                        db.execute("""
                            UPDATE sales_order_lines
                            SET ship_date = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (ship_date, order['id']))
                    available_stock -= remaining_qty
                    updated_orders.add(order['id'])

                    order_debug["action"] = "updated_from_stock"
                    order_debug["new_ship_date"] = ship_date.isoformat()
                    order_debug["respect_requested"] = "Yes - shipped on or after requested date"
                else:
                    # Find when we'll have enough stock from incoming POs
                    cumulative_incoming = available_stock
                    assigned_date = None
                    used_po = None

                    for po in incoming_pos:
                        cumulative_incoming += po['quantity']

                        # Convert promised_date to datetime if it's a string
                        po_date = po['promised_date']
                        if isinstance(po_date, str):
                            po_date = datetime.strptime(po_date, '%Y-%m-%d').date()

                        if cumulative_incoming >= remaining_qty:
                            # We'll have enough stock by this date
                            earliest_possible_date = po_date + timedelta(days=processing_days)
                            # Respect the requested date - don't ship before the customer wants it
                            assigned_date = max(earliest_possible_date, requested_date)

                            # Adjust for weekends if needed
                            if avoid_weekends:
                                assigned_date = adjust_for_weekends(assigned_date)
                                if debug:
                                    order_debug["weekend_adjusted"] = True

                            used_po = po
                            break

                    # Update the ship date if we found one
                    if assigned_date:
                        # Update with new status if needed
                        if new_status_id and new_status_id != order['sales_status_id']:
                            db.execute("""
                                UPDATE sales_order_lines
                                SET ship_date = ?, sales_status_id = ?, updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (assigned_date, new_status_id, order['id']))
                        else:
                            db.execute("""
                                UPDATE sales_order_lines
                                SET ship_date = ?, updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (assigned_date, order['id']))
                        available_stock = cumulative_incoming - remaining_qty
                        updated_orders.add(order['id'])

                        order_debug["action"] = "updated_from_po"
                        order_debug["new_ship_date"] = assigned_date.isoformat()
                        order_debug["respect_requested"] = "Yes - shipped on or after requested date"

                        # Get PO date for debug
                        po_debug_date = used_po['promised_date']
                        if isinstance(po_debug_date, str):
                            order_debug["po_date"] = po_debug_date
                        else:
                            order_debug["po_date"] = po_debug_date.isoformat()

                        # Remove used incoming stock from consideration
                        new_incoming_pos = []
                        used_up_to_index = incoming_pos.index(used_po)
                        for i, po_line in enumerate(incoming_pos):
                            if i <= used_up_to_index:
                                # This PO was consumed
                                continue
                            new_incoming_pos.append(po_line)
                        incoming_pos = new_incoming_pos
                    else:
                        # No guaranteed date, use lead time but respect requested date
                        lead_time_days = 60  # Default if we don't know when stock will arrive
                        earliest_possible_date = current_date + timedelta(days=lead_time_days)
                        # Respect the requested date - don't ship before the customer wants it
                        ship_date = max(earliest_possible_date, requested_date)

                        # Adjust for weekends if needed
                        if avoid_weekends:
                            ship_date = adjust_for_weekends(ship_date)
                            if debug:
                                order_debug["weekend_adjusted"] = True

                        # Update with new status if needed
                        if new_status_id and new_status_id != order['sales_status_id']:
                            db.execute("""
                                UPDATE sales_order_lines
                                SET ship_date = ?, sales_status_id = ?, updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (ship_date, new_status_id, order['id']))
                        else:
                            db.execute("""
                                UPDATE sales_order_lines
                                SET ship_date = ?, updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (ship_date, order['id']))
                        updated_orders.add(order['id'])

                        order_debug["action"] = "updated_with_lead_time"
                        order_debug["new_ship_date"] = ship_date.isoformat()
                        order_debug["respect_requested"] = "Yes - shipped on or after requested date"

                if debug:
                    part_debug["order_processing"].append(order_debug)

        # Get the final count of updated orders
        updated_count = len(updated_orders)

        db.commit()
        return {
            "status": "success",
            "message": f"Updated ship dates and statuses for {updated_count} order lines",
            "updated_count": updated_count
        }

    except Exception as e:
        db.rollback()
        import traceback
        error_trace = traceback.format_exc()
        if debug:
            debug_info["error"] = str(e)
            debug_info["traceback"] = error_trace
        return {"status": "error", "message": str(e), "traceback": error_trace}

    finally:
        db.close()


def adjust_for_weekends(date):
    """
    Adjusts a date to the next business day if it falls on a weekend

    Parameters:
    date (datetime.date): The date to check and adjust

    Returns:
    datetime.date: The adjusted date (same date if not a weekend, next Monday if weekend)
    """
    # Check if the date falls on a weekend
    # 5 = Saturday, 6 = Sunday in datetime.weekday()
    if date.weekday() == 5:  # Saturday
        return date + timedelta(days=2)  # Move to Monday
    elif date.weekday() == 6:  # Sunday
        return date + timedelta(days=1)  # Move to Monday
    return date


def get_communication_metrics(customer_id=None, start_date=None, end_date=None):
    """Get metrics on communication activities (emails and calls)"""
    db = get_db_connection()
    cursor = db.cursor()

    query = '''
        SELECT 
            communication_type, 
            COUNT(*) as count,
            COUNT(DISTINCT contact_id) as unique_contacts,
            COUNT(DISTINCT customer_id) as unique_customers,
            COUNT(DISTINCT salesperson_id) as unique_salespeople
        FROM contact_communications
        WHERE 1=1
    '''

    params = []

    if customer_id:
        query += ' AND customer_id = ?'
        params.append(customer_id)

    if start_date:
        query += ' AND date >= ?'
        params.append(start_date)

    if end_date:
        query += ' AND date <= ?'
        params.append(end_date)

    query += ' GROUP BY communication_type'

    cursor.execute(query, params)
    results = cursor.fetchall()

    db.close()
    return results


def get_communication_log(customer_id=None, contact_id=None, communication_type=None, limit=100, offset=0):
    """Get a detailed log of communications"""
    db = get_db_connection()
    cursor = db.cursor()

    query = '''
        SELECT 
            cc.id, cc.date, cc.communication_type, cc.notes,
            c.name as contact_name, c.email as contact_email,
            cust.name as customer_name,
            ls.name as salesperson_name
        FROM contact_communications cc
        LEFT JOIN contacts c ON cc.contact_id = c.id
        LEFT JOIN customers cust ON cc.customer_id = cust.id
        LEFT JOIN legacy_salesperson ls ON cc.salesperson_id = ls.id
        WHERE 1=1
    '''

    params = []

    if customer_id:
        query += ' AND cc.customer_id = ?'
        params.append(customer_id)

    if contact_id:
        query += ' AND cc.contact_id = ?'
        params.append(contact_id)

    if communication_type:
        query += ' AND cc.communication_type = ?'
        params.append(communication_type)

    query += ' ORDER BY cc.date DESC LIMIT ? OFFSET ?'
    params.extend([limit, offset])

    cursor.execute(query, params)
    results = cursor.fetchall()

    db.close()
    return results


def add_rfq_update(rfq_id, user_id, update_text=None, update_type='comment'):
    """
    Add an update to an RFQ

    Args:
        rfq_id: The ID of the RFQ
        user_id: The ID of the user making the update
        update_text: Optional text for the update
        update_type: Type of update ('comment', 'chased', etc.)

    Returns:
        The ID of the newly created update
    """
    db = get_db_connection()
    try:
        cursor = db.execute(
            'INSERT INTO rfq_updates (rfq_id, user_id, update_text, update_type) VALUES (?, ?, ?, ?)',
            (rfq_id, user_id, update_text, update_type)
        )
        update_id = cursor.lastrowid
        db.commit()
        return update_id
    except Exception as e:
        db.rollback()
        logging.error(f"Error adding RFQ update: {e}")
        raise
    finally:
        db.close()


def get_rfq_updates(rfq_id):
    """
    Get all updates for an RFQ

    Args:
        rfq_id: The ID of the RFQ

    Returns:
        List of updates with user information
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
        ''', (rfq_id,))
        updates = cursor.fetchall()
        return [dict(update) for update in updates]
    except Exception as e:
        logging.error(f"Error getting RFQ updates: {e}")
        return []
    finally:
        db.close()
