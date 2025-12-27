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

_CALL_LIST_HAS_SNOOZED_UNTIL = None

def _call_list_has_snoozed_until():
    global _CALL_LIST_HAS_SNOOZED_UNTIL
    if _CALL_LIST_HAS_SNOOZED_UNTIL is not None:
        return _CALL_LIST_HAS_SNOOZED_UNTIL
    try:
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("call_list")}
        _CALL_LIST_HAS_SNOOZED_UNTIL = "snoozed_until" in columns
    except Exception:
        _CALL_LIST_HAS_SNOOZED_UNTIL = False
    return _CALL_LIST_HAS_SNOOZED_UNTIL

# PostgreSQL compatibility helper

def add_to_call_list(contact_id, salesperson_id, notes=None, priority=0):
    """Add a contact to the salesperson's call list"""
    db = get_db_connection()
    try:
        # Check if already in active call list
        existing = db.execute('''
            SELECT id FROM call_list 
            WHERE contact_id = ? AND salesperson_id = ? AND is_active = TRUE
        ''', (contact_id, salesperson_id)).fetchone()

        if existing:
            return {'success': False, 'error': 'Contact already in call list'}

        # Get current local time
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        result = db.execute('''
            INSERT INTO call_list (contact_id, salesperson_id, notes, priority, added_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (contact_id, salesperson_id, notes, priority, current_time))

        db.commit()

        return {'success': True, 'call_list_id': result.lastrowid}
    except Exception as e:
        print(f"Error adding to call list: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        db.close()


def remove_from_call_list(call_list_id):
    """Remove a contact from the call list"""
    db = get_db_connection()
    try:
        db.execute('DELETE FROM call_list WHERE id = ?', (call_list_id,))
        db.commit()
        return {'success': True}
    except Exception as e:
        print(f"Error removing from call list: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        db.close()


def snooze_call_list_entry(call_list_id, salesperson_id, snooze_until):
    """Snooze a call list entry until a specific datetime."""
    db = get_db_connection()
    try:
        db.execute(
            '''
            UPDATE call_list
            SET snoozed_until = ?
            WHERE id = ? AND salesperson_id = ? AND is_active = TRUE
            ''',
            (snooze_until, call_list_id, salesperson_id)
        )
        db.commit()
        return {'success': True}
    except Exception as e:
        print(f"Error snoozing call list: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        db.close()


def get_call_list_with_communication_status(salesperson_id):
    """
    Get call list divided into two groups:
    1. Contacts with NO communications since being added to list
    2. Contacts WITH communications since being added to list
    """
    snooze_clause = ""
    if _call_list_has_snoozed_until():
        snooze_clause = "AND (cl.snoozed_until IS NULL OR cl.snoozed_until <= CURRENT_TIMESTAMP)"

    query = f'''
        WITH base AS (
            SELECT
                cl.id as call_list_id,
                cl.contact_id,
                cl.added_date,
                cl.notes as call_list_notes,
                cl.priority,
                c.name,
                c.second_name,
                c.email,
                c.phone,
                c.job_title,
                c.notes as contact_notes,
                cu.id as customer_id,
                cu.name as customer_name,
                cs.status as customer_status,
                st.name as contact_status,
                st.color as status_color
            FROM call_list cl
            JOIN contacts c ON cl.contact_id = c.id
            JOIN customers cu ON c.customer_id = cu.id
            LEFT JOIN customer_status cs ON cu.status_id = cs.id
            LEFT JOIN contact_statuses st ON c.status_id = st.id
            WHERE cl.salesperson_id = ?
              AND cl.is_active = TRUE
              {snooze_clause}
        ),
        comm_counts AS (
            SELECT
                b.call_list_id,
                COUNT(cc.id) as communications_since_added,
                MAX(cc.date) as latest_communication_since_added
            FROM base b
            LEFT JOIN contact_communications cc
                ON cc.contact_id = b.contact_id
                AND CAST(cc.date AS TIMESTAMP) > b.added_date
            GROUP BY b.call_list_id
        ),
        latest_comm AS (
            SELECT
                b.call_list_id,
                cc.communication_type,
                cc.notes,
                ROW_NUMBER() OVER (
                    PARTITION BY b.call_list_id
                    ORDER BY CAST(cc.date AS TIMESTAMP) DESC, cc.id DESC
                ) as rn
            FROM base b
            JOIN contact_communications cc
                ON cc.contact_id = b.contact_id
                AND CAST(cc.date AS TIMESTAMP) > b.added_date
        )
        SELECT 
            b.call_list_id,
            b.contact_id,
            CAST(b.added_date AS TEXT) as added_date,
            b.call_list_notes,
            b.priority,
            b.name,
            b.second_name,
            b.email,
            b.phone,
            b.job_title,
            b.contact_notes,
            b.customer_id,
            b.customer_name,
            b.customer_status,
            b.contact_status,
            b.status_color,
            COALESCE(cc.communications_since_added, 0) as communications_since_added,
            cc.latest_communication_since_added,
            lc.communication_type as latest_communication_type,
            lc.notes as latest_communication_notes
        FROM base b
        LEFT JOIN comm_counts cc ON cc.call_list_id = b.call_list_id
        LEFT JOIN latest_comm lc
            ON lc.call_list_id = b.call_list_id
            AND lc.rn = 1
        ORDER BY 
            b.priority DESC,
            communications_since_added ASC,
            b.added_date ASC
    '''

    try:
        rows = db_execute(query, (salesperson_id,), fetch='all') or []
    except Exception as e:
        print(f"SQL Error: {e}")
        raise

    # Separate into two lists
    no_communications = []
    has_communications = []

    for row in rows:
        contact_data = dict(row)

        # Debug logging
        print(f"Contact: {contact_data['name']}, Added: {contact_data['added_date']}, "
              f"Comms since: {contact_data['communications_since_added']}, "
              f"Latest comm: {contact_data['latest_communication_since_added']}")

        # Only add to has_communications if there are actual communications AFTER being added
        if (contact_data['communications_since_added'] > 0 and
                contact_data['latest_communication_since_added'] is not None):
            has_communications.append(contact_data)
        else:
            no_communications.append(contact_data)

    return {
        'no_communications': no_communications,
        'has_communications': has_communications,
        'total_count': len(rows)
    }

def update_call_list_priority(call_list_id, priority):
    """Update the priority of a call list item"""
    db = get_db_connection()
    try:
        db.execute('''
            UPDATE call_list 
            SET priority = ?
            WHERE id = ?
        ''', (priority, call_list_id))
        db.commit()
        return {'success': True}
    except Exception as e:
        print(f"Error updating call list priority: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        db.close()


def update_call_list_notes(call_list_id, notes):
    """Update the notes for a call list item"""
    db = get_db_connection()
    try:
        db.execute('''
            UPDATE call_list 
            SET notes = ?
            WHERE id = ?
        ''', (notes, call_list_id))
        db.commit()
        return {'success': True}
    except Exception as e:
        print(f"Error updating call list notes: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        db.close()


def bulk_add_to_call_list(contact_ids, salesperson_id, notes=None, priority=0):
    """Bulk add multiple contacts to call list"""
    db = get_db_connection()
    added_count = 0
    skipped_count = 0

    try:
        for contact_id in contact_ids:
            # Check if already exists
            existing = db.execute('''
                SELECT id FROM call_list 
                WHERE contact_id = ? AND salesperson_id = ? AND is_active = TRUE
            ''', (contact_id, salesperson_id)).fetchone()

            if existing:
                skipped_count += 1
                continue

            db.execute('''
                INSERT INTO call_list (contact_id, salesperson_id, notes, priority)
                VALUES (?, ?, ?, ?)
            ''', (contact_id, salesperson_id, notes, priority))
            added_count += 1

        db.commit()
        return {
            'success': True,
            'added_count': added_count,
            'skipped_count': skipped_count
        }
    except Exception as e:
        print(f"Error in bulk add to call list: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        db.close()

def get_call_list_contact_ids(salesperson_id):
    """Get all contact IDs currently on a salesperson's call list"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT contact_id 
        FROM call_list 
        WHERE salesperson_id = ? 
        AND is_active = TRUE
    """, (salesperson_id,))

    result = {row['contact_id'] for row in cursor.fetchall()}
    conn.close()

    return result


def get_base_currency():
    """Get the base currency from settings (defaults to GBP)"""
    result = query_one("SELECT value FROM settings WHERE key = 'base_currency'")
    return result['value'] if result else 'GBP'


def set_base_currency(currency_code):
    """Set the base currency in settings"""
    db_execute(
        """
        INSERT INTO settings (key, value)
        VALUES ('base_currency', ?)
        ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
        """,
        (currency_code,),
        commit=True,
    )


def get_conversion_mode():
    """Get conversion mode from settings"""
    result = query_one("SELECT value FROM settings WHERE key = 'conversion_mode'")
    return result['value'] if result else 'manual'


def fetch_live_exchange_rates():
    """
    Fetch live exchange rates and convert them to the base currency

    Returns:
        dict: Currency codes mapped to exchange rates relative to base currency
              e.g., if base is GBP: {'USD': 1.27, 'EUR': 1.20, 'GBP': 1.00}
    """
    try:
        base_currency = get_base_currency()

        # Fetch rates from your API (adjust URL as needed)
        # This example uses exchangerate-api.com - replace with your actual API
        api_key = "YOUR_API_KEY"  # Store this in settings or environment variable
        url = f"https://api.exchangerate-api.com/v4/latest/{base_currency}"

        response = requests.get(url, timeout=10)
        response.raise_for_status()

        data = response.json()

        if 'rates' in data:
            rates = data['rates']
            # Ensure base currency has rate of 1.0
            rates[base_currency] = 1.0

            logging.info(f"Successfully fetched live rates with base {base_currency}")
            return rates
        else:
            logging.error("API response missing 'rates' field")
            return None

    except requests.RequestException as e:
        logging.error(f"Failed to fetch live exchange rates: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error fetching exchange rates: {e}")
        return None


def get_currencies():
    """
    Get all currencies with their exchange rates
    The exchange_rate_to_eur column is interpreted as exchange_rate_to_base
    """
    db = get_db_connection()
    currencies = db_execute('SELECT * FROM currencies ORDER BY id', fetch="all")
    db.close()
    return currencies


def convert_currency(amount, from_currency, to_currency):
    """
    Convert amount from one currency to another using base currency as intermediate

    Args:
        amount: Amount to convert
        from_currency: Source currency code
        to_currency: Target currency code

    Returns:
        float: Converted amount
    """
    if from_currency == to_currency:
        return amount

    db = get_db_connection()

    # Get rates (stored as rate to base currency)
    from_rate = db.execute(
        'SELECT exchange_rate_to_eur FROM currencies WHERE currency_code = ?',
        (from_currency,)
    ).fetchone()

    to_rate = db.execute(
        'SELECT exchange_rate_to_eur FROM currencies WHERE currency_code = ?',
        (to_currency,)
    ).fetchone()

    db.close()

    if not from_rate or not to_rate:
        raise ValueError(f"Currency not found: {from_currency} or {to_currency}")

    # Convert: amount in from_currency -> base currency -> to_currency
    # If exchange_rate_to_eur stores "1 BASE = X CURRENCY", then:
    # amount_in_base = amount / from_rate
    # amount_in_to = amount_in_base * to_rate

    # If exchange_rate_to_eur stores "1 CURRENCY = X BASE", then:
    # amount_in_base = amount * from_rate
    # amount_in_to = amount_in_base / to_rate

    # Adjust based on how your rates are stored!
    # Assuming: exchange_rate_to_eur = "1 BASE = X CURRENCY"
    base_amount = amount / from_rate['exchange_rate_to_eur']
    converted = base_amount * to_rate['exchange_rate_to_eur']

    return converted

def get_global_alternatives(base_part_number):
    base_part_number = create_base_part_number(base_part_number)

    rows = db_execute("""
        SELECT m2.base_part_number
        FROM part_alt_group_members m1
        JOIN part_alt_group_members m2
          ON m1.group_id = m2.group_id
        WHERE m1.base_part_number = ?
          AND m2.base_part_number <> m1.base_part_number
        ORDER BY m2.base_part_number
    """, (base_part_number,), fetch='all') or []

    return [r["base_part_number"] for r in rows]


def add_global_alternative(base_a, base_b):
    base_a = create_base_part_number(base_a)
    base_b = create_base_part_number(base_b)

    if base_a == base_b:
        return True  # nothing to do

    # Find existing group IDs
    group_a = db_execute(
        "SELECT group_id FROM part_alt_group_members WHERE base_part_number = ?",
        (base_a,), fetch='one'
    )
    group_b = db_execute(
        "SELECT group_id FROM part_alt_group_members WHERE base_part_number = ?",
        (base_b,), fetch='one'
    )

    try:
        with db_cursor(commit=True) as cur:
            # Case 1: Neither in a group → create a new group
            if not group_a and not group_b:
                cur.execute("INSERT INTO part_alt_groups (description) VALUES (?)",
                            (f"{base_a} / {base_b} family",))
                new_group_id = cur.lastrowid

                cur.execute(
                    "INSERT INTO part_alt_group_members (group_id, base_part_number) VALUES (?, ?)",
                    (new_group_id, base_a))
                cur.execute(
                    "INSERT INTO part_alt_group_members (group_id, base_part_number) VALUES (?, ?)",
                    (new_group_id, base_b))

            # Case 2: Only A has a group → insert B into A's group
            elif group_a and not group_b:
                cur.execute(
                    "INSERT INTO part_alt_group_members (group_id, base_part_number) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (group_a['group_id'], base_b)
                )

            # Case 3: Only B has a group → insert A into B's group
            elif group_b and not group_a:
                cur.execute(
                    "INSERT INTO part_alt_group_members (group_id, base_part_number) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (group_b['group_id'], base_a)
                )

            # Case 4: Both have groups, and they differ → MERGE
            elif group_a['group_id'] != group_b['group_id']:
                old_group = group_b['group_id']
                new_group = group_a['group_id']

                # Move all B's group members to A's group
                cur.execute("""
                    UPDATE part_alt_group_members
                    SET group_id = ?
                    WHERE group_id = ?
                """, (new_group, old_group))

                # Delete the now-empty old group record
                cur.execute("DELETE FROM part_alt_groups WHERE id = ?", (old_group,))

            # Case 5: Both already in the same group → nothing to do

        return True

    except Exception as e:
        raise e
